"""登录测试只占用精确测试凭据，拒绝覆盖既有账号数据。"""

import ctypes
import hashlib
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from testbench.login_credentials import WindowsCredentialBackend, WindowsCredentialGuard, cleanup_login_credentials
from types import SimpleNamespace


class FakeBackend:
    def __init__(self, present=False):
        self.present = present
        self.calls = []

    def exists(self, target):
        self.calls.append(("exists", target))
        return self.present

    def delete(self, target):
        self.calls.append(("delete", target))
        self.present = False


def test_target_matches_rust_keyring_windows_mapping():
    """目标名与 Rust SHA256 加 keyring 的 user.service 约定完全一致。"""
    backend = FakeBackend()
    guard = WindowsCredentialGuard("http://platform.example:50000", " Alice ", backend=backend)
    digest = hashlib.sha256(b"http://platform.example:50000\nalice").hexdigest()
    assert guard.target == digest + ".com.mskdsp.upper:platform"
    assert WindowsCredentialGuard("https://platform.example:50000", "alice", backend=backend).target != guard.target
    assert WindowsCredentialGuard("http://platform.example:50000", "bob", backend=backend).target != guard.target


def test_existing_credential_is_rejected_without_deletion():
    """初始已有凭据时只查询存在性，不能进入用例或删除凭据。"""
    backend = FakeBackend(present=True)
    guard = WindowsCredentialGuard("http://platform.example:50000", "alice", backend=backend)
    with pytest.raises(RuntimeError, match="已有.*凭据"):
        with guard:
            pytest.fail("不应进入测试正文")
    assert backend.calls == [("exists", guard.target)]
    assert backend.present


@pytest.mark.parametrize("failure", [None, RuntimeError("用例失败"), InterruptedError("用户停止"), KeyboardInterrupt()])
def test_owned_credential_is_cleaned_on_every_exit(failure):
    """通过、失败和取消都清理本次声明的精确目标，不影响其他目标。"""
    backend = FakeBackend()
    guard = WindowsCredentialGuard("http://platform.example:50000", "alice", backend=backend)
    def run():
        with guard as active:
            assert active is guard
            backend.present = True
            assert guard.exists()
            if failure is not None:
                raise failure
    if failure is None:
        run()
    else:
        with pytest.raises(type(failure)):
            run()
    assert backend.calls == [("exists", guard.target), ("exists", guard.target), ("delete", guard.target)]
    assert not backend.present


def test_cleanup_failure_is_reported_instead_of_passing():
    """凭据清理异常必须使测试失败，不能将残留凭据伪装成清理成功。"""
    backend = FakeBackend()
    backend.delete = Mock(side_effect=RuntimeError("清除测试凭据失败"))
    with pytest.raises(RuntimeError, match="清除测试凭据失败"):
        with WindowsCredentialGuard("http://platform.example:50000", "alice", backend=backend):
            pass


def test_failed_preflight_does_not_claim_or_delete_credential():
    """凭据查询失败时不能将其当作不存在，也不能执行清理删除。"""
    backend = FakeBackend()
    backend.exists = Mock(side_effect=RuntimeError("查询测试凭据失败"))
    backend.delete = Mock()
    with pytest.raises(RuntimeError, match="查询测试凭据失败"):
        with WindowsCredentialGuard("http://platform.example:50000", "alice", backend=backend):
            pass
    backend.delete.assert_not_called()


def native_api(read_result=0, delete_result=0, error=1168):
    api = Mock()
    def read(target, kind, flags, pointer):
        ctypes.set_last_error(error)
        if read_result:
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = 12345
        return read_result
    def delete(target, kind, flags):
        ctypes.set_last_error(error)
        return delete_result
    api.CredReadW.side_effect = read
    api.CredDeleteW.side_effect = delete
    return api


def test_native_exists_frees_credential_without_reading_its_blob():
    """查询只释放系统返回的指针，不解码凭据结构或秘密字节。"""
    api = native_api(read_result=1)
    backend = WindowsCredentialBackend(api=api)
    assert backend.exists("精确测试目标")
    api.CredReadW.assert_called_once()
    assert api.CredReadW.call_args.args[:3] == ("精确测试目标", 1, 0)
    assert api.CredFree.call_args.args[0].value == 12345


def test_native_missing_is_the_only_ignored_error():
    """1168 表示凭据不存在，查询返回假且清理可重复执行。"""
    api = native_api()
    backend = WindowsCredentialBackend(api=api)
    assert not backend.exists("精确测试目标")
    backend.delete("精确测试目标")
    api.CredFree.assert_not_called()
    api.CredDeleteW.assert_called_once_with("精确测试目标", 1, 0)


@pytest.mark.parametrize("operation", ["exists", "delete"])
def test_native_permission_error_is_not_silenced(operation):
    """访问被拒绝等错误不能误报成不存在或删除成功。"""
    api = native_api(error=5)
    backend = WindowsCredentialBackend(api=api)
    with pytest.raises(RuntimeError, match="5"):
        getattr(backend, operation)("精确测试目标")


def owned_fixture(close_error=None, cleanup_error=None, running=False):
    events = []
    desktop = SimpleNamespace(close_app=False, cleanup_failure=None,
                              process=SimpleNamespace(poll=lambda: None if running else 0))
    @contextmanager
    def step(name, *, cancellable=True):
        assert cancellable is False, "清理步骤必须忽略停止标记"
        yield
    desktop.step = step
    guard = SimpleNamespace()
    def close():
        events.append(("close", desktop.close_app))
        if close_error:
            raise close_error
    def release(*args):
        events.append(("delete",))
        if cleanup_error:
            raise cleanup_error
    desktop.close = close
    guard.__exit__ = release
    return desktop, guard, events


def test_cleanup_stops_owned_app_before_deleting_credentials(capsys):
    """先等待本次应用退出，防止未完成登录请求在凭据删除后重新写入。"""
    desktop, guard, events = owned_fixture()
    cleanup_login_credentials(desktop, guard)
    assert events == [("close", True), ("delete",)]
    assert "清理" in capsys.readouterr().out


@pytest.mark.parametrize("close_error", [RuntimeError("关闭错误"), InterruptedError("用户停止")])
def test_cleanup_still_deletes_when_closing_raises(close_error):
    """关闭异常和中断都不跳过尽力删除，且最终必须失败。"""
    desktop, guard, events = owned_fixture(close_error=close_error)
    with pytest.raises(RuntimeError, match="关闭.*失败"):
        cleanup_login_credentials(desktop, guard)
    assert events == [("close", True), ("delete",)]


def test_cleanup_ignores_cancellation_flag_and_final_keep_window_setting(tmp_path, monkeypatch):
    """用户停止且配置保留窗口时，仍关闭测试实例并清理凭据。"""
    stop = tmp_path / "stop"
    stop.touch()
    monkeypatch.setenv("TESTBENCH_STOP", str(stop))
    desktop, guard, events = owned_fixture()
    desktop.step = Mock(wraps=desktop.step)
    cleanup_login_credentials(desktop, guard)
    assert events == [("close", True), ("delete",)]
    assert desktop.step.call_count >= 3
    assert all(call.kwargs.get("cancellable") is False for call in desktop.step.call_args_list)


def test_cleanup_failure_property_is_not_reported_as_success(capsys):
    """close 只设置失败属性不抛出时，依然删除凭据并明确失败。"""
    desktop, guard, events = owned_fixture()
    def close():
        events.append(("close", desktop.close_app))
        desktop.cleanup_failure = "清理本次进程失败"
    desktop.close = close
    with pytest.raises(RuntimeError, match="关闭.*失败"):
        cleanup_login_credentials(desktop, guard)
    assert events == [("close", True), ("delete",)]
    assert "已清理" not in capsys.readouterr().out


def test_cleanup_running_process_is_not_reported_as_success():
    """close 返回后进程仍在运行时不能宣称已经完成清理。"""
    desktop, guard, events = owned_fixture(running=True)
    with pytest.raises(RuntimeError, match="进程仍在运行"):
        cleanup_login_credentials(desktop, guard)
    assert events == [("close", True), ("delete",)]


def test_cleanup_deletion_failure_is_not_reported_as_success():
    """进程已退出但凭据删除失败时，最终结果仍必须失败。"""
    desktop, guard, events = owned_fixture(cleanup_error=RuntimeError("Windows 错误码：5"))
    with pytest.raises(RuntimeError, match="删除.*失败"):
        cleanup_login_credentials(desktop, guard)
    assert events == [("close", True), ("delete",)]
