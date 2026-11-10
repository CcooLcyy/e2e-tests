"""RTU 用例在启动上位机前校验各自需要的专项配置。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

class DesktopBoundary(Exception):
    """证明已完成配置读取，实际桌面入口在自测中不执行。"""


@pytest.mark.parametrize("filename,fixture,profile_name", [
    ("test_modbus_rtu.py", "rtu", "modbus-rtu"),
    ("test_modbus_rtu.py", "independent_rtu", "modbus-rtu-independent"),
    ("test_huawei_fleet.py", "huawei_fleet", "huawei-rtu"),
])
def test_fixture_requires_its_own_environment_profile(tmp_path, monkeypatch, filename, fixture, profile_name):
    """每例只需所属场景的配置，缺少时在打开桌面前失败。"""
    source = Path(__file__).resolve().parents[1] / "tests" / filename
    spec = importlib.util.spec_from_file_location("fixture_environment_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("TESTBENCH_DATA_ROOT", str(tmp_path))
    module.load_login_account = lambda _: SimpleNamespace(username="test", password="test")
    environment = {"slavesim_url": "http://192.0.2.1:16000", "modbus_acquisition_timeout": 30}
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"environment": environment}), encoding="utf-8")
    monkeypatch.setenv("TESTBENCH_TASK", str(task))
    def stop_before_desktop(name):
        assert name == "desktop"
        raise DesktopBoundary()
    request = SimpleNamespace(node=SimpleNamespace(), getfixturevalue=stop_before_desktop)
    prepare = getattr(module, fixture).__wrapped__
    with pytest.raises(RuntimeError, match="前置条件未满足|外部测试环境未就绪"):
        next(prepare(request))
    local = tmp_path / "local"
    local.mkdir()
    example = source.parents[1] / "docs" / "examples" / f"{profile_name}.example.json"
    (local / f"{profile_name}.json").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(DesktopBoundary):
        next(prepare(request))
