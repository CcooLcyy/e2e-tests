"""通过 slaveSim 可见界面创建华为行为机群并读取协议原始反馈。"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import logging

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select

from .slavesim_page import SlaveSimPage, _literal


LOG = logging.getLogger(__name__)
SCENARIO_LABELS = {"dc_available_kw": "太阳可用直流功率（kW）", "grid_line_voltage_v": "电网线电压（V）",
                   "grid_frequency_hz": "电网频率（Hz）", "ambient_temperature_c": "环境温度（℃）",
                   "efficiency": "转换效率（0～1，不含0）"}


def decode_register(kind, raw, displayed):
    """支持模板 HL/AB 编码，独立交叉检查显示值和真实原始字节。"""
    sizes = {"UINT16": 2, "INT16": 2, "UINT32": 4, "INT32": 4}
    try:
        encoded = bytes.fromhex(raw)
        number = Decimal(str(displayed).strip().split()[0])
        if kind not in sizes or len(encoded) != sizes[kind] or not number.is_finite():
            raise ValueError()
        actual = int.from_bytes(encoded, "big", signed=kind.startswith("INT"))
        if number != actual:
            raise ValueError()
        return actual
    except (ValueError, InvalidOperation, IndexError):
        raise AssertionError(f"华为寄存器显示值与 {kind} HL/AB 原始字节不一致：{displayed} / {raw}") from None


def validate_fleet(buses, *, require_paths=True):
    if not isinstance(buses, list) or len(buses) != 6:
        raise ValueError("华为机群必须恰好六路 RS485")
    names, paths = set(), set()
    for index, bus in enumerate(buses, 1):
        name, path = bus.get("name"), bus.get("serial_device")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("华为总线名称必须非空且唯一")
        if require_paths and (not isinstance(path, str) or not path.startswith("/") or path in paths):
            raise ValueError("华为总线串口路径必须为唯一绝对路径")
        names.add(name); paths.add(path)
        devices = bus.get("devices", [])
        if (any(type(device.get("station_id")) is not int for device in devices)
                or [device.get("station_id") for device in devices] != list(range(1, index + 1))):
            raise ValueError(f"第 {index} 路必须包含站号1至{index}，不得缺站或重复")


class HuaweiSlaveSimPage(SlaveSimPage):
    """独立浏览器，保留本次新建六路模型以便现场检查。"""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.close()
        except Exception as error:
            self.desktop.cleanup_failure = f"华为模拟器浏览器关闭失败：{error}"
            self.desktop.failure = self.desktop.failure or self.desktop.cleanup_failure
            self.desktop.metadata["华为模拟器清理错误"] = str(error)
            if exc_value is None:
                raise

    @staticmethod
    def _replace(element, value):
        element.click()
        element.send_keys(Keys.CONTROL, "a", Keys.NULL, Keys.BACKSPACE)
        element.send_keys(str(value), Keys.TAB)

    def _config_bus(self, name):
        return self._unique([card for card in self.driver.find_elements(By.CSS_SELECTOR, "#config-buses > details.bus-editor")
                             if self._field(card, "总线名称").get_attribute("value") == name], "目标配置总线")

    def _config_station(self, name, station):
        return self._unique([node for node in self._config_bus(name).find_elements(By.CSS_SELECTOR, ":scope > details.slave-editor")
                             if self._field(node, "站号（1～247）").get_attribute("value") == str(station)], "目标配置从站")

    def _clear_existing_devices(self, before):
        """通过页面删除已有从站，独立保存零台配置，保留总线定义。"""
        for bus in before:
            for remaining in range(len(bus["slaves"]), 0, -1):
                with self._step(f"清空 {bus['name']} 模拟从站，删除前剩余{remaining}台"):
                    card = self._config_bus(bus["name"])
                    self._open(card)
                    stations = card.find_elements(By.CSS_SELECTOR, ":scope > details.slave-editor")
                    if len(stations) != remaining:
                        raise AssertionError("删除前模拟从站数量与实际读取配置不一致，保留现场")
                    stations[0].find_element(By.XPATH, "./summary/button[normalize-space(.)='删除从站']").click()
                    self._wait(lambda _: len(self._config_bus(bus["name"]).find_elements(
                        By.CSS_SELECTOR, ":scope > details.slave-editor")) == remaining - 1,
                        "删除模拟从站后页面数量未减少")
        actual = self._save_configuration("清空模拟从站并保存零台状态", runtime_impact=
                                          "原有从站及点表已删除，空总线保留，运行模型已清空，不自动恢复")
        expected = [{**bus, "slaves": []} for bus in before]
        if actual != expected:
            raise AssertionError("清空后读回配置与保留总线的零台配置不一致，禁止创建新机群")
        self.desktop.metadata["华为自动清空结果"] = {
            "删除从站数": sum(len(bus["slaves"]) for bus in before), "保留总线数": len(actual), "保存后从站数": 0}
        self._snapshot({"类型": "华为自动清空结果", **self.desktop.metadata["华为自动清空结果"]})
        LOG.info("已通过页面清空模拟从站并保存零台状态，原总线定义保留")
        return actual

    def _verify_zero_runtime(self):
        """确认保存后的新运行页面确实没有设备，不能只检查草稿。"""
        with self._step("确认模拟器实际运行设备为0台", expected="新的运行反馈中不存在模拟从站"):
            self.driver.find_element(By.ID, "show-runtime").click()
            before = self._tick()
            self._wait(lambda _: self._tick() > max(before, 0) and not self.driver.find_elements(
                By.CSS_SELECTOR, "#buses section.runtime-device"), "模拟器未反馈实际零台状态，禁止创建新机群")
            result = {"类型": "华为创建前实际零台确认", "运行设备数": 0, "刷新序号": self._tick()}
            self._snapshot(result)
            self.desktop.metadata["华为创建前零台确认"] = result
            LOG.info("已确认模拟器新的运行反馈为零台，允许从头创建21台")

    def prepare_fleet(self, environment, run_id, *, check_paths=None):
        """先确认零逆变器，再使用页面默认路径从头新增完整机群。"""
        from .huawei_fleet import HuaweiFleetConfig, build_fleet
        from .modbus_environment import huawei_config_from_environment
        from .slavesim_runtime import assert_existing_preserved
        with self._step("读取初始模拟逆变器并清空至0台", expected="已有从站通过页面删除并保存，确认实际零台后再创建") as entry:
            before = self._read_configuration("华为机群零逆变器起点")
            devices = [{"总线": bus["name"], "站号": slave["station_id"], "模型": slave["model"]}
                       for bus in before for slave in bus["slaves"]]
            modeled = sum(bool(device["模型"]) for device in devices)
            initial = {"已有从站数": len(devices), "已识别逆变器数": modeled,
                       "未确认类型从站数": len(devices) - modeled, "设备": devices}
            self.desktop.metadata["华为初始逆变器检查"] = initial
            self._snapshot({"类型": "华为初始逆变器检查", **initial})
            entry["实际"] = json.dumps(initial, ensure_ascii=False)
            LOG.info("华为机群零起点检查：已有从站=%s，模型设备=%s，未确认类型=%s",
                     len(devices), modeled, len(devices) - modeled)
            # 无模型的静态厂商点表也可能是逆变器，清空整个专用模拟器的从站。
            if devices:
                before = self._clear_existing_devices(before)
            if any(bus["slaves"] for bus in before):
                raise AssertionError("清空后仍存在模拟从站，禁止开始创建21台")
            self._verify_zero_runtime()
        # 此时只生成固定设备工况，六条路径在页面新增后读取，不参与DSP连接。
        buses = build_fleet(HuaweiFleetConfig(environment["modbus_manager_address"], ("",) * 6), run_id)
        self.create_fleet(buses, discover_paths=True, check_paths=check_paths)
        actual = self._read_configuration("华为从零新增机群保存后")
        assert_existing_preserved(before, actual)
        actual_count = sum(len(bus["slaves"]) for bus in actual)
        self.desktop.metadata["华为创建后设备数"] = actual_count
        if actual_count != 21:
            raise AssertionError(f"华为从零创建后应恰好有21台模拟逆变器，实际有{actual_count}个从站；保留现场")
        self.desktop.metadata["华为资源策略"] = "先通过页面清空全部模拟从站并确认实际零台，再从头创建六路21台，不复用已有设备"
        config = huawei_config_from_environment(environment, {"serial_devices": [bus["serial_device"] for bus in buses]})
        validate_fleet(buses)
        self._prepared_fleet = buses
        self.desktop.metadata["华为运行时拓扑"] = buses
        self.desktop.metadata["RTU串口参数来源"] = "slaveSim采用PTY，无UART参数；DSP使用9600/8N1，路径是否可访问由真实连接及采集结果判定"
        self.driver.find_element(By.ID, "show-runtime").click()
        self._verify_fleet_runtime(buses)
        return config, buses

    def _verify_fleet_runtime(self, buses):
        for bus in buses:
            for device in bus["devices"]:
                result = self.observe_device(bus["name"], device["station_id"], [32089])
                if result["link_path"] != bus["serial_device"]:
                    raise AssertionError("华为总线运行路径与实际配置不一致，未推测宿主机路径")

    def create_fleet(self, buses, *, discover_paths=False, check_paths=None):
        if getattr(self, "_prepared_fleet", None) == buses:
            self._verify_fleet_runtime(buses)
            return
        validate_fleet(buses, require_paths=not discover_paths)
        with self._step("读取专用模拟器配置并检查六路资源未占用"):
            self.driver.find_element(By.ID, "show-config").click()
            self._wait(lambda _: self.driver.find_element(By.ID, "config-status").text == "已读取当前配置", "未读取模拟器当前配置")
            cards = self.driver.find_elements(By.CSS_SELECTOR, "#config-buses > details.bus-editor")
            existing_names = {self._field(node, "总线名称").get_attribute("value") for node in cards}
            existing_paths = {self._field(node, "串口软链接路径").get_attribute("value") for node in cards}
            if any(bus["name"] in existing_names or (not discover_paths and bus["serial_device"] in existing_paths) for bus in buses):
                raise AssertionError("六路华为测试名称或路径已占用，拒绝覆盖既有配置")
        for bus in buses:
            with self._step(f"新增华为总线 {bus['name']}，共{len(bus['devices'])}台"):
                before = len(self.driver.find_elements(By.CSS_SELECTOR, "#config-buses > details.bus-editor"))
                self.driver.find_element(By.ID, "add-bus").click()
                self._wait(lambda _: len(self.driver.find_elements(By.CSS_SELECTOR, "#config-buses > details.bus-editor")) == before + 1,
                           "新增总线未显示")
                card = self.driver.find_elements(By.CSS_SELECTOR, "#config-buses > details.bus-editor")[-1]
                self._replace(self._field(card, "总线名称"), bus["name"])
                if discover_paths:
                    bus["serial_device"] = self._field(card, "串口软链接路径").get_attribute("value")
                else:
                    self._replace(self._field(card, "串口软链接路径"), bus["serial_device"])
            for device in bus["devices"]:
                station = device["station_id"]
                with self._step(f"新增 {bus['name']} / 站号 {station} 华为V3行为模型"):
                    card = self._config_bus(bus["name"])
                    self._open(card)
                    select = card.find_element(By.CSS_SELECTOR, "select[name='device-type']")
                    def option_ready(_):
                        return [item for item in Select(select).options if "华为" in item.text and "行为" in item.text]
                    options = self._wait(option_ready, "没有华为行为模板选项")
                    if len(options) != 1:
                        raise AssertionError("华为行为模板选项不唯一")
                    Select(select).select_by_value(options[0].get_attribute("value"))
                    card.find_element(By.XPATH, ".//button[normalize-space(.)='添加设备']").click()
                    self._wait(lambda _: len(self._config_bus(bus["name"]).find_elements(By.CSS_SELECTOR, ":scope > details.slave-editor")) == station,
                               "华为模板设备未加入草稿")
                    node = self._config_station(bus["name"], station)
                    self._open(node)
                    if "华为逆变器" not in node.find_element(By.CSS_SELECTOR, ":scope > summary").text:
                        raise AssertionError("新增设备不是华为行为模型")
                    model = node.find_element(By.CSS_SELECTOR, ".model-editor")
                    for key, label in SCENARIO_LABELS.items():
                        if key in device:
                            self._replace(self._field(model, label), device[key])
                    disturbance = model.find_element(By.CSS_SELECTOR, "input[name='disturbance_enabled']")
                    if disturbance.is_selected():
                        disturbance.click()
                    if disturbance.is_selected():
                        raise AssertionError("未关闭华为模型轻微扰动")
            self.desktop.metadata.setdefault("华为创建意图", []).append(bus)
        with self._step("保存并应用六路21台华为模型配置"):
            validate_fleet(buses)
            if check_paths is not None:
                check_paths(tuple(bus["serial_device"] for bus in buses))
            draft = self._configuration_snapshot("华为机群保存前草稿")
            from .slavesim_runtime import validate_fleet_capacity
            capacity = validate_fleet_capacity(draft, buses)
            self._snapshot({"类型": "华为新增机群固定工况能力校验", "设备": capacity})
            self.driver.find_element(By.ID, "save-config").click()
            def saved(_):
                node = self.driver.find_element(By.ID, "config-status")
                if "error" in (node.get_attribute("class") or ""):
                    raise AssertionError(f"华为机群配置保存失败：{node.text}")
                return node.text.startswith("配置已保存并应用")
            self._wait(saved, "华为机群配置未确认保存")
            self.desktop.metadata["华为已创建六路"] = buses
            self.desktop.metadata.setdefault("slaveSim配置调整", []).append({
                "内容": "新增六路21台华为模型，使用页面实际默认路径", "运行影响": "保存整份配置会重建所有模型并重置临时手动值及指令；原有配置定义保留"})
            LOG.info("已通过页面保存六路21台华为行为模型，本次配置保留供用户检查")
            self.driver.find_element(By.ID, "show-runtime").click()
        for bus in buses:
            for device in bus["devices"]:
                result = self.observe_device(bus["name"], device["station_id"], [32089])
                if result["link_path"] != bus["serial_device"]:
                    raise AssertionError("新建华为总线的运行串口路径与配置不一致")

    def _device(self, bus, station):
        buttons = self.driver.find_elements(By.XPATH, "//div[@id='buses']//button[contains(@class,'bus-choice')][strong[normalize-space(.)=" + _literal(bus) + "]]")
        button = self._unique(buttons, "华为运行总线")
        if button.get_attribute("aria-pressed") != "true":
            button.click()
        key = button.get_attribute("data-bus-key")
        workspace = self.driver.find_element(By.XPATH, "//section[contains(@class,'bus-workspace')][@data-bus-key=" + _literal(key) + "]")
        if "连接中断" in workspace.find_element(By.CSS_SELECTOR, ".bus-summary").text or self._tick() < 0:
            raise AssertionError("模拟器连接中断，拒绝读取缓存反馈")
        device = workspace.find_element(By.CSS_SELECTOR, f"section.runtime-device[data-device-id='{station}']")
        if not device.find_elements(By.CSS_SELECTOR, "details.huawei-card"):
            raise AssertionError("目标运行设备没有华为行为模型")
        return workspace, device

    def observe_device(self, bus, station_id, addresses):
        with self._step(f"读取 {bus} / 站号 {station_id} 华为实际寄存器反馈") as entry:
            before = self._tick()
            self._wait(lambda _: self._tick() > max(before, 0), "华为模拟器没有新的状态刷新")
            workspace, device = self._device(bus, station_id)
            group = device.find_element(By.CSS_SELECTOR, "details.runtime-registers")
            self._open(group)
            # 一次同步读取真实 DOM，避免18个寄存器逐个驱动调用跨过页面刷新。
            visible = self.driver.execute_script("""
                return {rows: Array.from(arguments[0].querySelectorAll('tbody tr'), row =>
                    Array.from(row.querySelectorAll('td'), cell => cell.innerText)),
                    status: arguments[1].querySelector('.huawei-status').innerText,
                    paths: Array.from(arguments[2].querySelectorAll('.bus-path-value'), node => node.innerText)};
            """, group, device, workspace)
            registers = {}
            for address in addresses:
                cells = self._unique([row for row in visible["rows"] if row[0].strip() == "0x03"
                                      and row[1].strip() == str(address)], f"华为寄存器 {address}")
                kind, raw, displayed = cells[2].strip(), cells[5].strip(), cells[4].strip()
                registers[address] = {"value": decode_register(kind, raw, displayed), "raw": raw, "type": kind,
                                      "tag": cells[3].strip(), "address": address}
            self.driver.execute_script("arguments[0].scrollIntoView({block:'start'});", device)
            result = {"bus": bus, "station_id": station_id, "registers": registers,
                      "link_path": visible["paths"][1].strip(),
                      "status": visible["status"].strip(),
                      "observed_at": datetime.now().astimezone().isoformat(timespec="milliseconds"), "refresh_tick": self._tick()}
            self._snapshot(result)
            entry["实际"] = json.dumps(result, ensure_ascii=False)
            LOG.info("已读取华为实际寄存器：总线=%s，站号=%s，点数=%s", bus, station_id, len(registers))
            return result

    def _panel(self, bus, station):
        _, device = self._device(bus, station)
        panel = device.find_element(By.CSS_SELECTOR, "details.huawei-card")
        self._open(panel)
        return panel

    def _wait_applied(self, panel, message, before):
        def applied(_):
            text = message.text
            if "失败" in text:
                raise AssertionError(f"华为模型页面操作失败：{text}")
            return text.startswith("已应用") and self._tick() > max(before, 0)
        self._wait(applied, "华为页面操作没有确认应用及后续刷新")

    def apply_scenario(self, bus, station_id, **scenario):
        if set(scenario) - (set(SCENARIO_LABELS) | {"disturbance_enabled"}):
            raise ValueError("华为场景含未知参数")
        with self._step(f"应用 {bus} / 站号 {station_id} 华为场景", expected=scenario) as entry:
            panel = self._panel(bus, station_id)
            form = panel.find_element(By.CSS_SELECTOR, "form[data-form='scenario']")
            def observation():
                return {key: (form.find_element(By.CSS_SELECTOR, f"input[name='{key}']").is_selected()
                              if key == "disturbance_enabled" else
                              form.find_element(By.CSS_SELECTOR, f"input[name='{key}']").get_attribute("value"))
                        for key in scenario}
            self._snapshot({"类型": "华为场景调整前", "总线": bus, "站号": station_id, "场景": observation()})
            for key, value in scenario.items():
                if key == "disturbance_enabled":
                    if type(value) is not bool:
                        raise ValueError("轻微扰动开关必须为布尔值")
                    control = form.find_element(By.CSS_SELECTOR, "input[name='disturbance_enabled']")
                    if control.is_selected() != value:
                        control.click()
                else:
                    self._replace(form.find_element(By.CSS_SELECTOR, f"input[name='{key}']"), value)
            before = self._tick()
            form.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
            self._wait_applied(panel, form.find_element(By.CSS_SELECTOR, ".huawei-message"), before)
            applied_tick = self._tick()
            self._wait(lambda _: self._tick() > applied_tick, "场景应用后未取得新的实际状态刷新")
            actual = observation()
            self._snapshot({"类型": "华为场景调整后实际反馈", "总线": bus, "站号": station_id, "场景": actual})
            for key, expected in scenario.items():
                if ((actual[key] != expected) if key == "disturbance_enabled" else
                        Decimal(str(actual[key])) != Decimal(str(expected))):
                    raise AssertionError(f"华为场景实际反馈不符：{key}预期{expected}，实际{actual[key]}")
            entry["实际"] = json.dumps(actual, ensure_ascii=False)

    def apply_control(self, bus, station_id, kind, value):
        if kind not in {"percent", "fixed", "pf", "ratio", "active_gradient", "reactive_gradient"}:
            raise ValueError("不支持的华为模型控制类型；不发送未实现的启停命令")
        with self._step(f"设置 {bus} / 站号 {station_id} 华为{kind}", expected=value) as entry:
            panel = self._panel(bus, station_id)
            control = panel.find_element(By.CSS_SELECTOR, f"input[name='{kind}']")
            self._replace(control, value)
            row = control.find_element(By.XPATH, "ancestor::div[contains(@class,'huawei-command')][1]")
            form = control.find_element(By.XPATH, "ancestor::form[1]")
            before = self._tick()
            row.find_element(By.TAG_NAME, "button").click()
            self._wait_applied(panel, form.find_element(By.CSS_SELECTOR, ".huawei-message"), before)
            entry["实际"] = "页面确认命令已应用，实际行为由后续双端数据判定"
