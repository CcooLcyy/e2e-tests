"""通过上位机反馈和模拟设备实际收到的控制值验收 AGC 通信重分配。"""
import pytest

from testbench.pytest_plugin import fail_e2e_prerequisite


pytestmark = pytest.mark.case_group("AGC 通信重分配")


@pytest.mark.case_flow(
    ("三台 RTU 在线时，在上位机提交一次 300 kW 有功总目标", "三台各分配 100 kW"),
    ("观察用例外预先安排的第三台 RTU 失联", "另外两台各分配 150 kW，第三台保留原设定"),
    ("观察用例外预先安排的第三台 RTU 恢复", "无需再次下发总目标，三台重新各分配 100 kW"),
    blocked="当前缺少运行中 AGC 组的总目标提交入口，失联和恢复工况也需预先安排。以下为待验证流程。",
)
def test_agc_redistributes_when_rtu_member_loses_communication(request):
    """一次下发 300 kW，单台 RTU 通信异常后重新分配，恢复后自动恢复三台均分。"""
    fail_e2e_prerequisite(
        request,
        required="从 DSP 上位机提交 300 kW 总目标，第三台 RTU 的失联和恢复工况在用例外安排",
        missing="当前 DSP 上位机没有为运行中的 AGC 组提交总目标的业务界面，失联和恢复工况也需要预先安排。",
        checks=[("三台在线", "三台各分配 100 kW"),
                ("第三台失联", "其余两台各分配 150 kW，第三台保留原设定"),
                ("第三台恢复", "三台重新各分配 100 kW")],
    )
