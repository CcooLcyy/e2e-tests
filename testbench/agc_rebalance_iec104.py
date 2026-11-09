"""从空白专用下位机的界面配置 AGC 验收使用的 IEC104 遥调入口。"""

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from .modbus_rtu_page import ModbusRtuPage, xpath_text


COMMON_ADDRESS = 1
COMMAND_IOA = 0x6201
COMMAND_POINT = "e2e_agc_rebalance_total_kw"
LINK_NAME = "e2e-agc-rebalance-104"


class AgcRebalanceIec104Page(ModbusRtuPage):
    """通过 IEC104 点位导入建立遥调路由，并在数据总线读回。"""

    _LIST = ".iec104-page .protocol-connection-list-card"
    _DETAIL = ".iec104-page .iec104-connection-card"

    def _open(self):
        menu = (By.XPATH, "//li[@role='menuitem'][.//span[normalize-space(.)='IEC104']]")
        if not self._visible(menu):
            self._click((By.XPATH, "//div[@role='menuitem'][.//span[normalize-space(.)='协议接入']]"),
                        "展开协议接入菜单")
        self._click(menu, "打开 IEC104 页面")
        tab = (By.XPATH, "//*[@aria-label='协议页面切换']//*[@role='tab'][normalize-space(.)='连接配置']")
        if self._visible(tab):
            self._click(tab, "切换 IEC104 连接配置页签")
        self._until(lambda _: self._visible((By.CSS_SELECTOR, self._LIST)), "IEC104 连接列表未显示")

    def assert_empty(self):
        """读取持久配置。已停止或未注册到数据总线的连接也会阻止用例。"""
        self._open()
        with self.desktop.step("核对专用下位机没有 IEC104 连接", expected="IEC104 连接列表为空"):
            refresh = (By.CSS_SELECTOR, f'{self._LIST} button[aria-label="刷新连接列表"]')

            def ready(_):
                button = self._visible(refresh)
                return button and button.is_enabled() and "ant-btn-loading" not in (button.get_attribute("class") or "")

            self._until(ready, "IEC104 连接列表尚未加载")
            self._click(refresh, "只读刷新 IEC104 连接列表")
            self._until(ready, "IEC104 连接列表刷新未完成")
            for alert in self.driver.find_elements(By.CSS_SELECTOR, ".iec104-page .iec104-page-alert"):
                if alert.is_displayed() and "连接列表刷新失败" in alert.text:
                    raise AssertionError("IEC104 连接列表刷新失败，无法确认空环境")
            card = self._visible((By.CSS_SELECTOR, self._LIST))
            existing = [item.text.strip() for item in card.find_elements(
                By.CSS_SELECTOR, ".protocol-connection-item-main strong") if item.is_displayed()]
            self.desktop.metadata["初始IEC104连接"] = existing
            if existing or "暂无连接" not in card.text:
                raise AssertionError(f"要求 IEC104 连接列表为空，实际连接：{existing}")

    def configure(self, group_name, command_tag, port=2404):
        """配置指定 AGC 组的专用遥调入口，成功后启动监听。"""
        if not group_name.startswith("e2e-agc-") or not command_tag or not 1 <= port <= 65535:
            raise ValueError("IEC104 遥调需要本例专用 AGC 组、命令标签和有效端口")
        self.assert_empty()
        self._create_link(port)
        self._import_command_point(group_name, command_tag)
        self._start()
        self._assert_route(group_name, command_tag)

    def _create_link(self, port):
        list_root = "//div[contains(@class,'iec104-page')]//div[contains(@class,'protocol-connection-list-card')]"
        self._click(self._button("新增连接", list_root), "新增专用 IEC104 连接")
        self._fill("conn_name", LINK_NAME, "IEC104 连接名称")
        with self.desktop.step("核对 IEC104 新建连接的默认角色", expected="SERVER / SLAVE (被控站)"):
            for field, expected in (("role", "SERVER"), ("station_role", "SLAVE (被控站)")):
                select = self._field(field).find_element(
                    By.XPATH, "ancestor::div[contains(concat(' ',normalize-space(@class),' '),' ant-select ')][1]")
                if select.text.strip() != expected:
                    raise AssertionError(f"IEC104 新建连接的默认角色不符：{field}={select.text}")
        self._fill("ca", COMMON_ADDRESS, "IEC104 公共地址")
        self._fill("oa", 0, "IEC104 源地址")
        self._fill("local_ip", "0.0.0.0", "IEC104 监听 IP")
        self._fill("local_port", port, "IEC104 监听端口")
        self._submit("确定")
        self._click(self._connection_locator(LINK_NAME), "选择刚创建的 IEC104 连接")

        with self.desktop.step("读取新建 IEC104 被控站", expected=f"SERVER / SLAVE，CA={COMMON_ADDRESS}，端口={port}"):
            def detail_ready(_):
                card = self._visible((By.CSS_SELECTOR, self._DETAIL))
                return card and all(text in card.text for text in (LINK_NAME, "SERVER", "SLAVE (被控站)", f"0.0.0.0:{port}"))

            self._until(detail_ready, "未读回新建 IEC104 监听端点和站点角色")
            ca = (By.XPATH, "//div[contains(@class,'iec104-connection-card')]"
                  "//td[.//*[contains(@class,'ant-descriptions-item-label') and normalize-space(.)='公共地址 (CA)']]"
                  "//*[contains(@class,'ant-descriptions-item-content')]")
            self._until(lambda _: (value := self._visible(ca)) and value.text.strip() == str(COMMON_ADDRESS),
                        "未读回 IEC104 公共地址")

    def _import_option(self, placeholder, label, *, searchable=False):
        locator = (By.XPATH, "//div[contains(@class,'iec104-import-modal')]"
                   "//div[contains(concat(' ',normalize-space(@class),' '),' ant-select ')]"
                   "[not(contains(concat(' ',normalize-space(@class),' '),' ant-select-disabled '))]"
                   "[.//*[contains(@class,'placeholder') and normalize-space(.)=" + xpath_text(placeholder) + "]]")
        with self.desktop.step(f"设置{placeholder}", expected=label):
            container = self._until(lambda _: self._visible(locator), f"缺少下拉字段：{placeholder}")
            container.click()
            field = container.find_element(By.CSS_SELECTOR, "input") if searchable else None
            if field is not None:
                field.send_keys(label)
            option = (By.XPATH, "//div[contains(concat(' ',normalize-space(@class),' '),' ant-select-item-option ')"
                      " and @title=" + xpath_text(label) + "]")
            self._until(lambda _: self._visible(option), f"未找到 IEC104 导入选项：{label}").click()
            if field is not None and field.get_attribute("aria-expanded") == "true":
                field.send_keys(Keys.ESCAPE)

    def _import_command_point(self, group_name, command_tag):
        point_root = "//div[contains(@class,'iec104-page')]//div[contains(@class,'protocol-point-card')]"
        self._click(self._button("添加点位", point_root), "打开 IEC104 添加点位菜单")
        self._click((By.XPATH, "//*[@role='menuitem'][normalize-space(.)='从数据总线导入']"), "从数据总线导入 AGC 命令点")
        group = f"AGC/{group_name}"
        self._import_option("先选择来源连接", group, searchable=True)
        self._import_option("选择一个或多个来源点位", f"{group} : {command_tag}", searchable=True)
        self._import_option("统一点位类型", "FLOAT (短浮点值)")
        self._import_option("统一 IOA 业务类别", "遥调")
        with self.desktop.step("设置 IEC104 专用遥调标签", expected=COMMAND_POINT):
            field = (By.CSS_SELECTOR, ".iec104-import-modal tbody tr[data-row-key] td:nth-child(2) input")
            self._replace(self._until(lambda _: self._visible(field), "导入草稿标签编辑框未显示"), COMMAND_POINT)
        self._submit("导入 1 个点位")

        with self.desktop.step("读回 IEC104 遥调点表", expected=f"{COMMAND_POINT} / {COMMAND_IOA} / 短浮点遥调"):
            locator = (By.CSS_SELECTOR, f'.iec104-page .protocol-point-card [data-row-key="{COMMAND_POINT}"]')

            def point_ready(_):
                row = self._visible(locator)
                return row and all(part in row.text for part in (COMMAND_POINT, f"{COMMAND_IOA} (", "FLOAT (短浮点值)", "遥调"))

            self._until(point_ready, "未读回 IEC104 短浮点遥调点位")

    def _start(self):
        self._click(self._button("连接", "//div[contains(@class,'iec104-connection-card')]"), "启动专用 IEC104 被控站")
        with self.desktop.step("确认 IEC104 被控站已开始监听", expected="运行中"):
            def running(_):
                title = self._visible((By.CSS_SELECTOR, f"{self._DETAIL} .iec104-connection-title"))
                return title and LINK_NAME in title.text and "运行中" in title.text

            self._until(running, "IEC104 专用被控站未启动")

    def _assert_route(self, group_name, command_tag):
        self._menu("数据总线")
        tab = (By.XPATH, "//*[@aria-label='数据总线页面切换']//*[@role='tab'][normalize-space(.)='路由配置']")
        if self._visible(tab):
            self._click(tab, "切换数据总线路由配置")
        root = "//div[contains(@class,'data-bus-route-card')]"
        self._click(self._button("刷新", root), "只读刷新数据总线路由")
        source = f"IEC104/{LINK_NAME} : {COMMAND_POINT}"
        destination = f"AGC/{group_name} : {command_tag}"
        with self.desktop.step("确认遥调路由从 IEC104 指向 AGC", expected=f"{source} → {destination}"):
            locator = (By.XPATH, root + "//tr[.//td[.//strong[normalize-space(.)=" + xpath_text(source)
                       + "]]/following-sibling::td[.//strong[normalize-space(.)=" + xpath_text(destination) + "]]"
                       + " and .//td[normalize-space(.)='已配置']]")
            self._until(lambda _: self._visible(locator), "数据总线未读回 IEC104 → AGC 遥调路由")
            self.desktop.metadata["IEC104遥调路由"] = {"源": source, "目标": destination,
                                                     "CA": COMMON_ADDRESS, "IOA": COMMAND_IOA}
