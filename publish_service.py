"""
发布草稿打包服务：把“处理好的图片 + 文案 + 标签”打包成一个发布草稿。

衔接实际发布动作（需求 10）：
- 图片按 ``01_``/``02_`` 顺序前缀复制，保证上传顺序（验收 10.2、Property 8）；
- 写一个 ``caption.txt``（标题 / 正文 / #标签），无文案时仅含占位（验收 10.3）；
- 产物可以是草稿目录，也可以是 ZIP（验收 10.4）；
- 输出沿用 ``output_xiaohongshu/draft`` 约定，由调用方传入 ``out_dir``（验收 10.5）。

设计原则与其他 service 一致：
- 纯逻辑、可在不联网 / 不依赖重库的情况下单独验证；
- 复制已处理好的成图，不再重新编码（成图已是最终像素）；
- 单张源图缺失 / 不可读时跳过，但保持成功图片的顺序与连续编号，绝不中断整批打包。
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Optional


# 默认的草稿子目录名；具体落点（如 output_xiaohongshu/draft）由调用方决定。
DEFAULT_DRAFT_DIR_NAME = "draft"

# 无文案时写入 caption.txt 的占位文本，保证文件始终存在（验收 10.3）。
EMPTY_CAPTION_PLACEHOLDER = "（暂无文案）"


class PublishError(Exception):
    """发布草稿打包失败时抛出，携带面向用户的友好中文提示。"""


def _format_caption(caption: Optional[dict]) -> str:
    """
    把文案 dict 渲染为发布用纯文本：标题 / 空行 / 正文 / 空行 / 空格连接的 #标签。

    caption 形如 {"title": str, "body": str, "tags": [str, ...]}（同
    ai_preset_service.generate_xiaohongshu_caption 的返回结构）。
    为 None / 空 / 三个字段都为空时，返回占位文本，保证 caption.txt 不为空（验收 10.3）。
    """
    if not isinstance(caption, dict):
        return EMPTY_CAPTION_PLACEHOLDER

    title = str(caption.get("title", "") or "").strip()
    body = str(caption.get("body", "") or "").strip()

    tags_raw = caption.get("tags") or []
    tags: list[str] = []
    if isinstance(tags_raw, (list, tuple)):
        for tag in tags_raw:
            text = str(tag or "").strip().lstrip("#").strip()
            if text:
                tags.append(f"#{text}")
    tag_line = " ".join(tags)

    # 标题、正文、标签三段之间用空行分隔；缺失的段落跳过，避免出现多余空行。
    sections = [section for section in (title, body, tag_line) if section]
    if not sections:
        return EMPTY_CAPTION_PLACEHOLDER
    return "\n\n".join(sections)


def build_publish_draft(image_paths: list[Path], caption: Optional[dict],
                        out_dir: Path, as_zip: bool = False) -> Path:
    """
    生成发布草稿。

    参数:
        image_paths: 已处理好的成图路径，按发布顺序排列。
        caption: 文案 dict（title/body/tags），可为 None / 空。
        out_dir: 草稿目录（调用方决定落点，如 output_xiaohongshu/draft）。
        as_zip: True 时把草稿目录打包为同名 .zip 并返回该 zip 路径；
                False 时返回草稿目录路径。

    行为:
        - 每张图片以顺序前缀 ``01_``/``02_``... 命名（至少 2 位，超过 99 时自动加宽），
          保留原文件名主干与扩展名；前缀严格递增且与输入顺序一致（Property 8）。
        - 写 ``caption.txt``（标题 / 正文 / #标签），无文案时写占位（验收 10.3）。
        - 源图缺失 / 不可读时跳过，但成功图片仍使用连续编号，保证产物可用。

    返回:
        草稿目录路径（as_zip=False）或 zip 文件路径（as_zip=True）。

    异常:
        PublishError: 目录无法创建等硬失败。
    """
    out_dir = Path(out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PublishError(f"无法创建草稿目录：{out_dir}（{exc}）") from exc

    paths = [Path(p) for p in (image_paths or [])]
    # 至少 2 位数（01_、02_…），数量超过 99 时自动加宽到匹配位数。
    pad_width = max(2, len(str(len(paths))))

    copied: list[Path] = []
    index = 1
    for src in paths:
        # 源图缺失 / 不是文件时跳过，但不推进编号，保证成功图片编号连续。
        if not src.is_file():
            continue
        target = out_dir / f"{index:0{pad_width}d}_{src.name}"
        try:
            shutil.copy2(src, target)
        except OSError:
            # 复制失败（权限 / IO 等）跳过该图，保持其余图片连续编号。
            continue
        copied.append(target)
        index += 1

    # 写文案文件：无文案时写占位，保证文件始终存在（验收 10.3）。
    caption_path = out_dir / "caption.txt"
    try:
        caption_path.write_text(_format_caption(caption) + "\n", encoding="utf-8")
    except OSError as exc:
        raise PublishError(f"无法写入文案文件：{caption_path}（{exc}）") from exc

    if not as_zip:
        return out_dir

    # 打包为 zip：放在草稿目录同级，文件名与草稿目录同名。
    zip_path = out_dir.with_suffix(".zip")
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in copied:
                archive.write(item, arcname=item.name)
            archive.write(caption_path, arcname=caption_path.name)
    except OSError as exc:
        raise PublishError(f"无法生成草稿压缩包：{zip_path}（{exc}）") from exc
    return zip_path
