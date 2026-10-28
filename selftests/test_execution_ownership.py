"""桌面会话初始化失败时的进程所有权保护。"""
from pathlib import Path
import sys
import pytest
from desktop_demo import ReleaseDesktop


def test_preflight_failure_never_cleans_existing_instance(tmp_path, monkeypatch):
    """本次尚未创建进程时，不允许清理固定工作区的既有实例。"""
    app = ReleaseDesktop(Path(sys.executable), Path(sys.executable), tmp_path / "artifacts", 5,
                         mode="workspace", workspace_path=str(tmp_path / "workspace"))

    def reject():
        raise RuntimeError("工作区已有实例")

    def forbidden_cleanup():
        raise AssertionError("不得清理非本次创建的进程")

    monkeypatch.setattr(app, "_check_legacy_cache", reject)
    monkeypatch.setattr(app, "_stop_owned_app", forbidden_cleanup)
    with pytest.raises(RuntimeError, match="已有实例"):
        with app:
            pass
    assert app.process is None
    assert (tmp_path / "artifacts" / "report.json").exists()
