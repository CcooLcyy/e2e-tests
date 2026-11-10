"""配置并核对本例专用的 slaveSim RTU 应答源。"""

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

from .slavesim_page import SlaveSimPage


class RtuQualitySimulator(SlaveSimPage):
    def __init__(self, desktop, url, name):
        super().__init__(desktop, url)
        self.names = {role: f"{name}-{role}" for role in ("reference", "target")}
        self.paths = {}
        self.baseline = []

    def _check_exclusive(self, buses):
        for bus in buses:
            if bus["name"] in self.paths:
                if bus["link"] != self.paths[bus["name"]]:
                    raise AssertionError("本例模拟总线的路径已变化，停止配置")
            elif bus["slaves"]:
                raise AssertionError("slaveSim 存在无关运行从站。保存配置会重建所有模型，未调整该模拟器")

    def _station(self, name, address, value):
        bus = self._config_bus(name)
        self._open(bus)
        stations = bus.find_elements(By.CSS_SELECTOR, ":scope > details.slave-editor")
        if stations:
            station = self._unique(stations, "本例模拟从站")
            self._open(station)
            station.find_element(By.XPATH, "./summary/button[normalize-space(.)='删除从站']").click()
        if address is None:
            return
        bus = self._config_bus(name)
        Select(bus.find_element(By.CSS_SELECTOR, "select[name='device-type']")).select_by_value("__custom_modbus__")
        bus.find_element(By.XPATH, ".//button[normalize-space(.)='添加设备']").click()
        station = self._unique(self._config_bus(name).find_elements(By.CSS_SELECTOR, ":scope > details.slave-editor"),
                               "本例模拟从站")
        self._open(station)
        register = self._unique(station.find_elements(By.CSS_SELECTOR, "details.register-editor"), "本例模拟寄存器")
        self._open(station.find_element(By.CSS_SELECTOR, "details.register-group"))
        self._open(register)
        self._replace(self._field(register, "协议地址"), address)
        self._replace(self._field(register, "原始值"), value)

    def _assert_mode(self, buses, role, address, value):
        bus = self._unique([bus for bus in buses if bus["name"] == self.names[role]], f"本例 {role} 总线")
        assert bus["link"] == self.paths[bus["name"]], f"模拟总线串口路径变化：{bus!r}"
        if address is None:
            assert not bus["slaves"], f"静默阶段目标站仍存在：{bus!r}"
            return
        slave = self._unique(bus["slaves"], f"本例 {role} 从站")
        reg = self._unique(slave["registers"], f"本例 {role} 寄存器")
        assert (slave["station_id"], slave["fields"]["未配置地址"], reg["function"], reg["address"],
                reg["type"], reg["source"], reg["byte_order"], int(reg["value"])) == (
                    1, "exception", "0x03", address, "UINT16", "const", "AB", value), bus

    def prepare(self, check_paths):
        self.baseline = self._read_configuration("品质场景准备前")
        self._check_exclusive(self.baseline)
        for name in self.names.values():
            if any(bus["name"] == name for bus in self.baseline):
                raise AssertionError(f"已有同名模拟总线 {name}，不接管")
            draft, link = self._add_default_bus()
            self._replace(self._field(self._config_bus(draft), "总线名称"), name)
            self.paths[name] = link
        check_paths(tuple(self.paths.values()))
        self._station(self.names["reference"], 0, 100)
        actual = self._save_configuration("新建品质验收参照及静默目标总线")
        self._assert_preserved(actual)
        self._assert_mode(actual, "reference", 0, 100)
        self._assert_mode(actual, "target", None, 0)
        return {role: self.paths[name] for role, name in self.names.items()}

    def _assert_preserved(self, actual):
        assert [bus for bus in actual if bus["name"] not in self.paths] == self.baseline, "无关模拟器配置发生变化"

    def set_target(self, address, value, label):
        actual = self._read_configuration(f"切换{label}前")
        self._check_exclusive(actual)
        self._station(self.names["target"], address, value)
        actual = self._save_configuration(label)
        self._assert_preserved(actual)
        self._assert_mode(actual, "target", address, value)
        self._assert_mode(actual, "reference", 0, 100)

    def wait_target_exchange(self, address, timeout):
        """从本页最新配置应用后的报文证明目标实际收到读请求及按场景应答。"""
        prefix = f"总线={self.names['target']} 站号=1 "
        self.driver.find_element(By.ID, "show-runtime").click()
        def exchange(_):
            rows = [row.text for row in self.driver.find_elements(By.CSS_SELECTOR, "#logs > .row")]
            applied = max((i for i, row in enumerate(rows) if "Web 配置已保存并应用" in row), default=-1)
            if applied < 0:
                return False
            requests = [row for row in rows[applied + 1:] if f"[接收] {prefix}01 03 00 00 00 01 " in row]
            if address is None:
                return next((row for row in requests if "站号 1 未配置，已丢弃" in row), False)
            if not requests:
                return False
            frame = "01 03 02 00 64 " if address == 0 else "01 83 02 "
            return next((row for row in rows[applied + 1:] if f"[发送] {prefix}{frame}" in row), False)

        with self._step("核对最新配置应用后的目标站实际 RTU 报文"):
            evidence = self._wait(exchange, "slaveSim 未显示目标站本阶段的请求或应答报文", timeout=timeout)
            self._snapshot({"类型": "目标站实际RTU报文", "目标地址配置": address, "日志": evidence})
            return evidence

    def cleanup(self):
        if not self.paths:
            return
        self._restoring = True
        try:
            before = self._read_configuration("清理本例品质总线前")
            self._check_exclusive(before)
            owned = [bus["name"] for bus in before if bus["name"] in self.paths]
            for name in owned:
                card = self._config_bus(name)
                card.find_element(By.XPATH, "./summary/button[normalize-space(.)='删除总线']").click()
            if owned:
                after = self._save_configuration("删除本例品质总线")
                assert after == [bus for bus in before if bus["name"] not in owned], "清理后模拟器配置不符"
        finally:
            self._restoring = False
