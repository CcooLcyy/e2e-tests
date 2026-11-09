"""三机 AGC 用例的专用 RTU 资源及下游调节取证。"""
from decimal import Decimal

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

from .huawei_fleet import READ_POINTS, HuaweiPoint
from .huawei_rtu_page import HuaweiRtuPage
from .modbus_environment import _shared
from .modbus_rtu_page import xpath_text


STATIONS = (1, 2, 3)
OFFLINE_STATION = 4
CAPACITY_KW = 200
MEASUREMENT = next(point for point in READ_POINTS if point.key == "active")
SETPOINT = HuaweiPoint("active_setpoint", "有功固定值限值", 40126, "UINT32", 2,
                      "0.001", function=16, unit="kW")
CONNECTION_LIST = ".protocol-connection-list-card"
CONNECTION_DETAIL = "//div[contains(@class,'modbus-connection-card')]"
LIST_REFRESH_ERROR = (By.XPATH, "//div[contains(@class,'modbus-page-alert')]"
                      "//*[normalize-space(.)='连接列表刷新失败']")
COMM_HEALTHY = "通信正常"
COMM_FAILED = "本轮无有效数据"


class _RebalanceRtuPage(HuaweiRtuPage):
    def _select_root(self, name):
        return self._field(name).find_element(
            By.XPATH, "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' ant-select ')][1]")

    def _select_option(self, name, label):
        with self.desktop.step(f"设置{name}", expected=label):
            root = self._select_root(name)
            legacy_selectors = root.find_elements(By.CSS_SELECTOR, ".ant-select-selector")
            (legacy_selectors[0] if legacy_selectors else root).click()
            option = (By.XPATH, "//div[contains(@class,'ant-select-item-option') and @title=" + xpath_text(label) + "]")
            self._until(lambda _: self._visible(option), f"未找到选项：{label}").click()

    def _assert_select(self, field, expected):
        selected = self._select_root(field).find_element(
            By.CSS_SELECTOR, ".ant-select-content-has-value, .ant-select-selection-item")
        self.desktop.check(f"保存后{field}一致", selected.get_attribute("title") or selected.text, expected)


class AgcRebalanceRtu:
    def __init__(self, desktop, simulator, environment, run_id):
        self.desktop = desktop
        self.simulator = simulator
        self.upper = _RebalanceRtuPage(desktop)
        self.config = _shared(environment)
        self.bus = f"e2e-agc-{run_id}"
        self.connections = ()

    def open_device_and_assert_empty(self):
        self.upper.open_device(self.config["manager_address"], required_modules=("ModbusRTU", "DataCenter", "AGC", "IEC104"))
        self._assert_empty_connections()

    def _assert_empty_connections(self):
        upper = self.upper
        refresh = (By.CSS_SELECTOR, f'{CONNECTION_LIST} button[aria-label="刷新连接列表"]')

        def ready(_):
            button = upper._visible(refresh)
            return button and button.is_enabled() and "ant-btn-loading" not in (button.get_attribute("class") or "")

        with self.desktop.step("确认专用下位机 RTU 连接列表为空", expected="刷新成功且没有连接"):
            upper._click(refresh, "读取现有 RTU 连接")
            upper._until(ready, "RTU连接列表刷新未完成")
            error = upper._visible(LIST_REFRESH_ERROR)
            if error:
                raise AssertionError(f"无法确认 RTU 连接列表为空：{error.text}")
            card = upper._visible((By.CSS_SELECTOR, CONNECTION_LIST))
            names = card.find_elements(By.CSS_SELECTOR, ".protocol-connection-item-main strong")
            if names or "暂无连接" not in card.text:
                raise AssertionError("RTU 连接列表非空或未确认空状态，停止用例并保留配置")

    def assert_simulator_empty(self):
        sim = self.simulator
        if sim._read_configuration("AGC 三机配置前"):
            raise AssertionError("专用 slaveSim 已有模拟总线，停止用例并保留配置")
        with sim._step("确认 slaveSim 实际运行环境为空"):
            sim.driver.find_element(By.ID, "show-runtime").click()
            tick = sim._tick()
            sim._wait(lambda _: sim._tick() > max(tick, 0) and not sim.driver.find_elements(
                By.CSS_SELECTOR, "#buses .bus-choice, #buses section.bus-workspace, #buses section.runtime-device"),
                "slaveSim 未反馈新的零总线、零设备状态")

    def _create_simulator(self):
        sim = self.simulator
        if sim._read_configuration("AGC 三机配置提交前"):
            raise AssertionError("slaveSim 已有总线，拒绝覆盖既有配置")
        with sim._step("新增单路总线与三台 200 kW 华为逆变器"):
            default_name, path = sim._add_default_bus()
            sim._replace(sim._field(sim._config_bus(default_name), "总线名称"), self.bus)
            for station in STATIONS:
                card = sim._config_bus(self.bus)
                sim._open(card)
                selector = Select(card.find_element(By.CSS_SELECTOR, "select[name='device-type']"))
                choices = sim._wait(lambda _: [option for option in selector.options
                                               if "华为" in option.text and "行为" in option.text],
                                    "slaveSim 缺少华为行为模板")
                if len(choices) != 1:
                    raise AssertionError("slaveSim 华为行为模板不唯一")
                selector.select_by_value(choices[0].get_attribute("value"))
                card.find_element(By.XPATH, ".//button[normalize-space(.)='添加设备']").click()
                sim._wait(lambda _: len(sim._config_bus(self.bus).find_elements(
                    By.CSS_SELECTOR, ":scope > details.slave-editor")) == station, f"未创建站号 {station}")
                device = sim._config_station(self.bus, station)
                sim._open(device)
                model = device.find_element(By.CSS_SELECTOR, ".model-editor")
                sim._replace(sim._field(model, "逆变器额定容量（kW）"), CAPACITY_KW)
                sim._replace(sim._field(model, "太阳可用直流功率（kW）"), CAPACITY_KW)
                sim._replace(sim._field(model, "转换效率（0～1，不含0）"), 1)
                disturbance = model.find_element(By.CSS_SELECTOR, "input[name='disturbance_enabled']")
                if disturbance.is_selected():
                    disturbance.click()
            self._assert_simulator_config(sim._configuration_snapshot("AGC 三机配置草稿"), path)
        saved = sim._save_configuration("AGC 单路三台 200 kW 逆变器")
        self._assert_simulator_config(saved, path)
        sim.driver.find_element(By.ID, "show-runtime").click()
        for station in STATIONS:
            result = sim.observe_device(self.bus, station, [30073, 30075, 30077])
            if result["link_path"] != path or result["registers"][30073]["value"] != CAPACITY_KW * 1000:
                raise AssertionError(f"slaveSim 站号 {station} 的运行串口路径或额定容量与已保存配置不一致")
            if any(result["registers"][address]["value"] < CAPACITY_KW * 1000 for address in (30075, 30077)):
                raise AssertionError(f"slaveSim 站号 {station} 的运行有功或视在容量不足")
        return path

    def _assert_simulator_config(self, buses, path):
        if len(buses) != 1 or buses[0]["name"] != self.bus or buses[0]["link"] != path:
            raise AssertionError("slaveSim 未确认仅存在本例总线及其实际串口路径")
        slaves = buses[0]["slaves"]
        if sorted(slave["station_id"] for slave in slaves) != list(STATIONS):
            raise AssertionError("slaveSim 未确认恰有三个指定站号，且故障站号未使用")
        for slave in slaves:
            fields = slave["model_fields"]
            if (slave["model"] != "huawei_v3"
                    or Decimal(fields.get("逆变器额定容量（kW）", "0")) != CAPACITY_KW
                    or Decimal(fields.get("太阳可用直流功率（kW）", "0")) != CAPACITY_KW
                    or Decimal(fields.get("转换效率（0～1，不含0）", "0")) != 1
                    or fields.get("轻微扰动") is not False):
                raise AssertionError(f"slaveSim 站号 {slave['station_id']} 的模型或工况配置与固定预期不一致")

    def configure(self):
        upper = self.upper
        upper._rtu()
        self._assert_empty_connections()
        path = self._create_simulator()
        connections = []
        for station in STATIONS:
            name = f"{self.bus}-rtu-{station}"
            with self.desktop.step(f"配置 RTU 站号 {station} 的有功采集及固定限值写点"):
                identity = upper.create_connection(name, path, self.config["baud_rate"], 8, 1, "N",
                                                   station, self.config["poll_interval_ms"])
                p_meas_tag, p_set_tag = "有功功率", "有功目标"
                upper.add_huawei_point(MEASUREMENT, p_meas_tag)
                upper.add_huawei_point(SETPOINT, p_set_tag)
                upper.assert_saved_configuration()
                upper.start()
                connections.append(dict(identity, station_id=station, p_meas_tag=p_meas_tag, p_set_tag=p_set_tag))
                self.desktop.metadata["本例RTU连接"][-1]["清理状态"] = "保留配置供复查"
        self.connections = tuple(connections)
        self._wait_communication(offline=False)
        return self.connections

    def _wait_communication(self, *, offline):
        upper = self.upper
        expected = {row["name"]: COMM_FAILED if offline and row["station_id"] == STATIONS[-1] else COMM_HEALTHY
                    for row in self.connections}

        def observed(_):
            if upper._visible(LIST_REFRESH_ERROR):
                return False
            actual = {}
            for name in expected:
                row = upper._visible(upper._connection_locator(name))
                labels = row.find_elements(By.CSS_SELECTOR, "span[aria-label]") if row else []
                actual[name] = labels[0].get_attribute("aria-label") if labels else "未显示"
            self.desktop.metadata["AGC 三机 RTU 通信状态"] = actual
            return actual if actual == expected else False

        with self.desktop.step("核对三条 RTU 连接的轮询结果", expected=str(expected)):
            return upper._until(observed, f"未观察到预期的 RTU 通信状态 {expected}",
                                self.config["acquisition_timeout"])

    def set_fault(self, offline):
        upper = self.upper
        original = self.connections[-1]
        previous = STATIONS[-1] if offline else OFFLINE_STATION
        target = OFFLINE_STATION if offline else STATIONS[-1]
        upper._rtu()
        upper.select(original["name"])
        with self.desktop.step(f"将第三台 RTU 连接的从站地址由 {previous} 改为 {target}"):
            upper._click(upper._button("编辑配置", CONNECTION_DETAIL), "打开第三台 RTU 连接配置")
            upper._assert_input("device_id", previous)
            upper._fill("device_id", target, "从站地址")
            upper._submit("保存修改")
            title = (By.XPATH, f"{CONNECTION_DETAIL}//span[normalize-space(.)='从站 {target}']")
            upper._until(lambda _: upper._visible(title), "修改后的从站地址未出现在连接详情")
            upper._assert_identity()
            upper.owned[original["name"]]["fields"]["device_id"] = target
        # 编辑连接会短暂停启轮询，必须等到真实抄读结果，不能用停止状态代替失联。
        return self._wait_communication(offline=offline)

    def assert_dispatch(self, expected_kw):
        expected_watts = tuple(Decimal(str(value)) * 1000 for value in expected_kw)
        if len(expected_watts) != len(STATIONS):
            raise ValueError("三机调节验收须给出三个目标值")

        def matches(_):
            actual = {}
            accepted = True
            for station, target in zip(STATIONS, expected_watts):
                observed = self.simulator.observe_device(self.bus, station, [32080, 35300, 35301, 35303, 40126])
                actual[station] = observed
                values = {address: register["value"] for address, register in observed["registers"].items()}
                accepted = accepted and (
                    values[35300] == 1 and values[35303] == SETPOINT.address
                    and values[35301] == values[40126] == target and abs(values[32080] - target) <= 1
                )
            self.desktop.metadata["AGC 三机下游调节观察"] = actual
            return actual if accepted else False

        with self.desktop.step("核对三台逆变器的固定限值命令和实际有功", expected=str(expected_kw)):
            return self.upper._until(matches, f"slaveSim 未观察到指定的有功目标及输出 {expected_kw}",
                                     self.config["acquisition_timeout"])
