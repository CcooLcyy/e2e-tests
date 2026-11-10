"""任务无进展保护与步骤等待的契约，不运行工作进程或浏览器。"""
from types import SimpleNamespace

import pytest

from testbench import controller as module
from testbench.modbus_environment import HUAWEI_CASE_ID, RTU_CONFIG_CASE_ID, RTU_OBSERVATION_CASE_IDS


CASE_ID = "tests/test_example.py::test_example"


def polling_controller(tmp_path, monkeypatch, events):
    controller = module.TaskController(tmp_path)
    controller.run_dir = tmp_path / "run"
    controller.run_dir.mkdir()
    (controller.run_dir / "worker.json").write_text("{}", encoding="utf-8")
    controller.active = True
    controller._started, controller._last_progress, controller._timeout = 1, 1, 10
    controller._selected = [CASE_ID]
    controller._process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(controller, "_read_events", lambda: events)
    monkeypatch.setattr(controller, "_worker_exited", lambda: False)
    cleanup = []
    monkeypatch.setattr(controller, "_begin_cleanup", lambda *args: cleanup.append(args))
    monkeypatch.setattr(module.time, "monotonic", lambda: 100)
    return controller, cleanup


@pytest.mark.parametrize("event_type", ["case_started", "case_finished", "step"])
def test_selected_case_progress_prevents_total_wall_clock_timeout(tmp_path, monkeypatch, event_type):
    """合法步骤持续推进时，即使任务总时长已超旧期限，也不能提前截断长流程。"""
    event = {"type": event_type, "case_id": CASE_ID, "status": "passed"}
    controller, cleanup = polling_controller(tmp_path, monkeypatch, [event])
    controller.poll()
    assert controller._last_progress == 100
    assert cleanup == []


def test_no_progress_past_deadline_still_fails(tmp_path, monkeypatch):
    """没有新步骤且距最近进展超过期限时必须失败，不能无限等待。"""
    controller, cleanup = polling_controller(tmp_path, monkeypatch, [])
    controller._last_progress = 89
    controller.poll()
    assert cleanup and cleanup[0][0] == "failed"
    assert "无步骤进展" in cleanup[0][1]


@pytest.mark.parametrize("event", [
    {"type": "step", "case_id": "tests/test_unknown.py::test_unknown"},
    {"type": "message", "case_id": CASE_ID},
])
def test_unknown_case_or_nonprogress_event_cannot_extend_deadline(tmp_path, monkeypatch, event):
    """无关用例或普通消息不得冒充步骤进展延长期限。"""
    controller, cleanup = polling_controller(tmp_path, monkeypatch, [event])
    controller.poll()
    assert controller._last_progress == 1
    assert cleanup and cleanup[0][0] == "failed"


@pytest.mark.parametrize("case_id,expected", [
    (HUAWEI_CASE_ID, 3120), (sorted(RTU_OBSERVATION_CASE_IDS - {HUAWEI_CASE_ID, RTU_CONFIG_CASE_ID})[0], 3120),
    (RTU_CONFIG_CASE_ID, 3120), (CASE_ID, 140),
])
def test_rtu_wait_is_included_in_task_protection_only_when_selected(tmp_path, monkeypatch, case_id, expected):
    """RTU五分钟采集不被短UI等待截断，非RTU任务不受未使用的采集参数影响。"""
    monkeypatch.setattr(module, "discover_cases", lambda root: [SimpleNamespace(id=case_id, name="测试用例")])
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=42))
    monkeypatch.setattr(module.time, "monotonic", lambda: 100)
    environment = SimpleNamespace(validate=lambda: None, validate_for_cases=lambda _: None, to_dict=lambda: {},
                                  timeout=2, master104_enabled=False, modbus_acquisition_timeout=300)
    controller = module.TaskController(tmp_path)
    controller.start(environment, [case_id])
    assert controller._timeout == expected
    assert controller._last_progress == controller._started == 100
