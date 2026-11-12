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
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    controller._started = now[0]
    controller._timeout = 60
    controller._process = SimpleNamespace(poll=lambda: None)
    (controller.run_dir / "worker.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(controller, "_worker_exited", lambda: False)
    exited = [False]
    monkeypatch.setattr(controller, "_completion_exited", lambda: exited[0])
    terminated = []
    monkeypatch.setattr(controller, "_terminate", lambda: terminated.append(True))
    controller.stop()
    assert (controller.run_dir / "stop").is_file()

    case_id = "tests/test_case.py::test_cleanup"
    controller._selected = [case_id]
    path = controller.run_dir / "events.jsonl"
    now[0] += 45
    path.write_text(json.dumps({"type": "plan_step", "case_id": case_id,
                               "plan_step_id": "cleanup", "status": "running"}) + "\n", encoding="utf-8")
    events = controller.poll()
    assert controller.active
    assert not any(event["type"] == "task_finished" for event in events)
    now[0] += 45
    assert not controller.poll()
    assert controller.active
    assert not terminated

    cleanup_error = "IEC104 本例连接：删除后仍在列表"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "case_finished", "case_id": case_id, "status": "cancelled",
                                 "cleanup_errors": [cleanup_error]}) + "\n")
        stream.write(json.dumps({"type": "task_finished", "status": "cancelled"}) + "\n")
    assert not any(event["type"] == "task_finished" for event in controller.poll())
    exited[0] = True
    events = controller.poll()
    assert not controller.active
    finished = next(event for event in events if event["type"] == "task_finished")
    assert finished["status"] == "cancelled"
    assert finished["cleanup_errors"] == [cleanup_error]
    assert cleanup_error in (controller.run_dir / "report.md").read_text(encoding="utf-8")
    path.write_text(json.dumps({"type": "task_finished", "status": "passed"}) + "\n", encoding="utf-8")
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
