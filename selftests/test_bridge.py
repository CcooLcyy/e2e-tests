"""React/Tauri 到 Python 的协议与文件边界验收。"""
import json
from pathlib import Path
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from testbench.bridge import TestbenchBridge, respond


def test_protocol_errors_keep_request_id_and_do_not_terminate_bridge(tmp_path):
    """未知操作明确失败，后续正常请求仍可使用桥接进程。"""
    bridge = TestbenchBridge(tmp_path)
    response = respond(bridge, {"id": 8, "method": "shell", "payload": {}})
    assert response["id"] == 8
    assert response["ok"] is False
    assert "error" in response
    response = respond(bridge, {"id": 9, "method": "get_environment", "payload": {}})
    assert response["ok"] is True
    assert response["result"]["mode"] == "fresh"


def test_artifact_details_reject_paths_outside_run_area(tmp_path):
    """桥接不能将任意本机文件通过界面读取。"""
    bridge = TestbenchBridge(tmp_path)
    (tmp_path / "outside").mkdir()
    with pytest.raises(ValueError):
        bridge.handle("case_detail", {"artifacts_dir": str(tmp_path / "outside")})


def test_history_summarizes_real_reports_and_case_details(tmp_path):
    """历史任务及单例报告引用真实文件，不捏造通过状态。"""
    run = tmp_path / "artifacts" / "task-1"
    case = run / "cases" / "first"
    case.mkdir(parents=True)
    summary = {"status": "failed", "environment_name": "实验环境", "cases": [
        {"case_id": "tests/test_one.py::test_value", "status": "failed", "duration": 0.3, "artifacts_dir": str(case)}]}
    (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run / "task.json").write_text(json.dumps({"environment": {"name": "实验环境"}}), encoding="utf-8")
    (case / "report.json").write_text(json.dumps({"结果": "失败", "步骤": [{"预期": 0, "实际": False}]}), encoding="utf-8")
    bridge = TestbenchBridge(tmp_path)
    item = bridge.handle("history", {})[0]
    assert item["failed"] == 1
    assert item["passed"] == 0
    assert item["environment_name"] == "实验环境"
    details = bridge.handle("history_detail", {"run_dir": str(run)})
    assert details["cases"][0]["report"]["结果"] == "失败"


def test_latest_case_results_use_each_cases_most_recent_execution(tmp_path):
    first, second, third = (
        f"tests/test_sample.py::test_value[{index}]" for index in range(3)
    )

    def report(index, cases):
        directory = tmp_path / "artifacts" / f"task-{index}"
        directory.mkdir(parents=True)
        summary = directory / "summary.json"
        summary.write_text(json.dumps({"cases": cases}), encoding="utf-8")
        modified_at = 1_700_000_000 + index
        os.utime(summary, (modified_at, modified_at))

    report(1, [{"case_id": first, "status": "passed"}])
    report(2, [{"case_id": first, "status": "failed"}, {"case_id": second, "status": "passed"}])
    for index in range(3, 103):
        report(index, [{"case_id": third, "status": "passed"}])
    report(103, [{"case_id": second, "status": "skipped", "message": "未执行"},
                 {"case_id": third, "status": "failed"}])

    snapshot = TestbenchBridge(tmp_path).handle("history_snapshot")
    assert len(snapshot["history"]) == 100
    assert snapshot["latest_case_results"] == {first: "failed", second: "passed", third: "failed"}


def test_poll_reads_controller_events_and_shutdown_waits_for_cleanup(tmp_path):
    """关闭协议等待工作进程清理完成，不能只发送停止就返回。"""
    bridge = TestbenchBridge(tmp_path)
    state = {"polls": 0, "stopped": False}
    controller = SimpleNamespace(active=True, run_dir=tmp_path / "artifacts")
    def poll():
        state["polls"] += 1
        if state["stopped"]:
            controller.active = False
        return [{"type": "log", "message": "正在清理"}]
    controller.poll = poll
    controller.stop = lambda: state.update(stopped=True)
    bridge.controller = controller
    assert bridge.handle("poll", {})["active"] is True
    assert bridge.handle("shutdown", {})["closed"] is True
    assert not controller.active
    assert state["polls"] >= 2


def test_json_line_process_survives_malformed_request_and_exits_cleanly(tmp_path):
    """真实桥接子进程每行只输出一个响应，错误后可以继续且正常关闭。"""
    root = Path(__file__).resolve().parent.parent
    requests = [json.dumps({"id": 1, "method": "get_environment"}), "invalid-json",
                json.dumps({"id": 2, "method": "shutdown"})]
    result = subprocess.run([sys.executable, "-u", "-m", "testbench.bridge", "--root", str(tmp_path)],
                            cwd=root, input="\n".join(requests) + "\n", capture_output=True,
                            encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"), timeout=10)
    assert result.returncode == 0
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 3
    assert [item["ok"] for item in responses] == [True, False, True]
    assert responses[-1]["result"] == {"closed": True}
