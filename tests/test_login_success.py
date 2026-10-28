"""使用本机测试账号验证真实平台登录及工作台入口。"""

import json
from pathlib import Path

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

pytestmark = pytest.mark.case_group("基础登录")


def test_valid_account_enters_workspace(desktop):
    """正确账号密码登录成功并进入配置工作台。"""
    with desktop.step("读取本机测试账号"):
        account_path = Path(__file__).resolve().parents[1] / "local" / "login-account.json"
        if not account_path.is_file():
            raise RuntimeError("请先在 local/login-account.json 配置测试账号的 username 和 password")
        account = json.loads(account_path.read_text(encoding="utf-8"))
        username, password = account.get("username"), account.get("password")
        if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
            raise RuntimeError("本机测试账号的 username 和 password 必须是非空字符串")

    with desktop.step("在登录页填写正确账号密码"):
        username_input = desktop.wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'input[placeholder="平台账号"]')))
        username_input.clear()
        username_input.send_keys(username)
        password_input = desktop.wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'input[placeholder="请输入密码"]')))
        password_input.clear()
        password_input.send_keys(password)
        desktop.check("登录账号", username_input.get_attribute("value"), username)

    with desktop.step("提交登录并等待进入配置工作台"):
        desktop.wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, '.platform-login-form-panel button[type="submit"]'))).click()
        desktop.wait.until(EC.invisibility_of_element_located(
            (By.CSS_SELECTOR, 'main.platform-login-screen')))
        logout = desktop.wait.until(EC.visibility_of_element_located(
            (By.XPATH, '//header//button[normalize-space(.)="退出"]')))
        desktop.check("工作台账号退出入口可见", logout.is_displayed(), True)
        account_badge = desktop.wait.until(EC.visibility_of_element_located(
            (By.CSS_SELECTOR, 'header .ant-badge-status-text')))
        desktop.check("工作台显示已登录账号", bool(account_badge.text.strip()), True)
        desktop.capture("successful-login-workspace",
            description="正确账号登录后显示的工作台及退出入口",
            expected="登录页消失，工作台显示账号信息和退出入口",
            actual="已等待登录页消失，并验证退出入口可见、工作台账号信息非空")
