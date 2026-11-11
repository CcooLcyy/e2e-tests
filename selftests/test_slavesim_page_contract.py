"""模拟器会话清理与失败取证契约，不启动浏览器或连接设备。"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from testbench.slavesim_page import SlaveSimPage


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
    def __init__(self, *, fail_capture=False, fail_close=False):
        self.fail_capture = fail_capture
        self.fail_close = fail_close
        self.closed = False

    def save_screenshot(self, target):
        if self.fail_capture:
            raise RuntimeError("截图通道异常")
        Path(target).write_bytes(b"\x89PNG\r\n\x1a\n")
        return True

    def quit(self):
        self.closed = True
        if self.fail_close:
            raise RuntimeError("浏览器关闭失败")


class MemoryPage(SlaveSimPage):
    """替换页面配置边界，仍经过真实会话步骤、等待、截图与清理逻辑。"""

    def __init__(self, desktop, *, buses=(), fail_capture=False, fail_close=False):
        super().__init__(desktop, "http://127.0.0.1:16000")
        self.driver = ScreenshotDriver(fail_capture=fail_capture, fail_close=fail_close)
        self.driver.find_element = self._element
        self.driver.find_elements = lambda *_: self.draft
        self.buses = [{"name": name, "link": f"/srv/live/tty{index}"} for index, name in enumerate(buses)]
        self.draft = []
        self.calls = []

    def __enter__(self):
        return self

    def _element(self, _, name):
        assert name == "delete-all-buses"

        def delete():
            assert self.draft
            self.calls.append("批量删除草稿")
            self.draft.clear()

        return SimpleNamespace(click=delete)

    def _read_configuration(self, label):
        self.calls.append("读配置")
        self.draft = list(self.buses)
        return list(self.buses)

    def _save_configuration(self, label, *, before, owned_names, removed_names):
        assert not owned_names and not self.draft
        assert before == self.buses
        assert removed_names == tuple(bus["name"] for bus in before)
        self.calls.append("保存清空配置")
        self.buses = list(self.draft)
        return self._read_configuration(label)


@pytest.mark.parametrize("buses", [(), ("旧总线", "本例总线")])
def test_clear_all_completes_after_cancellation(tmp_path, buses):
    """停止后仍清空所有已保存总线，空配置无需再保存。"""
    desktop = MemoryDesktop(tmp_path)
    page = MemoryPage(desktop, buses=buses, fail_capture=True)
    desktop.stopped = True
    with pytest.raises(InterruptedError):
        with page._step("不得执行的新业务"):
            pytest.fail("取消后执行了新业务")
    checks_before = desktop.stop_checks
    page.clear_all_buses()
    assert page.buses == []
    assert page.calls == (["读配置", "批量删除草稿", "保存清空配置", "读配置"] if buses else ["读配置"])
    assert desktop.metadata["slaveSim总线清理"] == {"清理前总线数": len(buses), "清理后总线数": 0}
    assert desktop.stopped and desktop.stop_checks == checks_before
    assert page._restoring is False


def test_prepare_clears_only_the_draft_and_remembers_the_shared_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTBENCH_DATA_ROOT", str(tmp_path))
    page = MemoryPage(MemoryDesktop(tmp_path), buses=("旧总线一", "旧总线二"))
    before = page._prepare_empty_configuration("准备新总线前")
    assert before == page.buses and len(before) == 2
    assert page.draft == []
    assert page.calls == ["读配置", "批量删除草稿"]
    next_page = SlaveSimPage(page.desktop, page.url)
    assert str(next_page._serial_directory("/srv/serial-sim/tty0")) == "/srv/live"
    other_page = SlaveSimPage(page.desktop, "http://127.0.0.1:17000")
    assert str(other_page._serial_directory("/srv/serial-sim/tty0")) == "/srv/serial-sim"


def test_cleanup_reload_confirms_discard_of_an_unsaved_draft(tmp_path):
    desktop = MemoryDesktop(tmp_path)
    page = SlaveSimPage(desktop, "http://127.0.0.1:16000")
    page.driver = ScreenshotDriver()
    calls = []
    alert = SimpleNamespace(text="重新读取会丢弃尚未保存的配置修改，是否继续？",
                            accept=lambda: calls.append("丢弃未保存草稿"))
    page.driver.switch_to = SimpleNamespace(alert=alert)
    nodes = {
        "show-config": SimpleNamespace(click=lambda: calls.append("打开配置")),
        "config-fields": SimpleNamespace(is_enabled=lambda: True),
        "reload-config": SimpleNamespace(click=lambda: calls.append("重新读取")),
        "config-status": SimpleNamespace(text="已读取当前配置"),
    }
    page.driver.find_element = lambda _, name: nodes[name]
    page._configuration_snapshot = lambda _: [{"name": "已保存的总线"}]
    page._restoring = True

    assert page._read_configuration("清理前重读") == [{"name": "已保存的总线"}]
    assert calls == ["打开配置", "重新读取", "丢弃未保存草稿"]


def test_context_close_failure_preserves_original_business_exception(tmp_path):
    """关闭浏览器失败记录独立诊断，原业务断言对象仍传播。"""
    desktop = MemoryDesktop(tmp_path)
    page = MemoryPage(desktop, fail_close=True)
    driver = page.driver
    original = AssertionError("双端实际值不一致")
    desktop.failure = str(original)
    with pytest.raises(AssertionError) as caught:
        with page:
            raise original
    assert caught.value is original
    assert desktop.failure == str(original)
    assert "浏览器清理失败" in desktop.cleanup_failure
    assert "slaveSim浏览器清理错误" in desktop.metadata
    assert driver.closed and page.driver is None


def test_context_success_does_not_hide_close_failure(tmp_path):
    """业务未抛错时关闭故障仍使上下文失败。"""
    desktop = MemoryDesktop(tmp_path)
    page = MemoryPage(desktop, fail_close=True)
    driver = page.driver
    with pytest.raises(RuntimeError, match="slaveSim 浏览器清理失败"):
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
