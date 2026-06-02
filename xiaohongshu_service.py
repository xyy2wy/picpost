"""
小红书多图工具：长图切割 / 九宫格、拼图封面、页码角标、推荐尺寸。

这些函数都是无副作用的纯函数，输入 PIL.Image，返回新的 PIL.Image（或列表），
不会修改或关闭传入的图片对象，方便在 CLI 和 Web 前端复用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

from utils import crop_image_to_canvas

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_BADGE_FONT = ROOT_DIR / "fonts" / "Roboto-Medium.ttf"

# 默认输出 JPEG 质量（小红书工具产物）。
DEFAULT_OUTPUT_QUALITY = 95


def save_jpg(image: Image.Image, target_path: Path, quality: int = DEFAULT_OUTPUT_QUALITY) -> Path:
    """
    统一的 JPEG 保存：转 RGB、关闭色度子采样以保住文字/边缘细节。
    CLI 与 Web 共用，避免重复的 convert('RGB').save(..., subsampling=0)。
    """
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    try:
        rgb.save(target_path, quality=max(1, min(int(quality), 100)), subsampling=0)
    finally:
        if rgb is not image:
            rgb.close()
    return target_path

# 小红书推荐尺寸：label -> (width, height)
XIAOHONGSHU_SIZES = {
    "竖图 3:4 (1080x1440)": (1080, 1440),
    "方图 1:1 (1080x1080)": (1080, 1080),
    "横图 4:3 (1440x1080)": (1440, 1080),
}

# 一键多比例导出可选的比例：label -> (width, height)
MULTI_RATIO_SIZES = {
    "竖图 3:4 (1080x1440)": (1080, 1440),
    "方图 1:1 (1080x1080)": (1080, 1080),
    "竖图 9:16 (1080x1920)": (1080, 1920),
    "横图 4:3 (1440x1080)": (1440, 1080),
}

# 切图模式：label -> mode
SPLIT_MODES = [
    ("竖向切条（长图）", "vertical"),
    ("横向切条（宽幅）", "horizontal"),
    ("九宫格 3x3", "grid"),
]

# 页码角标位置
BADGE_POSITIONS = [
    ("右下角", "bottom_right"),
    ("左下角", "bottom_left"),
    ("右上角", "top_right"),
    ("左上角", "top_left"),
]


# ---------- 切图 ----------

def split_vertical(image: Image.Image, count: int) -> list[Image.Image]:
    """从上到下等高切成 count 段（适合长图轮播）。"""
    count = max(1, int(count))
    width, height = image.size
    segment = height // count
    pieces: list[Image.Image] = []
    for index in range(count):
        top = index * segment
        bottom = height if index == count - 1 else (index + 1) * segment
        pieces.append(image.crop((0, top, width, bottom)))
    return pieces


def split_horizontal(image: Image.Image, count: int) -> list[Image.Image]:
    """从左到右等宽切成 count 段（适合宽幅全景轮播）。"""
    count = max(1, int(count))
    width, height = image.size
    segment = width // count
    pieces: list[Image.Image] = []
    for index in range(count):
        left = index * segment
        right = width if index == count - 1 else (index + 1) * segment
        pieces.append(image.crop((left, 0, right, height)))
    return pieces


def split_grid(image: Image.Image, rows: int, cols: int,
               square_first: bool = True) -> list[Image.Image]:
    """
    切成 rows x cols 网格，按行优先顺序返回（适合九宫格无缝大图）。
    square_first=True 时会先把图片按 cols:rows 的比例居中裁切，保证每格比例一致。
    """
    rows = max(1, int(rows))
    cols = max(1, int(cols))

    source = image
    if square_first:
        width, height = image.size
        target_ratio = cols / rows
        current_ratio = width / height
        if abs(current_ratio - target_ratio) > 1e-3:
            if current_ratio > target_ratio:
                new_width = int(round(height * target_ratio))
                new_height = height
            else:
                new_width = width
                new_height = int(round(width / target_ratio))
            source = crop_image_to_canvas(image, new_width, new_height, auto_close=False)

    width, height = source.size
    cell_w = width // cols
    cell_h = height // rows
    pieces: list[Image.Image] = []
    for r in range(rows):
        for c in range(cols):
            left = c * cell_w
            top = r * cell_h
            right = width if c == cols - 1 else (c + 1) * cell_w
            bottom = height if r == rows - 1 else (r + 1) * cell_h
            pieces.append(source.crop((left, top, right, bottom)))

    if source is not image:
        source.close()
    return pieces


def split_image(image: Image.Image, mode: str = "grid", count: int = 3,
                rows: int = 3, cols: int = 3) -> list[Image.Image]:
    """切图统一入口。"""
    if mode == "vertical":
        return split_vertical(image, count)
    if mode == "horizontal":
        return split_horizontal(image, count)
    return split_grid(image, rows, cols)


# ---------- 拼图封面 ----------

def make_collage(images: list[Image.Image], rows: int, cols: int,
                 output_size: tuple[int, int] = (1080, 1080),
                 gap: int = 16, bg_color: str = "white") -> Image.Image:
    """
    将多张图片拼成 rows x cols 网格封面图。
    多余的图片忽略，不足的格子留背景色。
    """
    rows = max(1, int(rows))
    cols = max(1, int(cols))
    total_w, total_h = output_size
    gap = max(0, int(gap))

    cell_w = (total_w - gap * (cols + 1)) // cols
    cell_h = (total_h - gap * (rows + 1)) // rows
    if cell_w <= 0 or cell_h <= 0:
        raise ValueError("拼图间距过大或输出尺寸过小，无法容纳所有格子。")

    canvas = Image.new("RGB", (total_w, total_h), bg_color)
    for index, image in enumerate(images[: rows * cols]):
        r = index // cols
        c = index % cols
        cell = crop_image_to_canvas(image, cell_w, cell_h, auto_close=False)
        if cell.mode != "RGB":
            converted = cell.convert("RGB")
            cell.close()
            cell = converted
        x = gap + c * (cell_w + gap)
        y = gap + r * (cell_h + gap)
        canvas.paste(cell, (x, y))
        cell.close()
    return canvas


# ---------- 页码角标 ----------

_font_cache: dict[str, ImageFont.FreeTypeFont] = {}


def _load_badge_font(font_path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    path = str(font_path) if font_path else str(DEFAULT_BADGE_FONT)
    cache_key = f"{path}:{size}"
    if cache_key not in _font_cache:
        try:
            _font_cache[cache_key] = ImageFont.truetype(path, size)
        except OSError:
            _font_cache[cache_key] = ImageFont.load_default()
    return _font_cache[cache_key]


def add_page_number_badge(image: Image.Image, index: int, total: int,
                          position: str = "bottom_right",
                          font_path: Optional[str] = None) -> Image.Image:
    """
    在图片角落添加 “index/total” 的半透明圆角页码角标。
    返回新的 RGB 图片，不修改原图。
    """
    base = image.convert("RGBA")
    width, height = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    text = f"{index}/{total}"
    font_size = max(20, int(min(width, height) * 0.045))
    font = _load_badge_font(font_path, font_size)

    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]

    pad_x = int(font_size * 0.65)
    pad_y = int(font_size * 0.4)
    box_w = text_w + pad_x * 2
    box_h = text_h + pad_y * 2
    margin = int(min(width, height) * 0.035)

    if position == "bottom_left":
        x, y = margin, height - box_h - margin
    elif position == "top_right":
        x, y = width - box_w - margin, margin
    elif position == "top_left":
        x, y = margin, margin
    else:  # bottom_right
        x, y = width - box_w - margin, height - box_h - margin

    radius = box_h // 2
    draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=radius, fill=(0, 0, 0, 140))
    draw.text(
        (x + pad_x - text_bbox[0], y + pad_y - text_bbox[1]),
        text, font=font, fill=(255, 255, 255, 235),
    )

    result = Image.alpha_composite(base, overlay).convert("RGB")
    base.close()
    overlay.close()
    return result


def add_page_numbers(images: list[Image.Image], position: str = "bottom_right",
                     font_path: Optional[str] = None) -> list[Image.Image]:
    """为一组图片批量添加 1/N、2/N ... 页码角标。"""
    total = len(images)
    return [
        add_page_number_badge(image, idx + 1, total, position=position, font_path=font_path)
        for idx, image in enumerate(images)
    ]


# ---------- 一键多比例导出 ----------

def export_multi_ratio(image: Image.Image, sizes: list[tuple[int, int]],
                       mode: str = "crop", background_color: str = "white") -> list[tuple[tuple[int, int], Image.Image]]:
    """
    把一张图导出为多个比例的版本。
    返回 [((w, h), Image), ...]，每个都是新图，不动原图。
    mode: crop（居中裁切，推荐）/ padding（留白补齐）/ stretch（拉伸）。
    """
    from utils import resize_image_by_mode

    results: list[tuple[tuple[int, int], Image.Image]] = []
    for width, height in sizes:
        out = resize_image_by_mode(image, width, height, mode=mode,
                                   background_color=background_color, auto_close=False)
        results.append(((width, height), out))
    return results


# ---------- 控制文件体积的保存 ----------

def save_jpg_under_size(image: Image.Image, target_path: Path,
                        max_kb: int, min_quality: int = 60,
                        max_quality: int = 95) -> tuple[Path, int, int]:
    """
    在不超过 max_kb 的前提下，二分查找尽可能高的 JPEG 质量保存。
    返回 (路径, 实际质量, 实际KB)。若最低质量仍超限，则用最低质量保存。
    """
    import io

    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    rgb = image if image.mode == "RGB" else image.convert("RGB")

    max_bytes = max(1, int(max_kb)) * 1024
    low, high = max(1, int(min_quality)), min(100, int(max_quality))

    best_bytes: Optional[bytes] = None
    best_quality = low

    def encode(quality: int) -> bytes:
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=quality, subsampling=0, optimize=True)
        return buffer.getvalue()

    # 先试最高质量，已达标就直接用
    high_data = encode(high)
    if len(high_data) <= max_bytes:
        best_bytes, best_quality = high_data, high
    else:
        # 二分逼近
        lo, hi = low, high
        while lo <= hi:
            mid = (lo + hi) // 2
            data = encode(mid)
            if len(data) <= max_bytes:
                best_bytes, best_quality = data, mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best_bytes is None:
            # 连最低质量都超限，仍用最低质量保存
            best_bytes, best_quality = encode(low), low

    target_path.write_bytes(best_bytes)
    actual_kb = len(best_bytes) // 1024
    if rgb is not image:
        rgb.close()
    return target_path, best_quality, actual_kb
