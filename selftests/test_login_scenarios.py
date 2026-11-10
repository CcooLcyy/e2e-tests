"""登录记忆用例的账号输入、密码报告和工作区边界。"""
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.keys import Keys

from testbench.login_page import load_login_account, PlatformLoginPage


def test_account_password_is_not_exposed_by_repr(tmp_path):
    """读取专用账号时规范账号，但不修改密码且不通过对象显示暴露密码。"""
    source = tmp_path / "account.json"
    source.write_text(json.dumps({"username": " TestUser ", "password": " secret "}), encoding="utf-8")
    account = load_login_account(source)
    assert account.username == "testuser"
    assert account.password == " secret "
    assert "secret" not in repr(account)


@pytest.mark.parametrize("data", [[], {}, {"username": " ", "password": "secret"},
                                  {"username": "test", "password": ""},
                                  {"username": 12, "password": "secret"}])
def test_invalid_account_has_sanitized_error(tmp_path, data):
    """账号配置异常明确报错，错误信息不包含配置中的密码。"""
    source = tmp_path / "account.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError) as caught:
        load_login_account(source)
    assert "secret" not in str(caught.value)


def test_corrupt_or_missing_account_has_actionable_error(tmp_path):
    """缺少账号或 JSON 损坏都提示修正本机配置，不输出配置原文。"""
    source = tmp_path / "account.json"
    for content in (None, '{"password":"secret"'):
        if content is not None:
            source.write_text(content, encoding="utf-8")
        with pytest.raises(RuntimeError, match="login-account.json") as caught:
            load_login_account(source)
        assert "secret" not in str(caught.value)


def test_fresh_desktop_ignores_fixed_workspace_without_losing_baseline(tmp_path, monkeypatch):
    """需要干净状态的场景不用全局固定工作区，但仍使用显式基线。"""
    source = Path(__file__).resolve().parents[1] / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("login_desktop_fixtures", source)
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    launch = MagicMock()
    launch.return_value.metadata = {}
    launch.return_value.cleanup_failure = None
    monkeypatch.setattr(fixtures, "ReleaseDesktop", launch)

    workspace = str(tmp_path / "fixed")
    baseline = str(tmp_path / "baseline")
    node = SimpleNamespace(nodeid="tests/test_login.py::test_login", name="test_login", obj=None)
    task = tmp_path / "task.json"
    task.write_text(json.dumps({
        "environment": {"mode": "workspace", "workspace_path": workspace,
                        "baseline_path": baseline, "close_app": True},
        "case_ids": [node.nodeid],
    }), encoding="utf-8")
    monkeypatch.setenv("TESTBENCH_TASK", str(task))
    options = {"--app-path": "release.exe", "--driver-path": "driver.exe",
               "--artifacts-dir": str(tmp_path), "--ui-timeout": "5"}
    request = SimpleNamespace(node=node, config=SimpleNamespace(getoption=options.__getitem__))

    with fixtures._desktop_session(request, fresh=True):
        isolated = launch.call_args.kwargs
        assert isolated["mode"] == "fresh"
        assert isolated["workspace_path"] == ""
        assert isolated["baseline_path"] == baseline
    with fixtures._desktop_session(request):
        shared = launch.call_args.kwargs
        assert shared["mode"] == "workspace"
        assert shared["workspace_path"] == workspace


def test_password_comparison_reports_only_boolean():
    """密码不匹配时报告只显示真假，避免将预期或实际密码写入事件与断言。"""
    checks = []

    @contextmanager
    def step(*args, **kwargs):
        yield

    desktop = SimpleNamespace(step=step, check=lambda *args, **kwargs: checks.append(args))
    page = PlatformLoginPage(desktop)
    page.password_input = lambda: SimpleNamespace(get_attribute=lambda name: "wrong-secret")
    page.check_password("expected-secret")
    assert checks == [("密码与测试密码匹配", False, True)]
    assert "secret" not in repr(checks)


def test_login_ready_accepts_ant_design_chinese_button_spacing():
    """真实 Release 的双字按钮显示为“登 录”，不能因此一直误判初始化未完成。"""
    button = SimpleNamespace(is_enabled=lambda: True, is_displayed=lambda: True, text="登 录")
    driver = SimpleNamespace(find_element=lambda *args: button)

    def until(condition, message=""):
        result = condition(driver)
        assert result, "登录表单应已就绪"
        return result

    @contextmanager
    def step(*args, **kwargs):
        yield

    page = PlatformLoginPage(SimpleNamespace(driver=driver, wait=SimpleNamespace(until=until), step=step))
    page.wait_ready()


def test_page_rebuild_retries_readiness_with_new_elements():
    """退出后的元素在读取属性时失效，应重新定位，不能将页面重建报为功能失败。"""
    page = PlatformLoginPage(None)
    rebuilding = SimpleNamespace(find_element=lambda *args: (_ for _ in ()).throw(StaleElementReferenceException()))
    assert page._ready(rebuilding) is False
    ready = SimpleNamespace(is_enabled=lambda: True, text="登 录")
    assert page._ready(SimpleNamespace(find_element=lambda *args: ready)) is True


def test_keyboard_replacement_updates_controlled_account_instead_of_appending():
    """模拟受控账号框，键盘全选删除后新账号不能接在旧账号后面。"""
    class ControlledInput:
        value = "old-account"

        def click(self):
            pass

        def send_keys(self, *keys):
            if Keys.BACKSPACE in keys:
                self.value = ""
            else:
                self.value += "".join(key for key in keys if key != Keys.TAB)

    element = ControlledInput()
    PlatformLoginPage.replace_text(element, "other-account")
    assert element.value == "other-account"


def test_password_typing_failure_does_not_expose_driver_payload(monkeypatch):
    """驱动输入异常可能携带原请求，必须隐藏异常链，避免密码进入 pytest 报告。"""
    @contextmanager
    def step(*args, **kwargs):
        yield

    desktop = SimpleNamespace(step=step, wait=SimpleNamespace(until=lambda *args: True))
    page = PlatformLoginPage(desktop)
    monkeypatch.setattr(page, "wait_ready", lambda: None)
    monkeypatch.setattr(page, "username_input", lambda: "账号框")
    monkeypatch.setattr(page, "password_input", lambda: "密码框")

    def replace(element, value):
        if element == "密码框":
            raise WebDriverException("secret-driver-payload")

    monkeypatch.setattr(page, "replace_text", replace)
    from testbench.login_page import LoginAccount
    with pytest.raises(RuntimeError, match="密码输入失败") as caught:
        page.fill_account(LoginAccount("test", "secret-driver-payload"))
    assert caught.value.__suppress_context__
    assert "secret-driver-payload" not in str(caught.value)


def test_login_error_is_reported_instead_of_waiting_for_workspace():
    """登录页已显示平台拒绝提示时立即保留该错误，不再等到工作台超时。"""
    error = SimpleNamespace(is_displayed=lambda: True, text="登录失败，请检查账号、密码及平台连接")
    driver = SimpleNamespace(find_elements=lambda *args: [error])
    page = PlatformLoginPage(None)
    with pytest.raises(AssertionError, match="平台登录未成功"):
        page._workspace_visible(driver)
