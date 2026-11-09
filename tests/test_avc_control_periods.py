"""保留 AVC 周期验收目标，明确报告当前无法从上位机完成的前置条件。"""

import pytest

from testbench.pytest_plugin import fail_e2e_prerequisite


pytestmark = [pytest.mark.case_group("AVC 周期控制"), pytest.mark.opt_in]


def test_avc_voltage_deviation_recalculates_without_new_input(request):
    """AVC 定压持续偏差且没有新输入时按配置周期重新计算。"""
    fail_e2e_prerequisite(
        request,
        required="上位机支持定压周期模式及目标电压业务操作，并能观察逐轮偏差和成员命令",
        missing="当前上位机的定压模式仅支持 PI 事件触发，且 AVC 页面没有实际目标电压下发入口。",
        checks=[
            ("定压首轮发布成员设定", "目标 230 V、实测 220 V、总无功 0 kVar、比例系数 2 时发布 20 kVar"),
            ("无新输入时持续计算定压偏差", "连续三轮偏差均为 10 V，相邻计算结果至少间隔 1000 毫秒"),
        ],
    )


def test_avc_burst_respects_command_interval_and_uses_latest_target(request):
    """总无功高频输入只在限频窗口后发布最新目标。"""
    fail_e2e_prerequisite(
        request,
        required="上位机可发起总无功业务操作，并能记录目标到达和成员命令时刻",
        missing="当前 AVC 页面配置和显示总无功命令点，但没有实际目标下发入口。",
        checks=[
            ("总无功周期控制", "计算周期 1 秒、命令最小间隔 4 秒，首条成员设定为 10 kVar"),
            ("高频目标按最小间隔合并", "在首条下发后 4000 毫秒内输入 20 至 49 kVar；下一条成员设定为 49 kVar，且与首条至少间隔 4000 毫秒"),
        ],
    )


def test_avc_stopped_group_applies_new_periods_after_restart(request):
    """停止、保存并再启动 AVC 控制组后应用新的计算和命令周期。"""
    fail_e2e_prerequisite(
        request,
        required="上位机可在运行和停组状态下发总无功目标，并能观察逐轮计算结果和成员命令",
        missing="当前 AVC 页面没有总无功目标下发入口，无法在停组时通过业务操作提交 20 kVar 或在重启后提交 30 kVar。",
        checks=[
            ("旧周期运行", "计算 1 秒、命令间隔 30 秒，首条成员设定为 10 kVar"),
            ("停组保存并按新周期启动", "停组时提交 20 kVar，保存计算 3 秒、命令间隔 7 秒，重启后在 3000 毫秒内发布 20 kVar"),
            ("应用新计算周期", "随后提交 30 kVar，相邻两轮计算从 20 kVar 更新到 30 kVar，间隔至少 3000 且小于 4500 毫秒"),
            ("应用新命令周期", "随后发布 30 kVar，与重启后的首条命令至少间隔 7000 且小于 15000 毫秒"),
        ],
    )
