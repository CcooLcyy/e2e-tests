"""每个操作及检查的截图、关联和无法拍摄边界。"""
import pytest
from types import SimpleNamespace

from selftests.test_automatic_evidence import make_app, report_at


def test_each_step_and_check_capture_before_next_action(tmp_path):
    """每次操作结束立即拍摄，检查也有独立证据，不能只拍复合步骤末尾。"""
    app, calls = make_app(tmp_path)
    with app.step("填写账号"):
        calls.append(("输入账号", ""))
    with app.step("填写密码"):
        calls.append(("输入密码", ""))
    app.check("密码保持掩码", True, True)
    assert [kind for kind, _ in calls] == ["输入账号", "截图", "输入密码", "截图", "截图"]
    assert [shot["step_id"] for shot in app.screenshots] == [1, 2, 3]
    assert all(shot["capture_status"] == "saved" for shot in app.screenshots)
    assert len({shot["name"] for shot in app.screenshots}) == 3


def test_nested_same_named_steps_have_unique_correct_association(tmp_path):
    """父子和同名步骤独立编号，截图按完成顺序关联产生它的步骤。"""
    app, _ = make_app(tmp_path)
    with app.step("外层"):
        app.check("检查", 0, 0)
        app.check("检查", False, False)
    assert [step["序号"] for step in app.steps] == [1, 2, 3]
    assert [shot["step_id"] for shot in app.screenshots] == [2, 3, 1]
    assert [shot["actual"] for shot in app.screenshots[:2]] == [0, False]
    app.close()
    report = report_at(tmp_path)
    markdown = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    for step in report["步骤"]:
        shots = [shot for shot in report["截图"] if shot.get("step_id") == step["序号"]]
        assert len(shots) == 1
        if shots[0]["capture_status"] == "saved":
            assert f"]({shots[0]['name']})" in markdown
        else:
            assert shots[0]["error"] in markdown


def test_successful_step_screenshot_failure_records_missing_without_rewriting_result(tmp_path):
    """成功步骤截图失败明确缺失，保留独立的步骤通过结果。"""
    app, _ = make_app(tmp_path, save=False)
    app.check("界面检查", True, True)
    assert app.steps[0]["状态"] == "通过"
    assert app.failure is None
    assert app.screenshots[0]["capture_status"] == "missing"
    assert app.screenshots[0]["error"]


def test_unavailable_step_has_missing_record_without_reopening(tmp_path):
    """尚无驱动时也为该步骤记录缺失，不补开窗口。"""
    app, _ = make_app(tmp_path)
    app.driver = None
    with app.step("准备副本"):
        pass
    assert app.screenshots[0]["step_id"] == app.steps[0]["序号"]
    assert app.screenshots[0]["capture_status"] == "missing"
    assert "未连接或已关闭" in app.screenshots[0]["error"]


def test_cleanup_step_still_runs_after_stop(tmp_path, monkeypatch):
    """停止后不进入后续业务步骤，但清理步骤仍执行并记录证据。"""
    app, _ = make_app(tmp_path)
    stop = tmp_path / "stop"
    stop.touch()
    monkeypatch.setenv("TESTBENCH_STOP", str(stop))
    with pytest.raises(InterruptedError):
        with app.step("未执行的业务"):
            pytest.fail("停止后不能执行")
    with app.step("必要清理", cancellable=False):
        pass
    assert [step["步骤"] for step in app.steps] == ["必要清理"]
    assert app.screenshots[0]["capture_status"] == "saved"


def test_step_events_carry_stable_ids(tmp_path, monkeypatch):
    """事件开始和完成携带同一序号，重复名称不会使界面错配。"""
    from testbench import events
    records = []
    monkeypatch.setattr(events, "emit", records.append)
    app, _ = make_app(tmp_path)
    app.check("同名", True, True)
    app.check("同名", True, True)
    assert [(event["step_id"], event["status"]) for event in records] == [
        (1, "running"), (1, "passed"), (2, "running"), (2, "passed")]


def test_repeated_close_does_not_duplicate_finished_process_cleanup(tmp_path, monkeypatch):
    """进程已清理后重复关闭只更新报告，不重复执行或新增清理证据。"""
    app, _ = make_app(tmp_path)
    state = {"running": True, "stops": 0}
    app.process = SimpleNamespace(poll=lambda: None if state["running"] else 0)

    def stop():
        state["running"] = False
        state["stops"] += 1

    monkeypatch.setattr(app, "_stop_owned_app", stop)
    app.close()
    first_steps = list(app.steps)
    first_shots = list(app.screenshots)
    app.close()
    assert state["stops"] == 1
    assert app.steps == first_steps
    assert app.screenshots == first_shots


def test_cleanup_exception_keeps_original_business_error_and_its_own_evidence(tmp_path):
    """业务失败后关闭驱动再失败，保留首错及两个步骤各自的错误现场。"""
    app, _ = make_app(tmp_path, quit_error=True)
    with pytest.raises(AssertionError, match="业务原错"):
        app.check("业务结果", "业务原错", "预期结果")
    app.close()
    report = report_at(tmp_path)
    assert "业务原错" in report["错误"]
    cleanup = next(step for step in report["步骤"] if step["步骤"] == "关闭本次测试驱动")
    assert "驱动关闭失败" in cleanup["错误"]
    cleanup_shot = next(shot for shot in report["截图"] if shot.get("step_id") == cleanup["序号"])
    assert "驱动关闭失败" in cleanup_shot["actual"]
    assert report["截图"][0]["actual"].startswith("业务原错")
