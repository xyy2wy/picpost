"""
首图文字卡片（封面图）：在纯色或图片背景上叠加大标题 + 副标题。

适合做小红书轮播的第一张封面。纯函数，返回新的 PIL.Image。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image
from PIL import ImageColor
from PIL import ImageDraw
from PIL import ImageFont

from utils_pkg import crop_image_to_canvas

ROOT_DIR = Path(__file__).resolve().parent.parent
TITLE_FONT = ROOT_DIR / "fonts" / "AlibabaPuHuiTi-2-85-Bold.otf"
SUBTITLE_FONT = ROOT_DIR / "fonts" / "AlibabaPuHuiTi-2-45-Light.otf"

# 封面推荐尺寸
COVER_SIZES = {
    "竖图 3:4 (1080x1440)": (1080, 1440),
    "方图 1:1 (1080x1080)": (1080, 1080),
    "竖图 9:16 (1080x1920)": (1080, 1920),
}

# 文字位置
TITLE_POSITIONS = [
    ("居中", "center"),
    ("靠上", "top"),
    ("靠下", "bottom"),
]


def _load_font(font_path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(font_path), size)
    except OSError:
        return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int,
               draw: ImageDraw.ImageDraw) -> list[str]:
    """按像素宽度对（主要为中文的）文本做自动换行。"""
    text = text.strip()
    if not text:
        return []
    lines: list[str] = []
    # 优先按用户显式换行拆分
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            trial = current + char
            width = draw.textbbox((0, 0), trial, font=font)[2]
            if width > max_width and current:
                lines.append(current)
                current = char
            else:
                current = trial
        if current:
            lines.append(current)
    return lines


def _draw_text_block(draw: ImageDraw.ImageDraw, lines: list[str],
                     font: ImageFont.FreeTypeFont, center_x: int, start_y: int,
                     fill: str, line_spacing: float = 1.3) -> int:
    """逐行居中绘制，返回结束 y 坐标。"""
    y = start_y
    for line in lines:
        bbox = draw.textbbox((0, 0), line or " ", font=font)
        line_w = bbox[2] - bbox[0]
        line_h = bbox[3] - bbox[1]
        draw.text((center_x - line_w / 2 - bbox[0], y - bbox[1]), line, font=font, fill=fill)
        y += int(line_h * line_spacing)
    return y


def make_cover(title: str, subtitle: str = "", *,
               size: tuple[int, int] = (1080, 1440),
               background_image: Optional[Image.Image] = None,
               bg_color: str = "#1f1f1f",
               text_color: str = "#ffffff",
               subtitle_color: str = "#e0e0e0",
               position: str = "center",
               overlay_opacity: float = 0.35) -> Image.Image:
    """
    生成封面卡片。
    - 有 background_image 时按目标尺寸居中裁切并叠半透明遮罩，保证文字可读；
    - 否则用纯色背景。
    返回新的 RGB 图。
    """
    width, height = size

    if background_image is not None:
        canvas = crop_image_to_canvas(background_image, width, height, auto_close=False)
        if canvas.mode != "RGB":
            tmp = canvas.convert("RGB")
            canvas.close()
            canvas = tmp
        # 叠加暗色遮罩提升文字可读性
        opacity = max(0.0, min(1.0, overlay_opacity))
        if opacity > 0:
            overlay = Image.new("RGBA", canvas.size, (0, 0, 0, int(opacity * 255)))
            merged = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
            canvas.close()
            overlay.close()
            canvas = merged
    else:
        try:
            fill_rgb = ImageColor.getrgb(bg_color)
        except ValueError:
            fill_rgb = (31, 31, 31)
        canvas = Image.new("RGB", (width, height), fill_rgb)

    draw = ImageDraw.Draw(canvas)
    margin = int(width * 0.1)
    max_text_width = width - margin * 2

    title_font = _load_font(TITLE_FONT, max(36, int(width * 0.085)))
    subtitle_font = _load_font(SUBTITLE_FONT, max(24, int(width * 0.04)))

    title_lines = _wrap_text(title, title_font, max_text_width, draw)
    subtitle_lines = _wrap_text(subtitle, subtitle_font, max_text_width, draw)

    # 估算整块文字高度
    def block_height(lines, font, spacing):
        h = 0
        for line in lines:
            bbox = draw.textbbox((0, 0), line or " ", font=font)
            h += int((bbox[3] - bbox[1]) * spacing)
        return h

    title_h = block_height(title_lines, title_font, 1.3)
    gap = int(height * 0.03) if subtitle_lines else 0
    subtitle_h = block_height(subtitle_lines, subtitle_font, 1.4)
    total_h = title_h + gap + subtitle_h

    if position == "top":
        start_y = int(height * 0.14)
    elif position == "bottom":
        start_y = height - total_h - int(height * 0.14)
    else:  # center
        start_y = (height - total_h) // 2

    center_x = width // 2
    end_y = _draw_text_block(draw, title_lines, title_font, center_x, start_y,
                             text_color, line_spacing=1.3)
    if subtitle_lines:
        _draw_text_block(draw, subtitle_lines, subtitle_font, center_x, end_y + gap,
                         subtitle_color, line_spacing=1.4)

    return canvas
