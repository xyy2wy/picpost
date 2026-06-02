"""
配置加载、菜单组装（数据驱动）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from entity.config import Config
from entity.image_processor import (
    BackgroundBlurProcessor,
    BackgroundBlurWithWhiteBorderProcessor,
    CustomWatermarkProcessor,
    DarkWatermarkLeftLogoProcessor,
    DarkWatermarkRightLogoProcessor,
    ProcessorComponent,
    PureWhiteMarginProcessor,
    SimpleProcessor,
    SquareProcessor,
    WatermarkLeftLogoProcessor,
    WatermarkRightLogoProcessor,
)
from entity.menu import Menu, SubMenu, MenuItem
from enums.constant import (
    MODEL_NAME, MODEL_VALUE,
    MAKE_NAME, MAKE_VALUE,
    LENS_NAME, LENS_VALUE,
    PARAM_NAME, PARAM_VALUE,
    DATETIME_NAME, DATETIME_VALUE,
    DATE_NAME, DATE_VALUE,
    CUSTOM_NAME, CUSTOM_VALUE,
    NONE_NAME, NONE_VALUE,
    LENS_MAKE_LENS_MODEL_NAME, LENS_MAKE_LENS_MODEL_VALUE,
    CAMERA_MODEL_LENS_MODEL_NAME, CAMERA_MODEL_LENS_MODEL_VALUE,
    TOTAL_PIXEL_NAME, TOTAL_PIXEL_VALUE,
    CAMERA_MAKE_CAMERA_MODEL_NAME, CAMERA_MAKE_CAMERA_MODEL_VALUE,
    FILENAME_NAME, FILENAME_VALUE,
    DATE_FILENAME_NAME, DATE_FILENAME_VALUE,
    DATETIME_FILENAME_NAME, DATETIME_FILENAME_VALUE,
    GEO_INFO, GEO_INFO_VALUE,
)
from gen_video import generate_video
from xiaohongshu_cli import run_annotate
from xiaohongshu_cli import run_collage
from xiaohongshu_cli import run_compose
from xiaohongshu_cli import run_cover
from xiaohongshu_cli import run_multi_ratio
from xiaohongshu_cli import run_page_numbers
from xiaohongshu_cli import run_publish_draft
from xiaohongshu_cli import run_split
from color_service import FILTER_OPTIONS

# ---------- 日志配置 ----------

Path('./logs').mkdir(parents=True, exist_ok=True)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

info_handler = logging.FileHandler('./logs/info.log', mode='w', encoding='utf-8')
info_handler.setLevel(logging.INFO)
info_handler.setFormatter(formatter)

error_handler = logging.FileHandler('./logs/error.log', mode='w', encoding='utf-8')
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(formatter)

debug_handler = logging.FileHandler('./logs/all.log', mode='w', encoding='utf-8')
debug_handler.setLevel(logging.DEBUG)
debug_handler.setFormatter(formatter)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[debug_handler, info_handler, error_handler],
)

SEPARATE_LINE = '+' + '-' * 15 + '+' + '-' * 15 + '+'


# ---------- 数据定义 ----------

@dataclass
class ElementItem:
    name: str
    value: str


@dataclass
class LayoutItem:
    name: str
    value: str
    processor: ProcessorComponent

    @staticmethod
    def from_processor(processor: ProcessorComponent) -> LayoutItem:
        return LayoutItem(processor.LAYOUT_NAME, processor.LAYOUT_ID, processor)


# 四角可选元素列表
ELEMENT_ITEMS = [
    ElementItem(MODEL_NAME, MODEL_VALUE),
    ElementItem(MAKE_NAME, MAKE_VALUE),
    ElementItem(LENS_NAME, LENS_VALUE),
    ElementItem(PARAM_NAME, PARAM_VALUE),
    ElementItem(DATETIME_NAME, DATETIME_VALUE),
    ElementItem(DATE_NAME, DATE_VALUE),
    ElementItem(CUSTOM_NAME, CUSTOM_VALUE),
    ElementItem(NONE_NAME, NONE_VALUE),
    ElementItem(LENS_MAKE_LENS_MODEL_NAME, LENS_MAKE_LENS_MODEL_VALUE),
    ElementItem(CAMERA_MODEL_LENS_MODEL_NAME, CAMERA_MODEL_LENS_MODEL_VALUE),
    ElementItem(TOTAL_PIXEL_NAME, TOTAL_PIXEL_VALUE),
    ElementItem(CAMERA_MAKE_CAMERA_MODEL_NAME, CAMERA_MAKE_CAMERA_MODEL_VALUE),
    ElementItem(FILENAME_NAME, FILENAME_VALUE),
    ElementItem(DATE_FILENAME_NAME, DATE_FILENAME_VALUE),
    ElementItem(DATETIME_FILENAME_NAME, DATETIME_FILENAME_VALUE),
    ElementItem(GEO_INFO, GEO_INFO_VALUE),
]

# 菜单位置定义
LOCATION_DEFINITIONS = [
    ('left_top', '左上角', lambda x: x['layout']['elements']['left_top']['name']),
    ('left_bottom', '左下角', lambda x: x['layout']['elements']['left_bottom']['name']),
    ('right_top', '右上角', lambda x: x['layout']['elements']['right_top']['name']),
    ('right_bottom', '右下角', lambda x: x['layout']['elements']['right_bottom']['name']),
]


# ---------- 读取配置 ----------

config = Config('config.yaml')


# ---------- 菜单构建（数据驱动） ----------

def _build_layout_menu() -> SubMenu:
    """构建布局子菜单。"""
    layout_processors = [
        WatermarkLeftLogoProcessor(config),
        WatermarkRightLogoProcessor(config),
        DarkWatermarkLeftLogoProcessor(config),
        DarkWatermarkRightLogoProcessor(config),
        CustomWatermarkProcessor(config),
        SquareProcessor(config),
        SimpleProcessor(config),
        BackgroundBlurProcessor(config),
        BackgroundBlurWithWhiteBorderProcessor(config),
        PureWhiteMarginProcessor(config),
    ]

    menu = SubMenu('布局')
    menu.set_value_getter(config, lambda x: x['layout']['type'])
    menu.set_compare_method(lambda x, y: x == y)

    for proc in layout_processors:
        item = MenuItem(proc.LAYOUT_NAME)
        item._value = proc.LAYOUT_ID
        item.set_procedure(config.set_layout, layout=proc.LAYOUT_ID)
        menu.add(item)

    return menu


def _build_toggle_menu(name: str, getter, enable_fn, disable_fn) -> SubMenu:
    """构建启用/不启用的开关子菜单。"""
    menu = SubMenu(name)
    menu.set_value_getter(config, getter)
    menu.set_compare_method(lambda x, y: x == y)

    enable_item = MenuItem('启用')
    enable_item._value = True
    enable_item.set_procedure(enable_fn)
    menu.add(enable_item)

    disable_item = MenuItem('不启用')
    disable_item._value = False
    disable_item.set_procedure(disable_fn)
    menu.add(disable_item)

    return menu


def _build_element_menu(location: str, label: str, getter) -> SubMenu:
    """构建四角文字选择子菜单。"""
    menu = SubMenu(label)
    menu.set_value_getter(config, getter)
    menu.set_compare_method(lambda x, y: x == y)

    for item_def in ELEMENT_ITEMS:
        menu_item = MenuItem(item_def.name)
        menu_item._value = item_def.value
        menu_item.set_procedure(config.set_element_name, location=location, name=item_def.value)
        menu.add(menu_item)

    return menu


def _build_default_logo_menu() -> SubMenu:
    """构建默认 Logo 选择子菜单。"""
    menu = SubMenu('【新选项】设置默认 logo，机身无法匹配时将使用默认 logo（比如大疆）')
    menu.set_value_getter(config, lambda x: x['logo']['default']['path'])
    menu.set_compare_method(lambda x, y: x == y)

    for m in config._makes.values():
        item = MenuItem(m['id'])
        item._value = m['path']
        item.set_procedure(config.set_default_logo_path, logo_path=m['path'])
        menu.add(item)

    return menu


def _build_more_settings_menu() -> SubMenu:
    """构建更多设置子菜单。"""
    menu = SubMenu('更多设置')
    menu.set_value_getter(config, lambda x: None)
    menu.set_compare_method(lambda x, y: False)

    # 白色边框
    menu.add(_build_toggle_menu(
        '白色边框',
        lambda x: x['global']['white_margin']['enable'],
        config.enable_white_margin,
        config.disable_white_margin,
    ))

    # 等效焦距
    menu.add(_build_toggle_menu(
        '等效焦距',
        lambda x: x['global']['focal_length']['use_equivalent_focal_length'],
        config.enable_equivalent_focal_length,
        config.disable_equivalent_focal_length,
    ))

    # 阴影
    menu.add(_build_toggle_menu(
        '阴影',
        lambda x: x['global']['shadow']['enable'],
        config.enable_shadow,
        config.disable_shadow,
    ))

    # 按比例填充
    menu.add(_build_toggle_menu(
        '按比例填充',
        lambda x: x['global']['padding_with_original_ratio']['enable'],
        config.enable_padding_with_original_ratio,
        config.disable_padding_with_original_ratio,
    ))

    # 统一尺寸
    menu.add(_build_toggle_menu(
        '统一尺寸',
        lambda x: x['global']['uniform_resize']['enable'],
        config.enable_uniform_resize,
        config.disable_uniform_resize,
    ))

    # 统一尺寸模式
    mode_menu = SubMenu('统一尺寸模式')
    mode_menu.set_value_getter(config, lambda x: x['global']['uniform_resize']['mode'])
    mode_menu.set_compare_method(lambda x, y: x == y)

    mode_options = [
        ('留白补齐', 'padding'),
        ('居中裁切', 'crop'),
        ('强制拉伸', 'stretch'),
        ('智能裁切', 'smart'),
    ]
    for mode_name, mode_value in mode_options:
        item = MenuItem(mode_name)
        item._value = mode_value
        item.set_procedure(config.set_uniform_resize_mode, mode=mode_value)
        mode_menu.add(item)
    menu.add(mode_menu)

    # 设置统一尺寸分辨率
    size_item = MenuItem('【新功能】设置统一尺寸分辨率')
    size_item.set_procedure(config.set_uniform_resize_size)
    menu.add(size_item)

    # 清除 GPS 拍摄位置（隐私保护，发布到公开平台时建议开启）
    menu.add(_build_toggle_menu(
        '【隐私】清除 GPS 拍摄位置',
        lambda x: x['global']['privacy']['strip_gps'],
        config.enable_strip_gps,
        config.disable_strip_gps,
    ))

    # 清除全部 EXIF 元数据
    menu.add(_build_toggle_menu(
        '【隐私】清除全部 EXIF 元数据',
        lambda x: x['global']['privacy']['strip_all_exif'],
        config.enable_strip_all_exif,
        config.disable_strip_all_exif,
    ))

    # 调色滤镜
    filter_menu = SubMenu('【新功能】调色滤镜')
    filter_menu.set_value_getter(config, lambda x: x['global']['color']['filter'])
    filter_menu.set_compare_method(lambda x, y: x == y)
    for filter_label, filter_value in FILTER_OPTIONS:
        item = MenuItem(filter_label)
        item._value = filter_value
        item.set_procedure(config.set_color_filter, name=filter_value)
        filter_menu.add(item)
    menu.add(filter_menu)

    # 自动对比度
    menu.add(_build_toggle_menu(
        '【新功能】自动对比度增强',
        lambda x: x['global']['color'].get('auto_contrast', False),
        config.enable_auto_contrast,
        config.disable_auto_contrast,
    ))

    # 防盗文字水印开关
    menu.add(_build_toggle_menu(
        '【新功能】防盗文字水印',
        lambda x: x['global']['text_watermark']['enable'],
        config.enable_text_watermark,
        config.disable_text_watermark,
    ))

    # 设置防盗水印文字
    watermark_text_item = MenuItem('【新功能】设置防盗水印文字')
    watermark_text_item.set_procedure(config.set_text_watermark_text_interactive)
    menu.add(watermark_text_item)

    return menu


def help_gen_video():
    """视频生成辅助函数。"""
    if not os.path.exists('help.txt'):
        with open('help.txt', 'w') as f:
            f.write('')
        print('以下提示仅在第一次运行时出现，如果需要重新设置，请删除 help.txt 文件后再次运行')
        print('- 该功能用于将 output 中的图片制作成视频，需要ffmpeg支持，默认在 bin 文件夹中附带')
        print('- 如果需要添加背景音乐，请将音乐文件放在 output 文件夹中，命名为 bgm.mp3')
        gap_time = input('- 请输入一个数字，指定两张图片切换之间的间隔时间，建议 2s：')
        while not gap_time.isdigit():
            gap_time = input('提示：你输入的不是数字，请重新输入：')
        config.set("video_gap_time", int(gap_time))
        config.save()

    generate_video(config.get_output_dir(), config.get_or_default("video_gap_time", 2))
    input("按任意键返回主菜单...")


def _build_xiaohongshu_menu() -> SubMenu:
    """构建小红书多图工具子菜单（对图片文件夹做后处理）。"""
    menu = SubMenu('【新功能】小红书多图工具')
    menu.set_value_getter(config, lambda x: None)
    menu.set_compare_method(lambda x, y: False)

    split_item = MenuItem('长图切割 / 九宫格（切成多张轮播图）')
    split_item.set_procedure(run_split, config=config)
    menu.add(split_item)

    collage_item = MenuItem('拼图封面（多张拼成一张网格封面）')
    collage_item.set_procedure(run_collage, config=config)
    menu.add(collage_item)

    page_item = MenuItem('批量页码角标（打上 1/N、2/N…）')
    page_item.set_procedure(run_page_numbers, config=config)
    menu.add(page_item)

    cover_item = MenuItem('首图文字卡片（生成带标题的封面）')
    cover_item.set_procedure(run_cover, config=config)
    menu.add(cover_item)

    multi_ratio_item = MenuItem('一键多比例导出（3:4 / 1:1 / 9:16 / 4:3）')
    multi_ratio_item.set_procedure(run_multi_ratio, config=config)
    menu.add(multi_ratio_item)

    annotate_item = MenuItem('图上标注（文字贴纸）')
    annotate_item.set_procedure(run_annotate, config=config)
    menu.add(annotate_item)

    compose_item = MenuItem('长图拼接 / 前后对比')
    compose_item.set_procedure(run_compose, config=config)
    menu.add(compose_item)

    publish_item = MenuItem('打包发布草稿（图片 + 文案打包成草稿目录 / ZIP）')
    publish_item.set_procedure(run_publish_draft, config=config)
    menu.add(publish_item)

    return menu


def build_root_menu() -> Menu:
    """构建完整的主菜单树。"""
    root = Menu('【Semi-Utils】\n    当前设置')

    # 布局
    root.add(_build_layout_menu())

    # Logo 开关
    root.add(_build_toggle_menu(
        'logo',
        lambda x: x['layout']['logo_enable'],
        config.enable_logo,
        config.disable_logo,
    ))

    # 四角文字
    for location, label, getter in LOCATION_DEFINITIONS:
        root.add(_build_element_menu(location, label, getter))

    # 制作视频
    video_item = MenuItem('【新功能】制作视频')
    video_item.set_procedure(help_gen_video)
    root.add(video_item)

    # 小红书多图工具
    root.add(_build_xiaohongshu_menu())

    # 默认 Logo
    root.add(_build_default_logo_menu())

    # 更多设置
    root.add(_build_more_settings_menu())

    return root


# 构建菜单
root_menu = build_root_menu()
root_menu.set_parent(root_menu)
