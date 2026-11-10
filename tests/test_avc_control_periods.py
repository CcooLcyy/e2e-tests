"""保留 AVC 周期验收目标，明确报告当前无法从上位机完成的前置条件。"""

import pytest

from testbench.blocked_control import report_blocked_control_case


pytestmark = pytest.mark.case_group("AVC 周期控制")


@pytest.mark.case_flow(
    ("登录独立的 DSP 上位机会话并打开专用 AVC 组",
     "可观察逐轮电压偏差和成员命令及其时间，预置实测为 220 V、初始总无功为 0 kVar"),
    ("将该组按定压周期模式运行", "计算周期 1 秒，命令最小间隔 4 秒，比例系数 2"),
    ("在上位机提交 230 V 目标并观察首轮成员设定", "首轮下发 20 kVar"),
    ("保持输入不变，连续观察三轮计算", "每轮电压偏差为 10 V，相邻计算至少间隔 1 秒"),
    blocked="当前上位机定压模式仅支持 PI 事件触发，且缺少提交目标电压的业务界面。",
)
def test_avc_voltage_deviation_recalculates_without_new_input(request):
    """AVC 定压持续偏差且没有新输入时按配置周期重新计算。"""
    report_blocked_control_case(
        request,
        required="上位机支持定压周期模式及目标电压业务操作，并能观察逐轮偏差和成员命令",
    )


@pytest.mark.case_flow(
    ("登录独立的 DSP 上位机会话并打开专用 AVC 组",
     "该组按总无功模式运行，计算周期 1 秒、命令最小间隔 4 秒，可以观察成员命令及其时间"),
    ("在上位机提交 10 kVar 总无功目标，记录首条成员命令的时间", "首条成员设定为 10 kVar"),
    ("首条下发后 4 秒内连续提交 20 至 49 kVar 的目标", "间隔窗口内不额外发布成员命令"),
    ("观察下一条成员设定", "取最新目标 49 kVar，与首条至少间隔 4 秒"),
    blocked="当前 DSP 上位机缺少提交 AVC 总无功目标的业务界面。",
)
def test_avc_burst_respects_command_interval_and_uses_latest_target(request):
    """总无功高频输入只在限频窗口后发布最新目标。"""
    report_blocked_control_case(
        request,
        required="上位机可发起总无功业务操作，并能记录目标到达和成员命令时刻",
    )


@pytest.mark.case_flow(
    ("登录独立的 DSP 上位机会话并打开专用 AVC 组",
     "该组按总无功模式运行，计算周期 1 秒、命令间隔 30 秒，可以观察逐轮计算和成员命令及其时间"),
    ("在上位机提交 10 kVar 总无功目标并观察首条成员命令", "首条成员设定为 10 kVar"),
    ("停组后在上位机提交 20 kVar", "上位机确认停组及目标提交"),
    ("保存计算周期 3 秒、命令间隔 7 秒后重新启动控制组", "启动后 3 秒内发布 20 kVar"),
    ("随后提交 30 kVar，观察相邻两轮计算", "计算从 20 更新到 30 kVar，间隔至少 3 秒且小于 4.5 秒"),
    ("观察成员命令更新", "发布 30 kVar，与重新启动后的首条命令至少间隔 7 秒且小于 15 秒"),
    blocked="当前 DSP 上位机缺少提交 AVC 总无功目标的业务界面，无法在运行或停组时提交本例目标。",
)
def test_avc_stopped_group_applies_new_periods_after_restart(request):
    """停止、保存并再启动 AVC 控制组后应用新的计算和命令周期。"""
    report_blocked_control_case(
        request,
        required="上位机可在运行和停组状态下发总无功目标，并能观察逐轮计算结果和成员命令",
    )
