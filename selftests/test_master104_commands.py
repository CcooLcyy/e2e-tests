"""以 WebDriver 替身验证 104 遥调结果匹配、单次发送和失败证据。"""
from contextlib import nullcontext
from decimal import Decimal
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from selenium.webdriver.common.by import By

from testbench.master104_commands import Master104CommandSender, normalize_target


def test_target_requires_finite_exact_float32_and_valid_addresses():
    assert normalize_target(Decimal("100")) == "100"
    for value in (True, Decimal("NaN"), Decimal("Infinity"), Decimal("0.1"), Decimal("1e40")):
        with pytest.raises(ValueError):
            normalize_target(value)
    for ca, ioa in ((0, 1), (65535, 1), (True, 1), (1, -1), (1, 0x1000000)):
        with pytest.raises(ValueError):
            Master104CommandSender(None, ca, ioa)


def fake_session(directory, *, ca="1", result_changes=None, confirmed_first=False):
    calls = []
    waits = []
    result = {"operation-id": "1", "state": "terminated"}

    def click_send():
        calls.append("send")
        sent_at = time.time_ns() // 1_000_000
        result.update({"operation-id": "2", "state": "confirmed" if confirmed_first else "terminated", "ioa": "28673",
                       "common-address": "1", "value": "100.0",
                       "sent-at": str(sent_at), "sent-frame-id": "10",
                       "confirmed-at": str(sent_at + 1), "confirmed-frame-id": "12",
                       "terminated-at": str(sent_at + 2), "terminated-frame-id": "15",
                       "sent-raw": "68 12 00 00 00 00 32 01 06 00 01 00 01 70 00 00 00 C8 42 00",
                       "confirmed-raw": "68 12 00 00 02 00 32 01 07 00 01 00 01 70 00 00 00 C8 42 00",
                       "terminated-raw": "68 12 02 00 02 00 32 01 0A 00 01 00 01 70 00 00 00 C8 42 00"})
        result.update(result_changes or {})

    fields = {
        "tab-setpoints": SimpleNamespace(click=lambda: calls.append("tab")),
        "setpoint-ioa": "IOA",
        "setpoint-value": "value",
        "setpoint-result": SimpleNamespace(get_dom_attribute=lambda name: result.get(name.removeprefix("data-"))),
        "send-setpoint": SimpleNamespace(click=click_send, is_enabled=lambda: True),
    }

    def find_element(by, selector):
        assert by == By.ID
        return fields[selector]

    def wait_for(condition, message, timeout):
        waits.append(timeout)
        if observed := condition():
            return observed
        if confirmed_first and calls[-1] == "send":
            result["state"] = "terminated"
            if observed := condition():
                return observed
        raise TimeoutError(message)

    session = SimpleNamespace(
        identity={"pid": 10, "path": "msk104master.exe"}, artifacts=directory, metadata={},
        timeout=10, waits=waits,
        _check_stop=lambda: None, _assert_live_display=lambda: None,
        step=lambda *a, **k: nullcontext({}),
        _connection_fields=lambda: [None, None, SimpleNamespace(get_property=lambda _: ca)],
        driver=SimpleNamespace(find_element=find_element),
        fill=lambda field, value: calls.append((field, value)),
        wait_for=wait_for,
    )
    return session, calls


def test_only_new_matching_positive_confirmation_and_termination_count(tmp_path):
    session, calls = fake_session(tmp_path, confirmed_first=True)
    result = Master104CommandSender(session, 1, 0x7001).send_target_kw(Decimal("100"))
    assert calls == ["tab", ("IOA", "28673"), ("value", "100"), "send"]
    assert result["identity"] == session.identity
    assert result["operation_id"] == "2" and result["state"] == "terminated"
    assert result["sent_frame_id"] == 10 and result["sent_at"]
    assert [result["frames"][kind]["frame_id"] for kind in ("sent", "confirmed", "terminated")] == [10, 12, 15]
    assert all(result["frames"][kind]["raw"] for kind in ("sent", "confirmed", "terminated"))
    assert session.waits[-1] >= 30
    path, = tmp_path.glob("master104-command-*.json")
    assert json.loads(path.read_text(encoding="utf-8"))["result"] == result


@pytest.mark.parametrize(("changes", "error", "message"), [
    ({"operation-id": "1"}, TimeoutError, "本次遥调"),
    ({"state": "sent"}, TimeoutError, "本次遥调"),
    ({"state": "confirmed"}, TimeoutError, "肯定终止"),
    ({"state": "rejected"}, RuntimeError, "拒绝"),
    ({"state": "failed"}, RuntimeError, "失败"),
    ({"state": "timeout"}, RuntimeError, "超时"),
    ({"ioa": "8"}, RuntimeError, "目标参数"),
    ({"common-address": "2"}, RuntimeError, "目标参数"),
    ({"value": "101"}, RuntimeError, "目标参数"),
    ({"sent-at": "1"}, RuntimeError, "有效发送证据"),
    ({"confirmed-raw": ""}, RuntimeError, "原始报文"),
    ({"confirmed-frame-id": "10"}, RuntimeError, "帧序号"),
    ({"terminated-at": "1"}, RuntimeError, "报文时间"),
    ({"terminated-raw": "68 12 02 00 02 00 32 01 4A 00 01 00 01 70 00 00 00 C8 42 00"}, RuntimeError, "原始报文"),
    ({"terminated-raw": "68 12 02 00 02 00 32 01 0A 00 01 00 02 70 00 00 00 C8 42 00"}, RuntimeError, "原始报文"),
])
def test_transaction_failure_keeps_evidence_and_does_not_retry(tmp_path, changes, error, message):
    session, calls = fake_session(tmp_path, result_changes=changes)
    with pytest.raises(error, match=message):
        Master104CommandSender(session, 1, 0x7001).send_target_kw(Decimal("100"))
    assert calls.count("send") == 1
    path, = tmp_path.glob("master104-command-*.json")
    assert "error" in json.loads(path.read_text(encoding="utf-8"))


def test_connection_target_mismatch_prevents_send(tmp_path):
    session, calls = fake_session(tmp_path, ca="2")
    with pytest.raises(RuntimeError, match="当前连接不一致"):
        Master104CommandSender(session, 1, 0x7001).send_target_kw(100)
    assert calls == []


def test_evidence_failure_does_not_replace_unknown_send_result(tmp_path, monkeypatch):
    session, calls = fake_session(tmp_path, result_changes={"state": "sent"})

    def no_space(*args, **kwargs):
        raise OSError("测试模拟证据磁盘满")

    monkeypatch.setattr(Path, "write_text", no_space)
    with pytest.raises(TimeoutError, match="本次遥调"):
        Master104CommandSender(session, 1, 0x7001).send_target_kw(100)
    assert calls.count("send") == 1
    assert "磁盘满" in session.metadata["主站遥调取证错误"][0]
