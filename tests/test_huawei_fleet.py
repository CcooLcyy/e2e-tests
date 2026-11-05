"""六路1至6台华为行为模型的真实双端配置与采集验收。"""
import json
import logging
import os
import sys
from pathlib import Path
import uuid

import pytest

from testbench.modbus_environment import huawei_config_from_environment
from testbench.huawei_fleet_run import HuaweiFleetRun
from testbench.login_page import PlatformLoginPage, load_login_account
from testbench.model import validate_slavesim_url


pytestmark = pytest.mark.case_group("Modbus RTU")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def huawei_fleet(request):
    """先验证环境，再打开两端；失败现场先于本例浏览器关闭。"""
    from testbench.huawei_slavesim_page import HuaweiSlaveSimPage
    from testbench.huawei_rtu_page import HuaweiRtuPage

    task_path = os.environ.get("TESTBENCH_TASK")
    if not task_path:
        raise RuntimeError("请从测试上位机执行华为机群用例，以读取任务环境和模拟器地址")
    task = json.loads(Path(task_path).read_text(encoding="utf-8"))
    environment = task["environment"]
    config = huawei_config_from_environment(environment)
    if environment.get("mode") != "fresh":
        raise RuntimeError("华为21台配置用例要求全新独立副本")
    url = validate_slavesim_url(environment.get("slavesim_url", ""), required=True)
    account = load_login_account(ROOT / "local" / "login-account.json")
    desktop = request.getfixturevalue("desktop")
    desktop.metadata["华为验收阶段"] = "前置条件"
    upper = HuaweiRtuPage(desktop)
    with HuaweiSlaveSimPage(desktop, url) as simulator:
        scenario = HuaweiFleetRun(desktop, simulator, upper, config, uuid.uuid4().hex[:8])
        setup_failed = False
        try:
            PlatformLoginPage(desktop).login(account, remember=False, auto=False)
            upper.open_device(config.manager_address)
            desktop.metadata["华为验收阶段"] = "六路模型、21台连接与点表配置及抄读"
            yield scenario
        except BaseException as error:
            setup_failed = True
            desktop.failure = desktop.failure or str(error)
            scenario.capture_both("前置阶段失败，保留两端现场")
            raise
        finally:
            if not setup_failed and any(report.failed for report in getattr(request.node, "_bench_reports", [])):
                scenario.capture_both("机群验收失败，关闭浏览器前现场")
            had_failure = setup_failed or sys.exc_info()[0] is not None or any(
                report.failed for report in getattr(request.node, "_bench_reports", []))
            try:
                scenario.write_report()
            except Exception as error:
                desktop.metadata["华为报告写入错误"] = str(error)
                logging.getLogger(__name__).exception("华为汇总报告写入失败，保留原始业务错误")
                if not had_failure:
                    raise


def test_huawei_six_buses_21_devices_match(huawei_fleet):
    """六路21台华为逆变器自动配置并逐台核对遥测、状态及调节反馈。"""
    huawei_fleet.run()
