"""验证历史记录清理的时间筛选、确认快照与目录边界。"""
import json
import os
import time

import pytest

from testbench import history_cleanup
from testbench.bridge import TestbenchBridge


def _run(root, index, *, age=0, status="passed"):
    directory = root / f"20261101-100000-{index:08x}"
    evidence = directory / "cases" / "first"
    evidence.mkdir(parents=True)
    (evidence / "screenshot.png").write_bytes(b"image")
    summary = directory / "summary.json"
    summary.write_text(json.dumps({"status": status, "cases": []}), encoding="utf-8")
    modified = time.time() - age * 86400
    os.utime(summary, (modified, modified))
    return directory


def test_history_cleanup_only_removes_previously_previewed_completed_runs(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    old = _run(root, 1, age=10)
    recent = _run(root, 2, age=4)
    unfinished = _run(root, 3, age=12, status="running")
    other = root / "scratch"
    other.mkdir()
    monkeypatch.setattr(history_cleanup, "_active_runs", lambda root: set())

    view, preview = history_cleanup.preview_cleanup(root, 7)
    size = (old / "summary.json").stat().st_size + len(b"image")
    assert view == {"count": 1, "bytes": size, "skipped": []}
    newly_imported = _run(root, 4, age=11)
    result = history_cleanup.apply_cleanup(root, preview)
    assert result == {"deleted": 1, "deleted_ids": [old.name], "deleted_bytes": size, "failures": []}
    assert not old.exists()
    assert all(path.is_dir() for path in (recent, unfinished, newly_imported, other))


def test_history_cleanup_preserves_changed_or_open_runs_and_continues(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    opened = _run(root, 1)
    changed = _run(root, 2)
    free = _run(root, 3)
    monkeypatch.setattr(history_cleanup, "_active_runs", lambda root: {opened.name})

    _, preview = history_cleanup.preview_cleanup(root, 0)
    (changed / "new.log").write_text("more evidence", encoding="utf-8")
    result = history_cleanup.apply_cleanup(root, preview)
    assert result["deleted"] == 1
    assert [failure["id"] for failure in result["failures"]] == [opened.name, changed.name]
    assert not free.exists()
    assert (opened / "summary.json").is_file()
    assert (changed / "new.log").is_file()


def test_history_cleanup_skips_links_to_other_directories(tmp_path):
    root = tmp_path / "artifacts"
    run = _run(root, 1)
    external = tmp_path / "external"
    external.mkdir()
    evidence = external / "preserve.txt"
    evidence.write_text("external", encoding="utf-8")
    try:
        (run / "linked").symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前账户无法创建目录链接")

    view, preview = history_cleanup.preview_cleanup(root, 0)
    assert view["count"] == 0
    assert [entry["id"] for entry in view["skipped"]] == [run.name]
    assert history_cleanup.apply_cleanup(root, preview)["deleted"] == 0
    assert evidence.read_text(encoding="utf-8") == "external"


@pytest.mark.parametrize("value", [-7, 1, "7", False])
def test_history_cleanup_rejects_unsupported_retention_values(tmp_path, value):
    with pytest.raises(ValueError, match="保留时间"):
        history_cleanup.preview_cleanup(tmp_path, value)


def test_bridge_cleanup_requires_idle_and_a_single_use_preview(tmp_path, monkeypatch):
    bridge = TestbenchBridge(tmp_path)
    run = _run(bridge.artifacts, 1, age=10)
    monkeypatch.setattr(history_cleanup, "_active_runs", lambda root: set())
    bridge.controller.active = True
    with pytest.raises(ValueError, match="结束后"):
        bridge.handle("preview_history_cleanup", {"days": 7})
    bridge.controller.active = False
    token = bridge.handle("preview_history_cleanup", {"days": 7})["token"]
    assert bridge.handle("apply_history_cleanup", {"token": token})["deleted_ids"] == [run.name]
    assert bridge.handle("history_snapshot")["history"] == []
    with pytest.raises(ValueError, match="重新预览"):
        bridge.handle("apply_history_cleanup", {"token": token})
