"""框架统一生成用例报告，截图说明与判定保留在证据旁。"""
import json
from pathlib import Path


STATUS_LABELS = {"passed": "通过", "failed": "失败", "skipped": "跳过", "cancelled": "已停止", "unknown": "未判定"}


def write_case_report(directory, result):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    lines = ["# 上位机用例测试报告", "", f"结果：**{result['结果']}**", ""]
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
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
