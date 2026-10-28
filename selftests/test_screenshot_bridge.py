"""截图证据说明的读取与历史兼容验收，不连接真实应用。"""
import json

from testbench.bridge import TestbenchBridge


def make_case(tmp_path, records=None):
    case = tmp_path / "artifacts" / "task" / "cases" / "first"
    case.mkdir(parents=True)
    (case / "report.json").write_text(json.dumps({"截图": records or []}), encoding="utf-8")
    return case


def test_saved_screenshot_preserves_explanation_and_check_result(tmp_path):
    """截图说明关联实际图片，拍摄时失败判定不能被界面改成通过。"""
    record = {"name": "failure.png", "description": "错误密码登录后的提示",
              "expected": "显示错误提示", "actual": "进入工作台", "status": "failed",
              "capture_status": "saved", "error": "", "time": "2026-10-28T12:00:00+08:00"}
    case = make_case(tmp_path, [record])
    (case / "failure.png").write_bytes(b"\x89PNG\r\n\x1a\ntest")
    details = TestbenchBridge(tmp_path).handle("case_detail", {"artifacts_dir": str(case)})
    shot = details["screenshots"][0]
    assert all(shot[key] == value for key, value in record.items())
    assert shot["data_url"].startswith("data:image/png;base64,")


def test_legacy_screenshot_does_not_invent_observations_or_pass_status(tmp_path):
    """旧图缺少说明时明确标记未提供，不能以存在图片推断检查通过。"""
    case = make_case(tmp_path)
    (case / "old.png").write_bytes(b"\x89PNG\r\n\x1a\ntest")
    shot = TestbenchBridge(tmp_path).handle("case_detail", {"artifacts_dir": str(case)})["screenshots"][0]
    assert "未提供" in shot["description"]
    assert "未提供" in shot["expected"]
    assert "未提供" in shot["actual"]
    assert shot["status"] == "unknown"


def test_missing_screenshot_keeps_reason_for_display(tmp_path):
    """无法截图时仍向界面返回证据缺失及原因，不能成为空白的截图区。"""
    record = {"name": "result.png", "description": "用例结束现场", "expected": "未提供业务预期",
              "actual": "应用已退出", "status": "failed", "capture_status": "missing",
              "error": "截图失败：驱动未连接", "time": "2026-10-28T12:00:00+08:00"}
    case = make_case(tmp_path, [record])
    shot = TestbenchBridge(tmp_path).handle("case_detail", {"artifacts_dir": str(case)})["screenshots"][0]
    assert shot["capture_status"] == "missing"
    assert shot["error"] == record["error"]
    assert shot["data_url"] == ""


def test_record_cannot_read_image_from_outside_case_directory(tmp_path):
    """说明中的文件名不能扩大图片读取范围；仅展示缺失记录。"""
    case = make_case(tmp_path, [{"name": "../../outside.png", "capture_status": "saved"}])
    outside = case.parent.parent / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nprivate")
    shot = TestbenchBridge(tmp_path).handle("case_detail", {"artifacts_dir": str(case)})["screenshots"][0]
    assert shot["capture_status"] == "missing"
    assert shot["data_url"] == ""
