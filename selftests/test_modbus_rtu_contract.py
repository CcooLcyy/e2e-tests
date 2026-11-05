"""RTU 配置和双端验收边界；不连接浏览器、平台或下位机。"""
import pytest

from testbench.modbus_rtu_config import RtuEnvironment, assert_dual_observation


def environment():
    return {"dedicated_test_resources": True, "device_name": "测试设备", "manager_address": "192.0.2.1:50051", "bus": "专用总线",
            "serial_device": "/dev/rtu-test", "stations": [
                {"station_id": 1, "address": 0}, {"station_id": 2, "address": 65535}]}


def test_rtu_acquisition_wait_defaults_to_five_minutes_and_accepts_custom_values():
    """基础RTU构造及配置默认五分钟，300秒可用，显式20秒保留。"""
    config = RtuEnvironment.from_dict(environment())
    assert config.acquisition_timeout == 300
    assert RtuEnvironment(config.device_name, config.manager_address, config.bus,
                          config.serial_device, config.stations).acquisition_timeout == 300
    for timeout in (20, 300):
        assert RtuEnvironment.from_dict({**environment(), "acquisition_timeout": timeout}).acquisition_timeout == timeout
    with pytest.raises(ValueError, match="采集时限"):
        RtuEnvironment.from_dict({**environment(), "acquisition_timeout": 301})


def test_station_address_bounds_and_distinct_stations():
    """零基首末地址可用，不同站号允许同址，重复站号必须拒绝。"""
    assert RtuEnvironment.from_dict(environment()).stations[1].address == 65535
    value = environment()
    value["stations"][1]["station_id"] = 1
    with pytest.raises(ValueError, match="不同站号"):
        RtuEnvironment.from_dict(value)


@pytest.mark.parametrize("path", ["/srv/serial-sim/tty0", "/dev/pts/12"])
def test_runtime_pty_absolute_paths_are_preserved(path):
    """实际模拟器共享路径不必位于/dev，但必须原样传递给DSP连接。"""
    assert RtuEnvironment.from_dict({**environment(), "serial_device": path}).serial_device == path


@pytest.mark.parametrize("path", ["/", "relative/tty0", "/srv/../tty0", "/srv//tty0", "/srv/tty 0", "/srv/tty0/"])
def test_noncanonical_serial_paths_are_rejected(path):
    """拒绝歧义路径，不能自行归一化后指向其他串口。"""
    with pytest.raises(ValueError, match="绝对"):
        RtuEnvironment.from_dict({**environment(), "serial_device": path})


@pytest.mark.parametrize("field,invalid", [
    ("station_id", 0), ("station_id", 248), ("station_id", True),
    ("address", -1), ("address", 65536), ("address", 1.5),
])
def test_invalid_station_or_address_is_rejected(field, invalid):
    """广播站号、越界地址和非整数不得用于实际页面操作。"""
    value = environment()
    value["stations"][0][field] = invalid
    with pytest.raises(ValueError):
        RtuEnvironment.from_dict(value)


@pytest.mark.parametrize("key,value", [
    ("dedicated_test_resources", False), ("dedicated_test_resources", "true"),
    ("serial_device", "COM1"), ("device_name", ""), ("poll_interval_ms", True),
    ("manager_address", "http://host:50051/path"), ("manager_address", "host"),
    ("acquisition_timeout", "NaN"), ("acquisition_timeout", 0),
    ("acquisition_timeout", float("inf")), ("unexpected", 1),
])
def test_invalid_environment_refuses_before_touching_resources(key, value):
    """缺少专用资源声明或参数非法时必须拒绝，不使用环境默认值继续操作。"""
    options = environment()
    options[key] = value
    with pytest.raises(ValueError):
        RtuEnvironment.from_dict(options)


def test_engineering_expectation_comes_from_fixed_raw_input():
    """1234 经 0.1 倍率和 2 偏移应为 125.4，不把两端同错判为正确。"""
    actual = {"value": "125.4", "quality": "正常", "timestamp": "12:00:02"}
    result = assert_dual_observation({"value": 1234}, actual, 1234, scale="0.1", offset=2)
    assert result["工程量期望"] == "125.4"
    with pytest.raises(AssertionError, match="原始值不符"):
        assert_dual_observation({"value": 999}, {**actual, "value": 999}, 1234)


@pytest.mark.parametrize("change", [
    {"value": "125.41"}, {"quality": "未知"}, {"timestamp": ""},
    {"timestamp": "—"}, {"timestamp": "12:00:01"},
])
def test_bad_quality_missing_time_stale_time_and_wrong_value_fail(change):
    """错误工程量、质量与过期时间均保持失败，不能只比较数值。"""
    upper = {"value": "125.4", "quality": "正常", "timestamp": "12:00:02", **change}
    with pytest.raises(AssertionError):
        assert_dual_observation({"value": 1234}, upper, 1234, scale="0.1", offset=2,
                                previous_timestamp="12:00:01")


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "—", "", None, True])
def test_invalid_numeric_values_cannot_pass_comparison(invalid):
    """非数值与非有限值不能绕过误差比较。"""
    with pytest.raises(ValueError):
        assert_dual_observation({"value": invalid},
                                {"value": 1234, "quality": "正常", "timestamp": "12:00:02"}, 1234)
