"""104 主站可选配置及非阻塞只读检查的框架契约，不操作真实主站。"""
import sys
import threading
from types import SimpleNamespace

import pytest

from testbench.bridge import TestbenchBridge
from testbench.model import Environment, load_environment, save_environment


def master_environment(tmp_path, **changes):
    """使用临时占位程序验证配置，不启动程序。"""
    program = tmp_path / "Msk104Master.exe"
    program.touch(exist_ok=True)
    driver = tmp_path / "msedgedriver.exe"
    driver.touch(exist_ok=True)
    data = {"master104_enabled": True, "master104_path": str(program),
            "driver_path": str(driver), "master104_timeout": 2}
    data.update(changes)
    return data


def install_probe(monkeypatch, probe):
    """替换主站接入边界，框架自测不需要启动 EdgeDriver。"""
    monkeypatch.setitem(sys.modules, "testbench.master104", SimpleNamespace(probe_master104=probe))


def test_old_environment_keeps_master104_disabled():
    """旧环境缺少主站字段时不要求程序或驱动。"""
    env = Environment.from_dict({"name": "旧环境"})
    assert env.master104_enabled is False
    assert env.master104_path == ""
    assert env.master104_timeout == 10
    env.validate_master104()
    with pytest.raises(ValueError, match="启用"):
        env.validate_master104(required=True)


def test_master104_configuration_round_trips_without_changing_old_fields(tmp_path):
    """主站配置与既有环境一起保存，禁用时允许保留未完成配置。"""
    app = tmp_path / "upper.exe"
    app.touch()
    env = Environment(app_path=str(app), **master_environment(tmp_path, master104_timeout="1.5"))
    path = tmp_path / "environment.json"
    save_environment(path, env)
    restored = load_environment(path)
    assert restored.to_dict() == env.to_dict()
    assert restored.master104_timeout == 1.5
    restored.master104_enabled = False
    restored.master104_path = ""
    save_environment(path, restored)
    assert load_environment(path).master104_enabled is False


@pytest.mark.parametrize("changes", [
    {"master104_enabled": "true"}, {"master104_path": ""}, {"master104_path": "missing.exe"},
    {"master104_path": None}, {"driver_path": ""}, {"driver_path": None},
    {"driver_path": "missing.exe"}, {"driver_path": __file__},
    {"master104_timeout": 0}, {"master104_timeout": -1}, {"master104_timeout": True},
    {"master104_timeout": "bad"}, {"master104_timeout": float("inf")},
    {"master104_timeout": float("nan")},
])
def test_invalid_master104_configuration_never_probes(tmp_path, monkeypatch, changes):
    """无效配置在启动主站或 EdgeDriver 前明确拒绝。"""
    calls = []
    install_probe(monkeypatch, lambda *args, **kwargs: calls.append((args, kwargs)))
    bridge = TestbenchBridge(tmp_path)
    with pytest.raises(ValueError):
        bridge.handle("check_master104", {"environment": master_environment(tmp_path, **changes)})
    assert calls == []


def test_legacy_master_program_must_be_reselected(tmp_path):
    """旧配置可读取，但旧程序存在也不可用于主站检查或测试。"""
    other = tmp_path / "ProIEC104Client.exe"
    other.touch()
    data = master_environment(tmp_path, master104_path=str(other), master104_backend="win32")
    env = Environment.from_dict(data)
    assert "master104_backend" not in env.to_dict()
    with pytest.raises(ValueError, match="Msk104Master"):
        env.validate_master104(required=True)


@pytest.mark.parametrize("active,enabled", [(True, True), (False, False)])
def test_running_task_or_disabled_master104_cannot_probe(tmp_path, monkeypatch, active, enabled):
    """运行任务时和主站禁用时均不能发起检查。"""
    calls = []
    install_probe(monkeypatch, lambda *args, **kwargs: calls.append(args))
    bridge = TestbenchBridge(tmp_path)
    bridge.controller.active = active
    with pytest.raises(ValueError):
        bridge.handle("check_master104", {"environment": master_environment(tmp_path, master104_enabled=enabled)})
    assert calls == []


def test_probe_is_nonblocking_and_mutually_exclusive_with_tasks(tmp_path, monkeypatch):
    """检查在独立线程等待，桥接仍响应；重复检查与启动任务被拒绝。"""
    entered, release = threading.Event(), threading.Event()
    snapshot = {"pid": 123, "title": "104 模拟主站", "observed_at": "2026-11-04T12:00:00Z"}
    data = master_environment(tmp_path)

    def probe(path, driver_path, timeout, cancel_event=None):
        assert path == data["master104_path"]
        assert driver_path == data["driver_path"] and timeout == 2
        entered.set()
        if not release.wait(2):
            raise TimeoutError("自测检查未释放")
        return snapshot

    install_probe(monkeypatch, probe)
    bridge = TestbenchBridge(tmp_path)
    try:
        assert bridge.handle("check_master104", {"environment": data}) == {"checking": True}
        assert entered.wait(1)
        assert bridge.handle("poll_master104") == {"checking": True}
        assert bridge.handle("get_environment")["master104_enabled"] is False
        for method, payload in (("check_master104", {"environment": data}),
                                ("start", {}), ("save_environment", {}), ("load_environment", {})):
            with pytest.raises(ValueError, match="检查"):
                bridge.handle(method, payload)
        release.set()
        bridge._master104_check.result(timeout=2)
        result = bridge.handle("poll_master104")
        assert result["checking"] is False
        assert result["snapshot"] == snapshot
        assert "尚未验证" in result["message"]
        assert not (tmp_path / "local" / "environment.json").exists()
    finally:
        release.set()


def test_probe_error_is_reported_and_next_check_is_allowed(tmp_path, monkeypatch):
    """读取超时明确失败，不返回旧快照；读取错误后可重新检查。"""
    def fail(*args, **kwargs):
        raise TimeoutError("104 主站读取超时")

    install_probe(monkeypatch, fail)
    bridge = TestbenchBridge(tmp_path)
    payload = {"environment": master_environment(tmp_path)}
    bridge.handle("check_master104", payload)
    with pytest.raises(TimeoutError):
        bridge._master104_check.result(timeout=2)
    with pytest.raises(TimeoutError, match="主站读取超时"):
        bridge.handle("poll_master104")
    install_probe(monkeypatch, lambda *args, **kwargs: {"pid": 456})
    bridge.handle("check_master104", payload)
    bridge._master104_check.result(timeout=2)
    assert bridge.handle("poll_master104")["snapshot"] == {"pid": 456}


def test_result_poll_does_not_invent_a_probe_result(tmp_path):
    """未进行检查时不能返回伪造主站状态。"""
    with pytest.raises(ValueError, match="没有待查询"):
        TestbenchBridge(tmp_path).handle("poll_master104")


def test_shutdown_cancels_probe_before_exiting(tmp_path, monkeypatch):
    """退出等待本次检查释放主站及驱动资源。"""
    entered = threading.Event()
    finished = threading.Event()

    def probe(*args, cancel_event, **kwargs):
        entered.set()
        if not cancel_event.wait(2):
            raise TimeoutError("未收到取消检查信号")
        finished.set()
        raise InterruptedError("测试检查已停止")

    install_probe(monkeypatch, probe)
    bridge = TestbenchBridge(tmp_path)
    bridge.handle("check_master104", {"environment": master_environment(tmp_path)})
    assert entered.wait(1)
    assert bridge.handle("shutdown") == {"closed": True}
    assert finished.is_set()
    assert bridge._master104_check is None


@pytest.mark.parametrize("enabled,expected", [(False, 140), (True, 620)])
def test_task_deadline_accounts_for_enabled_master104(tmp_path, monkeypatch, enabled, expected):
    """长主站等待不会被任务期限提前截断；未启用时保留原期限。"""
    from testbench import controller as module
    case_id = "tests/test_later.py::test_later"
    monkeypatch.setattr(module, "discover_cases", lambda root: [SimpleNamespace(id=case_id, name="后续用例")])
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=42))
    env = Environment(timeout=2, master104_enabled=enabled, master104_timeout=50)
    monkeypatch.setattr(env, "validate", lambda: None)
    task = module.TaskController(tmp_path)
    task.start(env, [case_id])
    assert task._timeout == expected
