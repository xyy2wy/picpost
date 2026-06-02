"""
智能裁切：把图片裁切到目标比例时尽量保留主体 / 人脸。

设计原则（对应需求 8 与设计文档「smart_crop_service.py」）：
- **纯 PIL 显著性始终可用**：用「边缘能量重心」估计主体位置，无需任何新依赖，
  保证开箱即可降级使用。
- **OpenCV 人脸检测为可选增强**：通过延迟导入 + 能力探测封装，缺失或失败时优雅降级，
  绝不阻断模块导入或核心流程。
- **绝不抛出**：``smart_crop`` 的任何异常路径都回退到 ``crop_image_to_canvas`` 居中裁切，
  始终产出有效图（Property 3）。
- **不破坏入参**：所有函数返回新的 PIL.Image，不修改、不关闭传入的图片对象（Property 1）。
- **尺寸精确**：``smart_crop`` / ``saliency_crop`` 及全部降级路径的输出尺寸恰为
  ``(target_w, target_h)``（Property 2）。
"""
from __future__ import annotations

import math

from PIL import Image
from PIL import ImageFilter

from utils import crop_image_to_canvas


# 计算显著性重心前，将灰度图下采样到的最大边长（控制纯 Python 计算量）
_EDGE_MAX_SIDE = 256
# 人脸级联文件名（OpenCV 自带）
_FACE_CASCADE_FILE = "haarcascade_frontalface_default.xml"


def is_face_detection_available() -> bool:
    """探测 OpenCV 人脸检测是否可用。

    要求 ``cv2`` 可导入且人脸级联文件可被加载（``empty()`` 为 False）。
    任何失败（未安装、缺级联文件、加载异常）都返回 ``False``，绝不抛出（需求 8.3）。
    """
    try:
        import cv2  # 延迟导入，缺失时不影响模块导入
        import os

        cascade_path = os.path.join(cv2.data.haarcascades, _FACE_CASCADE_FILE)
        if not os.path.exists(cascade_path):
            return False
        classifier = cv2.CascadeClassifier(cascade_path)
        return not classifier.empty()
    except Exception:
        return False


def _crop_around_center(image: Image.Image, target_w: int, target_h: int,
                        cx_frac: float, cy_frac: float) -> Image.Image:
    """以 max-scale 逻辑放大图片后，围绕给定相对锚点裁出目标尺寸窗口。

    - ``scale = max(target_w / w, target_h / h)``，与 ``crop_image_to_canvas`` 一致，
      保证放大后两个维度都不小于目标尺寸。
    - ``cx_frac`` / ``cy_frac`` 为 [0, 1] 的相对锚点（主体重心 / 人脸中心）。
    - 裁切窗口以锚点为中心放置，并夹取到图像边界内。
    - 返回新的 RGB 图，尺寸恰为 ``(target_w, target_h)``；不修改、不关闭入参。
    """
    w, h = image.size
    scale = max(target_w / w, target_h / h)
    # 用 ceil 保证放大后尺寸 >= 目标尺寸，规避取整误差
    scaled_w = max(target_w, int(math.ceil(w * scale)))
    scaled_h = max(target_h, int(math.ceil(h * scale)))

    resized = image.resize((scaled_w, scaled_h), Image.LANCZOS)
    try:
        cx_frac = min(1.0, max(0.0, cx_frac))
        cy_frac = min(1.0, max(0.0, cy_frac))
        center_x = cx_frac * scaled_w
        center_y = cy_frac * scaled_h

        left = int(round(center_x - target_w / 2))
        top = int(round(center_y - target_h / 2))
        # 夹取窗口，保证完全落在放大后的图像内
        left = max(0, min(left, scaled_w - target_w))
        top = max(0, min(top, scaled_h - target_h))

        cropped = resized.crop((left, top, left + target_w, top + target_h))
    finally:
        resized.close()

    rgb = cropped.convert("RGB")
    if rgb is not cropped:
        cropped.close()
    return rgb


def _energy_centroid(image: Image.Image) -> tuple[float, float]:
    """用边缘能量图估计主体相对重心，返回 (cx_frac, cy_frac) ∈ [0, 1]。

    - 转灰度并下采样到 ``_EDGE_MAX_SIDE`` 以内，再做 ``FIND_EDGES`` 得到能量图。
    - 用 BOX 重采样把能量图压成单行 / 单列，得到每列 / 每行的平均能量，
      据此求加权重心（C 层重采样，避免逐像素 Python 循环）。
    - 能量全为 0（纯色图）时回退到正中心 (0.5, 0.5)。
    - 不修改、不关闭入参。
    """
    gray = image.convert("L")
    if gray is image:  # 极端情况下 convert 可能返回自身，复制以免改动入参
        gray = image.copy()
    try:
        gray.thumbnail((_EDGE_MAX_SIDE, _EDGE_MAX_SIDE), Image.BILINEAR)
        edges = gray.filter(ImageFilter.FIND_EDGES)
    finally:
        gray.close()

    try:
        ew, eh = edges.size
        col_line = edges.resize((ew, 1), Image.BOX)
        row_line = edges.resize((1, eh), Image.BOX)
        try:
            col_weights = list(col_line.getdata())
            row_weights = list(row_line.getdata())
        finally:
            col_line.close()
            row_line.close()
    finally:
        edges.close()

    total_col = sum(col_weights)
    if total_col > 0:
        cx = sum(i * v for i, v in enumerate(col_weights)) / total_col
        cx_frac = (cx + 0.5) / ew
    else:
        cx_frac = 0.5

    total_row = sum(row_weights)
    if total_row > 0:
        cy = sum(i * v for i, v in enumerate(row_weights)) / total_row
        cy_frac = (cy + 0.5) / eh
    else:
        cy_frac = 0.5

    return cx_frac, cy_frac


def saliency_crop(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """纯 PIL 显著性裁切：以边缘能量重心为锚点裁出目标尺寸（需求 8.1）。

    - 目标宽 / 高非正时回退居中裁切（与 ``crop_image_to_canvas`` 行为一致）。
    - 返回新的 RGB 图，尺寸恰为 ``(target_w, target_h)``；不修改、不关闭入参（Property 1、2）。
    """
    if target_w <= 0 or target_h <= 0:
        return crop_image_to_canvas(image, target_w, target_h, auto_close=False)

    cx_frac, cy_frac = _energy_centroid(image)
    return _crop_around_center(image, target_w, target_h, cx_frac, cy_frac)


def _detect_faces(image: Image.Image) -> list:
    """用 OpenCV 在副本上做人脸检测，返回人脸框列表 [(x, y, w, h), ...]。

    在原图灰度副本上运行，不修改、不关闭入参。仅在 ``is_face_detection_available()``
    为真时调用；cv2 运行期异常向上抛出，由 ``smart_crop`` 统一兜底。
    """
    import cv2
    import numpy as np
    import os

    gray = np.array(image.convert("L"))
    cascade_path = os.path.join(cv2.data.haarcascades, _FACE_CASCADE_FILE)
    classifier = cv2.CascadeClassifier(cascade_path)
    faces = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                         minSize=(24, 24))
    return [tuple(int(v) for v in face) for face in faces]


def _faces_center_frac(faces: list, w: int, h: int) -> tuple[float, float]:
    """求所有人脸并集包围盒的中心相对坐标 ∈ [0, 1]。"""
    left = min(f[0] for f in faces)
    top = min(f[1] for f in faces)
    right = max(f[0] + f[2] for f in faces)
    bottom = max(f[1] + f[3] for f in faces)
    cx_frac = ((left + right) / 2) / w if w > 0 else 0.5
    cy_frac = ((top + bottom) / 2) / h if h > 0 else 0.5
    return cx_frac, cy_frac


def smart_crop(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """智能裁切：人脸优先 → 显著性 → 居中裁切兜底（需求 8.1–8.6）。

    流程：
    1. ``is_face_detection_available()`` 为真时做人脸检测；检出人脸则把裁切窗口
       对准人脸并集中心（需求 8.2）。
    2. 无人脸或检测不可用 → ``saliency_crop`` 显著性裁切。
    3. 任何环节抛出异常 → ``crop_image_to_canvas`` 居中裁切兜底（需求 8.3、8.4）。

    始终返回新的 RGB 图，尺寸恰为 ``(target_w, target_h)``，不修改 / 不关闭入参，
    且**绝不抛出**（Property 1、2、3）。
    """
    try:
        if target_w <= 0 or target_h <= 0:
            return crop_image_to_canvas(image, target_w, target_h, auto_close=False)

        if is_face_detection_available():
            faces = _detect_faces(image)
            if faces:
                cx_frac, cy_frac = _faces_center_frac(faces, image.width, image.height)
                return _crop_around_center(image, target_w, target_h, cx_frac, cy_frac)

        return saliency_crop(image, target_w, target_h)
    except Exception:
        # 任何异常都回退居中裁切；连兜底也失败时返回纯色画布，确保绝不抛出
        try:
            return crop_image_to_canvas(image, target_w, target_h, auto_close=False)
        except Exception:
            return Image.new("RGB", (max(1, target_w), max(1, target_h)), "white")
