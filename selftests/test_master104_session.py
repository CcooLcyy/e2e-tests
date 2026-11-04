"""主站会话证据契约，不使用实际主站或原生自动化依赖。"""
from pathlib import Path

import pytest

from testbench.master104 import Master104Session


def session(tmp_path, *, fail_capture=False):
    """替身仅生成本用例证据，不接管任何进程。"""
    calls = []

    def runner(request, timeout, **kwargs):
        calls.append(request["action"])
        if request["action"] == "capture":
            if fail_capture:
                raise RuntimeError("窗口被遮挡")
            Path(request["output"]).write_bytes(b"\x89PNG\r\n\x1a\n")
        return {"identity": {"pid": 10, "hwnd": 20, "path": "C:/ProIEC104Client.exe", "created": 30},
                "controls": [{"text": "连接状态"}], "observed_at": "2026-11-04T12:00:00+08:00"}

    return Master104Session(tmp_path, "C:/ProIEC104Client.exe", "win32", 1, runner=runner), calls


def test_session_preserves_local_step_ids_and_never_closes_master(tmp_path, monkeypatch):
    """步骤和截图按本地序号关联，事件使用来源命名空间，退出不关闭用户主站。"""
    events = []
    monkeypatch.setattr("testbench.master104.emit", events.append)
    app, calls = session(tmp_path)
    with app:
        app.check("窗口状态", "正常", "正常")
    assert [step["序号"] for step in app.steps] == [1, 2]
    assert app.steps[0]["诊断步骤"] is True
    assert "诊断步骤" not in app.steps[1]
    assert [shot["step_id"] for shot in app.screenshots] == [1, 2]
    assert all(shot["automatic"] is True for shot in app.screenshots)
    assert all(event["step_id"].startswith("master104:") for event in events)
    assert all(shot["name"].startswith("master104-") for shot in app.screenshots)
    assert "close" not in calls and not (tmp_path / "report.json").exists()


def test_original_assertion_survives_capture_failure(tmp_path):
    """采集故障不覆盖原始断言，缺失证据须带明确原因。"""
    app, _ = session(tmp_path, fail_capture=True)
    with pytest.raises(AssertionError, match="预期"):
        with app:
            app.check("结果", "实际", "预期")
    assert app.failure
    assert app.screenshots[-1]["capture_status"] == "missing"
    assert "遮挡" in app.screenshots[-1]["error"]
    assert app.screenshots[-1]["automatic"] is True


def test_explicit_capture_is_distinct_from_automatic_diagnostic_evidence(tmp_path):
    """用户主动选择的业务画面与框架自动取证分开标记，便于报告筛选。"""
    app, _ = session(tmp_path)
    with app:
        app.check("用户指定检查", True, True)
        app.capture("用户指定关键画面", expected="用户预期", actual="用户实际")
    explicit = [shot for shot in app.screenshots if not shot["automatic"]]
    assert len(explicit) == 1
    assert explicit[0]["step_id"] == app.steps[1]["序号"]
    assert explicit[0]["expected"] == "用户预期"
    assert all(shot["automatic"] for shot in app.screenshots if shot["step_id"] == 1)


@pytest.mark.parametrize("snapshot", [
    {"controls": [{"text": ""}], "observed_at": "2026-11-04"},
    {"controls": [{"text": "连接状态"}]},
])
def test_snapshot_rejects_missing_time_or_empty_text(tmp_path, snapshot):
    """不能把空树或缺少时间的历史数据当作本次有效观察。"""
    identity = {"path": "C:/ProIEC104Client.exe", "pid": 10, "hwnd": 20, "created": 30}
    app = Master104Session(tmp_path, identity["path"], runner=lambda *a, **k: {"identity": identity, **snapshot})
    with pytest.raises(RuntimeError, match="有效控件文本"):
        app.snapshot()
