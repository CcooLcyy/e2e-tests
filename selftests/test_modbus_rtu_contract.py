"""RTU 配置和双端验收边界；不连接浏览器、平台或下位机。"""
import pytest

from testbench.modbus_rtu_config import RtuRuntime, RtuStation, assert_dual_observation


def test_rtu_runtime_keeps_observed_station_and_pty():
    runtime = RtuRuntime("实际总线", "/srv/serial-sim/tty9", (RtuStation(23, 99),), 30)
    assert runtime.serial_device == "/srv/serial-sim/tty9"
    assert runtime.stations == (RtuStation(23, 99),)
    assert (runtime.baud_rate, runtime.data_bits, runtime.parity, runtime.stop_bits) == (9600, 8, "N", 1)
    assert runtime.acquisition_timeout == 30


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
