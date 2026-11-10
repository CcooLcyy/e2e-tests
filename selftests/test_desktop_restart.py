"""同一用例重开桌面进程的隔离和失败清理回归。"""
from pathlib import Path
from types import SimpleNamespace
import sys
import io
import json
import subprocess

import pytest

from desktop_demo import ReleaseDesktop


def running_app(tmp_path, monkeypatch):
    app = ReleaseDesktop(Path(sys.executable), Path(sys.executable), tmp_path / "artifacts", 1, close_app=False)
    app.artifacts.mkdir()
    app.runtime.mkdir()
    (app.runtime / "data").mkdir()
    (app.runtime / "data" / "platform-login.json").write_text("保留登录设置", encoding="utf-8")
    (app.runtime / "webview").mkdir()
    app.application_log = (app.artifacts / "application.log").open("ab")
    app.process = SimpleNamespace(pid=123, poll=lambda: None)
    calls = []
    app.driver = SimpleNamespace(quit=lambda: calls.append("断开驱动"))
    monkeypatch.setattr(app, "_close_owned_window", lambda: calls.append("关闭窗口"), raising=False)
    monkeypatch.setattr(app, "_wait_debug_port_closed", lambda: calls.append("释放端口"), raising=False)
    monkeypatch.setattr(app, "_stop_owned_app", lambda: calls.append("强制清理"))
    monkeypatch.setattr(app, "_launch", lambda: calls.append("重新启动"), raising=False)
    return app, calls


def test_restart_preserves_data_and_closes_before_launch_even_when_keep_open(tmp_path, monkeypatch):
    """重开始终关闭旧窗口并释放驱动，原工作区和登录设置原样保留。"""
    app, calls = running_app(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "_prepare_runtime", lambda: pytest.fail("重开不能重新初始化工作区"))
    app.restart()
    assert calls == ["关闭窗口", "断开驱动", "释放端口", "重新启动"]
    assert (app.runtime / "data" / "platform-login.json").read_text(encoding="utf-8") == "保留登录设置"
    assert (app.runtime / "webview").is_dir()
    assert app.steps[0]["状态"] == "通过"


@pytest.mark.parametrize("failure_stage", ["关闭窗口", "断开驱动", "释放端口", "重新启动"])
def test_restart_failure_never_reports_success_or_leaves_owned_process(tmp_path, monkeypatch, failure_stage):
    """旧进程、驱动或新启动失败时不得继续启动且必须清理自有进程。"""
    app, calls = running_app(tmp_path, monkeypatch)
    def fail():
        calls.append(failure_stage)
        raise RuntimeError("重开失败")
    methods = {"关闭窗口": "_close_owned_window", "释放端口": "_wait_debug_port_closed", "重新启动": "_launch"}
    if failure_stage == "断开驱动":
        app.driver.quit = fail
    else:
        monkeypatch.setattr(app, methods[failure_stage], fail)
    with pytest.raises(RuntimeError, match="重开失败"):
        app.restart()
    assert calls.count("重新启动") == (1 if failure_stage == "重新启动" else 0)
    assert "强制清理" in calls or app.process is None
    assert app.failure
    assert '"结果": "失败"' in (app.artifacts / "report.json").read_text(encoding="utf-8")
    assert app.application_log is None


def test_cancel_during_restart_cleans_up_and_does_not_launch(tmp_path, monkeypatch):
    """关闭旧窗口时取消任务仍执行清理，不创建下一实例。"""
    app, calls = running_app(tmp_path, monkeypatch)
    def cancelled():
        raise InterruptedError("测试任务已停止")
    monkeypatch.setattr(app, "_close_owned_window", cancelled)
    with pytest.raises(InterruptedError):
        app.restart()
    assert "重新启动" not in calls
    assert "强制清理" in calls
    assert app.application_log is None


def test_close_window_verifies_both_path_and_pid_and_waits_for_exit(tmp_path, monkeypatch):
    """正常关闭只能针对本次 PID 和副本路径，并等待进程真正退出。"""
    app, _ = running_app(tmp_path, monkeypatch)
    monkeypatch.undo()
    state = {"closed": False}
    app.process.poll = lambda: 0 if state["closed"] else None
    def close_command(args, **options):
        assert options["env"]["MSKDSP_E2E_APP_PATH"] == str(app.copied_app)
        assert options["env"]["MSKDSP_E2E_APP_PID"] == "123"
        assert "ExecutablePath -eq" in args[-1] and "ProcessId -eq" in args[-1]
        assert "CloseMainWindow" in args[-1] and "Stop-Process" not in args[-1]
        state["closed"] = True
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", close_command)
    app._close_owned_window()
    assert app.process.poll() == 0
    app.application_log.close()


def test_close_window_timeout_does_not_force_kill_or_claim_success(tmp_path, monkeypatch):
    """正常关闭超时必须失败，不能在重开验证阶段将强杀当作正常退出。"""
    app, _ = running_app(tmp_path, monkeypatch)
    monkeypatch.undo()
    app.timeout = 0
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    with pytest.raises(TimeoutError, match="未按时正常退出"):
        app._close_owned_window()
    app.application_log.close()


def test_launch_reuses_runtime_appends_log_and_records_each_process(tmp_path, monkeypatch):
    """每次启动使用同一数据目录，日志追加且 PID 和驱动日志按启动分别保存。"""
    from desktop_demo import webdriver
    import desktop_demo
    app, _ = running_app(tmp_path, monkeypatch)
    app.application_log.close()
    monkeypatch.undo()
    launches = []
    drivers = []
    def launch(command, **options):
        launches.append(options)
        options["stdout"].write(b"application-output\n")
        options["stdout"].flush()
        return SimpleNamespace(pid=100 + len(launches), poll=lambda: None)
    class Opener:
        def open(self, *args, **kwargs):
            return io.BytesIO(json.dumps({"webSocketDebuggerUrl": "test"}).encode())
    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(desktop_demo, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(desktop_demo, "Service", lambda **kwargs: drivers.append(kwargs) or kwargs)
    monkeypatch.setattr(webdriver, "Edge", lambda **kwargs: SimpleNamespace(capabilities={"browserVersion": "test"}))
    for _ in range(2):
        app._launch()
        app.application_log.close()
    assert len(launches) == 2
    assert all(item["cwd"] == app.runtime for item in launches)
    assert all(item["env"]["WEBVIEW2_USER_DATA_FOLDER"] == str(app.runtime / "webview") for item in launches)
    assert [entry["PID"] for entry in app.metadata["启动历史"]] == [101, 102]
    assert drivers[0]["log_output"] != drivers[1]["log_output"]
    assert all(item["service_args"] == ["--log-level=OFF"] for item in drivers)
    assert (app.artifacts / "application.log").read_bytes().count(b"application-output") == 2


@pytest.mark.parametrize("actual_version", ["157.0.4341.46", "160.0.4444.22"])
def test_private_webview_environment_and_actual_version_are_checked_before_attach(tmp_path, monkeypatch, actual_version):
    import desktop_demo

    fixed_runtime = tmp_path / "157.0.4341.46" / "msedgewebview2.exe"
    fixed_runtime.parent.mkdir()
    fixed_runtime.touch()
    fixed_driver = fixed_runtime.parent / "msedgedriver.exe"
    fixed_driver.touch()
    app = ReleaseDesktop(Path(sys.executable), None, tmp_path / "artifacts", 1,
                         private_webview_pair=(fixed_runtime, fixed_driver))
    app.artifacts.mkdir()
    launches, services = [], []
    monkeypatch.delenv("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kwargs:
                        launches.append(kwargs) or SimpleNamespace(pid=101, poll=lambda: None))
    response = {"webSocketDebuggerUrl": "test", "Browser": f"Edg/{actual_version}"}
    monkeypatch.setattr(desktop_demo, "build_opener", lambda *args:
                        SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode())))
    monkeypatch.setattr(desktop_demo, "Service", lambda **kwargs: services.append(kwargs) or kwargs)
    monkeypatch.setattr(desktop_demo.webdriver, "Edge", lambda **kwargs: SimpleNamespace())
    try:
        if actual_version == fixed_runtime.parent.name:
            app._launch()
            assert services[0]["executable_path"] == str(fixed_driver)
            assert "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER" not in services[0]["env"]
        else:
            with pytest.raises(RuntimeError, match="预期私有版本"):
                app._launch()
            assert services == []
        assert app.driver_path is None
        assert launches[0]["env"]["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] == str(fixed_runtime.parent)
        assert launches[0]["env"]["WEBVIEW2_USER_DATA_FOLDER"] == str(app.runtime / "webview-fixed-157.0.4341.46")
        assert app.metadata["WebView2版本"] == actual_version
    finally:
        app.application_log.close()
