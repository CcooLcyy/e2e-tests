"""业务报告与完整诊断资料分别生成，原始测试记录保持不变。"""
import json
import logging
import math
from pathlib import Path
import re
from urllib.parse import quote


STATUS_LABELS = {"passed": "通过", "failed": "失败", "skipped": "跳过", "cancelled": "已停止", "unknown": "未判定"}


def _write_case_diagnostics(directory, result):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    def annotated_name(shot):
        name = shot.get("annotated_name", "")
        if (shot.get("annotation_status") != "saved" or not isinstance(name, str)
                or not name or Path(name).name != name or name == shot["name"]):
            return ""
        path = directory / name
        if path.is_file() and path.resolve().parent == directory.resolve() and (directory / shot["name"]).is_file():
            return name
        return ""
    def evidence_link(shot):
        if shot["capture_status"] != "saved":
            return f"证据缺失：{cell(shot['error'])}"
        copy = annotated_name(shot)
        return f"[查看截图]({copy or shot['name']})" + (f" · [查看原图]({shot['name']})" if copy else "")
    lines = ["# 用例诊断资料", "", f"结果：**{result['结果']}**", ""]
    lines.extend(f"- {key}：{value}" for key, value in result.items() if key not in ("步骤", "截图", "错误"))
    lines.extend(["", "| 序号 | 步骤 | 结果 | 预期 | 实际 | 耗时（秒） | 截图 |", "| --- | --- | --- | --- | --- | --- | --- |"])
    for step in result.get("步骤", []):
        shots = [shot for shot in result.get("截图", [])
                 if step.get("序号") is not None and shot.get("step_id") == step["序号"]]
        evidence = "；".join(evidence_link(shot) for shot in shots)
        lines.append(f"| {step.get('序号', '—')} | {cell(step['步骤'])} | {step['状态']} | {cell(step.get('预期', '未提供'))} | {cell(step.get('实际', '未提供'))} | {step.get('耗时秒', '—')} | {evidence or '旧记录未关联截图'} |")
    if result.get("错误"):
        lines.extend(["", "```text", str(result["错误"]), "```"])
    shots = result.get("截图", [])
    for shot in shots:
        lines.extend(["", f"## {shot['description']}", "", f"- 预期现象：{cell(shot['expected'])}",
                      f"- 实际现象：{cell(shot['actual'])}", f"- 拍摄时判定：{STATUS_LABELS.get(shot['status'], '未判定')}",
                      f"- 记录时间：{shot['time']}"])
        if shot["capture_status"] == "saved":
            copy = annotated_name(shot)
            lines.extend(["", f"![{shot['description']}]({copy or shot['name']})"])
            if copy:
                lines.extend(["", f"[查看原图]({shot['name']})"])
            elif shot.get("annotation_status") == "saved":
                lines.extend(["", "标注副本无法读取，已展示原图。"])
            if shot.get("annotation_error"):
                lines.extend(["", f"- 标注说明：{cell(shot['annotation_error'])}"])
            for index, annotation in enumerate(shot.get("annotations", []), 1):
                reason = f"（{annotation['reason']}）" if annotation.get("reason") else ""
                lines.append(f"- 标记 {index}：{cell(annotation.get('label', ''))}{cell(reason)}")
        else:
            lines.append(f"- 截图证据缺失：{shot['error']}")
    known = {shot["name"] for shot in shots}
    known.update(shot.get("annotated_name", "") for shot in shots)
    for path in sorted(directory.glob("*.png")):
        if path.name not in known and not path.name.endswith(".annotated.png"):
            lines.extend(["", f"![{path.stem}]({path.name})", "", "旧截图未提供说明、预期/实际和判定。"])
    lines.extend(["", "## 完整原始记录", "", "```json", json.dumps(result, ensure_ascii=False, indent=2), "```",
                  "", "[原始结构化记录](report.json)"])
    (directory / "diagnostics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


_TECHNICAL = re.compile(
    r"Traceback|WebDriver|selenium|pytest|\b[A-Za-z_]*(?:Error|Exception)\b|"
    r"\b[A-Za-z_][\w.-]*\.(?:py|tsx?|exe|dll|so|rs|json)(?::\d+)?\b|"
    r"[A-Za-z]:[/\\]|(?:^|\s)/(?:home|data|tmp|storage|Users|usr)/|"
    r"<[^>]*(?:object|0x[0-9a-fA-F]+)[^>]*>|\b(?:PID|SHA256|WebView2|gRPC)\b|"
    r"\b(?:http|https|tauri)://|\breturn\s+|\bassert\s+|__\w+__|::|"
    r"\b(?:driver|desktop|self)\.[A-Za-z_]+|0x[0-9a-fA-F]{6,}")


def _business_text(value, fallback="未记录"):
    """只显示可读业务现象；技术载荷和结构体仍完整保留在诊断资料。"""
    if value is True:
        return "是"
    if value is False:
        return "否"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value) if math.isfinite(value) else fallback
    if not isinstance(value, str):
        return fallback
    value = value.split("\n异常：", 1)[0].strip()
    if value in ("true", "True", "false", "False"):
        return "是" if value.lower() == "true" else "否"
    array_like = value.startswith("[") and not re.match(r"^\[(?:DSP上位机|104主站)\]\s", value)
    if (not value or value.startswith(("未提供", "未记录")) or _TECHNICAL.search(value)
            or value.startswith(("{", "```")) or array_like or "\n" in value):
        return fallback
    return value[:800]


def _label(status):
    if not isinstance(status, str):
        return "未判定"
    return STATUS_LABELS.get(status, status if status in (*STATUS_LABELS.values(), "未执行") else "未判定")


def _records(result, key):
    value = result.get(key, [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def business_case(result):
    """从记录生成独立业务视图；不重写或补造原始预期、实际和判定。"""
    result = result if isinstance(result, dict) else {}
    title = _business_text(result.get("用例"), "测试用例")
    status = _label(result.get("结果", "unknown"))
    checks = []
    for step in _records(result, "步骤"):
        if step.get("步骤") in ("真实桌面运行环境", "页面来源属于上位机", "错误密码长度", "错误密码与正确密码不同"):
            continue
        if "预期" not in step and "实际" not in step and step.get("状态") != "失败":
            continue
        name = _business_text(step.get("步骤"), "业务检查")
        if name == "业务检查" and step.get("状态") != "失败":
            continue
        check_status = "已停止" if status == "已停止" and str(step.get("错误", "")).startswith("InterruptedError:") else _label(step.get("状态"))
        checks.append({"step_id": step.get("序号"), "name": name, "status": check_status,
                       "expected": _business_text(step.get("预期")), "actual": _business_text(step.get("实际"))})
    failures = [check for check in checks if check["status"] == "失败"]
    # 同一次失败优先呈现外层业务文字，布尔断言不能挤掉可读的差异。
    meaningful = [check for check in failures if check["expected"] not in ("未记录", "是", "否")
                  and check["actual"] not in ("未记录", "是", "否")]
    selected = meaningful[-1] if meaningful else (failures[-1] if failures else None)
    if status == "失败" and selected:
        summary = f"{selected['name']}未满足要求。预期：{selected['expected']}；实际：{selected['actual']}。"
    else:
        summary = {"通过": "本用例检查通过。", "失败": "测试未完成或检查未满足要求，具体原因见诊断资料。",
                   "已停止": "本用例已停止，未完成的行为尚未验证。", "跳过": "本用例未完成验证。",
                   "未执行": "本用例尚未执行。", "未判定": "尚未记录用例最终结果。"}.get(status, "尚未记录用例最终结果。")
    shots = _records(result, "截图")
    saved = [shot for shot in shots if shot.get("capture_status") == "saved" and isinstance(shot.get("name"), str)]
    chosen = []
    if status == "失败":
        chosen.extend(shot for shot in saved if selected and shot.get("step_id") == selected["step_id"])
        chosen.extend(shot for shot in saved if shot.get("status") == "failed")
    chosen.extend(shot for shot in saved if not shot["name"].startswith(("step-", "case-failure")))
    if not chosen and saved:
        chosen.append(saved[-1])
    names = list(dict.fromkeys(shot["name"] for shot in chosen))[:3]
    return {"title": title, "goal": _business_text(result.get("测试目标"), title), "result": status,
            "summary": summary, "checks": checks, "key_screenshots": names}


def _cell(value):
    return (str(value).replace("&", "&amp;").replace("|", "\\|").replace("\r", " ").replace("\n", " ")
            .replace("<", "&lt;").replace(">", "&gt;").replace("[", "&#91;").replace("]", "&#93;"))


def _image_name(directory, shot, annotated=True):
    """图片只能使用当前产物目录中的文件，副本存在时优先展示。"""
    original = shot.get("name")
    if not isinstance(original, str) or Path(original).name != original:
        return ""
    raw = directory / original
    if not raw.is_file() or raw.resolve().parent != directory.resolve():
        return ""
    name = shot.get("annotated_name")
    if annotated and shot.get("annotation_format") == "business-v1" and shot.get("annotation_status") == "saved" and isinstance(name, str) and Path(name).name == name:
        path = directory / name
        if path.is_file() and path.resolve().parent == directory.resolve():
            return name
    return original


def _diagnostic_result(result):
    """兼容旧截图字段，补齐仅供诊断排版的缺省值而不改原记录。"""
    value = dict(result)
    value["步骤"] = [{"步骤": "未记录步骤名称", "状态": "未判定", **step} for step in _records(result, "步骤")]
    value["截图"] = [{"description": "现场记录", "expected": "未提供预期现象", "actual": "未提供实际现象",
                     "status": "unknown", "capture_status": "missing", "error": "", "time": "", "name": "未命名截图", **shot}
                    for shot in _records(result, "截图")]
    for shot in value["截图"]:
        shot["annotations"] = [item for item in shot.get("annotations", []) if isinstance(item, dict)] if isinstance(shot.get("annotations"), list) else []
    value.setdefault("结果", "未判定")
    return value


def write_case_report(directory, result):
    """完整资料写入诊断报告，默认报告只展示业务结论和关键现场。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # 诊断排版有兼容缺省值，report.json 始终保存传入的完整原始记录。
    (directory / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_case_diagnostics(directory, _diagnostic_result(result))
    view = business_case(result)
    lines = [f"# {_cell(view['title'])}", "", f"结果：**{view['result']}**", "",
             f"测试目标：{_cell(view['goal'])}", "", _cell(view["summary"])]
    if view["checks"]:
        lines.extend(["", "## 检查结果", "", "| 检查内容 | 结果 | 预期表现 | 实际表现 |", "| --- | --- | --- | --- |"])
        for check in view["checks"]:
            lines.append(f"| {_cell(check['name'])} | {check['status']} | {_cell(check['expected'])} | {_cell(check['actual'])} |")
    else:
        lines.extend(["", "检查现象：未记录。"])
    selected = set(view["key_screenshots"])
    for shot in _records(result, "截图"):
        if shot.get("name") not in selected:
            continue
        image = _image_name(directory, shot)
        if not image:
            continue
        title = _business_text(shot.get("description"), "检查现场")
        lines.extend(["", f"## {_cell(title)}", "", f"预期表现：{_cell(_business_text(shot.get('expected')))}",
                      "", f"实际表现：{_cell(_business_text(shot.get('actual')))}", "", f"![{_cell(title)}]({quote(image)})", "",
                      f"[查看原图]({quote(shot['name'])})", "", "本图用于记录检查现场，最终结果以本用例结论为准。"])
    lines.extend(["", "[诊断资料](diagnostics.md)"])
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    logging.getLogger(__name__).info("已分别保存用例业务报告与完整诊断资料")


def _case_directory(directory, case):
    value = case.get("artifacts_dir")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).resolve()
    return path if path.is_dir() and path.is_relative_to(directory.resolve()) and path != directory.resolve() else None


def write_task_report(directory, summary, task):
    """正常结束及中断共用的业务汇总；完整快照与日志保存在诊断资料。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cases = _records(summary, "cases")
    names = task.get("case_names", {})
    names = names if isinstance(names, dict) else {}
    counts = {name: 0 for name in ("通过", "失败", "已停止", "跳过", "未执行")}
    for case in cases:
        status = "未执行" if case.get("status") in ("pending", "not_run") or (case.get("status") == "skipped" and case.get("message") == "未执行") else _label(case.get("status"))
        if status in counts:
            counts[status] += 1
    lines = ["# 测试任务报告", "", f"任务结果：**{_label(summary.get('status'))}**", "",
             " · ".join(f"{name} {count}" for name, count in counts.items())]
    for label, key in (("开始时间", "started_at"), ("结束时间", "finished_at")):
        text = _business_text(summary.get(key, summary.get("ended_at") if key == "finished_at" else task.get(key)), "")
        if text:
            lines.extend(["", f"{label}：{_cell(text)}"])
    lines.extend(["", "| 测试用例 | 结果 | 耗时（秒） | 报告 |", "| --- | --- | --- | --- |"])
    for index, case in enumerate(cases, 1):
        path = _case_directory(directory, case)
        raw = {}
        if path and (path / "report.json").is_file() and (path / "report.json").resolve().parent == path:
            try:
                raw = json.loads((path / "report.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logging.getLogger(__name__).warning("用例记录不可读，汇总保留已知结果")
        raw = raw if isinstance(raw, dict) else {}
        title = _business_text(names.get(case.get("case_id")), _business_text(raw.get("用例"), f"测试用例 {index}"))
        status = "未执行" if case.get("status") in ("pending", "not_run") or (case.get("status") == "skipped" and case.get("message") == "未执行") else _label(case.get("status"))
        link = "未生成"
        if path and (path / "report.md").is_file() and (path / "report.md").resolve().parent == path:
            link = f"[查看报告]({quote((path / 'report.md').relative_to(directory.resolve()).as_posix())})"
        lines.append(f"| {_cell(title)} | {status} | {_cell(_business_text(case.get('duration')))} | {link} |")
    lines.extend(["", "[诊断资料](diagnostics.md)"])
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    diagnostics = ["# 测试任务诊断资料", "", "## 任务快照", "", "```json", json.dumps(task, ensure_ascii=False, indent=2),
                   "```", "", "## 完整汇总", "", "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", "", "## 原始文件", ""]
    for filename in ("task.json", "summary.json", "events.jsonl", "pytest.log", "junit.xml", "worker.json", "done.json"):
        diagnostics.append(f"- [{filename}]({filename})")
    for index, case in enumerate(cases, 1):
        path = _case_directory(directory, case)
        if path:
            relative = path.relative_to(directory.resolve()).as_posix()
            diagnostics.append(f"- 用例 {index}：[{case.get('case_id', '未记录')} 的现场目录]({quote(relative + '/')})")
            for entry in sorted(path.iterdir()):
                if entry.is_file() and entry.resolve().parent == path and entry.suffix.lower() in (".json", ".md", ".log", ".png", ".txt"):
                    diagnostics.append(f"  - [{entry.name}]({quote(relative + '/' + entry.name)})")
    (directory / "diagnostics.md").write_text("\n".join(diagnostics) + "\n", encoding="utf-8")
    logging.getLogger(__name__).info("已分别保存任务业务汇总与完整诊断资料，共 %s 项用例", len(cases))
