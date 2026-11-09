"""AGC方向闭锁的固定期望与真实双端业务编排。"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import ipaddress
import json
import logging
import math
from pathlib import Path
import time
from urllib.parse import urlsplit


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgcDirectionProfile:
    group_name: str
    member_name: str
    conn_id: int
    manager_address: str
    master_host: str
    master_port: int
    common_address: int
    command_ioa: int
    increase_ioa: int
    decrease_ioa: int
    lower_kw: Decimal
    middle_kw: Decimal
    upper_kw: Decimal
    timeout_seconds: float

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("AGC验收配置字段不完整或含未知字段，请按示例填写")
        values = dict(data)
        if (not isinstance(values["group_name"], str) or not values["group_name"].startswith("e2e-agc-")
                or not isinstance(values["member_name"], str) or not values["member_name"].strip()):
            raise ValueError("AGC验收须明确e2e-agc-开头的专用组及成员名称")
        for name, maximum in (("conn_id", 0xffffffff), ("master_port", 65535), ("common_address", 65534),
                              ("command_ioa", 0xffffff), ("increase_ioa", 0xffffff), ("decrease_ioa", 0xffffff)):
            if type(values[name]) is not int or not 1 <= values[name] <= maximum:
                raise ValueError(f"AGC验收{name}须为范围内的正整数")
        if len({values[name] for name in ("command_ioa", "increase_ioa", "decrease_ioa")}) != 3:
            raise ValueError("AGC遥调与两个遥信IOA必须不同")
        try:
            values["master_host"] = str(ipaddress.ip_address(values["master_host"]))
            address = urlsplit("//" + values["manager_address"])
            if (not address.hostname or not address.port or address.username or address.password or address.path
                    or address.query or address.fragment or address.hostname != values["master_host"]):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError("ModuleManager地址须为同一测试下位机的IP:端口，不能混入路径或凭据") from None
        for name in ("lower_kw", "middle_kw", "upper_kw"):
            try:
                if isinstance(values[name], (bool, float)):
                    raise ValueError()
                values[name] = Decimal(values[name])
                if not values[name].is_finite():
                    raise ValueError()
            except (ValueError, TypeError, InvalidOperation):
                raise ValueError(f"AGC验收{name}须为有限十进制文本") from None
        if not values["lower_kw"] < values["middle_kw"] < values["upper_kw"]:
            raise ValueError("AGC固定期望必须满足下限 < 中间目标 < 上限")
        try:
            timeout = values["timeout_seconds"]
            if isinstance(timeout, bool) or not math.isfinite(float(timeout)) or not 5 <= float(timeout) <= 300:
                raise ValueError()
            values["timeout_seconds"] = float(timeout)
        except (ValueError, TypeError, OverflowError):
            raise ValueError("AGC验收等待时间须在5至300秒之间") from None
        return cls(**values)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.is_file():
            raise ValueError("缺少local/agc-direction-block.json，请按docs示例确认专用测试组和104地址")
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, UnicodeError):
            raise ValueError("AGC验收配置不是有效的UTF-8 JSON") from None

    @property
    def ioas(self):
        return {"increase": self.increase_ioa, "decrease": self.decrease_ioa}

    def page_profile(self):
        return {key: getattr(self, key) for key in ("group_name", "member_name", "conn_id", "lower_kw", "upper_kw")}

    def to_dict(self):
        return {key: str(value) if isinstance(value, Decimal) else value for key, value in asdict(self).items()}

    def expected(self, target):
        return {"increase": target >= self.upper_kw, "decrease": target <= self.lower_kw}


class AgcDirectionScenario:
    def __init__(self, desktop, page, master, observer, sender, profile):
        self.desktop, self.page, self.master = desktop, page, master
        self.observer, self.sender, self.profile = observer, sender, profile
        self.observations = []
        self.desktop.metadata["AGC固定验收配置"] = profile.to_dict()

    def _wait_current(self, expected):
        deadline = time.monotonic() + self.profile.timeout_seconds
        last = None
        while time.monotonic() < deadline:
            last = self.observer.checkpoint()
            if all(last["points"][key]["value"] == value for key, value in expected.items()):
                return last
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise TimeoutError(f"准备阶段主站遥信未满足固定期望：预期{expected}，最后观察{last}")

    def _record(self, row):
        self.observations.append(row)
        self.desktop.metadata["AGC双端观察"] = self.observations
        self._save()

    def _save(self):
        self.desktop.artifacts.mkdir(parents=True, exist_ok=True)
        (self.desktop.artifacts / "agc-direction-observations.json").write_text(
            json.dumps(self.observations, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def _preserve_failure(self, error, row=None):
        try:
            self._record(row) if row is not None else self._save()
        except Exception as evidence_error:
            message = f"AGC失败取证保存异常：{evidence_error}"
            self.desktop.metadata.setdefault("AGC取证错误", []).append(message)
            error.add_note(message)
            LOGGER.exception("AGC失败观察记录保存异常，保留原始错误")

    def _apply(self, target, *, fresh_key=None, stage):
        expected = self.profile.expected(target)
        row = {"阶段": stage, "固定目标kW": str(target), "固定闭锁期望": expected,
               "配置": self.profile.to_dict()}
        try:
            with self.master.step(f"核对104目标并单次下发AGC目标 {target} kW", expected=expected):
                self.master.verify_target(self.profile.master_host, self.profile.master_port,
                                          self.profile.common_address)
                baseline = self.observer.checkpoint()
                row["基线"] = baseline
                if fresh_key and baseline["points"][fresh_key]["value"] == expected[fresh_key]:
                    raise AssertionError("业务操作前闭锁已是最终期望，不能证明本次变位")
                row["发送结果"] = self.sender.send_target_kw(target)
                # 使用主站实际发送时刻划定本轮接收边界。
                baseline["wall_time"] = row["发送结果"]["sent_at"]
            with self.desktop.step(f"核对AGC当前已发布目标与闭锁 {target} kW", expected=expected):
                row["upper"] = self.page.wait_snapshot(self.profile.page_profile(), target,
                                                        expected["increase"], expected["decrease"],
                                                        self.profile.timeout_seconds)
            with self.master.step("核对主站闭锁值、品质与本轮变位", expected=expected):
                changed = (fresh_key,) if fresh_key else tuple(
                    key for key, value in expected.items() if baseline["points"][key]["value"] != value)
                if changed:
                    row["master"] = self.observer.wait_state(
                        expected, baseline, self.profile.timeout_seconds,
                        fresh_keys=changed, sent_frame_id=row["发送结果"]["sent_frame_id"])
                else:
                    row["master"] = self._wait_current(expected)
            row["状态"] = "通过"
            LOGGER.info("AGC闭锁双端步骤完成：阶段=%s，目标=%s，期望=%s", stage, target, expected)
            self._record(row)
            return row
        except BaseException as error:
            row["状态"], row["错误"] = "失败", f"{type(error).__name__}：{error}"
            # 取证失败不得替换原业务失败，不下发任何恢复或重试命令。
            self._preserve_failure(error, row)
            raise

    def run_case(self, prepare, target, changed_key, *, require_lag=False):
        if changed_key not in self.profile.ioas:
            raise ValueError("AGC验收必须明确增闭锁或减闭锁变位点")
        prepared = self._apply(prepare, stage="独立准备")
        if require_lag:
            try:
                measured = prepared["upper"]["member"]["measurement"]
                self.desktop.check("实测滞后场景前置：实测严格位于调节区间内部",
                                   self.profile.lower_kw < measured < self.profile.upper_kw, True)
            except BaseException as error:
                self._preserve_failure(error, {"阶段": "实测滞后前置条件", "状态": "失败", "错误": str(error),
                                                "最后观察": prepared["upper"]})
                raise
        result = self._apply(target, fresh_key=changed_key, stage="业务验收")
        # 主站变位完成后再读同一AGC总览，不能用很早以前的实测值证明滞后。
        if require_lag:
            try:
                expected = self.profile.expected(target)
                with self.desktop.step("确认增闭锁已置位时实测仍未达到上限"):
                    deadline = time.monotonic() + self.profile.timeout_seconds
                    before_measurement = self._measurement_time(prepared["upper"]["member"]["measurement_time"])
                    boundary = datetime.fromisoformat(result["发送结果"]["attempted_at"]).replace(microsecond=0)
                    fresh_measurement = False
                    while time.monotonic() < deadline:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        final = self.page.wait_snapshot(self.profile.page_profile(), target,
                                                        expected["increase"], expected["decrease"], remaining)
                        result["滞后最终观察"] = final
                        measured_at = self._measurement_time(final["member"]["measurement_time"])
                        if measured_at > datetime.now().astimezone() + timedelta(seconds=1):
                            raise AssertionError("实测时间超出本机时钟容差，不能证明实时滞后")
                        if measured_at > before_measurement and measured_at >= boundary:
                            self.desktop.check("实测未到上限仍增闭锁", final["member"]["measurement"] < self.profile.upper_kw, True)
                            fresh_measurement = True
                            break
                        self.desktop._check_stop()
                        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                    if not fresh_measurement:
                        raise TimeoutError("未取得本轮发送后的新实测，不能用旧GOOD缓存证明滞后")
            except BaseException as error:
                result["状态"], result["错误"] = "失败", f"{type(error).__name__}：{error}"
                self._preserve_failure(error)
                raise
            self._save()
        return result

    @staticmethod
    def _measurement_time(text):
        normalized = str(text).strip().replace("/", "-").replace("年", "-").replace("月", "-").replace("日", " ")
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return datetime.strptime(normalized, pattern).astimezone()
            except ValueError:
                pass
        raise AssertionError(f"实测时间不可解析：{text!r}")
