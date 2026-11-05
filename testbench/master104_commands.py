"""通过真实ProIEC104Client设值对话框发送单次短浮点直接执行命令。"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import logging
import struct
import sys

from .master104_native import run_helper


LOGGER = logging.getLogger(__name__)


def normalize_target(value):
    """固定验收目标必须可由C_SE_NC_1精确表示，不静默量化。"""
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value))
        encoded = struct.unpack("<f", struct.pack("<f", float(number)))[0]
        if not number.is_finite() or Decimal.from_float(encoded) != number:
            raise ValueError()
    except (InvalidOperation, ValueError, OverflowError, struct.error):
        raise ValueError("AGC目标必须为可精确表示的有限32位浮点数") from None
    return format(number, "f")


class Master104CommandSender:
    """只支持直接执行；不自动尝试SBO、不在任何异常后重发。"""
    def __init__(self, session, common_address, command_ioa, *, runner=None):
        if (type(common_address) is not int or not 1 <= common_address <= 65535
                or type(command_ioa) is not int or not 0 <= command_ioa <= 0xffffff):
            raise ValueError("主站公共地址须为1～65535，遥调IOA须为24位整数")
        self.session, self.common_address, self.command_ioa = session, common_address, command_ioa
        self.runner = runner or run_helper

    def send_target_kw(self, target, timeout=None):
        value = normalize_target(target)
        session = self.session
        session._check_stop()
        if session._closed or not session.identity:
            raise RuntimeError("发送遥调前须接管已确认身份的主站会话")
        request = {"action": "send_target_kw", "path": session.path, "backend": session.backend,
                   "identity": dict(session.identity), "common_address": self.common_address,
                   "command_ioa": self.command_ioa, "target_kw": value}
        record = {"requested_at": datetime.now().astimezone().isoformat(), "request": request,
                  "result": "未取得发送结果，不表示下游执行成功"}
        session.artifacts.mkdir(parents=True, exist_ok=True)
        path = session.artifacts / ("master104-command-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
        try:
            with session.step(f"主站单次直接执行遥调 {value} kW", expected=f"CA={self.common_address}, IOA={self.command_ioa}, C_SE_NC_1"):
                result = self.runner(request, session.timeout if timeout is None else timeout,
                                     helper_module="testbench.master104_command_helper")
                if (result.get("identity") != request["identity"] or result.get("target_kw") != value
                        or result.get("send_attempted") is not True):
                    raise RuntimeError("主站遥调返回身份或参数不一致，发送结果未知，不得重试")
                record["result"] = result
                LOGGER.info("已完成主站单次发送动作：CA=%s，IOA=%s，目标=%s；仍需核对AGC设点和新遥信",
                            self.common_address, self.command_ioa, value)
                return result
        except BaseException as error:
            record["error"] = f"{type(error).__name__}：{error}"
            raise
        finally:
            original = sys.exc_info()[1]
            try:
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                session.metadata.setdefault("主站遥调请求证据", []).append(path.name)
            except Exception as evidence_error:
                message = f"主站遥调请求证据保存失败：{type(evidence_error).__name__}：{evidence_error}"
                session.metadata.setdefault("主站遥调取证错误", []).append(message)
                LOGGER.exception("主站遥调请求证据保存失败，保留原始发送异常且不重发")
                if original is None:
                    raise
                original.add_note(message)
