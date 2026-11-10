"""华为上位机点表操作契约，不创建浏览器或连接设备。"""
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from testbench.huawei_rtu_page import HuaweiRtuPage, normalize_point, parse_visible_value


@dataclass(frozen=True)
class Point:
    key: str = "current_a"
    name: str = "A相电流"
    address: int = 32072
    data_type: str = "INT32"
    reg_count: int = 2
    scale: str = "0.001"
    function: int = 3
    bit_index: int | None = None
    enum_value: int | None = None
    unit: str = "A"


def test_point_model_retains_signed_word_count_and_scale():
    """32位有符号电流必须保留双寄存器与千分之一倍率。"""
    spec = normalize_point(Point())
    assert (spec["data_type"], spec["reg_count"], spec["scale"]) == ("INT32", 2, "0.001")


@pytest.mark.parametrize("change", [
    {"reg_count": 1}, {"address": 65535}, {"function": 5},
    {"scale": "NaN"}, {"scale": "0"}, {"address": True},
])
def test_invalid_point_refused_before_any_ui_write(change):
    """越界、多字宽度错误与非有限倍率不能用于实际页面操作。"""
    value = vars(Point()).copy()
    value.update(change)
    with pytest.raises(ValueError):
        normalize_point(value)


def test_status_bool_cannot_mix_bit_and_enum_or_write_as_register():
    """状态字BOOL必须明确唯一解析方式，不允许隐式按bit0或混合解析。"""
    spec = dict(key="state", name="发电", address=32000, data_type="BOOL", reg_count=1, scale="1")
    assert normalize_point({**spec, "bit_index": 1})["bit_index"] == 1
    assert normalize_point({**spec, "enum_value": 0})["enum_value"] == 0
    for change in ({}, {"bit_index": 1, "enum_value": 2}, {"bit_index": 16}, {"bit_index": 1, "function": 6}):
        with pytest.raises(ValueError):
            normalize_point({**spec, **change})


@pytest.mark.parametrize("text,expected", [("true", True), ("false", False), ("-2.345", "-2.345")])
def test_only_explicit_bool_text_is_converted(text, expected):
    """BOOL false不能按非空字符串转换成true，普通数值保留页面原文。"""
    assert parse_visible_value(text) == expected


class RecordingPage(HuaweiRtuPage):
    def __init__(self):
        self.actions = []
        self.current = "owned"
        self.owned = {"owned": {"points": {}}}
        self.desktop = SimpleNamespace(metadata={})

    def _assert_identity(self):
        pass

    def _click(self, locator, name):
        self.actions.append(("click", name))

    def _fill(self, field, value, label):
        self.actions.append(("fill", field, str(value)))

    def _select_option(self, field, value):
        self.actions.append(("select", field, value))

    def _submit(self, label):
        self.actions.append(("submit", label))

    def _until(self, predicate, message="", timeout=None):
        return predicate(None)

    def _visible(self, locator):
        return True


def test_write_point_does_not_fill_hidden_deadband_and_preserves_signed_type():
    """写单寄存器Q/S使用INT16，不能填写写点页面不存在的死区。"""
    page = RecordingPage()
    page.add_huawei_point(dict(key="q_cmd", name="Q/S命令", address=40123, data_type="INT16", reg_count=1,
                               function=6, scale="0.001"), "test-q-cmd")
    assert ("select", "function", "0x06 写单寄存器") in page.actions
    assert ("select", "data_type", "INT16") in page.actions
    assert not any(action[:2] == ("fill", "deadband") for action in page.actions)


def test_status_bool_uses_explicit_bit_without_numeric_scale_fields():
    """发电位选择bit1且不填写BOOL页面隐藏的倍率和偏移字段。"""
    page = RecordingPage()
    page.add_huawei_point(dict(key="generating", name="发电", address=32000, data_type="BOOL", reg_count=1,
                               scale="1", bit_index=1), "test-generating")
    assert ("select", "bool_mode", "位索引") in page.actions
    assert ("fill", "bit_index", "1") in page.actions
    assert not any(action[0] == "fill" and action[1] in ("scale", "offset", "deadband") for action in page.actions)


def test_observation_missing_point_fails_instead_of_returning_partial_success():
    """批量读取缺少任一点时保持失败，且保存已读取的最后观察供诊断。"""
    page = RecordingPage()
    page._runtime_view = lambda: None
    page._read_visible_rows = lambda: {"tag-a": {"value": "1", "quality": "正常", "timestamp": "12:00:01"}}
    page.desktop.step = lambda *args, **kwargs: empty_step()
    with pytest.raises(AssertionError, match="tag-b"):
        page.observe_device({"a": "tag-a", "b": "tag-b"})
    assert page.desktop.metadata["华为上位机最后观察"]["owned"]["a"]["value"] == "1"


@contextmanager
def empty_step():
    yield
