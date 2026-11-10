"""使用本机测试账号验证真实平台登录及工作台入口。"""

import json
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from testbench.case_plan import (
    DESKTOP_FINISH, DESKTOP_START, Step, case_plan, desktop_action, desktop_check, run_case_plan,
)
from testbench.login_page import PlatformLoginPage
from testbench.login_plan import submit_steps
from testbench.workspace import local_config

pytestmark = pytest.mark.case_group("基础登录")
PAGE = PlatformLoginPage
LOGOUT = (By.XPATH, '//header//button[normalize-space(.)="退出"]')
USERNAME_TARGET = PAGE.target(PAGE.USERNAME, "输入并检查登录账号")
PASSWORD_TARGET = PAGE.target(PAGE.PASSWORD, "输入密码，保持掩码")
SUBMIT_TARGET = PAGE.target(PAGE.SUBMIT, "点击登录")
LOGOUT_TARGET = PAGE.target(LOGOUT, "检查工作台退出入口")
BADGE_TARGET = PAGE.target(PAGE.BADGE_ANNOTATION, "检查工作台账号信息")


def _read_account(context):
    account_path = local_config("login-account.json")
    if not account_path.is_file():
        raise RuntimeError("请先在 local/login-account.json 配置测试账号的 username 和 password")
    account = json.loads(account_path.read_text(encoding="utf-8"))
    username, password = account.get("username"), account.get("password")
    if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
        raise RuntimeError("本机测试账号的 username 和 password 必须是非空字符串")
    context.username, context.password = username, password


def _enter_password(context):
    with context.desktop.step("填写正确密码", targets=PASSWORD_TARGET):
        try:
            context.password_input.send_keys(context.password)
        except WebDriverException:
            # 屏蔽驱动异常中可能附带的密码参数。
            raise RuntimeError("密码输入失败，请检查本次测试驱动和窗口状态") from None


def _capture_workspace(context):
    context.desktop.capture(
        "successful-login-workspace",
        description="正确账号登录后显示的工作台及退出入口",
        expected="登录页消失，工作台显示账号信息和退出入口",
        actual="已等待登录页消失，并验证退出入口可见、工作台账号信息非空",
        targets=BADGE_TARGET + LOGOUT_TARGET,
    )


@case_plan(
    DESKTOP_START,
    desktop_action("account.read", "读取本机测试账号", "账号和密码均非空", _read_account),
    desktop_action("username.wait", "等待平台账号输入框可点击", "账号输入框可操作",
                   lambda c: c.desktop.wait.until(EC.element_to_be_clickable(PAGE.USERNAME)),
                   targets=USERNAME_TARGET, save_as="username_input"),
    desktop_action("username.clear", "清空平台账号输入框", "清除原账号值",
                   lambda c: c.username_input.clear(), targets=USERNAME_TARGET),
    desktop_action("username.fill", "填写正确平台账号", "向账号框输入测试账号",
                   lambda c: c.username_input.send_keys(c.username), targets=USERNAME_TARGET),
    desktop_action("password.wait", "等待密码输入框可点击", "密码输入框可操作",
                   lambda c: c.desktop.wait.until(EC.element_to_be_clickable(PAGE.PASSWORD)),
                   targets=PASSWORD_TARGET, save_as="password_input"),
    desktop_action("password.clear", "清空密码输入框", "清除原密码值",
                   lambda c: c.password_input.clear(), targets=PASSWORD_TARGET),
    Step("password.fill", "填写正确密码", "向密码框输入测试密码", _enter_password),
    desktop_check("username.check", "登录账号", "账号框显示测试账号",
                  lambda c: c.username_input.get_attribute("value"), lambda c: c.username, targets=USERNAME_TARGET),
    *submit_steps("submit", click_name="点击登录按钮", targets=SUBMIT_TARGET),
    desktop_action("screen.hidden", "等待登录页消失", "登录页不可见",
                   lambda c: c.desktop.wait.until(EC.invisibility_of_element_located(PAGE.SCREEN)), targets=LOGOUT_TARGET),
    desktop_action("logout.wait", "等待工作台退出入口可见", "出现退出按钮",
                   lambda c: c.desktop.wait.until(EC.visibility_of_element_located(LOGOUT)),
                   targets=LOGOUT_TARGET, save_as="logout"),
    desktop_check("logout.check", "工作台账号退出入口可见", "退出按钮可见",
                  lambda c: c.logout.is_displayed(), True, targets=LOGOUT_TARGET),
    desktop_action("badge.wait", "等待工作台账号信息可见", "账号信息可见",
                   lambda c: c.desktop.wait.until(EC.visibility_of_element_located(PAGE.BADGE)),
                   targets=BADGE_TARGET, save_as="badge"),
    desktop_check("badge.check", "工作台显示已登录账号", "账号信息非空",
                  lambda c: bool(c.badge.text.strip()), True, targets=BADGE_TARGET),
    Step("workspace.capture", "保存工作台截图", "记录账号信息和退出入口", _capture_workspace),
    DESKTOP_FINISH,
)
def test_valid_account_enters_workspace(request, desktop):
    """正确账号密码登录成功并进入配置工作台。"""
    run_case_plan(request, SimpleNamespace(desktop=desktop))
