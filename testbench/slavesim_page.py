"""slaveSim 独立浏览器页面操作，仅使用可见配置与运行反馈判定业务。"""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select

from .model import validate_slavesim_url


LOG = logging.getLogger(__name__)


def _literal(value):
    """生成 XPath 字面量，允许总线名包含引号。"""
    value = str(value)
    if "'" not in value:
        return "'" + value + "'"
    if '"' not in value:
        return '"' + value + '"'
    return "concat(" + ', "\'", '.join("'" + part + "'" for part in value.split("'")) + ")"


class SlaveSimPage:
    """每例创建独立 Edge；不接管用户浏览器，不保存整份模拟器配置。"""

    def __init__(self, desktop, url):
        self.desktop = desktop
        self.url = validate_slavesim_url(url, required=True)
        self.driver = None
        self._originals = {}
        self._verified = set()
        self._restoring = False
        self._snapshots = 0

    def __enter__(self):
        try:
            with self._step("打开独立管理页面"):
                profile = self.desktop.artifacts / "slavesim-edge-profile"
                profile.mkdir(parents=True, exist_ok=True)
                options = webdriver.EdgeOptions()
                options.add_argument(f"--user-data-dir={profile.resolve()}")
                options.add_argument("--no-first-run")
                options.add_argument("--window-size=1600,1000")
                service = Service(executable_path=str(self.desktop.driver_path),
                                  service_args=["--log-level=OFF"],
                                  log_output=str(self.desktop.artifacts / "slavesim-webdriver.log"))
                self.driver = webdriver.Edge(service=service, options=options)
                self.driver.set_page_load_timeout(self.desktop.timeout)
                self.driver.set_script_timeout(self.desktop.timeout)
                self.driver.get(self.url)
                self._wait(lambda _: self.driver.find_element(By.ID, "show-runtime").is_displayed(),
                           "slaveSim 页面未显示运行入口")
                # 只观察页面已有的刷新事件；业务数值仍从可见表格读取。
                self.driver.execute_script("""
                    window.__testbenchSim = {tick: 0, failed: false};
                    window.addEventListener('sim-state', () => {
                        window.__testbenchSim.tick++; window.__testbenchSim.failed = false;
                    });
                    window.addEventListener('sim-state-error', () => {window.__testbenchSim.failed = true;});
                """)
                self._wait(lambda _: self._tick() > 0, "slaveSim 未出现新的运行状态刷新")
                self.desktop.metadata["slaveSim管理页"] = self.url
                self.desktop.metadata["slaveSim浏览器版本"] = self.driver.capabilities.get("browserVersion")
            return self
        except BaseException:
            try:
                self.close()
            except Exception as error:
                self.desktop.metadata["slaveSim浏览器清理错误"] = str(error)
                LOG.exception("slaveSim 初始化失败后浏览器清理异常，保留原始错误")
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        cleanup = None
        try:
            self.restore_all()
        except Exception as error:
            cleanup = error
            self.desktop.cleanup_failure = f"slaveSim 原值恢复失败：{error}"
            self.desktop.failure = self.desktop.failure or self.desktop.cleanup_failure
            self.desktop.metadata["slaveSim清理错误"] = str(error)
            LOG.exception("slaveSim 原值恢复失败，保留原始业务异常")
        finally:
            try:
                self.close()
            except Exception as error:
                cleanup = cleanup or error
                self.desktop.cleanup_failure = f"slaveSim 浏览器清理失败：{error}"
                self.desktop.failure = self.desktop.failure or self.desktop.cleanup_failure
                self.desktop.metadata["slaveSim浏览器清理错误"] = str(error)
        if cleanup is not None and exc_value is None:
            raise RuntimeError(self.desktop.cleanup_failure) from cleanup

    def close(self):
        """仅退出本对象创建的浏览器会话。"""
        driver, self.driver = self.driver, None
        if driver is not None:
            driver.quit()

    def capture(self, name, *, expected=None, actual=None, description=None):
        """失败后在清理之前留存当前页面，不切换总线或重新打开窗口。"""
        entries = self.desktop._active_steps or self.desktop.steps
        entry = dict(entries[-1] if entries else {"序号": 0})
        entry["步骤"] = f"[slaveSim] {description or '保留现场 ' + Path(name).name}"
        if expected is not None:
            entry["预期"] = expected
        if actual is not None:
            entry["实际"] = actual
        failed = self.desktop.failure or entry.get("状态") == "失败" or entry.get("错误")
        self._capture(entry, "failed" if failed else "unknown", required=True)

    def _wait(self, predicate, message):
        def condition(driver):
            if not self._restoring:
                self.desktop._check_stop()
            try:
                return predicate(driver)
            except StaleElementReferenceException:
                return False
        return WebDriverWait(self.driver, self.desktop.timeout, poll_frequency=0.2).until(condition, message)

    def _tick(self):
        state = self.driver.execute_script("return window.__testbenchSim")
        if not state or state["failed"]:
            return -1
        return state["tick"]

    @contextmanager
    def _step(self, name, expected=None):
        with self.desktop.step(f"[slaveSim] {name}", expected=expected, cancellable=not self._restoring):
            entry = self.desktop._active_steps[-1]
            entry["来源"] = "slaveSim"
            LOG.info("slaveSim 页面步骤：%s", name)
            try:
                yield entry
            except BaseException as error:
                entry["错误"] = str(error)
                self._capture(entry, "failed", required=False)
                raise
            else:
                self._capture(entry, "passed", required=True)

    def _capture(self, entry, status, *, required):
        stem = f"slavesim-step-{entry['序号']:04d}-{status}"
        target = self.desktop.artifacts / f"{stem}.png"
        serial = 2
        known = {shot["name"] for shot in self.desktop.screenshots}
        while target.exists() or target.name in known:
            target = self.desktop.artifacts / f"{stem}-{serial}.png"
            serial += 1
        shot = {"name": target.name, "description": entry["步骤"], "source": "slavesim",
                "step_id": entry["序号"], "expected": entry.get("预期", "查看模拟器实际页面"),
                "actual": entry.get("实际", entry.get("错误", "已执行页面操作")),
                "status": status, "capture_status": "missing", "error": "",
                "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "automatic": True, "annotated_name": "", "annotation_status": "disabled",
                "annotation_error": "", "annotations": []}
        self.desktop.screenshots.append(shot)
        try:
            if self.driver is None:
                raise RuntimeError("模拟器浏览器未建立，无法保存页面现场")
            if not self.driver.save_screenshot(str(target)) or not target.is_file() or target.stat().st_size == 0:
                raise RuntimeError("模拟器浏览器未保存有效截图")
            shot["capture_status"] = "saved"
        except Exception as error:
            shot["error"] = f"slaveSim 截图缺失：{error}"
            LOG.warning("%s", shot["error"])
            if required:
                raise RuntimeError(shot["error"]) from error

    @staticmethod
    def _open(details):
        if details.get_attribute("open") is None:
            details.find_element(By.XPATH, "./summary").click()

    @staticmethod
    def _field(root, label):
        return root.find_element(By.XPATH, ".//label[span[normalize-space(.)=" + _literal(label) + "]]/*[self::input or self::select]")

    @staticmethod
    def _unique(items, description):
        if len(items) != 1:
            raise AssertionError(f"{description}必须唯一，实际找到 {len(items)} 项")
        return items[0]

    @staticmethod
    def _key(bus, station_id, function, address):
        if function != "0x03":
            raise ValueError("首批 slaveSim 页面用例只支持 0x03 保持寄存器")
        if not isinstance(bus, str) or not bus or type(station_id) is not int or not 1 <= station_id <= 247:
            raise ValueError("模拟器总线与站号无效")
        if type(address) is not int or not 0 <= address <= 65535:
            raise ValueError("模拟器协议地址必须在 0～65535 范围内")
        return bus, station_id, function, address

    @staticmethod
    def _replace(element, value):
        element.click()
        element.send_keys(Keys.CONTROL, "a", Keys.NULL, Keys.BACKSPACE)
        element.send_keys(str(value), Keys.TAB)

    def _config_bus(self, name):
        return self._unique([node for node in self.driver.find_elements(By.CSS_SELECTOR, "#config-buses > details.bus-editor")
                             if self._field(node, "总线名称").get_attribute("value") == name], "目标配置总线")

    def _configuration_snapshot(self, label):
        """只读取页面配置控件，不访问隐藏草稿或业务接口。"""
        buses = self.driver.execute_script("""
            const fields = root => Object.fromEntries(Array.from(root.querySelectorAll('label.config-field'), label => {
                const input = label.querySelector('input,select');
                return [label.querySelector('span').textContent.trim(), input?.type === 'checkbox' ? input.checked : input?.value];
            }));
            return Array.from(document.querySelectorAll('#config-buses > details.bus-editor'), bus => {
                const f = fields(bus.querySelector(':scope > .bus-fields'));
                return {name: f['总线名称'], link: f['串口软链接路径'], slaves:
                    Array.from(bus.querySelectorAll(':scope > details.slave-editor'), slave => {
                        const sf = fields(slave.querySelector(':scope > .config-grid'));
                        const model = slave.querySelector(':scope > .model-editor');
                        return {station_id: Number(sf['站号（1～247）']), fields: sf,
                            model: model ? (model.textContent.includes('当前页面不支持模型') ? '未知模型' : 'huawei_v3') : '',
                            model_fields: model ? fields(model) : {},
                            registers: Array.from(slave.querySelectorAll(':scope > details.register-group > details.register-editor'), reg => {
                                const rf = fields(reg);
                                return {function: rf['功能码'], address: Number(rf['协议地址']), type: rf['数据类型'],
                                    source: rf['数值来源'], byte_order: rf['字节序'], word_order: rf['32 位字序'],
                                    access: rf['访问权限'], value: rf['原始值'], fields: rf};
                            })};
                    })};
            });
        """)
        from .slavesim_runtime import validate_topology
        validate_topology(buses)
        self._snapshot({"类型": "模拟器实际配置", "阶段": label, "buses": buses})
        self.desktop.metadata.setdefault("slaveSim配置读取", []).append({"阶段": label, "总线数": len(buses)})
        return buses

    def _read_configuration(self, label):
        with self._step(f"读取slaveSim当前配置：{label}"):
            self.driver.find_element(By.ID, "show-config").click()
            self._wait(lambda _: self.driver.find_element(By.ID, "config-fields").is_enabled(), "配置读取尚未完成")
            self.driver.find_element(By.ID, "reload-config").click()
            self._wait(lambda _: (self.driver.find_element(By.ID, "config-fields").is_enabled()
                                  and self.driver.find_element(By.ID, "config-status").text == "已读取当前配置"),
                       "未读取slaveSim当前实际配置")
            return self._configuration_snapshot(label)

    def _add_default_bus(self):
        before = len(self.driver.find_elements(By.CSS_SELECTOR, "#config-buses > details.bus-editor"))
        self.driver.find_element(By.ID, "add-bus").click()
        self._wait(lambda _: len(self.driver.find_elements(By.CSS_SELECTOR, "#config-buses > details.bus-editor")) == before + 1,
                   "新增总线未显示")
        card = self.driver.find_elements(By.CSS_SELECTOR, "#config-buses > details.bus-editor")[-1]
        name = self._field(card, "总线名称").get_attribute("value")
        link = self._field(card, "串口软链接路径").get_attribute("value")
        return name, link

    def _save_configuration(self, label):
        with self._step(f"保存并应用slaveSim必要配置：{label}"):
            self._configuration_snapshot(label + "保存前草稿")
            self.driver.find_element(By.ID, "save-config").click()
            def saved(_):
                node = self.driver.find_element(By.ID, "config-status")
                if "error" in (node.get_attribute("class") or ""):
                    raise AssertionError(f"slaveSim配置保存失败：{node.text}")
                return node.text.startswith("配置已保存并应用")
            self._wait(saved, "slaveSim配置未确认保存并应用")
            self.desktop.metadata.setdefault("slaveSim配置调整", []).append({
                "内容": label, "运行影响": "保存整份配置会重建所有模型并重置临时手动值及指令；原有配置定义保留"})
        return self._read_configuration(label + "保存后实际配置")

    def prepare_basic(self, environment, *, check_paths=None):
        """执行时选择真实资源，不足时通过页面新增总线及两站手动测试点。"""
        from .modbus_environment import basic_config_from_environment
        from .slavesim_runtime import basic_candidate, assert_existing_preserved
        before = self._read_configuration("基础采集准备前")
        selected = basic_candidate(before)
        if selected is None:
            with self._step("按当前模拟器页面新增基础采集总线与两站手动点"):
                name, new_link = self._add_default_bus()
                for station in range(1, 3):
                    card = self._config_bus(name)
                    self._open(card)
                    Select(card.find_element(By.CSS_SELECTOR, "select[name='device-type']")).select_by_value("__custom_modbus__")
                    card.find_element(By.XPATH, ".//button[normalize-space(.)='添加设备']").click()
                    self._wait(lambda _: len(self._config_bus(name).find_elements(By.CSS_SELECTOR, ":scope > details.slave-editor")) == station,
                               "新增自定义从站未显示")
                    slave = self._config_bus(name).find_elements(By.CSS_SELECTOR, ":scope > details.slave-editor")[-1]
                    self._open(slave)
                    group = slave.find_element(By.CSS_SELECTOR, ":scope > details.register-group")
                    self._open(group)
                    reg = self._unique(group.find_elements(By.CSS_SELECTOR, ":scope > details.register-editor"), "新增从站默认寄存器")
                    self._open(reg)
                    Select(self._field(reg, "访问权限")).select_by_value("readwrite")
                    Select(self._field(reg, "数值来源")).select_by_value("manual")
                if check_paths is not None:
                    check_paths((new_link,))
            actual = self._save_configuration("新增基础采集总线及两站手动寄存器")
            assert_existing_preserved(before, actual)
            selected = basic_candidate([bus for bus in actual if bus["name"] == name])
            if selected is None:
                raise AssertionError("新增资源保存后未形成两站manual UINT16 AB读写测试点")
        bus, stations = selected
        if check_paths is not None:
            check_paths((bus["link"],))
        runtime = {"device_name": environment.get("name") or environment["modbus_manager_address"],
                   "bus": bus["name"], "serial_device": bus["link"], "stations": stations,
                   "data_bits": 8, "stop_bits": 1, "parity": "无校验"}
        config = basic_config_from_environment(environment, runtime)
        self.desktop.metadata["RTU运行时资源"] = runtime
        self.desktop.metadata["RTU串口参数来源"] = "slaveSim采用PTY，无UART参数；DSP使用9600/8N1，路径是否可访问由真实连接及采集结果判定"
        self.driver.find_element(By.ID, "show-runtime").click()
        for station in config.stations:
            observed = self.observe(config.bus, station.station_id, "0x03", station.address)
            if observed["link_path"] != config.serial_device:
                raise AssertionError("slaveSim运行路径与实际保存配置不一致，未猜测宿主机路径")
        return config

    def _verify_manual(self, key):
        """只读配置页，确认原值恢复不会破坏动态来源或行为模型。"""
        if key in self._verified:
            return
        bus, station, function, address = key
        self.driver.find_element(By.ID, "show-config").click()
        self._wait(lambda _: self.driver.find_element(By.ID, "config-status").text == "已读取当前配置",
                   "模拟器配置页未成功读取当前配置")
        buses = self.driver.find_elements(By.CSS_SELECTOR, "#config-buses > details.bus-editor")
        card = self._unique([node for node in buses if self._field(node, "总线名称").get_attribute("value") == bus], "模拟器配置总线")
        self._open(card)
        stations = card.find_elements(By.CSS_SELECTOR, ":scope > details.slave-editor")
        slave = self._unique([node for node in stations if self._field(node, "站号（1～247）").get_attribute("value") == str(station)], "模拟器配置从站")
        self._open(slave)
        title = slave.find_element(By.CSS_SELECTOR, ":scope > summary .editor-title").text
        if "自定义 Modbus 设备" not in title:
            raise AssertionError("RTU 测试点必须属于无行为模型的专用自定义设备")
        group = slave.find_element(By.CSS_SELECTOR, ":scope > details.register-group")
        self._open(group)
        registers = group.find_elements(By.CSS_SELECTOR, ":scope > details.register-editor")
        reg = self._unique([node for node in registers
                            if self._field(node, "功能码").get_attribute("value") == function
                            and self._field(node, "协议地址").get_attribute("value") == str(address)], "模拟器配置寄存器")
        self._open(reg)
        for label, expected in (("数值来源", "manual"), ("数据类型", "UINT16"),
                                ("字节序", "AB"), ("访问权限", "readwrite")):
            actual = self._field(reg, label).get_attribute("value")
            if actual != expected:
                raise AssertionError(f"模拟器 {label}：预期 {expected}，实际 {actual}；未修改配置")
        self._verified.add(key)
        self.driver.find_element(By.ID, "show-runtime").click()

    def _row(self, key):
        bus, station, function, address = key
        buttons = self.driver.find_elements(By.XPATH, "//div[@id='buses']//button[contains(@class,'bus-choice')][strong[normalize-space(.)=" + _literal(bus) + "]]")
        button = self._unique(buttons, "模拟器运行总线")
        if button.get_attribute("aria-pressed") != "true":
            button.click()
        bus_key = button.get_attribute("data-bus-key")
        workspace = self.driver.find_element(By.XPATH, "//section[contains(@class,'bus-workspace')][@data-bus-key=" + _literal(bus_key) + "]")
        if "连接中断" in workspace.find_element(By.CSS_SELECTOR, ".bus-summary").text:
            raise AssertionError("slaveSim 连接中断，页面反馈可能过期")
        device = workspace.find_element(By.CSS_SELECTOR, f"section.runtime-device[data-device-id='{station}']")
        group = device.find_element(By.CSS_SELECTOR, "details.runtime-registers")
        self._open(group)
        rows = group.find_elements(By.XPATH, ".//tbody/tr[td[1][normalize-space(.)=" + _literal(function)
                                   + "] and td[2][normalize-space(.)=" + _literal(address) + "]]")
        row = self._unique(rows, "模拟器运行寄存器")
        # 只调整可见区域，截图应包含被读取的寄存器。
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", row)
        return workspace, device, row

    def _read(self, key):
        if self._tick() < 0:
            raise AssertionError("slaveSim 状态刷新失败，拒绝使用过期页面值")
        workspace, _, row = self._row(key)
        cells = row.find_elements(By.TAG_NAME, "td")
        if cells[2].text != "UINT16":
            raise AssertionError("模拟器运行类型不是 UINT16")
        raw = cells[5].text.strip()
        try:
            encoded = bytes.fromhex(raw)
            value = Decimal(cells[4].text.strip().split()[0])
        except (ValueError, InvalidOperation, IndexError):
            raise AssertionError("模拟器当前值或原始字节格式无效") from None
        if len(encoded) != 2 or not value.is_finite() or value != int.from_bytes(encoded, "big"):
            raise AssertionError(f"模拟器当前值与 UINT16 AB 原始字节不一致：{cells[4].text} / {raw}")
        paths = workspace.find_elements(By.CSS_SELECTOR, ".bus-path-value")
        return {"bus": key[0], "station_id": key[1], "function": key[2], "address": key[3],
                "type": cells[2].text, "tag": cells[3].text, "value": int(value), "raw": raw,
                "link_path": paths[1].text, "observed_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "refresh_tick": self._tick()}

    def _snapshot(self, result):
        self._snapshots += 1
        path = self.desktop.artifacts / f"slavesim-observation-{self._snapshots:04d}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.desktop.metadata.setdefault("slaveSim页面观察", []).append(path.name)

    def observe(self, bus, station_id, function, address):
        key = self._key(bus, station_id, function, address)
        with self._step(f"读取总线 {bus} / 站号 {station_id} / 地址 {address} 实际反馈") as entry:
            self._verify_manual(key)
            before = self._tick()
            self._wait(lambda _: self._tick() > max(before, 0), "slaveSim 没有新的状态反馈，拒绝使用旧页面值")
            result = self._read(key)
            self._snapshot(result)
            entry["实际"] = json.dumps(result, ensure_ascii=False)
            return result

    def set_value(self, bus, station_id, function, address, value):
        key = self._key(bus, station_id, function, address)
        if type(value) is not int or not 0 <= value <= 65535:
            raise ValueError("UINT16 测试值必须为 0～65535 的整数")
        original = self.observe(*key)
        if key not in self._originals:
            self._originals[key] = original["value"]
            self.desktop.metadata.setdefault("slaveSim恢复原值", []).append({
                "总线": bus, "站号": station_id, "功能码": function, "地址": address,
                "原值": original["value"], "恢复状态": "待恢复"})
        with self._step(f"设置总线 {bus} / 站号 {station_id} / 地址 {address}", expected=value) as entry:
            _, _, row = self._row(key)
            input_box = row.find_element(By.CSS_SELECTOR, "input[type=text]")
            input_box.click()
            input_box.send_keys(Keys.CONTROL, "a", Keys.NULL, Keys.BACKSPACE)
            input_box.send_keys(str(value), Keys.TAB)
            before = self._tick()
            row.find_element(By.CSS_SELECTOR, "button.primary").click()
            def confirmed(_):
                _, current_device, _ = self._row(key)
                message = current_device.find_element(By.CSS_SELECTOR, ".register-message").text
                if "失败" in message or "请输入" in message:
                    raise AssertionError(f"slaveSim 设值失败：{message}")
                return message.startswith("已设置") and self._tick() > max(before, 0) and self._read(key)["value"] == value
            self._wait(confirmed, "slaveSim 手动设值未在时限内得到页面实际值确认")
            result = self._read(key)
            self._snapshot(result)
            entry["实际"] = json.dumps(result, ensure_ascii=False)
            return result

    def restore_all(self):
        """manual 来源恢复原数值；隐藏覆盖标志无页面撤销入口。"""
        failures = []
        self._restoring = True
        try:
            for key, value in list(self._originals.items()):
                try:
                    self.set_value(*key, value)
                    del self._originals[key]
                    for record in self.desktop.metadata.get("slaveSim恢复原值", []):
                        if (record["总线"], record["站号"], record["功能码"], record["地址"]) == key:
                            record["恢复状态"] = "已恢复原数值"
                except Exception as error:
                    failures.append(f"{key}：{error}")
            if failures:
                raise RuntimeError("；".join(failures))
        finally:
            self._restoring = False
