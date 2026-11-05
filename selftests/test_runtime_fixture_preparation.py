"""验证运行时发现入口和发现失败取证，不连接浏览器或外部设备。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("filename,fixture", [
    ("test_modbus_rtu.py", "rtu"), ("test_huawei_fleet.py", "huawei_fleet"),
])
def test_runtime_preparation_failure_preserves_both_pages(tmp_path, monkeypatch, filename, fixture):
    """旧配置无效也进入实际模拟器准备；准备失败必须保留两端现场及原异常。"""
    source = Path(__file__).resolve().parents[1] / "tests" / filename
    spec = importlib.util.spec_from_file_location("runtime_fixture_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    environment = {"mode": "fresh", "slavesim_url": "http://192.0.2.1:16000",
                   "modbus_manager_address": "192.0.2.1:50051",
                   "modbus_basic": {"serial_device": "过期路径", "dedicated_test_resources": False},
                   "modbus_huawei": {"serial_devices": ["过期路径"], "dedicated_simulator": False}}
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"environment": environment}), encoding="utf-8")
    monkeypatch.setenv("TESTBENCH_TASK", str(task))

    desktop = SimpleNamespace(metadata={}, failure="", artifacts=tmp_path,
                              capture=lambda *a, **kw: calls.append("DSP现场"))

    class Simulator:
        def __init__(self, desktop, url):
            assert url == environment["slavesim_url"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("模拟器关闭")

        def prepare_basic(self, supplied, *, check_paths=None):
            assert supplied == environment
            assert callable(check_paths)
            calls.append("运行时准备")
            raise RuntimeError("实际模拟器准备失败")

        def prepare_fleet(self, supplied, run_id, *, check_paths=None):
            assert callable(check_paths)
            return self.prepare_basic(supplied, check_paths=check_paths)

        def capture(self, *args, **kwargs):
            calls.append("模拟器现场")

    class Upper:
        def __init__(self, desktop):
            pass

        def open_device(self, address):
            assert address == environment["modbus_manager_address"]
            calls.append("连接DSP")

        def cleanup(self):
            calls.append("连接清理")

        def assert_serial_paths_available(self, paths):
            calls.append("只读检查串口占用")

    class Login:
        def __init__(self, desktop):
            pass

        def login(self, *args, **kwargs):
            calls.append("登录")

    monkeypatch.setattr("testbench.slavesim_page.SlaveSimPage", Simulator)
    monkeypatch.setattr("testbench.huawei_slavesim_page.HuaweiSlaveSimPage", Simulator)
    monkeypatch.setattr("testbench.modbus_rtu_page.ModbusRtuPage", Upper)
    monkeypatch.setattr("testbench.huawei_rtu_page.HuaweiRtuPage", Upper)
    monkeypatch.setattr(module, "PlatformLoginPage", Login)
    monkeypatch.setattr(module, "load_login_account", lambda _: object())
    request = SimpleNamespace(node=SimpleNamespace(_bench_reports=[]),
                              getfixturevalue=lambda _: desktop)
    generator = getattr(module, fixture).__wrapped__(request)
    with pytest.raises(RuntimeError, match="实际模拟器准备失败"):
        next(generator)
    assert calls[:3] == ["登录", "连接DSP", "运行时准备"]
    assert calls.index("模拟器现场") < calls.index("模拟器关闭")
    assert calls.index("DSP现场") < calls.index("模拟器关闭")


def test_huawei_scenario_uses_prepared_actual_bus_names(tmp_path):
    """业务场景保留模拟器实际名称，不重新生成与现场不对应的总线名。"""
    from testbench.huawei_fleet import HuaweiFleetConfig, build_fleet
    from testbench.huawei_fleet_run import HuaweiFleetRun

    config = HuaweiFleetConfig("192.0.2.1:50051", tuple(f"/srv/serial-sim/tty{i}" for i in range(6)))
    buses = build_fleet(config, "original")
    for index, bus in enumerate(buses):
        bus["name"] = f"现场485-{index}"
    desktop = SimpleNamespace(metadata={})
    scenario = HuaweiFleetRun(desktop, None, None, config, "different", buses=buses)
    assert scenario.buses is buses
    assert scenario.buses[0]["name"] == "现场485-0"
