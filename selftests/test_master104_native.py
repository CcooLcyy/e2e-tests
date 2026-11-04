"""原生主站辅助进程边界，不操作真实窗口。"""
import json
import subprocess
from threading import Event
from types import SimpleNamespace

import pytest

from testbench.master104_native import run_helper, select_identity


def test_exact_identity_rejects_ambiguous_or_missing_windows():
    """只允许唯一完整路径匹配，不能任意接管同名主站。"""
    row = {"path": "C:/tools/ProIEC104Client.exe", "pid": 10, "hwnd": 20, "created": 30}
    assert select_identity([row], row["path"]) == row
    for rows in ([], [row, dict(row, pid=11)]):
        with pytest.raises(RuntimeError):
            select_identity(rows, row["path"])
    with pytest.raises(RuntimeError):
        select_identity([dict(row, path="C:/other/ProIEC104Client.exe")], row["path"])


class Process:
    """可控制的辅助进程替身；只能终止自身。"""
    returncode = None

    def __init__(self, *, hangs=False):
        self.hangs, self.killed, self.reaped = hangs, False, False

    def communicate(self, input=None, timeout=None):
        if self.hangs and not self.killed:
            raise subprocess.TimeoutExpired("辅助进程", timeout)
        self.returncode, self.reaped = 0, True
        return json.dumps({"ok": True, "result": {"pid": 10}}), ""

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True


def test_helper_timeout_kills_and_reaps_only_spawned_helper():
    """原生读取超时后回收辅助进程，不能终止主站。"""
    process = Process(hangs=True)
    ticks = iter([0, 0, 2])
    with pytest.raises(TimeoutError, match="超时"):
        run_helper({}, 1, popen=lambda *a, **k: process, clock=lambda: next(ticks))
    assert process.killed and process.reaped


def test_cancelled_request_does_not_spawn_helper(tmp_path):
    """停止标记已存在时不启动任何辅助进程。"""
    stop = tmp_path / "stop"
    stop.touch()
    with pytest.raises(InterruptedError):
        run_helper({}, 1, stop_path=stop, popen=lambda *a, **k: pytest.fail("不应启动"))


def test_successful_helper_returns_json_and_is_reaped():
    """成功结果来自结构化响应，并等待辅助进程退出。"""
    process = Process()
    assert run_helper({}, 1, popen=lambda *a, **k: process) == {"pid": 10}
    assert process.reaped and not process.killed


def test_shutdown_event_cancels_active_helper_and_reaps_it():
    """桥接退出事件取消正在读取的辅助进程，不留下后台采集。"""
    event = Event()
    process = Process(hangs=True)
    original = process.communicate

    def communicate(input=None, timeout=None):
        if not process.killed:
            event.set()
        return original(input=input, timeout=timeout)

    process.communicate = communicate
    with pytest.raises(InterruptedError):
        run_helper({}, 10, cancel_event=event, popen=lambda *a, **k: process)
    assert process.killed and process.reaped


def test_cleanup_timeout_preserves_original_timeout():
    """辅助进程清理再次超时不能覆盖最初的采集失败。"""
    process = Process(hangs=True)

    def communicate(input=None, timeout=None):
        raise subprocess.TimeoutExpired("辅助进程", timeout)

    process.communicate = communicate
    ticks = iter([0, 0, 2])
    with pytest.raises(TimeoutError, match="读取超时") as error:
        run_helper({}, 1, popen=lambda *a, **k: process, clock=lambda: next(ticks))
    assert any("回收失败" in note for note in error.value.__notes__)


def test_capture_accepts_owned_modal_without_switching_to_main_window(tmp_path, monkeypatch):
    """同进程且根所有者为目标主窗的模态框可取证，不能强行切回主窗。"""
    from testbench import master104_helper as helper

    identity = {"path": str(tmp_path / "ProIEC104Client.exe"), "pid": 10, "hwnd": 20, "created": 30}
    captured = []

    class Window:
        def __init__(self, hwnd):
            self.hwnd = hwnd

        def wrapper_object(self):
            return self

        def window_text(self):
            return "主站模态框"

        def set_focus(self):
            pytest.fail("不应切回主窗")

        def capture_as_image(self):
            captured.append(self.hwnd)
            return SimpleNamespace(save=lambda stream, **kwargs: stream.write(b"PNG"))

    desktop = lambda **kwargs: SimpleNamespace(window=lambda handle: Window(handle))
    gui = SimpleNamespace(IsIconic=lambda hwnd: False, GetForegroundWindow=lambda: 30,
                          GetAncestor=lambda hwnd, flag: 20)
    processes = SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (1, 10))
    monkeypatch.setattr(helper, "_native", lambda: (desktop, gui, processes))
    monkeypatch.setattr(helper, "_verify", lambda expected, *args: expected)
    result = helper.collect({"action": "capture", "path": identity["path"], "identity": identity,
                             "output": str(tmp_path / "modal.png")})
    assert captured == [30]
    assert result["capture_window"]["hwnd"] == 30
    assert (tmp_path / "modal.png").read_bytes() == b"PNG"
