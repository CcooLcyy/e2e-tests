"""分组及组内顺序的清单契约和真实执行回归。"""
import json
import os
from pathlib import Path
import sys
import time
import uuid

import pytest

from testbench.case_order import order_case_ids
from testbench.catalog import discover_cases
from testbench.controller import TaskController
from testbench.model import Environment


def policy(root, groups):
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/case-order.json").write_text(json.dumps({"groups": groups}), encoding="utf-8")


def test_without_policy_groups_stably_by_first_appearance(tmp_path):
    """无清单时仍先按首次发现的组归并，保留每组原相对顺序。"""
    assert order_case_ids([("a", "甲"), ("b", "乙"), ("c", "甲")], tmp_path) == ["a", "c", "b"]


def test_policy_controls_groups_members_and_new_items_append(tmp_path, caplog):
    """字母较早的新用例也追加所属组末尾，未知分组追加末尾并提示补登记。"""
    policy(tmp_path, [{"name": "乙", "cases": ["b2", "b1"]}, {"name": "甲", "cases": ["a1"]}])
    records = [("new_a", "甲"), ("b1", "乙"), ("c", "新组"), ("a1", "甲"), ("b2", "乙"), ("new_b", "乙")]
    assert order_case_ids(records, tmp_path) == ["b2", "b1", "new_b", "a1", "new_a", "c"]
    assert "未登记" in caplog.text
    assert records[0] == ("new_a", "甲")


def test_partial_selection_keeps_policy_order_and_missing_items_are_ignored(tmp_path):
    """选择子集或已有用例删除时仍按已登记的相对顺序执行。"""
    policy(tmp_path, [{"name": "甲", "cases": ["removed", "a", "c"]}, {"name": "乙", "cases": ["b"]}])
    assert order_case_ids([("b", "乙"), ("c", "甲")], tmp_path) == ["c", "b"]


@pytest.mark.parametrize("value", [
    [], {}, {"groups": None}, {"groups": ["甲"]}, {"groups": [{"name": "", "cases": []}]},
    {"groups": [{"name": "甲", "cases": "a"}]},
    {"groups": [{"name": "甲", "cases": [12]}]},
    {"groups": [{"name": "甲", "cases": [""]}]},
    {"groups": [{"name": "甲", "cases": ["a", "a"]}]},
    {"groups": [{"name": "甲", "cases": []}, {"name": "甲", "cases": []}]},
    {"groups": [{"name": "甲", "cases": ["a"]}, {"name": "乙", "cases": ["a"]}]},
])
def test_invalid_policy_is_explicit_error(tmp_path, value):
    """非法结构、类型和重复登记必须报错，不能悄悄回退到其他顺序。"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/case-order.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="顺序清单"):
        order_case_ids([("a", "甲")], tmp_path)


def test_corrupt_policy_is_explicit_error(tmp_path):
    """损坏的 JSON 顺序清单必须阻止执行。"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/case-order.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="顺序清单"):
        order_case_ids([], tmp_path)


def test_declared_group_must_match_actual_marker(tmp_path):
    """登记到其他分组的用例应明确报错，避免显示顺序和执行归属不一致。"""
    policy(tmp_path, [{"name": "甲", "cases": ["a"]}])
    with pytest.raises(ValueError, match="分组不一致"):
        order_case_ids([("a", "乙")], tmp_path)


def test_earlier_filename_and_parameterized_cases_append_after_registered_members(tmp_path):
    """真实收集时新文件排序更早仍排组尾，参数化实例按清单中的顺序保留。"""
    policy(tmp_path, [{"name": "甲", "cases": ["tests/test_z.py::test_old[2]", "tests/test_z.py::test_old[1]"]}])
    (tmp_path / "tests/test_a.py").write_text('import pytest\npytestmark=pytest.mark.case_group("甲")\ndef test_new(): pass\n', encoding="utf-8")
    (tmp_path / "tests/test_z.py").write_text('import pytest\npytestmark=pytest.mark.case_group("甲")\n@pytest.mark.parametrize("number",[1,2])\ndef test_old(number): pass\n', encoding="utf-8")
    assert [case.id for case in discover_cases(tmp_path)] == [
        "tests/test_z.py::test_old[2]", "tests/test_z.py::test_old[1]", "tests/test_a.py::test_new",
    ]


def test_reversed_request_task_record_and_real_worker_follow_same_group_order():
    """真实独立工作进程按清单执行，逆序及重复请求不能打乱任务记录和清理末项。"""
    root = Path(__file__).resolve().parents[1] / "artifacts" / ("case-order-" + uuid.uuid4().hex)
    root.mkdir(parents=True)
    suite = root / "case-suite"
    suite.mkdir()
    (suite / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    (suite / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    ids = {name: "tests/test_flow.py::test_" + name for name in ("a1", "a2", "b")}
    policy(suite, [{"name": "乙", "cases": [ids["b"]]}, {"name": "甲", "cases": [ids["a2"], ids["a1"]]}])
    (suite / "case_data.py").write_text("OBSERVED = 'observed.txt'\n", encoding="utf-8")
    source = 'import pytest\nfrom pathlib import Path\nfrom case_data import OBSERVED\n'
    for name, group in (("a1", "甲"), ("b", "乙"), ("a2", "甲")):
        source += f'@pytest.mark.case_group({group!r})\ndef test_{name}():\n    with Path(OBSERVED).open("a",encoding="utf-8") as stream: stream.write({name!r}+"\\n")\n'
    (suite / "tests/test_flow.py").write_text(source, encoding="utf-8")
    controller = TaskController(root)
    environment = Environment(app_path=sys.executable, driver_path=sys.executable, timeout=5)
    run = controller.start(environment, [ids["a1"], ids["b"], ids["a2"], ids["a1"]])
    expected = [ids["b"], ids["a2"], ids["a1"]]
    events = []
    try:
        task = json.loads((run / "task.json").read_text(encoding="utf-8"))
        assert task["case_ids"] == expected
        assert task["root"] == str(root) and task["case_root"] == str(suite)
        assert task["case_pack"]["version"] == "1.2.3"
        assert controller._selected == expected
        deadline = time.monotonic() + 30
        while controller.active and time.monotonic() < deadline:
            events.extend(controller.poll())
            time.sleep(0.1)
        assert not controller.active
        assert (suite / "observed.txt").read_text(encoding="utf-8").splitlines() == ["b", "a2", "a1"]
        assert [event["case_id"] for event in events if event["type"] == "case_finished"] == expected
        summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
        assert summary["status"] == "passed" and summary["case_pack"] == task["case_pack"]
        assert "用例包版本 1.2.3" in (run / "report.md").read_text(encoding="utf-8")
    finally:
        if controller.active:
            controller.stop()


def test_stale_request_is_rejected_before_launch(tmp_path):
    """过期标识不能进入任务清单或创建工作进程。"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_one.py").write_text("def test_one(): pass\n", encoding="utf-8")
    controller = TaskController(tmp_path)
    with pytest.raises(ValueError, match="已经变化"):
        controller.start(Environment(app_path=sys.executable, driver_path=sys.executable), ["tests/test_missing.py::test_missing"])
    assert controller.run_dir is None
