"""RTU 用例无需手填拓扑或下位机地址即可进入业务准备。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

class DesktopBoundary(Exception):
    """证明已完成配置读取，实际桌面入口在自测中不执行。"""


@pytest.mark.parametrize("filename,fixture", [
    ("test_modbus_rtu.py", "independent_rtu"),
    ("test_modbus_rtu.py", "two_station_rtu"),
    ("test_huawei_fleet.py", "huawei_fleet"),
])
def test_fixture_uses_supplied_entries_without_a_topology_profile(tmp_path, monkeypatch, filename, fixture):
    source = Path(__file__).resolve().parents[1] / "tests" / filename
    spec = importlib.util.spec_from_file_location("fixture_environment_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("TESTBENCH_DATA_ROOT", str(tmp_path))
    def read_account(path):
        assert path.name == "login-account.json"
        return SimpleNamespace(username="test", password="test")

    module.load_login_account = read_account
    environment = {"slavesim_url": "http://192.0.2.1:16000", "modbus_acquisition_timeout": 30}
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"environment": environment}), encoding="utf-8")
    monkeypatch.setenv("TESTBENCH_TASK", str(task))
    def stop_before_desktop(name):
        assert name == "fresh_desktop"
        raise DesktopBoundary()
    request = SimpleNamespace(node=SimpleNamespace(), getfixturevalue=stop_before_desktop)
    prepare = getattr(module, fixture).__wrapped__
    with pytest.raises(DesktopBoundary):
        next(prepare(request))
