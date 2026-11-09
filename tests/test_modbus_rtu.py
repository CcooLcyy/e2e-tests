"""从 DSP 上位机和 slaveSim 观察预置 RTU 链路的采集结果。"""
from dataclasses import asdict
from datetime import datetime
import json
import logging
import os
from pathlib import Path

import pytest

from testbench.login_page import PlatformLoginPage, load_login_account
from testbench.modbus_rtu_config import assert_dual_observation, expected_value
from testbench.modbus_environment import BASIC_CASE_IDS, validate_selected_modbus_cases
from testbench.pytest_plugin import fail_e2e_prerequisite
from testbench.rtu_observation import BasicObservationProfile, PreparedRtuPage


pytestmark = pytest.mark.case_group("Modbus RTU")
LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


class RtuScenario:
    def __init__(self, desktop, simulator, upper, environment):
        self.desktop, self.simulator, self.upper, self.environment = desktop, simulator, upper, environment
        self.connections = {}
        self.pairs = []

    def point(self, index):
        return self.environment.stations[index]

    def observe_simulator(self, index):
        point = self.point(index)
        observed = self.simulator.observe(self.environment.bus, point.station_id, "0x03", point.address,
                                          verify_manual=False)
        self.desktop.check("两端使用同一专用串口路径", observed["link_path"], self.environment.serial_device)
        return observed

    def use_prepared_connection(self, index=0, *, scaled=False):
        config, point = self.environment, self.point(index)
        if scaled:
            name, tag, scale, offset = config.scaled_connection, config.scaled_tag, "0.1", 2
        else:
            name, tag, scale, offset = point.connection, point.tag, 1, 0
        self.upper.expect_connection(name, config.serial_device, point.station_id)
        self.connections[index] = {"name": name, "tag": tag, "scale": scale, "offset": offset}

    def verify(self, index, raw, *, previous_timestamp=None):
        connection = self.connections[index]
        self.upper.select(connection["name"])
        expected = expected_value(raw, connection["scale"], connection["offset"])
        with self.desktop.step(f"双端核对站号 {self.point(index).station_id} 采集结果",
                               expected=f"slaveSim={raw}；上位机={expected}，质量正常且采集时间有效"):
            try:
                upper = self.upper.wait_value(connection["tag"], expected, "0.000001",
                                              self.environment.acquisition_timeout,
                                              previous_timestamp=previous_timestamp)
                simulator = self.observe_simulator(index)
            except BaseException as error:
                # 失败不重发设值或启动操作；保留最后观察，补读另一端仅用于诊断。
                upper = self.desktop.metadata.get("RTU最后观察", {}).get(
                    f"{connection['name']}/{connection['tag']}", {"观察错误": "本轮未取得上位机点值"})
                try:
                    simulator = self.observe_simulator(index)
                except Exception as observation_error:
                    simulator = {"观察错误": str(observation_error)}
                try:
                    self._record_pair(index, raw, upper, simulator, error=str(error))
                except Exception as evidence_error:
                    self.desktop.metadata.setdefault("RTU取证错误", []).append(str(evidence_error))
                    LOGGER.exception("双端失败对照保存异常，保留原业务错误")
                raise
            try:
                assert_dual_observation(simulator, upper, raw, scale=connection["scale"], offset=connection["offset"],
                                        previous_timestamp=previous_timestamp)
                self.desktop.check("上位机显示预置寄存器地址", upper["address"], str(self.point(index).address))
                self.desktop.check("上位机显示读取保持寄存器", "0x03" in upper["function"], True)
            except AssertionError as error:
                try:
                    self._record_pair(index, raw, upper, simulator, error=str(error))
                except Exception as evidence_error:
                    self.desktop.metadata.setdefault("RTU取证错误", []).append(str(evidence_error))
                    LOGGER.exception("双端失败对照保存异常，保留原业务错误")
                raise
            self._record_pair(index, raw, upper, simulator)
            self.desktop.capture(f"rtu-pair-{len(self.pairs):02d}", description="DSP上位机 RTU 采集结果",
                                 expected=str(expected), actual=json.dumps(upper, ensure_ascii=False, default=str))
        return upper

    def _record_pair(self, index, raw, upper, simulator, *, error=None):
        """成功与超时都保存固定期望及两端观察，缺失观察必须明确标注。"""
        connection = self.connections[index]
        pair = {"观察时间": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "总线": self.environment.bus, "串口": self.environment.serial_device,
                "站号": self.point(index).station_id, "功能码": "0x03", "零基地址": self.point(index).address,
                "Tag": connection["tag"], "倍率": str(connection["scale"]), "偏移": str(connection["offset"]),
                "原始期望": str(raw), "工程量期望": str(expected_value(raw, connection["scale"], connection["offset"])),
                "slaveSim": simulator, "DSP上位机": upper}
        if error:
            pair["观察错误"] = error
        self.pairs.append(pair)
        self.desktop.metadata["RTU双端校验"] = self.pairs
        (self.desktop.artifacts / "modbus-rtu-observations.json").write_text(
            json.dumps(self.pairs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

@pytest.fixture
def rtu(request):
    """按预置链路观察两端，失败保留现场。"""
    from testbench.slavesim_page import SlaveSimPage
    from testbench.model import validate_slavesim_url

    task_path = os.environ.get("TESTBENCH_TASK")
    if not task_path:
        raise RuntimeError("RTU 双端用例需要从测试上位机运行，以读取任务环境和 slaveSim 地址")
    task = json.loads(Path(task_path).read_text(encoding="utf-8"))
    environment = task["environment"]
    validate_selected_modbus_cases(environment, BASIC_CASE_IDS)
    simulator_url = validate_slavesim_url(environment.get("slavesim_url", ""), required=True)
    config = BasicObservationProfile.load(ROOT / "local" / "modbus-rtu.json",
                                          float(environment.get("modbus_acquisition_timeout", 300)))
    account = load_login_account(ROOT / "local" / "login-account.json")
    desktop = request.getfixturevalue("desktop")
    desktop.metadata["RTU验收阶段"] = "前置条件"
    upper = PreparedRtuPage(desktop)

    def capture_failure(simulator):
        for source, capture in (("slaveSim", simulator.capture), ("DSP上位机", desktop.capture)):
            try:
                capture("rtu-failure-before-cleanup", description=f"{source} 清理前失败现场")
            except Exception as error:
                LOGGER.exception("%s 失败现场采集异常，保留原业务错误", source)
                desktop.metadata.setdefault("RTU取证错误", []).append(f"{source}：{error}")

    with SlaveSimPage(desktop, simulator_url) as simulator:
        setup_failed = False
        try:
            PlatformLoginPage(desktop).login(account, remember=False, auto=False)
            upper.open_prepared_links()
            desktop.metadata["RTU环境"] = asdict(config)
            desktop.metadata["RTU验收阶段"] = "业务验收"
            yield RtuScenario(desktop, simulator, upper, config)
        except BaseException:
            setup_failed = True
            capture_failure(simulator)
            raise
        finally:
            if not setup_failed and any(report.failed for report in getattr(request.node, "_bench_reports", [])):
                capture_failure(simulator)


def test_rtu_configuration_saved_on_both_pages(request):
    """RTU连接与点表保存后重新读取一致，模拟器点位身份正确。"""
    fail_e2e_prerequisite(
        request,
        required="通过上位机业务界面完成保存与重读，并观察对应模拟器点位",
        missing="在线保存 RTU 连接和点表会修改下位机配置。当前没有符合端到端边界的保存入口。",
        checks=[("在线配置保存和重读", "连接与点表保存后重新读取一致，模拟器点位身份正确")],
    )


def test_rtu_initial_acquisition_matches_simulator(rtu):
    """RTU初始采集值与slaveSim已生效原始值一致。"""
    rtu.use_prepared_connection()
    rtu.verify(0, 1234)


def test_rtu_source_change_updates_value_and_time(rtu):
    """slaveSim数值变化后RTU实时值与采集时间同步更新。"""
    rtu.use_prepared_connection()
    first = rtu.verify(0, 1234)
    rtu.verify(0, 2345, previous_timestamp=first["timestamp"])


def test_rtu_scale_and_offset_match_fixed_expectations(rtu):
    """RTU倍率0.1偏移2将两组原始值换算为125.4和236.5。"""
    rtu.use_prepared_connection(scaled=True)
    first = rtu.verify(0, 1234)
    rtu.verify(0, 2345, previous_timestamp=first["timestamp"])


def test_rtu_two_stations_do_not_cross_values(rtu):
    """同一RTU总线两站数值独立，改变第一站不改变第二站。"""
    rtu.use_prepared_connection(0)
    rtu.use_prepared_connection(1)
    first = rtu.verify(0, 1234)
    second = rtu.verify(1, 4321)
    rtu.verify(0, 2345, previous_timestamp=first["timestamp"])
    rtu.verify(1, 4321, previous_timestamp=second["timestamp"])
