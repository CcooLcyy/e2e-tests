"""从104主站侧验收AGC增减闭锁和解除。"""
import json
from pathlib import Path

import pytest

from testbench.agc_direction_scenario import AgcDirectionProfile, AgcDirectionScenario


pytestmark = pytest.mark.case_group("AGC 增减闭锁")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def agc_direction(request):
    """使用独立104主站会话观察专用下位机的协议行为。"""
    from testbench.master104_commands import Master104CommandSender, normalize_target
    from testbench.master104_signals import Master104PointObserver

    profile = AgcDirectionProfile.load(ROOT / "local" / "agc-direction-block.json")
    for value in (profile.lower_kw, profile.middle_kw, profile.upper_kw):
        normalize_target(value)
    master = request.getfixturevalue("master104")
    master.metadata["AGC固定验收配置"] = profile.to_dict()
    (master.artifacts / "agc-direction-profile.json").write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    master.connect_and_interrogate(profile.master_host, profile.master_port, profile.common_address)
    observer = Master104PointObserver(master, profile.ioas, profile.common_address, measured_ioa=profile.measured_ioa)
    sender = Master104CommandSender(master, profile.common_address, profile.command_ioa)
    return AgcDirectionScenario(master, observer, sender, profile)


def test_agc_upper_limit_sets_increase_block(agc_direction):
    """AGC下发设定到上限时增闭锁置位，104主站收到有效的新遥信。"""
    profile = agc_direction.profile
    agc_direction.run_case(profile.middle_kw, profile.upper_kw, "increase")


def test_agc_lower_target_clears_increase_block(agc_direction):
    """AGC从上限下调至区间内部后增闭锁解除，104主站收到新变位。"""
    profile = agc_direction.profile
    agc_direction.run_case(profile.upper_kw, profile.middle_kw, "increase")


def test_agc_lower_limit_sets_decrease_block(agc_direction):
    """AGC下发设定到下限时减闭锁置位，104主站收到有效的新遥信。"""
    profile = agc_direction.profile
    agc_direction.run_case(profile.middle_kw, profile.lower_kw, "decrease")


def test_agc_higher_target_clears_decrease_block(agc_direction):
    """AGC从下限上调至区间内部后减闭锁解除，104主站收到新变位。"""
    profile = agc_direction.profile
    agc_direction.run_case(profile.lower_kw, profile.middle_kw, "decrease")


def test_agc_increase_block_does_not_wait_for_measured_limit(agc_direction):
    """实测仍低于上限时，AGC按已下发上限设定置增闭锁并上送104遥信。"""
    profile = agc_direction.profile
    agc_direction.run_case(profile.middle_kw, profile.upper_kw, "increase", require_lag=True)
