"""原RTU用例从任务快照取参数，无需两个独立配置文件。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from testbench.huawei_fleet import HuaweiFleetConfig
from testbench.modbus_rtu_config import RtuEnvironment


class DesktopBoundary(Exception):
    """证明已完成配置读取，实际桌面入口在自测中不执行。"""


@pytest.mark.parametrize("filename,fixture", [
    ("test_modbus_rtu.py", "rtu"), ("test_huawei_fleet.py", "huawei_fleet"),
])
def test_existing_fixture_reads_task_environment_without_sidecar_files(tmp_path, monkeypatch, filename, fixture):
    """只有环境快照、没有独立JSON时也能完成前置读取，未接触真实浏览器。"""
    source = Path(__file__).resolve().parents[1] / "tests" / filename
    spec = importlib.util.spec_from_file_location("fixture_environment_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    module.load_login_account = lambda _: SimpleNamespace(username="test", password="test")
    environment = {"mode": "fresh", "slavesim_url": "http://192.0.2.1:16000",
                   "modbus_manager_address": "192.0.2.1:50051", "modbus_baud_rate": 9600,
                   "modbus_poll_interval_ms": 1000, "modbus_acquisition_timeout": 30,
                   "modbus_basic": {"device_name": "测试设备", "bus": "专用总线", "serial_device": "/dev/rtu-test",
                       "stations": [{"station_id": 1, "address": 0}, {"station_id": 2, "address": 0}],
                       "dedicated_test_resources": True, "data_bits": 8, "stop_bits": 1, "parity": "无校验"},
                   "modbus_huawei": {"dedicated_simulator": True, "serial_devices": [f"/dev/rtu-{i}" for i in range(6)]}}
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"environment": environment}), encoding="utf-8")
    monkeypatch.setenv("TESTBENCH_TASK", str(task))
    def forbidden_file(*args, **kwargs):
        pytest.fail("用例仍在读取旧的独立RTU配置文件")
    monkeypatch.setattr(RtuEnvironment, "load", forbidden_file)
    monkeypatch.setattr(HuaweiFleetConfig, "load", forbidden_file)
    def stop_before_desktop(name):
        assert name == "desktop"
        raise DesktopBoundary()
    request = SimpleNamespace(getfixturevalue=stop_before_desktop)
    generator = getattr(module, fixture).__wrapped__(request)
    with pytest.raises(DesktopBoundary):
        next(generator)
