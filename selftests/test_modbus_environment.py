"""统一RTU环境保存和按用例前置校验，不运行浏览器或外部设备。"""
import pytest

from testbench.controller import TaskController
from testbench.modbus_environment import (AGC_REBALANCE_CASE_ID, BASIC_CASE_IDS, HUAWEI_CASE_ID, basic_config_from_environment,
                                         huawei_config_from_environment)
from testbench.model import CaseInfo, Environment, load_environment, save_environment


def populated_environment(tmp_path):
    app, driver = tmp_path / "upper.exe", tmp_path / "driver.exe"
    app.touch()
    driver.touch()
    return Environment(app_path=str(app), driver_path=str(driver),
                       modbus_manager_address="192.0.2.1:50051", modbus_baud_rate=19200,
                       modbus_poll_interval_ms=500, modbus_acquisition_timeout=35,
                       modbus_basic={"device_name": "专用下位机", "bus": "专用总线",
                                     "serial_device": "/dev/rtu-test", "dedicated_test_resources": True,
                                     "data_bits": 8, "stop_bits": 1, "parity": "无校验",
                                     "stations": [{"station_id": 1, "address": 10}, {"station_id": 2, "address": 20}]},
                       modbus_huawei={"serial_devices": [f"/srv/serial-sim/tty{i}" for i in range(6)],
                                      "dedicated_simulator": True})


def test_legacy_environment_and_mutable_settings_are_isolated():
    """旧文件无新字段可加载，默认和同一导入对象不会导致环境间字典串改。"""
    first, second = Environment.from_dict({"name": "旧环境"}), Environment()
    first.modbus_basic["bus"] = "只属于第一个环境"
    assert second.modbus_basic == {}
    assert first.modbus_manager_address == second.modbus_manager_address == "192.168.1.219:17000"
    source = {"modbus_huawei": {"serial_devices": ["/dev/a"]}}
    imported = Environment.from_dict(source)
    imported.modbus_huawei["serial_devices"].append("/dev/b")
    assert source["modbus_huawei"]["serial_devices"] == ["/dev/a"]


def test_uniform_environment_round_trip_and_config_builders(tmp_path):
    """保存导入全部新值，两种用例均使用同一份任务共享参数，不读取独立JSON。"""
    environment = populated_environment(tmp_path)
    path = tmp_path / "environment.json"
    save_environment(path, environment)
    restored = load_environment(path)
    assert restored.to_dict() == environment.to_dict()
    basic = basic_config_from_environment(restored.to_dict(), {
        "bus": "实际总线", "serial_device": "/srv/serial-sim/actual",
        "stations": [{"station_id": 7, "address": 30}, {"station_id": 9, "address": 40}]})
    huawei = huawei_config_from_environment(restored.to_dict(), {
        "serial_devices": [f"/srv/serial-sim/actual{i}" for i in range(6)]})
    assert basic.manager_address == huawei.manager_address == "192.0.2.1:50051"
    assert basic.baud_rate == huawei.baud_rate == 9600
    assert basic.poll_interval_ms == huawei.poll_interval_ms == 500
    assert basic.acquisition_timeout == huawei.acquisition_timeout == 35
    assert basic.stations[1].address == 40
    assert basic.bus == "实际总线"
    assert huawei.serial_devices[-1] == "/srv/serial-sim/actual5"
    restored.validate_for_cases([*BASIC_CASE_IDS, HUAWEI_CASE_ID])


@pytest.mark.parametrize("timeout", [None, 300, 35])
def test_shared_acquisition_wait_defaults_to_five_minutes_and_preserves_custom_values(tmp_path, timeout):
    """运行时两类RTU参数默认五分钟，支持上限300秒且不覆盖显式短等待。"""
    data = populated_environment(tmp_path).to_dict()
    if timeout is None:
        data.pop("modbus_acquisition_timeout")
    else:
        data["modbus_acquisition_timeout"] = timeout
    basic = basic_config_from_environment(data, {"bus": "现场", "serial_device": "/srv/actual",
        "stations": [{"station_id": 1, "address": 0}, {"station_id": 2, "address": 0}]})
    huawei = huawei_config_from_environment(data, {"serial_devices": [f"/srv/actual{i}" for i in range(6)]})
    assert basic.acquisition_timeout == huawei.acquisition_timeout == (300 if timeout is None else timeout)


def test_shared_acquisition_wait_above_five_minutes_is_rejected(tmp_path):
    """超过支持上限的采集时限必须在开始业务用例前明确失败。"""
    environment = populated_environment(tmp_path)
    environment.modbus_acquisition_timeout = 301
    with pytest.raises(ValueError, match="采集"):
        environment.validate_for_cases([HUAWEI_CASE_ID])


def test_task_dict_is_a_snapshot_not_a_reference(tmp_path):
    """任务快照包含当时配置，后续编辑或导入不会改变已生成任务参数。"""
    environment = populated_environment(tmp_path)
    task = environment.to_dict()
    environment.modbus_huawei["serial_devices"][0] = "/dev/changed"
    assert task["modbus_huawei"]["serial_devices"][0] == "/srv/serial-sim/tty0"


def test_incomplete_rtu_draft_can_be_saved_and_does_not_block_login(tmp_path):
    """只运行登录用例不要求RTU配置完整，也不因草稿中未填写数值而被拦截。"""
    environment = populated_environment(tmp_path)
    environment.modbus_manager_address = ""
    environment.modbus_poll_interval_ms = None
    environment.modbus_basic = {"stations": [{"station_id": ""}]}
    environment.modbus_huawei = {"serial_devices": [""]}
    path = tmp_path / "draft.json"
    save_environment(path, environment)
    loaded = load_environment(path)
    loaded.validate_for_cases(["tests/test_login_success.py::test_valid_account_enters_workspace"])
    assert loaded.modbus_basic == environment.modbus_basic


def test_rtu_preflight_does_not_require_static_simulator_resources(tmp_path):
    """两类用例均在执行阶段读取模拟器，不要求静态参数和专用资源勾选。"""
    environment = populated_environment(tmp_path)
    environment.modbus_basic = {}
    environment.modbus_huawei = {}
    environment.validate_for_cases([*BASIC_CASE_IDS, HUAWEI_CASE_ID])
    environment.modbus_basic = {"dedicated_test_resources": False, "stations": "过期值"}
    environment.modbus_huawei = {"dedicated_simulator": False, "serial_devices": ["过期路径"]}
    environment.validate_for_cases([*BASIC_CASE_IDS, HUAWEI_CASE_ID])


def test_rebalance_requires_dedicated_environment_and_an_exclusive_task(tmp_path):
    environment = populated_environment(tmp_path)
    with pytest.raises(ValueError, match="单独运行"):
        environment.validate_for_cases([AGC_REBALANCE_CASE_ID, HUAWEI_CASE_ID])
    with pytest.raises(ValueError, match="专用"):
        environment.validate_for_cases([AGC_REBALANCE_CASE_ID])
    environment.agc_rebalance_dedicated = True
    with pytest.raises(ValueError, match="启用 104"):
        environment.validate_for_cases([AGC_REBALANCE_CASE_ID])
    master = tmp_path / "Msk104Master.exe"
    driver = tmp_path / "msedgedriver.exe"
    master.touch()
    driver.touch()
    environment.master104_enabled = True
    environment.master104_path = str(master)
    environment.driver_path = str(driver)
    environment.validate_for_cases([AGC_REBALANCE_CASE_ID])
    environment.agc_rebalance_iec104_port = 0
    with pytest.raises(ValueError, match="监听端口"):
        environment.validate_for_cases([AGC_REBALANCE_CASE_ID])


@pytest.mark.parametrize("change,label", [
    ({"modbus_manager_address": ""}, "ModuleManager"),
    ({"mode": "workspace"}, "全新独立副本"),
    ({"slavesim_url": ""}, "slaveSim"),
    ({"modbus_poll_interval_ms": None}, "轮询"),
    ({"modbus_acquisition_timeout": 0}, "采集"),
])
def test_preflight_errors_name_the_configuration_entry(tmp_path, change, label):
    """开始前直接指出界面入口，不在worker启动后报独立JSON缺失。"""
    environment = populated_environment(tmp_path)
    for key, value in change.items():
        setattr(environment, key, value)
    with pytest.raises(ValueError, match=label):
        environment.validate_for_cases([next(iter(BASIC_CASE_IDS))])


def test_shared_configuration_cannot_be_overridden_by_nested_data(tmp_path):
    """历史嵌套配置被忽略，运行时数据也不能覆盖共享连接参数。"""
    data = populated_environment(tmp_path).to_dict()
    data["modbus_huawei"]["manager_address"] = "unexpected:50051"
    actual = {"serial_devices": [f"/srv/serial-sim/actual{i}" for i in range(6)],
              "manager_address": "unexpected:50051", "baud_rate": 115200}
    config = huawei_config_from_environment(data, actual)
    assert config.manager_address == data["modbus_manager_address"]
    assert config.baud_rate == 9600


@pytest.mark.parametrize("builder", [basic_config_from_environment, huawei_config_from_environment])
def test_runtime_configuration_must_be_explicit(tmp_path, builder):
    """尚未读取模拟器时禁止退回环境中的过期路径或伪造发现成功。"""
    with pytest.raises(ValueError, match="运行时"):
        builder(populated_environment(tmp_path).to_dict())


def test_controller_rejects_missing_configuration_before_artifacts_or_worker(tmp_path, monkeypatch):
    """业务用例存在但RTU配置缺失时，不创建失败任务目录，也不启动任何工作进程。"""
    environment = populated_environment(tmp_path)
    environment.modbus_manager_address = ""
    case_id = next(iter(BASIC_CASE_IDS))
    monkeypatch.setattr("testbench.controller.discover_cases", lambda _: [CaseInfo(case_id, "RTU用例")])
    monkeypatch.setattr("testbench.controller.subprocess.Popen", lambda *args, **kwargs: pytest.fail("配置缺失后启动了worker"))
    controller = TaskController(tmp_path)
    with pytest.raises(ValueError, match="ModuleManager"):
        controller.start(environment, [case_id])
    assert controller.run_dir is None and controller.active is False
    assert not (tmp_path / "artifacts").exists()
