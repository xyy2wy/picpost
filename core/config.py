"""
配置对象：读写 config.yaml，提供各项配置的 getter/setter。
"""
from __future__ import annotations

import os
from typing import Optional

import yaml
from PIL import Image
from PIL import ImageFont

from core.constants import CUSTOM_VALUE
from core.constants import LOCATION_LEFT_BOTTOM
from core.constants import LOCATION_LEFT_TOP
from core.constants import LOCATION_RIGHT_BOTTOM
from core.constants import LOCATION_RIGHT_TOP


class ElementConfig:
    """布局中元素的配置对象"""

    def __init__(self, element: dict):
        self.element = element

    def get_name(self) -> str:
        return self.element['name']

    def is_bold(self) -> bool:
        return self.element['is_bold']

    def get_value(self) -> Optional[str]:
        return self.element.get('value')

    def get_color(self) -> str:
        return self.element.get('color', '#212121')


# 字体大小，影响字体的清晰度
FONT_SIZE = 240
BOLD_FONT_SIZE = 260

# 字体大小映射
_FONT_SIZE_MAP = {1: 240, 2: 250, 3: 300}
_BOLD_FONT_SIZE_MAP = {1: 260, 2: 290, 3: 320}


class Config:
    """配置对象"""

    def __init__(self, path: str):
        self._path = path
        with open(self._path, 'r', encoding='utf-8') as f:
            self._data = yaml.safe_load(f)
        self._initialize_defaults()
        self._logos: dict[str, Image.Image] = {}
        self._font_cache: dict[str, ImageFont.FreeTypeFont] = {}
        self._left_top = ElementConfig(self._data['layout']['elements'][LOCATION_LEFT_TOP])
        self._left_bottom = ElementConfig(self._data['layout']['elements'][LOCATION_LEFT_BOTTOM])
        self._right_top = ElementConfig(self._data['layout']['elements'][LOCATION_RIGHT_TOP])
        self._right_bottom = ElementConfig(self._data['layout']['elements'][LOCATION_RIGHT_BOTTOM])
        self._makes = self._data['logo']['makes']
        self.bg_color: str = self._data['layout'].get('background_color', '#ffffff')

    def _initialize_defaults(self) -> None:
        global_config = self._data.setdefault('global', {})
        uniform_resize = global_config.setdefault('uniform_resize', {})
        uniform_resize.setdefault('enable', False)
        uniform_resize.setdefault('width', 1080)
        uniform_resize.setdefault('height', 1350)
        uniform_resize.setdefault('mode', 'padding')

        # EXIF 隐私设置：发布到公开平台时清除元数据（尤其是 GPS 拍摄位置）
        privacy = global_config.setdefault('privacy', {})
        privacy.setdefault('strip_gps', False)
        privacy.setdefault('strip_all_exif', False)

        # 调色 / 滤镜 / 自动增强
        color = global_config.setdefault('color', {})
        color.setdefault('filter', 'none')
        color.setdefault('brightness', 1.0)
        color.setdefault('contrast', 1.0)
        color.setdefault('saturation', 1.0)
        color.setdefault('sharpness', 1.0)
        color.setdefault('temperature', 0)
        color.setdefault('auto_contrast', False)

        # 平铺 / 单点防盗文字水印
        text_watermark = global_config.setdefault('text_watermark', {})
        text_watermark.setdefault('enable', False)
        text_watermark.setdefault('text', '')
        text_watermark.setdefault('tiled', True)
        text_watermark.setdefault('opacity', 0.15)
        text_watermark.setdefault('color', '#ffffff')
        text_watermark.setdefault('position', 'bottom_right')

        # 批次内多样式：封面单独样式（封面用风格包 A，其余用风格包 B）
        batch_style = global_config.setdefault('batch_style', {})
        batch_style.setdefault('cover_separate', False)
        batch_style.setdefault('cover_index', 0)

    def _load_font(self, font_path: str, size: int) -> ImageFont.FreeTypeFont:
        """缓存字体对象，避免重复创建。"""
        cache_key = f'{font_path}:{size}'
        if cache_key not in self._font_cache:
            self._font_cache[cache_key] = ImageFont.truetype(font_path, size)
        return self._font_cache[cache_key]

    def get(self, key: str):
        return self._data.get(key)

    def get_or_default(self, key: str, default):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def load_logo(self, make: str) -> Image.Image:
        """
        根据厂商获取 logo（带缓存）
        :param make: 厂商
        :return: logo
        """
        if make in self._logos:
            return self._logos[make]

        for m in self._makes.values():
            if m['id'] == '':
                continue
            if m['id'].lower() in make.lower():
                logo = Image.open(m['path'])
                self._logos[make] = logo
                return logo

        logo_path = self._data['logo']['default']['path']
        logo = Image.open(logo_path)
        self._logos[make] = logo
        return logo

    def get_data(self) -> dict:
        return self._data

    def get_input_dir(self) -> str:
        return self._data['base']['input_dir']

    def get_output_dir(self) -> str:
        output_dir = self._data['base']['output_dir']
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        return output_dir

    def get_quality(self) -> int:
        return self._data['base']['quality']

    def get_alternative_font(self) -> ImageFont.FreeTypeFont:
        return self._load_font(self._data['base']['alternative_font'], self.get_font_size())

    def get_alternative_bold_font(self) -> ImageFont.FreeTypeFont:
        return self._load_font(self._data['base']['alternative_bold_font'], self.get_bold_font_size())

    def get_font(self) -> ImageFont.FreeTypeFont:
        return self._load_font(self._data['base']['font'], self.get_font_size())

    def get_bold_font(self) -> ImageFont.FreeTypeFont:
        return self._load_font(self._data['base']['bold_font'], self.get_bold_font_size())

    def get_font_size(self) -> int:
        font_size = self._data['base']['font_size']
        return _FONT_SIZE_MAP.get(font_size, 240)

    def get_bold_font_size(self) -> int:
        font_size = self._data['base']['bold_font_size']
        return _BOLD_FONT_SIZE_MAP.get(font_size, 260)

    def get_font_padding_level(self) -> int:
        bold_font_size = self._data['base']['bold_font_size'] if 1 <= self._data['base']['bold_font_size'] <= 3 else 1
        font_size = self._data['base']['font_size'] if 1 <= self._data['base']['font_size'] <= 3 else 1
        return bold_font_size + font_size

    def save(self) -> None:
        with open(self._path, 'w', encoding='utf-8') as f:
            yaml.dump(self._data, f, encoding='utf-8')

    # ---------- Shadow ----------

    def enable_shadow(self) -> None:
        self._data['global']['shadow']['enable'] = True

    def disable_shadow(self) -> None:
        self._data['global']['shadow']['enable'] = False

    def has_shadow_enabled(self) -> bool:
        return self._data['global']['shadow']['enable']

    # ---------- White Margin ----------

    def has_white_margin_enabled(self) -> bool:
        return self._data['global']['white_margin']['enable']

    def enable_white_margin(self) -> None:
        self._data['global']['white_margin']['enable'] = True

    def disable_white_margin(self) -> None:
        self._data['global']['white_margin']['enable'] = False

    def get_white_margin_width(self) -> int:
        white_margin_width = self._data['global']['white_margin']['width']
        return max(0, min(30, white_margin_width))

    # ---------- Focal Length ----------

    def enable_equivalent_focal_length(self) -> None:
        self._data['global']['focal_length']['use_equivalent_focal_length'] = True

    def disable_equivalent_focal_length(self) -> None:
        self._data['global']['focal_length']['use_equivalent_focal_length'] = False

    def use_equivalent_focal_length(self) -> bool:
        return self._data['global']['focal_length']['use_equivalent_focal_length']

    # ---------- Padding with Original Ratio ----------

    def enable_padding_with_original_ratio(self) -> None:
        self._data['global']['padding_with_original_ratio']['enable'] = True

    def disable_padding_with_original_ratio(self) -> None:
        self._data['global']['padding_with_original_ratio']['enable'] = False

    def has_padding_with_original_ratio_enabled(self) -> bool:
        return self._data['global']['padding_with_original_ratio']['enable']

    # ---------- Uniform Resize ----------

    def enable_uniform_resize(self) -> None:
        self._data['global']['uniform_resize']['enable'] = True

    def disable_uniform_resize(self) -> None:
        self._data['global']['uniform_resize']['enable'] = False

    def has_uniform_resize_enabled(self) -> bool:
        return self._data['global']['uniform_resize']['enable']

    def get_uniform_resize_width(self) -> int:
        width = self._data['global']['uniform_resize']['width']
        if not isinstance(width, int) or width <= 0:
            return 1080
        return width

    def get_uniform_resize_height(self) -> int:
        height = self._data['global']['uniform_resize']['height']
        if not isinstance(height, int) or height <= 0:
            return 1350
        return height

    def get_uniform_resize_mode(self) -> str:
        mode = self._data['global']['uniform_resize']['mode']
        if mode not in ('padding', 'crop', 'stretch', 'smart'):
            return 'padding'
        return mode

    def set_uniform_resize_mode(self, mode: str) -> None:
        if mode in ('padding', 'crop', 'stretch', 'smart'):
            self._data['global']['uniform_resize']['mode'] = mode

    def set_uniform_resize_size(self) -> None:
        current_width = self.get_uniform_resize_width()
        current_height = self.get_uniform_resize_height()

        width_input = input(f'输入统一尺寸宽度（当前：{current_width}）\n').strip()
        while not width_input.isdigit() or int(width_input) <= 0:
            width_input = input('请输入大于 0 的整数宽度\n').strip()

        height_input = input(f'输入统一尺寸高度（当前：{current_height}）\n').strip()
        while not height_input.isdigit() or int(height_input) <= 0:
            height_input = input('请输入大于 0 的整数高度\n').strip()

        self._data['global']['uniform_resize']['width'] = int(width_input)
        self._data['global']['uniform_resize']['height'] = int(height_input)

    # ---------- Privacy / EXIF ----------

    def enable_strip_gps(self) -> None:
        self._data['global']['privacy']['strip_gps'] = True

    def disable_strip_gps(self) -> None:
        self._data['global']['privacy']['strip_gps'] = False

    def has_strip_gps_enabled(self) -> bool:
        return self._data['global']['privacy']['strip_gps']

    def enable_strip_all_exif(self) -> None:
        self._data['global']['privacy']['strip_all_exif'] = True

    def disable_strip_all_exif(self) -> None:
        self._data['global']['privacy']['strip_all_exif'] = False

    def has_strip_all_exif_enabled(self) -> bool:
        return self._data['global']['privacy']['strip_all_exif']

    # ---------- Color / Filter / Auto Enhance ----------

    def get_color_filter(self) -> str:
        return self._data['global']['color'].get('filter', 'none')

    def set_color_filter(self, name: str) -> None:
        self._data['global']['color']['filter'] = name

    def get_color_adjustments(self) -> dict:
        color = self._data['global']['color']
        return {
            'brightness': float(color.get('brightness', 1.0)),
            'contrast': float(color.get('contrast', 1.0)),
            'saturation': float(color.get('saturation', 1.0)),
            'sharpness': float(color.get('sharpness', 1.0)),
            'temperature': int(color.get('temperature', 0)),
        }

    def set_color_adjustment(self, key: str, value) -> None:
        if key in ('brightness', 'contrast', 'saturation', 'sharpness'):
            self._data['global']['color'][key] = float(value)
        elif key == 'temperature':
            self._data['global']['color'][key] = int(value)

    def has_auto_contrast_enabled(self) -> bool:
        return self._data['global']['color'].get('auto_contrast', False)

    def enable_auto_contrast(self) -> None:
        self._data['global']['color']['auto_contrast'] = True

    def disable_auto_contrast(self) -> None:
        self._data['global']['color']['auto_contrast'] = False

    def has_color_processing_enabled(self) -> bool:
        """是否有任何调色/增强需要执行（用于决定是否加入处理器）。"""
        color = self._data['global']['color']
        if color.get('filter', 'none') != 'none':
            return True
        if color.get('auto_contrast', False):
            return True
        adjustments = self.get_color_adjustments()
        if abs(adjustments['brightness'] - 1.0) > 1e-3:
            return True
        if abs(adjustments['contrast'] - 1.0) > 1e-3:
            return True
        if abs(adjustments['saturation'] - 1.0) > 1e-3:
            return True
        if abs(adjustments['sharpness'] - 1.0) > 1e-3:
            return True
        if adjustments['temperature'] != 0:
            return True
        return False

    # ---------- Text Watermark（防盗水印） ----------

    def has_text_watermark_enabled(self) -> bool:
        tw = self._data['global']['text_watermark']
        return tw.get('enable', False) and bool(str(tw.get('text', '')).strip())

    def enable_text_watermark(self) -> None:
        self._data['global']['text_watermark']['enable'] = True

    def disable_text_watermark(self) -> None:
        self._data['global']['text_watermark']['enable'] = False

    def get_text_watermark(self) -> dict:
        tw = self._data['global']['text_watermark']
        return {
            'text': str(tw.get('text', '')),
            'tiled': bool(tw.get('tiled', True)),
            'opacity': float(tw.get('opacity', 0.15)),
            'color': str(tw.get('color', '#ffffff')),
            'position': str(tw.get('position', 'bottom_right')),
        }

    def set_text_watermark_text(self, text: str) -> None:
        self._data['global']['text_watermark']['text'] = text

    def set_text_watermark_tiled(self, tiled: bool) -> None:
        self._data['global']['text_watermark']['tiled'] = bool(tiled)

    def set_text_watermark_text_interactive(self) -> None:
        """CLI 交互：输入防盗水印文字。"""
        current = self._data['global']['text_watermark'].get('text', '')
        user_input = input(f'输入防盗水印文字，如 @你的昵称（当前：{current}）\n').strip()
        self._data['global']['text_watermark']['text'] = user_input

    # ---------- Batch Style（批次内多样式：封面单独样式） ----------

    def has_cover_separate_enabled(self) -> bool:
        return self._data['global']['batch_style'].get('cover_separate', False)

    def enable_cover_separate(self) -> None:
        self._data['global']['batch_style']['cover_separate'] = True

    def disable_cover_separate(self) -> None:
        self._data['global']['batch_style']['cover_separate'] = False

    def get_cover_index(self) -> int:
        idx = self._data['global']['batch_style'].get('cover_index', 0)
        if not isinstance(idx, int) or idx < 0:
            return 0
        return idx

    def set_cover_index(self, idx: int) -> None:
        if isinstance(idx, int) and idx >= 0:
            self._data['global']['batch_style']['cover_index'] = idx

    # ---------- Layout ----------

    def set_layout(self, layout: str) -> None:
        self._data['layout']['type'] = layout

    def get_background_color(self) -> str:
        return self._data['layout'].get('background_color', '#ffffff')

    def enable_logo(self) -> None:
        self._data['layout']['logo_enable'] = True

    def disable_logo(self) -> None:
        self._data['layout']['logo_enable'] = False

    def has_logo_enabled(self) -> bool:
        return self._data['layout']['logo_enable']

    def is_logo_left(self) -> bool:
        return self._data['layout']['logo_position'] == 'left'

    def set_logo_left(self) -> None:
        self._data['layout']['logo_position'] = 'left'

    def set_logo_right(self) -> None:
        self._data['layout']['logo_position'] = 'right'

    def get_layout_type(self) -> str:
        return self._data['layout']['type']

    # ---------- Elements ----------

    def get_left_top(self) -> ElementConfig:
        return self._left_top

    def get_left_bottom(self) -> ElementConfig:
        return self._left_bottom

    def get_right_top(self) -> ElementConfig:
        return self._right_top

    def get_right_bottom(self) -> ElementConfig:
        return self._right_bottom

    def get_custom_value(self, location: str) -> str:
        return self._data['layout']['elements'][location].get('value', '')

    def set_custom(self, location: str) -> None:
        self._data['layout']['elements'][location]['name'] = 'Custom'
        user_input = input('输入自定义字段的值（上次使用的值为：{}）\n'.format(self.get_custom_value(location)))
        self._data['layout']['elements'][location]['value'] = user_input

    def set_element_name(self, location: str, name: str) -> None:
        if CUSTOM_VALUE == name:
            self.set_custom(location)
        else:
            self._data['layout']['elements'][location]['name'] = name

    def set_default_logo_path(self, logo_path: str) -> None:
        self._data["logo"]['default']['path'] = logo_path
        self.save()
