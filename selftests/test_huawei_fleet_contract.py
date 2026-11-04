"""六路21台拓扑与逐点换算规则，不运行真实浏览器。"""
import pytest

from testbench.huawei_fleet import HuaweiFleetConfig, READ_POINTS, build_fleet, compare_point


def configuration():
    return {"dedicated_simulator": True, "manager_address": "192.0.2.1:50051",
            "serial_devices": [f"/dev/huawei-test-{i}" for i in range(1, 7)]}


def test_six_buses_contain_one_to_six_devices_without_duplicate_identities():
    """总计21台，站号每路重置，电压及光照场景可区分串路串站。"""
    buses = build_fleet(HuaweiFleetConfig.from_dict(configuration()), "test-run")
    assert [len(bus["devices"]) for bus in buses] == [1, 2, 3, 4, 5, 6]
    devices = [(bus["name"], device["station_id"]) for bus in buses for device in bus["devices"]]
    assert len(set(devices)) == 21
    assert len({device["grid_line_voltage_v"] for bus in buses for device in bus["devices"]}) == 21


@pytest.mark.parametrize("key,value", [
    ("dedicated_simulator", False), ("serial_devices", ["/dev/test"] * 6),
    ("serial_devices", ["/dev/test"]), ("manager_address", "http://host:50051"),
    ("acquisition_timeout", "NaN"), ("poll_interval_ms", True),
])
def test_invalid_fleet_configuration_refuses_before_external_operations(key, value):
    """非专用模拟器、重复路径及非法参数必须在页面操作前拒绝。"""
    config = configuration()
    config[key] = value
    with pytest.raises(ValueError):
        HuaweiFleetConfig.from_dict(config)


def point(key):
    return next(item for item in READ_POINTS if item.key == key)


def test_negative_reactive_power_and_pf_use_signed_raw_conversion():
    """负无功和负功率因数按有符号整数及倍率比较，不能变成正数。"""
    assert compare_point(point("reactive"), -12345, -12345,
                         {"value": "-12.345", "quality": "正常", "timestamp": "12:00:02"})["通过"]
    failed = compare_point(point("reactive"), -12345, -12345,
                           {"value": "4294954.951", "quality": "正常", "timestamp": "12:00:02"})
    assert not failed["通过"]
    assert compare_point(point("pf"), -900, -900,
                         {"value": "-0.9", "quality": "正常", "timestamp": "12:00:02"})["通过"]


def test_same_wrong_value_on_both_ends_does_not_ignore_status_expectation():
    """两端状态虽相等但不满足指定工况，仍然失败。"""
    result = compare_point(point("status"), 512, 512,
                           {"value": "512", "quality": "正常", "timestamp": "12:00:02"}, expected_raw=40960)
    assert not result["通过"]


def test_generation_bit_remains_true_during_limited_generation():
    """正常并网状态字6和限电降额状态字10的发电位均为true。"""
    for raw in (6, 10):
        assert compare_point(point("generating"), raw, raw,
                             {"value": True, "quality": "正常", "timestamp": "12:00:02"})["通过"]
    assert not compare_point(point("generating"), 128, 128,
                             {"value": True, "quality": "正常", "timestamp": "12:00:02"})["通过"]


@pytest.mark.parametrize("upper", [
    {"value": "230.9", "quality": "异常", "timestamp": "12:00:02"},
    {"value": "230.9", "quality": "正常", "timestamp": "-"},
    {"value": "231.0", "quality": "正常", "timestamp": "12:00:02"},
])
def test_wrong_value_quality_and_missing_time_fail(upper):
    """严格工程量比较，协议分辨率不是允许相邻错误寄存器通过的容差。"""
    assert not compare_point(point("voltage_a"), 2309, 2309, upper)["通过"]


def test_unstable_source_and_stale_upper_are_reported_separately():
    """源值变化或新工况仍显示上次时间，不得扩大容差判定通过。"""
    upper = {"value": "230.9", "quality": "正常", "timestamp": "12:00:02"}
    assert not compare_point(point("voltage_a"), 2309, 2310, upper)["通过"]
    assert not compare_point(point("voltage_a"), 2309, 2309, upper,
                             previous_timestamp="12:00:02")["通过"]
