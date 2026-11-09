"""使用本机测试账号验证真实平台登录及工作台入口。"""

import json

import pytest
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from testbench.login_page import PlatformLoginPage
from testbench.workspace import local_config

pytestmark = pytest.mark.case_group("基础登录")


def test_valid_account_enters_workspace(desktop):
    """正确账号密码登录成功并进入配置工作台。"""
    mark = PlatformLoginPage.target
    username_target = mark(PlatformLoginPage.USERNAME, "输入并检查登录账号")
    password_target = mark(PlatformLoginPage.PASSWORD, "输入密码，保持掩码")
    submit_target = mark(PlatformLoginPage.SUBMIT, "点击登录")
    logout_target = mark((By.XPATH, '//header//button[normalize-space(.)="退出"]'), "检查工作台退出入口")
    badge_target = mark(PlatformLoginPage.BADGE_ANNOTATION, "检查工作台账号信息")
    with desktop.step("读取本机测试账号"):
        account_path = local_config("login-account.json")
        if not account_path.is_file():
            raise RuntimeError("请先在 local/login-account.json 配置测试账号的 username 和 password")
        account = json.loads(account_path.read_text(encoding="utf-8"))
        username, password = account.get("username"), account.get("password")
        if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
            raise RuntimeError("本机测试账号的 username 和 password 必须是非空字符串")

    with desktop.step("在登录页填写正确账号密码"):
        with desktop.step("等待平台账号输入框可点击", targets=username_target):
            username_input = desktop.wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, 'input[placeholder="平台账号"]')))
        with desktop.step("清空平台账号输入框", targets=username_target):
            username_input.clear()
        with desktop.step("填写正确平台账号", targets=username_target):
            username_input.send_keys(username)
        with desktop.step("等待密码输入框可点击", targets=password_target):
            password_input = desktop.wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, 'input[placeholder="请输入密码"]')))
        with desktop.step("清空密码输入框", targets=password_target):
            password_input.clear()
        with desktop.step("填写正确密码", targets=password_target):
            try:
                password_input.send_keys(password)
            except WebDriverException:
                # 步骤异常会进入报告，先屏蔽驱动可能附带的密码参数。
                raise RuntimeError("密码输入失败，请检查本次测试驱动和窗口状态") from None
        desktop.check("登录账号", username_input.get_attribute("value"), username, targets=username_target)

    with desktop.step("提交登录并等待进入配置工作台"):
        with desktop.step("等待登录按钮可点击", targets=submit_target):
            action_button = desktop.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, '.platform-login-form-panel button[type="submit"]')))
        with desktop.step("点击登录按钮", targets=submit_target):
            action_button.click()
        with desktop.step("等待登录页消失", targets=logout_target):
            desktop.wait.until(EC.invisibility_of_element_located(
                (By.CSS_SELECTOR, 'main.platform-login-screen')))
        with desktop.step("等待工作台退出入口可见", targets=logout_target):
            logout = desktop.wait.until(EC.visibility_of_element_located(
                (By.XPATH, '//header//button[normalize-space(.)="退出"]')))
        desktop.check("工作台账号退出入口可见", logout.is_displayed(), True, targets=logout_target)
        with desktop.step("等待工作台账号信息可见", targets=badge_target):
            account_badge = desktop.wait.until(EC.visibility_of_element_located(
                (By.CSS_SELECTOR, 'header .ant-badge-status-text')))
        desktop.check("工作台显示已登录账号", bool(account_badge.text.strip()), True, targets=badge_target)
        desktop.capture("successful-login-workspace",
            description="正确账号登录后显示的工作台及退出入口",
            expected="登录页消失，工作台显示账号信息和退出入口",
            actual="已等待登录页消失，并验证退出入口可见、工作台账号信息非空", targets=badge_target + logout_target)
