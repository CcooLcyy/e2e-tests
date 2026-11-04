"""业务正文与完整诊断资料的隔离契约，不访问应用或账号。"""
import copy
import json
import pytest

from testbench.reporting import business_case, write_case_report, write_task_report


def test_raw_record_is_saved_even_when_diagnostic_layout_fails(tmp_path, monkeypatch):
    """诊断排版异常也不能阻止完整原始证据先落盘。"""
    import testbench.reporting as reporting
    result = {"结果": "失败", "错误": "原始异常", "截图": [], "步骤": []}
    def broken(*args):
        raise OSError("诊断排版失败")
    monkeypatch.setattr(reporting, "_write_case_diagnostics", broken)
    with pytest.raises(OSError):
        reporting.write_case_report(tmp_path, result)
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == result


def test_task_report_shows_recorded_end_time(tmp_path):
    """执行端的 ended_at 字段显示为业务报告结束时间。"""
    write_task_report(tmp_path, {"status": "cancelled", "ended_at": "2026-11-04T13:48:33+08:00", "cases": []}, {})
    assert "结束时间：2026-11-04T13:48:33+08:00" in (tmp_path / "report.md").read_text(encoding="utf-8")


def failure_result():
    return {"用例": "错误密码应有明确提示", "用例ID": "tests/test_login.py::test_bad_password", "结果": "失败",
            "源程序": "C:/private/app.exe", "错误": "Traceback: RuntimeError: internal_object",
            "阶段结果": [{"阶段": "call", "错误": "private traceback"}],
            "步骤": [
                {"序号": 1, "步骤": "验证错误提示", "状态": "失败", "预期": "明确提示密码错误", "实际": "登录失败，请检查连接地址"},
                {"序号": 2, "步骤": "提示符合标准", "状态": "失败", "预期": True, "实际": False}],
            "截图": [{"name": "step-0001-failure.png", "description": "错误提示现场", "step_id": 1,
                       "expected": "明确提示密码错误", "actual": "登录失败，请检查连接地址", "status": "failed",
                       "capture_status": "saved", "time": "2026-11-04", "error": "",
                       "annotated_name": "step-0001-failure.annotated.png", "annotation_status": "saved", "annotation_format": "business-v1"}]}


def test_case_report_separates_technical_details_without_modifying_records(tmp_path):
    """业务正文不带源码、路径或堆栈，完整原记录与诊断正文保留。"""
    result = failure_result()
    original = copy.deepcopy(result)
    write_case_report(tmp_path, result)
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    technical = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    assert result == original
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == original
    assert "明确提示密码错误" in report and "登录失败，请检查连接地址" in report
    for detail in ("Traceback", "RuntimeError", "internal_object", "C:/private", "tests/test_login.py", "private traceback"):
        assert detail not in report
        assert detail in technical
    assert "[诊断资料](diagnostics.md)" in report


def test_failure_summary_prefers_meaningful_outer_step_over_boolean_assertion():
    """失败摘要保留业务差异，不能退化成内层真假断言。"""
    view = business_case(failure_result())
    assert "明确提示密码错误" in view["summary"]
    assert "登录失败，请检查连接地址" in view["summary"]
    assert view["checks"][1]["expected"] == "是"
    assert view["checks"][1]["actual"] == "否"
    assert "True" not in str(view) and "False" not in str(view)


def test_unknown_legacy_data_and_technical_business_fields_are_not_invented():
    """历史缺失和误传的技术载荷仅展示未记录，原始资料仍由诊断保存。"""
    view = business_case({"结果": "失败", "步骤": [{"序号": 1, "步骤": "检查提示", "状态": "失败",
        "预期": "RuntimeError: private", "实际": "Traceback (most recent call last):\nsource.py:22"}]})
    assert view["checks"][0]["expected"] == "未记录"
    assert view["checks"][0]["actual"] == "未记录"
    assert "RuntimeError" not in str(view) and "source.py" not in str(view)
    assert business_case({})["result"] == "未判定"


def test_business_screenshots_are_limited_but_diagnostics_retains_every_image(tmp_path):
    """关键图片数量受控，全部逐步图与未关联原图仍在诊断中有入口。"""
    result = failure_result()
    for index in range(6):
        result["截图"].append({"name": f"step-{index + 2}.png", "description": "业务检查现场", "capture_status": "saved"})
    for shot in result["截图"]:
        (tmp_path / shot["name"]).write_bytes(b"original")
    (tmp_path / "orphan.png").write_bytes(b"older-original")
    write_case_report(tmp_path, result)
    view = business_case(result)
    assert len(view["key_screenshots"]) <= 3
    diagnostics = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    assert all(shot["name"] in diagnostics for shot in result["截图"])
    assert "orphan.png" in diagnostics


def test_key_image_uses_annotation_and_does_not_present_capture_state_as_final_result(tmp_path):
    """步骤拍摄时未结束只说明现场用途，正文不显示未判定或目标定位技术原因。"""
    result = failure_result()
    result["结果"] = "通过"
    result["步骤"] = []
    shot = result["截图"][0]
    shot.update(name="workspace.png", status="unknown", annotation_error="WebDriver failed",
                annotations=[{"label": "工作台", "reason": "可见目标不唯一，未画框"}])
    for name in (shot["name"], shot["annotated_name"]):
        (tmp_path / name).write_bytes(b"image")
    write_case_report(tmp_path, result)
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert shot["annotated_name"] in report
    assert "拍摄时判定" not in report and "未判定" not in report
    assert "WebDriver" not in report and "目标不唯一" not in report
    assert "最终结果以本用例结论为准" in report


def test_task_report_keeps_counts_names_links_and_details_separate(tmp_path):
    """正常和停止任务共用汇总：中文名称、完整计数、耗时和证据入口。"""
    case = tmp_path / "cases" / "first"
    case.mkdir(parents=True)
    (case / "report.json").write_text(json.dumps({"用例": "平台设置可以展开", "结果": "已停止"}), encoding="utf-8")
    (case / "report.md").write_text("业务报告", encoding="utf-8")
    summary = {"status": "cancelled", "message": "Traceback: private stop payload", "cases": [
        {"case_id": "tests/test_x.py::test_x", "status": "cancelled", "duration": 8.021, "artifacts_dir": str(case)},
        {"case_id": "tests/test_y.py::test_y", "status": "failed", "duration": 2},
        {"case_id": "tests/test_z.py::test_z", "status": "skipped", "message": "未执行", "duration": 0}]}
    task = {"case_names": {"tests/test_y.py::test_y": "认证提示明确"}, "environment": {"driver_path": "private driver"}}
    write_task_report(tmp_path, summary, task)
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "平台设置可以展开" in report and "认证提示明确" in report
    assert "已停止 1" in report and "失败 1" in report and "未执行 1" in report
    assert "8.021" in report and "cases/first/report.md" in report
    assert "test_x.py" not in report and "Traceback" not in report and "private driver" not in report
    diagnostics = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    assert "private stop payload" in diagnostics and "private driver" in diagnostics


def test_task_report_does_not_link_to_case_outside_run(tmp_path):
    """汇总中不可信产物地址不能引导业务报告访问任务外文件。"""
    write_task_report(tmp_path, {"status": "failed", "cases": [{"case_id": "internal", "status": "failed", "artifacts_dir": str(tmp_path.parent)}]}, {})
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "../" not in report and "internal" not in report
