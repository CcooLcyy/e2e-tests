"""错误密码用例的文案验收边界，不启动上位机或访问平台。"""
import importlib.util
from pathlib import Path

import pytest


source = Path(__file__).resolve().parents[1] / "case-suite" / "tests" / "test_wrong_password.py"
spec = importlib.util.spec_from_file_location("wrong_password_case", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("message", [
    "账号或密码错误，请重新输入。", "帐号或密码错误，请重试", "用户名或密码不正确",
    "密码错误", " 密码 不匹配，请重新输入密码！", "登录失败：账户或密码有误，请重新登录。",
])
def test_explicit_password_error_is_accepted(message):
    """明确的认证错误允许称呼、标点、空格和重试指引差异。"""
    assert module._is_explicit_password_error(message)


@pytest.mark.parametrize("message", [
    "", "登录失败", "登录失败，请检查账号、密码、账号启用状态及平台连接地址。",
    "平台连接失败，请稍后重试", "当前登录会话过多，请退出其他登录后重试",
    "登录尝试过于频繁，请一分钟后重试", "密码不能为空", "没有发现密码错误",
    "账号或密码错误，或账号已停用", "平台在线",
])
def test_generic_or_unrelated_error_is_rejected(message):
    """泛化提示、环境问题、空值校验与否定表达不能使错误密码用例通过。"""
    assert not module._is_explicit_password_error(message)
