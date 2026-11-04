"""验证图内标注、原图保护与截图时的坐标边界，不访问被测应用。"""
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

from desktop_demo import ReleaseDesktop
from testbench.screenshot_annotations import DEFAULT_STYLE, render_annotated, snapshot_targets, stable_snapshot


TARGETS = [{"locator": ("css selector", "#account"), "label": "填写账号"}]


def geometry(rect=None, width=200, height=120):
    return {"viewport": {"width": width, "height": height}, "targets": [
        {"label": "填写账号", "rect": rect or {"x": 20, "y": 30, "width": 100, "height": 25}, "reason": ""}]}


class ScreenshotDriver:
    def __init__(self, snapshots=None):
        self.snapshots = iter(snapshots or [geometry(), geometry()])
        self.scripts = []

    def execute_script(self, script, targets):
        self.scripts.append((script, targets))
        return next(self.snapshots)

    def save_screenshot(self, path):
        Image.new("RGB", (400, 240), "white").save(path)
        return True


def desktop(tmp_path, **kwargs):
    app = ReleaseDesktop(Path(sys.executable), Path(sys.executable), tmp_path, 1, **kwargs)
    app.driver = ScreenshotDriver()
    return app


def test_render_maps_viewport_pixels_and_preserves_original(tmp_path):
    """视口缩放为两倍时正确框选，中文说明在附加区，原图字节不变。"""
    raw, marked = tmp_path / "raw.png", tmp_path / "raw.annotated.png"
    Image.new("RGB", (400, 240), "white").save(raw)
    original = hashlib.sha256(raw.read_bytes()).digest()
    annotations = render_annotated(raw, marked, "步骤 1 完成：输入账号", "passed", geometry())
    assert hashlib.sha256(raw.read_bytes()).digest() == original
    with Image.open(marked) as image:
        assert image.width >= 400 and image.height > 240
        assert image.getpixel((40, 60)) == tuple(DEFAULT_STYLE.normal_color)
        assert image.getpixel((100, 100)) == (255, 255, 255)
        # 中文说明区确实绘制了文字，不是只有空白背景。
        assert len(set(image.crop((0, 240, image.width, image.height)).get_flattened_data())) > 3
    assert annotations[0]["rect"] == {"x": 40, "y": 60, "width": 200, "height": 50}


def test_failed_step_uses_red_outline_and_does_not_show_actual_values(tmp_path):
    """失败标记用红框；标注渲染接口不读取实际输入值。"""
    app = desktop(tmp_path)
    with pytest.raises(AssertionError, match="原始断言"):
        with app.step("验证账号", targets=TARGETS):
            raise AssertionError("原始断言")
    shot = app.screenshots[0]
    assert shot["status"] == "failed" and shot["annotation_status"] == "saved"
    assert shot["capture_status"] == "saved"
    with Image.open(tmp_path / shot["annotated_name"]) as image:
        assert image.getpixel((40, 60)) == tuple(DEFAULT_STYLE.failure_color)


def test_global_switch_disables_rendering_and_target_lookup(tmp_path):
    """统一开关关闭时只有原图，不查询目标，不生成副本。"""
    app = desktop(tmp_path, annotation_enabled=False)
    app.check("账号存在", True, True, targets=TARGETS)
    shot = app.screenshots[0]
    assert shot["annotation_status"] == "disabled" and not shot["annotated_name"]
    assert not app.driver.scripts
    assert [path.name for path in tmp_path.glob("*.png")] == [shot["name"]]


def test_no_target_still_has_caption_and_capture_names_never_overwrite(tmp_path):
    """无定位步骤仍带说明，同名主动截图的原图和副本均不覆盖。"""
    app = desktop(tmp_path)
    app.capture("现场", description="等待登录结果")
    first = {p.name: p.read_bytes() for p in tmp_path.glob("*.png")}
    app.capture("现场", description="等待登录结果")
    assert len(list(tmp_path.glob("*.png"))) == 4
    assert all((tmp_path / name).read_bytes() == data for name, data in first.items())
    assert all(shot["annotation_status"] == "saved" for shot in app.screenshots)
    assert not app.driver.scripts


def test_annotation_failure_preserves_saved_original_and_business_result(tmp_path, monkeypatch):
    """渲染异常只记标注失败，已保存的原图和业务通过状态不变。"""
    import testbench.screenshot_annotations as annotations
    def broken(*args, **kwargs):
        raise OSError("字体不可用")
    monkeypatch.setattr(annotations, "render_annotated", broken)
    app = desktop(tmp_path)
    app.check("登录页可见", True, True)
    shot = app.screenshots[0]
    assert shot["capture_status"] == "saved" and shot["status"] == "passed"
    assert shot["annotation_status"] == "failed" and shot["annotation_error"]
    assert not shot["annotated_name"] and app.failure is None
    assert (tmp_path / shot["name"]).is_file()


def test_failed_screenshot_remains_missing_and_no_annotation_is_created(tmp_path):
    """不能截图时不生成替代图，原始业务错误优先保留。"""
    app = desktop(tmp_path)
    app.driver.save_screenshot = lambda path: False
    with pytest.raises(AssertionError, match="业务原错"):
        with app.step("检查反馈", targets=TARGETS):
            raise AssertionError("业务原错")
    assert app.screenshots[0]["capture_status"] == "missing"
    assert not list(tmp_path.glob("*.annotated.png"))
    assert "业务原错" in app.failure


def test_nested_steps_keep_their_own_annotation_targets(tmp_path):
    """嵌套步骤的目标与截图按各自编号关联，不串用内层目标。"""
    app = desktop(tmp_path)
    with app.step("父步骤"):
        app.check("账号检查", True, True, targets=TARGETS)
    assert [shot["step_id"] for shot in app.screenshots] == [2, 1]
    assert len(app.screenshots[0]["annotations"]) == 1
    assert app.screenshots[1]["annotations"] == []


@pytest.mark.parametrize("after", [geometry(width=300), geometry({"x": 25, "y": 30, "width": 100, "height": 25}),
                                      {"viewport": {"width": 200, "height": 120}, "targets": [{"label": "填写账号", "rect": None, "reason": "目标不可见"}]}])
def test_changed_page_or_window_never_reuses_previous_rectangle(after):
    """截图前后视口变化、目标移动或消失均放弃旧框。"""
    result = stable_snapshot(geometry(), after)
    assert result["targets"][0]["rect"] is None
    assert result["targets"][0]["reason"]


def test_replaced_element_at_same_position_is_not_assumed_to_be_original_target():
    """新页面在相同位置出现新控件时，不能只凭相同矩形沿用标记。"""
    before, after = geometry(), geometry()
    before["targets"][0]["identity"] = "original-element"
    after["targets"][0]["identity"] = "new-element"
    assert stable_snapshot(before, after)["targets"][0]["rect"] is None


def test_clipped_target_only_draws_visible_intersection(tmp_path):
    """部分越界控件只标当前可见区域，不画到说明区或截图外。"""
    raw, marked = tmp_path / "raw.png", tmp_path / "marked.png"
    Image.new("RGB", (400, 240), "white").save(raw)
    result = render_annotated(raw, marked, "检查输入框", "passed",
                              geometry({"x": -10, "y": 100, "width": 80, "height": 40}))
    assert result[0]["rect"] == {"x": 0, "y": 200, "width": 140, "height": 40}


def test_geometry_failure_keeps_safe_explanation_without_driver_payload():
    """定位异常不泄漏驱动载荷或输入值，说明里保留无法标记原因。"""
    class Driver:
        def execute_script(self, *args):
            raise RuntimeError("private-password")
    result = snapshot_targets(Driver(), TARGETS)
    assert result["targets"][0]["rect"] is None
    assert result["targets"][0]["reason"]
    assert "private-password" not in json.dumps(result)


def test_invalid_locator_is_not_sent_to_browser():
    """不支持的目标定位记录原因，不执行任意脚本或猜测位置。"""
    driver = ScreenshotDriver()
    result = snapshot_targets(driver, [{"locator": ("script", "secret"), "label": "检查提示"}])
    assert not driver.scripts
    assert result["targets"][0]["rect"] is None
    assert result["targets"][0]["reason"]
