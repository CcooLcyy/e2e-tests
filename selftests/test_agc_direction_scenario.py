"""AGC业务编排的独立契约，不连接设备、不发送真实遥调。"""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from testbench.agc_direction_scenario import AgcDirectionProfile, AgcDirectionScenario


def profile_data():
    return {"group_name": "e2e-agc-direction", "member_name": "test-inverter", "conn_id": 12,
            "manager_address": "192.0.2.1:17000", "master_host": "192.0.2.1", "master_port": 2404,
            "common_address": 1, "command_ioa": 16385, "increase_ioa": 1, "decrease_ioa": 2,
            "lower_kw": "10", "middle_kw": "50", "upper_kw": "100", "timeout_seconds": 30}


@pytest.mark.parametrize("changes", [
    {"lower_kw": "50"}, {"upper_kw": "NaN"}, {"increase_ioa": 2},
    {"command_ioa": True}, {"conn_id": 0}, {"master_port": 0},
    {"group_name": "production"}, {"manager_address": ""}, {"timeout_seconds": float("inf")},
])
def test_profile_rejects_ambiguous_or_invalid_control_targets(changes):
    """错误边界、重复地址、未知测试组及无效等待参数在任何操作前拒绝。"""
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

    def snapshot(self, **kwargs):
        return {"tables": [{"rows": [["目的地址", "192.0.2.1"], ["目的端口", "2404"]]}]}


class Observer:
    def __init__(self):
        self.expected = {"increase": False, "decrease": False}
        self.waits = []

    def checkpoint(self):
        stamp = (datetime.now().astimezone() - timedelta(seconds=3)).isoformat()
        return {"wall_time": stamp, "points": {key: {"value": value, "updated_at": stamp}
                                               for key, value in self.expected.items()}}

    def wait_state(self, expected, baseline, timeout, *, fresh_keys):
        self.waits.append((deepcopy(expected), tuple(fresh_keys)))
        self.expected = dict(expected)
        return self.checkpoint()


def scenario(tmp_path, *, fail_upper=False, measurement="20"):
    desktop, master, observer = Session(tmp_path), Session(tmp_path), Observer()
    sent = []
    class Sender:
        def send_target_kw(self, value):
            sent.append(value)
            observer.expected = {"increase": value == Decimal("100"), "decrease": value == Decimal("10")}
            return {"requested_kw": str(value), "attempted_at": datetime.now().astimezone().isoformat()}
    class Page:
        def wait_snapshot(self, profile, target, increase, decrease, timeout):
            if fail_upper:
                raise AssertionError("被测系统设定值未更新")
            return {"member": {"setpoint": target, "measurement": Decimal(measurement)},
                    "increase": increase, "decrease": decrease}
    runner = AgcDirectionScenario(desktop, Page(), master, observer, Sender(),
                                  AgcDirectionProfile.from_dict(profile_data()))
    # 仅替换跨显示秒等待，测试契约不产生真实等待或窗口操作。
    runner._cross_timestamp_boundary = lambda baseline: None
    return runner, sent, observer


def test_upper_transition_sends_each_target_once_and_requires_fresh_increment(tmp_path):
    """准备中间值后只发送一次上限目标，业务判定必须要求增闭锁的新变位。"""
    runner, sent, observer = scenario(tmp_path)
    result = runner.run_case(Decimal("50"), Decimal("100"), "increase")
    assert sent == [Decimal("50"), Decimal("100")]
    assert observer.waits[-1] == ({"increase": True, "decrease": False}, ("increase",))
    assert result["upper"]["member"]["setpoint"] == Decimal("100")


def test_failure_is_preserved_without_retry_or_restoring_command(tmp_path):
    """准备失败时不继续发送最终目标，也不发送恢复指令掩盖失败。"""
    runner, sent, _ = scenario(tmp_path, fail_upper=True)
    with pytest.raises(AssertionError, match="设定值未更新"):
        runner.run_case(Decimal("50"), Decimal("100"), "increase")
    assert sent == [Decimal("50")]
    assert "设定值未更新" in (tmp_path / "agc-direction-observations.json").read_text(encoding="utf-8")


def test_measurement_at_limit_cannot_pass_the_lagging_measurement_case(tmp_path):
    """实测已到上限时不得将普通到限场景改判为实测滞后场景通过。"""
    runner, sent, _ = scenario(tmp_path, measurement="100")
    with pytest.raises(AssertionError, match="实测"):
        runner.run_case(Decimal("50"), Decimal("100"), "increase", require_lag=True)
    assert sent == [Decimal("50")]


def test_lagging_case_rejects_old_good_measurement_after_fresh_block(tmp_path, monkeypatch):
    """即使本轮闭锁更新正确，昨天的GOOD量测也不能证明当前实测尚未到限。"""
    from itertools import count
    runner, sent, _ = scenario(tmp_path)
    original = runner.page.wait_snapshot
    def old_measurement(*args):
        observed = original(*args)
        observed["member"]["measurement_time"] = (datetime.now() - timedelta(days=1)).strftime("%Y/%m/%d %H:%M:%S")
        return observed
    runner.page.wait_snapshot = old_measurement
    ticks = count(0, 10)
    monkeypatch.setattr("testbench.agc_direction_scenario.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("testbench.agc_direction_scenario.time.sleep", lambda *_: None)
    with pytest.raises(TimeoutError, match="新实测"):
        runner.run_case(Decimal("50"), Decimal("100"), "increase", require_lag=True)
    assert sent == [Decimal("50"), Decimal("100")]


def test_lag_precondition_failure_survives_evidence_write_failure(tmp_path, monkeypatch):
    """实测前置断言失败和取证写盘同时失败时，保留原始业务断言。"""
    runner, sent, _ = scenario(tmp_path, measurement="100")
    original = runner._record
    def fail_only_failure_record(row):
        if row["阶段"] == "实测滞后前置条件":
            raise OSError("模拟取证目录写入失败")
        return original(row)
    monkeypatch.setattr(runner, "_record", fail_only_failure_record)
    with pytest.raises(AssertionError, match="实测"):
        runner.run_case(Decimal("50"), Decimal("100"), "increase", require_lag=True)
    assert sent == [Decimal("50")]
