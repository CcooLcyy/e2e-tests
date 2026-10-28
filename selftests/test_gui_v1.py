"""验证界面状态映射，避免跳过和停止被误显示为通过。"""

from testbench.gui import status_label, status_color, format_value, format_history


def test_gui_status_colors_and_names():
    assert status_color("pending") == "#94a3b8"
    assert status_color("running") == "#94a3b8"
    assert status_color("passed") == "#16a34a"
    assert status_color("failed") == "#dc2626"
    assert status_color("skipped") == "#94a3b8"
    assert status_color("cancelled") == "#94a3b8"
    assert status_label("cancelled") == "已停止"
    assert status_label("skipped") == "未执行（跳过）"


def test_details_keep_zero_and_false_values():
    assert format_value(0) == "0"
    assert format_value(False) == "false"
    assert format_value(None) == "—"


def test_history_shows_chinese_case_summary_and_failure_reason():
    summary = {"status": "failed", "cases": [
        {"case_id": "a", "status": "passed", "duration": 0.2},
        {"case_id": "b", "status": "failed", "duration": 0.1, "message": "预期 10，实际 20"},
    ]}
    text = format_history(summary, {"environment": {"name": "联调环境", "mode": "workspace"}}, {"a": "验证连接", "b": "验证点值"})
    assert "总体结果：失败" in text
    assert "测试环境：联调环境" in text
    assert "固定测试工作区" in text
    assert "通过 1 项 · 失败 1 项" in text
    assert "验证连接" in text
    assert "预期 10，实际 20" in text
    assert '"case_id"' not in text


def test_gui_displays_cases_and_live_results(monkeypatch, tmp_path):
    """验证真实 Tk 控件可构建，运行状态与结束颜色同步到列表。"""
    import tkinter as tk
    from testbench.gui import TestbenchApp
    from testbench.model import CaseInfo
    import testbench.controller

    class Controller:
        active = False

        def __init__(self, root):
            pass

        def poll(self):
            return []

    case_id = "tests/test_example.py::test_example"
    monkeypatch.setattr(testbench.controller, "TaskController", Controller)
    monkeypatch.setattr(TestbenchApp, "refresh_cases", lambda app: app._catalog_received([CaseInfo(case_id, "示例用例", "界面状态验收")]))
    window = tk.Tk()
    window.withdraw()
    try:
        app = TestbenchApp(window, tmp_path)
        window.update_idletasks()
        assert app.case_tree.item(case_id, "values")[1] == "未执行"
        app._event({"type": "case_started", "case_id": case_id})
        assert "运行中" in app.case_tree.item(case_id, "values")[1]
        app._event({"type": "step", "case_id": case_id, "name": "检查返回值", "status": "running", "expected": 0})
        app._event({"type": "step", "case_id": case_id, "name": "检查返回值", "status": "passed", "expected": 0, "actual": 0})
        assert len(app.records[case_id]["steps"]) == 1
        assert app.records[case_id]["steps"][0]["actual"] == 0
        app._event({"type": "case_finished", "case_id": case_id, "status": "passed", "duration": 0.2})
        assert app.case_tree.item(case_id, "values")[1] == "通过"
        assert app.case_tree.item(case_id, "image")[0] == str(app.dot_images["passed"])
        app._event({"type": "case_finished", "case_id": case_id, "status": "failed", "duration": 0.3})
        assert app.case_tree.item(case_id, "image")[0] == str(app.dot_images["failed"])
    finally:
        window.destroy()
