"""运行时资源选择契约：只跟随页面实际配置，不读旧手填路径。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from testbench.slavesim_runtime import (basic_candidate, fleet_candidates, validate_topology,
                                       assert_existing_preserved, validate_fleet_capacity)
from testbench.slavesim_page import SlaveSimPage
from testbench.huawei_slavesim_page import HuaweiSlaveSimPage
from testbench.huawei_fleet import READ_POINTS, HuaweiFleetConfig, build_fleet


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


def test_fleet_reuse_resets_commands_and_preserves_actual_names(tmp_path):
    page = HuaweiSlaveSimPage(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    page.driver = ReadOnlyDriver()
    addresses = {point.address for point in READ_POINTS} | {30075}
    buses = [{"name": f"现场{i}", "link": f"/srv/existing/tty{i}", "slaves": [
        {"station_id": station, "model": "huawei_v3", "registers": huawei_registers(addresses)}
        for station in range(1, i + 1)]} for i in range(1, 7)]
    page._read_configuration = lambda _: deepcopy(buses)
    calls = []
    page.apply_scenario = lambda bus, station, **values: calls.append((bus, station, "场景", values))
    page.apply_control = lambda *args: calls.append(args)
    page.observe_device = lambda bus, station, _: {"link_path": next(item["link"] for item in buses if item["name"] == bus)}
    checked = []
    def check_paths(paths):
        assert calls == []
        checked.append(paths)
    config, actual = page.prepare_fleet(environment(), "本次", check_paths=check_paths)
    assert config.serial_devices == tuple(bus["link"] for bus in buses)
    assert [bus["name"] for bus in actual] == [bus["name"] for bus in buses]
    assert checked == [tuple(bus["link"] for bus in buses)]
    assert len(calls) == 21 * 5
    for bus in actual:
        for device in bus["devices"]:
            assert (bus["name"], device["station_id"], "ratio", 0) in calls
            assert (bus["name"], device["station_id"], "percent", 100) in calls
            assert (bus["name"], device["station_id"], "active_gradient", 100) in calls
            assert (bus["name"], device["station_id"], "reactive_gradient", 100) in calls
    # 后续编排仍调用create_fleet，但不得重新创建或写入同名资源。
    page._step = lambda *_: pytest.fail("已准备机群不应重新进入创建步骤")
    page.create_fleet(actual)


def test_fleet_path_conflict_prevents_runtime_mutation(tmp_path):
    page = HuaweiSlaveSimPage(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    addresses = {point.address for point in READ_POINTS} | {30075}
    buses = [{"name": f"已有{i}", "link": f"/srv/existing/tty{i}", "slaves": [
        {"station_id": station, "model": "huawei_v3", "registers": huawei_registers(addresses)}
        for station in range(1, i + 1)]} for i in range(1, 7)]
    page._read_configuration = lambda _: buses
    page.apply_scenario = lambda *_args, **_kwargs: pytest.fail("路径占用时不得应用工况")
    page.apply_control = lambda *_: pytest.fail("路径占用时不得修改控制")
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


def test_last_device_capacity_failure_prevents_all_scenario_changes(tmp_path):
    actual, _ = capacity_fleet()
    for bus in actual:
        for slave in bus["slaves"]:
            slave["registers"] = huawei_registers({point.address for point in READ_POINTS})
    next(reg for reg in actual[-1]["slaves"][-1]["registers"] if reg["address"] == 30075)["value"] = 1000
    page = HuaweiSlaveSimPage(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    page._read_configuration = lambda _: actual
    page.apply_scenario = lambda *_args, **_kwargs: pytest.fail("全机群容量校验失败前不得应用工况")
    page.apply_control = lambda *_: pytest.fail("全机群容量校验失败前不得应用控制")
    with pytest.raises(AssertionError, match="固定发电目标"):
        page.prepare_fleet(environment(), "本次")
