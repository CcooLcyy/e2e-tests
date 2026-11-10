"""通过上位机反馈和模拟设备实际收到的控制值验收 AGC 通信重分配。"""
import pytest

from testbench.blocked_control import report_blocked_control_case


pytestmark = pytest.mark.case_group("AGC 通信重分配")


@pytest.mark.case_flow(
    ("登录独立的 DSP 上位机会话，打开运行中的专用 AGC 组并核对初态",
     "三台额定 200 kW 的 RTU 成员均在线，组按通信状态筛选成员，第三台的失联和恢复已由外部安排"),
    ("在上位机提交一次 300 kW 有功总目标，并观察 slaveSim 收到的设定", "三台各分配 100 kW"),
    ("保持原目标，观察第三台 RTU 失联后的通信状态及 slaveSim 设定",
     "另外两台各分配 150 kW，第三台保留原设定"),
    ("保持原目标，观察第三台 RTU 恢复后的通信状态及 slaveSim 设定",
     "无需再次下发总目标，三台重新各分配 100 kW"),
    blocked="当前 DSP 上位机没有为运行中的 AGC 组提交有功总目标的业务界面。"
            "第三台 RTU 的失联和恢复也必须在用例外预先安排，本例当前未检查该工况。",
)
def test_agc_redistributes_when_rtu_member_loses_communication(request):
    """一次下发 300 kW，单台 RTU 通信异常后重新分配，恢复后自动恢复三台均分。"""
    report_blocked_control_case(
        request,
        required="从 DSP 上位机提交 300 kW 总目标，第三台 RTU 的失联和恢复工况在用例外安排",
    )
