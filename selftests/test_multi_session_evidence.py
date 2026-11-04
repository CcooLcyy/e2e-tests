"""双端证据汇总契约，不启动被测程序。"""
from types import SimpleNamespace

from testbench.session_evidence import collect_sessions, merge_session_evidence


def session(label, value):
    return SimpleNamespace(metadata={"程序": label}, steps=[{"序号": 1, "步骤": "读取状态", "状态": "通过", "实际": value}],
                           screenshots=[{"name": label + ".png", "step_id": 1, "actual": value}])


def test_two_sources_keep_same_numbered_steps_and_original_evidence():
    """两端的本地编号1独立关联截图，合并不能覆盖原数据或把0/False当空值。"""
    upper, master = session("upper", 0), session("master104", False)
    result = merge_session_evidence({}, [("desktop", upper), ("master104", master)])
    assert [step["序号"] for step in result["步骤"]] == [1, "master104:1"]
    assert [shot["step_id"] for shot in result["截图"]] == [1, "master104:1"]
    assert result["步骤"][0]["实际"] == 0
    assert result["截图"][1]["actual"] is False
    assert result["会话"]["master104"] == master.metadata
    assert master.steps[0]["序号"] == master.screenshots[0]["step_id"] == 1


def test_upper_only_keeps_existing_numbering_and_metadata():
    """既有单上位机会话保持原有报告契约。"""
    upper = session("upper", "旧状态")
    result = merge_session_evidence({"用例ID": "old"}, [("desktop", upper)])
    assert result["程序"] == "upper"
    assert result["用例ID"] == "old"
    assert result["步骤"] == upper.steps and result["截图"] == upper.screenshots
    assert "会话" not in result


def test_master_only_evidence_is_not_discarded():
    """仅申请主站fixture时仍保留其步骤截图，不虚构DSP桌面缺失。"""
    master = session("master104", "主站状态")
    item = SimpleNamespace(_bench_master104=master)
    sources = collect_sessions(item)
    assert sources == [("master104", master)]
    assert merge_session_evidence({}, sources)["截图"][0]["name"] == "master104.png"


def test_live_event_order_determines_merged_step_order(tmp_path):
    """报告按本例步骤开始事件排序，忽略其他用例事件及未写完的末行。"""
    import json
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(json.dumps(event) for event in [
        {"type": "step", "case_id": "other", "step_id": 1, "status": "running"},
        {"type": "step", "case_id": "case", "step_id": "master104:1", "status": "running"},
        {"type": "step", "case_id": "case", "step_id": 1, "status": "running"},
    ]) + '\n{"type":', encoding="utf-8")
    result = merge_session_evidence({}, [("desktop", session("upper", 1)), ("master104", session("master104", 2))],
                                    event_path=events, case_id="case")
    assert [step["序号"] for step in result["步骤"]] == ["master104:1", 1]


def test_source_metadata_keeps_existing_diagnostic_filter():
    """双端合并保留DSP原检查名，不能让技术检查重新进入业务摘要。"""
    from testbench.reporting import business_case
    upper, master = session("upper", True), session("master104", True)
    upper.steps[0].update({"步骤": "页面来源属于上位机", "预期": True})
    result = merge_session_evidence({"结果": "通过"}, [("desktop", upper), ("master104", master)])
    assert all("页面来源属于上位机" not in check["name"] for check in business_case(result)["checks"])


def test_native_initialization_is_diagnostic_and_explicit_business_image_has_priority():
    """初始化和自动主站截图留作诊断，不能占满显式业务画面的展示名额。"""
    from testbench.reporting import business_case
    master = session("master104", "已读取")
    master.steps[0].update({"步骤": "接管主站", "预期": "唯一实例", "诊断步骤": True})
    master.screenshots = [{"name": f"master104-step-{i}.png", "capture_status": "saved", "status": "passed",
                           "automatic": True, "step_id": 1} for i in range(4)]
    master.screenshots.append({"name": "master104-business.png", "capture_status": "saved", "status": "passed",
                               "automatic": False, "step_id": 1})
    result = merge_session_evidence({"结果": "通过"}, [("master104", master)])
    view = business_case(result)
    assert view["checks"] == []
    assert view["key_screenshots"] == ["master104-business.png"]
    assert len(result["截图"]) == 5
