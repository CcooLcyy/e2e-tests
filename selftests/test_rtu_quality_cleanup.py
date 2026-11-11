"""核对品质用例的清理顺序及失败保留。"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def quality_case():
    path = Path(__file__).resolve().parents[1] / "case-suite" / "tests" / "test_modbus_rtu_104_quality.py"
    spec = importlib.util.spec_from_file_location("rtu_quality_case", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("preparation_started", [False, True])
def test_upper_cleanup_failure_leaves_evidence_and_clears_when_preparation_started(
        quality_case, monkeypatch, preparation_started):
    original = RuntimeError("上位机清理失败")
    calls = []

    def cleanup_upper(_):
        calls.append("清理上位机")
        raise original

    desktop = SimpleNamespace(metadata={}, capture=lambda *args, **kwargs: calls.append("上位机截图"))
    simulator = SimpleNamespace(capture=lambda *args, **kwargs: calls.append("slaveSim 截图"),
                                clear_all_buses=lambda: calls.append("清空总线"))
    context = SimpleNamespace(desktop=desktop, simulator=simulator,
                              simulator_preparation_started=preparation_started)
    request = SimpleNamespace(node=SimpleNamespace())
    monkeypatch.setattr(quality_case, "_cleanup_upper", cleanup_upper)

    with pytest.raises(RuntimeError) as caught:
        quality_case._cleanup(context, request, None)

    assert caught.value is original
    assert calls == ["清理上位机", "上位机截图", "slaveSim 截图"] + (["清空总线"] if preparation_started else [])


def test_preparation_failure_before_creating_paths_still_clears_and_preserves_error(quality_case, monkeypatch):
    original = AssertionError("读取实际配置失败")
    calls = []

    def prepare(_):
        raise original

    def clear_all_buses():
        calls.append("清空总线")
        raise RuntimeError("模拟器清理失败")

    desktop = SimpleNamespace(metadata={})
    simulator = SimpleNamespace(prepare=prepare, clear_all_buses=clear_all_buses, paths={})
    context = SimpleNamespace(desktop=desktop, simulator=simulator, simulator_preparation_started=False,
                              rtu=SimpleNamespace(assert_serial_paths_available=None))
    request = SimpleNamespace(node=SimpleNamespace())
    monkeypatch.setattr(quality_case, "_cleanup_upper", lambda _: calls.append("清理上位机"))

    with pytest.raises(AssertionError) as caught:
        try:
            quality_case._prepare_simulator(context)
        except AssertionError as error:
            quality_case._cleanup(context, request, error)
            raise

    assert caught.value is original
    assert calls == ["清理上位机", "清空总线"]
    assert desktop.metadata["RTU品质清理错误"] == ["清空 slaveSim 全部总线：模拟器清理失败"]
