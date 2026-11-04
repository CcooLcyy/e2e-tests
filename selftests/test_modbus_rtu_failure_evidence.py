"""业务等待超时的证据与异常保留，不启动任何浏览器。"""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from testbench.modbus_rtu_config import RtuStation


def scenario_type():
    path = Path(__file__).resolve().parents[1] / "tests" / "test_modbus_rtu.py"
    spec = importlib.util.spec_from_file_location("rtu_business_cases", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RtuScenario


class EvidenceDesktop:
    def __init__(self, directory):
        self.artifacts = directory
        self.metadata = {"RTU最后观察": {"owned/tag": {"value": "999", "quality": "正常", "timestamp": "12:00:01"}}}

    @contextmanager
    def step(self, *args, **kwargs):
        yield

    def check(self, name, actual, expected):
        assert actual == expected


@pytest.mark.parametrize("simulator_failed", [False, True])
def test_timeout_keeps_both_observations_without_resending_business(tmp_path, simulator_failed):
    """上位机超时仍保存固定期望与最后值，模拟器故障也不能遮蔽原错误。"""
    original = AssertionError("上位机未在20秒内达到1234")
    calls = []

    def wait_value(*args, **kwargs):
        calls.append("等待上位机")
        raise original

    def observe(*args):
        calls.append("读取模拟器")
        if simulator_failed:
            raise RuntimeError("模拟器连接中断")
        return {"value": 1234, "link_path": "/dev/rtu-test"}

    desktop = EvidenceDesktop(tmp_path)
    upper = SimpleNamespace(select=lambda _: None, wait_value=wait_value)
    simulator = SimpleNamespace(observe=observe)
    environment = SimpleNamespace(stations=(RtuStation(1, 0),), bus="专用总线",
                                  serial_device="/dev/rtu-test", acquisition_timeout=20)
    scenario = scenario_type()(desktop, simulator, upper, environment)
    scenario.connections[0] = {"name": "owned", "tag": "tag", "scale": 1, "offset": 0}
    with pytest.raises(AssertionError) as caught:
        scenario.verify(0, 1234)
    assert caught.value is original
    assert calls == ["等待上位机", "读取模拟器"]
    saved = json.loads((tmp_path / "modbus-rtu-observations.json").read_text(encoding="utf-8"))[0]
    assert saved["原始期望"] == "1234" and saved["工程量期望"] == "1234"
    assert saved["DSP上位机"]["value"] == "999"
    assert saved["观察错误"] == str(original)
    if simulator_failed:
        assert "连接中断" in saved["slaveSim"]["观察错误"]
    else:
        assert saved["slaveSim"]["value"] == 1234
