"""六路1至6台预置华为行为模型的双端采集验收。"""
import json
import logging
import os
import sys
from pathlib import Path

import pytest

from testbench.modbus_environment import HUAWEI_CASE_ID, validate_selected_modbus_cases
from testbench.huawei_fleet_run import HuaweiFleetRun
from testbench.login_page import PlatformLoginPage, load_login_account
from testbench.model import validate_slavesim_url
from testbench.rtu_observation import FleetObservationProfile, PreparedRtuPage


pytestmark = pytest.mark.case_group("Modbus RTU")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def huawei_fleet(request):
    """先验证环境，再打开两端；失败现场先于本例浏览器关闭。"""
    from testbench.huawei_slavesim_page import HuaweiSlaveSimPage

    task_path = os.environ.get("TESTBENCH_TASK")
    if not task_path:
        raise RuntimeError("请从测试上位机执行华为机群用例，以读取任务环境和模拟器地址")
    task = json.loads(Path(task_path).read_text(encoding="utf-8"))
    environment = task["environment"]
    validate_selected_modbus_cases(environment, [HUAWEI_CASE_ID])
    url = validate_slavesim_url(environment.get("slavesim_url", ""), required=True)
    config, buses = FleetObservationProfile.load(ROOT / "local" / "huawei-rtu.json",
                                                float(environment.get("modbus_acquisition_timeout", 300)))
    account = load_login_account(ROOT / "local" / "login-account.json")
    desktop = request.getfixturevalue("desktop")
    desktop.metadata["华为验收阶段"] = "前置条件"
    upper = PreparedRtuPage(desktop)
    with HuaweiSlaveSimPage(desktop, url) as simulator:
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
            PlatformLoginPage(desktop).login(account, remember=False, auto=False)
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


def test_huawei_six_buses_21_devices_match(huawei_fleet):
    """六路21台华为逆变器逐台核对遥测、状态及外部工况反馈。"""
    huawei_fleet.run()
