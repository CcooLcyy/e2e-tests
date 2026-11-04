"""业务标注不混入诊断原因，编号优先避开被检查的控件。"""
from contextlib import contextmanager
from types import SimpleNamespace

from testbench.login_page import PlatformLoginPage
from testbench.screenshot_annotations import caption_lines, number_position


def test_unknown_capture_explains_that_case_result_is_authoritative():
    """步骤未结束的截图仅记录现场，不显示含义不清的裸未判定。"""
    lines = caption_lines("登录后工作台", "unknown", [])
    assert "现场记录，最终结果见用例报告" in lines[0]
    assert "未判定" not in "\n".join(lines)


def test_location_reason_is_retained_but_not_burned_into_business_image():
    """完整定位原因留在原记录，业务图只说明当前标记提供文字说明。"""
    reason = "当前目标位置不可用（WebDriverException），未画框"
    targets = [{"label": "检查账号信息", "rect": None, "reason": reason}]
    lines = caption_lines("工作台账号", "passed", targets)
    assert "仅提供说明" in "\n".join(lines)
    assert reason not in "\n".join(lines)
    assert "WebDriverException" not in "\n".join(lines)
    assert targets[0]["reason"] == reason


def test_failure_fallback_caption_does_not_expose_test_execution_phase():
    """兜底失败图不把执行阶段或异常类别烧进业务图像。"""
    assert "call" not in "".join(caption_lines("登录提示：call 阶段失败现场", "failed", []))
    assert "WebDriverException" not in "".join(caption_lines("WebDriverException: private", "failed", []))


def test_top_edge_logout_number_uses_outside_space():
    """退出按钮接近上边缘时用右侧空隙，编号不能盖住按钮文字。"""
    rect = {"x": 2448, "y": 14, "width": 82, "height": 30}
    x, y = number_position(rect, (20, 24), (2560, 1352))
    assert 0 <= x <= 2540 and 0 <= y <= 1328
    assert (x + 20 <= rect["x"] or x >= rect["x"] + rect["width"]
            or y + 24 <= rect["y"] or y >= rect["y"] + rect["height"])


def test_account_annotation_is_scoped_to_logout_account_group():
    """仅标注目标收紧，原有业务账号定位与读取断言保持原样。"""
    records = []

    @contextmanager
    def step(name, **kwargs):
        records.append((name, kwargs.get("targets", [])))
        yield

    def check(name, actual, expected, **kwargs):
        assert actual == expected
        records.append((name, kwargs.get("targets", [])))

    page = PlatformLoginPage(SimpleNamespace(step=step, check=check,
        wait=SimpleNamespace(until=lambda *args, **kwargs: SimpleNamespace(text="测试账号"))))
    assert page.workspace("测试账号") == "测试账号"
    assert page.BADGE == ("css selector", "header .ant-badge-status-text")
    assert page.BADGE_ANNOTATION[0] == "xpath"
    assert "退出" in page.BADGE_ANNOTATION[1] and "ant-space" in page.BADGE_ANNOTATION[1]
    for name in ("等待并读取工作台账号信息", "工作台账号信息非空", "重开前后工作台账号一致"):
        targets = next(targets for recorded_name, targets in records if recorded_name == name)
        assert targets[0]["locator"] == page.BADGE_ANNOTATION
