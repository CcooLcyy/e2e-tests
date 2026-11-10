"""错误密码应显示明确的认证错误提示，保持独立验收标准。"""
import re
import secrets
import unicodedata

import pytest
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By

from testbench.login_page import LoginAccount

pytestmark = pytest.mark.case_group("基础登录")


def _is_explicit_password_error(message):
    normalized = "".join(char for char in message if not char.isspace()
                         and not unicodedata.category(char).startswith("P"))
    return re.fullmatch(
        r"(?:登录失败)?(?:(?:账号|帐号|账户|用户名)或)?密码(?:错误|不正确|有误|不匹配)"
        r"(?:请(?:重新输入(?:密码)?|重新登录|重试))?", normalized,
    ) is not None


@pytest.mark.case_flow(
    ("使用正确账号密码登录后退出", "有效账号能够进入工作台，退出后返回登录页"),
    ("输入正确账号及非空错误密码并提交", "显示明确的账号或密码错误提示，仍停留在登录页"),
    ("检查表单能否重试", "账号和密码可编辑，密码保持掩码，登录按钮可操作"),
)
def test_wrong_password_reports_explicit_credentials_error(login_memory):
    """正确账号输入错误密码时明确提示认证失败并允许重新输入。"""
    case = login_memory
    page, desktop = case.page, case.desktop
    error_targets = page.target((By.CSS_SELECTOR, ".platform-login-form-panel .ant-alert-error"), "检查认证错误提示")

    with desktop.step("前置：使用正确密码登录并退出，失败则尚未进入错误密码验证"):
        page.login(case.account)
        page.logout()
        page.wait_ready()

    # 随机错误密码有固定的有效长度，不通过空值或超长输入触发其他校验。
    candidate = secrets.token_urlsafe(18)
    if candidate == case.account.password:
        candidate = ("A" if candidate[0] != "A" else "B") + candidate[1:]
    wrong_account = LoginAccount(case.account.username, candidate)
    with desktop.step("准备确定不同于正确密码的非空错误密码"):
        desktop.check("错误密码与正确密码不同", wrong_account.password != case.account.password, True)
        desktop.check("错误密码长度", len(wrong_account.password), 24)

    page.fill_account(wrong_account)
    page.configure(remember=False, auto=False)
    with desktop.step("使用正确账号和错误密码提交一次登录"):
        page.submit()

    def visible_error(driver):
        try:
            if any(item.is_displayed() for item in driver.find_elements(*page.LOGOUT)):
                raise AssertionError("错误密码提交后进入了工作台")
            messages = [item.text.strip() for item in driver.find_elements(
                By.CSS_SELECTOR, ".platform-login-form-panel .ant-alert-error")
                if item.is_displayed() and item.text.strip()]
            return "\n".join(messages) if messages else False
        except StaleElementReferenceException:
            return False

    with desktop.step("等待错误密码的界面提示"):
        with desktop.step("等待错误密码认证提示显示", targets=error_targets):
            actual_message = desktop.wait.until(visible_error, message="错误密码提交后未显示登录错误提示")

    with desktop.step("验证拒绝登录后仍可重新输入账号密码"):
        desktop.check("仍停留在登录页", desktop.driver.find_element(*page.SCREEN).is_displayed(), True)
        desktop.check("没有进入工作台", any(item.is_displayed() for item in
                      desktop.driver.find_elements(*page.LOGOUT)), False)
        page.wait_ready()
        desktop.check("登录按钮可操作", desktop.driver.find_element(*page.SUBMIT).is_enabled(), True)
        for label, element in (("账号", page.username_input()), ("密码", page.password_input())):
            desktop.check(f"{label}输入框可编辑", element.is_enabled()
                          and not element.get_attribute("readonly"), True)
        desktop.check("密码保持掩码显示", page.password_input().get_attribute("type"), "password")

    # 报告记录实际提示，但避免意外回显的输入值进入文本报告。
    safe_message = actual_message.replace(case.account.password, "[已隐藏密码]")
    safe_message = safe_message.replace(wrong_account.password, "[已隐藏密码]")
    with desktop.step("验证错误提示明确说明账号或密码错误",
                      expected="账号或密码错误，请重新输入（或相同明确语义）", actual=safe_message, targets=error_targets):
        desktop.check("提示符合明确认证错误标准", _is_explicit_password_error(actual_message), True, targets=error_targets)
    desktop.capture("wrong-password-message",
        description="错误密码被拒绝后的认证提示与可重试登录表单",
        expected="显示明确的账号或密码错误提示，仍在登录页并允许重新输入，密码保持掩码",
        actual=f"已验证认证错误提示符合标准、未进入工作台、账号密码可编辑且密码保持掩码；实际提示：{safe_message}",
        targets=error_targets)
