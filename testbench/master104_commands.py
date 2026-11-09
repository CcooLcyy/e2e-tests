"""从 Msk104Master 界面发送单次短浮点直接执行命令。"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import logging
import struct
import sys

from selenium.webdriver.common.by import By


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
    def __init__(self, session, common_address, command_ioa):
        if (type(common_address) is not int or not 1 <= common_address <= 65534
                or type(command_ioa) is not int or not 0 <= command_ioa <= 0xffffff):
            raise ValueError("主站公共地址须为1～65534，遥调IOA须为24位整数")
        self.session, self.common_address, self.command_ioa = session, common_address, command_ioa

    def send_target_kw(self, target, timeout=None):
        value = normalize_target(target)
        session = self.session
        request = {"identity": session.identity, "common_address": self.common_address,
                   "command_ioa": self.command_ioa, "target_kw": value}
        record = {"requested_at": datetime.now().astimezone().isoformat(), "request": request,
                  "result": "未取得发送结果，不表示下游执行成功"}
        session.artifacts.mkdir(parents=True, exist_ok=True)
        path = session.artifacts / ("master104-command-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
        try:
            with session.step(f"主站单次直接执行遥调 {value} kW", expected=f"CA={self.common_address}, IOA={self.command_ioa}, C_SE_NC_1"):
                session._assert_live_display()
                ca = session._connection_fields()[2].get_property("value")
                if ca != str(self.common_address):
                    raise RuntimeError(f"遥调公共地址与当前连接不一致：{ca!r}")
                driver = session.driver
                driver.find_element(By.ID, "tab-setpoints").click()

                def read_result():
                    panel = driver.find_element(By.ID, "setpoint-result")
                    return {name: panel.get_dom_attribute(f"data-{name}") for name in
                            ("operation-id", "state", "ioa", "common-address", "value", "sent-at", "sent-frame-id")}

                def enabled_button():
                    button = driver.find_element(By.ID, "send-setpoint")
                    return button if button.is_enabled() else False

                button = session.wait_for(enabled_button, "主站未开放本次遥调发送操作", timeout)
                session.fill(driver.find_element(By.ID, "setpoint-ioa"), str(self.command_ioa))
                session.fill(driver.find_element(By.ID, "setpoint-value"), value)
                previous = read_result()["operation-id"]
                session._check_stop()
                attempted_at = datetime.now().astimezone().isoformat()
                record["attempted_at"] = attempted_at
                button.click()

                def confirmed():
                    session._assert_live_display()
                    operation = read_result()
                    operation_id, state = operation["operation-id"], operation["state"]
                    if not operation_id or operation_id == previous:
                        return False
                    record["observed_operation"] = operation
                    if (operation["ioa"] != str(self.command_ioa)
                            or operation["common-address"] != str(self.common_address)
                            or struct.pack("<f", float(operation["value"])) != struct.pack("<f", float(value))):
                        raise RuntimeError("主站本次遥调结果与目标参数不一致")
                    if state in ("rejected", "failed", "timeout"):
                        raise RuntimeError(f"104 遥调未获得肯定确认：{state}")
                    if state in ("confirmed", "terminated"):
                        sent_at = int(operation["sent-at"])
                        sent_frame_id = int(operation["sent-frame-id"])
                        if sent_at < int(datetime.fromisoformat(attempted_at).timestamp() * 1000) or sent_frame_id <= 0:
                            raise RuntimeError("主站缺少本次遥调的有效发送证据")
                        return {**request, "operation_id": operation_id, "state": state, "attempted_at": attempted_at,
                                "sent_at": datetime.fromtimestamp(sent_at / 1000).astimezone().isoformat(),
                                "sent_frame_id": sent_frame_id}
                    return False

                result = session.wait_for(confirmed, "104 主站未收到本次遥调的肯定确认", timeout)
                record["result"] = result
                LOGGER.info("已收到主站本次遥调的肯定确认：CA=%s，IOA=%s，目标=%s；仍需核对AGC设点和新遥信",
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
