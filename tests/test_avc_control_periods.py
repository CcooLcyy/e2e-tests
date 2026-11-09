"""通过真实 AVC 和 DataCenter 服务验收计算周期与成员命令间隔。"""
from collections import defaultdict, deque
from contextlib import contextmanager
from decimal import Decimal
import json
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
from threading import Thread
from time import monotonic
from uuid import uuid4

import pytest

from testbench.reporting import write_case_report


pytestmark = [pytest.mark.case_group("AVC 周期控制"), pytest.mark.opt_in]
Q_SET = "E2E_Q_SET"
Q_MEAS = "E2E_Q_MEAS"
Q_CMD = "E2E_Q_CMD"
V_MEAS = "E2E_V_MEAS"
V_CMD = "E2E_V_CMD"
MARKER = "E2E_MARKER"


class PeriodScenario:
    def __init__(self, evidence):
        self.grpcurl = shutil.which("grpcurl")
        self.protos = Path(os.environ.get("TESTBENCH_DSP_PROTO_DIR", ""))
        self.addresses = {
            "AVC": os.environ.get("TESTBENCH_AVC_ADDRESS"),
            "DataCenter": os.environ.get("TESTBENCH_DATACENTER_ADDRESS"),
        }
        if os.environ.get("TESTBENCH_AVC_PERIODS_ISOLATED") != "1":
            pytest.fail("前置条件未满足：仅允许在专用隔离下位机上运行 AVC 周期用例")
        if not self.grpcurl or not all(self.addresses.values()) or not all(
            (self.protos / f"{service}.proto").is_file() for service in self.addresses
        ):
            pytest.fail("前置条件未满足：需要 grpcurl、AVC 与 DataCenter 地址及配套 proto 目录")
        self.evidence = evidence
        self.name = f"e2e-avc-period-{uuid4().hex[:12]}"
        self.bus_key = {"moduleName": "e2e-testbench", "connName": self.name}
        self.group_created = False
        self.bus_created = False
        self.incoming = Queue()
        self.pending = defaultdict(deque)
        self.evidence["专用测试组"] = self.name

    def command(self, service, method, payload, timeout):
        return [
            self.grpcurl, "-plaintext", "-max-time", str(timeout),
            "-import-path", str(self.protos.resolve()), "-proto", f"{service}.proto",
            "-d", json.dumps(payload), self.addresses[service], f"{service}Proto.{service}Service/{method}",
        ]

    def rpc(self, service, method, payload):
        result = subprocess.run(self.command(service, method, payload, 3), capture_output=True, timeout=5)
        if result.returncode:
            detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
            raise AssertionError(f"{service}.{method} 失败（退出码 {result.returncode}）：{detail}")
        return json.loads(result.stdout or b"{}")

    def dc(self, method, payload):
        return self.rpc("DataCenter", method, payload)

    def lifecycle(self, method):
        return self.rpc("AVC", method, {"groupName": self.name})

    def save(self, calc, interval, *, voltage=False):
        inputs = [Q_MEAS, V_MEAS, V_CMD] if voltage else [Q_MEAS, Q_CMD]
        command = {"voltageCmd": {"tag": V_CMD, "unit": "V"},
                   "voltageMeas": {"tag": V_MEAS, "unit": "V"},
                   "voltageControl": {"kp": 2, "deadband": 1}} if voltage else {
            "qTotalCmd": {"signal": {"tag": Q_CMD, "unit": "kVar"}, "mode": "VALUE_MODE_ABSOLUTE"},
        }
        config = {
            "groupName": self.name, "controlMode": "CONTROL_MODE_DIRECT_CYCLIC",
            "calculationExecutionPeriodSeconds": calc, "commandControlPeriodSeconds": interval,
            "members": [{"memberName": "逆变器", "controllable": True, "weight": 1,
                         "qMinKvar": -100, "qMaxKvar": 100, "qMeas": {"tag": Q_MEAS, "unit": "kVar"},
                         "qSet": {"signal": {"tag": Q_SET, "unit": "kVar"}, "mode": "VALUE_MODE_ABSOLUTE"}}],
            **command,
        }
        self.evidence.setdefault("控制组配置", []).append(config)
        if self.group_created:
            self.rpc("AVC", "UpsertGroup", {"config": config})
            return

        group = self.rpc("AVC", "UpsertGroup", {"config": config, "createOnly": True})
        self.group_created = True
        self.group_id = group["connId"]
        # 创建配置可能自动启动控制组，显式停组后准备首轮输入快照。
        self.lifecycle("StopGroup")
        calculation_kind = "DEFAULT_POINT_KIND_VOLTAGE_ERROR" if voltage else "DEFAULT_POINT_KIND_TOTAL_Q_TARGET"
        self.calc_tag = next(point["tag"] for point in group["defaultPoints"] if point["kind"] == calculation_kind)
        conn_id = self.dc("GetOrCreateConnection", {"key": self.bus_key})["connId"]
        self.bus_created = True
        self.bus_id = conn_id
        self.dc("UpsertConnTags", {"connId": conn_id, "tags": [*inputs, self.calc_tag, Q_SET, MARKER]})

        def route(source, target, tag):
            return {"src": {"connId": source, "tag": tag}, "dst": {"connId": target, "tag": tag}}

        self.dc("UpsertRoutes", {"routes": [
            *(route(conn_id, self.group_id, tag) for tag in inputs),
            *(route(self.group_id, conn_id, tag) for tag in (self.calc_tag, Q_SET)),
            route(conn_id, conn_id, MARKER),
        ]})

    def assert_routed_target(self, target):
        response = self.dc("GetLatest", {"connId": self.group_id, "tags": [Q_CMD]})
        updates = response.get("updates", [])
        assert len(updates) == 1 and value(updates[0]) == target, (
            f"前置条件未满足：停组时未在 AVC 目标点缓存中看到 {target} kVar，实际为 {updates}"
        )

    def publish(self, *values):
        self.dc("BatchPublish", {"points": [
            {"connId": self.bus_id, "tag": tag, "value": {"doubleValue": value}, "quality": "QUALITY_GOOD"}
            for tag, value in values
        ]})
        self.evidence.setdefault("输入批次", []).append(values)

    @contextmanager
    def observe(self):
        self.publish((MARKER, 1))
        args = self.command("DataCenter", "Subscribe", {
            "connId": self.bus_id, "tags": [Q_SET, self.calc_tag, MARKER], "snapshot": True,
        }, 60)
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace") as process:
            reader = Thread(target=self.read_updates, args=(process.stdout,), daemon=True)
            reader.start()
            try:
                self.next_update(MARKER, timeout=5)
                yield
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
                reader.join(timeout=5)

    def read_updates(self, stream):
        decoder = json.JSONDecoder()
        buffer = ""
        for line in stream:
            buffer = (buffer + line).lstrip()
            try:
                update, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                continue
            self.incoming.put(update)
            buffer = buffer[end:]
        self.incoming.put(RuntimeError(f"DataCenter 订阅结束：{buffer.strip()}"))

    def next_update(self, tag, *, timeout):
        if self.pending[tag]:
            return self.pending[tag].popleft()
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            try:
                update = self.incoming.get(timeout=max(0, deadline - monotonic()))
            except Empty:
                break
            if isinstance(update, Exception):
                raise update
            self.evidence["点更新"].append(update)
            if update["dstTag"] == tag:
                return update
            self.pending[update["dstTag"]].append(update)
        raise AssertionError(f"{timeout:g} 秒内未收到 {tag} 更新")

    def close(self):
        try:
            if self.group_created:
                self.lifecycle("DeleteGroup")
        finally:
            if self.bus_created:
                self.dc("DeleteConnection", {"key": self.bus_key})


@pytest.fixture
def period_scenario(request):
    evidence = {"点更新": [], "步骤": []}
    scenario = PeriodScenario(evidence)
    try:
        yield scenario
    finally:
        try:
            scenario.close()
        finally:
            directory = getattr(request.node, "_bench_artifacts", "")
            if directory:
                write_case_report(directory, evidence)


@contextmanager
def check(scenario, name, expected):
    step = {"序号": len(scenario.evidence["步骤"]) + 1, "步骤": name, "预期": expected}
    scenario.evidence["步骤"].append(step)
    try:
        yield step
    except Exception as error:
        step.update({"状态": "失败", "实际": str(error)})
        raise
    else:
        step["状态"] = "通过"


def timestamp(update):
    return int(update["tsMs"])


def value(update):
    point = update["value"]
    return Decimal(str(point.get("decimalValue", point.get("doubleValue", point.get("intValue")))))


def test_avc_voltage_deviation_recalculates_without_new_input(period_scenario):
    """AVC 定压持续偏差且没有新输入时按配置周期重新计算。"""
    scenario = period_scenario
    with check(scenario, "保存定压周期控制组", "定压支持计算 1 秒、命令最小间隔 4 秒") as step:
        scenario.save(1, 4, voltage=True)
        step["实际"] = "下位机接受定压周期配置"

    with scenario.observe():
        scenario.publish((Q_MEAS, 0), (V_MEAS, 220), (V_CMD, 230))
        scenario.lifecycle("StartGroup")
        with check(scenario, "定压首轮发布成员设定", "10 V 偏差、比例系数 2，发布 20 kVar") as step:
            setpoint = scenario.next_update(Q_SET, timeout=4)
            assert value(setpoint) == 20, setpoint
            step["实际"] = f"发布 {value(setpoint)} kVar"
        with check(scenario, "无新输入时持续计算定压偏差", "连续三轮偏差均为 10 V，计算至少间隔 1000 毫秒") as step:
            first = scenario.next_update(scenario.calc_tag, timeout=4)
            second = scenario.next_update(scenario.calc_tag, timeout=3)
            third = scenario.next_update(scenario.calc_tag, timeout=3)
            measured = [value(point) for point in (first, second, third)]
            gaps = [timestamp(second) - timestamp(first), timestamp(third) - timestamp(second)]
            assert measured == [10, 10, 10] and all(gap >= 1000 for gap in gaps), (measured, gaps)
            step["实际"] = f"偏差均为 10 V，相邻计算间隔 {gaps} 毫秒"


def test_avc_burst_respects_command_interval_and_uses_latest_target(period_scenario):
    """总无功高频输入只在限频窗口后发布最新目标。"""
    scenario = period_scenario
    with check(scenario, "准备总无功周期控制组", "专用控制组和观测路由配置成功") as step:
        scenario.save(1, 4)
        step["实际"] = "专用控制组和观测路由已配置"
    with scenario.observe():
        scenario.publish((Q_MEAS, 0), (Q_CMD, 10))
        scenario.lifecycle("StartGroup")
        first = scenario.next_update(Q_SET, timeout=5)
        assert value(first) == 10, first
        with check(scenario, "高频目标按最小间隔合并", "下一条成员设定为 49 kVar，且距上次发布至少 4000 毫秒") as step:
            burst = [(Q_CMD, target) for target in range(20, 50)]
            scenario.publish(*burst, (MARKER, 2))
            marker = scenario.next_update(MARKER, timeout=5)
            burst_delay = timestamp(marker) - timestamp(first)
            assert 0 <= burst_delay < 4000, f"前置条件未满足：输入批次未在限频窗口内完成，耗时 {burst_delay} 毫秒"
            latest = scenario.next_update(Q_SET, timeout=8)
            gap = timestamp(latest) - timestamp(first)
            assert value(latest) == 49 and gap >= 4000, (value(latest), gap)
            step["实际"] = f"输入批次在 {burst_delay} 毫秒内到达，发布 {value(latest)} kVar，间隔 {gap} 毫秒"


def test_avc_stopped_group_applies_new_periods_after_restart(period_scenario):
    """停止、保存并再启动 AVC 控制组后应用新的计算和命令周期。"""
    scenario = period_scenario
    with check(scenario, "准备旧周期配置", "专用控制组和观测路由配置成功") as step:
        scenario.save(1, 30)
        step["实际"] = "专用控制组和观测路由已配置"
    with scenario.observe():
        scenario.publish((Q_MEAS, 0), (Q_CMD, 10))
        scenario.lifecycle("StartGroup")
        old_set = scenario.next_update(Q_SET, timeout=5)
        assert value(old_set) == 10, old_set
        with check(scenario, "停组并应用新周期", "停组时已缓存 20 kVar，保存并启动新周期") as step:
            scenario.lifecycle("StopGroup")
            scenario.publish((Q_CMD, 20), (MARKER, 2))
            marker = scenario.next_update(MARKER, timeout=5)
            scenario.assert_routed_target(20)
            scenario.pending[scenario.calc_tag].clear()
            # 保存配置可能自动启动，目标需在保存前已经写入路由缓存。
            scenario.save(3, 7)
            scenario.lifecycle("StartGroup")
            step["实际"] = "已缓存 20 kVar 并启动新周期"

        with check(scenario, "再启动允许立即发布首轮", "首轮在一个新计算周期内发布 20 kVar") as step:
            first = scenario.next_update(Q_SET, timeout=4)
            delay = timestamp(first) - timestamp(marker)
            assert value(first) == 20 and 0 <= delay < 3000, (value(first), delay)
            step["实际"] = f"发布 {value(first)} kVar，距启动前标记 {delay} 毫秒"

        scenario.publish((Q_CMD, 30))
        with check(scenario, "后续应用新计算周期", "后续计算结果为 30 kVar，相邻计算间隔至少 3000 毫秒") as step:
            calc_one = scenario.next_update(scenario.calc_tag, timeout=4)
            calc_two = scenario.next_update(scenario.calc_tag, timeout=5)
            gap = timestamp(calc_two) - timestamp(calc_one)
            assert value(calc_one) == 20 and value(calc_two) == 30 and 3000 <= gap < 4500, (
                value(calc_one), value(calc_two), gap
            )
            step["实际"] = f"两次计算间隔 {gap} 毫秒"

        with check(scenario, "后续应用新命令周期", "发布 30 kVar，且距首轮至少 7000 毫秒") as step:
            second = scenario.next_update(Q_SET, timeout=10)
            gap = timestamp(second) - timestamp(first)
            assert value(second) == 30 and 7000 <= gap < 15000, (value(second), gap)
            step["实际"] = f"发布 {value(second)} kVar，间隔 {gap} 毫秒"
