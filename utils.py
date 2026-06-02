"""
工具函数：EXIF 读取/写入、图片缩放/拼接/裁切等。
"""
from __future__ import annotations

import logging
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from PIL import ImageOps

from enums.constant import TRANSPARENT

if platform.system() == 'Windows':
    EXIFTOOL_PATH = Path('./exiftool/exiftool.exe')
    ENCODING = 'gbk'
elif shutil.which('exiftool') is not None:
    EXIFTOOL_PATH = shutil.which('exiftool')
    ENCODING = 'utf-8'
else:
    EXIFTOOL_PATH = Path('./exiftool/exiftool')
    ENCODING = 'utf-8'

logger = logging.getLogger(__name__)

# ---------- ExifTool 批处理模式 ----------

_exiftool_process: Optional[subprocess.Popen] = None


def _start_exiftool() -> subprocess.Popen:
    """启动 ExifTool 的 -stay_open 批处理模式，复用单个进程处理所有图片。"""
    global _exiftool_process
    if _exiftool_process is not None and _exiftool_process.poll() is None:
        return _exiftool_process

    try:
        _exiftool_process = subprocess.Popen(
            [str(EXIFTOOL_PATH), '-stay_open', 'True', '-@', '-'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding='utf-8',
            errors='ignore',
        )
    except (FileNotFoundError, OSError) as e:
        logger.warning(f'无法启动 ExifTool 批处理模式: {e}，将回退到单次调用模式')
        _exiftool_process = None

    return _exiftool_process


def _stop_exiftool() -> None:
    """关闭 ExifTool 批处理进程。"""
    global _exiftool_process
    if _exiftool_process is not None and _exiftool_process.poll() is None:
        try:
            _exiftool_process.stdin.write('-stay_open\nFalse\n')
            _exiftool_process.stdin.flush()
            _exiftool_process.wait(timeout=5)
        except Exception:
            _exiftool_process.kill()
        _exiftool_process = None


def _get_exif_batch_mode(path) -> dict:
    """通过 -stay_open 批处理模式读取 EXIF，避免每张图片启动新进程。"""
    proc = _start_exiftool()
    if proc is None:
        return _get_exif_single_mode(path)

    try:
        # 发送命令
        commands = f'-d\n%Y-%m-%d %H:%M:%S%3f%z\n{path}\n-execute\n'
        proc.stdin.write(commands)
        proc.stdin.flush()

        # 读取输出直到遇到 {ready} 标记
        output_lines = []
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            if line.strip() == '{ready}':
                break
            output_lines.append(line)

        return _parse_exif_output(''.join(output_lines))
    except Exception as e:
        logger.error(f'ExifTool 批处理模式读取失败: {path} : {e}')
        return _get_exif_single_mode(path)


def _get_exif_single_mode(path) -> dict:
    """回退：单次调用 ExifTool 读取 EXIF。"""
    exif_dict = {}
    try:
        output_bytes = subprocess.check_output(
            [str(EXIFTOOL_PATH), '-d', '%Y-%m-%d %H:%M:%S%3f%z', str(path)]
        )
        output = output_bytes.decode('utf-8', errors='ignore')
        return _parse_exif_output(output)
    except Exception as e:
        logger.error(f'get_exif error: {path} : {e}')
    return exif_dict


def _parse_exif_output(output: str) -> dict:
    """解析 ExifTool 的文本输出为字典。"""
    exif_dict = {}
    for line in output.splitlines():
        kv_pair = line.split(':')
        if len(kv_pair) < 2:
            continue
        key = kv_pair[0].strip()
        value = ':'.join(kv_pair[1:]).strip()
        # 将键中的空格和斜杠移除
        key = re.sub(r'\s+', '', key)
        key = re.sub(r'/', '', key)
        # 过滤非 ASCII 字符
        value_clean = ''.join(c for c in value if ord(c) < 128)
        exif_dict[key] = value_clean
    return exif_dict


def get_exif(path) -> dict:
    """
    获取 EXIF 信息（优先使用批处理模式）。
    :param path: 照片路径
    :return: exif 信息字典
    """
    return _get_exif_batch_mode(path)


def stop_exiftool() -> None:
    """公开接口：关闭 ExifTool 进程，在程序退出时调用。"""
    _stop_exiftool()


def is_exiftool_available() -> bool:
    """检测 ExifTool 是否可用。"""
    try:
        result = subprocess.run(
            [str(EXIFTOOL_PATH), '-ver'],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def insert_exif(source_path, target_path) -> None:
    """
    复制照片的 exif 信息
    :param source_path: 源照片路径
    :param target_path: 目的照片路径
    """
    try:
        subprocess.check_output(
            [str(EXIFTOOL_PATH), '-tagsfromfile', str(source_path), '-overwrite_original', str(target_path)]
        )
    except ValueError as e:
        logger.exception(f'ValueError: {source_path}: cannot insert exif {str(e)}')


def strip_gps_in_file(target_path) -> bool:
    """
    使用 ExifTool 从已保存的图片文件中移除 GPS 定位信息，保留其余 EXIF。
    用于发布到公开平台时隐藏拍摄位置。
    :param target_path: 图片文件路径
    :return: 是否成功执行（ExifTool 不可用时返回 False）
    """
    try:
        subprocess.check_output(
            [str(EXIFTOOL_PATH), '-gps:all=', '-overwrite_original', str(target_path)],
            stderr=subprocess.STDOUT,
        )
        return True
    except (FileNotFoundError, OSError) as e:
        logger.warning(f'ExifTool 不可用，无法清除 GPS: {target_path} : {e}')
        return False
    except subprocess.CalledProcessError as e:
        logger.error(f'清除 GPS 失败: {target_path} : {e}')
        return False


# ---------- 文件工具 ----------

SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png'}


def get_file_list(path) -> list[Path]:
    """
    获取支持的图片文件列表
    :param path: 路径
    :return: 文件路径列表
    """
    path = Path(path)
    return [
        file_path for file_path in path.iterdir()
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]


# ---------- 图片工具 ----------


def concatenate_image(images: list[Image.Image], align: str = 'left') -> Image.Image:
    """
    将多张图片拼接成一列
    :param images: 图片对象列表
    :param align: 对齐方向，left/center/right
    :return: 拼接后的图片对象
    """
    widths, heights = zip(*(i.size for i in images))

    sum_height = sum(heights)
    max_width = max(widths)

    new_img = Image.new('RGBA', (max_width, sum_height), color=TRANSPARENT)

    y_offset = 0
    for img in images:
        if align == 'center':
            x_offset = int((max_width - img.width) / 2)
        elif align == 'right':
            x_offset = max_width - img.width
        else:
            x_offset = 0
        new_img.paste(img, (x_offset, y_offset))
        y_offset += img.height

    return new_img


def padding_image(image: Optional[Image.Image], padding_size: int,
                  padding_location: str = 'tb', color=TRANSPARENT) -> Optional[Image.Image]:
    """
    在图片四周填充像素
    :param image: 图片对象
    :param padding_size: 填充像素大小
    :param padding_location: 填充位置，t/b/l/r 的组合
    :param color: 填充颜色
    :return: 填充后的图片对象
    """
    if image is None:
        return None

    total_width, total_height = image.size
    x_offset, y_offset = 0, 0
    if 't' in padding_location:
        total_height += padding_size
        y_offset += padding_size
    if 'b' in padding_location:
        total_height += padding_size
    if 'l' in padding_location:
        total_width += padding_size
        x_offset += padding_size
    if 'r' in padding_location:
        total_width += padding_size

    padding_img = Image.new('RGBA', (total_width, total_height), color=color)
    padding_img.paste(image, (x_offset, y_offset))
    return padding_img


def square_image(image: Image.Image, auto_close: bool = True) -> Image.Image:
    """
    将图片按照正方形进行填充
    :param auto_close: 是否自动关闭图片对象
    :param image: 图片对象
    :return: 填充后的图片对象
    """
    width, height = image.size
    if width == height:
        return image

    delta_w = abs(width - height)
    padding = (delta_w // 2, 0) if width < height else (0, delta_w // 2)

    square_img = ImageOps.expand(image, padding, fill='white')

    if auto_close:
        image.close()

    return square_img


def resize_image_with_height(image: Image.Image, height: int, auto_close: bool = True) -> Image.Image:
    """
    按照高度对图片进行缩放
    :param image: 图片对象
    :param height: 指定高度
    :param auto_close: 是否自动关闭原图
    :return: 按照高度缩放后的图片对象
    """
    width, old_height = image.size
    scale = height / old_height
    new_width = round(width * scale)

    resized_image = image.resize((new_width, height), Image.LANCZOS)

    if auto_close:
        image.close()

    return resized_image


def resize_image_with_width(image: Image.Image, width: int, auto_close: bool = True) -> Image.Image:
    """
    按照宽度对图片进行缩放
    :param image: 图片对象
    :param width: 指定宽度
    :param auto_close: 是否自动关闭原图
    :return: 按照宽度缩放后的图片对象
    """
    old_width, height = image.size
    scale = width / old_width
    new_height = round(height * scale)

    resized_image = image.resize((width, new_height), Image.LANCZOS)

    if auto_close:
        image.close()

    return resized_image


def resize_image_to_canvas(image: Image.Image, width: int, height: int,
                           background_color: str = 'white', auto_close: bool = True) -> Image.Image:
    """
    将图片等比缩放到指定画布内，并补齐空白区域
    """
    if width <= 0 or height <= 0:
        return image

    resized_image = image.copy()
    resized_image.thumbnail((width, height), Image.LANCZOS)

    canvas_mode = 'RGBA' if resized_image.mode == 'RGBA' else 'RGB'
    canvas = Image.new(canvas_mode, (width, height), color=background_color)
    x_offset = int((width - resized_image.width) / 2)
    y_offset = int((height - resized_image.height) / 2)
    canvas.paste(resized_image, (x_offset, y_offset))

    resized_image.close()
    if auto_close:
        image.close()

    return canvas


def crop_image_to_canvas(image: Image.Image, width: int, height: int,
                         auto_close: bool = True) -> Image.Image:
    """
    将图片等比放大后居中裁切到指定尺寸
    """
    if width <= 0 or height <= 0:
        return image

    old_width, old_height = image.size
    scale = max(width / old_width, height / old_height)
    resized_width = round(old_width * scale)
    resized_height = round(old_height * scale)
    resized_image = image.resize((resized_width, resized_height), Image.LANCZOS)

    left = int((resized_width - width) / 2)
    top = int((resized_height - height) / 2)
    cropped_image = resized_image.crop((left, top, left + width, top + height))

    resized_image.close()
    if auto_close:
        image.close()

    return cropped_image


def stretch_image_to_canvas(image: Image.Image, width: int, height: int,
                            auto_close: bool = True) -> Image.Image:
    """
    将图片直接拉伸到指定尺寸
    """
    if width <= 0 or height <= 0:
        return image

    resized_image = image.resize((width, height), Image.LANCZOS)
    if auto_close:
        image.close()

    return resized_image


def resize_image_by_mode(image: Image.Image, width: int, height: int,
                         mode: str = 'padding', background_color: str = 'white',
                         auto_close: bool = True) -> Image.Image:
    """
    根据模式统一图片尺寸
    """
    if mode == 'crop':
        return crop_image_to_canvas(image, width, height, auto_close=auto_close)
    if mode == 'stretch':
        return stretch_image_to_canvas(image, width, height, auto_close=auto_close)

    resized_image = image.copy()
    resized_image.thumbnail((width, height), Image.LANCZOS)

    canvas_mode = 'RGBA' if resized_image.mode == 'RGBA' else 'RGB'
    canvas = Image.new(canvas_mode, (width, height), color=background_color)
    x_offset = int((width - resized_image.width) / 2)
    y_offset = int((height - resized_image.height) / 2)
    canvas.paste(resized_image, (x_offset, y_offset))

    resized_image.close()
    if auto_close:
        image.close()

    return canvas


def append_image_by_side(background: Image.Image, images: list[Image.Image],
                         side: str = 'left', padding: int = 200,
                         is_start: bool = False) -> None:
    """
    将图片横向拼接到背景图片中
    :param background: 背景图片对象
    :param images: 图片对象列表
    :param side: 拼接方向，left/right
    :param padding: 图片之间的间距
    :param is_start: 是否在最左侧添加 padding
    """
    if side == 'right':
        if is_start:
            x_offset = background.width - padding
        else:
            x_offset = background.width
        images.reverse()
        for i in images:
            if i is None:
                continue
            i = resize_image_with_height(i, background.height, auto_close=False)
            x_offset -= i.width
            x_offset -= padding
            background.paste(i, (x_offset, 0))
    else:
        if is_start:
            x_offset = padding
        else:
            x_offset = 0
        for i in images:
            if i is None:
                continue
            i = resize_image_with_height(i, background.height, auto_close=False)
            background.paste(i, (x_offset, 0))
            x_offset += i.width
            x_offset += padding


def text_to_image(content: str, font: ImageFont.FreeTypeFont,
                  bold_font: ImageFont.FreeTypeFont,
                  is_bold: bool = False, fill: str = 'black') -> Image.Image:
    """
    将文字内容转换为图片
    """
    if is_bold:
        font = bold_font
    if content == '':
        content = '   '
    _, _, text_width, text_height = font.getbbox(content)
    image = Image.new('RGBA', (text_width, text_height), color=TRANSPARENT)
    draw = ImageDraw.Draw(image)
    draw.text((0, 0), content, fill=fill, font=font)
    return image


def merge_images(images: list[Image.Image], axis: int = 0, align: int = 0) -> Image.Image:
    """
    拼接多张图片
    :param images: 图片对象列表
    :param axis: 0 水平拼接，1 垂直拼接
    :param align: 0 居中对齐，1 底部/右对齐，2 顶部/左对齐
    :return: 拼接后的图片对象
    """
    widths, heights = zip(*(img.size for img in images))

    if axis == 0:  # 水平拼接
        total_width = sum(widths)
        max_height = max(heights)
    else:  # 垂直拼接
        total_width = max(widths)
        max_height = sum(heights)

    output_image = Image.new('RGBA', (total_width, max_height), color=TRANSPARENT)

    x_offset, y_offset = 0, 0
    for img in images:
        if axis == 0:  # 水平拼接
            if align == 1:
                y_offset = max_height - img.size[1]
            elif align == 2:
                y_offset = 0
            else:
                y_offset = (max_height - img.size[1]) // 2
            output_image.paste(img, (x_offset, y_offset))
            x_offset += img.size[0]
        else:  # 垂直拼接
            if align == 1:
                x_offset = total_width - img.size[0]
            elif align == 2:
                x_offset = 0
            else:
                x_offset = (total_width - img.size[0]) // 2
            output_image.paste(img, (x_offset, y_offset))
            y_offset += img.size[1]

    return output_image


def calculate_pixel_count(width: int, height: int) -> str:
    """计算百万像素数"""
    pixel_count = width * height
    megapixel_count = pixel_count / 1000000.0
    return f"{megapixel_count:.2f} MP"


def extract_attribute(data_dict: dict, *keys, default_value: str = '', prefix: str = '',
                      suffix: str = '') -> str:
    """
    从字典中提取对应键的属性值
    :param data_dict: 包含属性值的字典
    :param keys: 一个或多个键
    :param default_value: 默认值
    :param prefix: 前缀
    :param suffix: 后缀
    :return: 对应的属性值或默认值
    """
    for key in keys:
        if key in data_dict:
            return data_dict[key] + suffix
    return default_value


def extract_gps_lat_and_long(lat: str, long: str) -> tuple[str, str]:
    """提取 GPS 经纬度信息"""
    lat_deg, _, lat_min = re.findall(r"(\d+ deg \d+)", lat)[0].split()
    long_deg, _, long_min = re.findall(r"(\d+ deg \d+)", long)[0].split()

    lat_dir = re.findall(r"([NS])", lat)[0]
    long_dir = re.findall(r"([EW])", long)[0]

    latitude = f"{lat_deg}°{lat_min}'{lat_dir}"
    longitude = f"{long_deg}°{long_min}'{long_dir}"

    return latitude, longitude


def extract_gps_info(gps_info: str) -> tuple[str, str]:
    """从 GPS 信息字符串中提取经纬度"""
    lat, long = gps_info.split(", ")
    return extract_gps_lat_and_long(lat, long)
