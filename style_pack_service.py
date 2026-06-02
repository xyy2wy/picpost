"""
风格包（Style Pack）服务：schema 定义、默认补齐、序列化 / 反序列化、合法性校验。

风格包是现有 web_app.build_preset_snapshot() 的超集，额外覆盖调色 / 滤镜与防盗
水印维度，并带版本号，可命名、可复用、可导入导出（需求 3）。

这是纯逻辑模块，仅依赖标准库（json / copy），不依赖 PIL，方便在 CLI 与 Web
前端复用，也方便在不联网、不依赖重库的情况下单独验证：
- normalize_style_pack 用安全默认值补齐缺失字段，并对非法值优雅回退（需求 3.6）；
- serialize / deserialize 提供可分享的 JSON 文件格式（需求 3.3、3.4）；
- is_legacy_preset 识别旧 custom_presets.json 条目以便迁移（需求 3.7）。
"""
from __future__ import annotations

import copy
import json
from typing import Any


STYLE_PACK_VERSION = 1

# 四角文字位置，取值与 enums/constant.py 的 LOCATION_* 一致；此处以字面量定义，
# 保持本模块零重依赖（不引入 PIL / 处理链）。
_ELEMENT_LOCATIONS = ("left_top", "left_bottom", "right_top", "right_bottom")


def _default_elements() -> dict[str, dict[str, str]]:
    """构建四角文字的默认快照（每个位置一份新 dict，避免共享可变状态）。"""
    return {location: {"name": "None"} for location in _ELEMENT_LOCATIONS}


# 字段 -> 默认值；normalize_style_pack 以此补齐缺失字段并校验类型。
# 前半部分为 build_preset_snapshot 的字段（布局 / Logo / 白边 / 阴影 / 统一尺寸 /
# 质量 / 四角文字），后半部分为新增的调色 / 滤镜与防盗水印维度。
STYLE_PACK_FIELDS: dict[str, Any] = {
    # ---- 布局 / Logo / 边框 / 尺寸（build_preset_snapshot 子集）----
    "layout_type": "watermark_right_logo",
    "logo_enable": False,
    "logo_position": "left",
    "white_margin": False,
    "white_margin_width": 3,
    "shadow_enable": False,
    "equivalent_focal": False,
    "original_ratio_padding": False,
    "uniform_enable": False,
    "uniform_mode": "padding",
    "uniform_width": 1080,
    "uniform_height": 1440,
    "quality": 100,
    # ---- 调色 / 滤镜（新增维度）----
    "color_filter": "none",
    "color_brightness": 1.0,
    "color_contrast": 1.0,
    "color_saturation": 1.0,
    "color_sharpness": 1.0,
    "color_temperature": 0,
    "auto_contrast": False,
    # ---- 防盗水印（新增维度）----
    "tw_enable": False,
    "tw_text": "",
    "tw_tiled": True,
    "tw_opacity": 0.15,
    # ---- 四角文字 ----
    "elements": _default_elements(),
}


class StylePackError(Exception):
    """风格包相关操作失败，携带面向用户的友好中文提示。"""


def _coerce_bool(value: Any, default: bool) -> bool:
    """仅接受真正的布尔值，其余一律回退默认值（避免把 0/1/字符串误判）。"""
    if isinstance(value, bool):
        return value
    return default


def _coerce_int(value: Any, default: int) -> int:
    """把数值 / 数值字符串转 int；布尔或非法值回退默认值，绝不抛错。"""
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    """把数值 / 数值字符串转 float；布尔或非法值回退默认值，绝不抛错。"""
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any, default: str) -> str:
    """仅接受字符串，其余一律回退默认值（不把数字隐式转成字符串）。"""
    if isinstance(value, str):
        return value
    return default


def _normalize_elements(value: Any) -> dict[str, dict[str, str]]:
    """
    归一化四角文字：只保留四个标准位置，缺失或非法的位置回退为 {"name": "None"}。
    每个位置保留 name（字符串），当 name == "Custom" 时保留可选的 value 字符串。
    """
    source = value if isinstance(value, dict) else {}
    result: dict[str, dict[str, str]] = {}
    for location in _ELEMENT_LOCATIONS:
        item = source.get(location)
        if not isinstance(item, dict):
            result[location] = {"name": "None"}
            continue
        name = item.get("name")
        name = name if isinstance(name, str) else "None"
        element: dict[str, str] = {"name": name}
        raw_value = item.get("value")
        if isinstance(raw_value, str):
            element["value"] = raw_value
        result[location] = element
    return result


def normalize_style_pack(data: Any) -> dict[str, Any]:
    """
    返回包含 STYLE_PACK_FIELDS 全部键的快照（需求 3.6 字段补齐）：
    - 缺失字段以默认值补齐；
    - 提供的合法值予以保留；
    - 非法类型（如 quality="abc"）安全回退默认值，绝不抛错；
    - 非 dict 入参直接返回完整默认快照。

    该函数是幂等的：normalize(normalize(x)) == normalize(x)，保证往返一致（需求 3.3）。
    """
    if not isinstance(data, dict):
        return _full_defaults()

    snapshot: dict[str, Any] = {}
    for field, default in STYLE_PACK_FIELDS.items():
        if field == "elements":
            snapshot[field] = _normalize_elements(data.get(field))
            continue

        if field not in data:
            snapshot[field] = copy.deepcopy(default)
            continue

        value = data[field]
        if isinstance(default, bool):
            snapshot[field] = _coerce_bool(value, default)
        elif isinstance(default, int):
            snapshot[field] = _coerce_int(value, default)
        elif isinstance(default, float):
            snapshot[field] = _coerce_float(value, default)
        elif isinstance(default, str):
            snapshot[field] = _coerce_str(value, default)
        else:
            snapshot[field] = copy.deepcopy(value)
    return snapshot


def _full_defaults() -> dict[str, Any]:
    """构建一份完整的默认快照（深拷贝，避免共享可变状态）。"""
    snapshot: dict[str, Any] = {}
    for field, default in STYLE_PACK_FIELDS.items():
        if field == "elements":
            snapshot[field] = _default_elements()
        else:
            snapshot[field] = copy.deepcopy(default)
    return snapshot


def serialize_style_pack(snapshot: dict[str, Any], name: str = "") -> str:
    """
    把快照序列化为可分享的风格包 JSON 字符串（需求 3.3）。

    既接受裸快照，也接受已经是 {"version", "name", "snapshot"} 结构的包；后者会
    复用其中的 name（除非显式传入了非空 name 参数）。输出的 snapshot 总是经过
    normalize_style_pack 补齐 / 校验。
    """
    if isinstance(snapshot, dict) and isinstance(snapshot.get("snapshot"), dict):
        raw_snapshot = snapshot["snapshot"]
        effective_name = name or _coerce_str(snapshot.get("name"), "")
    else:
        raw_snapshot = snapshot
        effective_name = name

    payload = {
        "version": STYLE_PACK_VERSION,
        "name": effective_name,
        "snapshot": normalize_style_pack(raw_snapshot),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def deserialize_style_pack(text: str) -> dict[str, Any]:
    """
    解析风格包 JSON 文本（需求 3.4）。

    非法 JSON、非对象、或缺少 snapshot 字段都抛出 StylePackError（不污染现有数据）；
    合法时返回 {"version", "name", "snapshot"}，其中 snapshot 经 normalize 补齐。
    """
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise StylePackError("风格包文件不是合法的 JSON，无法导入。") from exc

    if not isinstance(parsed, dict):
        raise StylePackError("风格包文件格式不正确（应为一个 JSON 对象）。")

    raw_snapshot = parsed.get("snapshot")
    if not isinstance(raw_snapshot, dict):
        raise StylePackError("风格包文件缺少有效的 snapshot 字段，无法导入。")

    version = parsed.get("version", STYLE_PACK_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        version = STYLE_PACK_VERSION

    return {
        "version": version,
        "name": _coerce_str(parsed.get("name"), ""),
        "snapshot": normalize_style_pack(raw_snapshot),
    }


def is_legacy_preset(data: Any) -> bool:
    """
    判断 data 是否为旧版 custom_presets.json 条目：即一个裸快照
    （没有 version / snapshot 包裹）但带有 layout_type 等快照字段。
    供调用方识别并迁移历史数据（需求 3.7）。
    """
    if not isinstance(data, dict):
        return False
    if "version" in data or "snapshot" in data:
        return False
    return "layout_type" in data
