"""机群观测与汇总边界，不启动浏览器、不修改外部设备。"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from testbench.huawei_fleet import HuaweiFleetConfig, READ_POINTS, point_expected
from testbench.huawei_fleet_run import HuaweiFleetRun, STAGES


class Desktop:
    def __init__(self, path):
        self.artifacts, self.metadata, self.screenshots = path, {}, []

    @contextmanager
    def step(self, *args, **kwargs):
        yield

    def _check_stop(self):
        pass


def sample_run(tmp_path):
    config = HuaweiFleetConfig("192.0.2.1:50051", tuple(f"/dev/rtu{i}" for i in range(6)), acquisition_timeout=30)
    scenario = HuaweiFleetRun(Desktop(tmp_path), None, None, config, "unit")
    bus, device = scenario.buses[0], scenario.buses[0]["devices"][0]
    registers = {point.address: {"value": 0, "type": "UINT16" if point.data_type == "BOOL" else point.data_type}
                 for point in READ_POINTS}
    for address in (32069, 32070, 32071):
        registers[address]["value"] = int(Decimal(device["grid_line_voltage_v"]) / Decimal(3).sqrt() * 10)
    for address in (32072, 32074, 32076):
        registers[address]["value"] = 16669
    for address, value in {32080: 11000, 32082: 0, 32084: 1000, 32000: 6, 32089: 512,
                           35303: 40125, 35307: 40122}.items():
        registers[address]["value"] = value
    registers[30075] = {"value": 55000, "type": "UINT32"}
    tick = 0

    def observe(*args):
        nonlocal tick
        tick += 1
        return {"bus": bus["name"], "station_id": 1, "link_path": bus["serial_device"],
                "registers": deepcopy(registers), "refresh_tick": tick}

    observations = {point.key: {"value": (point_expected(point, registers[point.address]["value"])
                                         if point.data_type == "BOOL" else str(point_expected(point, registers[point.address]["value"]))),
                                "quality": "正常", "timestamp": "12:00:02", "address": str(point.address),
                                "function": "0x03 读保持寄存器"} for point in READ_POINTS}
    scenario.simulator = SimpleNamespace(observe_device=observe)
    scenario.upper = SimpleNamespace(select=lambda _: None, observe_device=lambda _: deepcopy(observations))
    scenario.connections["1/1"] = {"name": "owned", "tags": {point.key: point.key for point in READ_POINTS}}
    return scenario, bus, device, registers, observations


def test_stable_device_requires_all_eighteen_points_and_fresh_source(tmp_path):
    """同一设备的全部遥测与状态均匹配且有两次新源采样才能通过。"""
    scenario, bus, device, _, _ = sample_run(tmp_path)
    result = scenario.sample("发电", bus, device)
    assert result["通过"] and len(result["点位"]) == 18
    assert result["模拟器后采样"]["refresh_tick"] > result["模拟器前采样"]["refresh_tick"]


@pytest.mark.parametrize("stage", ["发电", "恢复发电"])
def test_unit_efficiency_generation_and_recovery_reject_old_loss_target(tmp_path, stage):
    """发电和恢复均要求11kW；两端相同的旧10.78kW也不能通过固定工况校验。"""
    scenario, _bus, device, raw, _upper = sample_run(tmp_path)
    snapshot = {"registers": raw}
    assert scenario._physics_errors(stage, device, snapshot) == []
    raw[32080]["value"] = 10780
    assert any("固定目标11kW" in error for error in scenario._physics_errors(stage, device, snapshot))


@pytest.mark.parametrize("point", READ_POINTS, ids=lambda point: point.key)
def test_each_collected_point_is_compared_with_independent_simulator_value(tmp_path, point):
    """18个采集点任意一个偏离独立模拟器源值，即使质量和时间有效仍失败。"""
    scenario, bus, device, raw, upper = sample_run(tmp_path)
    scenario.config = replace(scenario.config, acquisition_timeout=0)
    original = deepcopy(raw)
    value = upper[point.key]["value"]
    upper[point.key]["value"] = not value if point.data_type == "BOOL" else str(Decimal(value) + 1)
    result = scenario.sample("发电", bus, device)
    assert raw == original
    assert not result["通过"]
    failed = [row for row in result["点位"] if not row["通过"]]
    assert len(failed) == 1
    assert failed[0]["点标识"] == point.key


def test_same_values_cannot_hide_wrong_register_identity(tmp_path):
    """上位机值恰好相等但地址错误，仍失败，防止对称三相掩盖错配置。"""
    scenario, bus, device, _, upper = sample_run(tmp_path)
    scenario.config = replace(scenario.config, acquisition_timeout=0)
    upper["voltage_a"]["address"] = "32070"
    result = scenario.sample("发电", bus, device)
    assert not result["通过"]
    assert any("地址或功能码" in error for row in result["点位"] for error in row["错误"])


def test_positive_current_cannot_hide_wrong_signed_type(tmp_path):
    """正电流数值相同也必须发现INT32被错误配置为UINT32。"""
    scenario, bus, device, raw, _ = sample_run(tmp_path)
    scenario.config = replace(scenario.config, acquisition_timeout=0)
    raw[32072]["type"] = "UINT32"
    result = scenario.sample("发电", bus, device)
    assert not result["通过"]
    assert any("类型" in error for row in result["点位"] for error in row["错误"])


def test_source_with_wrong_scene_does_not_pass_when_both_ends_agree(tmp_path):
    """两端P相等但不符合本台可用直流功率和效率，工况仍失败。"""
    scenario, bus, device, raw, upper = sample_run(tmp_path)
    scenario.config = replace(scenario.config, acquisition_timeout=0)
    raw[32080]["value"] = 20000
    upper["active"]["value"] = "20"
    result = scenario.sample("发电", bus, device)
    assert not result["通过"] and any("固定目标" in error for error in result["工况错误"])


def test_late_matching_observation_cannot_pass_deadline(tmp_path, monkeypatch):
    """数值完全匹配但一轮页面取数耗时31秒，仍违反30秒验收时限。"""
    scenario, bus, device, _, _ = sample_run(tmp_path)
    ticks = iter((0, 31, 31))
    monkeypatch.setattr("testbench.huawei_fleet_run.time.monotonic", lambda: next(ticks))
    result = scenario.sample("发电", bus, device)
    assert not result["通过"]
    assert all(point["通过"] for point in result["点位"])
    assert any("超过30秒" in error for error in result["工况错误"])


def test_setup_path_conflict_stops_before_simulator_configuration(tmp_path):
    """已有连接占用目标串口时，不能先创建模型或修改模拟器配置。"""
    scenario, *_ = sample_run(tmp_path)
    def refuse(_):
        raise AssertionError("串口已占用")
    scenario.upper = SimpleNamespace(assert_serial_paths_available=refuse)
    scenario.simulator = SimpleNamespace(create_fleet=lambda _: pytest.fail("串口冲突后仍修改模拟器"))
    with pytest.raises(AssertionError, match="串口已占用"):
        scenario.configure()


def test_one_failed_device_stage_is_retained_while_other_devices_finish(tmp_path):
    """单台失败不放宽断言，完成其他独立观测后总结果仍失败且不能写全部抄通。"""
    scenario, *_ = sample_run(tmp_path)
    scenario.configure = lambda: None
    scenario.apply_stage = lambda *args: None
    scenario.capture_both = lambda *args: []
    def sample(stage, bus, device):
        return {"工况": stage, "路号": bus["index"], "站号": device["station_id"], "点位": [],
                "通过": not (stage == STAGES[0] and bus["index"] == 1), "工况错误": ["保留原始失败"]}
    scenario.sample = sample
    with pytest.raises(AssertionError, match="失败1项"):
        scenario.run()
    assert len(scenario.results) == 21 * len(STAGES)
    assert scenario.results[0]["通过"] is False
    assert scenario.desktop.metadata["华为验收汇总"]["全部抄通"] is False


def test_missing_screenshot_prevents_green_device_stage(tmp_path):
    """数据全匹配但双端取证缺失时，该工况与整台结果仍失败。"""
    scenario, *_ = sample_run(tmp_path)
    scenario.configure = lambda: None
    scenario.apply_stage = lambda *args: None
    def sample(stage, bus, device):
        return {"工况": stage, "路号": bus["index"], "站号": device["station_id"],
                "点位": [{"通过": True} for _ in READ_POINTS], "通过": True, "工况错误": []}
    calls = []
    def capture(title):
        if not calls:
            scenario.evidence_errors.append("slaveSim截图保存失败")
        calls.append(title)
        return []
    scenario.sample, scenario.capture_both = sample, capture
    with pytest.raises(AssertionError, match="失败1项"):
        scenario.run()
    assert scenario.results[0]["通过"] is False
    assert "双端证据缺失" in scenario.results[0]["工况错误"][0]
    assert scenario.desktop.metadata["华为验收汇总"]["全部抄通"] is False
