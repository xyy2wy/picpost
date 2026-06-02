"""
处理管线：构建 ProcessorChain、批量处理图片（支持并行）。
"""
from __future__ import annotations

import logging
from concurrent.futures import as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from entity.config import Config
from entity.image_container import ImageContainer
from entity.image_processor import BackgroundBlurProcessor
from entity.image_processor import BackgroundBlurWithWhiteBorderProcessor
from entity.image_processor import ColorGradingProcessor
from entity.image_processor import CustomWatermarkProcessor
from entity.image_processor import DarkWatermarkLeftLogoProcessor
from entity.image_processor import DarkWatermarkRightLogoProcessor
from entity.image_processor import MarginProcessor
from entity.image_processor import PaddingToOriginalRatioProcessor
from entity.image_processor import ProcessorChain
from entity.image_processor import PureWhiteMarginProcessor
from entity.image_processor import ShadowProcessor
from entity.image_processor import SimpleProcessor
from entity.image_processor import SquareProcessor
from entity.image_processor import TextWatermarkProcessor
from entity.image_processor import UniformResizeProcessor
from entity.image_processor import WatermarkLeftLogoProcessor
from entity.image_processor import WatermarkRightLogoProcessor
from enums.constant import CAMERA_MAKE_CAMERA_MODEL_NAME
from enums.constant import CAMERA_MAKE_CAMERA_MODEL_VALUE
from enums.constant import CAMERA_MODEL_LENS_MODEL_NAME
from enums.constant import CAMERA_MODEL_LENS_MODEL_VALUE
from enums.constant import CUSTOM_NAME
from enums.constant import CUSTOM_VALUE
from enums.constant import DATETIME_FILENAME_NAME
from enums.constant import DATETIME_FILENAME_VALUE
from enums.constant import DATETIME_NAME
from enums.constant import DATETIME_VALUE
from enums.constant import DATE_FILENAME_NAME
from enums.constant import DATE_FILENAME_VALUE
from enums.constant import DATE_NAME
from enums.constant import DATE_VALUE
from enums.constant import FILENAME_NAME
from enums.constant import FILENAME_VALUE
from enums.constant import GEO_INFO
from enums.constant import GEO_INFO_VALUE
from enums.constant import LENS_MAKE_LENS_MODEL_NAME
from enums.constant import LENS_MAKE_LENS_MODEL_VALUE
from enums.constant import LENS_NAME
from enums.constant import LENS_VALUE
from enums.constant import LOCATION_LEFT_BOTTOM
from enums.constant import LOCATION_LEFT_TOP
from enums.constant import LOCATION_RIGHT_BOTTOM
from enums.constant import LOCATION_RIGHT_TOP
from enums.constant import MAKE_NAME
from enums.constant import MAKE_VALUE
from enums.constant import MODEL_NAME
from enums.constant import MODEL_VALUE
from enums.constant import NONE_NAME
from enums.constant import NONE_VALUE
from enums.constant import PARAM_NAME
from enums.constant import PARAM_VALUE
from enums.constant import TOTAL_PIXEL_NAME
from enums.constant import TOTAL_PIXEL_VALUE
from utils import get_file_list
from utils import strip_gps_in_file

logger = logging.getLogger(__name__)

LAYOUT_OPTIONS = [
    ("normal", "watermark_left_logo"),
    ("normal(Logo 居右)", "watermark_right_logo"),
    ("normal(黑红配色)", "dark_watermark_left_logo"),
    ("normal(黑红配色，Logo 居右)", "dark_watermark_right_logo"),
    ("normal(自定义配置)", "custom_watermark"),
    ("1:1填充", "square"),
    ("简洁", "simple"),
    ("背景模糊", "background_blur"),
    ("背景模糊+白框", "background_blur_with_white_border"),
    ("白色边框", "pure_white_margin"),
]

ELEMENT_OPTIONS = [
    (MODEL_NAME, MODEL_VALUE),
    (MAKE_NAME, MAKE_VALUE),
    (LENS_NAME, LENS_VALUE),
    (PARAM_NAME, PARAM_VALUE),
    (DATETIME_NAME, DATETIME_VALUE),
    (DATE_NAME, DATE_VALUE),
    (CUSTOM_NAME, CUSTOM_VALUE),
    (NONE_NAME, NONE_VALUE),
    (LENS_MAKE_LENS_MODEL_NAME, LENS_MAKE_LENS_MODEL_VALUE),
    (CAMERA_MODEL_LENS_MODEL_NAME, CAMERA_MODEL_LENS_MODEL_VALUE),
    (TOTAL_PIXEL_NAME, TOTAL_PIXEL_VALUE),
    (CAMERA_MAKE_CAMERA_MODEL_NAME, CAMERA_MAKE_CAMERA_MODEL_VALUE),
    (FILENAME_NAME, FILENAME_VALUE),
    (DATE_FILENAME_NAME, DATE_FILENAME_VALUE),
    (DATETIME_FILENAME_NAME, DATETIME_FILENAME_VALUE),
    (GEO_INFO, GEO_INFO_VALUE),
]

ELEMENT_LOCATIONS = [
    (LOCATION_LEFT_TOP, "左上角"),
    (LOCATION_LEFT_BOTTOM, "左下角"),
    (LOCATION_RIGHT_TOP, "右上角"),
    (LOCATION_RIGHT_BOTTOM, "右下角"),
]

UNIFORM_RESIZE_MODES = [
    ("留白补齐", "padding"),
    ("居中裁切", "crop"),
    ("强制拉伸", "stretch"),
    ("智能裁切", "smart"),
]


@dataclass
class ProcessResult:
    source_path: Path
    output_path: Path
    original_size: tuple[int, int]
    output_size: tuple[int, int]


def build_runtime_config(config_path: str | Path, overrides: dict | None = None) -> Config:
    """构建运行时配置，支持覆盖参数。"""
    config = Config(str(config_path))
    if not overrides:
        return config

    data = config.get_data()
    base = overrides.get("base", {})
    global_settings = overrides.get("global", {})
    layout = overrides.get("layout", {})

    if "quality" in base:
        data["base"]["quality"] = int(base["quality"])

    if "type" in layout:
        data["layout"]["type"] = layout["type"]
    if "logo_enable" in layout:
        data["layout"]["logo_enable"] = bool(layout["logo_enable"])
    if "logo_position" in layout:
        data["layout"]["logo_position"] = layout["logo_position"]
    if "background_color" in layout:
        data["layout"]["background_color"] = layout["background_color"]

    layout_elements = layout.get("elements", {})
    for location, element_value in layout_elements.items():
        if location not in data["layout"]["elements"]:
            continue
        if "name" in element_value:
            data["layout"]["elements"][location]["name"] = element_value["name"]
        if "value" in element_value:
            data["layout"]["elements"][location]["value"] = element_value["value"]

    if "white_margin" in global_settings:
        data["global"]["white_margin"].update(global_settings["white_margin"])
    if "shadow" in global_settings:
        data["global"]["shadow"].update(global_settings["shadow"])
    if "focal_length" in global_settings:
        data["global"]["focal_length"].update(global_settings["focal_length"])
    if "padding_with_original_ratio" in global_settings:
        data["global"]["padding_with_original_ratio"].update(global_settings["padding_with_original_ratio"])
    if "uniform_resize" in global_settings:
        data["global"]["uniform_resize"].update(global_settings["uniform_resize"])
    if "privacy" in global_settings:
        data["global"].setdefault("privacy", {})
        data["global"]["privacy"].update(global_settings["privacy"])
    if "color" in global_settings:
        data["global"].setdefault("color", {})
        data["global"]["color"].update(global_settings["color"])
    if "text_watermark" in global_settings:
        data["global"].setdefault("text_watermark", {})
        data["global"]["text_watermark"].update(global_settings["text_watermark"])

    return config


def create_processor_chain(config: Config) -> ProcessorChain:
    """根据配置动态组装处理器链。"""
    processor_chain = ProcessorChain()

    # 调色 / 滤镜 / 自动增强：放在最前面，先调色再排版
    if config.has_color_processing_enabled():
        processor_chain.add(ColorGradingProcessor(config))

    if config.has_shadow_enabled() and config.get_layout_type() != "square":
        processor_chain.add(ShadowProcessor(config))

    layout_processors = {
        "watermark_left_logo": WatermarkLeftLogoProcessor,
        "watermark_right_logo": WatermarkRightLogoProcessor,
        "dark_watermark_left_logo": DarkWatermarkLeftLogoProcessor,
        "dark_watermark_right_logo": DarkWatermarkRightLogoProcessor,
        "custom_watermark": CustomWatermarkProcessor,
        "square": SquareProcessor,
        "simple": SimpleProcessor,
        "background_blur": BackgroundBlurProcessor,
        "background_blur_with_white_border": BackgroundBlurWithWhiteBorderProcessor,
        "pure_white_margin": PureWhiteMarginProcessor,
    }
    processor_cls = layout_processors.get(config.get_layout_type(), SimpleProcessor)
    processor_chain.add(processor_cls(config))

    if config.has_white_margin_enabled() and "watermark" in config.get_layout_type():
        processor_chain.add(MarginProcessor(config))

    if config.has_padding_with_original_ratio_enabled() and config.get_layout_type() != "square":
        processor_chain.add(PaddingToOriginalRatioProcessor(config))

    if config.has_uniform_resize_enabled():
        processor_chain.add(UniformResizeProcessor(config))

    # 防盗文字水印：放在最后，打在最终成图上
    if config.has_text_watermark_enabled():
        processor_chain.add(TextWatermarkProcessor(config))

    return processor_chain


def _process_single_image(source_path: Path, output_dir: Path, config: Config,
                          processor_chain: ProcessorChain) -> ProcessResult:
    """处理单张图片（供并行调用）。"""
    strip_all_exif = config.has_strip_all_exif_enabled()
    strip_gps = config.has_strip_gps_enabled()

    with ImageContainer(source_path) as container:
        container.is_use_equivalent_focal_length(config.use_equivalent_focal_length())
        original_size = container.get_watermark_img().size
        processor_chain.process(container)
        output_path = output_dir / source_path.name
        container.save(
            output_path,
            quality=config.get_quality(),
            strip_all_exif=strip_all_exif,
        )
        output_size = container.get_watermark_img().size

    # 仅清除 GPS（保留其余 EXIF）需在保存后用 ExifTool 处理；
    # 若已清除全部 EXIF 则无需再处理。
    if strip_gps and not strip_all_exif:
        strip_gps_in_file(output_path)

    return ProcessResult(source_path, output_path, original_size, output_size)


def process_images(source_paths: list[Path], output_dir: Path, config: Config,
                   max_workers: Optional[int] = None) -> list[ProcessResult]:
    """
    批量处理图片。
    使用线程池并行处理以提升性能（Pillow 操作释放 GIL 的部分可以并行）。
    :param source_paths: 源图片路径列表
    :param output_dir: 输出目录
    :param config: 配置对象
    :param max_workers: 最大并行数，None 表示自动
    :return: 处理结果列表
    """
    from concurrent.futures import ThreadPoolExecutor

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    processor_chain = create_processor_chain(config)
    results: list[ProcessResult] = []

    # 对于少量图片直接串行处理，避免线程池开销
    if len(source_paths) <= 2:
        for source_path in source_paths:
            result = _process_single_image(source_path, output_dir, config, processor_chain)
            results.append(result)
        return results

    # 多图片使用线程池并行处理
    effective_workers = max_workers or min(4, len(source_paths))
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {
            executor.submit(_process_single_image, path, output_dir, config, processor_chain): path
            for path in source_paths
        }
        for future in as_completed(futures):
            source_path = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.error(f'处理图片失败: {source_path}: {e}')

    # 按原始顺序排序结果
    path_order = {path: idx for idx, path in enumerate(source_paths)}
    results.sort(key=lambda r: path_order.get(r.source_path, 0))
    return results


def process_images_with_cover(source_paths: list[Path], output_dir: Path,
                              cover_config: Config, body_config: Config,
                              cover_index: int = 0,
                              max_workers: Optional[int] = None) -> list[ProcessResult]:
    """
    批次内多样式：封面图用 cover_config，其余图片用 body_config。

    封面图通过 cover_config 对应的处理器链处理，其余图片通过 body_config 的链处理，
    实现“封面一种风格、内页另一种风格”。

    :param source_paths: 源图片路径列表
    :param output_dir: 输出目录
    :param cover_config: 封面图使用的配置
    :param body_config: 内页（其余图片）使用的配置
    :param cover_index: 封面图在列表中的索引，越界（<0 或 >= 长度）回退为 0（需求 5.4）
    :param max_workers: 兼容 process_images 的签名，本函数串行处理时未使用
    :return: 处理结果列表，顺序与输入一致
    """
    del max_workers  # 串行处理，保留参数以与 process_images 签名一致

    if not source_paths:
        return []

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 封面序号越界回退为第一张（需求 5.4）
    if not isinstance(cover_index, int) or cover_index < 0 or cover_index >= len(source_paths):
        cover_index = 0

    # 分别构建封面链与内页链，每张图按归属走对应配置 + 链
    cover_chain = create_processor_chain(cover_config)
    body_chain = create_processor_chain(body_config)

    results: list[ProcessResult] = []
    for idx, source_path in enumerate(source_paths):
        if idx == cover_index:
            cfg, chain = cover_config, cover_chain
        else:
            cfg, chain = body_config, body_chain
        result = _process_single_image(source_path, output_dir, cfg, chain)
        results.append(result)

    return results


def list_input_images(input_dir: str | Path) -> list[Path]:
    """列出输入目录中的图片文件。"""
    return sorted(get_file_list(input_dir), key=lambda path: path.name.lower())
