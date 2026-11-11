"""AGC 可见页面读数的严格边界，不连接桌面或下位机。"""
from decimal import Decimal
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from case_support.agc_direction_page import (
    AgcDirectionPage, parse_bool, parse_decimal, parse_member_snapshot, select_bus_row, unique_match,
)


@pytest.mark.parametrize("text", ["—", "未知", "NaN", "Infinity", "100 kW（增量）", "1,000", ""])
def test_missing_or_ambiguous_numeric_value_is_rejected(text):
    """缺测、增量、非有限和带歧义分隔符的值不能冒充绝对功率。"""
    with pytest.raises(ValueError):
        parse_decimal(text)


def test_visible_bool_requires_explicit_value():
    """闭锁默认点只接受页面明确的是与否，空白不当作解除。"""
    assert parse_bool("是") is True
    assert parse_bool("否") is False
    for text in ("—", "未知", "", "false", "1"):
        with pytest.raises(ValueError):
            parse_bool(text)


def test_duplicate_group_or_member_is_never_silently_selected():
    """可见组或成员匹配重复时拒绝使用第一项掩盖身份歧义。"""
    with pytest.raises(RuntimeError, match="不唯一"):
        unique_match([object(), object()], "控制组")
    with pytest.raises(RuntimeError, match="未找到"):
        unique_match([], "成员")


def test_group_lookup_rejects_duplicate_visible_dom_matches():
    """真实组定位路径交给替身DOM后，重复可见组须在点击前失败。"""
    node = SimpleNamespace(is_displayed=lambda: True)
    page = AgcDirectionPage(SimpleNamespace(driver=SimpleNamespace(find_elements=lambda *_: [node, node])))
    with pytest.raises(RuntimeError, match="不唯一"):
        page._group_item("专用组")


def test_table_reading_uses_visible_headers_instead_of_fixed_column_numbers():
    """列表列顺序变化时仍按列名读，列缺失或重复不默默映射错误值。"""
    table = SimpleNamespace(find_elements=lambda *_: [SimpleNamespace(text=text) for text in ("质量", "缓存值", "点值时间")])
    row_element = SimpleNamespace(find_elements=lambda *_: [SimpleNamespace(text=text) for text in ("有效", "是", "2026/11/5 12:00:01")])
    assert AgcDirectionPage._row_values(table, row_element) == {"质量": "有效", "缓存值": "是", "点值时间": "2026/11/5 12:00:01"}
    repeated = SimpleNamespace(find_elements=lambda *_: [SimpleNamespace(text=text) for text in ("质量", "质量", "缓存值")])
    with pytest.raises(RuntimeError, match="列不完整或不唯一"):
        AgcDirectionPage._row_values(repeated, row_element)


def member():
    return dict(measurement="20 kW", setpoint="100 kW", caption="可控 · 容量 100 kW",
                limits="调节范围 10～0 kW", measurement_quality="有效", setpoint_quality="有效",
                measurement_time="2026/11/5 12:00:01", setpoint_time="2026/11/5 12:00:02",
                measurement_units="kW / 1 / 0", setpoint_units="kW / 1 / 0")


def test_capacity_is_effective_upper_when_configured_maximum_is_zero():
    """max=0 回退容量，而不是把有效可调上限读成零。"""
    result = parse_member_snapshot(member())
    assert result["lower_kw"] == Decimal("10")
    assert result["upper_kw"] == Decimal("100")
    assert result["measurement"] == Decimal("20")
    assert result["absolute"] is True


@pytest.mark.parametrize("field,value", [
    ("measurement_quality", "未知"), ("setpoint_quality", "无效"),
    ("setpoint", "100 kW（增量）"), ("caption", "仅测量 · 容量 100 kW"),
    ("measurement_units", "W / 1 / 0"), ("setpoint_units", "kW / 2 / 0"),
    ("measurement_time", "未知"), ("setpoint_time", ""),
])
def test_unsupported_member_state_cannot_pass(field, value):
    """质量、时间、工程量换算或成员模式不符合专用场景时明确失败。"""
    raw = member()
    raw[field] = value
    with pytest.raises(ValueError):
        parse_member_snapshot(raw)


def row(conn=7, quality="正常", endpoint="AGC/test : AGC增闭锁"):
    return {"key": f"7:AGC增闭锁->{conn}:AGC增闭锁-0", "target": endpoint,
            "value": "是", "quality": quality, "timestamp": "2026/11/5 12:00:02"}


def test_bus_observation_requires_exact_target_identity_and_quality():
    """仅匹配本组目标conn_id和完整端点，不把另一路转发或异常品质当作通过。"""
    result = select_bus_row([row(8), row()], 7, "AGC/test", "AGC增闭锁")
    assert result["value"] is True
    assert result["quality"] == "正常"
    with pytest.raises(RuntimeError, match="不唯一"):
        select_bus_row([row(), row()], 7, "AGC/test", "AGC增闭锁")
    with pytest.raises(ValueError, match="质量"):
        select_bus_row([row(quality="异常")], 7, "AGC/test", "AGC增闭锁")
    with pytest.raises(RuntimeError, match="未找到"):
        select_bus_row([row(endpoint="AGC/other : AGC增闭锁")], 7, "AGC/test", "AGC增闭锁")


@pytest.mark.parametrize("quality,issues,passes", [
    ("有效", "无", True), ("有效", "数据陈旧", True),
    ("无效", "数据陈旧；质量无效", False), ("有效", "时间超前", False),
    ("未指定", "质量未指定", False), ("有效", "时间未知", False),
])
def test_snapshot_acceptance_distinguishes_display_age_from_business_quality(monkeypatch, quality, issues, passes):
    """未变化伴随点可以超过展示新鲜度阈值；坏质量、缺时间和超前时间仍不能通过。"""
    desktop = SimpleNamespace(steps=[], _check_stop=lambda: None)

    @contextmanager
    def step(*args, **kwargs):
        desktop.steps.append({})
        yield

    desktop.step = step
    page = AgcDirectionPage(desktop)
    page.select_group = lambda *_: None
    page.read_member = lambda *_: parse_member_snapshot(member())
    page.read_direction_point = lambda tag: dict(value=tag == "AGC增闭锁", quality=quality,
                                                 issues=issues, timestamp="2026/11/5 12:00:02")
    clock = iter((0.0, 0.01, 0.02, 0.2))
    monkeypatch.setattr("case_support.agc_direction_page.time.monotonic", lambda: next(clock, 1.0))
    monkeypatch.setattr("case_support.agc_direction_page.time.sleep", lambda *_: None)
    profile = dict(group_name="专用组", member_name="成员", lower_kw="10", upper_kw="100")
    if passes:
        assert page.wait_snapshot(profile, "100", True, False, 0.1)["increase"] is True
    else:
        with pytest.raises(AssertionError, match="等待超时"):
            page.wait_snapshot(profile, "100", True, False, 0.1)
