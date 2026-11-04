"""公共登录操作提供截图目标，标注内容不包含输入值，不连接真实窗口。"""
import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from selenium.webdriver.common.by import By

from testbench.login_page import LoginAccount, PlatformLoginPage


def recording_page():
    records = []

    @contextmanager
    def step(name, **kwargs):
        records.append((name, kwargs.get("targets", [])))
        yield

    def check(name, actual, expected, **kwargs):
        records.append((name, kwargs.get("targets", [])))
        assert actual == expected

    element = SimpleNamespace(text="测试账号", click=lambda: None)
    desktop = SimpleNamespace(step=step, check=check,
                              wait=SimpleNamespace(until=lambda *args, **kwargs: element))
    page = PlatformLoginPage(desktop)
    page.username_input = lambda: SimpleNamespace(get_attribute=lambda _: "testuser")
    page.password_input = lambda: SimpleNamespace(
        get_attribute=lambda key: "password" if key == "type" else "private-password")
    page.replace_text = lambda *args: None
    page.option_label = lambda _: element
    page.selected = lambda _: False
    return page, records


def target_for(records, name):
    targets = next(targets for step_name, targets in records if step_name == name)
    assert targets, f"{name} 应声明截图目标"
    for target in targets:
        assert target["locator"][0] in (By.CSS_SELECTOR, By.XPATH)
        assert target["label"]
    return targets


def test_shared_account_input_and_password_checks_declare_safe_targets():
    """公共填写和检查复用控件定位，标注只含操作说明，不含密码。"""
    page, records = recording_page()
    page.fill_account(LoginAccount("testuser", "private-password"))
    for name, locator in (("输入专用测试账号", page.USERNAME), ("等待账号输入同步", page.USERNAME),
                          ("输入测试密码", page.PASSWORD), ("密码与测试密码匹配", page.PASSWORD)):
        assert target_for(records, name)[0]["locator"] == locator
    assert "private-password" not in repr(records)


def test_shared_options_and_submit_have_reusable_targets():
    """选项动作与状态检查以及登录等待、点击均声明当前控件。"""
    page, records = recording_page()
    page.set_option("remember_password", True)
    page.check_options(False, False)
    page.submit()
    for name in ("勾选记住密码", "等待记住密码状态同步", "记住密码勾选状态", "下次自动登录勾选状态"):
        assert target_for(records, name)[0]["locator"][0] == By.XPATH
    for name in ("等待登录按钮可点击", "点击登录按钮"):
        assert target_for(records, name)[0]["locator"] == page.SUBMIT


def test_workspace_and_logout_mark_their_own_current_controls():
    """工作台账号、退出按钮及确认弹窗分别声明位置，不缓存前一页坐标。"""
    page, records = recording_page()
    assert page.workspace("测试账号") == "测试账号"
    page.logout()
    for name in ("等待并读取工作台账号信息", "工作台账号信息非空", "重开前后工作台账号一致"):
        assert target_for(records, name)[0]["locator"] == page.BADGE_ANNOTATION
    for name in ("等待工作台退出按钮可点击", "点击工作台退出按钮"):
        assert target_for(records, name)[0]["locator"] == page.LOGOUT
    assert "ant-modal-confirm-btns" in target_for(records, "确认退出登录")[0]["locator"][1]


def test_wrong_password_business_case_declares_error_target_without_changing_assertions():
    """特殊错误提示等待、语义检查及补充截图显式声明目标，继续使用既有认证断言。"""
    source = Path(__file__).resolve().parents[1] / "tests" / "test_wrong_password.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = {node.args[0].value: node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in ("step", "check", "capture") and node.args
             and isinstance(node.args[0], ast.Constant)}
    for name in ("等待错误密码认证提示显示", "验证错误提示明确说明账号或密码错误",
                 "提示符合明确认证错误标准", "wrong-password-message"):
        assert any(keyword.arg == "targets" for keyword in calls[name].keywords)
    assert "_is_explicit_password_error(actual_message)" in ast.unparse(tree)


@pytest.mark.parametrize("filename,names", [
    ("test_login_validation.py", ["等待登录页账号输入框可见", "点击提交空账号密码", "账号必填提示", "密码必填提示", "empty-login-validation"]),
    ("test_login_success.py", ["填写正确平台账号", "填写正确密码", "点击登录按钮", "工作台显示已登录账号", "successful-login-workspace"]),
])
def test_direct_login_cases_declare_targets_for_operations_and_business_evidence(filename, names):
    """未封装为公共操作的登录用例也只声明目标，原有动作与检查保持不变。"""
    source = Path(__file__).resolve().parents[1] / "tests" / filename
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = {node.args[0].value: node for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in ("step", "check", "capture") and node.args
             and isinstance(node.args[0], ast.Constant)}
    for name in names:
        assert any(keyword.arg == "targets" for keyword in calls[name].keywords)
