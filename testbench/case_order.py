"""发现和执行共用的分组及组内顺序清单。"""
import json
import logging
from pathlib import Path


LOGGER = logging.getLogger(__name__)


def order_case_ids(records, root):
    """已登记用例按清单排序，新用例追加所属组末尾，无清单时按首次发现归组。"""
    path = Path(root) / "tests" / "case-order.json"
    groups, members = {}, {}
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("groups"), list):
                raise ValueError("groups 必须为分组列表")
            for group in value["groups"]:
                if (not isinstance(group, dict) or not isinstance(group.get("name"), str)
                        or not group["name"].strip() or not isinstance(group.get("cases"), list)):
                    raise ValueError("每组需要非空名称和 cases 列表")
                name = group["name"].strip()
                if name in groups:
                    raise ValueError("重复分组名称")
                groups[name] = len(groups)
                for position, case_id in enumerate(group["cases"]):
                    if not isinstance(case_id, str) or not case_id.strip():
                        raise ValueError("用例标识必须为非空字符串")
                    if case_id in members:
                        raise ValueError("重复用例标识")
                    members[case_id] = (name, position)
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError(f"用例顺序清单无效：{error}") from None

    ranks, unseen = {}, 0
    for discovery_position, (case_id, group) in enumerate(records):
        if case_id in ranks:
            raise ValueError("用例顺序清单不能处理重复的实际用例标识")
        if case_id in members and members[case_id][0] != group:
            raise ValueError(f"用例顺序清单分组不一致：{case_id}")
        if group not in groups:
            groups[group] = len(groups)
        position = members[case_id][1] if case_id in members else float("inf")
        ranks[case_id] = (groups[group], position, discovery_position)
        if path.exists() and case_id not in members:
            unseen += 1
    if unseen:
        LOGGER.warning("发现 %s 项未登记用例，已追加到所属分组末尾，请补充 tests/case-order.json", unseen)
    return sorted(ranks, key=ranks.__getitem__)
