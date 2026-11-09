"""通过真实桌面界面配置 RTU 并读取采集结果，仅清理本例创建的连接。"""
from decimal import Decimal, InvalidOperation
import logging

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait


def xpath_text(value):
    """将外部名称作为 XPath 字面量，避免名称改变定位语义。"""
    value = str(value)
    if "'" not in value:
        return "'" + value + "'"
    if '"' not in value:
        return '"' + value + '"'
    return "concat(" + ",\"'\",".join("'" + part + "'" for part in value.split("'")) + ")"


class ModbusRtuPage:
    """只使用 Selenium 可见控件，不调用被测系统内部 API。"""

    DIALOG = (By.CSS_SELECTOR, '.ant-modal[role="dialog"]')
    DIALOG_XPATH = "//div[@role='dialog' and contains(concat(' ',normalize-space(@class),' '),' ant-modal ')]"

    def __init__(self, desktop):
        self.desktop = desktop
        self.owned = {}
        self.current = None
        self._cleaning = False

    @property
    def driver(self):
        return self.desktop.driver

    def _until(self, predicate, message="", timeout=None):
        def probe(driver):
            if not self._cleaning:
                self.desktop._check_stop()
            return predicate(driver)
        return WebDriverWait(self.driver, timeout or self.desktop.timeout,
                             ignored_exceptions=(NoSuchElementException, StaleElementReferenceException)).until(probe, message)

    def _visible(self, locator):
        matches = [element for element in self.driver.find_elements(*locator) if element.is_displayed()]
        if len(matches) > 1:
            raise RuntimeError(f"界面定位不唯一，拒绝操作：{locator}")
        return matches[0] if matches else None

    def _click(self, locator, name):
        with self.desktop.step(name, cancellable=not self._cleaning):
            element = self._until(lambda _: (item if (item := self._visible(locator)) and item.is_enabled() else False),
                                  f"控件未就绪：{name}")
            element.click()

    @staticmethod
    def _button(text, root=""):
        return (By.XPATH, root + "//button[translate(normalize-space(.),' ','')=" + xpath_text(text.replace(" ", "")) + "]")

    def _menu(self, label):
        locator = (By.XPATH, "//li[@role='menuitem'][.//span[normalize-space(.)=" + xpath_text(label) + "]]")
        self._click(locator, f"打开{label}页面")

    def _rtu(self):
        locator = (By.XPATH, "//li[@role='menuitem'][.//span[normalize-space(.)='Modbus RTU']]")
        if not self._visible(locator):
            self._click((By.XPATH, "//div[@role='menuitem'][.//span[normalize-space(.)='协议接入']]"), "展开协议接入菜单")
        self._click(locator, "打开 Modbus RTU 页面")
        config_tab = (By.XPATH, "//*[@aria-label='协议页面切换']//*[@role='tab'][normalize-space(.)='连接配置']")
        if self._visible(config_tab):
            self._click(config_tab, "切换 RTU 配置页签")
        self._until(lambda _: self._visible((By.CSS_SELECTOR, ".protocol-connection-list-card")), "RTU连接列表未显示")

    def open_device(self, manager_address, *, required_modules=("ModbusRTU",)):
        self._menu("模块运维")
        self._click((By.XPATH, "//*[@aria-label='连接方式']//label[normalize-space(.)='现场直连']"), "选择现场直连")
        switch = self._button("切换至现场直连")
        if self._visible(switch):
            self._click(switch, "切换运行连接至现场直连")
        with self.desktop.step("填写目标下位机 ModuleManager 地址", expected=manager_address):
            field = self._until(lambda _: self._visible((By.CSS_SELECTOR, 'input[aria-label="ModuleManager 地址"]')))
            self._replace(field, manager_address)
        self._click(self._button("连接", "//div[contains(@class,'module-ops-connection-controls')]"), "连接目标下位机")
        with self.desktop.step("确认目标下位机所需模块已运行", expected="、".join(required_modules)):
            def ready(_):
                for module in required_modules:
                    row = self._visible((By.XPATH, "//div[contains(@class,'module-ops-list-card')]//tr[@data-row-key="
                                         + xpath_text(module) + "]"))
                    if row is None or "运行中" not in row.text:
                        return False
                    # 连接失败时界面可能保留旧表格，但停止按钮会禁用。
                    actions = row.find_elements(By.XPATH, ".//button[normalize-space(.)='停止']")
                    if not any(action.is_enabled() for action in actions):
                        return False
                return True
            self._until(ready, "未确认目标下位机已连接并运行所有所需模块，请检查测试环境")
        self._rtu()

    def assert_serial_paths_available(self, paths):
        """只读检查目标串口占用，在修改模拟资源前调用；不接管既有DSP连接。"""
        with self.desktop.step("检查目标串口未被既有RTU连接使用"):
            refresh = (By.CSS_SELECTOR, 'button[aria-label="刷新连接列表"]')
            def ready(_):
                button = self._visible(refresh)
                return button and button.is_enabled() and "ant-btn-loading" not in (button.get_attribute("class") or "")
            self._until(ready, "RTU连接列表仍在加载")
            self._click(refresh, "只读刷新既有RTU连接列表")
            self._until(ready, "RTU连接列表刷新未完成")
            for alert in self.driver.find_elements(By.CSS_SELECTOR, ".modbus-page-alert"):
                if alert.is_displayed() and "连接列表刷新失败" in alert.text:
                    raise AssertionError("既有RTU连接列表刷新失败，不能确认测试串口空闲")
            names = [node.text.strip() for node in self.driver.find_elements(
                By.CSS_SELECTOR, ".protocol-connection-item-main strong")]
            observed = []
            for name in names:
                self._click(self._connection_locator(name), f"只读检查既有连接 {name}")
                title = (By.XPATH, "//div[contains(@class,'modbus-connection-card')]"
                         "//div[contains(@class,'ant-card-head-title')]//span[normalize-space(.)=" + xpath_text(name) + "]")
                self._until(lambda _: self._visible(title), "既有连接详情未切换至目标连接")
                labels = [node.text.strip() for node in self.driver.find_elements(
                    By.CSS_SELECTOR, ".modbus-connection-card .ant-descriptions-item-label")]
                if "串口设备" in labels:
                    path = self._detail("串口设备")
                    observed.append({"连接": name, "串口": path})
                    if path in paths:
                        raise AssertionError(f"串口 {path} 已被既有连接 {name} 使用，停止新增DSP连接；模拟器准备情况见执行记录")
            self.desktop.metadata["RTU既有串口检查"] = observed

    @staticmethod
    def _replace(element, value):
        element.click()
        element.send_keys(Keys.CONTROL, "a", Keys.NULL, Keys.BACKSPACE)
        element.send_keys(str(value), Keys.TAB)

    def _dialog(self):
        return self._until(lambda _: self._visible(self.DIALOG), "配置窗口未打开")

    def _field(self, name):
        return self._dialog().find_element(By.CSS_SELECTOR, f'[id="{name}"]')

    def _fill(self, name, value, label):
        with self.desktop.step(f"填写{label}", expected=str(value), cancellable=not self._cleaning):
            self._replace(self._field(name), value)

    def _select_option(self, name, label):
        with self.desktop.step(f"设置{name}为{label}", expected=label, cancellable=not self._cleaning):
            # Antd 非搜索 Select 的内部 input 透明，点击可见 selector 而非透明输入框。
            container = self._field(name).find_element(
                By.XPATH, "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' ant-select ')][1]")
            container.find_element(By.CSS_SELECTOR, ".ant-select-selector").click()
            option = (By.XPATH, "//div[contains(@class,'ant-select-item-option') and @title=" + xpath_text(label) + "]")
            self._until(lambda _: self._visible(option), f"未找到选项：{label}").click()

    def _submit(self, text):
        self._click(self._button(text, self.DIALOG_XPATH), f"提交{text}")
        self._until(lambda _: not self._visible(self.DIALOG), f"{text}未成功，配置窗口仍然打开")

    def _connection_locator(self, name):
        return (By.XPATH, "//div[contains(@class,'protocol-connection-item-main')][.//strong[normalize-space(.)=" + xpath_text(name) + "]]")

    def _detail(self, label):
        return self.driver.find_element(By.XPATH, "//div[contains(@class,'modbus-connection-card')]//td[.//*[contains(@class,'ant-descriptions-item-label') and normalize-space(.)="
                                        + xpath_text(label) + "]]//*[contains(@class,'ant-descriptions-item-content')]").text.strip()

    def _assert_identity(self):
        if self.current not in self.owned:
            raise RuntimeError("未选择本例拥有的 RTU 连接")
        actual = self._detail("连接编号")
        if actual != self.owned[self.current]["conn_id"]:
            raise RuntimeError("RTU连接编号已变化，拒绝操作其他连接")

    def _selected_conn_id(self):
        return self.owned[self.current]["conn_id"]

    def select(self, name):
        if name not in self.owned:
            raise RuntimeError("拒绝选择非本例创建的连接")
        self._click(self._connection_locator(name), f"选择本例 RTU 连接 {name}")
        self._until(lambda _: name in self.driver.find_element(By.CSS_SELECTOR, ".modbus-connection-card .ant-card-head").text)
        self.current = name
        self._assert_identity()

    def create_connection(self, name, serial_device, baud_rate, data_bits, stop_bits, parity,
                          station_id, poll_interval_ms, address_base=0, read_timeout_ms=1000):
        if address_base not in (0, 1) or parity not in ("N", "O", "E"):
            raise ValueError("地址基准必须为0或1，校验位必须为N/O/E")
        with self.desktop.step("确认本例连接名称未被占用", expected="不存在同名连接", actual=name):
            if self.driver.find_elements(*self._connection_locator(name)) or name in self.owned:
                raise RuntimeError("存在同名 RTU 连接，拒绝接管或覆盖")
        expected = dict(conn_name=name, serial_device=serial_device, baud_rate=baud_rate,
                        data_bits=data_bits, device_id=station_id, poll_interval_ms=poll_interval_ms,
                        read_timeout_ms=read_timeout_ms)
        options = dict(transport_type="本地串口", parity={"N": "无校验", "O": "奇校验", "E": "偶校验"}[parity],
                       stop_bits=str(stop_bits), address_base="0 基 (协议偏移)" if address_base == 0 else "1 基 (人类编号)")
        self._click(self._button("新增连接", "//div[contains(@class,'protocol-connection-list-card')]"), "新增专用 RTU 连接")
        self._select_option("transport_type", options["transport_type"])
        for field, value in expected.items():
            self._fill(field, value, field)
        for field, value in options.items():
            if field != "transport_type":
                self._select_option(field, value)
        self.desktop.metadata.setdefault("RTU创建意图", []).append({"连接名称": name, "串口": serial_device,
                                                               "站号": station_id, "说明": "仅为创建请求，连接身份待页面确认"})
        self._submit("创建连接")
        self._until(lambda _: self._visible(self._connection_locator(name)), "创建后连接列表未显示本例连接")
        # 创建成功且名称已在提交前确认未占用，随后读取设备分配的编号作为清理凭据。
        self._click(self._connection_locator(name), "读取新建连接身份")
        self._until(lambda _: name in self.driver.find_element(By.CSS_SELECTOR, ".modbus-connection-card .ant-card-head").text)
        conn_id = self._until(lambda _: self._detail("连接编号") if self._detail("连接编号") not in ("", "-", "0") else False)
        self.owned[name] = {"name": name, "conn_id": conn_id, "fields": expected, "options": options, "points": {}}
        self.desktop.metadata.setdefault("本例RTU连接", []).append({"name": name, "conn_id": conn_id, "清理状态": "待清理"})
        self.current = name
        logging.getLogger(__name__).info("已创建本例 RTU 连接：%s，编号=%s", name, conn_id)
        return {"name": name, "conn_id": conn_id}

    def add_point(self, tag, function=3, address=0, data_type="UINT16", scale=1, offset=0):
        self._assert_identity()
        if function not in (3, 4) or data_type != "UINT16":
            raise ValueError("首批RTU采集用例仅支持0x03/0x04 UINT16")
        self._click(self._button("添加点位", "//div[contains(@class,'protocol-point-card')]"), "新增 RTU 采集点")
        self._fill("tag", tag, "Tag")
        function_label = "0x03 读保持寄存器" if function == 3 else "0x04 读输入寄存器"
        self._select_option("function", function_label)
        self._fill("address", address, "点位地址")
        self._select_option("data_type", data_type)
        self._select_option("reg_count", "1 个寄存器")
        self._fill("scale", scale, "缩放系数")
        self._fill("offset", offset, "偏移量")
        self._fill("deadband", 0, "死区")
        self._select_option("byte_order", "AB")
        self._submit("添加点位")
        self.owned[self.current]["points"][tag] = {"tag": tag, "function": function_label, "address": address,
                                                     "data_type": data_type, "scale": scale, "offset": offset}

    def _assert_input(self, field, expected):
        actual = self._field(field).get_attribute("value")
        try:
            equal = Decimal(actual) == Decimal(str(expected))
        except InvalidOperation:
            equal = actual == str(expected)
        self.desktop.check(f"保存后{field}一致", equal, True)

    def _assert_select(self, field, expected):
        actual = self._field(field).find_element(By.XPATH, "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' ant-select ')][1]")
        text = actual.find_element(By.CSS_SELECTOR, ".ant-select-selection-item").get_attribute("title")
        self.desktop.check(f"保存后{field}一致", text, expected)

    def assert_saved_configuration(self):
        name = self.current
        self._menu("模块运维")
        self._rtu()
        self.select(name)
        self._click(self._button("编辑配置", "//div[contains(@class,'modbus-connection-card')]"), "重新读取连接配置")
        for field, value in self.owned[name]["fields"].items():
            self._assert_input(field, value)
        for field, value in self.owned[name]["options"].items():
            self._assert_select(field, value)
        self._click(self._button("取消", self.DIALOG_XPATH), "关闭配置核对窗口")
        for tag, expected in self.owned[name]["points"].items():
            self._click((By.CSS_SELECTOR, f'button[aria-label="编辑点位 {tag}"]'), f"重新读取点位 {tag}")
            for field in ("tag", "address", "scale", "offset"):
                self._assert_input(field, expected[field])
            self._assert_input("deadband", 0)
            for field in ("function", "data_type"):
                self._assert_select(field, expected[field])
            self._assert_select("reg_count", "1 个寄存器")
            self._assert_select("byte_order", "AB")
            self._click(self._button("取消", self.DIALOG_XPATH), "关闭点位核对窗口")

    def start(self):
        self._assert_identity()
        self._click(self._button("启动轮询", "//div[contains(@class,'modbus-connection-card')]"), "启动本例 RTU 连接轮询")
        self._until(lambda _: "运行中" in self.driver.find_element(By.CSS_SELECTOR, ".modbus-connection-card .ant-card-head").text,
                    "连接未进入运行中")
        self._runtime_view()

    def _runtime_view(self):
        locator = (By.XPATH, "//div[contains(@class,'protocol-point-card')]//label[normalize-space(.)='运行视图']")
        choice = self._until(lambda _: self._visible(locator), "运行视图切换未显示")
        if "ant-segmented-item-selected" not in (choice.get_attribute("class") or ""):
            self._click(locator, "显示 RTU 运行点值")
        self._until(lambda _: any(cell.text.strip() == "实时值" for cell in
                                 self.driver.find_elements(By.CSS_SELECTOR, ".protocol-point-card .ant-table-thead th")),
                    "运行点表未显示实时值列")

    def observe(self, tag):
        self._assert_identity()
        # 切换连接会重新创建点表组件，默认回到配置视图。
        self._runtime_view()
        row = self.driver.find_element(By.XPATH, "//div[contains(@class,'protocol-point-card')]//tr[.//td//strong[normalize-space(.)=" + xpath_text(tag) + "]]")
        cells = row.find_elements(By.CSS_SELECTOR, "td")
        headers = [cell.text.strip() for cell in self.driver.find_elements(By.CSS_SELECTOR, ".protocol-point-card .ant-table-thead th")]
        values = {label: cell.text.strip() for label, cell in zip(headers, cells)}
        observation = {"tag": tag, "value": values["实时值"], "quality": values["质量"], "timestamp": values["时间"],
                "address": values["地址"], "function": values["功能码"], "connection": self.current,
                "conn_id": self._selected_conn_id()}
        self.desktop.metadata.setdefault("RTU最后观察", {})[f"{self.current}/{tag}"] = observation
        return observation

    def wait_value(self, tag, expected, tolerance, timeout, previous_timestamp=None):
        last = {}
        expected, tolerance = Decimal(str(expected)), Decimal(str(tolerance))
        def matches(_):
            nonlocal last
            last = self.observe(tag)
            try:
                value = Decimal(last["value"])
            except InvalidOperation:
                return False
            return (last if value.is_finite() and abs(value - expected) <= tolerance
                    and last["quality"] == "正常" and last["timestamp"] not in ("", "-")
                    and (previous_timestamp is None or last["timestamp"] != previous_timestamp) else False)
        with self.desktop.step(f"核对上位机点值 {tag}", expected=f"值={expected}，误差≤{tolerance}，质量正常且采集时间有效"):
            try:
                snapshot = self._until(matches, timeout=timeout)
            except TimeoutException:
                raise AssertionError(f"RTU点值未在{timeout}秒内满足预期；预期={expected}，容差={tolerance}，上一时间={previous_timestamp}，最后观察={last}") from None
            self.desktop.capture("rtu-value", description=f"DSP上位机点值：{tag}", expected=str(expected), actual=str(snapshot))
            logging.getLogger(__name__).info("RTU双端校验上位机观察：%s", snapshot)
            return snapshot

    def stop(self):
        self._assert_identity()
        header = self.driver.find_element(By.CSS_SELECTOR, ".modbus-connection-card .ant-card-head").text
        if "运行中" in header:
            self._click(self._button("停止轮询", "//div[contains(@class,'modbus-connection-card')]"), "停止本例 RTU 轮询")
            self._click(self._button("确定", "//div[contains(@class,'ant-popover') and not(contains(@class,'hidden'))]"), "确认停止本例轮询")
            self._until(lambda _: "已停止" in self.driver.find_element(By.CSS_SELECTOR, ".modbus-connection-card .ant-card-head").text,
                        "本例连接未停止")

    def cleanup(self):
        """协作取消时仍清理；身份不符时停止，不删除未知连接。"""
        self._cleaning = True
        errors = []
        try:
            for name in list(self.owned):
                try:
                    with self.desktop.step(f"清理本例 RTU 连接 {name}", cancellable=False):
                        # 失败现场已经由步骤截图保留，随后只关闭未提交的配置窗口。
                        modal = self._visible(self.DIALOG)
                        if modal:
                            self._click(self._button("取消", self.DIALOG_XPATH), "取消未提交的配置窗口")
                        self._rtu()
                        self.select(name)
                        self.stop()
                        self._assert_identity()
                        self._click((By.XPATH, "//button[@aria-label=" + xpath_text("删除连接 " + name) + "]"), "删除本例专用连接")
                        self._click(self._button("确定", "//div[contains(@class,'ant-popover') and not(contains(@class,'hidden'))]"), "确认删除本例专用连接")
                        self._until(lambda _: not self.driver.find_elements(*self._connection_locator(name)), "本例连接未删除")
                        for identity in self.desktop.metadata.get("本例RTU连接", []):
                            if identity["name"] == name and identity["conn_id"] == self.owned[name]["conn_id"]:
                                identity["清理状态"] = "已删除"
                        del self.owned[name]
                except Exception as error:
                    errors.append(f"{name}：{error}")
            if errors:
                raise RuntimeError("RTU连接清理失败，请人工检查本例资源：" + "；".join(errors))
        finally:
            self._cleaning = False
