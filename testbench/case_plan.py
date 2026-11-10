"""可执行用例步骤。步骤的说明和操作由同一份定义提供。"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

from .events import emit


@dataclass(frozen=True)
class Step:
    id: str
    action: str
    expected: str
    run: Callable[[object], None] | None = None
    when: Callable[[object], bool] | None = None


DESKTOP_START = Step("desktop.start", "准备并启动测试上位机", "独立测试实例启动，驱动接管窗口")
DESKTOP_FINISH = Step("desktop.finish", "收尾并清理桌面测试资源", "按测试环境设置结束桌面会话")
LOGIN_PREPARE = Step("login.prepare", "检查登录环境并读取专用账号", "使用全新副本及有效的测试账号配置")
LOGIN_GUARD = Step("login.guard", "检查专用账号的已有凭据", "专用账号没有已保存凭据")
LOGIN_CLEANUP = Step("login.cleanup", "关闭登录测试实例并清除测试凭据", "测试实例已关闭，专用账号凭据已清除")


def case_plan(*steps):
    if not steps or any(not step.id for step in steps) or len({step.id for step in steps}) != len(steps):
        raise ValueError("用例步骤必须具有非空且唯一的标识")

    def decorate(function):
        function._bench_case_plan = steps
        return function

    return decorate


def get_case_plan(function):
    return getattr(function, "_bench_case_plan", None)


def _record(item, step, status):
    if not hasattr(item, "_bench_plan_steps"):
        item._bench_plan_steps = {}
    item._bench_plan_steps[step.id] = status
    emit({"type": "plan_step", "case_id": item.nodeid, "plan_step_id": step.id, "status": status})


@contextmanager
def _track(item, step):
    _record(item, step, "running")
    try:
        yield
    except BaseException as error:
        _record(item, step, "cancelled" if isinstance(error, InterruptedError) else "failed")
        raise
    else:
        if item._bench_plan_steps[step.id] == "running":
            _record(item, step, "passed")


@contextmanager
def track_fixture_step(request, step):
    item = request.node
    if step in (get_case_plan(item.obj) or ()):
        with _track(item, step):
            yield
    else:
        yield


def run_case_plan(request, context):
    item = request.node
    for step in get_case_plan(item.obj):
        if step.run is None:
            continue
        context.desktop._check_stop()
        with _track(item, step):
            if step.when is not None and not step.when(context):
                _record(item, step, "skipped")
            else:
                step.run(context)


def desktop_action(id, action, expected, run, *, targets=(), save_as=None):
    def execute(context):
        with context.desktop.step(action, targets=targets):
            result = run(context)
            if save_as is not None:
                setattr(context, save_as, result)

    return Step(id, action, expected, execute)


def desktop_check(id, action, expected, actual, value, *, targets=()):
    def execute(context):
        expected_value = value(context) if callable(value) else value
        context.desktop.check(action, actual(context), expected_value, targets=targets)

    return Step(id, action, expected, execute)
