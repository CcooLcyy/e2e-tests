"""六路21台页面编排和逐点证据，控制与采集边界明确记录。"""
from collections import Counter
from decimal import Decimal
import json
import logging
import time

from .huawei_fleet import (CONTROL_POINTS, READ_POINTS, STATUS_VALUES, STATUS_WORD_VALUES, UNIMPLEMENTED_CONTROLS,
                           build_fleet, compare_point, point_expected)


LOGGER = logging.getLogger(__name__)
STAGES = ("发电", "有功调节", "无功调节", "负无功调节", "待机", "限电停运", "恢复发电")


class HuaweiFleetRun:
    def __init__(self, desktop, simulator, upper, config, run_id, *, buses=None):
        self.desktop, self.simulator, self.upper, self.config = desktop, simulator, upper, config
        # 执行时准备的实际总线名称和站号优先，不能重新生成假名称覆盖发现结果。
        self.buses = build_fleet(config, run_id) if buses is None else buses
        self.connections, self.results, self.previous, self.evidence_errors = {}, [], {}, []
        self.addresses = sorted({point.address for point in READ_POINTS} | {30075})
        desktop.metadata["华为六路拓扑"] = self.buses
        desktop.metadata["华为遥控缺口"] = list(UNIMPLEMENTED_CONTROLS)
        desktop.metadata["华为验收范围"] = "模拟器场景/控制变化后的DSP抄读匹配；DSP下发调节及104遥控未验证"
        desktop.metadata["华为保留配置"] = "结束时保留本次模拟器总线及DSP连接；重跑时自动删除全部模拟总线，DSP已有连接占用仍需处理"

    @staticmethod
    def _identity(bus, device):
        return f"{bus['index']}/{device['station_id']}"

    def configure(self):
        self.upper.assert_serial_paths_available(self.config.serial_devices)
        self.simulator.create_fleet(self.buses)
        for bus in self.buses:
            for device in bus["devices"]:
                name = f"{bus['name']}-s{device['station_id']}"
                with self.desktop.step(f"配置第{bus['index']}路站号{device['station_id']}华为连接及21点"):
                    self.upper.create_connection(name, bus["serial_device"], self.config.baud_rate,
                                                 8, 1, "N", device["station_id"], self.config.poll_interval_ms)
                    for record in self.desktop.metadata.get("本例RTU连接", []):
                        if record["name"] == name:
                            record["清理状态"] = "按机群验收方案保留，供用户检查"
                    tags = {}
                    for point in READ_POINTS + CONTROL_POINTS:
                        tag = f"{name}-{point.key}"
                        self.upper.add_huawei_point(point, tag)
                        if point.function == 3:
                            tags[point.key] = tag
                    self.upper.assert_saved_configuration()
                    self.upper.start()
                    self.connections[self._identity(bus, device)] = {"name": name, "tags": tags}
                    self.desktop.metadata["华为已配置设备数"] = len(self.connections)
        if len(self.connections) != 21:
            raise AssertionError("未完整配置21台华为连接，不能开始全机群抄读验收")

    def apply_stage(self, stage, bus, device):
        name, station = bus["name"], device["station_id"]
        if stage == "有功调节":
            self.simulator.apply_control(name, station, "percent", 10 + device["ordinal"] / 10)
        elif stage == "无功调节":
            self.simulator.apply_control(name, station, "pf", 0.9)
        elif stage == "负无功调节":
            self.simulator.apply_control(name, station, "ratio", -0.2)
        elif stage == "待机":
            self.simulator.apply_scenario(name, station, dc_available_kw=0, disturbance_enabled=False)
        elif stage == "限电停运":
            self.simulator.apply_scenario(name, station, dc_available_kw=device["dc_available_kw"], disturbance_enabled=False)
            self.simulator.apply_control(name, station, "percent", 0)
        elif stage == "恢复发电":
            self.simulator.apply_control(name, station, "ratio", 0)
            self.simulator.apply_control(name, station, "percent", 100)

    def _physics_errors(self, stage, device, snapshot):
        """固定场景与目标独立约束，避免两端同时落在错误工况仍通过。"""
        registers = snapshot["registers"]
        power = Decimal(registers[32080]["value"]) / 1000
        reactive = Decimal(registers[32082]["value"]) / 1000
        pf = Decimal(registers[32084]["value"]) / 1000
        errors = []
        # 按协议分辨率容纳取整；逐点双端比较仍使用1e-6绝对误差。
        phase_voltage = Decimal(device["grid_line_voltage_v"]) / Decimal(3).sqrt()
        for address in (32069, 32070, 32071):
            if abs(Decimal(registers[address]["value"]) / 10 - phase_voltage) > Decimal("0.1"):
                errors.append(f"地址{address}电压不符合本台固定场景")
        if stage in ("待机", "限电停运"):
            if power != 0 or reactive != 0 or pf != 1:
                errors.append("待机/限电停运应为P=0、Q=0、PF=1")
            if any(registers[address]["value"] != 0 for address in (32072, 32074, 32076)):
                errors.append("待机/限电停运的三相电流应为0")
        else:
            target = (Decimal(str(device["dc_available_kw"])) * Decimal(str(device["efficiency"]))
                      if stage in ("发电", "恢复发电") else
                      Decimal(registers[30075]["value"]) / 1000
                      * (Decimal(10) + Decimal(device["ordinal"]) / 10) / 100)
            if abs(power - target) > Decimal("0.001"):
                errors.append(f"有功未达到本台固定目标{target}kW")
            if stage in ("发电", "有功调节", "恢复发电") and (reactive != 0 or pf != 1):
                errors.append("零无功场景应为Q=0、PF=1")
            if stage == "无功调节" and (reactive <= 0 or abs(pf - Decimal("0.9")) > Decimal("0.001")):
                errors.append("PF0.9调节后应有正无功且PF接近0.9")
            if stage == "负无功调节" and (reactive >= 0 or not -1 < pf < 0):
                errors.append("负Q/S调节后Q和PF应为负值")
        return errors

    def sample(self, stage, bus, device):
        identity = self._identity(bus, device)
        connection = self.connections[identity]
        self.upper.select(connection["name"])
        started = time.monotonic()
        deadline = started + self.config.acquisition_timeout
        attempts, last = [], None
        while True:
            self.desktop._check_stop()
            before = self.simulator.observe_device(bus["name"], device["station_id"], self.addresses)
            upper = self.upper.observe_device(connection["tags"])
            after = self.simulator.observe_device(bus["name"], device["station_id"], self.addresses)
            rows = []
            for point in READ_POINTS:
                prior = self.previous.get((identity, point.key))
                previous_timestamp = None
                if prior and prior["换算期望"] != str(point_expected(point, before["registers"][point.address]["value"])):
                    previous_timestamp = prior["时间"]
                expected_raw = (STATUS_VALUES[stage] if point.key == "status" else
                                STATUS_WORD_VALUES[stage] if point.key == "status_word" else None)
                row = compare_point(point, before["registers"][point.address]["value"],
                                    after["registers"][point.address]["value"], upper.get(point.key, {}),
                                    expected_raw=expected_raw,
                                    previous_timestamp=previous_timestamp)
                row["Tag"] = connection["tags"][point.key]
                observation = upper.get(point.key, {})
                if str(observation.get("address")) != str(point.address) or "0x03" not in str(observation.get("function", "")):
                    row["错误"].append("运行表地址或功能码与点表目录不一致")
                expected_type = "UINT16" if point.data_type == "BOOL" else point.data_type
                if before["registers"][point.address]["type"] != expected_type:
                    row["错误"].append("模拟器寄存器类型与点表目录不一致")
                row["通过"] = not row["错误"]
                rows.append(row)
            errors = self._physics_errors(stage, device, before)
            if before["link_path"] != bus["serial_device"] or after["link_path"] != bus["serial_device"]:
                errors.append("模拟器实际总线串口路径与下位机连接配置不一致")
            if after["refresh_tick"] <= before["refresh_tick"]:
                errors.append("模拟器后采样不是新的页面状态刷新")
            if (before["bus"], before["station_id"]) != (bus["name"], device["station_id"]):
                errors.append("模拟器设备身份与目标不一致")
            elapsed = time.monotonic() - started
            if elapsed > self.config.acquisition_timeout:
                errors.append(f"采样已超过{self.config.acquisition_timeout}秒时限，不能判定按时匹配")
            last = {"工况": stage, "路号": bus["index"], "总线": bus["name"], "站号": device["station_id"],
                    "连接名称": connection["name"], "串口": bus["serial_device"],
                    "点位": rows, "模拟器前采样": before, "模拟器后采样": after,
                    "上位机采样": upper, "工况错误": errors,
                    "采样时长秒": round(elapsed, 3),
                    "通过": not errors and all(row["通过"] for row in rows)}
            attempts.append(last)
            self._save_attempts(identity, stage, attempts)
            if last["通过"] or time.monotonic() >= deadline:
                for row in rows:
                    self.previous[(identity, row["点标识"])] = row
                return last
            # 只等待同次操作收敛/刷新，不重发控制、配置或场景操作。
            time.sleep(0.2)

    def _save_attempts(self, identity, stage, attempts):
        path = self.desktop.artifacts / f"huawei-samples-{identity.replace('/', '-')}-{STAGES.index(stage)}.json"
        path.write_text(json.dumps(attempts, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def capture_both(self, description):
        shots = []
        for source, capture in (("slaveSim", self.simulator.capture), ("DSP上位机", self.desktop.capture)):
            try:
                begin = len(self.desktop.screenshots)
                capture("huawei-fleet-scene", description=f"{source}：{description}")
                shots.extend(shot["name"] for shot in self.desktop.screenshots[begin:])
            except Exception as error:
                message = f"{source}：{error}"
                self.evidence_errors.append(message)
                self.desktop.metadata.setdefault("华为取证错误", []).append(message)
                LOGGER.exception("华为%s现场采集失败，保留原始业务结果", source)
        return shots

    def run(self):
        self.configure()
        for stage in STAGES:
            for bus in self.buses:
                for device in bus["devices"]:
                    title = f"第{bus['index']}路站号{device['station_id']}：{stage}双端匹配"
                    last = None
                    try:
                        with self.desktop.step(title, expected="18个采集点全部匹配，质量正常，工况符合固定目标"):
                            self.apply_stage(stage, bus, device)
                            last = self.sample(stage, bus, device)
                            prior_evidence_errors = len(self.evidence_errors)
                            last["截图"] = self.capture_both(title)
                            if len(self.evidence_errors) > prior_evidence_errors:
                                last["工况错误"].extend("双端证据缺失：" + error for error in self.evidence_errors[prior_evidence_errors:])
                                last["通过"] = False
                            self.results.append(last)
                            self.write_report()
                            assert last["通过"], self.failure_description(last)
                    except (InterruptedError, KeyboardInterrupt):
                        self.write_report()
                        raise
                    except Exception as error:
                        if last is None:
                            last = {"工况": stage, "路号": bus["index"], "总线": bus["name"],
                                    "站号": device["station_id"], "点位": [], "通过": False,
                                    "工况错误": [f"操作或取数失败，未完成目标验收：{error}"],
                                    "截图": self.capture_both(title + "失败现场")}
                            self.results.append(last)
                        elif last["通过"]:
                            last["通过"] = False
                            last["工况错误"].append(f"验收或报告处理失败：{error}")
                        LOGGER.exception("华为设备验收失败，保留证据并继续其他设备：%s", title)
                        if isinstance(error, OSError):
                            raise
                        self.write_report()
        failed = [item for item in self.results if not item["通过"]]
        expected_count = 21 * len(STAGES)
        if len(self.results) != expected_count or failed:
            raise AssertionError(f"华为21台机群未全部通过：完成{len(self.results)}/{expected_count}项设备工况，失败{len(failed)}项；详见huawei-fleet-results.md")

    @staticmethod
    def failure_description(result):
        failures = [f"{point['点名']}：{'；'.join(point['错误'])}" for point in result["点位"] if not point["通过"]]
        return "；".join(result["工况错误"] + failures)

    def write_report(self):
        completed = len(self.results)
        total = 21 * len(STAGES)
        failed = [item for item in self.results if not item["通过"]]
        devices, channels = [], []
        for bus in self.buses:
            passed_devices = 0
            for device in bus["devices"]:
                records = [item for item in self.results if item["路号"] == bus["index"] and item["站号"] == device["station_id"]]
                passed = (len(records) == len(STAGES) and {item["工况"] for item in records} == set(STAGES)
                          and all(item["通过"] and len(item["点位"]) == len(READ_POINTS) for item in records))
                passed_devices += int(passed)
                devices.append({"路号": bus["index"], "站号": device["station_id"], "完成工况": len(records), "全部匹配": passed})
            channels.append({"路号": bus["index"], "设备数": len(bus["devices"]), "通过设备": passed_devices})
        passed_devices = sum(int(item["全部匹配"]) for item in devices)
        self.desktop.metadata["华为验收汇总"] = {"已配置设备": len(self.connections), "计划设备": 21,
                                                 "已完成工况": completed, "计划工况": total, "失败工况": len(failed),
                                                 "通过设备": passed_devices, "逐路": channels, "逐台": devices,
                                                 "全部抄通": len(self.connections) == 21 and completed == total and passed_devices == 21}
        report = {"拓扑": self.buses, "汇总": self.desktop.metadata["华为验收汇总"],
                  "未验证控制": list(UNIMPLEMENTED_CONTROLS), "设备工况结果": self.results}
        (self.desktop.artifacts / "huawei-fleet-results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        lines = ["# 华为六路21台抄读匹配结果", "", f"设备工况完成：{completed}/{total}，失败：{len(failed)}。",
                 "", "范围：模拟器发起场景/调节后的DSP抄读匹配。DSP下发调节及104遥控未验证。",
                 "40200/40201启停遥控：模型未实现，未发送命令，不计为通过。", "",
                 "| 路号 | 设备数 | 全部工况匹配设备数 |", "| --- | --- | --- |"]
        lines.extend(f"| {item['路号']} | {item['设备数']} | {item['通过设备']} |" for item in channels)
        lines.extend(["", "| 路号 | 站号 | 工况 | 匹配点数 | 结果 | 错误 |", "| --- | --- | --- | --- | --- | --- |"])
        for item in self.results:
            counts = Counter(point["通过"] for point in item["点位"])
            error = self.failure_description(item).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {item['路号']} | {item['站号']} | {item['工况']} | {counts[True]}/{len(READ_POINTS)} | {'通过' if item['通过'] else '失败'} | {error} |")
        lines.extend(["", "逐点原始值、期望、实际、差值、质量和时间见 [完整对照](huawei-fleet-results.json)。",
                      "每台各工况的采样过程保存在 huawei-samples-*.json，截图路径记录在完整对照中。"])
        (self.desktop.artifacts / "huawei-fleet-results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
