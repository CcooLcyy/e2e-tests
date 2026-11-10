"""六路1至6台预置华为行为模型的双端采集验收。"""
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from testbench.case_plan import (
    ContinueAfterFailure, DESKTOP_FINISH, DESKTOP_START, Step, case_plan,
    run_steps, track_fixture_context, track_fixture_step,
)
from testbench.huawei_fleet_run import DEVICE_EXPECTED, STAGES, HuaweiFleetRun, device_step_title
from testbench.login_page import PlatformLoginPage, load_login_account
from testbench.login_plan import login_steps
from testbench.modbus_environment import HUAWEI_CASE_ID, validate_selected_modbus_cases
from testbench.model import validate_slavesim_url
from testbench.rtu_observation import FleetObservationProfile, PreparedRtuPage
from testbench.workspace import local_config


pytestmark = pytest.mark.case_group("Modbus RTU")

FLEET_PREPARE = Step("fleet.prepare", "读取华为机群观察配置和专用账号", "六路预置总线配置与测试账号有效")
SIMULATOR_START = Step("fleet.simulator.open", "打开 slaveSim 观察会话", "运行页面显示新的状态刷新")
SIMULATOR_FINISH = Step("fleet.simulator.close", "关闭 slaveSim 观察会话", "释放本次浏览器资源")
OPEN_LINKS = Step("fleet.links.open", "打开上位机预置 RTU 连接列表", "可观察已预置的采集连接")
LOGIN_STEPS = login_steps("login")


@pytest.fixture
def huawei_fleet(request):
    """先验证环境，再打开两端；失败现场先于本例浏览器关闭。"""
    from testbench.huawei_slavesim_page import HuaweiSlaveSimPage

    with track_fixture_step(request, FLEET_PREPARE):
        task_path = os.environ.get("TESTBENCH_TASK")
        if not task_path:
            raise RuntimeError("请从测试上位机执行华为机群用例，以读取任务环境和模拟器地址")
        task = json.loads(Path(task_path).read_text(encoding="utf-8"))
        environment = task["environment"]
        validate_selected_modbus_cases(environment, [HUAWEI_CASE_ID])
        url = validate_slavesim_url(environment.get("slavesim_url", ""), required=True)
        config, buses = FleetObservationProfile.load(local_config("huawei-rtu.json"),
                                                    float(environment.get("modbus_acquisition_timeout", 300)))
        account = load_login_account(local_config("login-account.json"))
    desktop = request.getfixturevalue("desktop")
    desktop.metadata["华为验收阶段"] = "前置条件"
    upper = PreparedRtuPage(desktop)
    with track_fixture_context(request, SIMULATOR_START, SIMULATOR_FINISH, HuaweiSlaveSimPage(desktop, url)) as simulator:
        scenario = None
        setup_failed = False

        def capture_failure(description):
            if scenario is not None:
                scenario.capture_both(description)
                return
            # 预置资源无法观察时仍保留两端现场。
            for source, capture in (("slaveSim", simulator.capture), ("DSP上位机", desktop.capture)):
                try:
                    capture("huawei-runtime-preparation-failed", description=f"{source}：{description}")
                except Exception as error:
                    logging.getLogger(__name__).exception("%s 运行时准备失败现场采集异常", source)
                    desktop.metadata.setdefault("华为取证错误", []).append(f"{source}：{error}")

        try:
            run_steps(request, LOGIN_STEPS, SimpleNamespace(desktop=desktop, page=PlatformLoginPage(desktop), account=account))
            with track_fixture_step(request, OPEN_LINKS):
                upper.open_prepared_links()
            scenario = HuaweiFleetRun(desktop, simulator, upper, config, "prepared", buses=buses)
            desktop.metadata["华为验收阶段"] = "观察六路21台预置模型与采集反馈"
            yield scenario
        except BaseException as error:
            setup_failed = True
            desktop.failure = desktop.failure or str(error)
            capture_failure("前置阶段失败，保留两端现场")
            raise
        finally:
            if not setup_failed and any(report.failed for report in getattr(request.node, "_bench_reports", [])):
                capture_failure("机群验收失败，关闭浏览器前现场")
            had_failure = setup_failed or sys.exc_info()[0] is not None or any(
                report.failed for report in getattr(request.node, "_bench_reports", []))
            try:
                if scenario is not None:
                    scenario.write_report()
            except Exception as error:
                desktop.metadata["华为报告写入错误"] = str(error)
                logging.getLogger(__name__).exception("华为汇总报告写入失败，保留原始业务错误")
                if not had_failure:
                    raise


def _device_step(stage_index, stage, route, station):
    def observe(fleet):
        # 专项配置按路号生成拓扑，运行时使用其中的现场总线名和实际串口。
        bus = fleet.buses[route - 1]
        device = bus["devices"][station - 1]
        error = fleet.observe_device(stage, bus, device)
        if error is not None:
            raise ContinueAfterFailure(str(error)) from error

    return Step(f"fleet.stage-{stage_index}.route-{route}.station-{station}",
                device_step_title(stage, route, station), DEVICE_EXPECTED, observe, group=stage)


BUSINESS_STEPS = (
    Step("fleet.topology", "核对六路 21 台预置拓扑", "按现场总线、串口和站号登记对应的采集连接",
         lambda fleet: fleet.select_prepared_topology()),
    *(_device_step(stage_index, stage, route, station)
      for stage_index, stage in enumerate(STAGES, 1)
      for route in range(1, 7)
      for station in range(1, route + 1)),
    Step("fleet.summary", "核对机群汇总结果", "所有设备工况均已完成且通过，并保留双端证据",
         HuaweiFleetRun.assert_complete),
)


@case_plan(
    FLEET_PREPARE, DESKTOP_START, SIMULATOR_START, *LOGIN_STEPS, OPEN_LINKS,
    *BUSINESS_STEPS, SIMULATOR_FINISH, DESKTOP_FINISH,
)
def test_huawei_six_buses_21_devices_match(request, huawei_fleet):
    """六路21台华为逆变器逐台核对遥测、状态及外部工况反馈。"""
    run_steps(request, BUSINESS_STEPS, huawei_fleet)
