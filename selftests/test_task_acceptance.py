"""通过独立子进程验收任务执行、失败结果和停止，不调用真实上位机。"""

import sys
import time
import uuid
from pathlib import Path

from testbench.controller import TaskController
from testbench.model import Environment


def environment():
    return Environment(name="执行器验收", app_path=sys.executable, driver_path=sys.executable,
                       mode="fresh", workspace_path="", baseline_path="", timeout=5, close_app=True)


def project(tmp_path, source):
    # 管理员创建的 pytest 临时目录可能不允许普通权限工作进程读取。
    # 项目产物目录继承当前用户权限，并保留子进程证据供复核。
    tmp_path = Path(__file__).resolve().parent.parent / "artifacts" / ("task-acceptance-" + uuid.uuid4().hex)
    tmp_path.mkdir(parents=True)
    directory = tmp_path / "tests"
    directory.mkdir()
    (directory / "test_external.py").write_text(source, encoding="utf-8")
    return tmp_path


def finish(controller, events, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events.extend(controller.poll())
        if not controller.active:
            events.extend(controller.poll())
            return events
        time.sleep(0.05)
    controller.stop()
    raise AssertionError("测试任务未在规定时间内结束")


def test_failed_case_is_red_and_next_independent_case_still_executes(tmp_path):
    """一次任务中的失败不能被最终退出误报为通过，也不阻止后续独立用例。"""
    root = project(tmp_path, 'def test_bad():\n    assert False, "故意失败"\n'
                   'def test_good():\n    assert 1 + 1 == 2\n')
    controller = TaskController(root)
    ids = ["tests/test_external.py::test_bad", "tests/test_external.py::test_good"]
    run_dir = controller.start(environment(), ids)
    events = finish(controller, [])
    completed = [event for event in events if event["type"] == "case_finished"]
    assert [(event["case_id"], event["status"]) for event in completed] == [
        (ids[0], "failed"), (ids[1], "passed")]
    finished = [event for event in events if event["type"] == "task_finished"]
    assert len(finished) == 1
    assert finished[0]["status"] == "failed"
    assert (run_dir / "summary.json").is_file()


def test_stop_does_not_start_next_case_and_reports_cancelled(tmp_path):
    """协作停止应先收尾当前用例，并保持后续用例未执行。"""
    root = project(tmp_path, '''import os
from pathlib import Path
import time

def test_slow():
    stop = Path(os.environ["TESTBENCH_STOP"])
    resource = stop.with_name("test-resource")
    resource.touch()
    try:
        while not stop.exists():
            time.sleep(0.025)
        raise InterruptedError("已收到停止请求")
    finally:
        resource.unlink()
        assert not resource.exists()
        stop.with_name("cleanup-confirmed").touch()

def test_later():
    raise AssertionError("不应该执行")
''')
    controller = TaskController(root)
    ids = ["tests/test_external.py::test_slow", "tests/test_external.py::test_later"]
    run_dir = controller.start(environment(), ids)
    events = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        events.extend(controller.poll())
        if (run_dir / "test-resource").is_file():
            break
        time.sleep(0.05)
    else:
        controller.stop()
        raise AssertionError("任务没有开始执行用例")
    controller.stop()
    events = finish(controller, events, timeout=15)
    assert (run_dir / "cleanup-confirmed").is_file()
    assert not (run_dir / "test-resource").exists()
    assert not any(event["type"] == "case_started" and event["case_id"] == ids[1] for event in events)
    assert any(event["type"] == "task_finished" and event["status"] == "cancelled" for event in events)
