"""预置 RTU 观察入口失败时，两端证据在观察会话关闭前采集。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("filename,fixture", [
    ("test_modbus_rtu.py", "independent_rtu"), ("test_huawei_fleet.py", "huawei_fleet"),
])
def test_runtime_preparation_failure_preserves_both_pages(tmp_path, monkeypatch, filename, fixture):
    """上位机入口失败时不进入任何环境配置，保留原错误与两端现场。"""
    source = Path(__file__).resolve().parents[1] / "tests" / filename
    spec = importlib.util.spec_from_file_location("runtime_fixture_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    environment = {"slavesim_url": "http://192.0.2.1:16000", "modbus_acquisition_timeout": 30}
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

        def capture(self, *args, **kwargs):
            calls.append("模拟器现场")

    class Upper:
        creation_started = False

        def __init__(self, desktop):
            pass

        def open_device(self, address):
            assert address == "192.0.2.1:50051"
            calls.append("打开业务点表")
            raise RuntimeError("RTU 业务页面未打开")

    monkeypatch.setattr("testbench.slavesim_page.SlaveSimPage", Simulator)
    monkeypatch.setattr("testbench.huawei_slavesim_page.HuaweiSlaveSimPage", Simulator)
    config = SimpleNamespace(manager_address="192.0.2.1:50051")
    if fixture == "independent_rtu":
        monkeypatch.setattr(module, "ModbusRtuPage", Upper)
        monkeypatch.setattr(module, "RtuEnvironment", SimpleNamespace(load=lambda *args, **kwargs: config))
    else:
        monkeypatch.setattr(module, "HuaweiRtuPage", Upper)
        monkeypatch.setattr(module, "load_dedicated_fleet", lambda *args: (config, []))
    monkeypatch.setattr(module, "run_steps", lambda *_: calls.append("登录"))
    monkeypatch.setattr(module, "load_login_account", lambda _: object())
    request = SimpleNamespace(node=SimpleNamespace(_bench_reports=[]),
                              getfixturevalue=lambda _: desktop)
    generator = getattr(module, fixture).__wrapped__(request)
    with pytest.raises(RuntimeError, match="RTU 业务页面未打开"):
        next(generator)
    assert calls[:2] == ["登录", "打开业务点表"]
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
