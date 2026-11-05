"""从统一任务环境构造RTU参数，运行前按所选业务用例校验。"""
import logging

from .huawei_fleet import HuaweiFleetConfig
from .modbus_rtu_config import RtuEnvironment


LOGGER = logging.getLogger(__name__)
BASIC_CASE_IDS = frozenset({
    "tests/test_modbus_rtu.py::test_rtu_configuration_saved_on_both_pages",
    "tests/test_modbus_rtu.py::test_rtu_initial_acquisition_matches_simulator",
    "tests/test_modbus_rtu.py::test_rtu_source_change_updates_value_and_time",
    "tests/test_modbus_rtu.py::test_rtu_scale_and_offset_match_fixed_expectations",
    "tests/test_modbus_rtu.py::test_rtu_two_stations_do_not_cross_values",
})
HUAWEI_CASE_ID = "tests/test_huawei_fleet.py::test_huawei_six_buses_21_devices_match"


def _shared(environment):
    if not isinstance(environment, dict):
        raise ValueError("任务环境不是有效对象")
    return {"manager_address": environment.get("modbus_manager_address", ""),
            "baud_rate": environment.get("modbus_baud_rate", 9600),
            "poll_interval_ms": environment.get("modbus_poll_interval_ms", 1000),
            "acquisition_timeout": environment.get("modbus_acquisition_timeout", 30)}


def basic_config_from_environment(environment):
    """基础用例只读取任务环境快照，独立JSON不再参与执行。"""
    try:
        shared = _shared(environment)
        basic = environment.get("modbus_basic", {})
        if not isinstance(basic, dict):
            raise ValueError("基础采集参数必须为对象")
        # 共享参数只有一个来源，避免导入配置在子对象中悄悄覆盖界面输入。
        if set(basic) - {"device_name", "bus", "serial_device", "stations", "dedicated_test_resources",
                         "data_bits", "stop_bits", "parity"}:
            raise ValueError("基础采集含未知参数，请在界面重新填写")
        return RtuEnvironment.from_dict({**basic, **shared})
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError(f"请检查 测试环境→Modbus RTU→基础采集：{error}") from None


def huawei_config_from_environment(environment):
    """华为完整用例读取统一环境中的共享连接参数和六路路径。"""
    try:
        shared = _shared(environment)
        huawei = environment.get("modbus_huawei", {})
        if not isinstance(huawei, dict):
            raise ValueError("华为六路参数必须为对象")
        if set(huawei) - {"serial_devices", "dedicated_simulator"}:
            raise ValueError("华为六路含未知参数，请在界面重新填写")
        return HuaweiFleetConfig.from_dict({**huawei, **shared})
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError(f"请检查 测试环境→Modbus RTU→华为六路：{error}") from None


def validate_selected_modbus_cases(environment, case_ids):
    """只对实际选择的六项RTU用例要求完整配置，其余用例不受草稿影响。"""
    selected = set(case_ids)
    basic_selected = bool(selected & BASIC_CASE_IDS)
    huawei_selected = HUAWEI_CASE_ID in selected
    if not basic_selected and not huawei_selected:
        return
    if environment.get("mode") != "fresh":
        raise ValueError("所选RTU用例要求全新独立副本，请在 测试环境→运行与隔离 中切换")
    # 延迟导入避免环境模型与构造器之间形成循环依赖。
    from .model import validate_slavesim_url
    try:
        validate_slavesim_url(environment.get("slavesim_url", ""), required=True)
    except ValueError as error:
        raise ValueError(f"请检查 测试环境→slaveSim 模拟器：{error}") from None
    if basic_selected:
        basic_config_from_environment(environment)
    if huawei_selected:
        huawei_config_from_environment(environment)
    LOGGER.info("所选RTU用例前置配置检查通过：基础采集=%s，华为六路=%s", basic_selected, huawei_selected)
