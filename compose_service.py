"""
图片合成：长图竖向拼接、前后对比图（左右 / 上下）。

纯函数：输入 PIL.Image 返回新的 PIL.Image，不修改、不关闭传入的图片对象，
方便在处理链、CLI 与 Web 前端复用（满足非功能性需求「一致性」「资源管理」）。

设计选择：
- 等宽 / 等高对齐时统一缩放到「最小」边长，只缩小不放大，避免放大造成模糊。
- 复用 utils 的 ``resize_image_with_width`` / ``resize_image_with_height`` 做等比缩放
  （均以 ``auto_close=False`` 调用，绝不关闭入参）；为精确控制间距、分隔线、标签与
  RGB 输出，画布直接构建而非依赖 ``merge_images``（后者产出 RGBA 且无间距/分隔线能力）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image
from PIL import ImageColor
from PIL import ImageDraw
from PIL import ImageFont

from utils import resize_image_with_height
from utils import resize_image_with_width


ROOT_DIR = Path(__file__).resolve().parent
# 标签使用加粗字体，与项目其他模块保持一致
BOLD_FONT = ROOT_DIR / "fonts" / "AlibabaPuHuiTi-2-85-Bold.otf"

# 对比图支持的布局
COMPARISON_LAYOUTS = ("lr", "tb")


def _load_font(font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    path = str(font_path) if font_path else str(BOLD_FONT)
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _safe_color(color, fallback: tuple) -> tuple:
    """把颜色解析为 RGB 三元组，失败时回退。"""
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        return (int(color[0]), int(color[1]), int(color[2]))
    try:
        rgb = ImageColor.getrgb(color)
    except (ValueError, AttributeError):
        return fallback
    return (rgb[0], rgb[1], rgb[2])


def _to_rgb_copy(image: Image.Image) -> Image.Image:
    """返回 image 的独立 RGB 副本，绝不返回入参本身。"""
    rgb = image.convert("RGB")
    if rgb is image:  # 理论上不会发生，convert 始终返回新对象，留作保险
        rgb = image.copy()
    return rgb


def _resize_rgb(image: Image.Image, by: str, value: int) -> Image.Image:
    """按宽 / 高等比缩放并转 RGB，返回新图，不关闭入参。"""
    if by == "height":
        resized = resize_image_with_height(image, value, auto_close=False)
    else:
        resized = resize_image_with_width(image, value, auto_close=False)
    rgb = resized.convert("RGB")
    if rgb is not resized:
        resized.close()
    return rgb


def stack_vertical(images: list[Image.Image], gap: int = 0,
                   bg_color: str = "white", align_width: bool = True) -> Image.Image:
    """等宽对齐后纵向拼接成长图（需求 7.1）。

    - ``align_width=True``：所有图缩放到统一宽度（取最小宽度，只缩小不放大），
      画布宽度即该统一宽度。
    - ``align_width=False``：保留各图原始宽度，画布宽度取最大宽度，每行水平居中。
    - 图片之间填充 ``gap`` 像素的 ``bg_color`` 背景。
    - 返回新的 RGB 图，不修改、不关闭任何入参（Property 1）。
    - 0 张抛 ``ValueError``；1 张返回该图的 RGB 副本。
    """
    if not images:
        raise ValueError("stack_vertical 需要至少 1 张图片用于拼接")

    gap = max(0, int(gap))
    bg = _safe_color(bg_color, (255, 255, 255))

    if len(images) == 1:
        return _to_rgb_copy(images[0])

    common_width = min(img.width for img in images) if align_width else None

    rows: list[Image.Image] = []  # 全部为本函数持有的 RGB 副本
    try:
        for img in images:
            if align_width and img.width != common_width:
                rows.append(_resize_rgb(img, "width", common_width))
            else:
                rows.append(_to_rgb_copy(img))

        canvas_width = max(row.width for row in rows)
        total_height = sum(row.height for row in rows) + gap * (len(rows) - 1)
        canvas = Image.new("RGB", (canvas_width, total_height), color=bg)

        y = 0
        for row in rows:
            x = (canvas_width - row.width) // 2
            canvas.paste(row, (x, y))
            y += row.height + gap
        return canvas
    finally:
        for row in rows:
            row.close()


def _draw_divider(canvas: Image.Image, layout: str, seam: int, gap: int,
                  color: tuple = (200, 200, 200)) -> None:
    """在中缝处画分隔线。seam 为第一张图的边界坐标。"""
    draw = ImageDraw.Draw(canvas)
    line_w = max(1, min(gap, 4)) if gap > 0 else 1
    if layout == "lr":
        x = seam + gap // 2
        draw.line([(x, 0), (x, canvas.height)], fill=color, width=line_w)
    else:
        y = seam + gap // 2
        draw.line([(0, y), (canvas.width, y)], fill=color, width=line_w)


def _draw_badge(canvas: Image.Image, text: str, center_x: int, top_y: int,
                font: ImageFont.FreeTypeFont,
                text_color: tuple = (255, 255, 255),
                bg_color: tuple = (0, 0, 0)) -> None:
    """在 (center_x 顶部对齐 top_y) 处画一个圆角标签。"""
    draw = ImageDraw.Draw(canvas, "RGBA")
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x = max(4, int(text_h * 0.6))
    pad_y = max(3, int(text_h * 0.4))
    box_w = text_w + pad_x * 2
    box_h = text_h + pad_y * 2

    left = int(center_x - box_w / 2)
    top = int(top_y)
    # 夹取，保证标签留在画面内
    left = max(0, min(left, max(0, canvas.width - box_w)))
    top = max(0, min(top, max(0, canvas.height - box_h)))

    radius = max(4, int(box_h * 0.3))
    draw.rounded_rectangle([left, top, left + box_w, top + box_h], radius=radius,
                           fill=(bg_color[0], bg_color[1], bg_color[2], 200))
    draw.text((left + pad_x - bbox[0], top + pad_y - bbox[1]), text, font=font,
              fill=(text_color[0], text_color[1], text_color[2], 255))


def _draw_labels(canvas: Image.Image, layout: str, a: Image.Image, b: Image.Image,
                 gap: int, labels) -> None:
    """在两张图上分别渲染标签（如 Before / After）。标签为叠加贴纸，不改变画布尺寸。"""
    text_a = str(labels[0]).strip() if len(labels) > 0 and labels[0] else ""
    text_b = str(labels[1]).strip() if len(labels) > 1 and labels[1] else ""

    font_size = max(16, int(min(canvas.size) * 0.05))
    font = _load_font(None, font_size)

    if layout == "lr":
        margin = max(4, int(canvas.height * 0.03))
        if text_a:
            _draw_badge(canvas, text_a, a.width // 2, margin, font)
        if text_b:
            _draw_badge(canvas, text_b, a.width + gap + b.width // 2, margin, font)
    else:  # tb
        margin = max(4, int(canvas.width * 0.03))
        if text_a:
            _draw_badge(canvas, text_a, canvas.width // 2, margin, font)
        if text_b:
            _draw_badge(canvas, text_b, canvas.width // 2,
                        a.height + gap + margin, font)


def make_comparison(img_a: Image.Image, img_b: Image.Image, layout: str = "lr",
                    gap: int = 8, divider: bool = True,
                    labels: Optional[tuple] = None) -> Image.Image:
    """生成前后对比图（需求 7.2、7.3）。

    - ``layout="lr"``：左右排布，两图缩放到统一高度（取最小高度，只缩小不放大）。
    - ``layout="tb"``：上下排布，两图缩放到统一宽度。
    - ``gap``：两图之间的间距（白底）；``divider=True`` 时在中缝画分隔线。
    - ``labels``：形如 ("Before", "After")，在两图上分别渲染标签贴纸。
    - 返回新的 RGB 图，不修改、不关闭任何入参（Property 1）。
    """
    if img_a is None or img_b is None:
        raise ValueError("make_comparison 需要两张有效图片")

    layout = layout if layout in COMPARISON_LAYOUTS else "lr"
    gap = max(0, int(gap))
    bg = (255, 255, 255)

    a = None
    b = None
    try:
        if layout == "lr":
            common_height = min(img_a.height, img_b.height)
            a = _resize_rgb(img_a, "height", common_height)
            b = _resize_rgb(img_b, "height", common_height)
            canvas = Image.new("RGB", (a.width + gap + b.width, common_height), bg)
            canvas.paste(a, (0, 0))
            canvas.paste(b, (a.width + gap, 0))
            if divider:
                _draw_divider(canvas, "lr", a.width, gap)
        else:  # tb
            common_width = min(img_a.width, img_b.width)
            a = _resize_rgb(img_a, "width", common_width)
            b = _resize_rgb(img_b, "width", common_width)
            canvas = Image.new("RGB", (common_width, a.height + gap + b.height), bg)
            canvas.paste(a, (0, 0))
            canvas.paste(b, (0, a.height + gap))
            if divider:
                _draw_divider(canvas, "tb", a.height, gap)

        if labels:
            _draw_labels(canvas, layout, a, b, gap, labels)
        return canvas
    finally:
        if a is not None:
            a.close()
        if b is not None:
            b.close()
