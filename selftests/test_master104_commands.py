"""104遥调发送边界自测，不操作任何真实窗口。"""
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from testbench.master104_commands import Master104CommandSender, normalize_target


def test_target_requires_finite_exact_float32_and_valid_addresses():
    """不把非数、溢出或无法精确表示的目标静默变成另一条遥调。"""
    assert normalize_target(Decimal("100")) == "100"
    for value in (True, Decimal("NaN"), Decimal("Infinity"), Decimal("0.1"), Decimal("1e40")):
        with pytest.raises(ValueError):
            normalize_target(value)
    for ca, ioa in ((0, 1), (65536, 1), (True, 1), (1, -1), (1, 0x1000000)):
        with pytest.raises(ValueError):
            Master104CommandSender(None, ca, ioa)


def test_single_request_uses_fixed_identity_and_rejects_changed_response(tmp_path):
    """每个目标仅调用一次辅助进程，实例变化不能当作发送成功。"""
    identity = {"path": "C:/tools/ProIEC104Client.exe", "pid": 1, "hwnd": 2, "created": 3}
    session = SimpleNamespace(identity=identity, path=identity["path"], backend="win32", timeout=10,
                              artifacts=tmp_path, metadata={}, _closed=False,
                              _check_stop=lambda: None, step=lambda *a, **k: nullcontext({}))
    calls = []
    def runner(request, timeout, **kwargs):
        calls.append(request)
        return {"identity": identity, "target_kw": "100", "send_attempted": True}
    sender = Master104CommandSender(session, 1, 0x7001, runner=runner)
    result = sender.send_target_kw(Decimal("100"))
    assert len(calls) == 1 and calls[0]["identity"] == identity
    assert calls[0]["common_address"] == 1 and calls[0]["command_ioa"] == 0x7001
    assert result["send_attempted"] is True
    assert list(tmp_path.glob("master104-command-*.json"))
    def broken(request, timeout, **kwargs):
        raise TimeoutError("模拟发送结果未知")
    sender = Master104CommandSender(session, 1, 0x7001, runner=broken)
    with pytest.raises(TimeoutError):
        sender.send_target_kw(Decimal("50"))
    assert len(list(tmp_path.glob("master104-command-*.json"))) == 2


def test_unknown_helper_module_is_rejected_before_starting_process():
    """公共辅助进程入口不能执行任意Python模块。"""
    from testbench.master104_native import run_helper
    with pytest.raises(ValueError, match="模块"):
        run_helper({}, helper_module="untrusted.module")


def test_helper_rejects_incomplete_command_before_loading_native_driver(monkeypatch):
    """缺少绑定身份或出现非白名单动作时，不能触达原生发送能力。"""
    from testbench import master104_command_helper as helper
    monkeypatch.setattr(helper, "_native", lambda: pytest.fail("非法请求不应加载原生驱动"))
    for request in ({}, {"action": "send_target_kw"}, {"action": "connect"}):
        with pytest.raises(ValueError):
            helper.execute(request)


def test_duplicate_or_disabled_dialog_control_is_not_selected():
    """对话框定位不唯一或目标不可用时明确失败，不能取第一个相似控件。"""
    from testbench.master104_command_helper import _control
    item = SimpleNamespace(is_visible=lambda: True, control_id=lambda: 1,
                           class_name=lambda: "Button", is_enabled=lambda: True)
    for items in ([], [item, item]):
        with pytest.raises(RuntimeError, match="唯一"):
            _control(SimpleNamespace(descendants=lambda: items), 1, "Button")


def test_evidence_failure_does_not_replace_unknown_send_result(tmp_path, monkeypatch):
    """发送结果未知且磁盘取证失败时，保留原异常，两种错误分别记录且不重发。"""
    identity = {"path": "C:/tools/ProIEC104Client.exe", "pid": 1, "hwnd": 2, "created": 3}
    session = SimpleNamespace(identity=identity, path=identity["path"], backend="win32", timeout=10,
                              artifacts=tmp_path, metadata={}, _closed=False,
                              _check_stop=lambda: None, step=lambda *a, **k: nullcontext({}))
    calls = []
    def broken(request, timeout, **kwargs):
        calls.append(request)
        raise TimeoutError("原始发送结果未知")
    def no_space(*args, **kwargs):
        raise OSError("测试模拟证据磁盘满")
    monkeypatch.setattr(Path, "write_text", no_space)
    with pytest.raises(TimeoutError, match="原始发送结果未知"):
        Master104CommandSender(session, 1, 1, runner=broken).send_target_kw(Decimal("100"))
    assert len(calls) == 1
    assert "磁盘满" in session.metadata["主站遥调取证错误"][0]


def test_static_command_ids_require_the_exact_supported_binaries(tmp_path, monkeypatch):
    """同名其他版本不能沿用固定菜单命令ID，校验失败时不触达窗口。"""
    import hashlib
    from testbench import master104_command_helper as helper
    executable = tmp_path / "ProIEC104Client.exe"
    executable.write_bytes(b"test-executable")
    (tmp_path / "CMyMFCClass.dll").write_bytes(b"test-dialog-library")
    with pytest.raises(RuntimeError, match="版本"):
        helper._check_supported(executable)
    monkeypatch.setattr(helper, "SUPPORTED_SHA256", {
        "ProIEC104Client.exe": hashlib.sha256(b"test-executable").hexdigest(),
        "CMyMFCClass.dll": hashlib.sha256(b"test-dialog-library").hexdigest()})
    helper._check_supported(executable)
    (tmp_path / "CMyMFCClass.dll").write_bytes(b"different-version")
    with pytest.raises(RuntimeError, match="版本"):
        helper._check_supported(executable)
