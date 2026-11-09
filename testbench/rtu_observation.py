"""预置 RTU 链路的配置与只读上位机观测。"""
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from .huawei_fleet import build_fleet
from .huawei_rtu_page import HuaweiRtuPage, xpath_text


def _profile(path, fields):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"前置条件未满足：请按示例准备 {Path(path).name}，并在测试前部署对应环境") from error
    if not isinstance(result, dict) or set(result) != set(fields):
        raise ValueError(f"{Path(path).name} 必须填写：{'、'.join(fields)}")
    return result


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and not any(ord(char) < 32 for char in value)


def _serial(value):
    return (_text(value) and value.startswith("/") and value != "/" and str(PurePosixPath(value)) == value
            and ".." not in PurePosixPath(value).parts and "\\" not in value
            and not any(char.isspace() for char in value))


@dataclass(frozen=True)
class ObservedStation:
    station_id: int
    address: int
    connection: str
    tag: str


@dataclass(frozen=True)
class BasicObservationProfile:
    bus: str
    serial_device: str
    stations: tuple
    scaled_connection: str
    scaled_tag: str
    acquisition_timeout: float

    @classmethod
    def load(cls, path, timeout):
        value = _profile(path, ("bus", "serial_device", "stations", "scaled_connection", "scaled_tag"))
        if (not all(_text(value[key]) for key in ("bus", "scaled_connection", "scaled_tag"))
                or not _serial(value["serial_device"])):
            raise ValueError("须填写实际 slaveSim 总线、串口和已预置的倍率偏移连接/Tag")
        stations = value["stations"]
        if not isinstance(stations, list) or len(stations) != 2:
            raise ValueError("须预置两个 RTU 从站")
        for row in stations:
            if (not isinstance(row, dict) or set(row) != set(ObservedStation.__dataclass_fields__)
                    or type(row["station_id"]) is not int or not 1 <= row["station_id"] <= 247
                    or type(row["address"]) is not int or not 0 <= row["address"] <= 65535
                    or not _text(row["connection"]) or not _text(row["tag"])):
                raise ValueError("须填写从站的有效站号、零基地址、已预置上位机连接和 Tag")
        if (stations[0]["station_id"] == stations[1]["station_id"]
                or len({row["connection"] for row in stations} | {value["scaled_connection"]}) != 3):
            raise ValueError("两个站号及三个预置连接必须分别唯一")
        return cls(value["bus"], value["serial_device"], tuple(ObservedStation(**row) for row in stations),
                   value["scaled_connection"], value["scaled_tag"], timeout)


@dataclass(frozen=True)
class FleetObservationProfile:
    serial_devices: tuple
    acquisition_timeout: float

    @classmethod
    def load(cls, path, timeout):
        value = _profile(path, ("buses",))
        buses = value["buses"]
        if (not isinstance(buses, list) or len(buses) != 6
                or any(not isinstance(bus, dict) or set(bus) != {"name", "serial_device"}
                       or not _text(bus["name"]) or not _serial(bus["serial_device"]) for bus in buses)):
            raise ValueError("须填写六路已预置的 slaveSim 总线名称和对应串口")
        names = [bus["name"] for bus in buses]
        paths = tuple(bus["serial_device"] for bus in buses)
        if len(set(names)) != 6 or len(set(paths)) != 6:
            raise ValueError("华为机群的六个总线名称和六条串口路径必须分别唯一")
        profile = cls(paths, timeout)
        topology = build_fleet(profile, "prepared")
        for bus, name in zip(topology, names, strict=True):
            bus["name"] = name
        return profile, topology


class PreparedRtuPage(HuaweiRtuPage):
    """通过业务点表选择和观察预置连接。"""

    def __init__(self, desktop):
        super().__init__(desktop)
        self.prepared = {}

    def open_prepared_links(self):
        try:
            self._rtu()
        except TimeoutException as error:
            raise RuntimeError("前置条件未满足：请在运行前为 DSP 上位机准备可用的在线 RTU 业务连接") from error

    def expect_connection(self, name, serial_device, station_id):
        self.prepared[name] = (serial_device, station_id, None)

    def select(self, name):
        if name not in self.prepared:
            raise RuntimeError("只允许观察专项配置中声明的预置 RTU 连接")
        card = ".modbus-connection-card .ant-card-head"
        title = (By.XPATH, "//div[contains(@class,'modbus-connection-card')]"
                 "//div[contains(@class,'ant-card-head-title')]//span[normalize-space(.)=" + xpath_text(name) + "]")
        try:
            self._click(self._connection_locator(name), f"观察预置 RTU 连接 {name}")
            self._until(lambda _: self._visible(title), f"预置 RTU 连接 {name} 未显示")
            self._until(lambda _: "运行中" in self.driver.find_element(By.CSS_SELECTOR, card).text,
                        f"预置 RTU 连接 {name} 未运行")
        except TimeoutException as error:
            raise RuntimeError(f"前置条件未满足：RTU 连接 {name} 必须在执行前准备并运行") from error
        serial, station, previous_id = self.prepared[name]
        self.desktop.check("预置连接串口匹配", self._detail("串口设备"), serial)
        head = self.driver.find_element(By.CSS_SELECTOR, card)
        self.desktop.check("预置连接站号匹配", any(item.text.strip() == f"从站 {station}"
                                                  for item in head.find_elements(By.CSS_SELECTOR, "span")), True)
        conn_id = self._detail("连接编号")
        if not conn_id.isdecimal() or int(conn_id) <= 0 or (previous_id is not None and previous_id != conn_id):
            raise RuntimeError("预置 RTU 连接身份缺失或已变化")
        self.prepared[name] = (serial, station, conn_id)
        self.current = name

    def _assert_identity(self):
        if self.current not in self.prepared or self._detail("连接编号") != self._selected_conn_id():
            raise RuntimeError("预置 RTU 连接已变化，拒绝把其他连接的数据作为证据")

    def _selected_conn_id(self):
        return self.prepared[self.current][2]
