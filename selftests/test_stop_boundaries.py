"""停止清理完成与基线导入的回归验收。"""
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from desktop_demo import ReleaseDesktop
from testbench.controller import TaskController


def test_stop_stays_active_until_cleanup_finishes_and_ignores_late_events(tmp_path, monkeypatch):
    """清理尚未完成时禁止新任务，终结后旧完成事件不能覆盖停止状态。"""
    controller = TaskController(tmp_path)
    controller.run_dir = tmp_path / "run"
    controller.run_dir.mkdir()
    controller.active = True
    controller._started = time.monotonic()
    controller._stop_at = time.monotonic() - 5
    controller._timeout = 60
    controller._process = SimpleNamespace(poll=lambda: None)
    cleanup = SimpleNamespace(code=None)
    cleanup.poll = lambda: cleanup.code
    monkeypatch.setattr(controller, "_terminate", lambda: cleanup)
    monkeypatch.setattr(controller, "_worker_exited", lambda: cleanup.code == 0)
    events = controller.poll()
    assert controller.active
    assert not any(event["type"] == "task_finished" for event in events)
    cleanup.code = 0
    events = controller.poll()
    assert not controller.active
    assert any(event["type"] == "task_finished" and event["status"] == "cancelled" for event in events)
    (controller.run_dir / "events.jsonl").write_text(json.dumps({"type": "task_finished", "status": "passed"}) + "\n", encoding="utf-8")
    assert controller.poll() == []


def test_baseline_excludes_login_json_case_insensitively(tmp_path):
    """Windows 大小写不敏感的登录配置不得被基线复制。"""
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "Platform-Login.JSON").write_text('{"auto_login": true}', encoding="utf-8")
    (baseline / "settings.json").write_text(json.dumps({"schema_version": 1, "settings": {
        "mskdsp_app_mode": "online", "mskdsp_manager_addr": "127.0.0.1:17000",
    }}), encoding="utf-8")
    app = ReleaseDesktop(Path(sys.executable), Path(sys.executable), tmp_path / "run", 5, baseline_path=str(baseline))
    app._prepare_runtime()
    settings = json.loads((app.runtime / "data" / "settings.json").read_text(encoding="utf-8"))["settings"]
    assert settings == {"mskdsp_app_mode": "online", "mskdsp_manager_addr": "192.168.1.219:17000"}
    assert app.metadata["现场直连预设地址"] == "192.168.1.219:17000"
    assert not (app.runtime / "data" / "platform-login.json").exists()
