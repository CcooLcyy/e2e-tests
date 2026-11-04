"""合并不同桌面驱动的证据，不改变驱动原始编号和记录。"""
from copy import deepcopy
import json
import logging
from pathlib import Path


SOURCE_LABELS = {"desktop": "DSP上位机", "master104": "104主站"}


def collect_sessions(item):
    """兼容既有桌面绑定，只收集本例实际申请的会话。"""
    return [(source, app) for source, attribute in (("desktop", "_bench_desktop"), ("master104", "_bench_master104"))
            if (app := getattr(item, attribute, None)) is not None]


def merge_session_evidence(result, sessions, *, event_path=None, case_id=""):
    """保留来源与局部编号，主站使用与实时事件一致的命名空间。"""
    if not sessions:
        return result
    if len(sessions) == 1 and sessions[0][0] == "desktop":
        app = sessions[0][1]
        result.update(deepcopy(app.metadata))
        result.update({"步骤": deepcopy(app.steps), "截图": deepcopy(app.screenshots)})
        return result
    result["会话"] = {}
    steps, shots = [], []
    for source, app in sessions:
        result["会话"][source] = deepcopy(app.metadata)
        if source == "desktop":
            result.update(deepcopy(app.metadata))
        label = SOURCE_LABELS[source]
        for original in app.steps:
            step = deepcopy(original)
            step["来源"] = label
            step["原序号"] = original.get("序号")
            if source != "desktop" and "序号" in step:
                step["序号"] = f"{source}:{step['序号']}"
            if source != "desktop" and not str(step["步骤"]).startswith(f"[{label}]"):
                step["步骤"] = f"[{label}] {step['步骤']}"
            steps.append(step)
        for original in app.screenshots:
            shot = deepcopy(original)
            shot["source"] = source
            if source != "desktop" and "step_id" in shot:
                shot["step_id"] = f"{source}:{shot['step_id']}"
            shots.append(shot)
    if event_path:
        order = _event_step_order(event_path, case_id)
        steps.sort(key=lambda step: order.get(step.get("序号"), len(order)))
    result.update({"步骤": steps, "截图": shots})
    return result


def _event_step_order(path, case_id):
    order = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        logging.getLogger(__name__).warning("无法读取步骤事件，报告按证据来源保留原始顺序")
        return order
    for line in lines:
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if (event.get("type") == "step" and event.get("case_id") == case_id
                and event.get("status") == "running" and isinstance(event.get("step_id"), (str, int))):
            order.setdefault(event["step_id"], len(order))
    return order
