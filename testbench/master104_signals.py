"""从主站可见点表核对闭锁遥信、实测功率及接收证据。"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import logging
import math
import time


LOGGER = logging.getLogger(__name__)


class Master104PointObserver:
    """区分本轮收到的点值与重新读取的历史点表。"""

    def __init__(self, session, ioas, common_address, *, measured_ioa=None):
        if (not isinstance(ioas, dict) or not ioas or
                any(not isinstance(key, str) or not key or type(value) is not int or not 0 <= value <= 0xffffff
                    for key, value in ioas.items()) or len(set(ioas.values())) != len(ioas)):
            raise ValueError("主站遥信IOA映射须为非空唯一名称和24位整数地址")
        self.session, self.ioas, self.common_address = session, dict(ioas), common_address
        self.measured_ioa = measured_ioa

    def _point_evidence(self, row, label):
        received_ms = int(row["received"])
        frame_id = int(row["frame_id"])
        if frame_id <= 0 or received_ms <= 0:
            raise RuntimeError(f"{label}缺少有效接收帧序号或接收时间")
        if received_ms > time.time_ns() // 1_000_000 + 1000:
            raise RuntimeError(f"{label}接收时间超出本机时钟容差：{received_ms}")
        return {"ioa": int(row["cells"][1]), "quality": row["cells"][4],
                "updated_at": datetime.fromtimestamp(received_ms / 1000).astimezone().isoformat(),
                "received_ms": received_ms, "frame_id": frame_id, "protocol_time": row["cells"][5],
                "raw_cells": row["cells"]}

    def _read_measurement(self):
        if self.measured_ioa is None:
            raise ValueError("实测未到限场景须配置104实测功率点 measured_ioa")
        snapshot = self.session.read_measurements()
        matches = [row for row in snapshot["rows"] if int(row["cells"][0]) == self.common_address
                   and int(row["cells"][1]) == self.measured_ioa]
        if len(matches) != 1:
            raise RuntimeError("实测功率IOA必须在指定公共地址的遥测点表中唯一存在")
        row = matches[0]
        _, _, type_id, value, quality, _, _ = row["cells"]
        if int(type_id) not in (13, 36) or int(quality, 16) & 0xF1:
            raise RuntimeError(f"实测功率须为有效、当前且未溢出的短浮点遥测：{row['cells']!r}")
        try:
            measured_kw = Decimal(value)
        except InvalidOperation:
            raise RuntimeError(f"实测功率不可解析：{value!r}") from None
        if not measured_kw.is_finite():
            raise RuntimeError(f"实测功率须为有限值：{value!r}")
        return snapshot["identity"], dict(self._point_evidence(row, "实测功率"), value_kw=str(measured_kw))

    def _read(self, *, include_measurement=False):
        if include_measurement:
            measurement_identity, measurement = self._read_measurement()
        snapshot = self.session.read_signals()
        matches = {key: [] for key in self.ioas}
        for row in snapshot["rows"]:
            ca, ioa, type_id, value, quality, _, _ = row["cells"]
            if int(ca) != self.common_address:
                continue
            for key, expected_ioa in self.ioas.items():
                if int(ioa) != expected_ioa:
                    continue
                if int(type_id) not in (1, 30) or value not in ("分", "合"):
                    raise RuntimeError(f"遥信{key}不是明确的单点BOOL值：类型{type_id}，值{value!r}")
                if int(quality, 16) & 0xF0:
                    raise RuntimeError(f"遥信{key}不是有效、当前且未经取代或封锁的值：{quality!r}")
                matches[key].append(dict(self._point_evidence(row, f"遥信{key}"), value=value == "合"))
        if any(len(rows) != 1 for rows in matches.values()):
            raise RuntimeError("遥信 IOA 必须在指定公共地址的点表中唯一存在")
        result = {"identity": snapshot["identity"], "observed_at": snapshot["observed_at"],
                  "points": {key: rows[0] for key, rows in matches.items()}}
        if include_measurement:
            if measurement_identity != result["identity"]:
                raise RuntimeError("遥信和实测功率须来自同一主站会话")
            result["measurement"] = measurement
        return result

    def checkpoint(self, *, include_measurement=False):
        result = self._read(include_measurement=include_measurement)
        result["wall_time"] = datetime.now().astimezone().isoformat()
        LOGGER.info("已记录104遥信业务基线：IOA=%s，点更新时间=%s", self.ioas,
                    {key: row["updated_at"] for key, row in result["points"].items()})
        return result

    def wait_state(self, expected, baseline, timeout, *, fresh_keys, sent_frame_id, measurement_below=None):
        if (not isinstance(expected, dict) or not expected or any(key not in self.ioas or type(value) is not bool
                                                                 for key, value in expected.items())
                or not fresh_keys or any(key not in expected for key in fresh_keys)):
            raise ValueError("遥信期望须使用已配置的BOOL点，且明确本轮需变位的点")
        if isinstance(timeout, bool) or not math.isfinite(float(timeout)) or float(timeout) <= 0:
            raise ValueError("遥信业务等待时间须为有限正数")
        if (not isinstance(baseline, dict) or set(baseline.get("points", {})) != set(self.ioas)
                or any(baseline["points"][key].get("ioa") != ioa for key, ioa in self.ioas.items())):
            raise ValueError("遥信基线与当前IOA映射不一致")
        operation_boundary = round(datetime.fromisoformat(baseline["wall_time"]).timestamp() * 1000)
        if type(sent_frame_id) is not int or sent_frame_id <= 0:
            raise ValueError("须提供本次遥调实际发送帧序号")
        deadline = time.monotonic() + float(timeout)
        last = None
        while time.monotonic() < deadline:
            last = self._read(include_measurement=measurement_below is not None)
            if time.monotonic() >= deadline:
                break
            if last["identity"] != baseline["identity"]:
                raise RuntimeError("主站实例身份已变化，不能替代原业务现场")
            values_match = all(last["points"][key]["value"] == value for key, value in expected.items())
            fresh = all(last["points"][key]["frame_id"] > max(baseline["points"][key]["frame_id"], sent_frame_id)
                        and last["points"][key]["received_ms"] >= operation_boundary for key in fresh_keys)
            if values_match and fresh:
                if measurement_below is None:
                    return last
                measured = last["measurement"]
                if (measured["frame_id"] > max(baseline["measurement"]["frame_id"], sent_frame_id)
                        and measured["received_ms"] >= operation_boundary):
                    if Decimal(measured["value_kw"]) >= measurement_below:
                        raise AssertionError(f"增闭锁已置位时，本轮实测功率已到上限 {measurement_below} kW：{last!r}")
                    return last
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        measurement_note = "及本轮新实测功率" if measurement_below is not None else ""
        raise TimeoutError(f"104遥信未在{timeout}秒内满足值、品质和本轮更新时间{measurement_note}："
                           f"期望={expected!r}，最后观察={last!r}")
