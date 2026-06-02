"""
处理器链（Composite 模式）：所有布局/效果处理器。
"""
from __future__ import annotations

import string

from PIL import Image
from PIL import ImageFilter
from PIL import ImageOps

from entity.config import Config
from entity.image_container import ImageContainer
from enums.constant import GRAY
from enums.constant import TRANSPARENT
from utils import append_image_by_side
from utils import concatenate_image
from utils import merge_images
from utils import padding_image
from utils import resize_image_by_mode
from utils import resize_image_with_height
from utils import resize_image_with_width
from utils import square_image
from utils import text_to_image

from color_service import apply_adjustments
from color_service import apply_filter
from color_service import auto_enhance
from color_service import add_text_watermark

printable = set(string.printable)

NORMAL_HEIGHT = 1000


def _make_gap(width: int, height: int, color=TRANSPARENT) -> Image.Image:
    """工厂函数：创建间隔图片，避免全局单例被意外关闭。"""
    return Image.new('RGBA', (width, height), color=color)


def _small_h_gap() -> Image.Image:
    return _make_gap(50, 20)


def _middle_h_gap() -> Image.Image:
    return _make_gap(100, 20)


def _large_h_gap() -> Image.Image:
    return _make_gap(200, 20)


def _small_v_gap() -> Image.Image:
    return _make_gap(20, 50)


def _middle_v_gap() -> Image.Image:
    return _make_gap(20, 100)


def _large_v_gap() -> Image.Image:
    return _make_gap(20, 200)


def _line_gray() -> Image.Image:
    return _make_gap(20, 1000, color=GRAY)


def _line_transparent() -> Image.Image:
    return _make_gap(20, 1000, color=TRANSPARENT)


class ProcessorComponent:
    """图片处理器组件基类"""
    LAYOUT_ID: str | None = None
    LAYOUT_NAME: str | None = None

    def __init__(self, config: Config):
        self.config = config

    def process(self, container: ImageContainer) -> None:
        """处理图片容器中的 watermark_img，将处理后的图片放回容器中"""
        raise NotImplementedError

    def add(self, component: ProcessorComponent) -> None:
        raise NotImplementedError


class ProcessorChain(ProcessorComponent):
    """处理器链：按顺序执行所有处理器"""

    def __init__(self):
        super().__init__(None)
        self.components: list[ProcessorComponent] = []

    def add(self, component: ProcessorComponent) -> None:
        self.components.append(component)

    def process(self, container: ImageContainer) -> None:
        for component in self.components:
            component.process(container)


class EmptyProcessor(ProcessorComponent):
    LAYOUT_ID = 'empty'

    def process(self, container: ImageContainer) -> None:
        pass


class ShadowProcessor(ProcessorComponent):
    LAYOUT_ID = 'shadow'

    def process(self, container: ImageContainer) -> None:
        image = container.get_watermark_img()

        max_pixel = max(image.width, image.height)
        radius = int(max_pixel / 512)

        # 创建阴影效果
        shadow = Image.new('RGB', image.size, color='#6B696A')
        shadow = ImageOps.expand(shadow, border=(radius * 2, radius * 2, radius * 2, radius * 2), fill=(255, 255, 255))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=radius))

        # 将原始图像放置在阴影图像上方
        shadow.paste(image, (radius, radius))
        container.update_watermark_img(shadow)


class SquareProcessor(ProcessorComponent):
    LAYOUT_ID = 'square'
    LAYOUT_NAME = '1:1填充'

    def process(self, container: ImageContainer) -> None:
        image = container.get_watermark_img()
        container.update_watermark_img(square_image(image, auto_close=False))


class WatermarkProcessor(ProcessorComponent):
    """水印处理器基类，包含文字渲染和 Logo 拼接逻辑。"""
    LAYOUT_ID = 'watermark'

    def __init__(self, config: Config):
        super().__init__(config)
        # 默认值
        self.logo_position: str = 'left'
        self.logo_enable: bool = True
        self.bg_color: str = '#ffffff'
        self.line_color: str = GRAY
        self.font_color_lt: str = '#212121'
        self.bold_font_lt: bool = True
        self.font_color_lb: str = '#424242'
        self.bold_font_lb: bool = False
        self.font_color_rt: str = '#212121'
        self.bold_font_rt: bool = True
        self.font_color_rb: str = '#424242'
        self.bold_font_rb: bool = False

    def is_logo_left(self) -> bool:
        return self.logo_position == 'left'

    def _render_text_columns(self, container: ImageContainer) -> tuple[Image.Image, Image.Image]:
        """渲染左右两列文字内容。"""
        config = self.config
        empty_padding = Image.new('RGBA', (10, 100), color=self.bg_color)

        left_top = text_to_image(
            container.get_attribute_str(config.get_left_top()),
            config.get_font(), config.get_bold_font(),
            is_bold=self.bold_font_lt, fill=self.font_color_lt,
        )
        left_bottom = text_to_image(
            container.get_attribute_str(config.get_left_bottom()),
            config.get_font(), config.get_bold_font(),
            is_bold=self.bold_font_lb, fill=self.font_color_lb,
        )
        left = concatenate_image([left_top, empty_padding, left_bottom])

        right_top = text_to_image(
            container.get_attribute_str(config.get_right_top()),
            config.get_font(), config.get_bold_font(),
            is_bold=self.bold_font_rt, fill=self.font_color_rt,
        )
        right_bottom = text_to_image(
            container.get_attribute_str(config.get_right_bottom()),
            config.get_font(), config.get_bold_font(),
            is_bold=self.bold_font_rb, fill=self.font_color_rb,
        )
        right = concatenate_image([right_top, empty_padding, right_bottom])

        empty_padding.close()
        return left, right

    def _compose_watermark_bar(self, container: ImageContainer,
                               left: Image.Image, right: Image.Image,
                               padding_ratio: float, ratio: float) -> Image.Image:
        """组合水印条：文字 + Logo。"""
        config = self.config

        # 将左右两边的文字内容等比例缩放到相同的高度
        max_height = max(left.height, right.height)
        left = padding_image(left, int(max_height * padding_ratio), 'tb')
        right = padding_image(right, int(max_height * padding_ratio), 't')
        right = padding_image(right, left.height - right.height, 'b')

        # 创建水印画布
        watermark = Image.new('RGBA', (int(NORMAL_HEIGHT / ratio), NORMAL_HEIGHT), color=self.bg_color)

        logo = config.load_logo(container.make)
        if self.logo_enable:
            if self.is_logo_left():
                line = _line_transparent()
                logo = padding_image(logo, int(padding_ratio * logo.height))
                append_image_by_side(watermark, [line, logo, left], is_start=logo is None)
                append_image_by_side(watermark, [right], side='right')
            else:
                if logo is not None:
                    logo = padding_image(logo, int(padding_ratio * logo.height))
                    line = padding_image(_line_gray(), int(padding_ratio * 1000 * .8))
                else:
                    line = _line_transparent()
                append_image_by_side(watermark, [left], is_start=True)
                append_image_by_side(watermark, [logo, line, right], side='right')
                line.close()
        else:
            append_image_by_side(watermark, [left], is_start=True)
            append_image_by_side(watermark, [right], side='right')

        left.close()
        right.close()
        return watermark

    def _merge_with_image(self, container: ImageContainer, watermark: Image.Image) -> None:
        """将水印条合并到原图下方。"""
        watermark = resize_image_with_width(watermark, container.get_width())

        bg = ImageOps.expand(
            container.get_watermark_img().convert('RGBA'),
            border=(0, 0, 0, watermark.height),
            fill=self.bg_color,
        )
        fg = ImageOps.expand(watermark, border=(0, container.get_height(), 0, 0), fill=TRANSPARENT)
        result = Image.alpha_composite(bg, fg)
        watermark.close()

        result = ImageOps.exif_transpose(result).convert('RGB')
        container.update_watermark_img(result)

    def process(self, container: ImageContainer) -> None:
        """生成水印布局并合并到图片。"""
        container.bg_color = self.bg_color

        # 下方水印的占比
        ratio = (.04 if container.get_ratio() >= 1 else .09) + 0.02 * self.config.get_font_padding_level()
        # 水印中上下边缘空白部分的占比
        padding_ratio = (.52 if container.get_ratio() >= 1 else .7) - 0.04 * self.config.get_font_padding_level()

        left, right = self._render_text_columns(container)
        watermark = self._compose_watermark_bar(container, left, right, padding_ratio, ratio)
        self._merge_with_image(container, watermark)


class WatermarkRightLogoProcessor(WatermarkProcessor):
    LAYOUT_ID = 'watermark_right_logo'
    LAYOUT_NAME = 'normal(Logo 居右)'

    def __init__(self, config: Config):
        super().__init__(config)
        self.logo_position = 'right'


class WatermarkLeftLogoProcessor(WatermarkProcessor):
    LAYOUT_ID = 'watermark_left_logo'
    LAYOUT_NAME = 'normal'

    def __init__(self, config: Config):
        super().__init__(config)
        self.logo_position = 'left'


class DarkWatermarkRightLogoProcessor(WatermarkRightLogoProcessor):
    LAYOUT_ID = 'dark_watermark_right_logo'
    LAYOUT_NAME = 'normal(黑红配色，Logo 居右)'

    def __init__(self, config: Config):
        super().__init__(config)
        self.bg_color = '#212121'
        self.line_color = GRAY
        self.font_color_lt = '#D32F2F'
        self.bold_font_lt = True
        self.font_color_lb = '#d4d1cc'
        self.bold_font_lb = False
        self.font_color_rt = '#D32F2F'
        self.bold_font_rt = True
        self.font_color_rb = '#d4d1cc'
        self.bold_font_rb = False


class DarkWatermarkLeftLogoProcessor(WatermarkLeftLogoProcessor):
    LAYOUT_ID = 'dark_watermark_left_logo'
    LAYOUT_NAME = 'normal(黑红配色)'

    def __init__(self, config: Config):
        super().__init__(config)
        self.bg_color = '#212121'
        self.line_color = GRAY
        self.font_color_lt = '#D32F2F'
        self.bold_font_lt = True
        self.font_color_lb = '#d4d1cc'
        self.bold_font_lb = False
        self.font_color_rt = '#D32F2F'
        self.bold_font_rt = True
        self.font_color_rb = '#d4d1cc'
        self.bold_font_rb = False


class CustomWatermarkProcessor(WatermarkProcessor):
    LAYOUT_ID = 'custom_watermark'
    LAYOUT_NAME = 'normal(自定义配置)'

    def __init__(self, config: Config):
        super().__init__(config)
        self.logo_position = self.config.is_logo_left()
        self.logo_enable = self.config.has_logo_enabled()
        self.bg_color = self.config.get_background_color()
        self.font_color_lt = self.config.get_left_top().get_color()
        self.bold_font_lt = self.config.get_left_top().is_bold()
        self.font_color_lb = self.config.get_left_bottom().get_color()
        self.bold_font_lb = self.config.get_left_bottom().is_bold()
        self.font_color_rt = self.config.get_right_top().get_color()
        self.bold_font_rt = self.config.get_right_top().is_bold()
        self.font_color_rb = self.config.get_right_bottom().get_color()
        self.bold_font_rb = self.config.get_right_bottom().is_bold()


class MarginProcessor(ProcessorComponent):
    LAYOUT_ID = 'margin'

    def process(self, container: ImageContainer) -> None:
        config = self.config
        padding_size = int(config.get_white_margin_width() * min(container.get_width(), container.get_height()) / 100)
        padding_img = padding_image(container.get_watermark_img(), padding_size, 'tlr', color=container.bg_color)
        container.update_watermark_img(padding_img)


class SimpleProcessor(ProcessorComponent):
    LAYOUT_ID = 'simple'
    LAYOUT_NAME = '简洁'

    def process(self, container: ImageContainer) -> None:
        ratio = .16 if container.get_ratio() >= 1 else .1
        padding_ratio = .5

        first_text = text_to_image(
            'Shot on',
            self.config.get_alternative_font(),
            self.config.get_alternative_bold_font(),
            is_bold=False, fill='#212121',
        )
        model = text_to_image(
            container.get_model().replace(r'/', ' ').replace(r'_', ' '),
            self.config.get_alternative_font(),
            self.config.get_alternative_bold_font(),
            is_bold=True, fill='#D32F2F',
        )
        make = text_to_image(
            container.get_make().split(' ')[0],
            self.config.get_alternative_font(),
            self.config.get_alternative_bold_font(),
            is_bold=True, fill='#212121',
        )
        first_line = merge_images([first_text, _middle_h_gap(), model, _middle_h_gap(), make], 0, 1)
        second_line = text_to_image(
            container.get_param_str(),
            self.config.get_alternative_font(),
            self.config.get_alternative_bold_font(),
            is_bold=False, fill='#9E9E9E',
        )
        image = merge_images([first_line, _middle_v_gap(), second_line], 1, 0)
        height = container.get_height() * ratio * padding_ratio
        image = resize_image_with_height(image, int(height))
        horizontal_padding = int((container.get_width() - image.width) / 2)
        vertical_padding = int((container.get_height() * ratio - image.height) / 2)

        watermark = ImageOps.expand(image, (horizontal_padding, vertical_padding), fill=TRANSPARENT)
        bg = Image.new('RGBA', watermark.size, color='white')
        bg = Image.alpha_composite(bg, watermark)

        watermark_img = merge_images([container.get_watermark_img(), bg], 1, 1)
        container.update_watermark_img(watermark_img)


class PaddingToOriginalRatioProcessor(ProcessorComponent):
    LAYOUT_ID = 'padding_to_original_ratio'

    def process(self, container: ImageContainer) -> None:
        original_ratio = container.get_original_ratio()
        ratio = container.get_ratio()
        if original_ratio > ratio:
            padding_size = int(container.get_width() / original_ratio - container.get_height())
            padding_img = ImageOps.expand(container.get_watermark_img(), (0, padding_size), fill='white')
        else:
            padding_size = int(container.get_height() * original_ratio - container.get_width())
            padding_img = ImageOps.expand(container.get_watermark_img(), (padding_size, 0), fill='white')
        container.update_watermark_img(padding_img)


class UniformResizeProcessor(ProcessorComponent):
    LAYOUT_ID = 'uniform_resize'

    def process(self, container: ImageContainer) -> None:
        width = self.config.get_uniform_resize_width()
        height = self.config.get_uniform_resize_height()
        mode = self.config.get_uniform_resize_mode()
        background_color = getattr(container, 'bg_color', 'white')
        if mode == 'smart':
            # 智能裁切：保留主体 / 人脸；任何异常回退居中裁切。
            try:
                from smart_crop_service import smart_crop
                resized_img = smart_crop(container.get_watermark_img(), width, height)
            except Exception:
                resized_img = resize_image_by_mode(
                    container.get_watermark_img(), width, height,
                    mode='crop', background_color=background_color,
                )
        else:
            resized_img = resize_image_by_mode(
                container.get_watermark_img(), width, height,
                mode=mode, background_color=background_color,
            )
        container.update_watermark_img(resized_img)


PADDING_PERCENT_IN_BACKGROUND = 0.18
GAUSSIAN_KERNEL_RADIUS = 35


class BackgroundBlurProcessor(ProcessorComponent):
    LAYOUT_ID = 'background_blur'
    LAYOUT_NAME = '背景模糊'

    def process(self, container: ImageContainer) -> None:
        background = container.get_watermark_img()
        background = background.filter(ImageFilter.GaussianBlur(radius=GAUSSIAN_KERNEL_RADIUS))
        fg = Image.new('RGB', background.size, color=(255, 255, 255))
        background = Image.blend(background, fg, 0.1)
        background = background.resize((
            int(container.get_width() * (1 + PADDING_PERCENT_IN_BACKGROUND)),
            int(container.get_height() * (1 + PADDING_PERCENT_IN_BACKGROUND)),
        ))
        background.paste(
            container.get_watermark_img(),
            (int(container.get_width() * PADDING_PERCENT_IN_BACKGROUND / 2),
             int(container.get_height() * PADDING_PERCENT_IN_BACKGROUND / 2)),
        )
        container.update_watermark_img(background)


class BackgroundBlurWithWhiteBorderProcessor(ProcessorComponent):
    LAYOUT_ID = 'background_blur_with_white_border'
    LAYOUT_NAME = '背景模糊+白框'

    def process(self, container: ImageContainer) -> None:
        padding_size = int(
            self.config.get_white_margin_width() * min(container.get_width(), container.get_height()) / 256
        )
        padding_img = padding_image(container.get_watermark_img(), padding_size, 'tblr', color='white')

        background = container.get_img()
        background = background.filter(ImageFilter.GaussianBlur(radius=GAUSSIAN_KERNEL_RADIUS))
        background = background.resize((
            int(padding_img.width * (1 + PADDING_PERCENT_IN_BACKGROUND)),
            int(padding_img.height * (1 + PADDING_PERCENT_IN_BACKGROUND)),
        ))
        fg = Image.new('RGB', background.size, color=(255, 255, 255))
        background = Image.blend(background, fg, 0.1)
        background.paste(
            padding_img,
            (int(padding_img.width * PADDING_PERCENT_IN_BACKGROUND / 2),
             int(padding_img.height * PADDING_PERCENT_IN_BACKGROUND / 2)),
        )
        container.update_watermark_img(background)


class PureWhiteMarginProcessor(ProcessorComponent):
    LAYOUT_ID = 'pure_white_margin'
    LAYOUT_NAME = '白色边框'

    def process(self, container: ImageContainer) -> None:
        config = self.config
        padding_size = int(config.get_white_margin_width() * min(container.get_width(), container.get_height()) / 100)
        padding_img = padding_image(container.get_watermark_img(), padding_size, 'tlrb', color=container.bg_color)
        container.update_watermark_img(padding_img)


class ColorGradingProcessor(ProcessorComponent):
    """调色 / 滤镜 / 自动增强处理器。建议放在处理链最前面（先调色再排版）。"""
    LAYOUT_ID = 'color_grading'

    def process(self, container: ImageContainer) -> None:
        config = self.config
        image = container.get_watermark_img()

        result = image
        # 1. 滤镜预设
        filter_name = config.get_color_filter()
        if filter_name and filter_name != 'none':
            new_img = apply_filter(result, filter_name)
            if result is not image and new_img is not result:
                result.close()
            result = new_img

        # 2. 手动微调
        adjustments = config.get_color_adjustments()
        need_adjust = (
            abs(adjustments['brightness'] - 1.0) > 1e-3
            or abs(adjustments['contrast'] - 1.0) > 1e-3
            or abs(adjustments['saturation'] - 1.0) > 1e-3
            or abs(adjustments['sharpness'] - 1.0) > 1e-3
            or adjustments['temperature'] != 0
        )
        if need_adjust:
            new_img = apply_adjustments(result, **adjustments)
            if result is not image:
                result.close()
            result = new_img

        # 3. 自动对比度
        if config.has_auto_contrast_enabled():
            new_img = auto_enhance(result, autocontrast=True)
            if result is not image:
                result.close()
            result = new_img

        if result is not image:
            container.update_watermark_img(result)


class TextWatermarkProcessor(ProcessorComponent):
    """平铺 / 单点防盗文字水印处理器。建议放在处理链末尾（最终成图上打水印）。"""
    LAYOUT_ID = 'text_watermark'

    def process(self, container: ImageContainer) -> None:
        settings = self.config.get_text_watermark()
        if not settings['text'].strip():
            return
        image = container.get_watermark_img()
        result = add_text_watermark(
            image,
            settings['text'],
            tiled=settings['tiled'],
            opacity=settings['opacity'],
            color=settings['color'],
            position=settings['position'],
        )
        container.update_watermark_img(result)
