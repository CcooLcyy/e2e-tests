"""停止收尾保留真实结束事件及诊断入口，不启动进程或真实用例。"""
import hashlib
import json
from types import SimpleNamespace

from testbench.controller import TaskController


CASE = "tests/test_flow.py::test_first"
NEXT = "tests/test_flow.py::test_next"


def controller_at(tmp_path):
    controller = TaskController(tmp_path)
    controller.run_dir = tmp_path / "artifacts" / "run"
    controller.run_dir.mkdir(parents=True)
    controller._selected = [CASE, NEXT]
    controller._current = CASE
    controller.active = True
    task = {"case_ids": [CASE, NEXT], "case_names": {CASE: "停止时保留现场", NEXT: "后续检查"},
            "environment": {"name": "测试环境"}, "started_at": "2026-11-04T13:00:00+08:00"}
    (controller.run_dir / "task.json").write_text(json.dumps(task), encoding="utf-8")
    return controller


def case_report(controller, result):
    directory = controller.run_dir / "cases" / hashlib.sha256(CASE.encode()).hexdigest()[:16]
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps({"用例ID": CASE, **result}), encoding="utf-8")
    return directory


def event_file(controller, events):
    (controller.run_dir / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def test_cleanup_drains_real_completion_and_preserves_duration_and_report(tmp_path):
    """清理期间到达的取消记录保留真实八秒耗时及报告目录，仅终结任务一次。"""
    controller = controller_at(tmp_path)
    directory = case_report(controller, {"结果": "已停止", "阶段结果": [{"阶段": "teardown", "结果": "通过"}]})
    event_file(controller, [{"type": "case_finished", "case_id": CASE, "status": "cancelled",
                             "duration": 8.021, "artifacts_dir": str(directory), "message": "用户停止了测试任务"},
                            {"type": "task_finished", "status": "cancelled"}])
    controller._cleanup = SimpleNamespace(poll=lambda: 0)
    controller._cleanup_reason = ("cancelled", "用户停止了测试任务")
    controller._process = SimpleNamespace(poll=lambda: 0)
    events = controller.poll()
    summary = json.loads((controller.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["cases"][0]["duration"] == 8.021
    assert summary["cases"][0]["artifacts_dir"] == str(directory)
    assert summary["cases"][1]["status"] == "skipped"
    assert len([event for event in events if event["type"] == "task_finished"]) == 1
    assert controller.poll() == []
    assert (controller.run_dir / "diagnostics.md").exists()


def test_completed_failure_in_summary_survives_stop_without_event(tmp_path):
    """已写汇总的真实失败不能被当前用例的通用停止记录覆盖。"""
    controller = controller_at(tmp_path)
    directory = case_report(controller, {"结果": "失败"})
    completed = {"case_id": CASE, "status": "failed", "duration": 5.25,
                 "artifacts_dir": str(directory), "message": "原始失败"}
    (controller.run_dir / "summary.json").write_text(json.dumps({"cases": [completed]}), encoding="utf-8")
    events = controller._finish_abnormal("cancelled", "用户停止了测试任务")
    assert controller._finished[CASE] == completed
    assert any(event.get("case_id") == CASE and event["status"] == "failed" for event in events)


def test_incomplete_pass_report_remains_cancelled_but_keeps_evidence(tmp_path):
    """强杀留下的临时通过报告不能变成整例通过，已有现场及记录耗时仍可追溯。"""
    controller = controller_at(tmp_path)
    directory = case_report(controller, {"结果": "通过", "开始时间": "2026-11-04T13:00:00+08:00",
                                         "结束时间": "2026-11-04T13:00:06+08:00", "步骤": []})
    controller._finish_abnormal("cancelled", "用户停止了测试任务")
    assert controller._finished[CASE]["status"] == "cancelled"
    assert controller._finished[CASE]["duration"] == 6.0
    assert controller._finished[CASE]["artifacts_dir"] == str(directory)
    assert json.loads((directory / "report.json").read_text(encoding="utf-8"))["结果"] == "通过"


def test_external_summary_path_is_not_linked_or_loaded(tmp_path):
    """汇总中的外部目录不能扩大诊断资料读取范围。"""
    controller = controller_at(tmp_path)
    external = tmp_path / "outside"
    external.mkdir()
    (external / "report.json").write_text('{"结果":"通过"}', encoding="utf-8")
    (controller.run_dir / "summary.json").write_text(json.dumps({"cases": [
        {"case_id": CASE, "status": "cancelled", "duration": 4, "artifacts_dir": str(external)}]}), encoding="utf-8")
    controller._finish_abnormal("cancelled", "用户停止了测试任务")
    assert controller._finished[CASE]["artifacts_dir"] == ""


def test_unknown_case_events_cannot_add_results_to_selected_cases(tmp_path):
    """尾部事件不能把不属于本次所选用例的记录混进报告。"""
    controller = controller_at(tmp_path)
    event_file(controller, [{"type": "case_finished", "case_id": "tests/test_other.py::test_other", "status": "passed"}])
    controller._finish_abnormal("cancelled", "用户停止了测试任务")
    assert set(controller._finished) == {CASE, NEXT}


def test_recorded_business_failure_before_teardown_is_not_erased_by_stop(tmp_path):
    """业务断言已经失败而结束事件尚未写出时，停止仍保留这一失败。"""
    controller = controller_at(tmp_path)
    directory = case_report(controller, {"结果": "失败", "步骤": [
        {"状态": "失败", "错误": "AssertionError: 业务预期未满足"}]})
    controller._finish_abnormal("cancelled", "用户停止了测试任务")
    assert controller._finished[CASE]["status"] == "failed"
    assert controller._finished[CASE]["artifacts_dir"] == str(directory)


def test_interruption_exception_alone_is_not_reclassified_as_business_failure(tmp_path):
    """取消异常自身造成的步骤失败不能冒充业务失败。"""
    controller = controller_at(tmp_path)
    case_report(controller, {"结果": "失败", "步骤": [
        {"状态": "失败", "错误": "InterruptedError: 测试任务已停止"}]})
    controller._finish_abnormal("cancelled", "用户停止了测试任务")
    assert controller._finished[CASE]["status"] == "cancelled"


def test_repeated_failure_can_fill_duration_and_directory(tmp_path):
    """先收到不完整失败，后续真实结束事件可补齐耗时及现场路径。"""
    controller = controller_at(tmp_path)
    controller._finished[CASE] = {"case_id": CASE, "status": "failed", "duration": 0, "artifacts_dir": "", "message": "原始失败"}
    directory = case_report(controller, {"结果": "失败"})
    controller._remember_finished({"case_id": CASE, "status": "failed", "duration": 8.021, "artifacts_dir": str(directory)})
    assert controller._finished[CASE]["status"] == "failed"
    assert controller._finished[CASE]["duration"] == 8.021
    assert controller._finished[CASE]["artifacts_dir"] == str(directory)
    assert controller._finished[CASE]["message"] == "原始失败"


def test_late_cancel_event_is_forwarded_as_preserved_failure(tmp_path):
    """归一后的界面事件必须与最终汇总保持同一个失败状态。"""
    controller = controller_at(tmp_path)
    controller._finished[CASE] = {"case_id": CASE, "status": "failed", "duration": 5, "message": "原始失败", "artifacts_dir": ""}
    event_file(controller, [{"type": "case_finished", "case_id": CASE, "status": "cancelled", "duration": 0}])
    controller._started = 10**20
    controller._timeout = 1000
    controller._process = SimpleNamespace(poll=lambda: None)
    controller._worker_exited = lambda: False
    events = controller.poll()
    assert next(event for event in events if event["type"] == "case_finished")["status"] == "failed"


def test_incomplete_summary_and_only_images_remain_accessible(tmp_path):
    """损坏汇总或仅留下图片的中断现场仍保留真实存在的资料入口。"""
    controller = controller_at(tmp_path)
    directory = controller.run_dir / "cases" / hashlib.sha256(CASE.encode()).hexdigest()[:16]
    directory.mkdir(parents=True)
    (directory / "现场.png").write_bytes(b"saved-original")
    (controller.run_dir / "summary.json").write_text('{"cases":null}', encoding="utf-8")
    controller._finish_abnormal("cancelled", "用户停止了测试任务")
    diagnostics = (controller.run_dir / "diagnostics.md").read_text(encoding="utf-8")
    assert "现场.png" in diagnostics
    assert "report.json)" not in diagnostics and "diagnostics.md)" not in diagnostics
