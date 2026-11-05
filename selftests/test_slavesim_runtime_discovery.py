"""运行时资源选择契约：只跟随页面实际配置，不读旧手填路径。"""
from copy import deepcopy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from testbench.slavesim_runtime import (basic_candidate, fleet_candidates, validate_topology,
                                       assert_existing_preserved, validate_fleet_capacity)
from testbench.slavesim_page import SlaveSimPage
from testbench.huawei_slavesim_page import HuaweiSlaveSimPage
from testbench.huawei_fleet import HuaweiFleetConfig, build_fleet


def manual(station, address):
    return {"station_id": station, "model": "", "registers": [{
        "function": "0x03", "address": address, "type": "UINT16", "source": "manual",
        "byte_order": "AB", "access": "readwrite"}]}


def huawei_registers(addresses):
    capacities = {30075: 55000, 30077: 55000, 30079: 30000, 30081: -30000}
    return [{"function": "0x03", "address": address, "source": "const", "value": capacities.get(address, 0)}
            for address in set(addresses) | capacities.keys()]


def test_basic_uses_actual_station_address_and_fixed_link():
    buses = [{"name": "现场总线", "link": "/srv/real/tty7", "slaves": [manual(7, 81), manual(23, 99)]}]
    before = deepcopy(buses)
    bus, stations = basic_candidate(buses)
    assert bus["link"] == "/srv/real/tty7"
    assert stations == [{"station_id": 7, "address": 81}, {"station_id": 23, "address": 99}]
    assert buses == before


@pytest.mark.parametrize("field,value", [("source", "const"), ("type", "INT16"),
                                          ("byte_order", "BA"), ("access", "readonly")])
def test_basic_does_not_repurpose_incompatible_registers(field, value):
    buses = [{"name": "现有", "link": "/srv/tty2", "slaves": [manual(1, 0), manual(2, 4)]}]
    buses[0]["slaves"][1]["registers"][0][field] = value
    assert basic_candidate(buses) is None


def test_duplicate_links_are_reported_instead_of_guessed():
    buses = [{"name": "一路", "link": "/srv/tty0", "slaves": []},
             {"name": "二路", "link": "/srv/tty0", "slaves": []}]
    with pytest.raises(ValueError, match="重复"):
        validate_topology(buses)


def test_adding_resources_requires_all_existing_configuration_definitions_unchanged():
    before = [{"name": "原有", "link": "/srv/tty1", "slaves": [manual(1, 5)]}]
    after = deepcopy(before) + [{"name": "新增", "link": "/srv/tty2", "slaves": [manual(1, 0)]}]
    assert_existing_preserved(before, after)
    after[0]["slaves"][0]["registers"][0]["address"] = 6
    with pytest.raises(AssertionError, match="既有配置定义"):
        assert_existing_preserved(before, after)


def test_fleet_requires_six_exact_station_sets_and_model_registers():
    addresses = {32089, 30075}
    buses = [{"name": f"实际{i}", "link": f"/srv/actual{i}", "slaves": [
        {"station_id": station, "model": "huawei_v3", "registers": huawei_registers(addresses)}
        for station in range(1, i + 1)]} for i in range(1, 7)]
    assert [bus["name"] for bus in fleet_candidates(buses, addresses)] == [f"实际{i}" for i in range(1, 7)]
    buses[-1]["slaves"][0]["model"] = ""
    assert fleet_candidates(buses, addresses) is None


class ReadOnlyDriver:
    def find_element(self, *_):
        return SimpleNamespace(click=lambda: None)


def environment():
    return {"modbus_manager_address": "192.0.2.1:50051", "modbus_basic": {
        "bus": "过期总线", "serial_device": "/dev/obsolete"}, "modbus_huawei": {
        "serial_devices": [f"/dev/old{i}" for i in range(6)]}, "modbus_baud_rate": 12345}


def test_basic_prepare_uses_live_topology_without_saving_or_manual_mapping(tmp_path):
    page = SlaveSimPage(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    page.driver = ReadOnlyDriver()
    page._read_configuration = lambda _: [{"name": "实际", "link": "/srv/live/tty9",
                                           "slaves": [manual(9, 10), manual(28, 17)]}]
    page._save_configuration = lambda _: pytest.fail("存在合适资源时不应保存整份配置")
    calls = []
    page.observe = lambda *args: calls.append(args) or {"link_path": "/srv/live/tty9"}
    checked = []
    def check_paths(paths):
        assert calls == []
        checked.append(paths)
    config = page.prepare_basic(environment(), check_paths=check_paths)
    assert config.serial_device == "/srv/live/tty9" and config.bus == "实际"
    assert [(station.station_id, station.address) for station in config.stations] == [(9, 10), (28, 17)]
    assert config.baud_rate == 9600
    assert checked == [("/srv/live/tty9",)]
    assert calls == [("实际", 9, "0x03", 10), ("实际", 28, "0x03", 17)]


def test_basic_runtime_path_mismatch_keeps_failure(tmp_path):
    page = SlaveSimPage(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    page.driver = ReadOnlyDriver()
    page._read_configuration = lambda _: [{"name": "实际", "link": "/srv/live/tty9",
                                           "slaves": [manual(1, 0), manual(2, 0)]}]
    page.observe = lambda *_: {"link_path": "/srv/another/tty9"}
    with pytest.raises(AssertionError, match="运行路径"):
        page.prepare_basic(environment())


def test_basic_path_conflict_prevents_observation_and_modification(tmp_path):
    page = SlaveSimPage(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    page._read_configuration = lambda _: [{"name": "实际", "link": "/srv/live/tty9",
                                           "slaves": [manual(1, 0), manual(2, 0)]}]
    page.observe = lambda *_: pytest.fail("路径占用时不得设置或读取测试点")
    page._save_configuration = lambda _: pytest.fail("路径占用时不得保存配置")
    def conflict(_):
        raise AssertionError("DSP串口路径已占用")
    with pytest.raises(AssertionError, match="已占用"):
        page.prepare_basic(environment(), check_paths=conflict)


def fleet_page(tmp_path):
    page = HuaweiSlaveSimPage(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    page.driver = ReadOnlyDriver()
    @contextmanager
    def step(*_args, **_kwargs):
        yield {}
    page._step = step
    page._snapshot = lambda result: None
    return page


@pytest.mark.parametrize("before", [[], [{"name": "已有空总线", "link": "/srv/empty", "slaves": []}]])
def test_fleet_starts_from_zero_and_creates_all_twenty_one_devices(tmp_path, before):
    """零从站才进入完整创建流程，空总线保留，串口来自实际新增结果。"""
    page = fleet_page(tmp_path)
    saved = deepcopy(before)
    calls = []
    page._read_configuration = lambda _: deepcopy(saved)
    def create(buses, *, discover_paths, check_paths):
        assert sum(len(bus["slaves"]) for bus in saved) == 0
        assert discover_paths is True
        calls.append("创建")
        for index, bus in enumerate(buses):
            bus["serial_device"] = f"/srv/new/tty{index}"
            saved.append({"name": bus["name"], "link": bus["serial_device"], "slaves": [
                {"station_id": device["station_id"], "model": "huawei_v3", "registers": []}
                for device in bus["devices"]]})
        check_paths(tuple(bus["serial_device"] for bus in buses))
    page.create_fleet = create
    page.observe_device = lambda bus, _station, _addresses: {
        "link_path": next(item["link"] for item in saved if item["name"] == bus)}
    checked = []
    config, actual = page.prepare_fleet(environment(), "本次", check_paths=checked.append)
    assert calls == ["创建"]
    assert [len(bus["devices"]) for bus in actual] == list(range(1, 7))
    assert config.serial_devices == tuple(f"/srv/new/tty{i}" for i in range(6))
    assert checked == [config.serial_devices]
    assert all(bus in saved for bus in before)
    evidence = page.desktop.metadata["华为初始逆变器检查"]
    assert evidence["已有从站数"] == evidence["已识别逆变器数"] == evidence["未确认类型从站数"] == 0


@pytest.mark.parametrize("model", ["huawei_v3", "未知模型", ""])
def test_any_existing_device_fails_before_creating_or_changing_resources(tmp_path, model):
    """已有模型或类型无法确认的静态从站均阻止零起点测试，不删除、不复用。"""
    page = fleet_page(tmp_path)
    before = [{"name": "其他总线", "link": "/srv/other", "slaves": [
        {"station_id": 17, "model": model, "registers": []}]}]
    original = deepcopy(before)
    page._read_configuration = lambda _: before
    page.create_fleet = lambda *_args, **_kwargs: pytest.fail("已有设备时不得创建或覆盖机群")
    page.apply_scenario = lambda *_args, **_kwargs: pytest.fail("已有设备时不得应用工况")
    page.apply_control = lambda *_: pytest.fail("已有设备时不得修改控制")
    def check_paths(_):
        pytest.fail("零起点失败时不得继续检查DSP资源")
    with pytest.raises(AssertionError, match="初始模拟逆变器必须为0台"):
        page.prepare_fleet(environment(), "本次", check_paths=check_paths)
    assert before == original
    evidence = page.desktop.metadata["华为初始逆变器检查"]
    assert evidence["已有从站数"] == 1
    assert len(evidence["设备"]) == 1


def test_complete_existing_fleet_is_rejected_instead_of_reused(tmp_path):
    """完整六路21台也必须前置失败，不能因配置匹配而绕过从零创建。"""
    page = fleet_page(tmp_path)
    before, _ = capacity_fleet()
    page._read_configuration = lambda _: deepcopy(before)
    page.create_fleet = lambda *_args, **_kwargs: pytest.fail("完整既有机群不得复用或重新创建")
    page.apply_scenario = lambda *_args, **_kwargs: pytest.fail("完整既有机群不得重置工况")
    page.apply_control = lambda *_: pytest.fail("完整既有机群不得重置控制")
    with pytest.raises(AssertionError, match="初始模拟逆变器必须为0台"):
        page.prepare_fleet(environment(), "本次")
    assert page.desktop.metadata["华为初始逆变器检查"]["已有从站数"] == 21


def test_zero_start_path_conflict_remains_a_precondition_failure(tmp_path):
    """确认零台后，DSP路径占用仍保持失败，不进入采集或工况操作。"""
    page = fleet_page(tmp_path)
    page._read_configuration = lambda _: []
    def create(_buses, *, discover_paths, check_paths):
        check_paths(("/srv/new/tty0",))
    page.create_fleet = create
    page.observe_device = lambda *_: pytest.fail("路径占用时不得读取测试点")
    def conflict(_):
        raise AssertionError("DSP串口路径已占用")
    with pytest.raises(AssertionError, match="已占用"):
        page.prepare_fleet(environment(), "本次", check_paths=conflict)


def capacity_fleet():
    planned = build_fleet(HuaweiFleetConfig("192.0.2.1:50051", tuple(f"/srv/tty{i}" for i in range(6))), "容量")
    actual = [{"name": bus["name"], "link": bus["serial_device"], "slaves": [
        {"station_id": device["station_id"], "model": "huawei_v3", "registers": huawei_registers({32089})}
        for device in bus["devices"]]} for bus in planned]
    return actual, planned


def test_default_fleet_capacity_supports_all_original_fixed_stages():
    actual, planned = capacity_fleet()
    before = deepcopy(actual)
    evidence = validate_fleet_capacity(actual, planned)
    assert len(evidence) == 21
    assert actual == before


@pytest.mark.parametrize("address,value,reason", [(30075, 1000, "固定发电目标"),
                                                  (30077, 1000, "固定发电目标"),
                                                  (30075, 400000, "固定限功目标"),
                                                  (30079, 1, "PF0.9"),
                                                  (30081, -1, "负Q/S")])
def test_incompatible_capacity_is_a_precondition_failure(address, value, reason):
    actual, planned = capacity_fleet()
    # 最后一台也必须在任何应用操作前得到校验，不能边修改边发现资源不适用。
    reg = next(reg for reg in actual[-1]["slaves"][-1]["registers"] if reg["address"] == address)
    reg["value"] = value
    with pytest.raises(AssertionError, match=reason):
        validate_fleet_capacity(actual, planned)


def test_missing_capacity_is_not_guessed_from_template_defaults():
    actual, planned = capacity_fleet()
    actual[0]["slaves"][0]["registers"] = [reg for reg in actual[0]["slaves"][0]["registers"] if reg["address"] != 30075]
    with pytest.raises(AssertionError, match="30075"):
        validate_fleet_capacity(actual, planned)


@pytest.mark.parametrize("count", [20, 22])
def test_created_fleet_readback_requires_exactly_twenty_one_devices(tmp_path, count):
    """从零创建后必须读回确认数量，少台或额外设备均保持失败。"""
    page = fleet_page(tmp_path)
    saved = []
    page._read_configuration = lambda _: deepcopy(saved)
    def create(buses, **_kwargs):
        for index, bus in enumerate(buses):
            bus["serial_device"] = f"/srv/new/tty{index}"
            saved.append({"name": bus["name"], "link": bus["serial_device"], "slaves": [
                {"station_id": device["station_id"], "model": "huawei_v3", "registers": []}
                for device in bus["devices"]]})
        if count == 20:
            saved[-1]["slaves"].pop()
        else:
            saved[-1]["slaves"].append({"station_id": 7, "model": "huawei_v3", "registers": []})
    page.create_fleet = create
    page.observe_device = lambda *_: pytest.fail("实际台数不符时不得进入运行验收")
    with pytest.raises(AssertionError, match="21"):
        page.prepare_fleet(environment(), "本次")
