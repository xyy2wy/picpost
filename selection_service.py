"""
选片服务：缩略图生成、选片状态模型、按条件过滤、导出选中。

这些函数都是无副作用的纯逻辑，方便在 CLI 与 Web 前端复用：
- 不修改、不提前关闭外部传入的对象；
- 读取失败的图片跳过而非中断整个流程（需求 1.7）；
- 缩略图基于下采样，避免逐张加载原图全尺寸（需求 1.6）；
- 导出统一走 xiaohongshu_service.save_jpg，保留关闭色度子采样的细节策略。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image

from xiaohongshu_service import DEFAULT_OUTPUT_QUALITY
from xiaohongshu_service import save_jpg


class SelectionError(Exception):
    """选片相关操作失败，携带面向用户的友好中文提示。"""


@dataclass
class SelectionItem:
    """单张图片的选片状态。"""

    path: Path
    selected: bool = False
    stars: int = 0  # 0–5，0 表示未评分

    def __post_init__(self) -> None:
        # 统一成 Path，并把星级夹到 [0, 5]。
        self.path = Path(self.path)
        self.stars = max(0, min(int(self.stars), 5))


def make_thumbnail(image_path: Path, max_side: int = 480) -> Image.Image:
    """
    读取并下采样为缩略图，返回新图，保证最长边不超过 max_side。

    仅缩小不放大（原图本身比 max_side 还小时按原尺寸返回）。
    读取失败抛 SelectionError，调用方可据此跳过该图。
    """
    path = Path(image_path)
    max_side = max(1, int(max_side))
    try:
        with Image.open(path) as img:
            img.load()
            thumb = img.copy()
    except Exception as exc:  # 文件损坏 / 不是图片 / 权限等
        raise SelectionError(f"无法读取图片：{path}（{exc}）") from exc

    # thumbnail 原地等比缩放，且不会放大小图。
    thumb.thumbnail((max_side, max_side), Image.LANCZOS)
    return thumb


def build_selection(paths: Iterable[Path]) -> list[SelectionItem]:
    """
    构建选片模型；读取失败（损坏 / 非图片）的图自动跳过，不中断流程（需求 1.7）。
    """
    items: list[SelectionItem] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            with Image.open(path) as img:
                img.verify()  # 轻量校验完整性，不做完整解码
        except Exception:
            continue
        items.append(SelectionItem(path=path))
    return items


def filter_items(items: Iterable[SelectionItem], only_selected: bool = False,
                 min_stars: int = 0) -> list[SelectionItem]:
    """
    按“仅看已选中”与“最低星级”过滤，保持原有顺序。

    结果是输入的子集：当 min_stars=k 时，结果恰为 stars >= k 的项（满足 Property 9）。
    """
    min_stars = int(min_stars)
    result: list[SelectionItem] = []
    for item in items:
        if only_selected and not item.selected:
            continue
        if item.stars < min_stars:
            continue
        result.append(item)
    return result


def export_selected(items: Iterable[SelectionItem], out_dir: Path,
                    add_index_prefix: bool = True,
                    quality: int = DEFAULT_OUTPUT_QUALITY) -> list[Path]:
    """
    把选中项按当前顺序导出到 out_dir，复用 save_jpg（转 RGB + 关闭子采样 + 建目录）。

    add_index_prefix=True 时按顺序加 01_/02_... 前缀，保证上传顺序；
    单张读取失败时跳过该图并继续（需求 1.7）。返回成功导出的目标路径列表（顺序一致）。
    """
    out_dir = Path(out_dir)
    selected = [item for item in items if item.selected]
    # 至少 2 位数（01_、02_…），数量超过 99 时自动加宽。
    pad_width = max(2, len(str(len(selected))))

    exported: list[Path] = []
    for index, item in enumerate(selected, start=1):
        if add_index_prefix:
            name = f"{index:0{pad_width}d}_{item.path.stem}.jpg"
        else:
            name = f"{item.path.stem}.jpg"
        target = out_dir / name
        try:
            with Image.open(item.path) as img:
                img.load()
                save_jpg(img, target, quality=quality)
        except Exception:
            # 读取/保存失败的图跳过，不中断整批导出。
            continue
        exported.append(target)
    return exported
