"""通过桌面配置专用 AGC 控制组并读取重分配证据。"""
from datetime import datetime
import re

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from .agc_direction_page import AgcDirectionPage, _timestamp, parse_decimal, unique_match
from .modbus_rtu_page import xpath_text


AGC_COMMAND_TAG = "AGC总控点"
GROUP_LIST = "//div[contains(@class,'ant-card')][./div[contains(@class,'ant-card-head')]//*[normalize-space(.)='控制组列表']]"
GROUP_DIALOG = "//div[contains(concat(' ',normalize-space(@class),' '),' agc-group-modal ')]"
MEMBER_DIALOG = "//div[contains(concat(' ',normalize-space(@class),' '),' agc-member-modal ')]"
OVERVIEW = (By.CSS_SELECTOR, 'section[aria-label="AGC 功率总览"]')
OVERVIEW_XPATH = "//section[@aria-label='AGC 功率总览']"


class AgcRebalancePage(AgcDirectionPage):
    """只操作可见控件，按连接身份读取本例控制组的运行结果。"""

    def __init__(self, desktop):
        super().__init__(desktop)
        self.members = ()

    def assert_empty(self):
        self._control_view("控制策略")
        refresh = (By.XPATH, GROUP_LIST + "//div[contains(@class,'ant-card-extra')]/button")
        empty = (By.XPATH, GROUP_LIST + "//*[normalize-space(text())='暂无控制组']")
        with self.desktop.step("确认专用下位机没有既有 AGC 控制组"):
            self._click(refresh, "刷新 AGC 控制组列表")

            def ready(_):
                button = self._visible(refresh)
                return button and "ant-btn-loading" not in (button.get_attribute("class") or "")

            self._until(ready, "AGC 控制组列表刷新未完成")
            if self._visible((By.XPATH, "//*[contains(@class,'ant-message-custom-content')][contains(.,'刷新控制组失败')]")):
                raise AssertionError("AGC 控制组列表读取失败，无法确认空环境")
            groups = self.driver.find_elements(By.XPATH, GROUP_LIST + "//li[contains(@class,'ant-list-item')]")
            if groups or not self._visible(empty):
                raise AssertionError("专用下位机的 AGC 控制组列表不是空列表，停止配置")

    def _field_value(self, root, field, value):
        locator = (By.XPATH, root + "//input[@id=" + xpath_text(field) + "]")
        with self.desktop.step(f"填写 AGC {field}", expected=str(value)):
            self._replace(self._until(lambda _: self._visible(locator)), value)

    def _choice(self, root, field, value, *, search=None, aria=False):
        attribute = "aria-label" if aria else "id"
        control = root + f"//input[@{attribute}=" + xpath_text(field) + "]"
        option = (By.XPATH, "//div[contains(@class,'ant-select-item-option') and @title=" + xpath_text(value) + "]")
        with self.desktop.step(f"选择 AGC {field}", expected=value):
            container = (By.XPATH, control + "/ancestor::div[contains(concat(' ',normalize-space(@class),' '),' ant-select ')][1]")
            def enabled(_):
                element = self._visible(container)
                return element and "ant-select-disabled" not in (element.get_attribute("class") or "") and element

            select = self._until(enabled)
            select.find_element(By.CSS_SELECTOR, ".ant-select-content").click()
            if search is not None:
                self.driver.find_element(By.XPATH, control).send_keys(search)
            self._until(lambda _: self._visible(option), f"未找到 AGC 选项 {value}").click()

    def _bind_member_point(self, label, connection, tag):
        prefix = f"ModbusRTU / {connection}"
        self._choice(MEMBER_DIALOG, label + "连接", prefix, search=connection, aria=True)
        self._choice(MEMBER_DIALOG, label, f"{prefix} / {tag}", search=tag, aria=True)

    def _create_member(self, connection, measurement_tag, setpoint_tag):
        self._click((By.CSS_SELECTOR, '.agc-group-modal button[aria-label="添加成员"]'), "打开 AGC 成员配置")
        self._field_value(MEMBER_DIALOG, "member_name", connection)
        self._field_value(MEMBER_DIALOG, "capacity_kw_decimal", 200)
        self._field_value(MEMBER_DIALOG, "max_kw_decimal", 200)
        self._bind_member_point("有功测量点", connection, measurement_tag)
        self._bind_member_point("有功设定点", connection, setpoint_tag)
        self._bind_member_point("通信状态点（可选）", connection, "__communication_state")
        self._click(self._button("确认成员", MEMBER_DIALOG), f"确认 AGC 成员 {connection}")
        card = (By.XPATH, GROUP_DIALOG + "//article[@aria-label=" + xpath_text(f"成员 {connection}") + "]")
        draft = self._until(lambda _: self._visible(card), f"未找到 AGC 成员草稿 {connection}")
        for tag in (measurement_tag, setpoint_tag, "__communication_state"):
            if f"ModbusRTU / {connection} / {tag}" not in draft.text:
                raise AssertionError(f"AGC 成员 {connection} 草稿缺少正确的 {tag} 路由")

    def create_group(self, group_name, connections, measurement_tag, setpoint_tag):
        connections = tuple(connections)
        if (not group_name.startswith("e2e-agc-") or len(connections) != 3 or len(set(connections)) != 3
                or any(not name.startswith("e2e-agc-") for name in connections)):
            raise ValueError("重分配用例要求专用命名的 AGC 控制组和三个独立 RTU 连接")
        self.assert_empty()
        self._click(self._button("+ 新增控制组", GROUP_LIST), "新增专用 AGC 控制组")
        self._field_value(GROUP_DIALOG, "group_name", group_name)
        self._choice(GROUP_DIALOG, "control_mode", "周期直分配")
        self._field_value(GROUP_DIALOG, "calculation_execution_period_seconds", 1)
        self._field_value(GROUP_DIALOG, "command_control_period_seconds", 4)
        self._choice(GROUP_DIALOG, "p_cmd_mode", "绝对值")
        tab = (By.XPATH, GROUP_DIALOG + "//*[@role='tab'][starts-with(normalize-space(.),'成员设备')]")
        self._click(tab, "切换到 AGC 成员设备页签")
        for connection in connections:
            self._create_member(connection, measurement_tag, setpoint_tag)
        self._click(self._button("保存控制组并建立路由", GROUP_DIALOG), "保存专用 AGC 控制组及成员路由")
        self._until(lambda _: not self._visible((By.XPATH, GROUP_DIALOG)), "AGC 配置或成员路由保存未完成")
        item = self._until(lambda _: self._group_item(group_name), "未找到新建 AGC 控制组")
        match = re.search(r"conn_id=(\d+)\s*\|", item.text)
        if not match or int(match[1]) <= 0:
            raise RuntimeError("新建 AGC 控制组缺少有效连接身份")
        self.group_name, self.conn_id, self.members = group_name, int(match[1]), connections
        self.desktop.metadata["AGC专用控制组"] = dict(group_name=group_name, conn_id=self.conn_id, members=list(connections))
        return self.conn_id

    def _open_owned_group(self):
        self._require_group()
        self._control_view("控制策略")
        item = self._until(lambda _: self._group_item(self.group_name), "未找到本例创建的 AGC 控制组")
        if f"conn_id={self.conn_id} |" not in item.text:
            raise RuntimeError("AGC 控制组连接身份已变化")
        item.click()
        self._until(lambda _: self._overview(), "AGC 总览未切换至本例控制组")

    def _overview(self):
        overview = self._visible(OVERVIEW)
        if overview and overview.find_element(By.CSS_SELECTOR, ".agc-runtime-heading h4").text == self.group_name:
            return overview
        return None

    def _running(self, overview):
        heading = overview.find_element(By.CSS_SELECTOR, ".agc-runtime-heading").text
        return all(label in heading for label in ("运行中", "功能投入", "远方"))

    def start_group(self):
        self._open_owned_group()
        self._click(self._button("启动控制组", OVERVIEW_XPATH), "启动本例 AGC 控制组")

        def running(_):
            overview = self._overview()
            return overview and self._running(overview)

        self._until(running, "AGC 未进入运行中、功能投入、远方状态")
        context = self._overview().find_element(By.CSS_SELECTOR, ".agc-runtime-context").text
        if not all(label in context for label in ("周期直分配", "平均分配", "绝对值")):
            raise AssertionError(f"AGC 实际控制方式与测试配置不一致：{context}")

    def _expand_members(self):
        for member in self.members:
            locator = (By.XPATH, OVERVIEW_XPATH + "//button[@aria-label=" + xpath_text(f"展开成员 {member}") + "]")
            if self._visible(locator):
                self._click(locator, f"展开 {member} 的 AGC 点值时间与质量")

    def _member_points(self, table, row, values):
        key = row.get_attribute("data-row-key")
        detail = table.find_element(By.XPATH, ".//tr[@data-row-key=" + xpath_text(key)
                                    + "]/following-sibling::tr[1][contains(@class,'ant-table-expanded-row')]")
        points = {}
        for label, kind in (("通信状态点", "communication"), ("已发布设定点", "setpoint")):
            block = unique_match(detail.find_elements(By.XPATH, ".//div[contains(@class,'ant-descriptions')]"
                                 "[./div[contains(@class,'ant-descriptions-header')]//div[normalize-space(.)="
                                 + xpath_text(label) + "]]"), label)
            points[f"{kind}_time"] = self._description(block, "点值时间")
            points[f"{kind}_quality"] = self._description(block, "质量")
        communication = row.find_element(By.CSS_SELECTOR, ".ant-tag").text.strip()
        setpoint = values["已发布设定"]
        points.update(communication=communication, setpoint_kw=setpoint, measurement_status=values["测量状态"])
        return points

    def _read_stage(self):
        overview = self._overview()
        if not overview:
            return None
        item = self._group_item(self.group_name)
        if not item or f"conn_id={self.conn_id} |" not in item.text:
            raise RuntimeError("AGC 控制组连接身份已变化")
        total = overview.find_element(By.CSS_SELECTOR, 'article[aria-label="总有功目标"]')
        table = overview.find_element(By.CSS_SELECTOR, ".agc-runtime-members")
        members = {}
        for row in table.find_elements(By.CSS_SELECTOR, ".ant-table-tbody > tr[data-row-key]"):
            name = row.find_element(By.CSS_SELECTOR, "strong").text.strip()
            capacity = row.find_element(By.CSS_SELECTOR, ".agc-runtime-member-caption").text
            if name in members or not re.fullmatch(r"可控\s*·\s*容量\s+200(?:\.0+)?\s*kW", capacity):
                raise AssertionError(f"AGC 成员重复或不是 200 kW 可控成员：{name} {capacity}")
            members[name] = self._member_points(table, row, self._row_values(table, row))
        if set(members) != set(self.members):
            raise AssertionError(f"AGC 成员名单与本例配置不一致：{list(members)}")
        return dict(running=self._running(overview),
                    total_kw=total.find_element(By.CSS_SELECTOR, ".agc-runtime-power-value").text,
                    total_status=total.text.splitlines()[-1], members=members)

    @staticmethod
    def _point_time(value):
        value = _timestamp(value).replace("/", "-").replace("年", "-").replace("月", "-").replace("日", " ")
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f" if "." in value else "%Y-%m-%d %H:%M:%S")

    def _matches_stage(self, snapshot, expected_setpoints, total_kw, offline_member, after):
        if not snapshot["running"]:
            return False
        if total_kw is not None and (snapshot["total_status"] != "新鲜 · 有效"
                                     or parse_decimal(snapshot["total_kw"]) != total_kw):
            return False
        for name, member in snapshot["members"].items():
            offline = name == offline_member
            previous = after["members"][name] if after else None
            expected_status = "通信异常" if offline else "通信正常"
            if member["communication"] != expected_status or member["communication_quality"] != "有效":
                return False
            communication_time = self._point_time(member["communication_time"])
            if previous and member["communication"] != previous["communication"]:
                if communication_time <= self._point_time(previous["communication_time"]):
                    return False
            if offline:
                if previous and (parse_decimal(member["setpoint_kw"]) != parse_decimal(previous["setpoint_kw"])
                                 or self._point_time(member["setpoint_time"]) > communication_time):
                    return False
                continue
            if member["measurement_status"] != "新鲜 · 有效":
                return False
            if name in expected_setpoints:
                if (member["setpoint_quality"] != "有效" or "（增量）" in member["setpoint_kw"]
                        or parse_decimal(member["setpoint_kw"]) != expected_setpoints[name]):
                    return False
                setpoint_time = self._point_time(member["setpoint_time"])
                if previous and previous["setpoint_quality"] == "有效":
                    if setpoint_time <= self._point_time(previous["setpoint_time"]):
                        return False
        return True

    def wait_stage(self, expected_setpoints, *, total_kw=300, offline_member=None, after=None, timeout=None):
        expected_online = set(self.members) - ({offline_member} if offline_member is not None else set())
        if ((offline_member is not None and offline_member not in self.members)
                or (total_kw is None and (offline_member is not None or expected_setpoints))
                or (total_kw is not None and set(expected_setpoints) != expected_online)):
            raise ValueError("AGC 阶段期望必须完整覆盖在线成员")
        self._open_owned_group()
        self._expand_members()
        with self.desktop.step("等待 AGC 通信状态与重新分配", expected=dict(
                total_kw=total_kw, setpoints=expected_setpoints, offline_member=offline_member)):
            entry = self.desktop.steps[-1]
            last = None

            def observed(_):
                nonlocal last
                snapshot = self._read_stage()
                if not snapshot:
                    return False
                last = snapshot
                entry["实际"] = str(last)
                try:
                    return snapshot if self._matches_stage(snapshot, expected_setpoints, total_kw, offline_member, after) else False
                except ValueError:
                    # 等待真实点值与时间出现，超时保留最后实际观察。
                    return False

            try:
                return self._until(observed, "AGC 期望分配未出现", timeout)
            except TimeoutException as error:
                raise AssertionError(f"AGC 通信或重新分配与预期不符，最后观察：{last}") from error
