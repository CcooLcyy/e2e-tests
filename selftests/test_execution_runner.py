"""执行内核的真实独立工作进程回归，不依赖设备或账号。"""
from pathlib import Path
import sys
import time
import uuid

from testbench.catalog import discover_cases
from testbench.controller import TaskController
from testbench.model import Environment


def make_suite(tmp_path, source):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_flow.py").write_text(source, encoding="utf-8")
    return Environment(app_path=sys.executable, driver_path=sys.executable)


def accessible_root():
    """使用项目产物目录，继承可供普通用户读取的权限。"""
    root = Path(__file__).resolve().parents[1] / "artifacts" / ("selftest-" + uuid.uuid4().hex)
    root.mkdir(parents=True)
    return root


def wait_task(controller, timeout=25):
    events = []
    deadline = time.monotonic() + timeout
    while controller.active and time.monotonic() < deadline:
        events.extend(controller.poll())
        time.sleep(0.1)
    assert not controller.active, "测试工作进程未按时退出"
    return events


def test_runner_keeps_failure_and_continues(tmp_path):
    """真实子进程失败后继续执行后续独立用例，不将失败误报为成功。"""
    tmp_path = accessible_root()
    env = make_suite(tmp_path, '''import os
from pathlib import Path
import subprocess
import sys
import tempfile

def test_fail():
    assert False, "预期失败"

def test_pass(tmp_path):
    run = Path(os.environ["TESTBENCH_TASK"]).parent
    assert tmp_path.is_relative_to(run / "pytest-tmp")
    temporary = str(run / "temp")
    assert tempfile.gettempdir() == temporary
    assert all(os.environ[name] == temporary for name in ("TEMP", "TMP", "TMPDIR"))
    child_temp = subprocess.check_output(
        [sys.executable, "-X", "utf8", "-c", "import tempfile; print(tempfile.gettempdir())"],
        encoding="utf-8",
    ).strip()
    assert Path(child_temp) == run / "temp"
''')
    cases = discover_cases(tmp_path)
    controller = TaskController(tmp_path)
    controller.start(env, [case.id for case in cases])
    events = wait_task(controller)
    assert [event["status"] for event in events if event["type"] == "case_finished"] == ["failed", "passed"]
    assert [event["status"] for event in events if event["type"] == "task_finished"] == ["failed"]


def test_runner_cancel_leaves_next_case_unexecuted(tmp_path):
    """业务步骤响应协作停止后，执行器不能启动下一用例。"""
    tmp_path = accessible_root()
    env = make_suite(tmp_path, '''import time
from desktop_demo import ReleaseDesktop

def test_slow():
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ReleaseDesktop._check_stop()
        time.sleep(0.05)
    raise AssertionError("业务步骤未收到停止信号")

def test_never():
    raise AssertionError("不应启动后续用例")
''')
    controller = TaskController(tmp_path)
    controller.start(env, [case.id for case in discover_cases(tmp_path)])
    deadline = time.monotonic() + 15
    events = []
    while time.monotonic() < deadline:
        events.extend(controller.poll())
        if any(event["type"] == "case_started" for event in events):
            break
        time.sleep(0.1)
    assert any(event["type"] == "case_started" for event in events)
    controller.stop()
    events.extend(wait_task(controller, 10))
    assert len([event for event in events if event["type"] == "case_started"]) == 1
    assert [event["status"] for event in events if event["type"] == "task_finished"] == ["cancelled"]
