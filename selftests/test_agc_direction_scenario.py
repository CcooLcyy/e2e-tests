"""AGC业务编排的独立契约，不连接设备、不发送真实遥调。"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from case_support.agc_direction_scenario import AgcDirectionProfile, AgcDirectionScenario


def profile_data():
    return {"master_host": "192.0.2.1", "master_port": 2404,
            "common_address": 1, "command_ioa": 16385, "increase_ioa": 1, "decrease_ioa": 2,
            "measured_ioa": 3,
            "lower_kw": "10", "middle_kw": "50", "upper_kw": "100", "timeout_seconds": 30}


@pytest.mark.parametrize("changes", [
    {"lower_kw": "50"}, {"upper_kw": "NaN"}, {"increase_ioa": 2},
    {"command_ioa": True}, {"measured_ioa": 2}, {"master_port": 0},
    {"measured_ioa": -1}, {"master_host": ""}, {"timeout_seconds": float("inf")},
])
def test_profile_rejects_ambiguous_or_invalid_control_targets(changes):
    """错误边界、重复地址、无效连接目标及无效等待参数在任何操作前拒绝。"""
    data = profile_data() | changes
    with pytest.raises(ValueError):
        AgcDirectionProfile.from_dict(data)


def test_profile_keeps_fixed_expectations_and_ignores_no_runtime_values():
    """期望来自显式测试配置，不从被测系统返回的上下限反推或修正。"""
    profile = AgcDirectionProfile.from_dict(profile_data())
    assert profile.lower_kw == Decimal("10")
    assert profile.middle_kw == Decimal("50")
    assert profile.upper_kw == Decimal("100")
    assert profile.ioas == {"increase": 1, "decrease": 2}


def test_legacy_profile_keeps_104_parameters_without_using_desktop_fields():
    data = profile_data() | {"group_name": "e2e-agc-direction", "member_name": "test-inverter",
                             "conn_id": 12, "manager_address": "192.0.2.1:17000"}
    del data["measured_ioa"]
    profile = AgcDirectionProfile.from_dict(data)
    assert profile.master_host == "192.0.2.1"
    assert profile.measured_ioa is None
    assert "manager_address" not in profile.to_dict()


class Step:
    def __enter__(self):
        return None

    def __exit__(self, *_):
        return False


class Session:
    def __init__(self, directory):
        self.artifacts, self.metadata, self.steps = Path(directory), {}, []

    def step(self, name, **kwargs):
        self.steps.append({"步骤": name})
        return Step()

    def check(self, name, actual, expected):
        assert actual == expected, name

    def capture(self, *args, **kwargs):
        return None

    def _check_stop(self):
        pass

    def verify_target(self, host, port, common_address):
        assert (host, port, common_address) == ("192.0.2.1", 2404, 1)


class Observer:
    def __init__(self, measurement, failure):
        self.expected = {"increase": False, "decrease": False}
        self.waits = []
        self.sent_frames = []
        self.measurement = measurement
        self.failure = failure

    def checkpoint(self, *, include_measurement=False):
        result = {"points": {key: {"value": value} for key, value in self.expected.items()}}
        if include_measurement:
            result["measurement"] = {"value_kw": self.measurement}
        return result

    def wait_state(self, expected, baseline, timeout, *, fresh_keys, sent_frame_id, measurement_below):
        if self.failure:
            raise self.failure
        self.waits.append((deepcopy(expected), tuple(fresh_keys)))
        self.sent_frames.append(sent_frame_id)
        self.expected = dict(expected)
        return self.checkpoint(include_measurement=measurement_below is not None)


def scenario(tmp_path, *, failure=None, measurement="20", initially_blocked=False):
    master, observer = Session(tmp_path), Observer(measurement, failure)
    observer.expected["increase"] = initially_blocked
    sent = []
    class Sender:
        def send_target_kw(self, value):
            sent.append(value)
            observer.expected = {"increase": value == Decimal("100"), "decrease": value == Decimal("10")}
            sent_at = datetime.now().astimezone().isoformat()
            return {"requested_kw": str(value), "sent_at": sent_at, "attempted_at": sent_at,
                    "sent_frame_id": len(sent)}
    runner = AgcDirectionScenario(master, observer, Sender(), AgcDirectionProfile.from_dict(profile_data()))
    return runner, sent, observer


def test_upper_transition_sends_each_target_once_and_requires_fresh_increment(tmp_path):
    """准备中间值后只发送一次上限目标，业务判定必须要求增闭锁的新变位。"""
    runner, sent, observer = scenario(tmp_path)
    result = runner.run_case(Decimal("50"), Decimal("100"), "increase")
    assert sent == [Decimal("50"), Decimal("100")]
    assert observer.waits[-1] == ({"increase": True, "decrease": False}, ("increase",))
    assert observer.sent_frames == [2]
    assert result["master"]["points"]["increase"]["value"] is True


def test_failure_is_preserved_without_retry_or_restoring_command(tmp_path):
    """准备失败时不继续发送最终目标，也不发送恢复指令掩盖失败。"""
    runner, sent, _ = scenario(tmp_path, failure=TimeoutError("未收到新遥信"), initially_blocked=True)
    with pytest.raises(TimeoutError, match="未收到新遥信"):
        runner.run_case(Decimal("50"), Decimal("100"), "increase")
    assert sent == [Decimal("50")]
    assert "未收到新遥信" in (tmp_path / "agc-direction-observations.json").read_text(encoding="utf-8")


def test_measurement_at_limit_cannot_pass_the_lagging_measurement_case(tmp_path):
    """实测已到上限时不得将普通到限场景改判为实测滞后场景通过。"""
    runner, sent, _ = scenario(tmp_path, measurement="100")
    with pytest.raises(AssertionError, match="实测"):
        runner.run_case(Decimal("50"), Decimal("100"), "increase", require_lag=True)
    assert sent == [Decimal("50")]


def test_lagging_case_records_observed_measurement_with_closure(tmp_path):
    """实测滞后场景保留最终104遥信和实测遥测证据。"""
    runner, sent, _ = scenario(tmp_path)
    result = runner.run_case(Decimal("50"), Decimal("100"), "increase", require_lag=True)
    assert sent == [Decimal("50"), Decimal("100")]
    assert result["master"]["points"]["increase"]["value"] is True
    assert result["master"]["measurement"]["value_kw"] == "20"


def test_lag_precondition_failure_survives_evidence_write_failure(tmp_path, monkeypatch):
    """实测前置断言失败和取证写盘同时失败时，保留原始业务断言。"""
    runner, sent, _ = scenario(tmp_path, measurement="100")
    original = runner._record
    def fail_only_failure_record(row):
        if row["状态"] == "失败":
            raise OSError("模拟取证目录写入失败")
        return original(row)
    monkeypatch.setattr(runner, "_record", fail_only_failure_record)
    with pytest.raises(AssertionError, match="实测"):
        runner.run_case(Decimal("50"), Decimal("100"), "increase", require_lag=True)
    assert sent == [Decimal("50")]
