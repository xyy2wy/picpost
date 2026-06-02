"""
调色 / 滤镜 / 图像增强 / 平铺水印：纯函数，输入 PIL.Image 返回新的 PIL.Image。

不修改、不关闭传入的图片对象，方便在处理链、CLI 与 Web 前端复用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image
from PIL import ImageColor
from PIL import ImageDraw
from PIL import ImageEnhance
from PIL import ImageFont
from PIL import ImageOps


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_WATERMARK_FONT = ROOT_DIR / "fonts" / "AlibabaPuHuiTi-2-45-Light.otf"


# ---------- 基础调整 ----------

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def adjust_brightness(image: Image.Image, factor: float) -> Image.Image:
    """factor=1.0 不变，>1 变亮，<1 变暗。"""
    return ImageEnhance.Brightness(image).enhance(_clamp(factor, 0.0, 3.0))


def adjust_contrast(image: Image.Image, factor: float) -> Image.Image:
    return ImageEnhance.Contrast(image).enhance(_clamp(factor, 0.0, 3.0))


def adjust_saturation(image: Image.Image, factor: float) -> Image.Image:
    return ImageEnhance.Color(image).enhance(_clamp(factor, 0.0, 3.0))


def adjust_sharpness(image: Image.Image, factor: float) -> Image.Image:
    return ImageEnhance.Sharpness(image).enhance(_clamp(factor, 0.0, 3.0))


def adjust_temperature(image: Image.Image, shift: int) -> Image.Image:
    """
    色温偏移：shift>0 偏暖（加红减蓝），shift<0 偏冷（减红加蓝）。
    shift 取值约 -100..100。
    """
    shift = int(_clamp(shift, -100, 100))
    if shift == 0:
        return image.copy()
    rgb = image.convert("RGB")
    r, g, b = rgb.split()
    if rgb is not image:
        rgb.close()
    delta = int(abs(shift) * 0.5)  # 最多 ±50 的通道增量
    if shift > 0:
        r = r.point(lambda v: min(255, v + delta))
        b = b.point(lambda v: max(0, v - delta))
    else:
        r = r.point(lambda v: max(0, v - delta))
        b = b.point(lambda v: min(255, v + delta))
    result = Image.merge("RGB", (r, g, b))
    return result


def apply_adjustments(image: Image.Image, *, brightness: float = 1.0,
                      contrast: float = 1.0, saturation: float = 1.0,
                      sharpness: float = 1.0, temperature: int = 0) -> Image.Image:
    """按顺序应用基础调整，返回新图（始终为新对象，不动原图）。"""
    result = image.convert("RGB")
    # convert 在 mode 已是 RGB 时返回原对象的副本？为保证不动原图，显式 copy
    if result is image:
        result = image.copy()

    steps = [
        (temperature != 0, lambda im: adjust_temperature(im, temperature)),
        (abs(brightness - 1.0) > 1e-3, lambda im: adjust_brightness(im, brightness)),
        (abs(contrast - 1.0) > 1e-3, lambda im: adjust_contrast(im, contrast)),
        (abs(saturation - 1.0) > 1e-3, lambda im: adjust_saturation(im, saturation)),
        (abs(sharpness - 1.0) > 1e-3, lambda im: adjust_sharpness(im, sharpness)),
    ]
    for enabled, fn in steps:
        if enabled:
            new_img = fn(result)
            if new_img is not result:
                result.close()
                result = new_img
    return result


# ---------- 滤镜预设 ----------

# 每个预设是一组 apply_adjustments 的参数；special 标记特殊处理（如黑白）。
FILTER_PRESETS: dict[str, dict] = {
    "none": {"label": "原图", "params": {}},
    "fresh": {"label": "清新", "params": {"brightness": 1.05, "contrast": 1.08,
                                          "saturation": 1.18, "temperature": -8}},
    "film": {"label": "胶片", "params": {"brightness": 1.02, "contrast": 1.12,
                                         "saturation": 0.92, "temperature": 12}},
    "warm": {"label": "暖调", "params": {"brightness": 1.03, "contrast": 1.05,
                                         "saturation": 1.1, "temperature": 25}},
    "cool": {"label": "冷调", "params": {"brightness": 1.0, "contrast": 1.05,
                                         "saturation": 1.05, "temperature": -25}},
    "ins": {"label": "Ins 风", "params": {"brightness": 1.04, "contrast": 1.15,
                                          "saturation": 1.12, "sharpness": 1.1, "temperature": 6}},
    "mono": {"label": "黑白", "params": {"contrast": 1.1}, "grayscale": True},
}

FILTER_OPTIONS = [(meta["label"], key) for key, meta in FILTER_PRESETS.items()]


def apply_filter(image: Image.Image, preset: str) -> Image.Image:
    """应用一个滤镜预设，返回新图。未知预设按原图处理。"""
    meta = FILTER_PRESETS.get(preset, FILTER_PRESETS["none"])
    params = meta.get("params", {})
    result = apply_adjustments(image, **params)
    if meta.get("grayscale"):
        gray = ImageOps.grayscale(result).convert("RGB")
        result.close()
        result = gray
    return result


# ---------- 自动增强 ----------

def auto_enhance(image: Image.Image, *, autocontrast: bool = True,
                 equalize: bool = False, cutoff: float = 1.0) -> Image.Image:
    """
    自动增强：自动对比度（按直方图裁剪两端）/ 直方图均衡。
    返回新图，不动原图。
    """
    result = image.convert("RGB")
    if result is image:
        result = image.copy()
    if autocontrast:
        new_img = ImageOps.autocontrast(result, cutoff=_clamp(cutoff, 0.0, 10.0))
        result.close()
        result = new_img
    if equalize:
        new_img = ImageOps.equalize(result)
        result.close()
        result = new_img
    return result


# ---------- 平铺 / 单点防盗水印 ----------

def _load_font(font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    path = str(font_path) if font_path else str(DEFAULT_WATERMARK_FONT)
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def add_text_watermark(image: Image.Image, text: str, *, tiled: bool = True,
                       opacity: float = 0.15, font_path: Optional[str] = None,
                       angle: int = 30, color: str = "#ffffff",
                       density: float = 1.0,
                       position: str = "bottom_right") -> Image.Image:
    """
    添加文字水印。tiled=True 时整图斜向平铺；否则在指定角落单点。
    opacity 0..1，density 控制平铺密度（越大越密）。返回新的 RGB 图。
    """
    text = (text or "").strip()
    if not text:
        return image.convert("RGB") if image.mode != "RGB" else image.copy()

    base = image.convert("RGBA")
    width, height = base.size
    alpha = int(_clamp(opacity, 0.0, 1.0) * 255)
    try:
        rgb = ImageColor.getrgb(color)
    except ValueError:
        rgb = (255, 255, 255)
    fill = (rgb[0], rgb[1], rgb[2], alpha)

    font_size = max(18, int(min(width, height) * 0.03))
    font = _load_font(font_path, font_size)

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))

    if tiled:
        # 在一个透明小贴片上写一次文字，再旋转、平铺
        tmp = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        tmp_draw = ImageDraw.Draw(tmp)
        bbox = tmp_draw.textbbox((0, 0), text, font=font)
        tmp.close()
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        tile = Image.new("RGBA", (text_w + 20, text_h + 20), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((10 - bbox[0], 10 - bbox[1]), text, font=font, fill=fill)
        tile = tile.rotate(angle, expand=True)

        density = _clamp(density, 0.3, 3.0)
        step_x = max(1, int(tile.width * 1.4 / density))
        step_y = max(1, int(tile.height * 2.2 / density))
        for y in range(0, height, step_y):
            for x in range(0, width, step_x):
                overlay.alpha_composite(tile, (x, y))
        tile.close()
    else:
        draw = ImageDraw.Draw(overlay)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        margin = int(min(width, height) * 0.03)
        if position == "bottom_left":
            x, y = margin, height - text_h - margin
        elif position == "top_right":
            x, y = width - text_w - margin, margin
        elif position == "top_left":
            x, y = margin, margin
        elif position == "center":
            x, y = (width - text_w) // 2, (height - text_h) // 2
        else:  # bottom_right
            x, y = width - text_w - margin, height - text_h - margin
        draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=fill)

    result = Image.alpha_composite(base, overlay).convert("RGB")
    base.close()
    overlay.close()
    return result
