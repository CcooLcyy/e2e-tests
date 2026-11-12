"""用例发现、实时状态、取消及任务汇总的 pytest 插件。"""
import json
import logging
import os
from pathlib import Path
from datetime import datetime
import pytest
from .events import ExecutionTiming, emit
from .case_order import order_case_ids
from .case_plan import get_case_plan
from .reporting import STATUS_LABELS, write_case_report
from .session_evidence import collect_sessions, merge_session_evidence


def fail_e2e_prerequisite(request, *, required, missing, checks):
    """报告缺少的外部能力，并明确标记未执行的验收步骤。"""
    steps = [{"步骤": "检查端到端测试前置条件", "状态": "失败", "预期": required, "实际": missing}]
    steps.extend({"步骤": name, "状态": "未执行", "预期": expected, "实际": "前置条件未满足"}
                 for name, expected in checks)
    for number, step in enumerate(steps, 1):
        step["序号"] = number
    directory = getattr(request.node, "_bench_artifacts", "")
    if directory:
        try:
            write_case_report(directory, {"用例ID": request.node.nodeid, "用例": request.node.obj.__doc__,
                                          "结果": "失败", "步骤": steps})
        except OSError:
            logging.getLogger(__name__).exception("保存前置条件报告失败，保留原始失败原因")
    pytest.fail(f"前置条件未满足，目标行为未验证。{missing}", pytrace=False)


def pytest_configure(config):
    if not any(value.startswith("case_group(") for value in config.getini("markers")):
        config.addinivalue_line("markers", "case_group(name): 声明用例所属的一级功能分组")
    config.addinivalue_line("markers", "opt_in: 此用例需要在界面中单独勾选")
    config.addinivalue_line("markers", "case_flow(*steps, blocked=''): 声明测试流程的操作和预期结果")
    config.addinivalue_line("markers", "requires_modbus: 运行前校验 slaveSim 地址及 RTU 采集等待时间")


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
    for option, default in (("--app-path", ""), ("--driver-path", ""), ("--artifacts-dir", ""), ("--ui-timeout", "300")):
        parser.addoption(option, default=default)


def _executable_flow(item):
    plan = get_case_plan(getattr(item, "obj", None))
    if plan is None:
        return None
    flow = []
    for step in plan:
        entry = {"id": step.id, "action": step.action, "expected": step.expected}
        if step.group:
            entry["group"] = step.group
        flow.append(entry)
    return flow


def pytest_collection_finish(session):
    output = os.environ.get("TESTBENCH_COLLECT")
    if output:
        items = []
        for item in session.items:
            function = getattr(item, "obj", None)
            description = (getattr(function, "__doc__", "") or "").strip()
            name = description.splitlines()[0] if description else item.name
            if "[" in item.name:
                name += " " + item.name[item.name.index("["):]
            flow = item.get_closest_marker("case_flow")
            executable_flow = _executable_flow(item)
            case = {"id": item.nodeid, "name": name, "description": description,
                    "execution_logic": getattr(function, "_bench_execution_logic", ""),
                    "group": getattr(item, "_bench_group", "未分组"),
                    "opt_in": item.get_closest_marker("opt_in") is not None,
                    "requires_modbus": item.get_closest_marker("requires_modbus") is not None,
                    "flow": [{"action": action, "expected": expected} for action, expected in flow.args] if flow else [],
                    "flow_blocked": flow.kwargs.get("blocked", "") if flow else ""}
            if executable_flow is not None:
                case.update({"executable_flow": True, "flow": executable_flow})
            items.append(case)
        Path(output).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def pytest_sessionstart(session):
    session.config._bench_results = {}
    session.config._bench_started = {}


def pytest_runtest_setup(item):
    stop = os.environ.get("TESTBENCH_STOP")
    if stop and Path(stop).exists():
        pytest.exit("测试任务已停止", returncode=2)
    timing = ExecutionTiming()
    item.config._bench_started[item.nodeid] = timing
    os.environ["TESTBENCH_CASE_ID"] = item.nodeid
    emit({"type": "case_started", "case_id": item.nodeid, "started_at": timing.started_at})
    task_path = os.environ.get("TESTBENCH_TASK")
    artifacts = item.config.getoption("--artifacts-dir", default="")
    if task_path or artifacts:
        import hashlib
        root = Path(task_path).parent if task_path else Path(artifacts)
        directory = root / "cases" / hashlib.sha256(item.nodeid.encode()).hexdigest()[:16]
        directory.mkdir(parents=True, exist_ok=True)
        item._bench_artifacts = str(directory)


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
    cleanup_errors = [error for source, app in sessions if source == "desktop"
                      for error in app.metadata.get("测试资源清理错误", [])]
    cleanup_errors.extend(app.cleanup_failure for _, app in sessions if getattr(app, "cleanup_failure", None))
    if report.failed and not cleanup_errors:
        crash = getattr(report.longrepr, "reprcrash", None)
        cleanup_errors.append(getattr(crash, "message", None) or "用例收尾失败，详见诊断资料")
    if cleanup_errors:
        details = "测试资源清理未完成：" + "；".join(cleanup_errors)
        if status == "passed":
            status = "failed"
        message = f"{message}\n{details}" if message else details
    timing = item.config._bench_started.pop(item.nodeid, None)
    elapsed = timing.finish() if timing is not None else {}
    executable_flow = _executable_flow(item)
    plan_details = {}
    if executable_flow is not None:
        plan_details = {"plan_steps": getattr(item, "_bench_plan_steps", {}),
                        "plan_step_timings": getattr(item, "_bench_plan_step_timings", {})}
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
        if cleanup_errors:
            result["测试资源清理错误"] = cleanup_errors
        result.update({"用例ID": item.nodeid, "用例": (getattr(item.obj, "__doc__", "") or item.name).strip(),
                       "结果": STATUS_LABELS[status], "步骤": result.get("步骤", []),
                       "阶段结果": [{"阶段": part.when, "结果": "失败" if part.failed else "跳过" if part.skipped else "通过",
                                   "错误": str(part.longrepr) if not part.passed else ""} for part in reports],
                       "结束时间": elapsed.get("finished_at", datetime.now().astimezone().isoformat(timespec="seconds"))})
        if elapsed:
            result.update({"开始时间": elapsed["started_at"], "耗时秒": elapsed["duration"]})
        if message:
            result["错误"] = message
        if executable_flow is not None:
            result.update({"flow": executable_flow, **plan_details})
        write_case_report(directory, result)
        print(f"[报告] 已保存用例报告：{item.nodeid}（{STATUS_LABELS[status]}）", flush=True)
    event = {"type": "case_finished", "case_id": item.nodeid, "status": status,
             "message": message, "artifacts_dir": getattr(item, "_bench_artifacts", ""), **elapsed, **plan_details}
    if cleanup_errors:
        event["cleanup_errors"] = cleanup_errors
    item.config._bench_results[item.nodeid] = event
    emit(event)


def pytest_sessionfinish(session, exitstatus):
    output = os.environ.get("TESTBENCH_SUMMARY")
    if output:
        Path(output).write_text(json.dumps({"exit_code": int(exitstatus),
            "cases": list(session.config._bench_results.values())}, ensure_ascii=False, indent=2), encoding="utf-8")
