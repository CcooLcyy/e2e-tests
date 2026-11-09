"""真实104遥调驱动AGC，双端核验当前设定值到限闭锁与解除。"""
import json
import logging
import os
from pathlib import Path

import pytest

from testbench.agc_direction_scenario import AgcDirectionProfile, AgcDirectionScenario
from testbench.login_page import PlatformLoginPage, load_login_account
from testbench.model import Environment


pytestmark = pytest.mark.case_group("AGC 增减闭锁")
ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


@pytest.fixture
def agc_direction(request):
    """只接入明确的专用测试组；前置不满足即失败，不修改被测配置。"""
    from testbench.agc_direction_page import AgcDirectionPage
    from testbench.master104_commands import Master104CommandSender, normalize_target
    from testbench.master104_signals import Master104PointObserver

    task_path = os.environ.get("TESTBENCH_TASK")
    if not task_path:
        raise RuntimeError("AGC双端验收须从测试上位机执行，以读取环境快照")
    environment = Environment.from_dict(json.loads(Path(task_path).read_text(encoding="utf-8"))["environment"])
    environment.validate_master104(required=True)
    if environment.mode != "fresh":
        raise RuntimeError("AGC闭锁验收要求全新独立上位机副本；下位机专用测试组需事先准备")
    profile = AgcDirectionProfile.load(ROOT / "local" / "agc-direction-block.json")
    for value in (profile.lower_kw, profile.middle_kw, profile.upper_kw):
        normalize_target(value)
    account = load_login_account(ROOT / "local" / "login-account.json")
    desktop = request.getfixturevalue("desktop")
    master = None

    def capture_failure():
        for source, session in (("DSP上位机", desktop), ("104主站", master)):
            if session is None:
                continue
            try:
                session.capture("agc-direction-failure", description=f"{source}：AGC闭锁验收失败现场，未发送恢复指令")
            except Exception as error:
                desktop.metadata.setdefault("AGC取证错误", []).append(f"{source}：{error}")
                LOGGER.exception("%s AGC失败现场采集异常，保留原业务失败", source)

    setup_failed = False
    try:
        desktop.metadata["AGC验收阶段"] = "前置条件"
        desktop.metadata["AGC固定验收配置"] = profile.to_dict()
        (desktop.artifacts / "agc-direction-profile.json").write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        PlatformLoginPage(desktop).login(account, remember=False, auto=False)
        page = AgcDirectionPage(desktop)
        page.connect_device(profile.manager_address)
        page.select_group(profile.group_name, profile.conn_id)
        # 任何控制操作前完整检查组和成员约束，不能以现状反推固定验收期望。
        member = page.read_member(profile.member_name)
        desktop.check("专用成员下限与固定期望一致", member["lower_kw"], profile.lower_kw)
        desktop.check("专用成员有效上限与固定期望一致", member["upper_kw"], profile.upper_kw)
        master = request.getfixturevalue("master104")
        master.connect_and_interrogate(profile.master_host, profile.master_port, profile.common_address)
        observer = Master104PointObserver(master, profile.ioas, profile.common_address)
        sender = Master104CommandSender(master, profile.common_address, profile.command_ioa)
        desktop.metadata["AGC验收阶段"] = "104遥调与双端业务验收"
        yield AgcDirectionScenario(desktop, page, master, observer, sender, profile)
    except BaseException:
        setup_failed = True
        capture_failure()
        raise
    finally:
        if not setup_failed and any(report.failed for report in getattr(request.node, "_bench_reports", [])):
            capture_failure()
        # 专用组和路由由用户管理；失败后不补发中间值或恢复指令。


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
