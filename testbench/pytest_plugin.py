"""用例发现、实时状态、取消及任务汇总的 pytest 插件。"""
import json
import logging
import os
from pathlib import Path
import time
from datetime import datetime
import pytest
from .events import emit
from .case_order import order_case_ids
from .reporting import STATUS_LABELS, write_case_report
from .session_evidence import collect_sessions, merge_session_evidence


def pytest_configure(config):
    if not any(value.startswith("case_group(") for value in config.getini("markers")):
        config.addinivalue_line("markers", "case_group(name): 声明用例所属的一级功能分组")


def pytest_collection_modifyitems(config, items):
    for item in items:
        groups = []
        for marker in item.iter_markers("case_group"):
            if (len(marker.args) != 1 or marker.kwargs or not isinstance(marker.args[0], str)
                    or not marker.args[0].strip()):
                raise pytest.UsageError(f"用例分组声明无效：{item.nodeid}；case_group 需要且仅接受一个非空字符串参数")
            groups.append(marker.args[0].strip())
        # pytest 按距离用例由近到远返回标记，单项声明自然覆盖文件默认分组。
        item._bench_group = groups[0] if groups else "未分组"
    try:
        ordered = order_case_ids([(item.nodeid, item._bench_group) for item in items], config.rootpath)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from None
    ranks = {case_id: position for position, case_id in enumerate(ordered)}
    items.sort(key=lambda item: ranks[item.nodeid])
    logging.getLogger(__name__).info("已按分组及组内清单顺序整理 %s 项用例", len(items))


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
            items.append({"id": item.nodeid, "name": name, "description": description,
                          "group": getattr(item, "_bench_group", "未分组")})
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
    artifacts = item.config.getoption("--artifacts-dir", default="")
    if task_path or artifacts:
        import hashlib
        root = Path(task_path).parent if task_path else Path(artifacts)
        directory = root / "cases" / hashlib.sha256(item.nodeid.encode()).hexdigest()[:16]
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
    sessions = collect_sessions(item)
    if sessions and (report.when == "call" or report.failed or (report.when == "setup" and report.skipped)):
        status = "failed" if report.failed else "skipped" if report.skipped else "passed"
        stop = os.environ.get("TESTBENCH_STOP")
        if stop and Path(stop).exists():
            status = "cancelled"
        for source, app in sessions:
            try:
                app.finish_case(status, str(report.longrepr) if report.failed else "",
                                f"{app.metadata.get('用例', item.name)}：{report.when} 阶段失败现场")
            except Exception as error:
                # 采集异常保留为独立诊断，不能遮蔽原始业务失败或阻止另一端取证。
                errors = getattr(item, "_bench_evidence_errors", [])
                errors.append({"来源": source, "阶段": report.when, "错误": str(error)})
                item._bench_evidence_errors = errors
                logging.getLogger(__name__).exception("%s 会话阶段取证失败", source)
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
    directory = getattr(item, "_bench_artifacts", "")
    if directory:
        result = {}
        path = Path(directory) / "report.json"
        if path.exists():
            result = json.loads(path.read_text(encoding="utf-8"))
        if sessions:
            result = merge_session_evidence(result, sessions, event_path=os.environ.get("TESTBENCH_EVENTS"),
                                            case_id=item.nodeid)
        elif status == "failed" and not result.get("截图"):
            result["截图"] = [{"name": "case-failure.png", "description": "用例失败现场",
                "expected": "未提供业务预期现象", "actual": message or "未建立桌面测试实例",
                "status": status, "capture_status": "missing", "error": "未建立桌面测试实例，无法保存现场",
                "time": datetime.now().astimezone().isoformat(timespec="seconds")}]
        result.setdefault("截图", [])
        if getattr(item, "_bench_evidence_errors", None):
            result["证据采集错误"] = item._bench_evidence_errors
        result.update({"用例ID": item.nodeid, "用例": (getattr(item.obj, "__doc__", "") or item.name).strip(),
                       "结果": STATUS_LABELS[status], "步骤": result.get("步骤", []),
                       "阶段结果": [{"阶段": part.when, "结果": "失败" if part.failed else "跳过" if part.skipped else "通过",
                                   "错误": str(part.longrepr) if not part.passed else ""} for part in reports],
                       "结束时间": datetime.now().astimezone().isoformat(timespec="seconds")})
        if message:
            result["错误"] = message
        write_case_report(directory, result)
        print(f"[报告] 已保存用例报告：{item.nodeid}（{STATUS_LABELS[status]}）", flush=True)
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
