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
    case = discover_cases(tmp_path)[0]
    assert case.group == "未分组"
    assert case.flow == []


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
    """完整用例清单保留分组及组内顺序，并提供运行前可读的流程。"""
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
        "tests/test_wrong_password.py::test_wrong_password_is_not_remembered_or_auto_used_after_reopen",
        "tests/test_modbus_rtu.py::test_rtu_configuration_saved_on_both_pages",
        "tests/test_modbus_rtu.py::test_rtu_initial_acquisition_matches_simulator",
        "tests/test_modbus_rtu.py::test_rtu_source_change_updates_value_and_time",
        "tests/test_modbus_rtu.py::test_rtu_scale_and_offset_match_fixed_expectations",
        "tests/test_modbus_rtu.py::test_rtu_two_stations_do_not_cross_values",
        "tests/test_huawei_fleet.py::test_huawei_six_buses_21_devices_match",
        "tests/test_modbus_rtu_104_quality.py::test_rtu_quality_reaches_iec104_with_unchanged_value",
        "tests/test_agc_direction_blocks.py::test_agc_upper_limit_sets_increase_block",
        "tests/test_agc_direction_blocks.py::test_agc_lower_target_clears_increase_block",
        "tests/test_agc_direction_blocks.py::test_agc_lower_limit_sets_decrease_block",
        "tests/test_agc_direction_blocks.py::test_agc_higher_target_clears_decrease_block",
        "tests/test_agc_direction_blocks.py::test_agc_increase_block_does_not_wait_for_measured_limit",
        "tests/test_agc_rebalance.py::test_agc_redistributes_when_rtu_member_loses_communication",
        "tests/test_agc_power_range.py::test_agc_station_power_range_reaches_iec104",
        "tests/test_measurement_expiry.py::test_agc_member_measurement_expiry",
        "tests/test_avc_control_periods.py::test_avc_voltage_deviation_recalculates_without_new_input",
        "tests/test_avc_control_periods.py::test_avc_burst_respects_command_interval_and_uses_latest_target",
        "tests/test_avc_control_periods.py::test_avc_stopped_group_applies_new_periods_after_restart",
        "tests/test_avc_reactive_headroom.py::test_avc_station_reactive_headroom_reaches_iec104",
        "tests/test_measurement_expiry.py::test_avc_voltage_measurement_expiry",
    ]
    assert Counter(case.group for case in cases) == {
        "基础登录": 4, "密码记忆与自动登录": 8, "Modbus RTU": 7, "AGC 控制": 8, "AVC 控制": 5,
    }
    for case in cases:
        assert case.flow, f"用例缺少测试流程：{case.id}"
        assert all(step["action"].strip() and step["expected"].strip() for step in case.flow), case.id
