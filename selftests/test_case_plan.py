"""计划步骤中断后保留已执行状态，收尾仍独立记录。"""

from types import SimpleNamespace

import pytest

from testbench import case_plan as plan


def test_failed_step_preserves_conditional_and_cleanup_status(monkeypatch):
    events = []
    monkeypatch.setattr(plan, "emit", events.append)

    def should_not_run(_context):
        pytest.fail("不应执行此步骤")

    def fail(_context):
        raise AssertionError("用例断言失败")

    @plan.case_plan(
        plan.Step("first", "第一步", "通过", lambda _: None),
        plan.Step("optional", "条件步骤", "满足条件时执行", should_not_run, when=lambda _: False),
        plan.Step("failure", "第二步", "通过", fail),
        plan.Step("later", "第三步", "通过", should_not_run),
        plan.DESKTOP_FINISH,
    )
    def example():
        pass

    item = SimpleNamespace(obj=example, nodeid="example")
    request = SimpleNamespace(node=item)
    context = SimpleNamespace(desktop=SimpleNamespace(_check_stop=lambda: None))
    with pytest.raises(AssertionError, match="用例断言失败"):
        try:
            plan.run_case_plan(request, context)
        finally:
            with plan.track_fixture_step(request, plan.DESKTOP_FINISH):
                pass

    assert item._bench_plan_steps == {
        "first": "passed",
        "optional": "skipped",
        "failure": "failed",
        "desktop.finish": "passed",
    }
    assert all(event["type"] == "plan_step" and event["case_id"] == "example" for event in events)
    assert [(event["plan_step_id"], event["status"]) for event in events if event["status"] != "running"] == [
        ("first", "passed"), ("optional", "skipped"), ("failure", "failed"), ("desktop.finish", "passed"),
    ]
