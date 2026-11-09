"""端到端任务只校验采集观察设置，旧环境中的管理字段不参与运行。"""
import pytest

from testbench.controller import TaskController
from testbench.modbus_environment import (
    AGC_REBALANCE_CASE_ID,
    RTU_CONFIG_CASE_ID,
    RTU_OBSERVATION_CASE_IDS,
)
from testbench.model import CaseInfo, Environment, load_environment, save_environment


def populated_environment(tmp_path):
    app, driver = tmp_path / "upper.exe", tmp_path / "driver.exe"
    app.touch()
    driver.touch()
    return Environment(app_path=str(app), driver_path=str(driver), modbus_acquisition_timeout=35)


def test_old_environment_loads_without_restoring_device_management_fields(tmp_path):
    """旧环境仍可载入，保留观察参数并忽略以前的环境操作参数。"""
    environment = populated_environment(tmp_path)
    previous = {"modbus_manager_address": "192.0.2.1:50051", "modbus_poll_interval_ms": 500,
                "agc_rebalance_iec104_port": 2404, "modbus_basic": {"bus": "旧总线"}}
    restored = Environment.from_dict(environment.to_dict() | previous)
    assert restored.to_dict() == environment.to_dict()
    path = tmp_path / "environment.json"
    save_environment(path, restored)
    assert load_environment(path).to_dict() == environment.to_dict()


def test_observation_allows_existing_upper_workspace(tmp_path):
    """可以复用已准备的上位机工作区观察采集反馈。"""
    environment = populated_environment(tmp_path)
    environment.mode = "workspace"
    environment.validate_for_cases(RTU_OBSERVATION_CASE_IDS)


def test_unrelated_cases_do_not_require_rtu_observation_settings(tmp_path):
    environment = populated_environment(tmp_path)
    environment.slavesim_url = ""
    environment.modbus_acquisition_timeout = None
    environment.validate_for_cases([RTU_CONFIG_CASE_ID, AGC_REBALANCE_CASE_ID,
                                    "tests/test_login_success.py::test_valid_account_enters_workspace"])


@pytest.mark.parametrize("timeout", [None, True, 0, 301, float("inf")])
def test_invalid_observation_wait_reports_which_setting_to_check(tmp_path, timeout):
    environment = populated_environment(tmp_path)
    environment.modbus_acquisition_timeout = timeout
    with pytest.raises(ValueError, match="采集等待时限"):
        environment.validate_for_cases(RTU_OBSERVATION_CASE_IDS)


def test_controller_rejects_missing_observer_before_artifacts_or_worker(tmp_path, monkeypatch):
    environment = populated_environment(tmp_path)
    environment.slavesim_url = ""
    case_id = sorted(RTU_OBSERVATION_CASE_IDS)[0]
    monkeypatch.setattr("testbench.controller.discover_cases", lambda _: [CaseInfo(case_id, "RTU用例")])
    monkeypatch.setattr("testbench.controller.subprocess.Popen", lambda *args, **kwargs: pytest.fail("缺少观察入口后启动了worker"))
    controller = TaskController(tmp_path)
    with pytest.raises(ValueError, match="slaveSim"):
        controller.start(environment, [case_id])
    assert controller.run_dir is None and controller.active is False
    assert not (tmp_path / "artifacts").exists()
