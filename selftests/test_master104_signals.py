"""验证真实主站界面快照到 AGC 遥信证据的解释规则。"""
from datetime import datetime
from decimal import Decimal
from itertools import count
import time
from types import SimpleNamespace

import pytest

from testbench.master104_signals import Master104PointObserver


def signal(ioa, received, frame, *, ca=1, value="分", quality="0x00", type_id=1):
    return {"received": str(received), "frame_id": str(frame),
            "cells": [str(ca), str(ioa), str(type_id), value, quality, "—", "12:00:00"]}


def snapshot(*rows, pid=10):
    return {"identity": {"pid": pid, "path": "msk104master.exe"},
            "observed_at": datetime.now().astimezone().isoformat(), "rows": rows}


def observer(*snapshots):
    pending = iter(snapshots)
    return Master104PointObserver(SimpleNamespace(read_signals=lambda: next(pending)),
                                  {"increase": 1, "decrease": 2}, 1)


def test_accepts_new_receive_frame_even_in_same_millisecond_as_send():
    now = time.time_ns() // 1_000_000
    companion = signal(2, now - 30, 2)
    point = observer(snapshot(signal(1, now - 10, 3), companion),
                     snapshot(signal(1, now, 7, value="合", quality="0x01", type_id=30), companion))
    baseline = point.checkpoint()
    baseline["wall_time"] = datetime.fromtimestamp(now / 1000).astimezone().isoformat()
    result = point.wait_state({"increase": True, "decrease": False}, baseline, 2,
                              fresh_keys=("increase",), sent_frame_id=6)
    assert result["points"]["increase"]["frame_id"] == 7
    assert result["points"]["decrease"] == baseline["points"]["decrease"]


@pytest.mark.parametrize(("quality", "type_id", "value", "message"), [
    ("0x81", 1, "合", "有效"),
    ("0x40", 1, "分", "当前"),
    ("0x20", 1, "分", "取代"),
    ("0x10", 1, "分", "封锁"),
    ("0x00", 3, "分", "单点"),
    ("0x00", 1, "未知", "单点"),
])
def test_checkpoint_rejects_bad_quality_or_non_single_point(quality, type_id, value, message):
    now = time.time_ns() // 1_000_000
    point = observer(snapshot(signal(1, now, 1, quality=quality, type_id=type_id, value=value),
                              signal(2, now, 2)))
    with pytest.raises(RuntimeError, match=message):
        point.checkpoint()


def test_missing_duplicate_and_wrong_common_address_points_are_rejected():
    now = time.time_ns() // 1_000_000
    increase = signal(1, now, 1)
    decrease = signal(2, now, 2)
    for rows in ((increase,), (increase, decrease, decrease), (signal(1, now, 3, ca=2), decrease)):
        with pytest.raises(RuntimeError, match="唯一存在"):
            observer(snapshot(*rows)).checkpoint()
    result = observer(snapshot(signal(1, now, 3, ca=2, value="合"), increase, decrease)).checkpoint()
    assert result["points"]["increase"]["value"] is False


def test_instance_change_is_rejected():
    now = time.time_ns() // 1_000_000
    companion = signal(2, now, 1)
    point = observer(snapshot(signal(1, now, 2), companion),
                     snapshot(signal(1, now, 4, value="合"), companion, pid=20))
    baseline = point.checkpoint()
    with pytest.raises(RuntimeError, match="实例身份"):
        point.wait_state({"increase": True}, baseline, 2, fresh_keys=("increase",), sent_frame_id=3)


@pytest.mark.parametrize("reason", ["unchanged-frame", "before-send-frame", "before-send-time"])
def test_new_dom_snapshot_does_not_turn_old_receive_into_a_fresh_point(monkeypatch, reason):
    now = time.time_ns() // 1_000_000
    companion = signal(2, now - 30, 1)
    baseline_row = signal(1, now - 20, 2)
    frame = {"unchanged-frame": 2, "before-send-frame": 3, "before-send-time": 6}[reason]
    received = now - 2 if reason == "before-send-time" else now
    point = observer(snapshot(baseline_row, companion),
                     snapshot(signal(1, received, frame, value="合"), companion))
    baseline = point.checkpoint()
    baseline["wall_time"] = datetime.fromtimestamp(now / 1000).astimezone().isoformat()
    ticks = count(0, 1)
    monkeypatch.setattr("testbench.master104_signals.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("testbench.master104_signals.time.sleep", lambda _: None)
    with pytest.raises(TimeoutError, match="本轮更新时间"):
        point.wait_state({"increase": True}, baseline, 2.5, fresh_keys=("increase",), sent_frame_id=5)


@pytest.mark.parametrize(("offset", "frame", "message"), [(5000, 1, "时钟"), (0, 0, "帧序号")])
def test_checkpoint_rejects_impossible_receive_evidence(offset, frame, message):
    now = time.time_ns() // 1_000_000
    point = observer(snapshot(signal(1, now + offset, frame), signal(2, now, 2)))
    with pytest.raises(RuntimeError, match=message):
        point.checkpoint()


@pytest.mark.parametrize(("frame", "offset", "kw", "error", "message"), [
    (8, 0, "20", None, ""),
    (3, -10, "20", TimeoutError, "本轮新实测功率"),
    (8, -1, "20", TimeoutError, "本轮新实测功率"),
    (8, 0, "100", AssertionError, "实测功率已到上限"),
])
def test_lag_requires_a_fresh_below_limit_measurement_with_the_new_signal(monkeypatch, frame, offset, kw, error, message):
    now = time.time_ns() // 1_000_000
    companion = signal(2, now - 20, 1)
    signals = iter([snapshot(signal(1, now - 10, 2), companion),
                    snapshot(signal(1, now, 7, value="合"), companion)])
    measurements = iter([snapshot(signal(3, now - 10, 3, value="20", type_id=13)),
                         snapshot(signal(3, now + offset, frame, value=kw, type_id=13))])
    session = SimpleNamespace(read_signals=lambda: next(signals), read_measurements=lambda: next(measurements))
    point = Master104PointObserver(session, {"increase": 1, "decrease": 2}, 1, measured_ioa=3)
    baseline = point.checkpoint(include_measurement=True)
    baseline["wall_time"] = datetime.fromtimestamp(now / 1000).astimezone().isoformat()
    ticks = count(0, 1)
    monkeypatch.setattr("testbench.master104_signals.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("testbench.master104_signals.time.sleep", lambda _: None)
    args = ({"increase": True, "decrease": False}, baseline, 3.5)
    kwargs = {"fresh_keys": ("increase",), "sent_frame_id": 6, "measurement_below": Decimal("100")}
    if error:
        with pytest.raises(error, match=message):
            point.wait_state(*args, **kwargs)
    else:
        result = point.wait_state(*args, **kwargs)
        assert result["measurement"]["value_kw"] == "20"
        assert result["points"]["increase"]["frame_id"] == 7


@pytest.mark.parametrize(("quality", "type_id", "value"), [("0x01", 13, "20"), ("0x00", 11, "20"), ("0x00", 36, "NaN")])
def test_lag_rejects_invalid_measured_power(quality, type_id, value):
    now = time.time_ns() // 1_000_000
    session = SimpleNamespace(read_measurements=lambda: snapshot(signal(3, now, 1, value=value,
                                                                        type_id=type_id, quality=quality)))
    point = Master104PointObserver(session, {"increase": 1, "decrease": 2}, 1, measured_ioa=3)
    with pytest.raises(RuntimeError, match="实测功率"):
        point.checkpoint(include_measurement=True)
