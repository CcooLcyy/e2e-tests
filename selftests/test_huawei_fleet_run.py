"""机群观测与汇总边界，不启动浏览器、不修改外部设备。"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from case_support.huawei_fleet import HuaweiFleetConfig, READ_POINTS, point_expected
from case_support.huawei_fleet_run import HuaweiFleetRun, STAGES


def load_business_case():
    path = Path(__file__).resolve().parents[1] / "case-suite" / "tests" / "test_huawei_fleet.py"
    spec = importlib.util.spec_from_file_location("huawei_fleet_business_case", path)
    case = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(case)
    return case


def run_business_plan(scenario):
    case = load_business_case()
    request = SimpleNamespace(node=SimpleNamespace(nodeid="huawei-fleet"))
    case.run_steps(request, case.BUSINESS_STEPS, scenario)


class Desktop:
    def __init__(self, path):
        self.artifacts, self.metadata, self.screenshots = path, {}, []

    @contextmanager
    def step(self, *args, **kwargs):
        yield

    def _check_stop(self):
        pass

    def check(self, name, actual, expected):
        assert actual == expected, name


def sample_run(tmp_path):
    config = HuaweiFleetConfig(tuple(f"/dev/pts/{i}" for i in range(6)), acquisition_timeout=30)
    scenario = HuaweiFleetRun(Desktop(tmp_path), None, None, config, "unit")
    for bus in scenario.buses:
        bus["link_path"] = f"/run/slavesim/{bus['name']}"
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
        return {"bus": bus["name"], "station_id": 1, "actual_device": bus["serial_device"], "link_path": bus["link_path"],
                "registers": deepcopy(registers), "refresh_tick": tick}

    observations = {}
    for point in READ_POINTS:
        value = point_expected(point, registers[point.address]["value"])
        if point.data_type != "BOOL":
            value = str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        observations[point.key] = {"value": value, "quality": "正常", "timestamp": "12:00:02",
                                   "address": str(point.address), "function": "0x03 读保持寄存器"}
    scenario.simulator = SimpleNamespace(observe_device=observe)
    scenario.upper = SimpleNamespace(select=lambda _: None, observe_device=lambda _: deepcopy(observations))
    scenario.connections["1/1"] = {"name": "owned", "tags": {point.key: point.key for point in READ_POINTS}}
    return scenario, bus, device, registers, observations


def test_stable_device_requires_all_seventeen_points_and_fresh_source(tmp_path):
    """同一设备的全部遥测与状态均匹配且有两次新源采样才能通过。"""
    scenario, bus, device, _, _ = sample_run(tmp_path)
    result = scenario.sample("发电", bus, device)
    assert result["通过"] and len(result["点位"]) == 17
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
    """17 个采集点任意一个偏离独立模拟器源值，即使质量和时间有效仍失败。"""
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
    assert not result["已观察到预定源工况"]
    assert any("模拟器工况未就绪" in error for error in result["工况错误"])


def test_late_matching_observation_cannot_pass_deadline(tmp_path, monkeypatch):
    """数值完全匹配但一轮页面取数耗时31秒，仍违反30秒验收时限。"""
    scenario, bus, device, _, _ = sample_run(tmp_path)
    ticks = iter((0, 31, 31))
    monkeypatch.setattr("case_support.huawei_fleet_run.time.monotonic", lambda: next(ticks))
    result = scenario.sample("发电", bus, device)
    assert not result["通过"]
    assert all(point["通过"] for point in result["点位"])
    assert any("超过30秒" in error for error in result["工况错误"])


def test_creation_uses_the_installed_serial_path_and_case_owned_tags(tmp_path):
    scenario, bus, device, *_ = sample_run(tmp_path)
    connections, points, checked, copied, started = [], [], [], [], []
    name = "e2e-huawei-unit-r1-s1"
    point_table = {f"{name}-{point.key}": {"地址": str(point.address)} for point in READ_POINTS}
    scenario.upper = SimpleNamespace(create_connection=lambda *args: connections.append(args),
                                     add_huawei_point=lambda *args: points.append(args),
                                     assert_saved_configuration=lambda: checked.append(True),
                                     read_point_configuration=lambda: deepcopy(point_table),
                                     copy_connection=lambda *args: copied.append(args),
                                     start=lambda: started.append(True))
    bus_paths = {bus["name"]: (bus["serial_device"], bus["link_path"]) for bus in scenario.buses}
    scenario.simulator = SimpleNamespace(observe_bus_paths=bus_paths.__getitem__)
    device["point_addresses"] = {point.key: point.address for point in READ_POINTS}
    scenario.create_device(bus, device)

    copy_bus = scenario.buses[1]
    copy_device = copy_bus["devices"][1]
    copy_device["point_addresses"] = dict(device["point_addresses"])
    scenario.create_device(copy_bus, copy_device)

    assert connections == [(name, "/dev/pts/0", 9600, 8, 1, "N", 1, 1000)]
    assert points == [(point, f"{name}-{point.key}") for point in READ_POINTS]
    assert checked == [True]
    assert copied == [(name, "e2e-huawei-unit-r2-s2", "/dev/pts/1", 2)]
    assert started == [True, True]
    assert scenario.connections["1/1"]["name"] == name
    assert scenario.connections["2/2"] == {"name": "e2e-huawei-unit-r2-s2", "tags": scenario.connections["1/1"]["tags"]}


def test_one_failed_device_stage_is_retained_while_other_devices_finish(tmp_path):
    """单台失败不放宽断言，完成其他独立观测后总结果仍失败且不能写全部抄通。"""
    scenario, *_ = sample_run(tmp_path)
    scenario.apply_stage = lambda _: None
    scenario.capture_both = lambda *args: []
    def sample(stage, bus, device):
        return {"工况": stage, "路号": bus["index"], "站号": device["station_id"], "点位": [],
                "通过": not (stage == STAGES[0] and bus["index"] == 1), "工况错误": ["保留原始失败"]}
    scenario.sample = sample
    with pytest.raises(AssertionError, match="失败1项"):
        run_business_plan(scenario)
    assert len(scenario.results) == 21 * len(STAGES)
    assert scenario.results[0]["通过"] is False
    assert scenario.desktop.metadata["华为验收汇总"]["全部抄通"] is False


def test_missing_screenshot_prevents_green_device_stage(tmp_path):
    """数据全匹配但双端取证缺失时，该工况与整台结果仍失败。"""
    scenario, *_ = sample_run(tmp_path)
    scenario.apply_stage = lambda _: None
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
        run_business_plan(scenario)
    assert scenario.results[0]["通过"] is False
    assert "双端证据缺失" in scenario.results[0]["工况错误"][0]
    assert scenario.desktop.metadata["华为验收汇总"]["全部抄通"] is False


@pytest.mark.parametrize("business_result", ["passed", "failed", "cancelled"])
def test_fleet_cleanup_failure_captures_both_pages_and_still_clears_buses(tmp_path, monkeypatch, business_result):
    case = load_business_case()
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"environment": {"slavesim_url": "http://localhost:16000"}}), encoding="utf-8")
    monkeypatch.setenv("TESTBENCH_TASK", str(task))
    monkeypatch.delenv("TESTBENCH_STOP", raising=False)
    calls = []
    upper_error = RuntimeError("上位机连接清理失败")
    desktop = SimpleNamespace(artifacts=tmp_path, metadata={}, screenshots=[], failure=None,
                              capture=lambda *args, **kwargs: calls.append("上位机现场"))

    class Simulator:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls.append("关闭浏览器")

        def prepare_owned_fleet(self, *_args, **_kwargs):
            return None, []

        def capture(self, *_args, **_kwargs):
            calls.append("模拟器现场")

        def clear_all_buses(self):
            calls.append("清空总线")
            raise RuntimeError("模拟器清理失败")

    def cleanup_upper():
        calls.append("清理上位机")
        raise upper_error

    upper = SimpleNamespace(creation_started=True, connect_site=lambda: None, open_current_links=lambda: None,
                            assert_serial_paths_available=None, cleanup=cleanup_upper)
    monkeypatch.setattr("case_support.huawei_slavesim_page.HuaweiSlaveSimPage", Simulator)
    monkeypatch.setattr(case, "HuaweiRtuPage", lambda _: upper)
    monkeypatch.setattr(case, "load_login_account", lambda _: object())
    monkeypatch.setattr(case, "run_steps", lambda *_: None)
    fixtures = {"slavesim_browser": object(), "fresh_desktop": desktop}
    request = SimpleNamespace(node=SimpleNamespace(_bench_reports=[]), getfixturevalue=fixtures.__getitem__)
    generator = case.huawei_fleet.__wrapped__(request)
    next(generator)

    if business_result == "cancelled":
        stop = tmp_path / "stop"
        stop.touch()
        monkeypatch.setenv("TESTBENCH_STOP", str(stop))
    request.node._bench_reports.append(SimpleNamespace(failed=business_result == "failed"))
    if business_result == "passed":
        with pytest.raises(RuntimeError) as caught:
            next(generator)
        assert caught.value is upper_error
    else:
        with pytest.raises(StopIteration):
            next(generator)

    prior_evidence = ["模拟器现场", "上位机现场"] if business_result != "passed" else []
    assert calls == prior_evidence + ["清理上位机", "模拟器现场", "上位机现场", "清空总线", "关闭浏览器"]
    assert desktop.metadata["测试资源清理错误"] == [
        f"停止并删除本次机群连接：{upper_error}",
        "清空 slaveSim 全部总线：模拟器清理失败",
    ]
