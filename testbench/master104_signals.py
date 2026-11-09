"""从可见遥信点表核对单点值、品质及本轮接收时间。"""
from datetime import datetime
import logging
import math
import time


LOGGER = logging.getLogger(__name__)


class Master104PointObserver:
    """严格区分本轮收到的遥信与重新读取的历史点表。"""

    def __init__(self, session, ioas, common_address):
        if (not isinstance(ioas, dict) or not ioas or
                any(not isinstance(key, str) or not key or type(value) is not int or not 0 <= value <= 0xffffff
                    for key, value in ioas.items()) or len(set(ioas.values())) != len(ioas)):
            raise ValueError("主站遥信IOA映射须为非空唯一名称和24位整数地址")
        self.session, self.ioas, self.common_address = session, dict(ioas), common_address

    def _read(self):
        snapshot = self.session.read_signals()
        matches = {key: [] for key in self.ioas}
        for row in snapshot["rows"]:
            ca, ioa, type_id, value, quality, protocol_time, _ = row["cells"]
            if int(ca) != self.common_address:
                continue
            for key, expected_ioa in self.ioas.items():
                if int(ioa) != expected_ioa:
                    continue
                if int(type_id) not in (1, 30) or value not in ("分", "合"):
                    raise RuntimeError(f"遥信{key}不是明确的单点BOOL值：类型{type_id}，值{value!r}")
                if int(quality, 16) & 0xF0:
                    raise RuntimeError(f"遥信{key}不是有效、当前且未经取代或封锁的值：{quality!r}")
                received_ms = int(row["received"])
                frame_id = int(row["frame_id"])
                if frame_id <= 0:
                    raise RuntimeError(f"遥信{key}缺少有效接收帧序号")
                if received_ms > time.time_ns() // 1_000_000 + 1000:
                    raise RuntimeError(f"遥信{key}接收时间超出本机时钟容差：{received_ms}")
                matches[key].append({"ioa": int(ioa), "value": value == "合", "quality": quality,
                                     "updated_at": datetime.fromtimestamp(received_ms / 1000).astimezone().isoformat(),
                                     "received_ms": received_ms, "frame_id": frame_id, "protocol_time": protocol_time,
                                     "raw_cells": row["cells"]})
        if any(len(rows) != 1 for rows in matches.values()):
            raise RuntimeError("遥信 IOA 必须在指定公共地址的点表中唯一存在")
        return {"identity": snapshot["identity"], "observed_at": snapshot["observed_at"],
                "points": {key: rows[0] for key, rows in matches.items()}}

    def checkpoint(self):
        result = self._read()
        result["wall_time"] = datetime.now().astimezone().isoformat()
        LOGGER.info("已记录104遥信业务基线：IOA=%s，点更新时间=%s", self.ioas,
                    {key: row["updated_at"] for key, row in result["points"].items()})
        return result

    def wait_state(self, expected, baseline, timeout, *, fresh_keys, sent_frame_id):
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
            last = self._read()
            if time.monotonic() >= deadline:
                break
            if last["identity"] != baseline["identity"]:
                raise RuntimeError("主站实例身份已变化，不能替代原业务现场")
            values_match = all(last["points"][key]["value"] == value for key, value in expected.items())
            fresh = all(last["points"][key]["frame_id"] > max(baseline["points"][key]["frame_id"], sent_frame_id)
                        and last["points"][key]["received_ms"] >= operation_boundary for key in fresh_keys)
            if values_match and fresh:
                LOGGER.info("104遥信值、品质及本轮变位确认通过：期望=%s，变位点=%s", expected, fresh_keys)
                return last
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise TimeoutError(f"104遥信未在{timeout}秒内满足值、品质和本轮更新时间：期望={expected!r}，最后观察={last!r}")
