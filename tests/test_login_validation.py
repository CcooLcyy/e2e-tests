"""登录页外部行为用例，不需要平台账号或连接下位机。"""

from types import SimpleNamespace

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from testbench.case_plan import (
    DESKTOP_FINISH, DESKTOP_START, Step, case_plan, desktop_action, desktop_check, run_case_plan,
)
from testbench.login_page import PlatformLoginPage
from testbench.login_plan import submit_steps

pytestmark = pytest.mark.case_group("基础登录")
PAGE = PlatformLoginPage
USERNAME_TARGET = PAGE.target(PAGE.USERNAME, "检查空账号输入框")
PASSWORD_TARGET = PAGE.target(PAGE.PASSWORD, "检查空密码输入框")
SUBMIT_TARGET = PAGE.target(PAGE.SUBMIT, "提交空账号密码")
USERNAME_ERROR = PAGE.target((By.XPATH, '//div[contains(@class,"ant-form-item-explain-error") and normalize-space(.)="请输入平台账号"]'), "检查账号必填提示")
PASSWORD_ERROR = PAGE.target((By.XPATH, '//div[contains(@class,"ant-form-item-explain-error") and normalize-space(.)="请输入密码"]'), "检查密码必填提示")


def _visible_messages(driver):
    messages = [item.text.strip() for item in driver.find_elements(
        By.CSS_SELECTOR, ".ant-form-item-explain-error") if item.is_displayed()]
    return messages if {"请输入平台账号", "请输入密码"}.issubset(messages) else False


def _capture_validation(context):
    context.desktop.capture(
        "empty-login-validation",
        description="提交空表单后的账号和密码必填提示",
        expected="同时显示“请输入平台账号”和“请输入密码”",
        actual="已等待两项必填提示出现，并逐项验证提示文本",
        targets=USERNAME_ERROR + PASSWORD_ERROR,
    )


@case_plan(
    DESKTOP_START,
    desktop_action("username.wait", "等待登录页账号输入框可见", "登录页显示账号输入框",
                   lambda c: c.desktop.wait.until(EC.visibility_of_element_located(PAGE.USERNAME)),
                   targets=USERNAME_TARGET, save_as="username_input"),
    desktop_action("password.find", "定位登录页密码输入框", "存在密码输入框",
                   lambda c: c.desktop.driver.find_element(*PAGE.PASSWORD), targets=PASSWORD_TARGET, save_as="password_input"),
    desktop_check("username.empty", "账号初始值", "账号为空",
                  lambda c: c.username_input.get_attribute("value"), "", targets=USERNAME_TARGET),
    desktop_check("password.empty", "密码初始值", "密码为空",
                  lambda c: c.password_input.get_attribute("value"), "", targets=PASSWORD_TARGET),
    *submit_steps("submit", wait_name="等待空表单登录按钮可点击", click_name="点击提交空账号密码",
                  targets=SUBMIT_TARGET),
    desktop_action("errors.wait", "等待账号和密码必填提示同时显示", "两项必填提示同时可见",
                   lambda c: c.desktop.wait.until(_visible_messages),
                   targets=USERNAME_ERROR + PASSWORD_ERROR, save_as="messages"),
    desktop_check("username.error", "账号必填提示", "显示「请输入平台账号」",
                  lambda c: "请输入平台账号" in c.messages, True, targets=USERNAME_ERROR),
    desktop_check("password.error", "密码必填提示", "显示「请输入密码」",
                  lambda c: "请输入密码" in c.messages, True, targets=PASSWORD_ERROR),
    Step("validation.capture", "保存必填提示截图", "记录两项必填提示", _capture_validation),
    desktop_check("login.visible", "登录页可见", "仍停留在登录页",
                  lambda c: c.desktop.driver.find_element(*PAGE.SCREEN).is_displayed(), True,
                  targets=PAGE.target(PAGE.SCREEN, "检查仍停留在登录页")),
    DESKTOP_FINISH,
)
def test_empty_login_reports_required_fields(request, desktop):
    """空账号密码登录时明确提示必填项。"""
    run_case_plan(request, SimpleNamespace(desktop=desktop))
