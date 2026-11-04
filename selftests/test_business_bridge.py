"""业务视图读取独立于原始证据，历史适配不重写文件；等待用户按需运行。"""
import hashlib
import json

from testbench.bridge import TestbenchBridge


def test_case_details_adds_business_view_but_preserves_technical_record(tmp_path):
    """默认业务摘要不展示原始异常，原 JSON 和原始桥接字段完整保留。"""
    directory = tmp_path / "artifacts" / "task" / "cases" / "one"
    directory.mkdir(parents=True)
    record = {"用例": "登录错误提示", "结果": "失败", "错误": "RuntimeError: private-driver-payload",
              "阶段结果": [{"阶段": "call", "错误": "Traceback private-driver-payload"}],
              "步骤": [{"序号": 1, "步骤": "认证错误提示", "状态": "失败", "预期": "明确密码错误", "实际": "通用登录失败"}], "截图": []}
    path = directory / "report.json"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    original = path.read_bytes()
    details = TestbenchBridge(tmp_path)._case_details(str(directory))
    assert details["report"] == record
    assert "private-driver-payload" not in json.dumps(details["business"], ensure_ascii=False)
    assert details["business"]["result"] == "失败"
    assert details["diagnostics_path"] == str(path.resolve())
    assert not details["business_report_path"]
    assert path.read_bytes() == original


def test_history_recovers_missing_case_directory_without_changing_summary(tmp_path):
    """旧停止记录无路径时按用例编号查找已有证据，仅做读取适配。"""
    run = tmp_path / "artifacts" / "task"
    case_id = "tests/test_demo.py::test_cancel"
    directory = run / "cases" / hashlib.sha256(case_id.encode()).hexdigest()[:16]
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps({"用例ID": case_id, "用例": "停止现场", "结果": "已停止", "步骤": [], "截图": []}), encoding="utf-8")
    summary = run / "summary.json"
    summary.write_text(json.dumps({"cases": [{"case_id": case_id, "status": "cancelled", "artifacts_dir": ""}]}), encoding="utf-8")
    (run / "task.json").write_text("{}", encoding="utf-8")
    before = summary.read_bytes()
    details = TestbenchBridge(tmp_path).handle("history_detail", {"run_dir": str(run)})
    assert details["cases"][0]["business"]["title"] == "停止现场"
    assert details["cases"][0]["diagnostics_path"]
    assert summary.read_bytes() == before


def test_history_cannot_attach_other_task_evidence(tmp_path):
    """历史记录不能把另一任务的证据冒充当前用例报告。"""
    run = tmp_path / "artifacts" / "task"
    other = tmp_path / "artifacts" / "other" / "case"
    run.mkdir(parents=True)
    other.mkdir(parents=True)
    (other / "report.json").write_text(json.dumps({"用例": "其他任务", "步骤": [], "截图": []}), encoding="utf-8")
    (run / "summary.json").write_text(json.dumps({"cases": [{"case_id": "x", "status": "failed", "artifacts_dir": str(other)}]}), encoding="utf-8")
    (run / "task.json").write_text("{}", encoding="utf-8")
    details = TestbenchBridge(tmp_path).handle("history_detail", {"run_dir": str(run)})
    assert not details["cases"][0].get("report")
