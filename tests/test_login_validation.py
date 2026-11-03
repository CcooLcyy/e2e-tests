"""登录页外部行为用例，不需要平台账号或连接下位机。"""

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

pytestmark = pytest.mark.case_group("基础登录")


def test_empty_login_reports_required_fields(desktop):
    """空账号密码登录时明确提示必填项。"""
    with desktop.step("等待登录页并确认账号密码为空"):
        with desktop.step("等待登录页账号输入框可见"):
            username = desktop.wait.until(EC.visibility_of_element_located(
                (By.CSS_SELECTOR, 'input[placeholder="平台账号"]')))
        password = desktop.driver.find_element(By.CSS_SELECTOR, 'input[placeholder="请输入密码"]')
        desktop.check("账号初始值", username.get_attribute("value"), "")
        desktop.check("密码初始值", password.get_attribute("value"), "")

    with desktop.step("提交空表单并等待前端必填提示"):
        with desktop.step("等待空表单登录按钮可点击"):
            button = desktop.wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, '.platform-login-form-panel button[type="submit"]')))
        with desktop.step("点击提交空账号密码"):
            button.click()
        def visible_messages(driver):
            messages = [item.text.strip() for item in driver.find_elements(
                By.CSS_SELECTOR, '.ant-form-item-explain-error') if item.is_displayed()]
            return messages if {"请输入平台账号", "请输入密码"}.issubset(messages) else False

        with desktop.step("等待账号和密码必填提示同时显示"):
            messages = desktop.wait.until(visible_messages)
        desktop.check("账号必填提示", "请输入平台账号" in messages, True)
        desktop.check("密码必填提示", "请输入密码" in messages, True)
        desktop.capture("empty-login-validation",
            description="提交空表单后的账号和密码必填提示",
            expected="同时显示“请输入平台账号”和“请输入密码”",
            actual="已等待两项必填提示出现，并逐项验证提示文本")

    with desktop.step("确认仍停留在登录页"):
        desktop.check("登录页可见", desktop.driver.find_element(
            By.CSS_SELECTOR, 'main.platform-login-screen').is_displayed(), True)
