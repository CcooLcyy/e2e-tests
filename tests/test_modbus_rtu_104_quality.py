"""用专用预置 RTU 场景验收 IEC104 对外品质。"""
from decimal import Decimal
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from testbench.case_plan import (
    DESKTOP_FINISH, DESKTOP_START, MASTER104_FINISH, MASTER104_START,
    Step, case_plan, run_steps, track_fixture_step,
)
from testbench.login_page import PlatformLoginPage, load_login_account
from testbench.login_plan import login_steps
from testbench.modbus_rtu_config import validate_manager_address
from testbench.modbus_rtu_page import ModbusRtuPage, xpath_text
from testbench.model import Environment
from testbench.rtu_quality_frames import capture_frames, decode_frame
from testbench.workspace import local_config


pytestmark = pytest.mark.case_group("Modbus RTU")
PHASES = (
    ("首次有效采集", 0x00),
    ("通信超时", 0x40),
    ("设备异常响应", 0xC0),
    ("同值恢复", 0x00),
)


def _profile():
    path = local_config("modbus-rtu-104-quality.json")
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"前置条件缺失或配置不可读，请填写 {path}") from exc
    text_fields = ("manager_address", "rtu_connection", "rtu_tag", "master_host")
    bounds = {"master_port": (1, 65535), "common_address": (1, 65534),
              "target_ioa": (0, 0xFFFFFF), "reference_ioa": (0, 0xFFFFFF)}
    if not isinstance(profile, dict) or profile.keys() != {*text_fields, *bounds, "phase_timeout_seconds"}:
        raise ValueError("专项配置字段不符，请参照 Modbus RTU 到 104 品质验收文档")
    for name in text_fields:
        if not isinstance(profile[name], str) or not profile[name].strip():
            raise ValueError(f"专项配置 {name} 须为非空字符串")
        profile[name] = profile[name].strip()
    validate_manager_address(profile["manager_address"])
    for name, (minimum, maximum) in bounds.items():
        if type(profile[name]) is not int or not minimum <= profile[name] <= maximum:
            raise ValueError(f"专项配置 {name} 须在 {minimum}～{maximum} 之间")
    if profile["target_ioa"] == profile["reference_ioa"]:
        raise ValueError("目标点与参照点的 IOA 必须不同")
    timeout = profile["phase_timeout_seconds"]
    if type(timeout) not in (int, float) or not 0 < timeout <= 300 or not math.isfinite(timeout):
        raise ValueError("每阶段等待时间须为 0～300 秒内的正数")
    return profile


def _load_account(context):
    context.account = load_login_account(local_config("login-account.json"))


def _check_master_environment():
    task_path = os.environ.get("TESTBENCH_TASK")
    if not task_path:
        raise RuntimeError("前置条件未满足：请从测试上位机运行并启用专用 104 主站")
    task = json.loads(Path(task_path).read_text(encoding="utf-8"))
    environment = Environment.from_dict(task["environment"])
    environment.validate_master104(required=True)
    environment.validate_master104_driver()


def _select_connection(context):
    page = context.rtu
    connection = context.profile["rtu_connection"]
    page._click(page._connection_locator(connection), "选择专用 RTU 连接")
    title = (By.XPATH, "//div[contains(@class,'modbus-connection-card')]"
             "//div[contains(@class,'ant-card-head-title')]//span[normalize-space(.)=" + xpath_text(connection) + "]")
    page._until(lambda _: page._visible(title), "前置条件未满足：未显示预置 RTU 连接")
    page._until(lambda _: "运行中" in page.driver.find_element(
        By.CSS_SELECTOR, ".modbus-connection-card .ant-card-head").text,
        "前置条件未满足：专用 RTU 连接须预置并保持轮询运行")


def _target_row(context):
    locator = (By.XPATH, "//div[contains(@class,'protocol-point-card')]//tr[.//td//strong[normalize-space(.)="
               + xpath_text(context.profile["rtu_tag"]) + "]]")
    return context.rtu._visible(locator)


def _wait_for_target(context):
    context.rtu._until(lambda _: _target_row(context), "前置条件未满足：专用 RTU 点表未显示目标点")


def _measurement(master, ca, ioa):
    rows = [row for row in master.read_measurements()["rows"]
            if int(row["cells"][0]) == ca and int(row["cells"][1]) == ioa]
    assert len(rows) <= 1, f"CA={ca} IOA={ioa} 的遥测行不唯一：{rows!r}"
    return rows[0] if rows else None


def _observation_step(index, label, quality):
    action = f"观察{label}的自发遥测"
    expected = f"值 100，品质 0x{quality:02X}"

    def observe(context):
        master, profile = context.master, context.profile
        last = None

        def match():
            nonlocal last
            last = _measurement(master, profile["common_address"], profile["target_ioa"])
            if last is None or int(last["frame_id"] or 0) <= context.frame_id:
                return False
            return last if int(last["cells"][4], 16) == quality else False

        with master.step(action, expected=expected) as step:
            try:
                row = master.wait_for(match, f"未看到 {label} 的新遥测", timeout=profile["phase_timeout_seconds"])
            except TimeoutException:
                evidence = {"阶段": label, "期望品质": f"0x{quality:02X}", "最后主站点表": last,
                            "上个已通过报文编号": context.frame_id, "应答源状态": "本例没有独立观测"}
                try:
                    upper = _target_row(context)
                    evidence["上位机目标行"] = upper.text if upper is not None else None
                except Exception as error:
                    evidence["上位机取证错误"] = str(error)
                step["实际"] = evidence
                master.metadata["RTU品质未观察到的阶段"] = evidence
                raise AssertionError(f"等待{label}阶段超时，104 未观察到品质 0x{quality:02X} 的新遥测；"
                                     f"最后点表行：{last!r}。请结合外部应答源记录区分工况未到和上送异常") from None
            step["实际"] = row
            context.observations.append(row)
            assert int(row["cells"][2]) == 36, f"目标点须为带时标短浮点遥测：{row!r}"
            assert Decimal(row["cells"][3]) == 100, f"目标值应保持 100：{row!r}"
            context.frame_id = int(row["frame_id"])

    return Step(f"quality.phase.{index}", action, expected, observe)


def _save_frames(master):
    frames = capture_frames(master)
    (master.artifacts / "rtu-104-quality-frames.json").write_text(
        json.dumps(frames, ensure_ascii=False, indent=2), encoding="utf-8")
    return frames


def _assert_wire_sequence(frames, profile):
    decoded = [entry for frame in frames if (entry := decode_frame(frame)) is not None]
    ca, ioa, reference = (profile[key] for key in ("common_address", "target_ioa", "reference_ioa"))

    def gi(entry, direction, cot):
        return (entry["direction"] == direction and entry["type_id"] == 100 and entry["ca"] == ca
                and entry["cot"] == cot and not entry["negative"] and not entry["test"]
                and entry["objects"] == [{"ioa": 0, "qoi": 20}])

    requests = [i for i, entry in enumerate(decoded) if gi(entry, "发送", 6)]
    assert len(requests) == 1, "报文历史须保留本会话唯一的站总召请求"
    confirmations = [i for i in range(requests[0] + 1, len(decoded)) if gi(decoded[i], "接收", 7)]
    terminations = [i for i in range(requests[0] + 1, len(decoded)) if gi(decoded[i], "接收", 10)]
    assert len(confirmations) == len(terminations) == 1 and confirmations[0] < terminations[0], (
        "报文中须有本次总召的肯定确认和终止")

    def points(entries, cot):
        for entry in entries:
            if entry["direction"] == "接收" and entry["ca"] == ca and entry["cot"] == cot:
                assert entry["type_id"] in (13, 36), (
                    f"专用测试 CA 收到非短浮点遥测，无法核对本例点序列：{entry!r}")
                for item in entry["objects"]:
                    yield entry, item

    gi_values = list(points(decoded[confirmations[0] + 1:terminations[0]], 20))
    assert any(entry["type_id"] in (13, 36) and item["ioa"] == reference and item["quality"] == 0
               and isinstance(item["value"], (int, float)) and math.isfinite(item["value"])
               and not entry["negative"] and not entry["test"]
               for entry, item in gi_values), "总召报文中缺少正常参照遥测"
    assert all(item["ioa"] != ioa for _, item in gi_values), "无首值目标点出现在总召响应中"
    assert all(item["ioa"] != ioa for _, item in points(decoded[:terminations[0] + 1], 3)), (
        "初始无样本阶段收到了目标点自发上送")

    sequence = []
    for entry, item in points(decoded[terminations[0] + 1:], 3):
        if item["ioa"] != ioa:
            continue
        assert entry["type_id"] == 36 and not entry["negative"] and not entry["test"], (
            f"目标点不是正常传送的带时标短浮点遥测：{entry!r}")
        assert item["value"] == 100, f"目标遥测数值发生变化：{item!r}"
        assert item["timestamp"], f"目标遥测缺少 CP56Time2a 时标：{item!r}"
        if not sequence or item["quality"] != sequence[-1]["quality"]:
            sequence.append(item)
        elif item["quality"] in (0x40, 0xC0):
            assert item["timestamp"] == sequence[-1]["timestamp"], f"重复故障报文刷新了采样时标：{item!r}"
    assert [item["quality"] for item in sequence] == [flags for _, flags in PHASES], (
        f"自发遥测品质顺序不符：{sequence!r}")
    assert sequence[1]["timestamp"] == sequence[2]["timestamp"], "两次故障沿用的样本时标不同"
    assert sequence[3]["timestamp"] != sequence[2]["timestamp"], "恢复后未刷新样本时标"
    return sequence


def _prepare_master_target(context):
    profile = context.profile
    context.master.prepare_target(profile["master_host"], profile["master_port"], profile["common_address"])


def _check_initial_table(context):
    master, profile = context.master, context.profile
    with master.step("检查总召结束时的初始点表", expected="参照点正常，目标点缺席") as step:
        reference = _measurement(master, profile["common_address"], profile["reference_ioa"])
        target = _measurement(master, profile["common_address"], profile["target_ioa"])
        step["实际"] = {"参照点": reference, "目标点": target}
        assert reference and int(reference["cells"][4], 16) == 0, "缺少正常参照点，请检查专用线路"
        assert target is None, f"目标点已有样本或初始无应答窗口不足：{target!r}"
    context.frame_id = 0
    context.observations = []
    master.metadata["RTU品质阶段观察"] = context.observations


def _verify_frames(context):
    master, profile = context.master, context.profile
    with master.step("核对原始总召及自发遥测报文", expected="总召省略目标，随后品质依次为 00、40、C0、00") as step:
        frames = _save_frames(master)
        step["实际"] = f"已保留 {len(frames)} 条收发报文，待核对"
        sequence = _assert_wire_sequence(frames, profile)
        master.metadata["RTU品质报文序列"] = sequence
        step["实际"] = sequence


PROFILE = Step("quality.profile", "检查 RTU 到 IEC104 品质专项配置", "专用下位机、预置 RTU 点位和 104 观察参数有效")
MASTER_READY = Step("quality.master.ready", "检查独立 104 主站的运行配置", "已启用专用主站，主站程序及驱动可用")
LOGIN = (
    Step("quality.login.account", "读取测试账号", "专用平台账号配置有效", _load_account),
    *login_steps("quality.login"),
)
PREPARED_POINT = (
    Step("quality.rtu.links", "通过上位机连接专用下位机并打开 RTU 配置", "RTU 模块运行中，连接列表可用",
         lambda context: context.rtu.open_device(context.profile["manager_address"])),
    Step("quality.rtu.select", "选择专用 RTU 连接", "详情显示配置指定的连接且轮询运行中", _select_connection),
    Step("quality.rtu.runtime", "打开 RTU 运行视图", "点表显示实时值列", lambda context: context.rtu._runtime_view()),
    Step("quality.rtu.target", "核对预置目标点", "运行点表显示配置指定的 Tag", _wait_for_target),
)
INITIAL_SESSION = (
    Step("quality.master.target", "填写并核对专用 104 连接参数", "主机、端口和公共地址与专项配置一致",
         _prepare_master_target),
    Step("quality.master.connect", "连接专用 104 下位机", "收到 STARTDT 确认",
         lambda context: context.master.connect()),
    Step("quality.gi", "在当前会话发起总召", "总召确认终止，主站实时显示正常",
         lambda context: context.master.interrogate()),
)
OBSERVATIONS = (
    Step("quality.gi.table", "检查总召结束时的初始点表", "参照点正常，目标点缺席", _check_initial_table),
    *(_observation_step(index, label, quality) for index, (label, quality) in enumerate(PHASES, start=1)),
)
VERIFY_FRAMES = Step("quality.frames", "核对原始总召及自发遥测报文",
                     "总召省略目标，品质依次为 00、40、C0、00，故障沿用样本时标且恢复后刷新", _verify_frames)


@case_plan(PROFILE, MASTER_READY, DESKTOP_START, *LOGIN, *PREPARED_POINT, MASTER104_START, *INITIAL_SESSION, *OBSERVATIONS, VERIFY_FRAMES,
           MASTER104_FINISH, DESKTOP_FINISH)
def test_rtu_quality_reaches_iec104_with_unchanged_value(request):
    """总召省略未采到的点，值保持 100 时仍依次收到正常、NT、IV+NT 与恢复。"""
    with track_fixture_step(request, PROFILE):
        profile = _profile()
    with track_fixture_step(request, MASTER_READY):
        _check_master_environment()
    desktop = request.getfixturevalue("fresh_desktop")
    context = SimpleNamespace(profile=profile, desktop=desktop, page=PlatformLoginPage(desktop))
    run_steps(request, LOGIN, context)
    context.rtu = ModbusRtuPage(desktop)
    with desktop.step("打开已预配置的 RTU 点表", expected=profile["rtu_tag"]):
        run_steps(request, PREPARED_POINT, context)
    master = request.getfixturevalue("master104")
    context.master = master
    master.metadata["RTU品质验收配置"] = profile
    try:
        with master.step("连接专用 104 下位机并完成总召",
                         expected=f"{profile['master_host']}:{profile['master_port']}，CA={profile['common_address']}"):
            run_steps(request, INITIAL_SESSION, context)
        run_steps(request, OBSERVATIONS, context)
    except Exception:
        try:
            _save_frames(master)
        except Exception as exc:
            master.metadata["RTU品质失败现场取证错误"] = str(exc)
        raise
    run_steps(request, (VERIFY_FRAMES,), context)
