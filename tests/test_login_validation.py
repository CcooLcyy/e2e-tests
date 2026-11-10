"""登录页外部行为用例，不需要平台账号或连接下位机。"""

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from testbench.login_page import PlatformLoginPage

pytestmark = pytest.mark.case_group("基础登录")


@pytest.mark.case_flow(
    ("打开上位机登录页", "账号和密码均为空"),
    ("直接点击登录", "同时显示「请输入平台账号」和「请输入密码」"),
    ("检查当前页面", "仍停留在登录页"),
)
def test_empty_login_reports_required_fields(desktop):
    """空账号密码登录时明确提示必填项。"""
    mark = PlatformLoginPage.target
    username_target = mark(PlatformLoginPage.USERNAME, "检查空账号输入框")
    password_target = mark(PlatformLoginPage.PASSWORD, "检查空密码输入框")
    submit_target = mark(PlatformLoginPage.SUBMIT, "提交空账号密码")
    username_error = mark((By.XPATH, '//div[contains(@class,"ant-form-item-explain-error") and normalize-space(.)="请输入平台账号"]'), "检查账号必填提示")
    password_error = mark((By.XPATH, '//div[contains(@class,"ant-form-item-explain-error") and normalize-space(.)="请输入密码"]'), "检查密码必填提示")
    with desktop.step("等待登录页并确认账号密码为空"):
        with desktop.step("等待登录页账号输入框可见", targets=username_target):
            username = desktop.wait.until(EC.visibility_of_element_located(
                (By.CSS_SELECTOR, 'input[placeholder="平台账号"]')))
        password = desktop.driver.find_element(By.CSS_SELECTOR, 'input[placeholder="请输入密码"]')
        desktop.check("账号初始值", username.get_attribute("value"), "", targets=username_target)
        desktop.check("密码初始值", password.get_attribute("value"), "", targets=password_target)

    with desktop.step("提交空表单并等待前端必填提示"):
        with desktop.step("等待空表单登录按钮可点击", targets=submit_target):
            button = desktop.wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, '.platform-login-form-panel button[type="submit"]')))
        with desktop.step("点击提交空账号密码", targets=submit_target):
            button.click()
        def visible_messages(driver):
            messages = [item.text.strip() for item in driver.find_elements(
                By.CSS_SELECTOR, '.ant-form-item-explain-error') if item.is_displayed()]
            return messages if {"请输入平台账号", "请输入密码"}.issubset(messages) else False

        with desktop.step("等待账号和密码必填提示同时显示", targets=username_error + password_error):
            messages = desktop.wait.until(visible_messages)
        desktop.check("账号必填提示", "请输入平台账号" in messages, True, targets=username_error)
        desktop.check("密码必填提示", "请输入密码" in messages, True, targets=password_error)
        desktop.capture("empty-login-validation",
            description="提交空表单后的账号和密码必填提示",
            expected="同时显示“请输入平台账号”和“请输入密码”",
            actual="已等待两项必填提示出现，并逐项验证提示文本", targets=username_error + password_error)

    with desktop.step("确认仍停留在登录页"):
        desktop.check("登录页可见", desktop.driver.find_element(
            By.CSS_SELECTOR, 'main.platform-login-screen').is_displayed(), True,
            targets=mark(PlatformLoginPage.SCREEN, "检查仍停留在登录页"))
