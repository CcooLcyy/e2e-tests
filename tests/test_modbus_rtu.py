"""从 DSP 上位机和 slaveSim 观察预置 RTU 链路的采集结果。"""
from dataclasses import asdict
from datetime import datetime
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from testbench.case_plan import (
    DESKTOP_FINISH, DESKTOP_START, Step, case_plan, run_steps, track_fixture_context, track_fixture_step,
)
from testbench.login_page import PlatformLoginPage, load_login_account
from testbench.login_plan import login_steps
from testbench.modbus_rtu_config import assert_dual_observation, expected_value
from testbench.modbus_environment import BASIC_CASE_IDS, validate_selected_modbus_cases
from testbench.pytest_plugin import fail_e2e_prerequisite
from testbench.rtu_observation import BasicObservationProfile, PreparedRtuPage
from testbench.workspace import local_config


pytestmark = pytest.mark.case_group("Modbus RTU")
LOGGER = logging.getLogger(__name__)
RAW_FACTORS = (1, 0)
SCALED_FACTORS = ("0.1", 2)
RTU_PREPARE = Step("rtu.prepare", "读取 RTU 专项配置", "已声明预置链路、观察时限和专用测试账号")
SIMULATOR_START = Step("rtu.simulator.start", "打开 slaveSim 观察会话", "运行页面已加载且收到新刷新")
RTU_LOGIN_STEPS = login_steps("login")
RTU_OPEN = Step("rtu.open", "打开上位机预置 RTU 业务页面", "可以观察已经准备好的连接")
SIMULATOR_FINISH = Step("rtu.simulator.finish", "关闭 slaveSim 观察会话", "本例观察会话已收尾")


class RtuScenario:
    def __init__(self, desktop, simulator, upper, environment):
        self.desktop, self.simulator, self.upper, self.environment = desktop, simulator, upper, environment
        self.connections = {}
        self.observations = {}
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
            name, tag = config.scaled_connection, config.scaled_tag
            scale, offset = SCALED_FACTORS
        else:
            name, tag = point.connection, point.tag
            scale, offset = RAW_FACTORS
        self.upper.expect_connection(name, config.serial_device, point.station_id)
        self.connections[index] = {"name": name, "tag": tag, "scale": scale, "offset": offset}

    def select_for_observation(self, index, raw, previous_timestamp):
        connection = self.connections[index]
        self.upper.select(connection["name"])
        return RtuObservation(self, index, raw, connection, previous_timestamp)

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


class RtuObservation:
    """同一轮双端观察及其失败证据，供各验收场景复用。"""

    def __init__(self, scenario, index, raw, connection, previous_timestamp):
        self.scenario, self.index, self.raw = scenario, index, raw
        self.connection, self.previous_timestamp = connection, previous_timestamp
        self.expected = expected_value(raw, connection["scale"], connection["offset"])
        self.desktop = scenario.desktop
        self.upper = self.simulator = None
        self.evidence_attempted = False

    def wait_upper(self):
        try:
            self.upper = self.scenario.upper.wait_value(
                self.connection["tag"], self.expected, "0.000001", self.scenario.environment.acquisition_timeout,
                previous_timestamp=self.previous_timestamp,
            )
        except BaseException as error:
            self._preserve_read_failure(error)
            raise

    def read_simulator(self):
        try:
            self.simulator = self.scenario.observe_simulator(self.index)
        except BaseException as error:
            self._preserve_read_failure(error)
            raise

    def _preserve_read_failure(self, error):
        if isinstance(error, InterruptedError):
            self.preserve_interruption(error)
            return
        # 补读另一端只用于取证，保留失败时上位机最后观察和原始异常。
        self.upper = self._last_upper()
        try:
            self.simulator = self.scenario.observe_simulator(self.index)
        except Exception as observation_error:
            self.simulator = {"观察错误": str(observation_error)}
        self._preserve_failure(error)

    def _last_upper(self):
        connection = self.connection
        return self.desktop.metadata.get("RTU最后观察", {}).get(
            f"{connection['name']}/{connection['tag']}", {"观察错误": "本轮未取得上位机点值"})

    def preserve_interruption(self, error):
        if self.evidence_attempted:
            return
        if self.upper is None:
            self.upper = self._last_upper()
        if self.simulator is None:
            self.simulator = {"观察错误": "任务停止，未完成本轮模拟器观察"}
        self._preserve_failure(error)

    def _preserve_failure(self, error):
        self.evidence_attempted = True
        try:
            self.scenario._record_pair(self.index, self.raw, self.upper, self.simulator, error=str(error))
        except Exception as evidence_error:
            self.desktop.metadata.setdefault("RTU取证错误", []).append(str(evidence_error))
            LOGGER.exception("双端失败对照保存异常，保留原业务错误")

    def check_values(self):
        station = self.scenario.point(self.index).station_id
        with self.desktop.step(f"双端核对站号 {station} 采集结果",
                               expected=f"slaveSim={self.raw}；上位机={self.expected}，质量正常且采集时间有效"):
            try:
                assert_dual_observation(self.simulator, self.upper, self.raw, scale=self.connection["scale"],
                                        offset=self.connection["offset"], previous_timestamp=self.previous_timestamp)
            except AssertionError as error:
                self._preserve_failure(error)
                raise

    def check_address(self):
        self._check_point("上位机显示预置寄存器地址", self.upper["address"],
                          str(self.scenario.point(self.index).address))

    def check_function(self):
        self._check_point("上位机显示读取保持寄存器", "0x03" in self.upper["function"], True)

    def _check_point(self, name, actual, expected):
        try:
            self.desktop.check(name, actual, expected)
        except AssertionError as error:
            self._preserve_failure(error)
            raise

    def save_evidence(self):
        self.evidence_attempted = True
        self.scenario._record_pair(self.index, self.raw, self.upper, self.simulator)
        self.desktop.capture(f"rtu-pair-{len(self.scenario.pairs):02d}", description="DSP上位机 RTU 采集结果",
                             expected=str(self.expected), actual=json.dumps(self.upper, ensure_ascii=False, default=str))


def connection_step(id, index, *, scaled=False):
    connection = "倍率 0.1、偏移 2 的预置点" if scaled else f"第 {index + 1} 站的预置连接"
    return Step(id, f"读取{connection}的观察配置", "已声明对应连接、串口和站号",
                lambda scenario: scenario.use_prepared_connection(index, scaled=scaled))


def observation_steps(prefix, index, raw, *, scaled=False, previous=None):
    expected = expected_value(raw, *(SCALED_FACTORS if scaled else RAW_FACTORS))
    label = f"第 {index + 1} 站{'后续' if previous else '初始'}采集"

    def select(scenario):
        previous_timestamp = scenario.observations[previous].upper["timestamp"] if previous else None
        scenario.observations[prefix] = scenario.select_for_observation(index, raw, previous_timestamp)

    def action(method):
        return lambda scenario: method(scenario.observations[prefix])

    time_expected = "采集时间与前次不同" if previous else "采集时间有效"
    return (
        Step(f"{prefix}.select", f"选择{label}的预置连接", "连接正在运行，串口、站号和连接身份符合预置", select),
        Step(f"{prefix}.upper", f"等待{label}的上位机点值", f"显示 {expected}，质量正常，{time_expected}",
             action(RtuObservation.wait_upper)),
        Step(f"{prefix}.simulator", f"读取{label}的 slaveSim 寄存器", "读到同一专用链路的预置寄存器",
             action(RtuObservation.read_simulator)),
        Step(f"{prefix}.compare", f"核对{label}的双端结果",
             f"slaveSim 原始值为 {raw}，上位机显示 {expected}，质量正常，{time_expected}",
             action(RtuObservation.check_values)),
        Step(f"{prefix}.address", f"核对{label}的寄存器地址", "上位机显示预置寄存器地址",
             action(RtuObservation.check_address)),
        Step(f"{prefix}.function", f"核对{label}的功能码", "上位机显示读取保持寄存器 0x03",
             action(RtuObservation.check_function)),
        Step(f"{prefix}.evidence", f"保存{label}的双端证据", "保存本轮观察及上位机截图",
             action(RtuObservation.save_evidence)),
    )


def rtu_plan(*steps):
    return case_plan(RTU_PREPARE, DESKTOP_START, SIMULATOR_START, *RTU_LOGIN_STEPS, RTU_OPEN,
                     *steps, SIMULATOR_FINISH, DESKTOP_FINISH)


def run_observations(request, steps, scenario):
    try:
        run_steps(request, steps, scenario)
    except InterruptedError as error:
        latest = next(reversed(scenario.observations.values()), None)
        if latest is not None:
            latest.preserve_interruption(error)
        raise


@pytest.fixture
def rtu(request):
    """按预置链路观察两端，失败保留现场。"""
    from testbench.slavesim_page import SlaveSimPage
    from testbench.model import validate_slavesim_url

    with track_fixture_step(request, RTU_PREPARE):
        task_path = os.environ.get("TESTBENCH_TASK")
        if not task_path:
            raise RuntimeError("RTU 双端用例需要从测试上位机运行，以读取任务环境和 slaveSim 地址")
        task = json.loads(Path(task_path).read_text(encoding="utf-8"))
        environment = task["environment"]
        validate_selected_modbus_cases(environment, BASIC_CASE_IDS)
        simulator_url = validate_slavesim_url(environment.get("slavesim_url", ""), required=True)
        config = BasicObservationProfile.load(local_config("modbus-rtu.json"),
                                              float(environment.get("modbus_acquisition_timeout", 300)))
        account = load_login_account(local_config("login-account.json"))
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

    with track_fixture_context(request, SIMULATOR_START, SIMULATOR_FINISH, SlaveSimPage(desktop, simulator_url)) as simulator:
        setup_failed = False
        try:
            login = SimpleNamespace(desktop=desktop, page=PlatformLoginPage(desktop), account=account)
            run_steps(request, RTU_LOGIN_STEPS, login)
            with track_fixture_step(request, RTU_OPEN):
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


@pytest.mark.case_flow(
    ("从上位机业务界面保存 RTU 连接和点表", "连接和点表保存成功"),
    ("重新读取连接和点表", "重读结果与保存内容一致"),
    ("观察对应的 slaveSim 点位", "设备站号和寄存器与点表映射一致"),
    blocked="当前缺少符合端到端测试边界的在线保存入口。以下为待验证流程。",
)
def test_rtu_configuration_saved_on_both_pages(request):
    """RTU连接与点表保存后重新读取一致，模拟器点位身份正确。"""
    fail_e2e_prerequisite(
        request,
        required="通过上位机业务界面完成保存与重读，并观察对应模拟器点位",
        missing="在线保存 RTU 连接和点表会修改下位机配置。当前没有符合端到端边界的保存入口。",
        checks=[("在线配置保存和重读", "连接与点表保存后重新读取一致，模拟器点位身份正确")],
    )


FIRST_CONNECTION = connection_step("first.connection", 0)
FIRST_BASELINE = observation_steps("first.baseline", 0, 1234)
FIRST_CHANGE = observation_steps("first.change", 0, 2345, previous="first.baseline")
INITIAL_ACQUISITION = (FIRST_CONNECTION, *FIRST_BASELINE)
SOURCE_CHANGE = (*INITIAL_ACQUISITION, *FIRST_CHANGE)
SCALED_CHANGE = (
    connection_step("scaled.connection", 0, scaled=True),
    *observation_steps("scaled.baseline", 0, 1234, scaled=True),
    *observation_steps("scaled.change", 0, 2345, scaled=True, previous="scaled.baseline"),
)
TWO_STATIONS = (
    FIRST_CONNECTION,
    connection_step("second.connection", 1),
    *FIRST_BASELINE,
    *observation_steps("second.baseline", 1, 4321),
    *FIRST_CHANGE,
    *observation_steps("second.change", 1, 4321, previous="second.baseline"),
)


@rtu_plan(*INITIAL_ACQUISITION)
def test_rtu_initial_acquisition_matches_simulator(request, rtu):
    """RTU初始采集值与slaveSim已生效原始值一致。"""
    run_observations(request, INITIAL_ACQUISITION, rtu)


@rtu_plan(*SOURCE_CHANGE)
def test_rtu_source_change_updates_value_and_time(request, rtu):
    """slaveSim数值变化后RTU实时值与采集时间同步更新。"""
    run_observations(request, SOURCE_CHANGE, rtu)


@rtu_plan(*SCALED_CHANGE)
def test_rtu_scale_and_offset_match_fixed_expectations(request, rtu):
    """RTU倍率0.1偏移2将两组原始值换算为125.4和236.5。"""
    run_observations(request, SCALED_CHANGE, rtu)


@rtu_plan(*TWO_STATIONS)
def test_rtu_two_stations_do_not_cross_values(request, rtu):
    """同一RTU总线两站数值独立，改变第一站不改变第二站。"""
    run_observations(request, TWO_STATIONS, rtu)
