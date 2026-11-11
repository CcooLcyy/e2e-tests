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


@pytest.fixture
def basic_draft(tmp_path):
    page = simulator_page(tmp_path, SlaveSimPage)
    previous = [{"name": "旧总线", "link": "/srv/live/tty9", "slaves": [manual(1, 0)]}]
    link = "/srv/live/new-tty"
    draft = {"name": "页面默认总线", "link": link, "slaves": []}
    page._prepare_empty_configuration = lambda _: previous
    page._add_default_bus = lambda *, replace_before: (draft["name"], link)
    page._config_bus = lambda _: draft
    page._field = lambda card, label: card
    page._replace = lambda card, name: card.update(name=name)
    page._add_basic_stations = lambda name, count: draft["slaves"].extend([manual(9, 10), manual(28, 17)][:count])
    return page, previous, draft


def test_basic_prepare_uses_new_topology_and_saved_actual_mapping(basic_draft):
    page, previous, draft = basic_draft
    calls = []

    def save(label, *, before, owned_names, removed_names):
        assert before == previous
        assert owned_names == (draft["name"],) and removed_names == ("旧总线",)
        calls.append(("保存", draft["name"]))
        return [draft]

    page._save_configuration = save
    page.observe = lambda *args: calls.append(args) or {"link_path": draft["link"]}
    config = page.prepare_basic(environment(), check_paths=lambda paths: calls.append(paths))
    assert config.serial_device == draft["link"] and config.bus == draft["name"]
    assert [(station.station_id, station.address) for station in config.stations] == [(9, 10), (28, 17)]
    assert config.baud_rate == 9600
    assert calls == [(draft["link"],), ("保存", draft["name"]),
                     (draft["name"], 9, "0x03", 10), (draft["name"], 28, "0x03", 17)]


def test_basic_runtime_path_mismatch_keeps_failure(basic_draft):
    page, _, draft = basic_draft
    page._save_configuration = lambda *args, **kwargs: [draft]
    page.observe = lambda *_: {"link_path": "/srv/another/tty9"}
    with pytest.raises(AssertionError, match="运行路径"):
        page.prepare_basic(environment())


def test_basic_path_conflict_prevents_preparation_save_and_observation(basic_draft):
    page, _, _ = basic_draft
    page.observe = lambda *_: pytest.fail("路径占用时不得设置或读取测试点")
    page._save_configuration = lambda *args, **kwargs: pytest.fail("路径占用时不得保存准备配置")
    def conflict(_):
        raise AssertionError("DSP串口路径已占用")
    with pytest.raises(AssertionError, match="已占用"):
        page.prepare_basic(environment(), check_paths=conflict)


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


def simulator_page(tmp_path, page_type=HuaweiSlaveSimPage):
    page = page_type(SimpleNamespace(metadata={}, artifacts=tmp_path), "http://localhost:16000")
    page.driver = ReadOnlyDriver()

    @contextmanager
    def step(*_args, **_kwargs):
        yield {}

    page._step = step
    return page


def test_fleet_uses_created_configuration_readback(tmp_path):
    page = simulator_page(tmp_path)
    checked = []

    def create(buses, *, check_paths):
        paths = tuple(f"/srv/actual/tty{index}" for index in range(6))
        check_paths(paths)
        return [{
            "name": bus["name"], "link": path,
            "slaves": [{"station_id": device["station_id"]} for device in bus["devices"]],
        } for bus, path in zip(buses, paths, strict=True)]

    page.create_fleet = create
    config, actual = page.prepare_owned_fleet(30, "本次", check_paths=checked.append)
    assert config.serial_devices == checked[0]
    assert [bus["serial_device"] for bus in actual] == list(checked[0])
    assert sum(len(bus["devices"]) for bus in actual) == 21


@pytest.mark.parametrize("changed_at", ["draft", "readback"])
def test_configuration_save_verifies_foreign_topology_before_and_after(tmp_path, changed_at):
    page = simulator_page(tmp_path, SlaveSimPage)
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


@pytest.mark.parametrize("remaining_at", ["draft", "readback", None])
def test_configuration_save_requires_deleted_buses_to_be_absent(tmp_path, remaining_at):
    page = simulator_page(tmp_path, SlaveSimPage)
    saved = []
    fieldset = SimpleNamespace(get_dom_attribute=lambda _: None)
    save = SimpleNamespace(click=lambda: saved.append(True))
    page.driver = SimpleNamespace(find_element=lambda _, name: fieldset if name == "config-fields" else save)
    page._wait = lambda *_: None
    old = {"name": "旧总线", "link": "/srv/old", "slaves": [manual(1, 0)]}
    page._configuration_snapshot = lambda _: [old] if remaining_at == "draft" else []
    page._read_configuration = lambda _: [old] if remaining_at == "readback" else []
    arguments = {"before": [old], "owned_names": (), "removed_names": ("旧总线",)}
    if remaining_at is None:
        assert page._save_configuration("清空", **arguments) == []
    else:
        with pytest.raises(AssertionError, match="旧总线"):
            page._save_configuration("清空", **arguments)
    assert bool(saved) == (remaining_at != "draft")


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
