"""专用 RTU 环境与双端数值验收；期望独立于被测页面计算。"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


LOGGER = logging.getLogger(__name__)


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


def validate_manager_address(address):
    try:
        parts = urlsplit("//" + address) if isinstance(address, str) else None
        valid = (parts and parts.hostname and parts.port and not parts.username and not parts.password
                 and not parts.path and not parts.query and not parts.fragment
                 and not any(char.isspace() or ord(char) < 32 for char in address))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("manager_address 必须是现场直连 ModuleManager 的主机:端口，不含凭据或路径")
    return address


@dataclass(frozen=True)
class RtuStation:
    station_id: int
    address: int


@dataclass(frozen=True)
class RtuEnvironment:
    device_name: str
    manager_address: str
    bus: str
    serial_device: str
    stations: tuple
    baud_rate: int = 9600
    data_bits: int = 8
    stop_bits: int = 1
    parity: str = "无校验"
    poll_interval_ms: int = 1000
    acquisition_timeout: float = 300

    @classmethod
    def load(cls, path, *, minimum_stations=2, acquisition_timeout=None):
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise ValueError(f"请按示例准备 local/{Path(path).name} 专用测试环境") from None
        if isinstance(value, dict) and acquisition_timeout is not None:
            value["acquisition_timeout"] = acquisition_timeout
        return cls.from_dict(value, minimum_stations=minimum_stations)

    @classmethod
    def from_dict(cls, value, *, minimum_stations=2):
        if not isinstance(value, dict):
            raise ValueError("RTU 测试环境必须是 JSON 对象")
        allowed = set(cls.__dataclass_fields__) | {"dedicated_test_resources"}
        if set(value) - allowed:
            raise ValueError("RTU 测试环境含未知字段，请核对示例")
        if value.get("dedicated_test_resources") is not True:
            raise ValueError("必须确认 dedicated_test_resources=true，串口和模拟点为专用测试资源")
        options = {}
        for key in ("device_name", "manager_address", "bus", "serial_device"):
            entry = value.get(key)
            if (not isinstance(entry, str) or not entry.strip()
                    or any(ord(char) < 32 for char in entry)):
                raise ValueError(f"RTU 环境 {key} 必须为非空单行字符串")
            options[key] = entry.strip()
        validate_manager_address(options["manager_address"])
        path = options["serial_device"]
        if (not path.startswith("/") or path == "/" or str(PurePosixPath(path)) != path
                or ".." in PurePosixPath(path).parts or "\\" in path
                or any(char.isspace() for char in path)):
            raise ValueError("serial_device 必须为规范的Linux绝对串口路径")
        stations = value.get("stations")
        if not isinstance(stations, list) or not minimum_stations <= len(stations) <= 2:
            raise ValueError("RTU 测试环境需要两个专用从站" if minimum_stations == 2
                             else "RTU 测试环境需要一至两个专用从站")
        parsed = []
        for station in stations:
            if not isinstance(station, dict) or set(station) != {"station_id", "address"}:
                raise ValueError("每个从站只能包含 station_id 和零基 address")
            station_id, address = station["station_id"], station["address"]
            if type(station_id) is not int or not 1 <= station_id <= 247:
                raise ValueError("RTU 从站站号须为 1 至 247 的整数")
            if type(address) is not int or not 0 <= address <= 65535:
                raise ValueError("RTU 地址须为 0 至 65535 的零基整数")
            parsed.append(RtuStation(station_id, address))
        if len({station.station_id for station in parsed}) != len(parsed):
            raise ValueError("各专用从站必须使用不同站号")
        options["stations"] = tuple(parsed)
        for key, default, minimum, maximum in (
                ("baud_rate", 9600, 1, 4000000), ("data_bits", 8, 5, 8),
                ("stop_bits", 1, 1, 2), ("poll_interval_ms", 1000, 100, 60000)):
            entry = value.get(key, default)
            if type(entry) is not int or not minimum <= entry <= maximum:
                raise ValueError(f"RTU 环境 {key} 必须为 {minimum} 至 {maximum} 的整数")
            options[key] = entry
        parity = value.get("parity", "无校验")
        if parity not in ("无校验", "奇校验", "偶校验"):
            raise ValueError("RTU parity 必须为无校验、奇校验或偶校验")
        options["parity"] = parity
        timeout = decimal_value(value.get("acquisition_timeout", 300))
        if not 1 <= timeout <= 300:
            raise ValueError("RTU 采集时限必须在 1 至 300 秒之间")
        options["acquisition_timeout"] = float(timeout)
        LOGGER.info("已读取专用 RTU 环境：总线=%s，从站数=%s", options["bus"], len(parsed))
        return cls(**options)


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
