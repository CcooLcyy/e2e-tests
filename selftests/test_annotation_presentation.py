"""标注副本的环境保存、证据关联与报告展示，不访问真实应用。"""
import json

import pytest

from testbench.bridge import TestbenchBridge
from testbench.model import Environment, load_environment, save_environment
from testbench.reporting import write_case_report


def make_record():
    return {"name": "step.png", "description": "点击登录", "expected": True,
            "actual": False, "status": "failed", "capture_status": "saved",
            "error": "", "time": "2026-11-04", "annotated_name": "step.annotated.png",
            "annotation_status": "saved", "annotation_error": "", "annotation_format": "business-v1",
            "annotations": [{"label": "登录按钮", "reason": "目标已消失"}]}


def make_case(tmp_path, record):
    directory = tmp_path / "artifacts" / "task" / "case"
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps({"截图": [record]}), encoding="utf-8")
    return directory


def test_annotation_switch_defaults_and_persists(tmp_path):
    """旧环境默认启用，显式关闭后保存加载保留布尔值。"""
    assert Environment.from_dict({}).annotation_enabled is True
    app = tmp_path / "app.exe"
    app.touch()
    env = Environment(app_path=str(app), driver_path=str(app), annotation_enabled=False)
    save_environment(tmp_path / "environment.json", env)
    assert load_environment(tmp_path / "environment.json").annotation_enabled is False
    env.annotation_enabled = "false"
    with pytest.raises(ValueError, match="标注"):
        env.validate()


def test_bridge_keeps_original_and_attaches_annotation_without_duplicate(tmp_path):
    """标注图只归属原证据，保留原图字节和失败判定。"""
    record = make_record()
    directory = make_case(tmp_path, record)
    for name in (record["name"], record["annotated_name"]):
        (directory / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    shots = TestbenchBridge(tmp_path)._case_details(str(directory))["screenshots"]
    assert len(shots) == 1
    shot = shots[0]
    assert shot["status"] == "failed"
    assert shot["name"] == "step.png" and shot["path"].endswith("step.png")
    assert shot["annotated_path"].endswith("step.annotated.png")
    assert shot["annotated_data_url"] != shot["data_url"]
    assert shot["annotations"] == record["annotations"]


@pytest.mark.parametrize("annotation_name", ["step.annotated.png", "../../outside.png", "step.png"])
def test_invalid_or_missing_annotation_falls_back_to_original(tmp_path, annotation_name):
    """缺少副本、路径越界及把原图冒充副本时均返回原图和明确原因。"""
    record = {**make_record(), "annotated_name": annotation_name}
    directory = make_case(tmp_path, record)
    (directory / "step.png").write_bytes(b"\x89PNG\r\n\x1a\noriginal")
    (directory.parent.parent / "outside.png").write_bytes(b"\x89PNG\r\n\x1a\nprivate")
    shot = TestbenchBridge(tmp_path)._case_details(str(directory))["screenshots"][0]
    assert shot["capture_status"] == "saved" and shot["data_url"]
    assert not shot["annotated_data_url"]
    assert shot["annotation_status"] == "failed" and shot["annotation_error"]


def test_annotation_cannot_replace_missing_original(tmp_path):
    """只有标注副本不能把缺失的原始证据改判为已保存。"""
    record = make_record()
    directory = make_case(tmp_path, record)
    (directory / record["annotated_name"]).write_bytes(b"\x89PNG\r\n\x1a\ncopy")
    shots = TestbenchBridge(tmp_path)._case_details(str(directory))["screenshots"]
    assert len(shots) == 1
    assert shots[0]["capture_status"] == "missing"
    assert not shots[0]["data_url"] and not shots[0]["annotated_data_url"]


def test_orphan_annotation_is_not_invented_as_legacy_evidence(tmp_path):
    """未关联报告的标注副本不能成为另一条独立证据。"""
    record = make_record()
    directory = make_case(tmp_path, record)
    (directory / "step.png").write_bytes(b"\x89PNG\r\n\x1a\noriginal")
    (directory / "orphan.annotated.png").write_bytes(b"\x89PNG\r\n\x1a\ncopy")
    shots = TestbenchBridge(tmp_path)._case_details(str(directory))["screenshots"]
    assert [shot["name"] for shot in shots] == ["step.png"]


def test_markdown_prefers_annotation_links_original_and_avoids_duplicate(tmp_path):
    """Markdown 默认嵌入标注图，给出原图链接，副本不另列旧证据。"""
    record = make_record()
    for name in (record["name"], record["annotated_name"]):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\nimage")
    write_case_report(tmp_path, {"结果": "失败", "步骤": [], "截图": [record]})
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "![点击登录](step.annotated.png)" in report
    assert "[查看原图](step.png)" in report
    assert report.count("![") == 1
    assert "目标已消失" in (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    (tmp_path / record["annotated_name"]).unlink()
    write_case_report(tmp_path, {"结果": "失败", "步骤": [], "截图": [record]})
    assert "![点击登录](step.png)" in (tmp_path / "report.md").read_text(encoding="utf-8")
