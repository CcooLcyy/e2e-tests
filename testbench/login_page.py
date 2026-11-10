"""平台登录页的外部界面动作，密码只参与内存比较。"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException


@dataclass(frozen=True)
class LoginAccount:
    username: str
    password: str = field(repr=False)


def load_login_account(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError()
        username, password = value.get("username"), value.get("password")
        if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
            raise ValueError()
        return LoginAccount(username.strip().lower(), password)
    except (OSError, UnicodeError, ValueError):
        raise RuntimeError("请在 local/login-account.json 配置专用测试账号，username 和 password 必须为非空字符串") from None


def require_fresh_login_environment(task_path):
    if task_path:
        task = json.loads(Path(task_path).read_text(encoding="utf-8"))
        if task["environment"]["mode"] != "fresh":
            raise RuntimeError("登录记忆用例仅支持全新独立副本，请先在测试环境切换运行模式")


class PlatformLoginPage:
    SCREEN = (By.CSS_SELECTOR, "main.platform-login-screen")
    USERNAME = (By.CSS_SELECTOR, 'input[placeholder="平台账号"]')
    PASSWORD = (By.CSS_SELECTOR, 'input[placeholder="请输入密码"]')
    SUBMIT = (By.CSS_SELECTOR, '.platform-login-form-panel button[type="submit"]')
    LOGOUT = (By.XPATH, '//header//button[translate(normalize-space(.), " ", "")="退出"]')
    BADGE = (By.CSS_SELECTOR, "header .ant-badge-status-text")
    # 标注只框选退出按钮所属账号组；原业务读取定位保留，避免修改既有验收行为。
    BADGE_ANNOTATION = (By.XPATH, '//header//button[translate(normalize-space(.), " ", "")="退出"]'
        '/ancestor::*[contains(concat(" ", normalize-space(@class), " "), " ant-space ")][1]'
        '//span[contains(concat(" ", normalize-space(@class), " "), " ant-badge-status-text ")]')
    LABELS = {"remember_password": "记住密码", "auto_login": "下次自动登录"}

    def __init__(self, desktop):
        self.desktop = desktop

    @property
    def driver(self):
        return self.desktop.driver

    def username_input(self):
        return self.driver.find_element(*self.USERNAME)

    def password_input(self):
        return self.driver.find_element(*self.PASSWORD)

    @staticmethod
    def target(locator, label):
        """公共操作只声明定位与含义，截图时由框架读取当前可见区域。"""
        return [{"locator": locator, "label": label}]

    @classmethod
    def option_locator(cls, option):
        return (By.XPATH, f'//label[.//span[normalize-space(.)="{cls.LABELS[option]}"]]')

    def option_label(self, option):
        return self.driver.find_element(*self.option_locator(option))

    def selected(self, option):
        return self.option_label(option).find_element(By.CSS_SELECTOR, 'input[type="checkbox"]').is_selected()

    def wait_ready(self):
        with self.desktop.step("等待登录页可见", targets=self.target(self.SCREEN, "检查登录页面")):
            self.desktop.wait.until(EC.visibility_of_element_located(self.SCREEN))
        with self.desktop.step("等待登录表单初始化或退出处理完成",
                               targets=self.target(self.SUBMIT, "等待登录表单就绪")):
            self.desktop.wait.until(self._ready, message="登录表单未在规定时间内完成初始化或退出处理")

    def _ready(self, driver):
        try:
            return (driver.find_element(*self.SUBMIT).is_enabled()
                    and "".join(driver.find_element(*self.SUBMIT).text.split()) == "登录"
                    and driver.find_element(*self.USERNAME).is_enabled())
        except StaleElementReferenceException:
            # 主动退出会重建整个页面，下一次轮询重新定位元素。
            return False

    @staticmethod
    def replace_text(element, value):
        # React 受控输入使用真实键盘删除，使状态随输入事件同步更新。
        element.click()
        element.send_keys(Keys.CONTROL, "a", Keys.NULL, Keys.BACKSPACE)
        element.send_keys(value, Keys.TAB)

    def endpoint(self):
        self.wait_ready()
        # 连接表单设置了 forceRender，可读取其当前值，不修改连接地址。
        return self.driver.find_element(By.CSS_SELECTOR, 'input[placeholder="https://platform.example:50000"]').get_attribute("value").strip().rstrip("/")

    def set_option(self, option, checked):
        self.wait_ready()
        if self.selected(option) != checked:
            with self.desktop.step(f"{'勾选' if checked else '取消'}{self.LABELS[option]}",
                                   targets=self.target(self.option_locator(option), f"设置{self.LABELS[option]}")):
                self.option_label(option).click()
        with self.desktop.step(f"等待{self.LABELS[option]}状态同步",
                               targets=self.target(self.option_locator(option), f"检查{self.LABELS[option]}状态")):
            self.desktop.wait.until(lambda _: self.selected(option) == checked)
        self.wait_ready()

    def check_options(self, remember, auto):
        self.desktop.check("记住密码勾选状态", self.selected("remember_password"), remember,
                           targets=self.target(self.option_locator("remember_password"), "检查记住密码状态"))
        self.desktop.check("下次自动登录勾选状态", self.selected("auto_login"), auto,
                           targets=self.target(self.option_locator("auto_login"), "检查自动登录状态"))

    def configure(self, remember=False, auto=False):
        with self.desktop.step("设置记住密码和自动登录选项"):
            self.set_option("auto_login", False)
            self.set_option("remember_password", remember)
            if auto:
                self.set_option("auto_login", True)
            self.check_options(remember or auto, auto)

    def fill_account(self, account):
        with self.desktop.step("填写专用测试账号密码"):
            self.wait_ready()
            username = self.username_input()
            with self.desktop.step("输入专用测试账号", targets=self.target(self.USERNAME, "输入测试账号")):
                self.replace_text(username, account.username)
            with self.desktop.step("等待账号输入同步", targets=self.target(self.USERNAME, "检查账号输入同步")):
                self.desktop.wait.until(lambda _: self.username_input().get_attribute("value") == account.username)
            password = self.password_input()
            with self.desktop.step("输入测试密码", targets=self.target(self.PASSWORD, "输入密码，保持掩码")):
                self._replace_password(password, account.password)
            self.desktop.check("登录账号", self.username_input().get_attribute("value"), account.username,
                               targets=self.target(self.USERNAME, "检查登录账号"))
            self.check_password(account.password)

    def enter_test_password(self, password):
        self._replace_password(self.password_input(), password)

    def _replace_password(self, element, password):
        try:
            self.replace_text(element, password)
        except WebDriverException:
            # 必须在步骤捕获异常前清理驱动载荷，避免密码进入截图说明。
            raise RuntimeError("密码输入失败，请检查本次测试驱动和窗口状态") from None

    def check_password(self, expected):
        actual_matches = self.password_input().get_attribute("value") == expected
        self.desktop.check("密码与测试密码匹配" if expected else "密码为空", actual_matches, True,
                           targets=self.target(self.PASSWORD, "检查密码状态，保持掩码"))

    def submit(self):
        with self.desktop.step("等待登录按钮可点击", targets=self.target(self.SUBMIT, "等待登录按钮可点击")):
            button = self.desktop.wait.until(EC.element_to_be_clickable(self.SUBMIT))
        with self.desktop.step("点击登录按钮", targets=self.target(self.SUBMIT, "点击登录")):
            button.click()

    def workspace(self, expected_badge=None):
        with self.desktop.step("验证进入工作台及账号显示"):
            with self.desktop.step("等待登录页消失并进入工作台", targets=self.target(self.LOGOUT, "检查工作台入口")):
                self.desktop.wait.until(self._workspace_visible, message="登录后未在规定时间内进入工作台")
            with self.desktop.step("等待工作台退出入口可见", targets=self.target(self.LOGOUT, "检查退出入口")):
                self.desktop.wait.until(EC.visibility_of_element_located(self.LOGOUT))
            with self.desktop.step("等待并读取工作台账号信息", targets=self.target(self.BADGE_ANNOTATION, "读取工作台账号信息")):
                badge = self.desktop.wait.until(EC.visibility_of_element_located(self.BADGE)).text.strip()
            self.desktop.check("工作台账号信息非空", bool(badge), True,
                               targets=self.target(self.BADGE_ANNOTATION, "检查工作台账号信息"))
            if expected_badge is not None:
                self.desktop.check("重开前后工作台账号一致", badge, expected_badge,
                                   targets=self.target(self.BADGE_ANNOTATION, "检查重开前后账号一致"))
            return badge

    def _workspace_visible(self, driver):
        try:
            for error in driver.find_elements(By.CSS_SELECTOR, ".platform-login-form-panel .ant-alert-error"):
                if error.is_displayed():
                    raise AssertionError(f"平台登录未成功：{error.text.strip()}")
            return (not any(item.is_displayed() for item in driver.find_elements(*self.SCREEN))
                    and any(item.is_displayed() for item in driver.find_elements(*self.LOGOUT)))
        except StaleElementReferenceException:
            return False

    def login(self, account, remember=False, auto=False):
        self.fill_account(account)
        self.configure(remember, auto)
        with self.desktop.step("提交登录"):
            self.submit()
        return self.workspace()

    def form(self, account, remember, auto, filled):
        with self.desktop.step("验证登录页的账号、密码回填及选项"):
            self.wait_ready()
            expected_password = account.password if filled else ""
            with self.desktop.step("等待账号、密码回填和登录选项同步",
                                   targets=self.target(self.USERNAME, "检查账号回填")
                                   + self.target(self.PASSWORD, "检查密码回填，保持掩码")):
                self.wait_for_form_values(account.username, expected_password, remember, auto)
            self.desktop.check("回填账号", self.username_input().get_attribute("value"), account.username,
                               targets=self.target(self.USERNAME, "检查回填账号"))
            self.check_password(expected_password)
            self.desktop.check("密码保持掩码显示", self.password_input().get_attribute("type"), "password",
                               targets=self.target(self.PASSWORD, "检查密码掩码显示"))
            self.check_options(remember, auto)
            with self.desktop.step("持续确认仍等待手动登录", targets=self.target(self.SCREEN, "检查仍停留在登录页")):
                self.wait_signed_out()

    def wait_for_form_values(self, username, password, remember, auto):
        self.desktop.wait.until(lambda _: (
            self.username_input().get_attribute("value") == username
            and self.password_input().get_attribute("value") == password
            and self.selected("remember_password") == remember
            and self.selected("auto_login") == auto
        ), message="重开后的账号、密码回填或登录选项不符合预期")

    def wait_signed_out(self):
        deadline = time.monotonic() + 1

        def remains_signed_out(driver):
            if not driver.find_element(*self.SCREEN).is_displayed() or driver.find_elements(*self.LOGOUT):
                raise AssertionError("应等待手动登录，但程序进入了工作台")
            return time.monotonic() >= deadline

        self.desktop.wait.until(remains_signed_out)

    def logout(self):
        with self.desktop.step("主动退出平台账号并确认"):
            with self.desktop.step("等待工作台退出按钮可点击", targets=self.target(self.LOGOUT, "等待退出按钮可点击")):
                logout = self.desktop.wait.until(EC.element_to_be_clickable(self.LOGOUT))
            with self.desktop.step("点击工作台退出按钮", targets=self.target(self.LOGOUT, "点击退出")):
                logout.click()
            confirm = (By.XPATH, '//div[contains(@class,"ant-modal-confirm-btns")]//button[normalize-space(.)="退出登录"]')
            with self.desktop.step("等待退出确认按钮可点击", targets=self.target(confirm, "等待退出确认")):
                confirm_button = self.desktop.wait.until(EC.element_to_be_clickable(confirm))
            with self.desktop.step("确认退出登录", targets=self.target(confirm, "确认退出登录")):
                confirm_button.click()
            self.wait_ready()
