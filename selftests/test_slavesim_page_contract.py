"""模拟器会话清理与失败取证契约，不启动浏览器或连接设备。"""
from contextlib import contextmanager
from pathlib import Path

import pytest

from testbench.slavesim_page import SlaveSimPage


POINT_A = ("专用测试总线", 1, "0x03", 10)
POINT_B = ("专用测试总线", 2, "0x03", 10)


class MemoryDesktop:
    """仅提供步骤生命周期与取消边界，外部页面效果由会话替身提供。"""

    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.timeout = 0.01
        self.metadata, self.steps, self.screenshots = {}, [], []
        self._active_steps = []
        self.failure = self.cleanup_failure = None
        self.stopped = False
        self.stop_checks = 0

    def _check_stop(self):
        self.stop_checks += 1
        if self.stopped:
            raise InterruptedError("测试任务已停止")

    @contextmanager
    def step(self, name, expected=None, *, cancellable=True):
        if cancellable:
            self._check_stop()
        entry = {"序号": len(self.steps) + 1, "步骤": name, "预期": expected, "状态": "执行中"}
        self.steps.append(entry)
        self._active_steps.append(entry)
        try:
            yield
            entry["状态"] = "通过"
        except BaseException as error:
            entry["状态"] = "失败"
            self.failure = self.failure or str(error)
            raise
        finally:
            self._active_steps.pop()


class ScreenshotDriver:
    def __init__(self, *, fail_capture=False):
        self.fail_capture = fail_capture
        self.closed = False

    def save_screenshot(self, target):
        if self.fail_capture:
            raise RuntimeError("截图通道异常")
        Path(target).write_bytes(b"\x89PNG\r\n\x1a\n")
        return True

    def quit(self):
        self.closed = True


class MemoryPage(SlaveSimPage):
    """替换外部设值边界，仍经过真实会话步骤、等待、截图与清理逻辑。"""

    def __init__(self, desktop, *, fail_points=(), fail_capture=False):
        super().__init__(desktop, "http://127.0.0.1:16000")
        self.driver = ScreenshotDriver(fail_capture=fail_capture)
        self.values = {POINT_A: 1234, POINT_B: 2345}
        self.calls = []
        self.fail_points = set(fail_points)

    def __enter__(self):
        return self

    def set_value(self, bus, station_id, function, address, value):
        key = (bus, station_id, function, address)
        self.calls.append((key, value))
        with self._step("外部页面设置原值", expected=value):
            self._wait(lambda _: True, "模拟页面未响应")
            if key in self.fail_points:
                raise RuntimeError(f"站号 {station_id} 恢复失败")
            self.values[key] = value


def test_restore_all_completes_after_cancellation(tmp_path):
    """取消阻止新业务，却不阻止恢复两站原值，也不清除取消状态。"""
    desktop = MemoryDesktop(tmp_path)
    page = MemoryPage(desktop)
    page._originals = {POINT_A: 11, POINT_B: 22}
    desktop.stopped = True
    with pytest.raises(InterruptedError):
        with page._step("不得执行的新业务"):
            pytest.fail("取消后执行了新业务")
    checks_before = desktop.stop_checks
    page.restore_all()
    assert page.values == {POINT_A: 11, POINT_B: 22}
    assert page._originals == {}
    assert desktop.stopped and desktop.stop_checks == checks_before
    assert page._restoring is False


def test_restore_failure_keeps_failed_point_and_attempts_other_station(tmp_path):
    """一站恢复失败不截断另一站清理，未恢复点保留供诊断。"""
    page = MemoryPage(MemoryDesktop(tmp_path), fail_points={POINT_A})
    page._originals = {POINT_A: 11, POINT_B: 22}
    with pytest.raises(RuntimeError, match="站号 1 恢复失败"):
        page.restore_all()
    assert page.calls == [(POINT_A, 11), (POINT_B, 22)]
    assert page.values == {POINT_A: 1234, POINT_B: 22}
    assert page._originals == {POINT_A: 11}
    assert page._restoring is False


def test_context_cleanup_failure_preserves_original_business_exception(tmp_path):
    """恢复失败记录独立诊断并关闭自有浏览器，原业务断言对象仍传播。"""
    desktop = MemoryDesktop(tmp_path)
    page = MemoryPage(desktop, fail_points={POINT_A})
    page._originals = {POINT_A: 11}
    driver = page.driver
    original = AssertionError("双端实际值不一致")
    desktop.failure = str(original)
    with pytest.raises(AssertionError) as caught:
        with page:
            raise original
    assert caught.value is original
    assert desktop.failure == str(original)
    assert "恢复失败" in desktop.cleanup_failure
    assert "slaveSim清理错误" in desktop.metadata
    assert driver.closed and page.driver is None


def test_context_success_does_not_hide_cleanup_failure(tmp_path):
    """业务未抛错时恢复故障仍使上下文失败，不能报告假通过。"""
    desktop = MemoryDesktop(tmp_path)
    page = MemoryPage(desktop, fail_points={POINT_A})
    page._originals = {POINT_A: 11}
    driver = page.driver
    with pytest.raises(RuntimeError, match="slaveSim 原值恢复失败"):
        with page:
            pass
    assert desktop.cleanup_failure
    assert driver.closed


@pytest.mark.parametrize("fail_capture", [False, True])
def test_failed_step_keeps_source_association_and_original_error(tmp_path, fail_capture):
    """失败截图带来源和步骤编号；截图通道故障也不能替换业务断言。"""
    desktop = MemoryDesktop(tmp_path)
    page = MemoryPage(desktop, fail_capture=fail_capture)
    original = AssertionError("模拟点值与预期不同")
    with pytest.raises(AssertionError) as caught:
        with page._step("核对实际点值", expected=1234):
            raise original
    assert caught.value is original
    shot = desktop.screenshots[-1]
    assert shot["source"] == "slavesim"
    assert shot["step_id"] == desktop.steps[-1]["序号"]
    assert shot["status"] == "failed"
    assert str(original) in shot["actual"]
    assert desktop.steps[-1]["来源"] == "slaveSim"
    if fail_capture:
        assert shot["capture_status"] == "missing"
        assert "截图通道异常" in shot["error"]
    else:
        assert shot["capture_status"] == "saved"
        assert (tmp_path / shot["name"]).is_file()
