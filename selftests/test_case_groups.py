"""分组元数据发现契约：兼容旧用例、校验声明且不改变执行顺序。"""
from collections import Counter
from pathlib import Path

import pytest

from testbench.catalog import discover_cases
from testbench.model import CaseInfo


def write_suite(root, source):
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_groups.py").write_text(source, encoding="utf-8")


def test_legacy_cases_default_to_ungrouped(tmp_path):
    """未声明分组的既有用例仍可发现，模型和发现结果都默认归入未分组。"""
    write_suite(tmp_path, 'def test_old():\n    """旧用例。"""\n    raise RuntimeError("发现不得执行")\n')
    assert CaseInfo("case", "旧用例").group == "未分组"
    assert discover_cases(tmp_path)[0].group == "未分组"


def test_module_group_function_override_and_parameterized_order(tmp_path):
    """函数分组覆盖文件默认，参数化每个实例带分组且保留原始发现顺序。"""
    write_suite(tmp_path, '''import pytest
pytestmark = pytest.mark.case_group(" 文件默认 ")
def test_first():
    raise RuntimeError("发现不得执行")
@pytest.mark.case_group("单项分组")
@pytest.mark.parametrize("value", [2, 1])
def test_second(value):
    raise RuntimeError("发现不得执行")
def test_last():
    raise RuntimeError("发现不得执行")
''')
    cases = discover_cases(tmp_path)
    assert [case.id for case in cases] == [
        "tests/test_groups.py::test_first", "tests/test_groups.py::test_last",
        "tests/test_groups.py::test_second[2]", "tests/test_groups.py::test_second[1]",
    ]
    assert [case.group for case in cases] == ["文件默认", "文件默认", "单项分组", "单项分组"]


@pytest.mark.parametrize("arguments", ["", '""', '"   "', "None", "12", '["分组"]',
                                       '"分组", "另一个"', 'name="分组"', '"分组", order=1'])
def test_invalid_group_declaration_fails_discovery(tmp_path, arguments):
    """分组声明的非法类型、空值及参数形态必须明确失败，不能当作未分组。"""
    write_suite(tmp_path, f'import pytest\n@pytest.mark.case_group({arguments})\n'
                         'def test_invalid():\n    pass\n')
    with pytest.raises(RuntimeError, match="分组"):
        discover_cases(tmp_path)


def test_current_cases_keep_ids_order_and_expected_group_counts():
    """真实十一项业务用例保留标识，按分组清单与组内顺序归并，分组三七一项。"""
    root = Path(__file__).resolve().parents[1]
    cases = discover_cases(root)
    assert [case.id for case in cases] == [
        "tests/test_login_success.py::test_valid_account_enters_workspace",
        "tests/test_login_validation.py::test_empty_login_reports_required_fields",
        "tests/test_wrong_password.py::test_wrong_password_reports_explicit_credentials_error",
        "tests/test_release_smoke.py::test_release_platform_settings_expand_and_collapse",
        "tests/test_session_preferences.py::test_no_remember_requires_password_after_reopen",
        "tests/test_session_preferences.py::test_remember_password_requires_manual_login_after_reopen",
        "tests/test_session_preferences.py::test_auto_login_enters_same_account_after_reopen",
        "tests/test_session_preferences.py::test_auto_login_and_remember_password_checkbox_linkage",
        "tests/test_session_preferences.py::test_forget_password_clears_saved_password_and_auto_login",
        "tests/test_session_preferences.py::test_explicit_logout_disables_auto_login_but_keeps_password",
        "tests/test_session_preferences.py::test_changing_account_does_not_reuse_saved_password",
    ]
    assert Counter(case.group for case in cases) == {
        "基础登录": 3, "密码记忆与自动登录": 7, "启动与界面": 1,
    }
