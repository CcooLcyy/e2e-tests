"""从真实主站可见遥信表判定值、品质及本轮变位；不发送任何协议命令。"""
from datetime import datetime, timedelta
import logging
import math
import re
import time


COLUMNS = ("序号", "信息地址", "信息描述", "值", "品质描述", "更新时间")
LOGGER = logging.getLogger(__name__)


def _ioa(text):
    value = str(text).strip()
    if re.fullmatch(r"[0-9a-fA-F]+[hH]", value):
        return int(value[:-1], 16)
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", value):
        return int(value, 16)
    if re.fullmatch(r"[0-9]+", value):
        return int(value, 10)
    raise RuntimeError(f"遥信信息地址无法解析：{value!r}")


def _stamp(text):
    value = str(text).strip()
    if not re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}", value):
        raise RuntimeError(f"遥信更新时间缺少完整日期与时分秒：{value!r}")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        try:
            result = datetime.strptime(value, "%Y/%m/%d %H:%M:%S")
        except ValueError:
            raise RuntimeError(f"遥信更新时间无法解析：{value!r}") from None
    # 主站与测试进程在同一台Windows机器；无时区的界面时间按本机时区解释。
    return result.astimezone()


def _good(text):
    value = str(text).strip()
    remaining = value
    for phrase in ("未被取代", "未取代", "未被封锁", "未封锁", "未被闭锁", "未闭锁"):
        remaining = remaining.replace(phrase, "")
    if any(word in remaining for word in ("无效", "非当前", "旧值", "被取代", "被封锁", "被闭锁", "未知")):
        return False
    return ("有效" in value and "当前值" in value
            and any(word in value for word in ("未被取代", "未取代"))
            and any(word in value for word in ("未被封锁", "未封锁", "未被闭锁", "未闭锁")))


class Master104PointObserver:
    """严格区分本轮收到的遥信与重新读取的历史点表。"""

    def __init__(self, session, ioas):
        if (not isinstance(ioas, dict) or not ioas or
                any(not isinstance(key, str) or not key or type(value) is not int or not 0 <= value <= 0xffffff
                    for key, value in ioas.items()) or len(set(ioas.values())) != len(ioas)):
            raise ValueError("主站遥信IOA映射须为非空唯一名称和24位整数地址")
        self.session, self.ioas = session, dict(ioas)

    def _read(self):
        self.session._check_stop()
        snapshot = self.session.snapshot(table_scope="signals")
        if snapshot.get("partial") or not snapshot.get("identity"):
            raise RuntimeError("主站快照不完整或缺少实例身份，不能判定遥信")
        tables = [table for table in snapshot.get("tables", []) if table.get("visible") is True]
        statuses = []
        pause_states = []
        for table in tables:
            for row in table.get("rows", []):
                if len(row) == 2 and str(row[0]).strip() == "连接状态":
                    if table.get("partial"):
                        raise RuntimeError("主站连接状态表读取不完整")
                    statuses.append(str(row[1]).strip())
                if len(row) == 2 and str(row[0]).strip() == "暂停状态":
                    if table.get("partial"):
                        raise RuntimeError("主站暂停状态表读取不完整")
                    pause_states.append(str(row[1]).strip())
        for control in snapshot.get("controls", []):
            texts = control.get("texts", [])
            if len(texts) == 2 and str(texts[0]).strip() == "连接状态":
                statuses.append(str(texts[1]).strip())
            if len(texts) == 2 and str(texts[0]).strip() == "暂停状态":
                pause_states.append(str(texts[1]).strip())
        if not statuses or any(status not in ("正常", "连接", "已连接") for status in statuses):
            raise RuntimeError(f"主站尚未确认正常连接：{statuses!r}")
        if not pause_states or any(state not in ("正常", "未暂停") for state in pause_states):
            raise RuntimeError(f"主站尚未确认未暂停：{pause_states!r}")
        candidates = [table for table in tables if table.get("panel_title") == "遥信信息"
                      and tuple(str(col).strip() for col in table.get("columns", [])) == COLUMNS]
        if len(candidates) != 1 or candidates[0].get("partial"):
            raise RuntimeError("必须完整读取唯一可见的六列遥信表")
        matches = {key: [] for key in self.ioas}
        for row in candidates[0].get("rows", []):
            if len(row) != len(COLUMNS):
                raise RuntimeError("主站遥信表行缺少完整单元格")
            address = _ioa(row[1])
            for key, ioa in self.ioas.items():
                if address != ioa:
                    continue
                if str(row[3]).strip() not in ("分", "合", "0", "1", "false", "true"):
                    raise RuntimeError(f"遥信{key}不是明确的单点BOOL值：{row[3]!r}")
                if not _good(row[4]):
                    raise RuntimeError(f"遥信{key}品质不是完整有效当前值：{row[4]!r}")
                stamp = _stamp(row[5])
                if stamp > datetime.now().astimezone() + timedelta(seconds=1):
                    raise RuntimeError(f"遥信{key}更新时间超出本机时钟容差：{row[5]!r}")
                matches[key].append({"ioa": ioa, "description": row[2],
                                     "value": str(row[3]).strip() in ("合", "1", "true"),
                                     "quality": row[4], "updated_at": stamp.isoformat(),
                                     "raw_cells": list(row)})
        if any(len(rows) != 1 for rows in matches.values()):
            raise RuntimeError("遥信IOA必须在当前表中唯一存在，不能使用缺失或重复点")
        return {"identity": snapshot["identity"], "observed_at": snapshot.get("observed_at"),
                "connection_status": statuses, "pause_status": pause_states,
                "points": {key: rows[0] for key, rows in matches.items()}}

    def checkpoint(self):
        result = self._read()
        result["wall_time"] = datetime.now().astimezone().isoformat()
        LOGGER.info("已记录104遥信业务基线：IOA=%s，点更新时间=%s", self.ioas,
                    {key: row["updated_at"] for key, row in result["points"].items()})
        return result

    def wait_state(self, expected, baseline, timeout, *, fresh_keys):
        if (not isinstance(expected, dict) or not expected or any(key not in self.ioas or type(value) is not bool
                                                                 for key, value in expected.items())
                or not fresh_keys or any(key not in expected for key in fresh_keys)):
            raise ValueError("遥信期望须使用已配置的BOOL点，且明确本轮需变位的点")
        if isinstance(timeout, bool) or not math.isfinite(float(timeout)) or float(timeout) <= 0:
            raise ValueError("遥信业务等待时间须为有限正数")
        if (not isinstance(baseline, dict) or set(baseline.get("points", {})) != set(self.ioas)
                or any(baseline["points"][key].get("ioa") != ioa for key, ioa in self.ioas.items())):
            raise ValueError("遥信基线与当前IOA映射不一致")
        operation_boundary = _stamp(baseline["wall_time"]).replace(microsecond=0)
        deadline = time.monotonic() + float(timeout)
        last = None
        while time.monotonic() < deadline:
            last = self._read()
            if time.monotonic() >= deadline:
                break
            if last["identity"] != baseline["identity"]:
                raise RuntimeError("主站实例身份已变化，不能替代原业务现场")
            values_match = all(last["points"][key]["value"] == value for key, value in expected.items())
            fresh = all(_stamp(last["points"][key]["updated_at"]) > _stamp(baseline["points"][key]["updated_at"])
                        and _stamp(last["points"][key]["updated_at"]) >= operation_boundary for key in fresh_keys)
            if values_match and fresh:
                LOGGER.info("104遥信值、品质及本轮变位确认通过：期望=%s，变位点=%s", expected, fresh_keys)
                return last
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise TimeoutError(f"104遥信未在{timeout}秒内满足值、品质和本轮更新时间：期望={expected!r}，最后观察={last!r}")
