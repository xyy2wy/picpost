"""
文字贴纸 / 图上标注：在图片上叠加气泡、纯文字、价格标签等标注。

纯函数：输入 PIL.Image 返回新的 PIL.Image，不修改、不关闭传入的图片对象，
方便在处理链、CLI 与 Web 前端复用。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL import ImageColor
from PIL import ImageDraw
from PIL import ImageFont


ROOT_DIR = Path(__file__).resolve().parent.parent
# 普通字体（气泡 / 纯文字），与 color_service 水印保持一致
DEFAULT_FONT = ROOT_DIR / "fonts" / "AlibabaPuHuiTi-2-45-Light.otf"
# 加粗字体（价格标签等需要强调的样式）
BOLD_FONT = ROOT_DIR / "fonts" / "AlibabaPuHuiTi-2-85-Bold.otf"

# 支持的标注样式
ANNOTATION_STYLES = ("bubble", "plain", "price")


@dataclass
class Annotation:
    """单条标注。

    - text: 文字内容，空白文本会被跳过。
    - x, y: 相对坐标（0–1），表示标注左上锚点在图中的相对位置。
    - style: 样式，bubble（气泡）/ plain（纯文字描边）/ price（价格标签）。
    - text_color / bg_color: 文字与底色（十六进制或 PIL 可识别的颜色名）。
    - font_scale: 字号相对系数，最终字号 = font_scale * min(宽, 高)。
    """
    text: str
    x: float
    y: float
    style: str = "bubble"
    text_color: str = "#ffffff"
    bg_color: str = "#000000"
    font_scale: float = 0.04


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _load_font(font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    path = str(font_path) if font_path else str(DEFAULT_FONT)
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _parse_color(color: str, fallback: tuple) -> tuple:
    """解析颜色为 RGB 三元组，失败时回退。"""
    try:
        rgb = ImageColor.getrgb(color)
    except (ValueError, AttributeError):
        return fallback
    return (rgb[0], rgb[1], rgb[2])


def _draw_one(base: Image.Image, overlay: Image.Image, ann: Annotation,
              font_path: Optional[str]) -> None:
    """在 overlay（RGBA）上绘制单条标注。base 仅用于读取尺寸。"""
    width, height = base.size
    short_side = min(width, height)

    # 字号：相对系数 * 短边，带一个合理下限
    font_size = max(14, int(_clamp(ann.font_scale, 0.01, 0.5) * short_side))
    use_bold = ann.style == "price"
    font = _load_font(font_path if font_path else (str(BOLD_FONT) if use_bold else None),
                      font_size)

    text_rgb = _parse_color(ann.text_color, (255, 255, 255))
    bg_rgb = _parse_color(ann.bg_color, (0, 0, 0))

    draw = ImageDraw.Draw(overlay)
    bbox = draw.textbbox((0, 0), ann.text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # 内边距：纯文字不需要底框，气泡/价格留出内边距
    pad_x = int(font_size * 0.45)
    pad_y = int(font_size * 0.3)

    if ann.style == "plain":
        box_w = text_w
        box_h = text_h
    else:
        box_w = text_w + pad_x * 2
        box_h = text_h + pad_y * 2

    # 相对锚点 → 像素左上角，并夹取使标注尽量留在画面内
    anchor_x = int(_clamp(ann.x, 0.0, 1.0) * width)
    anchor_y = int(_clamp(ann.y, 0.0, 1.0) * height)
    left = max(0, min(anchor_x, max(0, width - box_w)))
    top = max(0, min(anchor_y, max(0, height - box_h)))

    if ann.style == "bubble":
        # 圆角半透明底框 + 文字
        radius = max(4, int(box_h * 0.35))
        bg_fill = (bg_rgb[0], bg_rgb[1], bg_rgb[2], 190)
        draw.rounded_rectangle(
            [left, top, left + box_w, top + box_h], radius=radius, fill=bg_fill)
        text_x = left + pad_x - bbox[0]
        text_y = top + pad_y - bbox[1]
        draw.text((text_x, text_y), ann.text, font=font,
                  fill=(text_rgb[0], text_rgb[1], text_rgb[2], 255))
    elif ann.style == "price":
        # 高亮实心圆角标签 + 加粗文字
        radius = max(4, int(box_h * 0.2))
        bg_fill = (bg_rgb[0], bg_rgb[1], bg_rgb[2], 255)
        draw.rounded_rectangle(
            [left, top, left + box_w, top + box_h], radius=radius, fill=bg_fill)
        text_x = left + pad_x - bbox[0]
        text_y = top + pad_y - bbox[1]
        draw.text((text_x, text_y), ann.text, font=font,
                  fill=(text_rgb[0], text_rgb[1], text_rgb[2], 255))
    else:  # plain：纯文字 + 描边，无底框
        stroke_w = max(1, int(font_size * 0.06))
        text_x = left - bbox[0]
        text_y = top - bbox[1]
        draw.text((text_x, text_y), ann.text, font=font,
                  fill=(text_rgb[0], text_rgb[1], text_rgb[2], 255),
                  stroke_width=stroke_w,
                  stroke_fill=(bg_rgb[0], bg_rgb[1], bg_rgb[2], 255))


def add_annotations(image: Image.Image, annotations: list[Annotation],
                    font_path: Optional[str] = None) -> Image.Image:
    """按顺序在图片上叠加全部标注，返回新的 RGB 图。

    - 不修改、不关闭传入的 image（Property 1）。
    - 空白文本的标注会被跳过（需求 6.6）。
    - 多条标注按列表顺序依次渲染（需求 6.4）。
    - 相对坐标越界时夹取，保证标注尽量留在画面内。
    """
    base = image.convert("RGBA")
    # convert 在 mode 已是 RGBA 时可能返回原对象的副本；确保独立的工作副本
    if base is image:
        base = image.copy()

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))

    for ann in annotations or []:
        if not ann.text or not ann.text.strip():
            continue
        _draw_one(base, overlay, ann, font_path)

    result = Image.alpha_composite(base, overlay).convert("RGB")
    base.close()
    overlay.close()
    return result
