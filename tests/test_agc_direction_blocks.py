"""从 DSP 上位机发起目标操作，通过对外反馈验收 AGC 增减闭锁。"""
import pytest

from testbench.pytest_plugin import fail_e2e_prerequisite


pytestmark = pytest.mark.case_group("AGC 增减闭锁")


def _agc_flow(initial, target, feedback):
    return pytest.mark.case_flow(
        ("确认运行中的 AGC 组、初始设定和 104 遥信映射", initial),
        ("在 DSP 上位机提交有功总目标", target),
        ("在模拟 104 主站观察新变位和相关反馈", feedback),
        blocked="当前上位机缺少运行中 AGC 组的有功总目标提交入口。以下为待验证流程。",
    )


def _report_missing_target_entry(request, expected):
    fail_e2e_prerequisite(
        request,
        required="从 DSP 上位机提交有功总目标，使用 104 主站观察反馈",
        missing="当前 DSP 上位机没有为运行中的 AGC 组提交有功总目标的业务界面。",
        checks=[("核对闭锁变位和实测反馈", expected)],
    )


@_agc_flow("初始设定处于允许区间内部", "设定达到上限", "收到新的有效增闭锁置位遥信，减闭锁保持未置位")
def test_agc_upper_limit_sets_increase_block(request):
    """AGC 下发设定到上限时增闭锁置位，104 主站收到有效的新遥信。"""
    _report_missing_target_entry(request, "从区间内部调到上限后收到新的有效增闭锁置位，减闭锁保持未置位")


@_agc_flow("初始设定在上限，增闭锁已置位", "设定从上限下调至区间内部", "收到新的有效增闭锁解除遥信，减闭锁保持未置位")
def test_agc_lower_target_clears_increase_block(request):
    """AGC 从上限下调至区间内部后增闭锁解除，104 主站收到新变位。"""
    _report_missing_target_entry(request, "从上限调回区间内部后收到新的有效增闭锁解除，减闭锁保持未置位")


@_agc_flow("初始设定处于允许区间内部", "设定达到下限", "收到新的有效减闭锁置位遥信，增闭锁保持未置位")
def test_agc_lower_limit_sets_decrease_block(request):
    """AGC 下发设定到下限时减闭锁置位，104 主站收到有效的新遥信。"""
    _report_missing_target_entry(request, "从区间内部调到下限后收到新的有效减闭锁置位，增闭锁保持未置位")


@_agc_flow("初始设定在下限，减闭锁已置位", "设定从下限上调至区间内部", "收到新的有效减闭锁解除遥信，增闭锁保持未置位")
def test_agc_higher_target_clears_decrease_block(request):
    """AGC 从下限上调至区间内部后减闭锁解除，104 主站收到新变位。"""
    _report_missing_target_entry(request, "从下限调回区间内部后收到新的有效减闭锁解除，增闭锁保持未置位")


@_agc_flow("初始实测功率低于上限", "下发设定达到上限", "新实测仍低于上限时即收到有效的增闭锁置位遥信，减闭锁保持未置位")
def test_agc_increase_block_does_not_wait_for_measured_limit(request):
    """实测仍低于上限时，AGC 按已下发上限设定置增闭锁并上送 104 遥信。"""
    _report_missing_target_entry(request, "调到上限后收到新的有效增闭锁置位，减闭锁保持未置位，且新实测仍低于上限")
