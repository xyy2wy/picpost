"""
实时预览：基于下采样图 + 现有处理链的预览生成。

复用 `processing_service.create_processor_chain` 与 `entity.image_container.ImageContainer`，
保证预览与正式输出使用同一套处理器，视觉效果一致（仅分辨率不同）。

设计（零侵入）：把样图下采样到 max_side 后写入临时文件，再用现有的
`ImageContainer(path)` 路径构造方式跑同一条处理链，返回预览图。
不修改 `ImageContainer` 内部实现。
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from PIL import Image

from entity.config import Config
from entity.image_container import ImageContainer
from processing_service import create_processor_chain

logger = logging.getLogger(__name__)


class PreviewError(Exception):
    """预览生成失败时抛出，携带友好中文提示。"""


def render_preview(sample_path: Path, config: Config, max_side: int = 900) -> Image.Image:
    """
    生成样图预览。

    将样图下采样到最长边不超过 max_side，再用 `create_processor_chain(config)`
    跑与正式处理相同的处理链，返回预览图（RGB）。

    :param sample_path: 样图路径
    :param config: 运行时配置对象
    :param max_side: 下采样后的最长边像素上限（控制预览速度，需求 4.2）
    :return: 处理后的预览图（新的 PIL.Image，调用方可自由使用 / 关闭）
    :raises PreviewError: 任何环节失败时抛出，由上层保留上次成功预览
    """
    sample_path = Path(sample_path)
    if not sample_path.exists():
        raise PreviewError(f"找不到样图：{sample_path}")

    if max_side <= 0:
        raise PreviewError(f"预览尺寸上限必须为正数，当前为 {max_side}")

    temp_path: str | None = None
    try:
        # 1) 先下采样再进链（性能：需求 4.2）
        with Image.open(sample_path) as sample:
            downsampled = sample.convert("RGB")
            # thumbnail 原地缩放且保持比例，最长边 <= max_side；小图不会被放大
            downsampled.thumbnail((max_side, max_side), Image.LANCZOS)

            fd, temp_path = tempfile.mkstemp(suffix=".jpg", prefix="semi_preview_")
            os.close(fd)
            downsampled.save(temp_path, format="JPEG", quality=90)

        # 2) 用现有路径构造方式 + 同一条处理链（一致性：需求 4.3）
        container = ImageContainer(Path(temp_path))
        try:
            container.is_use_equivalent_focal_length(config.use_equivalent_focal_length())
            chain = create_processor_chain(config)
            chain.process(container)
            # copy 以便返回的图在 container 关闭后仍可用
            result = container.get_watermark_img().convert("RGB").copy()
        finally:
            container.close()

        return result
    except PreviewError:
        raise
    except Exception as e:  # noqa: BLE001 - 统一转为友好提示
        logger.exception("预览生成失败")
        raise PreviewError(f"预览生成失败：{e}") from e
    finally:
        if temp_path is not None:
            try:
                os.remove(temp_path)
            except OSError:
                pass
