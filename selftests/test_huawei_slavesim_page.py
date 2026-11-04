"""华为行为页面的拓扑、原始值和会话边界，不启动浏览器。"""
import pytest

from testbench.huawei_slavesim_page import HuaweiSlaveSimPage, decode_register, validate_fleet


def fleet():
    return [{"name": f"测试-{i}", "serial_device": f"/dev/test{i}", "devices": [
        {"station_id": station, "dc_available_kw": 20 + station, "grid_line_voltage_v": 390 + i}
        for station in range(1, i + 1)]} for i in range(1, 7)]


def test_fleet_is_six_buses_and_twenty_one_distinct_stations():
    """必须恰好六路，逐路站号1至路数，拒绝少站或重复路径。"""
    validate_fleet(fleet())
    value = fleet()
    value[5]["devices"].pop()
    with pytest.raises(ValueError):
        validate_fleet(value)
    value = fleet()
    value[1]["serial_device"] = value[0]["serial_device"]
    with pytest.raises(ValueError):
        validate_fleet(value)


@pytest.mark.parametrize("kind,raw,text,expected", [
    ("UINT16", "0F A0", "4000", 4000), ("INT16", "FC 18", "-1000", -1000),
    ("INT32", "FF FF D8 F0", "-10000", -10000), ("UINT32", "00 01 00 00", "65536", 65536),
])
def test_raw_decoding_preserves_signed_values(kind, raw, text, expected):
    """HL/AB 原始字节必须与当前值一致，负无功和负PF不能按无符号解释。"""
    assert decode_register(kind, raw, text) == expected


@pytest.mark.parametrize("kind,raw,text", [("INT32", "00 01", "1"), ("UINT16", "00 01", "2"),
                                         ("INT16", "FF FF", "65535"), ("UINT16", "00 01", "NaN")])
def test_raw_disagreement_is_failure(kind, raw, text):
    """半字、错误符号、非有限值与展示不一致均不能通过。"""
    with pytest.raises(AssertionError):
        decode_register(kind, raw, text)


def test_context_keeps_fleet_and_only_closes_owned_browser():
    """退出不恢复手动点或删除创建配置，原错误仍传播。"""
    calls = []
    page = object.__new__(HuaweiSlaveSimPage)
    page.close = lambda: calls.append("关闭本例浏览器")
    page.restore_all = lambda: pytest.fail("华为行为模型不得调用手动点恢复")
    assert page.__exit__(AssertionError, AssertionError("原错误"), None) is None
    assert calls == ["关闭本例浏览器"]
