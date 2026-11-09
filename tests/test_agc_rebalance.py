"""从空设备配置三台逆变器，验证 RTU 通信异常和恢复时的 AGC 重分配。"""
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit
import uuid

import pytest
from selenium.webdriver.common.by import By

from testbench.agc_rebalance_iec104 import AgcRebalanceIec104Page, COMMAND_IOA, COMMON_ADDRESS
from testbench.agc_rebalance_page import AGC_COMMAND_TAG, AgcRebalancePage
from testbench.agc_rebalance_rtu import AgcRebalanceRtu
from testbench.huawei_slavesim_page import HuaweiSlaveSimPage
from testbench.login_page import PlatformLoginPage, load_login_account
from testbench.master104_commands import Master104CommandSender
from testbench.model import Environment
from testbench.modbus_environment import AGC_REBALANCE_CASE_ID
from testbench.modbus_rtu_page import ModbusRtuPage


pytestmark = [pytest.mark.case_group("AGC 通信重分配"), pytest.mark.opt_in]
ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)
TOTAL_KW = 300


def _assert_no_registered_connections(desktop):
    """数据总线目录覆盖已注册的协议与控制连接，模块页面再检查各自配置。"""
    page = ModbusRtuPage(desktop)
    page._menu("数据总线")
    tab = (By.XPATH, "//*[@aria-label='数据总线页面切换']//*[@role='tab'][normalize-space(.)='路由配置']")
    page._click(tab, "打开数据总线路由配置")
    refresh = (By.CSS_SELECTOR, '.data-bus-connection-card button[aria-label="刷新连接列表"]')
    catalog = (By.CSS_SELECTOR, '.data-bus-connection-list[aria-busy="false"]')
    with desktop.step("确认数据总线连接目录为空", expected=[]):
        page._until(lambda _: page._visible(catalog), "数据总线连接目录未就绪")
        page._click(refresh, "刷新下位机已注册连接")
        directory = page._until(lambda _: page._visible(catalog), "数据总线连接目录刷新未完成")
        errors = desktop.driver.find_elements(By.CSS_SELECTOR, ".ant-message-error")
        if any(element.is_displayed() and "刷新连接列表失败" in element.text for element in errors):
            raise AssertionError("数据总线连接列表刷新失败，无法确认空环境")
        names = [item.text for item in directory.find_elements(By.CSS_SELECTOR, ".data-bus-connection-item")]
        desktop.metadata["AGC 初始已注册连接"] = names
        if names or "暂无已注册连接" not in directory.text:
            raise AssertionError(f"下位机数据总线连接目录非空或状态不明确：{names!r}，停止准备并保留现场")


def _capture_failure(desktop, simulator, master):
    for source, session in (("DSP上位机", desktop), ("slaveSim", simulator), ("104主站", master)):
        try:
            session.capture("agc-rebalance-failure", description=f"{source}：AGC 通信重分配失败现场")
        except Exception as error:
            desktop.metadata.setdefault("AGC 取证错误", []).append(f"{source}：{error}")
            LOGGER.exception("%s AGC 通信重分配取证异常", source)


def test_agc_redistributes_when_rtu_member_loses_communication(request):
    """一次下发 300 kW，单台 RTU 通信异常后重新分配，恢复后自动恢复三台均分。"""
    task_path = os.environ.get("TESTBENCH_TASK")
    if not task_path:
        raise RuntimeError("请从测试上位机单独运行 AGC 通信重分配用例")
    task = json.loads(Path(task_path).read_text(encoding="utf-8"))
    environment = Environment.from_dict(task["environment"])
    environment.validate_for_cases(task["case_ids"])
    if task["case_ids"] != [AGC_REBALANCE_CASE_ID]:
        raise RuntimeError("AGC 通信重分配须单独运行")
    account = load_login_account(ROOT / "local" / "login-account.json")
    desktop = request.getfixturevalue("desktop")
    master = request.getfixturevalue("master104")
    run_id = uuid.uuid4().hex[:8]
    group_name = f"e2e-agc-rebalance-{run_id}"
    host = urlsplit("//" + environment.modbus_manager_address).hostname
    port = environment.agc_rebalance_iec104_port
    timeout = float(environment.modbus_acquisition_timeout)

    with HuaweiSlaveSimPage(desktop, environment.slavesim_url) as simulator:
        rtu = AgcRebalanceRtu(desktop, simulator, environment.to_dict(), run_id)
        agc = AgcRebalancePage(desktop)
        iec104 = AgcRebalanceIec104Page(desktop)
        observations = desktop.metadata["AGC 重分配观察"] = {}
        try:
            desktop.metadata["AGC 验收阶段"] = "专用空环境检查"
            PlatformLoginPage(desktop).login(account, remember=False, auto=False)
            rtu.open_device_and_assert_empty()
            _assert_no_registered_connections(desktop)
            agc.assert_empty()
            iec104.assert_empty()
            rtu.assert_simulator_empty()

            desktop.metadata["AGC 验收阶段"] = "RTU、AGC 与 IEC104 自动配置"
            members = rtu.configure()
            names = tuple(member["name"] for member in members)
            agc_conn_id = agc.create_group(group_name, names, members[0]["p_meas_tag"], members[0]["p_set_tag"])
            desktop.metadata["AGC 专用控制组"] = {"name": group_name, "conn_id": agc_conn_id, "members": members}
            iec104.configure(group_name, AGC_COMMAND_TAG, port)
            agc.start_group()
            observations["下发前"] = agc.wait_stage({}, total_kw=None, timeout=timeout)

            desktop.metadata["AGC 验收阶段"] = "104 遥调与三台在线"
            master.connect_and_interrogate(host, port, COMMON_ADDRESS)
            sender = Master104CommandSender(master, COMMON_ADDRESS, COMMAND_IOA)
            command = sender.send_target_kw(TOTAL_KW)
            observations["104 遥调"] = command
            online = dict.fromkeys(names, 100)
            initial = agc.wait_stage(online, total_kw=TOTAL_KW, after=observations["下发前"], timeout=timeout)
            observations["三台在线"] = initial
            observations["三台在线逆变器反馈"] = rtu.assert_dispatch((100, 100, 100))

            desktop.metadata["AGC 验收阶段"] = "单台 RTU 抄读失败"
            observations["RTU 抄读失败"] = rtu.set_fault(True)
            redistributed = agc.wait_stage(dict.fromkeys(names[:2], 150), total_kw=TOTAL_KW,
                                            offline_member=names[2], after=initial, timeout=timeout)
            observations["单台通信异常"] = redistributed
            observations["单台异常逆变器反馈"] = rtu.assert_dispatch((150, 150, 100))

            desktop.metadata["AGC 验收阶段"] = "通信恢复"
            observations["RTU 恢复"] = rtu.set_fault(False)
            observations["三台恢复"] = agc.wait_stage(online, total_kw=TOTAL_KW,
                                                     after=redistributed, timeout=timeout)
            observations["三台恢复逆变器反馈"] = rtu.assert_dispatch((100, 100, 100))
            master._assert_live_display()
            result = master.driver.find_element(By.ID, "setpoint-result")
            master.check("通信恢复后仍是原 104 遥调事务",
                         result.get_dom_attribute("data-operation-id"), command["operation_id"])
        except BaseException as error:
            desktop.failure = desktop.failure or str(error)
            _capture_failure(desktop, simulator, master)
            raise
