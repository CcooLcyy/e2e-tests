"""专用 RTU 环境与双端数值验收；期望独立于被测页面计算。"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


def decimal_value(value):
    """拒绝空值、布尔、非有限数以及页面的占位文字。"""
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError()
        result = Decimal(str(value).strip())
        if not result.is_finite():
            raise ValueError()
        return result
    except (InvalidOperation, ValueError):
        raise ValueError(f"不是有效的有限数值：{value!r}") from None


@dataclass(frozen=True)
class RtuStation:
    station_id: int
    address: int


@dataclass(frozen=True)
class RtuRuntime:
    bus: str
    serial_device: str
    stations: tuple[RtuStation, ...]
    acquisition_timeout: float
    baud_rate: int = 9600
    data_bits: int = 8
    stop_bits: int = 1
    parity: str = "N"
    poll_interval_ms: int = 1000


def expected_value(raw, scale=1, offset=0):
    return decimal_value(raw) * decimal_value(scale) + decimal_value(offset)


def assert_dual_observation(simulator, upper, raw, *, scale=1, offset=0, previous_timestamp=None):
    """两端分别对照固定输入，不能两端碰巧相等就算正确。"""
    expected = expected_value(raw, scale, offset)
    assert decimal_value(simulator["value"]) == decimal_value(raw), (
        f"slaveSim 原始值不符：预期 {raw}，实际 {simulator.get('value')}")
    actual = decimal_value(upper["value"])
    assert abs(actual - expected) <= Decimal("0.000001"), (
        f"DSP 上位机工程量不符：预期 {expected}，实际 {actual}")
    assert upper.get("quality") == "正常", f"DSP 上位机质量不是正常：{upper.get('quality')}"
    timestamp = str(upper.get("timestamp") or "").strip()
    assert timestamp and timestamp not in ("-", "—", "未知"), "DSP 上位机缺少有效采集时间"
    if previous_timestamp is not None:
        assert timestamp != previous_timestamp, "数值修改后上位机仍显示前次采集时间"
    return {"原始期望": str(raw), "倍率": str(scale), "偏移": str(offset),
            "工程量期望": str(expected), "slaveSim": simulator, "DSP上位机": upper}
