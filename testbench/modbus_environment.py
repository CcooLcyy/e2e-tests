"""按所选 RTU 用例校验公共测试入口和采集时限。"""
from .modbus_rtu_config import decimal_value


RTU_CONFIG_CASE_ID = "tests/test_modbus_rtu.py::test_rtu_configuration_saved_on_both_pages"
BASIC_CASE_IDS = frozenset({
    RTU_CONFIG_CASE_ID,
    "tests/test_modbus_rtu.py::test_rtu_initial_acquisition_matches_simulator",
    "tests/test_modbus_rtu.py::test_rtu_source_change_updates_value_and_time",
    "tests/test_modbus_rtu.py::test_rtu_scale_and_offset_match_fixed_expectations",
    "tests/test_modbus_rtu.py::test_rtu_two_stations_do_not_cross_values",
})
HUAWEI_CASE_ID = "tests/test_huawei_fleet.py::test_huawei_six_buses_21_devices_match"
RTU_104_QUALITY_CASE_ID = "tests/test_modbus_rtu_104_quality.py::test_rtu_quality_reaches_iec104_with_unchanged_value"
AGC_REBALANCE_CASE_ID = "tests/test_agc_rebalance.py::test_agc_redistributes_when_rtu_member_loses_communication"
RTU_OBSERVATION_CASE_IDS = BASIC_CASE_IDS | {HUAWEI_CASE_ID, RTU_104_QUALITY_CASE_ID}


def validate_selected_modbus_cases(environment, case_ids):
    """按旧版用例标识校验 RTU 前置条件。"""
    if RTU_OBSERVATION_CASE_IDS.intersection(case_ids):
        validate_modbus_observation(environment)


def validate_modbus_observation(environment):
    """任务启动前校验 RTU 观察入口及采集等待时间。"""
    # 延迟导入避免环境模型与构造器之间形成循环依赖。
    from .model import validate_slavesim_url
    try:
        validate_slavesim_url(environment.get("slavesim_url", ""), required=True)
    except ValueError as error:
        raise ValueError(f"请检查 测试环境→slaveSim 模拟器：{error}") from None
    try:
        timeout = decimal_value(environment.get("modbus_acquisition_timeout", 300))
        if not 5 <= timeout <= 300:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("请检查 测试环境→Modbus RTU：采集等待时限必须为5至300秒") from None
