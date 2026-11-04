"""统一截图标注：只读定位可见目标，在独立副本上绘制编号及说明。"""
from dataclasses import dataclass
import math
import os
from pathlib import Path


@dataclass(frozen=True)
class AnnotationStyle:
    """所有用例共用的显示样式，不由业务用例各自绘图。"""
    normal_color: tuple = (37, 99, 235)
    failure_color: tuple = (220, 38, 38)
    background: tuple = (245, 247, 251)
    text_color: tuple = (38, 53, 76)
    font_size: int = 20
    line_width: int = 3
    padding: int = 16


DEFAULT_STYLE = AnnotationStyle()

# 只读取几何信息，不读取控件内容、输入值或执行页面修改。
TARGET_SCRIPT = """
function visibleRect(el) {
  if (!(el instanceof Element)) return null;
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  if (r.width <= 0 || r.height <= 0 || s.visibility === 'hidden' || s.visibility === 'collapse'
      || s.display === 'none' || Number(s.opacity) === 0
      || (el.checkVisibility && !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}))) return null;
  let left = Math.max(0, r.left), top = Math.max(0, r.top);
  let right = Math.min(innerWidth, r.right), bottom = Math.min(innerHeight, r.bottom);
  for (let parent = el.parentElement; parent; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    if (Number(style.opacity) === 0) return null;
    // 任意形状裁剪无法用一个矩形准确表达，宁可只提供说明。
    if (style.clipPath !== 'none' || (style.clip && style.clip !== 'auto')) return null;
    const clipX = /^(hidden|clip|scroll|auto)$/.test(style.overflowX);
    const clipY = /^(hidden|clip|scroll|auto)$/.test(style.overflowY);
    const paint = /(^| )(paint|strict|content)( |$)/.test(style.contain);
    if (!clipX && !clipY && !paint) continue;
    const p = parent.getBoundingClientRect();
    const sx = parent.offsetWidth ? p.width / parent.offsetWidth : 1;
    const sy = parent.offsetHeight ? p.height / parent.offsetHeight : 1;
    const px = p.left + parent.clientLeft * sx, py = p.top + parent.clientTop * sy;
    if (clipX || paint) {left = Math.max(left, px); right = Math.min(right, px + parent.clientWidth * sx);}
    if (clipY || paint) {top = Math.max(top, py); bottom = Math.min(bottom, py + parent.clientHeight * sy);}
  }
  return right > left && bottom > top ? {x: left, y: top, width: right - left, height: bottom - top} : null;
}
return {viewport: {width: window.innerWidth, height: window.innerHeight},
  targets: arguments[0].map(target => {
    const [by, selector, label] = target;
    let elements;
    try {
      if (by === 'css selector') elements = Array.from(document.querySelectorAll(selector));
      else {
        const found = document.evaluate(selector, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        elements = Array.from({length: found.snapshotLength}, (_, i) => found.snapshotItem(i));
      }
      const visible = elements.map(el => ({el, rect: visibleRect(el)})).filter(item => item.rect);
      if (visible.length !== 1) return {label, rect: null,
        reason: visible.length ? '可见目标不唯一，未画框' : '当前画面未找到可见目标，未画框'};
      return {label, rect: visible[0].rect, element: visible[0].el, reason: ''};
    } catch (_) {return {label, rect: null, reason: '无法定位当前目标，未画框'};}
  })};
"""


def snapshot_targets(driver, targets):
    """在当前视口一次性读取目标位置，异常仅保留安全原因。"""
    records, valid, positions = [], [], []
    for target in targets:
        label = str(target.get("label", "标注目标"))[:160] if isinstance(target, dict) else "标注目标"
        record = {"label": label, "rect": None, "reason": "目标定位格式无效，未画框"}
        records.append(record)
        locator = target.get("locator") if isinstance(target, dict) else None
        if (isinstance(locator, (tuple, list)) and len(locator) == 2
                and locator[0] in ("css selector", "xpath") and isinstance(locator[1], str)):
            valid.append([*locator, label])
            positions.append(len(records) - 1)
    result = {"viewport": None, "targets": records}
    if not valid:
        return result
    try:
        measured = driver.execute_script(TARGET_SCRIPT, valid)
        if not isinstance(measured, dict) or len(measured["targets"]) != len(valid):
            raise ValueError("目标几何信息不完整")
        result["viewport"] = measured["viewport"]
        for position, target in zip(positions, measured["targets"]):
            records[position].update(rect=target.get("rect"), reason=target.get("reason", ""),
                                     identity=getattr(target.get("element"), "id", None))
    except Exception as error:
        for position in positions:
            records[position]["rect"] = None
            records[position]["reason"] = f"当前目标位置不可用（{type(error).__name__}），未画框"
    return result


def stable_snapshot(before, after):
    """截图前后几何信息必须一致，避免在切页或滚动后的画面沿用旧框。"""
    targets = []
    current = after.get("targets", [])
    for index, record in enumerate(before.get("targets", [])):
        record = dict(record)
        if (before.get("viewport") != after.get("viewport") or index >= len(current)
                or record.get("rect") != current[index].get("rect")
                or record.get("identity") != current[index].get("identity")):
            record.update(rect=None, reason="截图期间目标或视口发生变化，未画框")
        targets.append(record)
    return {"viewport": before.get("viewport"), "targets": targets}


def _font(size):
    """优先使用系统中文字体，缺失时明确报错，避免输出乱码说明。"""
    from PIL import ImageFont
    candidates = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc",
                  Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                  Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
                  Path("/System/Library/Fonts/PingFang.ttc")]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise RuntimeError("未找到中文字体，保留原始截图；请安装微软雅黑或 Noto Sans CJK")


def _pixel_rect(rect, viewport, size):
    """视口坐标转换为 PNG 像素并裁剪到当前可见区域。"""
    if not isinstance(rect, dict) or not isinstance(viewport, dict):
        return None
    values = [rect.get(key) for key in ("x", "y", "width", "height")]
    values += [viewport.get("width"), viewport.get("height")]
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        return None
    x, y, width, height, vw, vh = values
    if min(width, height, vw, vh) <= 0:
        return None
    pw, ph = size
    left = round(max(0, x) * pw / vw)
    top = round(max(0, y) * ph / vh)
    right = round(min(vw, x + width) * pw / vw)
    bottom = round(min(vh, y + height) * ph / vh)
    if right <= left or bottom <= top:
        return None
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _wrap(text, font, width):
    """按实际字体宽度换行，长中文说明和英文单词均不得溢出。"""
    lines, line = [], ""
    for char in str(text).replace("\r", "")[:2000]:
        if char == "\n":
            lines.append(line)
            line = ""
        elif line and font.getlength(line + char) > width:
            lines.append(line)
            line = char
        else:
            line += char
    if line or not lines:
        lines.append(line)
    return lines


def render_annotated(raw_path, output_path, description, status, snapshot, style=DEFAULT_STYLE):
    """原图只读，编号框绘制到副本，说明区追加在图下方而不遮挡内容。"""
    from PIL import Image, ImageDraw
    raw_path, output_path = Path(raw_path), Path(output_path)
    if raw_path.resolve() == output_path.resolve() or output_path.exists():
        raise ValueError("标注图不能覆盖原图或已有文件")
    font = _font(style.font_size)
    small_font = _font(max(12, style.font_size - 4))
    color = style.failure_color if status == "failed" else style.normal_color
    with Image.open(raw_path) as source:
        original = source.convert("RGB")
    annotations = []
    for target in snapshot.get("targets", []):
        rect = _pixel_rect(target.get("rect"), snapshot.get("viewport"), original.size)
        annotations.append({"label": target["label"], "rect": rect,
                            "reason": "" if rect else target.get("reason") or "当前目标无有效可见区域，未画框"})
    verdict = {"passed": "步骤通过", "failed": "步骤失败", "unknown": "未判定",
               "skipped": "已跳过", "cancelled": "已停止"}.get(status, "未判定")
    lines = [f"{description}（{verdict}）"]
    for index, target in enumerate(annotations, 1):
        lines.append(f"{index}. {target['label']}" + (f"；{target['reason']}" if not target["rect"] else ""))
    if not annotations:
        lines.append("本步骤仅提供文字说明。")
    width = max(original.width, 320)
    wrapped = [line for text in lines for line in _wrap(text, font, width - style.padding * 2)]
    line_height = style.font_size + 10
    footer_height = len(wrapped) * line_height + style.padding * 2
    result = Image.new("RGB", (width, original.height + footer_height), style.background)
    result.paste(original, (0, 0))
    draw = ImageDraw.Draw(result)
    for index, target in enumerate(annotations, 1):
        rect = target["rect"]
        if rect is None:
            continue
        x, y, w, h = (rect[key] for key in ("x", "y", "width", "height"))
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=color, width=min(style.line_width, w, h))
        number = str(index)
        label_width = math.ceil(small_font.getlength(number)) + 10
        label_height = style.font_size + 4
        # 编号尽量放框外；靠近上边界时放在框内，不绘制到截图之外。
        lx = max(0, min(x + w - label_width, original.width - label_width))
        ly = y - label_height if y >= label_height else y
        ly = max(0, min(ly, original.height - label_height))
        draw.rectangle((lx, ly, lx + label_width, ly + label_height - 1), fill=color)
        draw.text((lx + 5, ly + 1), number, font=small_font, fill="white")
    for index, line in enumerate(wrapped):
        draw.text((style.padding, original.height + style.padding + index * line_height),
                  line, font=font, fill=style.text_color)
    # 原子落盘，失败时不会留下被误认作完整标注图的半文件。
    temporary = output_path.with_suffix(".pending")
    try:
        result.save(temporary, format="PNG")
        temporary.replace(output_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return annotations
