"""从 DSP 上位机发起目标操作，通过对外反馈验收 AGC 增减闭锁。"""

import pytest

from testbench.blocked_control import report_blocked_control_case


pytestmark = pytest.mark.case_group("AGC 增减闭锁")

_REQUIRED = "从 DSP 上位机为运行中的专用 AGC 组提交有功总目标，使用独立的 104 主站观察反馈"
_BLOCKED = "当前 DSP 上位机没有为运行中的 AGC 组提交有功总目标的业务界面。"


def _agc_flow(prepare_kw, initial, target_kw, feedback):
    return pytest.mark.case_flow(
        ("登录独立的 DSP 上位机会话并打开运行中的专用 AGC 组",
         "当前组包含一个支持绝对值有功控制的成员，试验上下限为 10 和 100 kW"),
        ("连接独立的模拟 104 主站并读取初值", "可按专用点表观察所需遥信或遥测及其有效品质"),
        (f"在 DSP 上位机提交 {prepare_kw} kW，建立本例初态", initial),
        (f"记录 104 接收基线，在 DSP 上位机提交 {target_kw} kW", "上位机确认本轮有功总目标提交"),
        ("在同一模拟 104 主站会话中核对本轮新反馈", feedback),
        blocked=_BLOCKED,
    )


@_agc_flow(50, "增、减闭锁均为否且品质有效", 100, "新收到有效增闭锁置位，减闭锁保持为否且品质有效")
def test_agc_upper_limit_sets_increase_block(request):
    """AGC 下发设定到上限时增闭锁置位，104 主站收到有效的新遥信。"""
    report_blocked_control_case(request, required=_REQUIRED)


@_agc_flow(100, "增闭锁已置位且品质有效", 50, "新收到有效增闭锁解除，减闭锁保持为否且品质有效")
def test_agc_lower_target_clears_increase_block(request):
    """AGC 从上限下调至区间内部后增闭锁解除，104 主站收到新变位。"""
    report_blocked_control_case(request, required=_REQUIRED)


@_agc_flow(50, "增、减闭锁均为否且品质有效", 10, "新收到有效减闭锁置位，增闭锁保持为否且品质有效")
def test_agc_lower_limit_sets_decrease_block(request):
    """AGC 下发设定到下限时减闭锁置位，104 主站收到有效的新遥信。"""
    report_blocked_control_case(request, required=_REQUIRED)


@_agc_flow(10, "减闭锁已置位且品质有效", 50, "新收到有效减闭锁解除，增闭锁保持为否且品质有效")
def test_agc_higher_target_clears_decrease_block(request):
    """AGC 从下限上调至区间内部后减闭锁解除，104 主站收到新变位。"""
    report_blocked_control_case(request, required=_REQUIRED)


@_agc_flow(
    50, "有效实测功率严格位于 10 和 100 kW 之间", 100,
    "同一轮观察中，新收到有效增闭锁置位和低于 100 kW 的新有效实测，减闭锁为否且品质有效",
)
def test_agc_increase_block_does_not_wait_for_measured_limit(request):
    """实测仍低于上限时，AGC 按已下发上限设定置增闭锁并上送 104 遥信。"""
    report_blocked_control_case(request, required=_REQUIRED)
