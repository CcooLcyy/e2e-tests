"""用例发现、实时状态、取消及任务汇总的 pytest 插件。"""
import json
import os
from pathlib import Path
import time
import pytest
from .events import emit


def pytest_addoption(parser):
    for option, default in (("--app-path", ""), ("--driver-path", ""), ("--artifacts-dir", ""), ("--ui-timeout", "20")):
        parser.addoption(option, default=default)


def pytest_collection_finish(session):
    output = os.environ.get("TESTBENCH_COLLECT")
    if output:
        items = []
        for item in session.items:
            description = (getattr(getattr(item, "obj", None), "__doc__", "") or "").strip()
            name = description.splitlines()[0] if description else item.name
            if "[" in item.name:
                name += " " + item.name[item.name.index("["):]
            items.append({"id": item.nodeid, "name": name, "description": description})
        Path(output).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def pytest_sessionstart(session):
    session.config._bench_results = {}
    session.config._bench_started = {}


def pytest_runtest_setup(item):
    stop = os.environ.get("TESTBENCH_STOP")
    if stop and Path(stop).exists():
        pytest.exit("测试任务已停止", returncode=2)
    item.config._bench_started[item.nodeid] = time.monotonic()
    task_path = os.environ.get("TESTBENCH_TASK")
    if task_path:
        import hashlib
        directory = Path(task_path).parent / "cases" / hashlib.sha256(item.nodeid.encode()).hexdigest()[:16]
        directory.mkdir(parents=True, exist_ok=True)
        item._bench_artifacts = str(directory)
    os.environ["TESTBENCH_CASE_ID"] = item.nodeid
    emit({"type": "case_started", "case_id": item.nodeid})


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    item._bench_last_report = report
    reports = getattr(item, "_bench_reports", [])
    reports.append(report)
    item._bench_reports = reports
    if report.when != "teardown":
        return
    status = "passed"
    message = ""
    for part in reports:
        if part.failed:
            status, message = "failed", str(part.longrepr)[-6000:]
            break
        if part.skipped:
            status, message = "skipped", str(part.longrepr)
    stop = os.environ.get("TESTBENCH_STOP")
    if stop and Path(stop).exists():
        status, message = "cancelled", "用户停止了测试任务"
    event = {"type": "case_finished", "case_id": item.nodeid, "status": status,
             "duration": round(time.monotonic() - item.config._bench_started.get(item.nodeid, time.monotonic()), 3),
             "message": message, "artifacts_dir": getattr(item, "_bench_artifacts", "")}
    item.config._bench_results[item.nodeid] = event
    emit(event)


def pytest_sessionfinish(session, exitstatus):
    output = os.environ.get("TESTBENCH_SUMMARY")
    if output:
        Path(output).write_text(json.dumps({"exit_code": int(exitstatus),
            "cases": list(session.config._bench_results.values())}, ensure_ascii=False, indent=2), encoding="utf-8")
