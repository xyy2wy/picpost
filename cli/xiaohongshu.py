"""
小红书多图工具的 CLI 交互处理函数。

这些函数被 init.py 注册为菜单项，对图片文件夹做后处理：
长图切割 / 九宫格、拼图封面、批量页码角标。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from utils_pkg import get_file_list
from services.annotation import ANNOTATION_STYLES
from services.annotation import Annotation
from services.annotation import add_annotations
from services.compose import COMPARISON_LAYOUTS
from services.compose import make_comparison
from services.compose import stack_vertical
from services.publish import PublishError
from services.publish import build_publish_draft
from services.xiaohongshu import BADGE_POSITIONS
from services.xiaohongshu import MULTI_RATIO_SIZES
from services.xiaohongshu import SPLIT_MODES
from services.xiaohongshu import add_page_numbers
from services.xiaohongshu import export_multi_ratio
from services.xiaohongshu import make_collage
from services.xiaohongshu import save_jpg
from services.xiaohongshu import split_image
from services.cover import COVER_SIZES
from services.cover import TITLE_POSITIONS
from services.cover import make_cover


def _prompt_int(prompt: str, default: int, minimum: int = 1) -> int:
    raw = input(f'{prompt}（默认 {default}）\n').strip()
    if not raw:
        return default
    while not raw.isdigit() or int(raw) < minimum:
        raw = input(f'请输入不小于 {minimum} 的整数（默认 {default}）\n').strip()
        if not raw:
            return default
    return int(raw)


def _prompt_folder(config, action_desc: str) -> Path:
    output_dir = config.get_output_dir()
    raw = input(
        f'要{action_desc}的图片文件夹（默认使用 output 目录：{output_dir}）\n'
    ).strip()
    folder = Path(raw) if raw else Path(output_dir)
    return folder


def _list_images(folder: Path) -> list[Path]:
    if not folder.exists():
        print(f'- 文件夹不存在：{folder}')
        return []
    images = sorted(get_file_list(folder), key=lambda p: p.name.lower())
    if not images:
        print(f'- 文件夹中没有图片：{folder}')
    return images


def _choose_option(title: str, options: list[tuple[str, str]], default_index: int = 0) -> str:
    print(title)
    for idx, (label, _) in enumerate(options):
        print(f'  【{idx + 1}】{label}')
    raw = input(f'选择序号（默认 {default_index + 1}）\n').strip()
    if not raw:
        return options[default_index][1]
    while not raw.isdigit() or not (1 <= int(raw) <= len(options)):
        raw = input(f'请输入 1-{len(options)} 之间的序号（默认 {default_index + 1}）\n').strip()
        if not raw:
            return options[default_index][1]
    return options[int(raw) - 1][1]


def _prompt_float(prompt: str, default: float, minimum: float = 0.0,
                  maximum: float = 1.0) -> float:
    raw = input(f'{prompt}（默认 {default}）\n').strip()
    if not raw:
        return default
    while True:
        try:
            value = float(raw)
        except ValueError:
            value = None
        if value is not None and minimum <= value <= maximum:
            return value
        raw = input(f'请输入 {minimum}-{maximum} 之间的数字（默认 {default}）\n').strip()
        if not raw:
            return default


def run_split(config) -> None:
    """长图切割 / 九宫格：把每张图片切成多张，方便轮播发布。"""
    folder = _prompt_folder(config, '切割')
    images = _list_images(folder)
    if not images:
        input('按任意键返回主菜单...')
        return

    mode = _choose_option('选择切割方式：', SPLIT_MODES, default_index=2)

    count = rows = cols = 3
    if mode in ('vertical', 'horizontal'):
        count = _prompt_int('切成几张', 3, minimum=2)
    else:
        rows = _prompt_int('网格行数', 3, minimum=1)
        cols = _prompt_int('网格列数', 3, minimum=1)

    out_dir = folder / 'split'
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for image_path in images:
        with Image.open(image_path) as image:
            image = image.convert('RGB')
            pieces = split_image(image, mode=mode, count=count, rows=rows, cols=cols)
        stem = image_path.stem
        suffix = image_path.suffix if image_path.suffix.lower() in ('.jpg', '.jpeg') else '.jpg'
        for index, piece in enumerate(pieces, start=1):
            target = out_dir / f'{stem}_{index:02d}{suffix}'
            save_jpg(piece, target, quality=config.get_quality())
            piece.close()
            total += 1
        print(f'  o {image_path.name} -> {len(pieces)} 张')

    print(f'o 切割完成，共生成 {total} 张，输出至：{out_dir}')
    input('按任意键返回主菜单...')


def run_collage(config) -> None:
    """拼图封面：把多张图片拼成一张网格封面图。"""
    folder = _prompt_folder(config, '拼图')
    images = _list_images(folder)
    if not images:
        input('按任意键返回主菜单...')
        return

    rows = _prompt_int('拼图行数', 2, minimum=1)
    cols = _prompt_int('拼图列数', 2, minimum=1)
    side = _prompt_int('输出正方形边长（像素）', 1080, minimum=200)
    gap = _prompt_int('图片间距（像素）', 16, minimum=0)

    needed = rows * cols
    loaded = []
    for image_path in images[:needed]:
        with Image.open(image_path) as image:
            loaded.append(image.convert('RGB'))

    if not loaded:
        print('- 没有可用图片。')
        input('按任意键返回主菜单...')
        return

    try:
        collage = make_collage(loaded, rows, cols, output_size=(side, side), gap=gap)
    except ValueError as e:
        print(f'- 拼图失败：{e}')
        for img in loaded:
            img.close()
        input('按任意键返回主菜单...')
        return

    target = folder / 'collage.jpg'
    save_jpg(collage, target, quality=config.get_quality())
    collage.close()
    for img in loaded:
        img.close()

    print(f'o 拼图完成（使用前 {min(needed, len(images))} 张），输出至：{target}')
    input('按任意键返回主菜单...')


def run_page_numbers(config) -> None:
    """批量页码角标：给一组图片打上 1/N、2/N ... 角标，形成系列感。"""
    folder = _prompt_folder(config, '添加页码')
    images = _list_images(folder)
    if not images:
        input('按任意键返回主菜单...')
        return

    position = _choose_option('页码位置：', BADGE_POSITIONS, default_index=0)

    out_dir = folder / 'numbered'
    out_dir.mkdir(parents=True, exist_ok=True)

    loaded = []
    for image_path in images:
        with Image.open(image_path) as image:
            loaded.append(image.convert('RGB'))

    numbered = add_page_numbers(loaded, position=position)
    for image_path, result in zip(images, numbered):
        target = out_dir / image_path.name
        save_jpg(result, target, quality=config.get_quality())
        result.close()
    for img in loaded:
        img.close()

    print(f'o 页码添加完成，共 {len(numbered)} 张，输出至：{out_dir}')
    input('按任意键返回主菜单...')


def run_cover(config) -> None:
    """首图文字卡片：生成带标题的封面图。"""
    title = input('输入封面大标题\n').strip()
    if not title:
        print('- 标题不能为空。')
        input('按任意键返回主菜单...')
        return
    subtitle = input('输入副标题（可留空）\n').strip()

    size_label = _choose_option(
        '封面尺寸：',
        [(label, label) for label in COVER_SIZES.keys()],
        default_index=0,
    )
    size = COVER_SIZES[size_label]
    position = _choose_option('标题位置：', TITLE_POSITIONS, default_index=0)

    use_bg = input('是否用一张图片作为背景？(y/N)\n').strip().lower() == 'y'
    background_image = None
    bg_path = None
    if use_bg:
        folder = _prompt_folder(config, '取背景图的')
        images = _list_images(folder)
        if images:
            bg_path = images[0]
            print(f'- 使用第一张图片作为背景：{bg_path.name}')

    out_dir = Path(config.get_output_dir())
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / 'cover.jpg'

    if bg_path is not None:
        with Image.open(bg_path) as bg:
            background_image = bg.convert('RGB')
            cover = make_cover(title, subtitle, size=size,
                               background_image=background_image, position=position)
            background_image.close()
    else:
        cover = make_cover(title, subtitle, size=size, position=position)

    save_jpg(cover, target, quality=config.get_quality())
    cover.close()
    print(f'o 封面已生成，输出至：{target}')
    input('按任意键返回主菜单...')


def run_multi_ratio(config) -> None:
    """一键多比例导出：把每张图导出成多个常用比例。"""
    folder = _prompt_folder(config, '多比例导出')
    images = _list_images(folder)
    if not images:
        input('按任意键返回主菜单...')
        return

    print('将导出以下全部比例：')
    for label in MULTI_RATIO_SIZES:
        print(f'  - {label}')
    sizes = list(MULTI_RATIO_SIZES.values())

    out_dir = folder / 'multi_ratio'
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for image_path in images:
        with Image.open(image_path) as image:
            image = image.convert('RGB')
            exported = export_multi_ratio(image, sizes, mode='crop')
        stem = image_path.stem
        for (w, h), out_img in exported:
            target = out_dir / f'{stem}_{w}x{h}.jpg'
            save_jpg(out_img, target, quality=config.get_quality())
            out_img.close()
            total += 1
        print(f'  o {image_path.name} -> {len(exported)} 个比例')

    print(f'o 多比例导出完成，共生成 {total} 张，输出至：{out_dir}')
    input('按任意键返回主菜单...')


# 标注样式的中文标签，供 CLI 选择菜单使用
_ANNOTATION_STYLE_OPTIONS = [
    ('气泡（圆角底 + 文字）', 'bubble'),
    ('纯文字（描边）', 'plain'),
    ('价格标签（高亮底）', 'price'),
]

# 对比图布局的中文标签
_COMPARISON_LAYOUT_OPTIONS = [
    ('左右', 'lr'),
    ('上下', 'tb'),
]

# 拼接模式
_COMPOSE_MODE_OPTIONS = [
    ('长图拼接（多张竖向拼成一张长图）', 'stack'),
    ('前后对比（两张拼成左右 / 上下对比图）', 'comparison'),
]


def run_annotate(config) -> None:
    """图上标注：给文件夹内每张图片添加一条文字贴纸（气泡 / 纯文字 / 价格标签）。"""
    folder = _prompt_folder(config, '标注')
    images = _list_images(folder)
    if not images:
        input('按任意键返回主菜单...')
        return

    text = input('输入标注文字\n').strip()
    if not text:
        print('- 标注文字不能为空。')
        input('按任意键返回主菜单...')
        return

    style = _choose_option('选择标注样式：', _ANNOTATION_STYLE_OPTIONS, default_index=0)
    if style not in ANNOTATION_STYLES:
        style = 'bubble'
    x = _prompt_float('水平位置（0=最左，1=最右）', 0.1, minimum=0.0, maximum=1.0)
    y = _prompt_float('垂直位置（0=最上，1=最下）', 0.1, minimum=0.0, maximum=1.0)

    out_dir = folder / 'annotated'
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for image_path in images:
        annotation = Annotation(text=text, x=x, y=y, style=style)
        with Image.open(image_path) as image:
            image = image.convert('RGB')
            result = add_annotations(image, [annotation])
        target = out_dir / f'{image_path.stem}_annotated.jpg'
        save_jpg(result, target, quality=config.get_quality())
        result.close()
        total += 1
        print(f'  o {image_path.name} -> {target.name}')

    print(f'o 标注完成，共 {total} 张，输出至：{out_dir}')
    input('按任意键返回主菜单...')


def run_compose(config) -> None:
    """长图拼接 / 前后对比：把文件夹内图片竖向拼成长图，或取前两张做对比图。"""
    folder = _prompt_folder(config, '拼接')
    images = _list_images(folder)
    if not images:
        input('按任意键返回主菜单...')
        return

    mode = _choose_option('选择拼接模式：', _COMPOSE_MODE_OPTIONS, default_index=0)

    if len(images) < 2:
        print('- 拼接 / 对比至少需要 2 张图片。')
        input('按任意键返回主菜单...')
        return

    out_dir = folder / 'composed'
    out_dir.mkdir(parents=True, exist_ok=True)

    if mode == 'stack':
        gap = _prompt_int('图片间距（像素）', 0, minimum=0)
        loaded = []
        for image_path in images:
            with Image.open(image_path) as image:
                loaded.append(image.convert('RGB'))
        try:
            result = stack_vertical(loaded, gap=gap, bg_color='white')
        except ValueError as e:
            print(f'- 拼接失败：{e}')
            for img in loaded:
                img.close()
            input('按任意键返回主菜单...')
            return
        target = out_dir / 'stack.jpg'
        save_jpg(result, target, quality=config.get_quality())
        result.close()
        for img in loaded:
            img.close()
        print(f'o 长图拼接完成（使用 {len(loaded)} 张），输出至：{target}')
    else:
        layout = _choose_option('对比布局：', _COMPARISON_LAYOUT_OPTIONS, default_index=0)
        if layout not in COMPARISON_LAYOUTS:
            layout = 'lr'
        with Image.open(images[0]) as img_a_src:
            img_a = img_a_src.convert('RGB')
        with Image.open(images[1]) as img_b_src:
            img_b = img_b_src.convert('RGB')
        try:
            result = make_comparison(img_a, img_b, layout=layout,
                                     labels=('Before', 'After'))
        except ValueError as e:
            print(f'- 对比图生成失败：{e}')
            img_a.close()
            img_b.close()
            input('按任意键返回主菜单...')
            return
        target = out_dir / 'comparison.jpg'
        save_jpg(result, target, quality=config.get_quality())
        result.close()
        img_a.close()
        img_b.close()
        print(f'o 对比图完成（使用 {images[0].name} 与 {images[1].name}），输出至：{target}')

    input('按任意键返回主菜单...')


def run_publish_draft(config) -> None:
    """打包发布草稿：把一个文件夹里的成图按顺序命名，连同文案打包成草稿（目录或 ZIP）。"""
    folder = _prompt_folder(config, '打包发布草稿')
    images = _list_images(folder)
    if not images:
        input('按任意键返回主菜单...')
        return

    # 文案为可选输入：留空则只打包图片，caption.txt 写占位（需求 10.3）。
    print('（以下文案均可留空，留空将只打包图片）')
    title = input('文案标题\n').strip()
    body = input('文案正文\n').strip()
    tags_raw = input('话题标签（用空格或逗号分隔，可不带 #）\n').strip()
    tags = [t.strip() for t in tags_raw.replace(',', ' ').replace('，', ' ').split() if t.strip()]

    caption = None
    if title or body or tags:
        caption = {'title': title, 'body': body, 'tags': tags}

    as_zip = input('打包为 ZIP？(y/N)\n').strip().lower() == 'y'

    # 输出沿用 output_xiaohongshu/draft 约定（需求 10.5）。
    out_dir = Path(config.get_output_dir()).parent / 'output_xiaohongshu' / 'draft'

    try:
        result_path = build_publish_draft(images, caption, out_dir, as_zip=as_zip)
    except PublishError as e:
        print(f'- 打包失败：{e}')
        input('按任意键返回主菜单...')
        return

    print(f'o 发布草稿已生成（共 {len(images)} 张），输出至：{result_path}')
    input('按任意键返回主菜单...')
