"""真实 DSP 上位机与 slaveSim 管理页的 RTU 双端采集闭环。"""
from dataclasses import asdict
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys
import time
import uuid

import pytest

from testbench.login_page import PlatformLoginPage, load_login_account
from testbench.modbus_rtu_config import assert_dual_observation, decimal_value, expected_value
from testbench.modbus_environment import basic_config_from_environment


pytestmark = pytest.mark.case_group("Modbus RTU")
LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


class RtuScenario:
    def __init__(self, desktop, simulator, upper, environment):
        self.desktop, self.simulator, self.upper, self.environment = desktop, simulator, upper, environment
        self.connections = {}
        self.pairs = []
        self.prefix = "e2e-rtu-" + uuid.uuid4().hex[:12]

    def point(self, index):
        return self.environment.stations[index]

    def observe_simulator(self, index):
        point = self.point(index)
        observed = self.simulator.observe(self.environment.bus, point.station_id, "0x03", point.address)
        self.desktop.check("两端使用同一专用串口路径", observed["link_path"], self.environment.serial_device)
        return observed

    def set_source(self, index, value):
        point = self.point(index)
        self.observe_simulator(index)
        self.simulator.set_value(self.environment.bus, point.station_id, "0x03", point.address, value)
        observed = self.observe_simulator(index)
        self.desktop.check(f"slaveSim 站号 {point.station_id} 原始值已生效",
                           str(decimal_value(observed["value"])), str(decimal_value(value)))

    def create(self, index=0, *, scale=1, offset=0):
        config, point = self.environment, self.point(index)
        name = f"{self.prefix}-s{point.station_id}"
        tag = f"{name}-value"
        self.upper.create_connection(name, config.serial_device, config.baud_rate, config.data_bits,
                                     config.stop_bits, {"无校验": "N", "奇校验": "O", "偶校验": "E"}[config.parity],
                                     point.station_id, config.poll_interval_ms, address_base=0)
        self.upper.add_point(tag, function=3, address=point.address, data_type="UINT16", scale=scale, offset=offset)
        self.upper.assert_saved_configuration()
        self.connections[index] = {"name": name, "tag": tag, "scale": scale, "offset": offset}
        self.desktop.metadata.setdefault("RTU专用连接", []).append(dict(self.connections[index]))
        return self.connections[index]

    def start(self, index=0):
        self.upper.select(self.connections[index]["name"])
        self.upper.start()

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
            self._record_pair(index, raw, upper, simulator)
            assert_dual_observation(simulator, upper, raw, scale=connection["scale"], offset=connection["offset"],
                                    previous_timestamp=previous_timestamp)
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

    def cross_timestamp_tick(self):
        # 页面时间仅显示到秒，等待跨越显示粒度，不重发业务操作。
        with self.desktop.step("等待跨越页面采集时间显示粒度"):
            deadline = time.monotonic() + 1.1
            while time.monotonic() < deadline:
                self.desktop._check_stop()
                time.sleep(0.1)


@pytest.fixture
def rtu(request):
    """先校验专用环境，再创建两端会话；失败证据先于资源清理。"""
    from testbench.modbus_rtu_page import ModbusRtuPage
    from testbench.slavesim_page import SlaveSimPage
    from testbench.model import validate_slavesim_url

    task_path = os.environ.get("TESTBENCH_TASK")
    if not task_path:
        raise RuntimeError("RTU 双端用例需要从测试上位机运行，以读取任务环境和 slaveSim 地址")
    task = json.loads(Path(task_path).read_text(encoding="utf-8"))
    environment = task["environment"]
    config = basic_config_from_environment(environment)
    if environment.get("mode") != "fresh":
        raise RuntimeError("RTU 双端用例要求全新独立副本，每项独立准备环境")
    simulator_url = validate_slavesim_url(environment.get("slavesim_url", ""), required=True)
    account = load_login_account(ROOT / "local" / "login-account.json")
    desktop = request.getfixturevalue("desktop")
    desktop.metadata["RTU环境"] = asdict(config)
    desktop.metadata["RTU验收阶段"] = "前置条件"
    upper = ModbusRtuPage(desktop)
    errors = []

    def capture_failure(simulator):
        for source, capture in (("slaveSim", simulator.capture), ("DSP上位机", desktop.capture)):
            try:
                capture("rtu-failure-before-cleanup", description=f"{source} 清理前失败现场")
            except Exception as error:
                LOGGER.exception("%s 失败现场采集异常，保留原业务错误", source)
                desktop.metadata.setdefault("RTU取证错误", []).append(f"{source}：{error}")

    try:
        with SlaveSimPage(desktop, simulator_url) as simulator:
            setup_failed = False
            try:
                PlatformLoginPage(desktop).login(account, remember=False, auto=False)
                upper.open_device(config.manager_address)
                scenario = RtuScenario(desktop, simulator, upper, config)
                # 在任何模拟点修改前确认两站身份、路径和可操作性。
                for index in range(2):
                    scenario.observe_simulator(index)
                desktop.metadata["RTU验收阶段"] = "业务验收"
                yield scenario
            except BaseException:
                # setup 异常发生时 pytest 尚未生成报告，必须就地取证再清理。
                setup_failed = True
                capture_failure(simulator)
                raise
            finally:
                failed = any(report.failed for report in getattr(request.node, "_bench_reports", []))
                if failed and not setup_failed:
                    capture_failure(simulator)
                try:
                    upper.cleanup()
                except Exception as error:
                    LOGGER.exception("本例 RTU 连接清理失败")
                    errors.append(f"上位机连接清理失败：{error}")
    finally:
        if errors:
            desktop.metadata["RTU清理错误"] = errors
            desktop.cleanup_failure = "；".join(errors)
            desktop.failure = desktop.failure or desktop.cleanup_failure
            if sys.exc_info()[0] is None:
                pytest.fail(desktop.cleanup_failure)


def test_rtu_configuration_saved_on_both_pages(rtu):
    """RTU连接与点表保存后重新读取一致，模拟器点位身份正确。"""
    rtu.observe_simulator(0)
    rtu.create()


def test_rtu_initial_acquisition_matches_simulator(rtu):
    """RTU初始采集值与slaveSim已生效原始值一致。"""
    rtu.set_source(0, 1234)
    rtu.create()
    rtu.start()
    rtu.verify(0, 1234)


def test_rtu_source_change_updates_value_and_time(rtu):
    """slaveSim数值变化后RTU实时值与采集时间同步更新。"""
    rtu.set_source(0, 1234)
    rtu.create()
    rtu.start()
    first = rtu.verify(0, 1234)
    rtu.cross_timestamp_tick()
    rtu.set_source(0, 2345)
    rtu.verify(0, 2345, previous_timestamp=first["timestamp"])


def test_rtu_scale_and_offset_match_fixed_expectations(rtu):
    """RTU倍率0.1偏移2将两组原始值换算为125.4和236.5。"""
    rtu.set_source(0, 1234)
    rtu.create(scale="0.1", offset=2)
    rtu.start()
    first = rtu.verify(0, 1234)
    rtu.cross_timestamp_tick()
    rtu.set_source(0, 2345)
    rtu.verify(0, 2345, previous_timestamp=first["timestamp"])


def test_rtu_two_stations_do_not_cross_values(rtu):
    """同一RTU总线两站数值独立，改变第一站不改变第二站。"""
    rtu.set_source(0, 1234)
    rtu.set_source(1, 4321)
    rtu.create(0)
    rtu.create(1)
    rtu.start(0)
    rtu.start(1)
    first = rtu.verify(0, 1234)
    second = rtu.verify(1, 4321)
    rtu.cross_timestamp_tick()
    rtu.set_source(0, 2345)
    rtu.verify(0, 2345, previous_timestamp=first["timestamp"])
    rtu.verify(1, 4321, previous_timestamp=second["timestamp"])
