"""运行时资源选择契约：只跟随页面实际配置，不读旧手填路径。"""
from copy import deepcopy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from testbench.slavesim_runtime import basic_candidate, fleet_candidates, validate_fleet_capacity, validate_topology
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


def test_basic_only_requires_a_second_station_for_the_dual_station_case():
    buses = [{"name": "单站总线", "link": "/srv/real/tty7", "slaves": [manual(23, 99)]}]
    assert basic_candidate(buses, station_count=1)[1] == [{"station_id": 23, "address": 99}]
    assert basic_candidate(buses, station_count=2) is None


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


def test_fleet_requires_six_exact_station_sets_and_model_registers():
    addresses = {32089, 30075}
    buses = [{"name": f"实际{i}", "link": f"/srv/actual{i}", "slaves": [
        {"station_id": station, "model": "huawei_v3", "registers": huawei_registers(addresses)}
        for station in range(1, i + 1)]} for i in range(1, 7)]
    assert [bus["name"] for bus in fleet_candidates(buses, addresses)] == [f"实际{i}" for i in range(1, 7)]
    buses[-1]["slaves"][0]["model"] = ""
    assert fleet_candidates(buses, addresses) is None


class ReadOnlyDriver:
    def __init__(self, marker=None):
        self.marker = marker

    def find_element(self, *_):
        return SimpleNamespace(click=lambda: None, get_dom_attribute=lambda _: self.marker)


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


def test_basic_does_not_rebuild_unrelated_running_devices(tmp_path):
    page = SlaveSimPage(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    page.driver = ReadOnlyDriver()
    unrelated = {"name": "现有", "link": "/srv/live/tty9", "slaves": [manual(1, 0)]}
    page._read_configuration = lambda _: [unrelated]
    page._add_default_bus = lambda: pytest.fail("不应改变含无关运行资源的配置")
    with pytest.raises(RuntimeError, match="重置既有设备运行状态"):
        page.prepare_basic(environment(), station_count=2)


@pytest.mark.parametrize("marker", [None, "false", "true"])
def test_only_explicit_preservation_capability_allows_unrelated_running_devices(tmp_path, marker):
    page = SlaveSimPage(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    page.driver = ReadOnlyDriver(marker)
    before = [{"name": "既有", "link": "/srv/existing", "slaves": [manual(1, 0)]}]
    if marker == "true":
        assert page._assert_save_safe(before, ("本例",)) is True
    else:
        with pytest.raises(RuntimeError, match="重置既有设备运行状态"):
            page._assert_save_safe(before, ("本例",))


def fleet_page(tmp_path):
    page = HuaweiSlaveSimPage(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    page.driver = ReadOnlyDriver()

    @contextmanager
    def step(*_args, **_kwargs):
        yield {}

    page._step = step
    return page


def test_fleet_does_not_save_over_other_simulator_buses(tmp_path):
    page = fleet_page(tmp_path)
    page._read_configuration = lambda _: [{"name": "既有", "link": "/srv/existing", "slaves": []}]
    page._save_configuration = lambda _: pytest.fail("已有其他资源时不得保存")
    with pytest.raises(RuntimeError, match="重置既有设备运行状态"):
        page.prepare_owned_fleet(30, "本次", check_paths=lambda _: pytest.fail("不应分配串口"))
    assert page.owned_fleet is None


def test_fleet_uses_created_configuration_readback(tmp_path):
    page = fleet_page(tmp_path)
    checked = []

    def create(buses, *, check_paths):
        paths = tuple(f"/srv/actual/tty{index}" for index in range(6))
        check_paths(paths)
        page.owned_fleet = {bus["name"]: {
            "name": bus["name"], "link": path,
            "slaves": [{"station_id": device["station_id"]} for device in bus["devices"]],
        } for bus, path in zip(buses, paths, strict=True)}
        return deepcopy(list(page.owned_fleet.values()))

    page.create_fleet = create
    config, actual = page.prepare_owned_fleet(30, "本次", check_paths=checked.append)
    assert config.serial_devices == checked[0]
    assert [bus["serial_device"] for bus in actual] == list(checked[0])
    assert sum(len(bus["devices"]) for bus in actual) == 21


def test_fleet_cleanup_refuses_a_changed_or_foreign_configuration(tmp_path):
    page = fleet_page(tmp_path)
    page.driver = ReadOnlyDriver("true")
    owned = {"name": "本次", "link": "/srv/case", "slaves": [{"station_id": 1}]}
    page.owned_fleet = {"本次": owned}
    page._read_configuration = lambda _: [{**owned, "link": "/srv/changed"},
                                          {"name": "其他", "link": "/srv/foreign", "slaves": []}]
    page._config_bus = lambda _: pytest.fail("拒绝删除任意总线")
    with pytest.raises(AssertionError, match="不一致"):
        page.cleanup_owned_fleet()
    assert page.owned_fleet == {"本次": owned}
    assert not page._restoring


def test_fleet_cleanup_with_preservation_capability_keeps_foreign_fleet(tmp_path):
    page = fleet_page(tmp_path)
    page.driver = ReadOnlyDriver("true")
    owned = {"name": "本次", "link": "/srv/case", "slaves": [{"station_id": 1}]}
    foreign = {"name": "其他", "link": "/srv/foreign", "slaves": [{"station_id": 1, "model": "huawei_v3"}]}
    page.owned_fleet = {"本次": owned}
    page._read_configuration = lambda _: deepcopy([owned, foreign])
    deleted = []
    page._config_bus = lambda name: SimpleNamespace(find_element=lambda *_: SimpleNamespace(click=lambda: deleted.append(name)))

    def save(_, *, before, owned_names, protect_empty_buses):
        assert before == [owned, foreign]
        assert set(owned_names) == {"本次"}
        assert deleted == ["本次"]
        return [foreign]

    page._save_configuration = save
    page.cleanup_owned_fleet()
    assert page.owned_fleet is None
    assert not page._restoring


@pytest.mark.parametrize("changed_at", ["draft", "readback"])
def test_configuration_save_verifies_foreign_topology_before_and_after(tmp_path, changed_at):
    page = fleet_page(tmp_path)
    saved = []
    fieldset = SimpleNamespace(get_dom_attribute=lambda _: "true")
    save = SimpleNamespace(click=lambda: saved.append(True))
    page.driver = SimpleNamespace(find_element=lambda _, name: fieldset if name == "config-fields" else save)
    page._wait = lambda *_: None
    foreign = {"name": "既有", "link": "/srv/foreign", "slaves": [manual(1, 0)]}
    changed = {**foreign, "slaves": [manual(1, 3)]}
    owned = {"name": "本次", "link": "/srv/case", "slaves": [manual(1, 0)]}
    page._configuration_snapshot = lambda _: [changed if changed_at == "draft" else foreign, owned]
    page._read_configuration = lambda _: [changed, owned]
    with pytest.raises(AssertionError, match="无关 slaveSim 总线配置发生变化：既有"):
        page._save_configuration("新增", before=[foreign], owned_names=("本次",))
    assert bool(saved) == (changed_at == "readback")


def capacity_fleet():
    planned = build_fleet(HuaweiFleetConfig(tuple(f"/srv/tty{i}" for i in range(6))), "容量")
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
