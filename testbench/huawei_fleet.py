"""华为六路21台固定拓扑、点表及独立双端验收规则。"""
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .modbus_rtu_config import decimal_value


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HuaweiPoint:
    key: str
    name: str
    address: int
    data_type: str
    reg_count: int = 1
    scale: str = "1"
    function: int = 3
    bit_index: int | None = None
    enum_value: int | None = None
    unit: str = ""


READ_POINTS = (
    HuaweiPoint("voltage_a", "A相电压", 32069, "UINT16", scale="0.1", unit="V"),
    HuaweiPoint("voltage_b", "B相电压", 32070, "UINT16", scale="0.1", unit="V"),
    HuaweiPoint("voltage_c", "C相电压", 32071, "UINT16", scale="0.1", unit="V"),
    HuaweiPoint("current_a", "A相电流", 32072, "INT32", 2, "0.001", unit="A"),
    HuaweiPoint("current_b", "B相电流", 32074, "INT32", 2, "0.001", unit="A"),
    HuaweiPoint("current_c", "C相电流", 32076, "INT32", 2, "0.001", unit="A"),
    HuaweiPoint("active", "有功功率", 32080, "INT32", 2, "0.001", unit="kW"),
    HuaweiPoint("reactive", "无功功率", 32082, "INT32", 2, "0.001", unit="kvar"),
    HuaweiPoint("pf", "功率因数", 32084, "INT16", scale="0.001"),
    HuaweiPoint("status_word", "状态字", 32000, "UINT16"),
    HuaweiPoint("status", "设备状态", 32089, "UINT16"),
    HuaweiPoint("generating", "发电", 32000, "BOOL", bit_index=1),
    HuaweiPoint("stopped", "停机_限电停运", 32000, "BOOL", bit_index=7),
    HuaweiPoint("standby", "待机", 32000, "BOOL", bit_index=0),
    HuaweiPoint("active_mode", "有功调节模式", 35300, "UINT16"),
    HuaweiPoint("reactive_mode", "无功调节模式", 35304, "UINT16"),
    HuaweiPoint("active_command", "最后有功指令地址", 35303, "UINT16"),
    HuaweiPoint("reactive_command", "最后无功指令地址", 35307, "UINT16"),
)
CONTROL_POINTS = (
    HuaweiPoint("active_setpoint", "有功百分比设定", 40125, "UINT16", scale="0.1", function=6, unit="%"),
    HuaweiPoint("pf_setpoint", "功率因数设定", 40122, "INT16", scale="0.001", function=6),
    HuaweiPoint("reactive_setpoint", "无功Q_S设定", 40123, "INT16", scale="0.001", function=6),
)
UNIMPLEMENTED_CONTROLS = (
    {"功能": "开机遥控", "地址": 40200, "状态": "模型未实现，未发送命令，未验证"},
    {"功能": "关机遥控", "地址": 40201, "状态": "模型未实现，未发送命令，未验证"},
)
STATUS_VALUES = {"发电": 512, "有功调节": 513, "无功调节": 513, "负无功调节": 513, "待机": 40960, "限电停运": 772,
                 "恢复发电": 512}
STATUS_WORD_VALUES = {"发电": 6, "有功调节": 10, "无功调节": 10, "负无功调节": 10,
                      "待机": 1, "限电停运": 128, "恢复发电": 6}


@dataclass(frozen=True)
class HuaweiFleetConfig:
    manager_address: str
    serial_devices: tuple
    acquisition_timeout: float = 300
    poll_interval_ms: int = 1000
    baud_rate: int = 9600

    @classmethod
    def load(cls, path):
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise ValueError("请按示例填写 local/huawei-rtu.json 的专用模拟器及六条共享串口路径") from None
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.get("dedicated_simulator") is not True:
            raise ValueError("华为21台测试必须明确 dedicated_simulator=true，配置保存会重建模型")
        if set(value) - (set(cls.__dataclass_fields__) | {"dedicated_simulator"}):
            raise ValueError("华为环境含未知字段，请核对示例")
        manager = value.get("manager_address")
        try:
            parts = urlsplit("//" + manager) if isinstance(manager, str) else None
            valid = (parts and parts.hostname and parts.port and not parts.username and not parts.password
                     and not parts.path and not parts.query and not parts.fragment and not any(c.isspace() for c in manager))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("manager_address 必须为现场直连主机:端口")
        paths = value.get("serial_devices")
        if (not isinstance(paths, list) or len(paths) != 6
                or any(not isinstance(path, str) or not path.startswith("/") or path == "/"
                       or str(PurePosixPath(path)) != path or ".." in PurePosixPath(path).parts
                       or any(char.isspace() or ord(char) < 32 for char in path) for path in paths)
                or len(set(paths)) != 6):
            raise ValueError("serial_devices 必须提供六条不同的Linux绝对串口路径，不含相对段或重复斜线")
        timeout = decimal_value(value.get("acquisition_timeout", 300))
        if not 5 <= timeout <= 300:
            raise ValueError("采集时限须为5至300秒")
        poll = value.get("poll_interval_ms", 1000)
        baud = value.get("baud_rate", 9600)
        if type(poll) is not int or not 100 <= poll <= 60000 or type(baud) is not int or not 1 <= baud <= 4000000:
            raise ValueError("轮询周期或波特率无效")
        return cls(manager, tuple(paths), float(timeout), poll, baud)


def build_fleet(config, run_id):
    """场景固定且每台不同；通过不同电压及有功识别同站号跨总线串站。"""
    buses, ordinal = [], 0
    for index, path in enumerate(config.serial_devices, 1):
        devices = []
        for station in range(1, index + 1):
            ordinal += 1
            devices.append({"station_id": station, "ordinal": ordinal,
                            "dc_available_kw": 10 + ordinal, "grid_line_voltage_v": 380 + ordinal,
                            "grid_frequency_hz": 50, "ambient_temperature_c": 25, "efficiency": 1,
                            "disturbance_enabled": False})
        buses.append({"name": f"e2e-huawei-{run_id}-485-{index}", "index": index,
                      "serial_device": path, "devices": devices})
    LOGGER.info("已生成六路华为行为拓扑，共21台，所有场景均关闭扰动")
    return buses


def load_dedicated_fleet(path, timeout):
    """Read the six installed simulator paths and the dedicated DSP endpoint."""
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise RuntimeError(f"前置条件未满足：请按示例填写 {path.name}") from error
    fields = {"manager_address", "dedicated_test_resources", "buses"}
    if not isinstance(value, dict) or value.keys() != fields or value["dedicated_test_resources"] is not True:
        raise ValueError("华为机群配置须填写专用 manager_address、dedicated_test_resources=true 和六路 buses")
    buses = value["buses"]
    if (not isinstance(buses, list) or len(buses) != 6
            or any(not isinstance(bus, dict) or set(bus) != {"name", "serial_device"}
                   or not isinstance(bus["name"], str) or not bus["name"].strip()
                   or any(ord(char) < 32 for char in bus["name"]) for bus in buses)
            or len({bus["name"] for bus in buses}) != 6):
        raise ValueError("须按路号填写六个不同的预置 slaveSim 总线名称及对应串口")
    config = HuaweiFleetConfig.from_dict({"manager_address": value["manager_address"],
                                         "serial_devices": [bus["serial_device"] for bus in buses],
                                         "acquisition_timeout": timeout, "dedicated_simulator": True})
    topology = build_fleet(config, "prepared")
    for bus, actual in zip(topology, buses, strict=True):
        bus["name"] = actual["name"]
    return config, topology


def point_expected(point, raw):
    value = decimal_value(raw)
    if value != value.to_integral_value():
        raise ValueError("模拟器原始寄存器值必须为整数")
    if point.data_type == "BOOL":
        return (int(value) == point.enum_value if point.enum_value is not None
                else bool((int(value) >> point.bit_index) & 1))
    return value * Decimal(point.scale)


def compare_point(point, before, after, upper, *, expected_raw=None, previous_timestamp=None):
    """两端源值在采样窗口不变时严格核对；错误逐点记录供汇总。"""
    errors = []
    expected = None
    actual = upper.get("value")
    difference = None
    try:
        expected = point_expected(point, before)
        if decimal_value(before) != decimal_value(after):
            errors.append("模拟器源值在采样窗口变化，未达到稳态")
        if expected_raw is not None and decimal_value(before) != decimal_value(expected_raw):
            errors.append(f"模拟器状态未达到指定工况：预期原始值{expected_raw}")
        if point.data_type == "BOOL":
            if type(actual) is not bool or actual != expected:
                errors.append("BOOL状态与模拟器状态字不匹配")
        else:
            difference = decimal_value(actual) - expected
            if abs(difference) > Decimal("0.000001"):
                errors.append("上位机工程量与原始值换算结果不匹配")
        if upper.get("quality") != "正常":
            errors.append("上位机质量不是正常")
        timestamp = str(upper.get("timestamp") or "").strip()
        if timestamp in ("", "-", "—", "未知"):
            errors.append("上位机采集时间无效")
        elif previous_timestamp is not None and timestamp == previous_timestamp:
            errors.append("数值变化后上位机仍显示前次时间")
    except (ValueError, TypeError, KeyError) as error:
        errors.append(f"点值格式无效：{error}")
    return {"点名": point.name, "点标识": point.key, "地址": point.address, "类型": point.data_type,
            "倍率": point.scale, "位索引": point.bit_index, "单位": point.unit,
            "模拟器前值": before, "模拟器后值": after, "换算期望": str(expected),
            "上位机实际": actual, "差值": str(difference) if difference is not None else "",
            "质量": upper.get("quality"), "时间": upper.get("timestamp"),
            "通过": not errors, "错误": errors}
