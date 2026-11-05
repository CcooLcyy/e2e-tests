"""从统一任务环境构造RTU参数，运行前按所选业务用例校验。"""
import logging
from urllib.parse import urlsplit

from .huawei_fleet import HuaweiFleetConfig
from .modbus_rtu_config import RtuEnvironment, decimal_value


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
    manager = environment.get("modbus_manager_address", "")
    try:
        parts = urlsplit("//" + manager) if isinstance(manager, str) else None
        valid = (parts and parts.hostname and parts.port and not parts.username and not parts.password
                 and not parts.path and not parts.query and not parts.fragment
                 and not any(c.isspace() or ord(c) < 32 for c in manager))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("ModuleManager 地址必须为现场直连主机:端口，不含凭据或路径")
    poll = environment.get("modbus_poll_interval_ms", 1000)
    if type(poll) is not int or not 100 <= poll <= 60000:
        raise ValueError("轮询周期必须为100至60000毫秒的整数")
    timeout = decimal_value(environment.get("modbus_acquisition_timeout", 30))
    if not 5 <= timeout <= 120:
        raise ValueError("采集等待时限必须为5至120秒")
    # slaveSim 使用PTY，不提供UART参数；历史手填波特率不再作为执行来源。
    return {"manager_address": manager, "baud_rate": 9600,
            "poll_interval_ms": poll, "acquisition_timeout": float(timeout)}


def basic_config_from_environment(environment, runtime=None):
    """只将执行时已读取并核实的模拟器资源与任务连接参数组合。"""
    try:
        shared = _shared(environment)
        if not isinstance(runtime, dict):
            raise ValueError("尚未取得 slaveSim 运行时基础采集配置")
        basic = {key: runtime[key] for key in ("bus", "serial_device", "stations") if key in runtime}
        return RtuEnvironment.from_dict({**basic, **shared,
                                        "device_name": shared["manager_address"],
                                        "data_bits": 8, "stop_bits": 1, "parity": "无校验",
                                        "dedicated_test_resources": True})
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError(f"slaveSim 运行时基础采集配置无效：{error}") from None


def huawei_config_from_environment(environment, runtime=None):
    """六路路径必须来自执行时模拟器页面，历史环境路径不参与执行。"""
    try:
        shared = _shared(environment)
        if not isinstance(runtime, dict):
            raise ValueError("尚未取得 slaveSim 运行时六路配置")
        return HuaweiFleetConfig.from_dict({"serial_devices": runtime.get("serial_devices"),
                                            **shared, "dedicated_simulator": True})
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError(f"slaveSim 运行时华为六路配置无效：{error}") from None


def validate_selected_modbus_cases(environment, case_ids):
    """任务启动前仅校验访问入口；具体模拟资源在用例执行时读取。"""
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
    try:
        _shared(environment)
    except (ValueError, TypeError) as error:
        raise ValueError(f"请检查 测试环境→Modbus RTU：{error}") from None
    LOGGER.info("RTU访问入口检查通过，模拟器资源将在用例执行时读取：基础采集=%s，华为六路=%s",
                basic_selected, huawei_selected)
