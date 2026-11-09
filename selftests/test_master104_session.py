"""用进程与 WebDriver 替身核对主站隔离、生命周期及证据。"""
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from testbench import master104
from testbench.master104 import Master104Session


def make_session(tmp_path, *, executable_bytes=b"MSK104MASTER_TEST_SESSION"):
    executable = tmp_path / "msk104master.exe"
    executable.write_bytes(executable_bytes)
    driver = tmp_path / "msedgedriver.exe"
    driver.touch()
    return Master104Session(tmp_path / "case", executable, driver, 1)


def fake_platform(monkeypatch):
    monkeypatch.setattr(master104.sys, "platform", "win32")
    monkeypatch.setattr(master104.ctypes, "windll",
                        SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: False)), raising=False)


def fake_driver(*, fail_capture=False):
    def screenshot(path):
        if fail_capture:
            raise RuntimeError("模拟 EdgeDriver 截图失败")
        Path(path).write_bytes(b"fake-png")
        return True

    return SimpleNamespace(save_screenshot=screenshot, quit=lambda: None)


def test_launches_isolated_copy_attaches_webview_and_cleans_only_owned_process(tmp_path, monkeypatch):
    app = make_session(tmp_path)
    fake_platform(monkeypatch)
    calls = []
    envs = []

    def terminate():
        calls.append("terminate")
        process.returncode = 0

    process = SimpleNamespace(pid=10, returncode=None, poll=lambda: process.returncode,
                              terminate=terminate, wait=lambda **kwargs: calls.append("wait"))

    def launch(command, *, cwd, env, **kwargs):
        envs.append(env)
        copy_path = app.artifacts / "master104-runtime" / "msk104master.exe"
        assert command == [str(copy_path)]
        assert cwd == copy_path.parent
        assert copy_path.read_bytes() == app.path.read_bytes()
        return process

    def debug_endpoint(url, timeout):
        assert url.endswith("/json/version") and url.startswith("http://127.0.0.1:")
        return BytesIO(b'{"webSocketDebuggerUrl":"ws://127.0.0.1/debug"}')

    driver = fake_driver()
    driver.quit = lambda: calls.append("quit")
    driver.find_element = lambda *args: object()

    def attach(*, service, options):
        assert options.use_webview is True
        assert options.debugger_address.startswith("127.0.0.1:")
        return driver

    monkeypatch.setattr(master104.subprocess, "Popen", launch)
    monkeypatch.setattr(master104, "build_opener", lambda _: SimpleNamespace(open=debug_endpoint))
    monkeypatch.setattr(master104.webdriver, "Edge", attach)
    with app:
        assert app.identity["pid"] == process.pid
        assert envs[0]["MSK104MASTER_TEST_SESSION"] == "1"
        assert Path(envs[0]["WEBVIEW2_USER_DATA_FOLDER"]).parent == app.artifacts / "master104-runtime"
    assert calls == ["quit", "terminate", "wait"]
    assert app.cleanup_failure is None


def test_old_binary_is_rejected_before_starting_any_process(tmp_path, monkeypatch):
    app = make_session(tmp_path, executable_bytes=b"old-release")
    fake_platform(monkeypatch)
    monkeypatch.setattr(master104.subprocess, "Popen", lambda *a, **k: pytest.fail("旧主站不能启动"))
    with pytest.raises(RuntimeError, match="缺少测试模式"):
        with app:
            pytest.fail("旧主站不能进入测试会话")


def evidence_session(tmp_path, monkeypatch, *, fail_capture=False):
    app = make_session(tmp_path)

    def launch():
        app.artifacts.mkdir()
        app.driver = fake_driver(fail_capture=fail_capture)
        app.identity = {"pid": 10}

    monkeypatch.setattr(app, "_launch", launch)
    return app


def test_session_preserves_local_step_ids_and_event_namespace(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(master104, "emit", events.append)
    app = evidence_session(tmp_path, monkeypatch)
    with app:
        app.check("窗口状态", "正常", "正常")
    assert [step["序号"] for step in app.steps] == [1, 2]
    assert [shot["step_id"] for shot in app.screenshots] == [1, 2]
    assert all(shot["automatic"] and shot["capture_status"] == "saved" for shot in app.screenshots)
    assert all(event["step_id"].startswith("master104:") for event in events)
    assert all(shot["name"].startswith("master104-") for shot in app.screenshots)
    assert not (app.artifacts / "report.json").exists()


def test_original_assertion_survives_capture_failure(tmp_path, monkeypatch):
    app = evidence_session(tmp_path, monkeypatch, fail_capture=True)
    with pytest.raises(AssertionError, match="预期"):
        with app:
            app.check("结果", "实际", "预期")
    assert app.failure
    assert app.screenshots[-1]["capture_status"] == "missing"
    assert "模拟 EdgeDriver 截图失败" in app.screenshots[-1]["error"]
    assert app.screenshots[-1]["automatic"] is True


def test_explicit_capture_is_distinct_from_automatic_evidence(tmp_path, monkeypatch):
    app = evidence_session(tmp_path, monkeypatch)
    with app:
        app.check("用户指定检查", True, True)
        app.capture("用户指定关键画面", expected="用户预期", actual="用户实际")
    explicit, = [shot for shot in app.screenshots if not shot["automatic"]]
    assert explicit["step_id"] == app.steps[1]["序号"]
    assert explicit["expected"] == "用户预期"
    assert all(shot["automatic"] for shot in app.screenshots if shot["step_id"] == 1)


@pytest.mark.parametrize(("state", "paused"), [("disconnected", False), ("started", True)])
def test_unusable_master_display_fails_before_reading_points(tmp_path, monkeypatch, state, paused):
    app = evidence_session(tmp_path, monkeypatch)
    with app:
        monkeypatch.setattr(app, "state", lambda: state)
        app.driver.find_elements = lambda *args: [object()] if paused else []
        with pytest.raises(RuntimeError, match="显示未暂停"):
            app.read_signals()


def test_signals_read_only_visible_signal_rows_with_frame_metadata(tmp_path, monkeypatch):
    app = evidence_session(tmp_path, monkeypatch)
    with app:
        monkeypatch.setattr(app, "state", lambda: "started")
        app.driver.find_elements = lambda *args: []
        app.driver.find_element = lambda *args: SimpleNamespace(get_dom_attribute=lambda _: "true")

        def read_dom(script):
            assert "#points-signals:not([hidden])" in script
            assert "receivedFrameId" in script
            return [{"received": "3", "frame_id": "4", "cells": ["1", "2", "1", "合", "0x01", "—", "12:00:00"]}]

        app.driver.execute_script = read_dom
        assert app.read_signals()["rows"][0]["frame_id"] == "4"
