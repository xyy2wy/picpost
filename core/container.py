"""
ImageContainer：封装单张图片 + EXIF 数据，支持上下文管理器。
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.Image import Transpose
from dateutil import parser

from core.config import ElementConfig
from core.constants import (
    CAMERA_MAKE_CAMERA_MODEL_VALUE,
    CAMERA_MODEL_LENS_MODEL_VALUE,
    CUSTOM_VALUE,
    DATE_FILENAME_VALUE,
    DATE_VALUE,
    DATETIME_FILENAME_VALUE,
    DATETIME_VALUE,
    DEFAULT_VALUE,
    FILENAME_VALUE,
    GEO_INFO_VALUE,
    LENS_MAKE_LENS_MODEL_VALUE,
    LENS_VALUE,
    MAKE_VALUE,
    MODEL_VALUE,
    PARAM_VALUE,
    TOTAL_PIXEL_VALUE,
)
from utils_pkg import calculate_pixel_count
from utils_pkg import extract_attribute
from utils_pkg import extract_gps_info
from utils_pkg import extract_gps_lat_and_long
from utils_pkg import get_exif

logger = logging.getLogger(__name__)


class ExifId(Enum):
    CAMERA_MODEL = 'CameraModelName'
    CAMERA_MAKE = 'Make'
    LENS_MODEL = ['LensModel', 'Lens', 'LensID']
    LENS_MAKE = 'LensMake'
    DATETIME = 'DateTimeOriginal'
    FOCAL_LENGTH = 'FocalLength'
    FOCAL_LENGTH_IN_35MM_FILM = 'FocalLengthIn35mmFormat'
    F_NUMBER = 'FNumber'
    ISO = 'ISO'
    EXPOSURE_TIME = 'ExposureTime'
    SHUTTER_SPEED_VALUE = 'ShutterSpeedValue'
    ORIENTATION = 'Orientation'


PATTERN = re.compile(r"(\d+)\.")  # 匹配小数


def get_datetime(exif: dict) -> datetime:
    """解析 EXIF 中的拍摄时间。"""
    dt = datetime.now()
    try:
        dt = parser.parse(extract_attribute(exif, ExifId.DATETIME.value,
                                            default_value=str(datetime.now())))
    except ValueError:
        logger.info(f'Error: 时间格式错误：{extract_attribute(exif, ExifId.DATETIME.value)}')
    return dt


def get_focal_length(exif: dict) -> tuple[str, str]:
    """解析 EXIF 中的焦距信息。"""
    focal_length = DEFAULT_VALUE
    focal_length_in_35mm_film = DEFAULT_VALUE

    try:
        focal_lengths = PATTERN.findall(extract_attribute(exif, ExifId.FOCAL_LENGTH.value))
        try:
            focal_length = focal_lengths[0] if focal_lengths else DEFAULT_VALUE
        except IndexError:
            logger.info(f'ValueError: 不存在焦距：{focal_lengths}')
        try:
            focal_length_in_35mm_film = focal_lengths[1] if len(focal_lengths) > 1 else DEFAULT_VALUE
        except IndexError:
            logger.info(f'ValueError: 不存在 35mm 焦距：{focal_lengths}')
    except Exception as e:
        logger.info(f'KeyError: 焦距转换错误：{extract_attribute(exif, ExifId.FOCAL_LENGTH.value)} : {e}')

    return focal_length, focal_length_in_35mm_film


# 方向映射表
_ORIENTATION_TRANSPOSE_MAP = {
    "Rotate 90 CW": Transpose.ROTATE_270,
    "Rotate 180": Transpose.ROTATE_180,
    "Rotate 270 CW": Transpose.ROTATE_90,
}

_ORIENTATION_SAVE_MAP = {
    "Rotate 90 CW": Transpose.ROTATE_90,
    "Rotate 180": Transpose.ROTATE_180,
    "Rotate 270 CW": Transpose.ROTATE_270,
}


class ImageContainer:
    """封装单张图片及其 EXIF 元数据，支持 with 语句自动释放资源。"""

    def __init__(self, path: Path):
        self.path: Path = path
        self.target_path: Optional[Path] = None
        self.img: Image.Image = Image.open(path)
        self.exif: dict = get_exif(path)

        # 图像信息
        self.original_width: int = self.img.width
        self.original_height: int = self.img.height

        self._param_dict: dict[str, str] = {}

        self.model: str = extract_attribute(self.exif, ExifId.CAMERA_MODEL.value)
        self.make: str = extract_attribute(self.exif, ExifId.CAMERA_MAKE.value)
        self.lens_model: str = extract_attribute(self.exif, *ExifId.LENS_MODEL.value)
        self.lens_make: str = extract_attribute(self.exif, ExifId.LENS_MAKE.value)
        self.date: datetime = get_datetime(self.exif)
        self.focal_length, self.focal_length_in_35mm_film = get_focal_length(self.exif)
        self.f_number: str = extract_attribute(self.exif, ExifId.F_NUMBER.value, default_value=DEFAULT_VALUE)
        self.exposure_time: str = extract_attribute(
            self.exif, ExifId.EXPOSURE_TIME.value, default_value=DEFAULT_VALUE, suffix='s'
        )
        self.iso: str = extract_attribute(self.exif, ExifId.ISO.value, default_value=DEFAULT_VALUE)

        # 是否使用等效焦距
        self.use_equivalent_focal_length: bool = False

        # 修正图像方向
        self.orientation: str = self.exif.get(ExifId.ORIENTATION.value, 'Rotate 0')
        transpose_method = _ORIENTATION_TRANSPOSE_MAP.get(self.orientation)
        if transpose_method is not None:
            self.img = self.img.transpose(transpose_method)

        # 水印设置
        self.custom: str = '无'
        self.logo: Optional[Image.Image] = None

        # 当前处理链使用的背景色（运行态，逐张图独立，避免并发共享 config 造成竞态）
        self.bg_color: str = 'white'

        # 水印图片
        self.watermark_img: Optional[Image.Image] = None

        # 构建参数字典
        self._build_param_dict()

    def _build_param_dict(self) -> None:
        """构建参数字典，用于水印文字渲染。"""
        self._param_dict[MODEL_VALUE] = self.model
        self._param_dict[PARAM_VALUE] = self.get_param_str()
        self._param_dict[MAKE_VALUE] = self.make
        self._param_dict[DATETIME_VALUE] = self._parse_datetime()
        self._param_dict[DATE_VALUE] = self._parse_date()
        self._param_dict[LENS_VALUE] = self.lens_model

        filename_without_ext = os.path.splitext(self.path.name)[0]
        self._param_dict[FILENAME_VALUE] = filename_without_ext
        self._param_dict[TOTAL_PIXEL_VALUE] = calculate_pixel_count(self.original_width, self.original_height)

        # GPS 信息
        if 'GPSPosition' in self.exif:
            self._param_dict[GEO_INFO_VALUE] = ' '.join(extract_gps_info(self.exif['GPSPosition']))
        elif 'GPSLatitude' in self.exif and 'GPSLongitude' in self.exif:
            self._param_dict[GEO_INFO_VALUE] = ' '.join(
                extract_gps_lat_and_long(self.exif['GPSLatitude'], self.exif['GPSLongitude'])
            )
        else:
            self._param_dict[GEO_INFO_VALUE] = '无'

        self._param_dict[CAMERA_MAKE_CAMERA_MODEL_VALUE] = ' '.join(
            [self._param_dict[MAKE_VALUE], self._param_dict[MODEL_VALUE]]
        )
        self._param_dict[LENS_MAKE_LENS_MODEL_VALUE] = ' '.join(
            [self.lens_make, self._param_dict[LENS_VALUE]]
        )
        self._param_dict[CAMERA_MODEL_LENS_MODEL_VALUE] = ' '.join(
            [self._param_dict[MODEL_VALUE], self._param_dict[LENS_VALUE]]
        )
        self._param_dict[DATE_FILENAME_VALUE] = ' '.join(
            [self._param_dict[DATE_VALUE], self._param_dict[FILENAME_VALUE]]
        )
        self._param_dict[DATETIME_FILENAME_VALUE] = ' '.join(
            [self._param_dict[DATETIME_VALUE], self._param_dict[FILENAME_VALUE]]
        )

    # ---------- 上下文管理器 ----------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ---------- 属性访问 ----------

    def get_height(self) -> int:
        return self.get_watermark_img().height

    def get_width(self) -> int:
        return self.get_watermark_img().width

    def get_model(self) -> str:
        return self.model

    def get_make(self) -> str:
        return self.make

    def get_ratio(self) -> float:
        return self.img.width / self.img.height

    def get_img(self) -> Image.Image:
        return self.img

    def _parse_datetime(self) -> str:
        return datetime.strftime(self.date, '%Y-%m-%d %H:%M')

    def _parse_date(self) -> str:
        return datetime.strftime(self.date, '%Y-%m-%d')

    def get_attribute_str(self, element: ElementConfig) -> str:
        """
        通过 element 获取属性值
        :param element: element 对象
        :return: 属性值字符串
        """
        if element is None or element.get_name() == '':
            return ''

        if element.get_name() == CUSTOM_VALUE:
            self.custom = element.get_value() or ''
            return self.custom

        return self._param_dict.get(element.get_name(), '')

    def get_param_str(self) -> str:
        """组合拍摄参数，输出一个字符串"""
        focal_length = self.focal_length_in_35mm_film if self.use_equivalent_focal_length else self.focal_length
        return '  '.join([
            str(focal_length) + 'mm',
            'f/' + self.f_number,
            self.exposure_time,
            'ISO' + str(self.iso),
        ])

    def get_original_height(self) -> int:
        return self.original_height

    def get_original_width(self) -> int:
        return self.original_width

    def get_original_ratio(self) -> float:
        return self.original_width / self.original_height

    def get_logo(self) -> Optional[Image.Image]:
        return self.logo

    def set_logo(self, logo: Image.Image) -> None:
        self.logo = logo

    def is_use_equivalent_focal_length(self, flag: bool) -> None:
        self.use_equivalent_focal_length = flag

    def get_watermark_img(self) -> Image.Image:
        if self.watermark_img is None:
            self.watermark_img = self.img.copy()
        return self.watermark_img

    def update_watermark_img(self, watermark_img: Image.Image) -> None:
        if self.watermark_img == watermark_img:
            return
        original_watermark_img = self.watermark_img
        self.watermark_img = watermark_img
        if original_watermark_img is not None:
            original_watermark_img.close()

    def close(self) -> None:
        """释放图片资源。"""
        if self.img is not None:
            self.img.close()
            self.img = None
        if self.watermark_img is not None:
            self.watermark_img.close()
            self.watermark_img = None

    def save(self, target_path, quality: int = 100,
             strip_all_exif: bool = False) -> None:
        """
        保存处理后的图片。
        :param target_path: 输出路径
        :param quality: JPEG 质量
        :param strip_all_exif: 是否清除全部 EXIF 元数据（不写入任何 EXIF）

        注意：仅清除 GPS（保留其余 EXIF）由 processing_service 在保存后通过
        ExifTool 处理，因为 Pillow 无法可靠地对 GPS 子 IFD 做外科手术式删除。
        """
        # 恢复原始方向
        save_transpose = _ORIENTATION_SAVE_MAP.get(self.orientation)
        if save_transpose is not None:
            self.watermark_img = self.watermark_img.transpose(save_transpose)

        target_suffix = Path(target_path).suffix.lower()
        save_kwargs = {}

        if not strip_all_exif and 'exif' in self.img.info:
            save_kwargs['exif'] = self.img.info['exif']
        if 'icc_profile' in self.img.info:
            save_kwargs['icc_profile'] = self.img.info['icc_profile']

        if target_suffix in ('.jpg', '.jpeg'):
            if self.watermark_img.mode != 'RGB':
                self.watermark_img = self.watermark_img.convert('RGB')
            save_kwargs['quality'] = max(1, min(int(quality), 100))
            # 关闭 JPEG 默认色度子采样，保住文字边缘和细节
            save_kwargs['subsampling'] = 0
            save_kwargs['optimize'] = True
        elif target_suffix == '.png':
            if self.watermark_img.mode not in ('RGB', 'RGBA'):
                self.watermark_img = self.watermark_img.convert('RGBA')
            save_kwargs['compress_level'] = 1
            save_kwargs['optimize'] = False
        else:
            if self.watermark_img.mode != 'RGB':
                self.watermark_img = self.watermark_img.convert('RGB')
            save_kwargs['quality'] = max(1, min(int(quality), 100))

        self.watermark_img.save(target_path, **save_kwargs)
