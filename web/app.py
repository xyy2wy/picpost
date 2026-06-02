from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st
from PIL import Image

from ai.preset import suggest_preset_from_image
from ai.preset import generate_xiaohongshu_caption
from ai.preset import AIServiceError
from ai.preset import is_openai_available
from ai.preset import AI_PROVIDERS
from ai.preset import CUSTOM_MODEL_VALUE
from ai.preset import DEFAULT_MODEL
from ai.preset import DEFAULT_PROVIDER_ID
from ai.preset import estimate_usage
from ai.preset import get_provider
from ai.preset import resolve_base_url
from ai.preset import resolve_model
from ai.preset import select_best_images
from ai.preset import suggest_style_and_tags
from services.annotation import Annotation
from services.annotation import add_annotations
from services.compose import make_comparison
from services.compose import stack_vertical
from services.smart_crop import smart_crop
from services.selection import build_selection
from services.selection import export_selected
from services.selection import filter_items
from services.selection import make_thumbnail
from services.selection import SelectionError
from ai.style_pack import serialize_style_pack
from ai.style_pack import deserialize_style_pack
from ai.style_pack import normalize_style_pack
from ai.style_pack import StylePackError
from web.preview import render_preview
from web.preview import PreviewError
from services.processing import ELEMENT_LOCATIONS
from services.processing import ELEMENT_OPTIONS
from services.processing import LAYOUT_OPTIONS
from services.processing import ProcessResult
from services.processing import UNIFORM_RESIZE_MODES
from services.processing import build_runtime_config
from services.processing import list_input_images
from services.processing import process_images
from services.processing import process_images_with_cover
from services.publish import build_publish_draft
from services.publish import PublishError
from services.xiaohongshu import BADGE_POSITIONS
from services.xiaohongshu import SPLIT_MODES
from services.xiaohongshu import add_page_numbers
from services.xiaohongshu import make_collage
from services.xiaohongshu import save_jpg
from services.xiaohongshu import split_image
from services.xiaohongshu import export_multi_ratio
from services.xiaohongshu import MULTI_RATIO_SIZES
from services.color import FILTER_OPTIONS
from services.cover import make_cover
from services.cover import COVER_SIZES
from services.cover import TITLE_POSITIONS


ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.yaml"
APP_DATA_DIR = ROOT_DIR / "app_data"
CUSTOM_PRESETS_PATH = APP_DATA_DIR / "custom_presets.json"
PROCESSING_HISTORY_PATH = APP_DATA_DIR / "processing_history.json"

LAYOUT_LABEL_TO_VALUE = {label: value for label, value in LAYOUT_OPTIONS}
LAYOUT_VALUE_TO_LABEL = {value: label for label, value in LAYOUT_OPTIONS}
ELEMENT_LABEL_TO_VALUE = {label: value for label, value in ELEMENT_OPTIONS}
ELEMENT_VALUE_TO_LABEL = {value: label for label, value in ELEMENT_OPTIONS}
MODE_LABEL_TO_VALUE = {label: value for label, value in UNIFORM_RESIZE_MODES}
MODE_VALUE_TO_LABEL = {value: label for label, value in UNIFORM_RESIZE_MODES}
SPLIT_VALUE_TO_LABEL = {value: label for label, value in SPLIT_MODES}
BADGE_VALUE_TO_LABEL = {value: label for label, value in BADGE_POSITIONS}

XIAOHONGSHU_TOOLS_OUTPUT = "output_xiaohongshu"

PRESET_TEMPLATES = {
    "保持当前配置": None,
    "小红书竖图": {
        "layout_type": "watermark_right_logo",
        "logo_enable": False,
        "logo_position": "right",
        "white_margin": True,
        "white_margin_width": 3,
        "shadow_enable": False,
        "equivalent_focal": False,
        "original_ratio_padding": False,
        "uniform_enable": True,
        "uniform_mode": "padding",
        "uniform_width": 1080,
        "uniform_height": 1440,
        "quality": 100,
        "elements": {
            "left_top": {"name": "LensModel"},
            "left_bottom": {"name": "Model"},
            "right_top": {"name": "Param"},
            "right_bottom": {"name": "Datetime"},
        },
    },
    "白边摄影": {
        "layout_type": "pure_white_margin",
        "logo_enable": False,
        "logo_position": "left",
        "white_margin": True,
        "white_margin_width": 6,
        "shadow_enable": False,
        "equivalent_focal": False,
        "original_ratio_padding": False,
        "uniform_enable": False,
        "uniform_mode": "padding",
        "uniform_width": 1080,
        "uniform_height": 1350,
        "quality": 100,
        "elements": {
            "left_top": {"name": "LensModel"},
            "left_bottom": {"name": "Model"},
            "right_top": {"name": "Param"},
            "right_bottom": {"name": "Datetime"},
        },
    },
    "极简信息条": {
        "layout_type": "simple",
        "logo_enable": False,
        "logo_position": "left",
        "white_margin": False,
        "white_margin_width": 3,
        "shadow_enable": False,
        "equivalent_focal": False,
        "original_ratio_padding": False,
        "uniform_enable": True,
        "uniform_mode": "crop",
        "uniform_width": 1080,
        "uniform_height": 1350,
        "quality": 100,
        "elements": {
            "left_top": {"name": "LensModel"},
            "left_bottom": {"name": "Model"},
            "right_top": {"name": "Param"},
            "right_bottom": {"name": "Datetime"},
        },
    },
}


def ensure_app_storage():
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def read_json_file(path: Path, default):
    ensure_app_storage()
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_file(path: Path, data):
    ensure_app_storage()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_custom_presets():
    presets = read_json_file(CUSTOM_PRESETS_PATH, {})
    return presets if isinstance(presets, dict) else {}


def save_custom_presets(presets):
    write_json_file(CUSTOM_PRESETS_PATH, presets)


def load_processing_history():
    history = read_json_file(PROCESSING_HISTORY_PATH, [])
    return history if isinstance(history, list) else []


def save_processing_history(history):
    write_json_file(PROCESSING_HISTORY_PATH, history[:40])


def append_processing_history(entry):
    history = load_processing_history()
    history.insert(0, entry)
    save_processing_history(history)


def build_preset_snapshot():
    elements = {}
    for location, _ in ELEMENT_LOCATIONS:
        element_value = {"name": st.session_state.get(f"element_{location}", "None")}
        if element_value["name"] == "Custom":
            element_value["value"] = st.session_state.get(f"custom_{location}", "")
        elements[location] = element_value

    return {
        "layout_type": st.session_state.get("layout_type", "watermark_right_logo"),
        "logo_enable": st.session_state.get("logo_enable", False),
        "logo_position": st.session_state.get("logo_position", "left"),
        "white_margin": st.session_state.get("white_margin", False),
        "white_margin_width": st.session_state.get("white_margin_width", 3),
        "shadow_enable": st.session_state.get("shadow_enable", False),
        "equivalent_focal": st.session_state.get("equivalent_focal", False),
        "original_ratio_padding": st.session_state.get("original_ratio_padding", False),
        "uniform_enable": st.session_state.get("uniform_enable", False),
        "uniform_mode": st.session_state.get("uniform_mode", "padding"),
        "uniform_width": st.session_state.get("uniform_width", 1080),
        "uniform_height": st.session_state.get("uniform_height", 1350),
        "quality": st.session_state.get("quality", 100),
        # ---- 调色 / 滤镜（风格包全维度）----
        "color_filter": st.session_state.get("color_filter", "none"),
        "color_brightness": st.session_state.get("color_brightness", 1.0),
        "color_contrast": st.session_state.get("color_contrast", 1.0),
        "color_saturation": st.session_state.get("color_saturation", 1.0),
        "color_sharpness": st.session_state.get("color_sharpness", 1.0),
        "color_temperature": st.session_state.get("color_temperature", 0),
        "auto_contrast": st.session_state.get("auto_contrast", False),
        # ---- 防盗水印（风格包全维度）----
        "tw_enable": st.session_state.get("tw_enable", False),
        "tw_text": st.session_state.get("tw_text", ""),
        "tw_tiled": st.session_state.get("tw_tiled", True),
        "tw_opacity": st.session_state.get("tw_opacity", 0.15),
        "elements": elements,
    }


def inject_styles():
    st.markdown(
        """
        <style>
            :root {
                --bg-main: #f6f4ef;
                --bg-panel: #ffffff;
                --bg-panel-strong: #ffffff;
                --line-soft: rgba(27, 31, 36, 0.08);
                --ink-main: #1f2328;
                --ink-soft: #57606a;
                --ink-faint: #6e7781;
                --accent: #0969da;
                --accent-deep: #0550ae;
                --shadow-soft: 0 8px 24px rgba(16, 24, 40, 0.06);
            }
            .stApp {
                background: var(--bg-main);
                color: var(--ink-main);
            }
            .block-container {
                max-width: 1180px;
                padding-top: 1rem;
                padding-bottom: 2rem;
            }
            [data-testid="stSidebar"] {
                background: #fdfdfd;
                border-right: 1px solid var(--line-soft);
            }
            [data-testid="stSidebar"] * {
                color: var(--ink-main);
            }
            [data-testid="stSidebar"] .stMarkdown p,
            [data-testid="stSidebar"] label,
            [data-testid="stSidebar"] .stCaption {
                color: var(--ink-soft) !important;
            }
            [data-testid="stSidebar"] [data-baseweb="select"] > div,
            [data-testid="stSidebar"] .stTextInput input,
            [data-testid="stSidebar"] .stNumberInput input {
                border-radius: 10px;
            }
            .hero {
                padding: 1.2rem 1.3rem;
                border-radius: 16px;
                background: var(--bg-panel);
                border: 1px solid var(--line-soft);
                box-shadow: var(--shadow-soft);
                margin-bottom: 0.8rem;
            }
            .hero h1 {
                font-size: 1.55rem;
                margin: 0;
                color: var(--ink-main);
            }
            .hero p {
                margin: 0.35rem 0 0;
                color: var(--ink-soft);
                font-size: 0.95rem;
                line-height: 1.5;
            }
            .section-card {
                background: var(--bg-panel);
                border: 1px solid var(--line-soft);
                border-radius: 16px;
                box-shadow: var(--shadow-soft);
                padding: 0.95rem 1rem;
                margin-bottom: 0.8rem;
            }
            .section-title {
                font-size: 0.78rem;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: var(--accent-deep);
                margin-bottom: 0.35rem;
            }
            .section-heading {
                color: var(--ink-main);
                font-size: 1.2rem;
                margin: 0 0 0.2rem;
            }
            .section-copy {
                color: var(--ink-soft);
                line-height: 1.55;
                margin: 0;
            }
            .result-card {
                padding: 0.95rem;
                border-radius: 16px;
                background: var(--bg-panel-strong);
                border: 1px solid var(--line-soft);
                box-shadow: var(--shadow-soft);
                margin-bottom: 0.8rem;
            }
            .result-title {
                color: var(--ink-main);
                font-size: 1rem;
                font-weight: 700;
                margin-top: 0.45rem;
            }
            .metric-line {
                color: var(--ink-soft);
                font-size: 0.9rem;
                margin-top: 0.18rem;
            }
            .folder-list {
                margin-top: 0.4rem;
                color: var(--ink-soft);
                line-height: 1.6;
            }
            .gallery-label {
                color: var(--ink-faint);
                font-size: 0.8rem;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                margin-bottom: 0.35rem;
            }
            .stButton > button,
            .stDownloadButton > button {
                border-radius: 16px;
                border: 1px solid rgba(143, 63, 34, 0.14);
                background: linear-gradient(180deg, #c96f46 0%, #b85c38 100%);
                color: white;
                font-weight: 700;
                min-height: 2.8rem;
                box-shadow: 0 10px 24px rgba(184, 92, 56, 0.2);
            }
            .stButton > button:hover,
            .stDownloadButton > button:hover {
                background: linear-gradient(180deg, #cf7a52 0%, #a84d2a 100%);
                border-color: rgba(143, 63, 34, 0.24);
            }
            [data-testid="stSegmentedControl"] {
                background: #fff;
                border: 1px solid var(--line-soft);
                padding: 0.25rem;
                border-radius: 12px;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def initialize_sidebar_state(default_data):
    defaults = {
        "preset_template": "保持当前配置",
        "panel_mode": "基础",
        "custom_preset_name": "",
        "enable_ai_assistant": False,
        "ai_goal": "",
        "ai_provider": DEFAULT_PROVIDER_ID,
        "ai_model": DEFAULT_MODEL,
        "ai_model_custom": "",
        "ai_base_url": "",
        "ai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "caption_variants": 1,
        "color_filter": default_data["global"].get("color", {}).get("filter", "none"),
        "color_brightness": float(default_data["global"].get("color", {}).get("brightness", 1.0)),
        "color_contrast": float(default_data["global"].get("color", {}).get("contrast", 1.0)),
        "color_saturation": float(default_data["global"].get("color", {}).get("saturation", 1.0)),
        "color_sharpness": float(default_data["global"].get("color", {}).get("sharpness", 1.0)),
        "color_temperature": int(default_data["global"].get("color", {}).get("temperature", 0)),
        "auto_contrast": default_data["global"].get("color", {}).get("auto_contrast", False),
        "tw_enable": default_data["global"].get("text_watermark", {}).get("enable", False),
        "tw_text": default_data["global"].get("text_watermark", {}).get("text", ""),
        "tw_tiled": default_data["global"].get("text_watermark", {}).get("tiled", True),
        "tw_opacity": float(default_data["global"].get("text_watermark", {}).get("opacity", 0.15)),
        "layout_type": default_data["layout"]["type"],
        "logo_enable": default_data["layout"]["logo_enable"],
        "logo_position": default_data["layout"]["logo_position"],
        "white_margin": default_data["global"]["white_margin"]["enable"],
        "white_margin_width": int(default_data["global"]["white_margin"]["width"]),
        "shadow_enable": default_data["global"]["shadow"]["enable"],
        "equivalent_focal": default_data["global"]["focal_length"]["use_equivalent_focal_length"],
        "original_ratio_padding": default_data["global"]["padding_with_original_ratio"]["enable"],
        "uniform_enable": default_data["global"]["uniform_resize"]["enable"],
        "uniform_mode": default_data["global"]["uniform_resize"]["mode"],
        "uniform_width": int(default_data["global"]["uniform_resize"]["width"]),
        "uniform_height": int(default_data["global"]["uniform_resize"]["height"]),
        "quality": int(default_data["base"]["quality"]),
        "strip_gps": default_data.get("global", {}).get("privacy", {}).get("strip_gps", False),
        "strip_all_exif": default_data.get("global", {}).get("privacy", {}).get("strip_all_exif", False),
    }

    for location, _ in ELEMENT_LOCATIONS:
        element_data = default_data["layout"]["elements"][location]
        defaults[f"element_{location}"] = element_data["name"]
        defaults[f"custom_{location}"] = element_data.get("value", "")

    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    st.session_state.setdefault("latest_results", [])
    # 批次内多样式（需求 5）状态默认值
    st.session_state.setdefault("cover_separate", default_data["global"].get("batch_style", {}).get("cover_separate", False))
    st.session_state.setdefault("cover_index_ui", int(default_data["global"].get("batch_style", {}).get("cover_index", 0)) + 1)
    st.session_state.setdefault("cover_result_index", -1)


def apply_preset_to_state(preset_name):
    preset = PRESET_TEMPLATES.get(preset_name)
    if not preset:
        return

    st.session_state["layout_type"] = preset["layout_type"]
    st.session_state["logo_enable"] = preset["logo_enable"]
    st.session_state["logo_position"] = preset["logo_position"]
    st.session_state["white_margin"] = preset["white_margin"]
    st.session_state["white_margin_width"] = preset["white_margin_width"]
    st.session_state["shadow_enable"] = preset["shadow_enable"]
    st.session_state["equivalent_focal"] = preset["equivalent_focal"]
    st.session_state["original_ratio_padding"] = preset["original_ratio_padding"]
    st.session_state["uniform_enable"] = preset["uniform_enable"]
    st.session_state["uniform_mode"] = preset["uniform_mode"]
    st.session_state["uniform_width"] = preset["uniform_width"]
    st.session_state["uniform_height"] = preset["uniform_height"]
    st.session_state["quality"] = preset["quality"]

    for location, _ in ELEMENT_LOCATIONS:
        element_data = preset["elements"].get(location, {"name": "None"})
        st.session_state[f"element_{location}"] = element_data["name"]
        st.session_state[f"custom_{location}"] = element_data.get("value", "")


# 快照中除 elements 外、与 session_state 同名的全部风格包字段。
_SNAPSHOT_SCALAR_KEYS = (
    "layout_type", "logo_enable", "logo_position",
    "white_margin", "white_margin_width", "shadow_enable",
    "equivalent_focal", "original_ratio_padding",
    "uniform_enable", "uniform_mode", "uniform_width", "uniform_height", "quality",
    "color_filter", "color_brightness", "color_contrast", "color_saturation",
    "color_sharpness", "color_temperature", "auto_contrast",
    "tw_enable", "tw_text", "tw_tiled", "tw_opacity",
)


def apply_snapshot_to_state(snapshot):
    """
    把一份风格包快照恢复到 session_state（需求 3.2）。

    先用 normalize_style_pack 补齐缺失字段（旧 preset 缺调色 / 水印维度时也能干净应用，
    需求 3.6/3.7），再把全维度写回 session_state，包括四角文字。
    """
    normalized = normalize_style_pack(snapshot)
    for key in _SNAPSHOT_SCALAR_KEYS:
        st.session_state[key] = normalized[key]

    elements = normalized.get("elements", {})
    for location, _ in ELEMENT_LOCATIONS:
        element_data = elements.get(location, {"name": "None"})
        st.session_state[f"element_{location}"] = element_data.get("name", "None")
        st.session_state[f"custom_{location}"] = element_data.get("value", "")


def snapshot_to_overrides(snapshot):
    """
    把一份风格包快照转换成 build_runtime_config 期望的 overrides 结构。

    用于“封面单独样式”（需求 5.1）：内页（body）走某个已保存预设/风格包，
    转换时镜像 build_overrides 里构造的 overrides 字典布局，并先用
    normalize_style_pack 补齐缺字段（兼容旧版裸快照，需求 3.6/3.7）。
    """
    normalized = normalize_style_pack(snapshot)

    elements = {}
    src_elements = normalized.get("elements", {})
    for location, _ in ELEMENT_LOCATIONS:
        element_data = src_elements.get(location, {"name": "None"})
        element_value = {"name": element_data.get("name", "None")}
        if element_value["name"] == "Custom":
            element_value["value"] = element_data.get("value", "")
        elements[location] = element_value

    return {
        "base": {"quality": int(normalized["quality"])},
        "layout": {
            "type": normalized["layout_type"],
            "logo_enable": bool(normalized["logo_enable"]),
            "logo_position": normalized["logo_position"],
            "elements": elements,
        },
        "global": {
            "white_margin": {
                "enable": bool(normalized["white_margin"]),
                "width": int(normalized["white_margin_width"]),
            },
            "shadow": {"enable": bool(normalized["shadow_enable"])},
            "focal_length": {"use_equivalent_focal_length": bool(normalized["equivalent_focal"])},
            "padding_with_original_ratio": {"enable": bool(normalized["original_ratio_padding"])},
            "uniform_resize": {
                "enable": bool(normalized["uniform_enable"]),
                "mode": normalized["uniform_mode"],
                "width": int(normalized["uniform_width"]),
                "height": int(normalized["uniform_height"]),
            },
            "color": {
                "filter": normalized["color_filter"],
                "brightness": float(normalized["color_brightness"]),
                "contrast": float(normalized["color_contrast"]),
                "saturation": float(normalized["color_saturation"]),
                "sharpness": float(normalized["color_sharpness"]),
                "temperature": int(normalized["color_temperature"]),
                "auto_contrast": bool(normalized["auto_contrast"]),
            },
            "text_watermark": {
                "enable": bool(normalized["tw_enable"]),
                "text": normalized["tw_text"],
                "tiled": bool(normalized["tw_tiled"]),
                "opacity": float(normalized["tw_opacity"]),
            },
        },
    }


def build_overrides(default_config):
    data = default_config.get_data()
    initialize_sidebar_state(data)
    custom_presets = load_custom_presets()
    preset_options = list(PRESET_TEMPLATES.keys()) + list(custom_presets.keys())

    st.sidebar.markdown("## 控制台")
    st.sidebar.caption("先选模板，再微调参数。")
    panel_mode = st.sidebar.segmented_control("面板模式", options=["基础", "高级"], default="基础", key="panel_mode")

    if panel_mode == "高级":
        st.sidebar.toggle("启用 AI 助手", key="enable_ai_assistant")
    else:
        st.session_state["enable_ai_assistant"] = False

    preset_name = st.sidebar.selectbox("预设模板", options=preset_options, key="preset_template")
    preset_button_col, preset_delete_col = st.sidebar.columns(2)
    if preset_button_col.button("应用预设", use_container_width=True):
        if preset_name in PRESET_TEMPLATES:
            apply_preset_to_state(preset_name)
        else:
            custom_preset = custom_presets.get(preset_name)
            if custom_preset:
                # 自定义预设可能是旧版裸快照（缺调色 / 水印维度），用 normalize 补齐后全维度恢复。
                apply_snapshot_to_state(custom_preset)
        st.rerun()
    if preset_delete_col.button("删除预设", use_container_width=True, disabled=preset_name in PRESET_TEMPLATES):
        custom_presets.pop(preset_name, None)
        save_custom_presets(custom_presets)
        st.session_state["preset_template"] = "保持当前配置"
        st.rerun()

    with st.sidebar.expander("布局与 Logo", expanded=True):
        layout_label = st.selectbox(
            "布局",
            options=[value for _, value in LAYOUT_OPTIONS],
            format_func=lambda value: LAYOUT_VALUE_TO_LABEL.get(value, value),
            key="layout_type",
        )

        logo_enable = st.toggle("启用 Logo", key="logo_enable")
        logo_position = st.radio(
            "Logo 位置",
            options=["left", "right"],
            format_func=lambda value: "左侧" if value == "left" else "右侧",
            horizontal=True,
            key="logo_position",
        )

    layout_elements = {}
    if panel_mode == "高级":
        with st.sidebar.expander("四角文字", expanded=False):
            for location, label in ELEMENT_LOCATIONS:
                selected_value = st.selectbox(
                    label,
                    options=[item_value for _, item_value in ELEMENT_OPTIONS],
                    format_func=lambda item_value: ELEMENT_VALUE_TO_LABEL.get(item_value, item_value),
                    key=f"element_{location}",
                )
                element_value = {"name": selected_value}
                if element_value["name"] == "Custom":
                    element_value["value"] = st.text_input(
                        f"{label} 自定义内容",
                        key=f"custom_{location}",
                    )
                layout_elements[location] = element_value
    else:
        for location, label in ELEMENT_LOCATIONS:
            element_value = {"name": st.session_state.get(f"element_{location}", data["layout"]["elements"][location]["name"])}
            if element_value["name"] == "Custom":
                element_value["value"] = st.session_state.get(f"custom_{location}", "")
            layout_elements[location] = element_value

    with st.sidebar.expander("输出与增强", expanded=True):
        white_margin = st.toggle("白色边框", key="white_margin")
        white_margin_width = st.slider(
            "白边宽度",
            min_value=0,
            max_value=30,
            disabled=not white_margin,
            key="white_margin_width",
        )
        uniform_enable = st.toggle("统一尺寸", key="uniform_enable")
        uniform_mode_value = st.selectbox(
            "统一尺寸模式",
            options=[value for _, value in UNIFORM_RESIZE_MODES],
            format_func=lambda value: MODE_VALUE_TO_LABEL.get(value, value),
            disabled=not uniform_enable,
            key="uniform_mode",
        )
        uniform_width = st.number_input(
            "统一宽度",
            min_value=1,
            disabled=not uniform_enable,
            key="uniform_width",
        )
        uniform_height = st.number_input(
            "统一高度",
            min_value=1,
            disabled=not uniform_enable,
            key="uniform_height",
        )

        quality = st.slider("输出质量", min_value=50, max_value=100, key="quality")

        if panel_mode == "高级":
            shadow_enable = st.toggle("阴影", key="shadow_enable")
            equivalent_focal = st.toggle("等效焦距", key="equivalent_focal")
            original_ratio_padding = st.toggle("按原比例补边", key="original_ratio_padding")
        else:
            shadow_enable = st.session_state.get("shadow_enable", False)
            equivalent_focal = st.session_state.get("equivalent_focal", False)
            original_ratio_padding = st.session_state.get("original_ratio_padding", False)

    with st.sidebar.expander("隐私（发布前清理）", expanded=False):
        st.caption("发布到小红书等公开平台前，建议清除 GPS 拍摄位置，避免泄露常去地点。")
        strip_gps = st.toggle("清除 GPS 位置", key="strip_gps")
        strip_all_exif = st.toggle("清除全部 EXIF", key="strip_all_exif")

    filter_values = [value for _, value in FILTER_OPTIONS]
    filter_label_map = {value: label for label, value in FILTER_OPTIONS}
    with st.sidebar.expander("调色 / 滤镜", expanded=False):
        color_filter = st.selectbox(
            "滤镜预设",
            options=filter_values,
            format_func=lambda value: filter_label_map.get(value, value),
            key="color_filter",
        )
        auto_contrast = st.toggle("自动对比度增强", key="auto_contrast")
        if panel_mode == "高级":
            color_brightness = st.slider("亮度", 0.5, 1.5, step=0.01, key="color_brightness")
            color_contrast = st.slider("对比度", 0.5, 1.5, step=0.01, key="color_contrast")
            color_saturation = st.slider("饱和度", 0.0, 2.0, step=0.01, key="color_saturation")
            color_sharpness = st.slider("锐化", 0.0, 2.0, step=0.01, key="color_sharpness")
            color_temperature = st.slider("色温（负=冷 正=暖）", -100, 100, step=1, key="color_temperature")
        else:
            color_brightness = st.session_state.get("color_brightness", 1.0)
            color_contrast = st.session_state.get("color_contrast", 1.0)
            color_saturation = st.session_state.get("color_saturation", 1.0)
            color_sharpness = st.session_state.get("color_sharpness", 1.0)
            color_temperature = st.session_state.get("color_temperature", 0)

    with st.sidebar.expander("防盗水印", expanded=False):
        st.caption("整图平铺或单点的半透明文字水印，防止盗图。")
        tw_enable = st.toggle("启用防盗水印", key="tw_enable")
        tw_text = st.text_input("水印文字", key="tw_text", placeholder="@你的昵称")
        tw_tiled = st.toggle("整图平铺", key="tw_tiled")
        tw_opacity = st.slider("不透明度", 0.05, 0.6, step=0.01, key="tw_opacity")

    with st.sidebar.expander("预设 / 风格包", expanded=False):
        st.text_input("保存为新预设", key="custom_preset_name", placeholder="例如：婚礼竖图")
        if st.button("保存当前参数为预设", use_container_width=True):
            preset_name_to_save = st.session_state.get("custom_preset_name", "").strip()
            if preset_name_to_save:
                if preset_name_to_save in PRESET_TEMPLATES:
                    st.warning("这个名称已经被内置预设占用，请换一个。")
                else:
                    custom_presets[preset_name_to_save] = build_preset_snapshot()
                    save_custom_presets(custom_presets)
                    st.session_state["preset_template"] = preset_name_to_save
                    st.rerun()
            else:
                st.warning("先输入预设名称。")

        st.divider()
        st.caption("风格包：把整套风格导出成 JSON 分享，或导入别人的风格包。")

        # 导出：把当前全维度快照序列化为可分享的风格包 JSON（需求 3.3）。
        export_name = st.session_state.get("preset_template", "")
        if export_name in ("保持当前配置", ""):
            export_name = st.session_state.get("custom_preset_name", "").strip() or "我的风格包"
        style_pack_json = serialize_style_pack(build_preset_snapshot(), name=export_name)
        safe_file_name = "".join(c for c in export_name if c.isalnum() or c in ("-", "_")) or "style-pack"
        st.download_button(
            "导出当前风格包 (.json)",
            data=style_pack_json.encode("utf-8"),
            file_name=f"{safe_file_name}.json",
            mime="application/json",
            use_container_width=True,
        )

        # 导入：上传风格包 JSON，校验合法后加入自定义预设（非法文件提示而不污染数据，需求 3.4）。
        uploaded_pack = st.file_uploader(
            "导入风格包 JSON",
            type=["json"],
            key="style_pack_uploader",
        )
        if uploaded_pack is not None:
            file_token = f"{uploaded_pack.name}:{uploaded_pack.size}"
            if st.session_state.get("style_pack_imported_token") != file_token:
                try:
                    text = uploaded_pack.getvalue().decode("utf-8")
                    pack = deserialize_style_pack(text)
                except StylePackError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"风格包导入失败：{exc}")
                else:
                    pack_name = (pack.get("name") or Path(uploaded_pack.name).stem).strip() or "导入的风格包"
                    if pack_name in PRESET_TEMPLATES:
                        pack_name = f"{pack_name}（导入）"
                    custom_presets[pack_name] = pack["snapshot"]
                    save_custom_presets(custom_presets)
                    st.session_state["style_pack_imported_token"] = file_token
                    st.session_state["preset_template"] = pack_name
                    st.success(f"已导入风格包「{pack_name}」，可在上方选择并应用。")
                    st.rerun()

    overrides = {
        "base": {"quality": quality},
        "layout": {
            "type": layout_label,
            "logo_enable": logo_enable,
            "logo_position": logo_position,
            "elements": layout_elements,
        },
        "global": {
            "white_margin": {"enable": white_margin, "width": white_margin_width},
            "shadow": {"enable": shadow_enable},
            "focal_length": {"use_equivalent_focal_length": equivalent_focal},
            "padding_with_original_ratio": {"enable": original_ratio_padding},
            "uniform_resize": {
                "enable": uniform_enable,
                "mode": uniform_mode_value,
                "width": int(uniform_width),
                "height": int(uniform_height),
            },
            "privacy": {
                "strip_gps": strip_gps,
                "strip_all_exif": strip_all_exif,
            },
            "color": {
                "filter": color_filter,
                "brightness": float(color_brightness),
                "contrast": float(color_contrast),
                "saturation": float(color_saturation),
                "sharpness": float(color_sharpness),
                "temperature": int(color_temperature),
                "auto_contrast": auto_contrast,
            },
            "text_watermark": {
                "enable": tw_enable,
                "text": tw_text,
                "tiled": tw_tiled,
                "opacity": float(tw_opacity),
            },
        },
    }

    return overrides


def apply_ai_suggestion_to_state(suggestion: dict):
    st.session_state["layout_type"] = suggestion["layout_type"]
    st.session_state["logo_enable"] = suggestion["logo_enable"]
    st.session_state["logo_position"] = suggestion["logo_position"]
    st.session_state["white_margin"] = suggestion["white_margin"]
    st.session_state["white_margin_width"] = suggestion["white_margin_width"]
    st.session_state["shadow_enable"] = suggestion["shadow_enable"]
    st.session_state["equivalent_focal"] = suggestion["equivalent_focal"]
    st.session_state["original_ratio_padding"] = suggestion["original_ratio_padding"]
    st.session_state["uniform_enable"] = suggestion["uniform_enable"]
    st.session_state["uniform_mode"] = suggestion["uniform_mode"]
    st.session_state["uniform_width"] = suggestion["uniform_width"]
    st.session_state["uniform_height"] = suggestion["uniform_height"]
    st.session_state["quality"] = suggestion["quality"]


def _switch_provider_defaults():
    """切换服务商时：把模型重置为该服务商的首个模型，并尝试自动填充对应 Key。"""
    provider = get_provider(st.session_state.get("ai_provider"))
    if provider.models:
        st.session_state["ai_model"] = provider.models[0][1]
    else:
        st.session_state["ai_model"] = ""  # 自定义服务商默认走自定义模型
    # 若当前 Key 为空，尝试用该服务商推荐的环境变量自动填充
    if not st.session_state.get("ai_api_key", "").strip() and provider.api_key_env:
        env_key = os.getenv(provider.api_key_env, "")
        if env_key:
            st.session_state["ai_api_key"] = env_key


def render_ai_settings(key_prefix: str) -> dict:
    """
    共用的 AI 设置块：服务商 + 模型 + API Key + Base URL。
    返回 {"model", "api_key", "base_url"}，供两个 AI 入口复用（单一来源）。
    """
    provider_ids = [p.id for p in AI_PROVIDERS]
    provider_label = {p.id: p.label for p in AI_PROVIDERS}

    current_provider_id = st.session_state.get("ai_provider", DEFAULT_PROVIDER_ID)
    provider_index = provider_ids.index(current_provider_id) if current_provider_id in provider_ids else 0

    selected_provider_id = st.selectbox(
        "服务商",
        options=provider_ids,
        index=provider_index,
        format_func=lambda pid: provider_label.get(pid, pid),
        key="ai_provider",
        on_change=_switch_provider_defaults,
    )
    provider = get_provider(selected_provider_id)

    # 模型选择：内置模型 + 自定义
    model_values = [value for _, value in provider.models] + [CUSTOM_MODEL_VALUE]
    label_map = {value: label for label, value in provider.models}
    label_map[CUSTOM_MODEL_VALUE] = "自定义（手动填写模型名）"

    current_model = st.session_state.get("ai_model", DEFAULT_MODEL)
    if current_model in model_values:
        model_index = model_values.index(current_model)
    else:
        model_index = model_values.index(CUSTOM_MODEL_VALUE)
        if current_model:
            st.session_state["ai_model_custom"] = current_model

    selected_model = st.selectbox(
        "模型",
        options=model_values,
        index=model_index,
        format_func=lambda value: label_map.get(value, value),
        key=f"{key_prefix}_model_select",
    )

    if selected_model == CUSTOM_MODEL_VALUE:
        custom_model = st.text_input(
            "自定义模型名",
            key="ai_model_custom",
            placeholder="例如：gpt-4o-2024-11-20、gemini-2.5-flash 或第三方模型名",
        )
        final_model = resolve_model(custom_model)
    else:
        final_model = selected_model
    st.session_state["ai_model"] = final_model

    # API Key + Base URL
    key_placeholder = "AIza..." if provider.id == "gemini" else "sk-..."
    st.text_input("API Key", key="ai_api_key", type="password", placeholder=key_placeholder)

    if provider.id == "gemini":
        base_help = "默认使用 Gemini 兼容端点，一般无需修改"
        base_placeholder = provider.base_url
    elif provider.id == "custom":
        base_help = "请填写兼容 OpenAI 接口的服务地址"
        base_placeholder = "https://your-endpoint/v1"
    else:
        base_help = "可选，使用代理/中转时填写"
        base_placeholder = "https://api.openai.com/v1"
    st.text_input("Base URL", key="ai_base_url", placeholder=base_placeholder, help=base_help)

    final_base_url = resolve_base_url(provider.id, st.session_state.get("ai_base_url", ""))
    return {
        "model": final_model,
        "api_key": st.session_state.get("ai_api_key", "").strip(),
        "base_url": final_base_url,
    }


def render_ai_assistant(sample_image_path: Path | None):
    st.subheader("AI 预设助手")
    st.caption("给一张样图和一句目标描述，AI 会推荐一套适合批量处理的参数。")

    if not is_openai_available():
        st.warning("未检测到 openai 依赖，AI 功能不可用。请先执行：pip3 install openai")
        return

    with st.expander("AI 参数建议", expanded=False):
        st.text_area(
            "你的目标",
            key="ai_goal",
            placeholder="例如：我要发布到小红书，风格干净、统一、保留拍摄参数，不要太花。",
        )
        ai_cfg = render_ai_settings("preset")
        if sample_image_path:
            usage = estimate_usage(1)
            st.caption(f"样图：{sample_image_path.name} ｜ 预计输入 ~{usage['approx_input_tokens']} tokens（{usage['level']}）")
        else:
            st.caption("当前还没有可供 AI 分析的样图。")

        if st.button("用 AI 生成参数建议", use_container_width=True, disabled=sample_image_path is None):
            api_key = ai_cfg["api_key"]
            user_goal = st.session_state.get("ai_goal", "").strip()
            if not api_key:
                st.warning("请先填写 API Key。")
                return
            if not user_goal:
                st.warning("请先描述你的目标。")
                return

            try:
                with st.spinner("AI 正在分析样图并生成建议..."):
                    suggestion = suggest_preset_from_image(
                        image_path=sample_image_path,
                        user_goal=user_goal,
                        api_key=api_key,
                        model=ai_cfg["model"],
                        base_url=ai_cfg["base_url"],
                    )
                apply_ai_suggestion_to_state(suggestion)
                st.session_state["preset_template"] = "保持当前配置"
                st.success("AI 建议已应用到当前参数。")
                reason = suggestion.get("reason", "")
                if reason:
                    st.info(f"AI 建议理由：{reason}")
                st.rerun()
            except AIServiceError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"AI 建议生成失败：{exc}")


def make_zip_bytes(result_paths: list[Path]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for result_path in result_paths:
            archive.write(result_path, arcname=result_path.name)
    return buffer.getvalue()


def serialize_results(results):
    return [
        {
            "source_path": str(result.source_path),
            "output_path": str(result.output_path),
            "original_size": list(result.original_size),
            "output_size": list(result.output_size),
        }
        for result in results
    ]


def deserialize_results(items):
    return [
        ProcessResult(
            source_path=Path(item["source_path"]),
            output_path=Path(item["output_path"]),
            original_size=tuple(item["original_size"]),
            output_size=tuple(item["output_size"]),
        )
        for item in items
        if Path(item["output_path"]).exists()
    ]


def set_latest_results(results):
    st.session_state["latest_results"] = serialize_results(results)


def render_batch_actions(results):
    selected_results = []
    selection_columns = st.columns(2)
    for index, result in enumerate(results):
        checkbox_key = f"result_select_{index}_{result.output_path.name}"
        with selection_columns[index % 2]:
            if st.checkbox(result.output_path.name, key=checkbox_key):
                selected_results.append(result)

    if not selected_results:
        st.caption("勾选结果后，可以批量打包下载或删除本轮输出。")
        return

    action_columns = st.columns(2)
    with action_columns[0]:
        st.download_button(
            "下载选中结果 ZIP",
            data=make_zip_bytes([result.output_path for result in selected_results]),
            file_name="semi-utils-selected.zip",
            mime="application/zip",
            use_container_width=True,
        )
    with action_columns[1]:
        if st.button("删除选中结果", use_container_width=True, key="delete_selected_results"):
            for result in selected_results:
                if result.output_path.exists():
                    result.output_path.unlink()
                source_path = result.source_path
                if "_source" in source_path.parts and source_path.exists():
                    source_path.unlink()
            remaining_results = [result for result in results if result not in selected_results]
            set_latest_results(remaining_results)
            st.success(f"已删除 {len(selected_results)} 个结果文件。")
            st.rerun()


def _caption_for_publish():
    """
    从 session_state 取已生成的文案，转换为 publish_service 期望的 {title, body, tags}。

    优先用顶层 title/body，缺失时回退到第一个候选；没有任何文案时返回 None（验收 10.3）。
    """
    caption = st.session_state.get("xhs_caption_result")
    if not isinstance(caption, dict):
        return None

    title = caption.get("title", "")
    body = caption.get("body", "")
    if not title and not body:
        variants = caption.get("variants") or []
        if variants and isinstance(variants[0], dict):
            title = variants[0].get("title", "")
            body = variants[0].get("body", "")
    tags = caption.get("tags", [])
    if not (title or body or tags):
        return None
    return {"title": title, "body": body, "tags": tags}


def render_publish_draft(results):
    """
    打包发布草稿（需求 10.1、10.4）：把本轮成图按顺序 + 已生成文案打成 ZIP 供下载。
    """
    st.caption("打包发布草稿：把上面这批成图（按顺序命名）和「AI 文案标签」生成的文案打成一个 ZIP。")
    if not st.button("打包发布草稿", use_container_width=True, key="build_publish_draft"):
        return

    caption = _caption_for_publish()
    draft_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT / "draft"
    try:
        zip_path = build_publish_draft(
            image_paths=[result.output_path for result in results],
            caption=caption,
            out_dir=draft_dir,
            as_zip=True,
        )
        zip_bytes = zip_path.read_bytes()
    except PublishError as exc:
        st.error(str(exc))
        return
    except OSError as exc:
        st.error(f"读取草稿压缩包失败：{exc}")
        return

    if caption is None:
        st.info("未检测到文案，草稿只包含图片与占位文案。可在「小红书工具 · AI 文案标签」生成文案后再打包。")
    else:
        st.success("发布草稿已打包：包含按顺序命名的成图与含标题/正文/标签的文案。")
    st.download_button(
        "下载发布草稿 ZIP",
        data=zip_bytes,
        file_name=zip_path.name,
        mime="application/zip",
        use_container_width=True,
        key="download_publish_draft",
    )


def render_results(results):
    if not results:
        st.info("当前没有可展示的处理结果。")
        return

    st.subheader("处理结果")
    zip_bytes = make_zip_bytes([result.output_path for result in results])
    st.download_button(
        "下载全部结果 ZIP",
        data=zip_bytes,
        file_name="semi-utils-output.zip",
        mime="application/zip",
        use_container_width=True,
    )
    render_batch_actions(results)
    render_publish_draft(results)

    # 封面所在索引（需求 5.3）：开启“封面单独样式”处理后写入；-1 表示无封面。
    cover_index = st.session_state.get("cover_result_index", -1)

    columns = st.columns(2)
    for index, result in enumerate(results):
        with columns[index % 2]:
            st.markdown('<div class="result-card">', unsafe_allow_html=True)
            compare_left, compare_right = st.columns(2)
            with compare_left:
                st.markdown('<div class="gallery-label">处理前</div>', unsafe_allow_html=True)
                st.image(str(result.source_path), use_container_width=True)
            with compare_right:
                st.markdown('<div class="gallery-label">处理后</div>', unsafe_allow_html=True)
                st.image(str(result.output_path), use_container_width=True)
            title_text = result.output_path.name
            if index == cover_index:
                title_text = f"封面 · {title_text}"
            st.markdown(f'<div class="result-title">{title_text}</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="metric-line">原图 {result.original_size[0]} x {result.original_size[1]} '
                f'→ 输出 {result.output_size[0]} x {result.output_size[1]}</div>',
                unsafe_allow_html=True,
            )
            with open(result.output_path, "rb") as file_obj:
                st.download_button(
                    "下载这张",
                    data=file_obj.read(),
                    file_name=result.output_path.name,
                    mime="image/jpeg",
                    key=f"download_{result.output_path.name}",
                    use_container_width=True,
                )
            st.markdown("</div>", unsafe_allow_html=True)


def render_processing_history():
    history = load_processing_history()
    st.subheader("处理历史")
    if not history:
        st.caption("还没有历史记录。完成一次处理后会自动出现在这里。")
        return

    if st.button("清空历史记录", use_container_width=True, key="clear_processing_history"):
        save_processing_history([])
        st.rerun()

    for index, entry in enumerate(history):
        timestamp = entry.get("timestamp", "-")
        source_mode = entry.get("source_mode", "-")
        count = entry.get("count", 0)
        preset_name = entry.get("preset_name", "保持当前配置")
        with st.expander(f"{timestamp} · {source_mode} · {count} 张 · {preset_name}", expanded=False):
            output_paths = [Path(item["output_path"]) for item in entry.get("results", []) if Path(item["output_path"]).exists()]
            if output_paths:
                st.download_button(
                    "下载这一批结果 ZIP",
                    data=make_zip_bytes(output_paths),
                    file_name=f"semi-utils-history-{index + 1}.zip",
                    mime="application/zip",
                    key=f"history_zip_{index}",
                    use_container_width=True,
                )
            else:
                st.caption("这一批结果文件已不存在。")

            for item in entry.get("results", [])[:8]:
                st.write(Path(item["output_path"]).name)


def _resolve_cover_index(total: int) -> int:
    """把 1-based 的封面序号 UI 值转成 0-based 索引；越界回退 0（需求 5.4）。"""
    raw = st.session_state.get("cover_index_ui", 1)
    try:
        idx = int(raw) - 1
    except (TypeError, ValueError):
        idx = 0
    if idx < 0 or idx >= max(total, 1):
        idx = 0
    return idx


def _resolve_body_overrides():
    """
    根据所选“内页风格包”返回内页 overrides；选不到 / 无意义时返回 None（回退普通处理）。

    内页风格来自已保存的自定义预设或内置 PRESET_TEMPLATES，经 snapshot_to_overrides
    转换为 build_runtime_config 期望的结构。
    """
    body_preset_name = st.session_state.get("cover_body_preset", "")
    if not body_preset_name:
        return None
    if body_preset_name in PRESET_TEMPLATES:
        preset = PRESET_TEMPLATES.get(body_preset_name)
        if not preset:  # “保持当前配置”等同封面，无区分意义
            return None
        return snapshot_to_overrides(preset)
    snapshot = load_custom_presets().get(body_preset_name)
    if not snapshot:
        return None
    return snapshot_to_overrides(snapshot)


def run_workbench_processing(source_paths, output_dir, overrides):
    """
    执行工作台批处理。

    - 开启“封面单独样式”且能解析到内页风格包时：封面走当前侧边栏配置、内页走所选风格包
      （需求 5.1）；封面序号越界回退第一张（需求 5.4）。
    - 否则保持现有 process_images 行为（需求 5.2）。

    处理后把封面所在结果索引写入 session_state["cover_result_index"]（-1 表示无封面），
    供 render_results 标记封面（需求 5.3）。返回 results 列表。
    """
    cover_separate = st.session_state.get("cover_separate", False)
    body_overrides = _resolve_body_overrides() if cover_separate else None

    if cover_separate and body_overrides is not None and source_paths:
        cover_index = _resolve_cover_index(len(source_paths))
        cover_config = build_runtime_config(CONFIG_PATH, overrides)
        body_config = build_runtime_config(CONFIG_PATH, body_overrides)
        results = process_images_with_cover(
            source_paths, output_dir, cover_config, body_config, cover_index=cover_index
        )
        st.session_state["cover_result_index"] = cover_index
    else:
        runtime_config = build_runtime_config(CONFIG_PATH, overrides)
        results = process_images(source_paths, output_dir, runtime_config)
        st.session_state["cover_result_index"] = -1
    return results


def render_cover_style_controls():
    """
    “封面单独样式”工作台控件（需求 5.1）：开关 + 封面序号 + 内页风格包选择。

    心智模型：当前左侧侧边栏参数 = 封面风格；这里选一个已保存的预设/风格包作为内页风格。
    """
    with st.expander("封面单独样式（封面/内页不同风格）", expanded=False):
        st.caption(
            "开启后：当前左侧侧边栏参数作为「封面」风格，下面选择的预设/风格包作为「内页」风格，"
            "适合首图吸睛、内页统一。未开启时全部图片用同一套参数。"
        )
        cover_separate = st.toggle("封面单独样式", key="cover_separate")
        st.number_input(
            "封面序号（从 1 开始）",
            min_value=1,
            step=1,
            key="cover_index_ui",
            disabled=not cover_separate,
            help="指定哪一张作为封面；超出范围会自动回退为第一张。",
        )

        body_options = [name for name in PRESET_TEMPLATES.keys() if name != "保持当前配置"]
        body_options += list(load_custom_presets().keys())
        if body_options:
            st.selectbox(
                "内页风格包",
                options=body_options,
                key="cover_body_preset",
                disabled=not cover_separate,
                help="内页（非封面图片）使用的风格；封面使用当前左侧参数。",
            )
        else:
            st.caption("还没有可用的内页风格包，可先在左侧「预设 / 风格包」里保存一个。")


def process_uploaded_files(overrides):
    st.caption("上传模式会把结果保存到 `output_web/`。")
    uploaded_files = st.file_uploader(
        "拖入或选择图片",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="workbench_uploader",
    )
    sample_image_path = None
    if not uploaded_files:
        st.info("选择一张或多张图片后，再点击“开始处理上传图片”。")
    else:
        with tempfile.TemporaryDirectory(prefix="semi_utils_ai_sample_") as temp_dir:
            temp_sample_path = Path(temp_dir) / uploaded_files[0].name
            temp_sample_path.write_bytes(uploaded_files[0].getbuffer())
            persisted_sample_dir = ROOT_DIR / "output_web" / "_ai_sample"
            persisted_sample_dir.mkdir(parents=True, exist_ok=True)
            sample_image_path = persisted_sample_dir / uploaded_files[0].name
            sample_image_path.write_bytes(temp_sample_path.read_bytes())

    if st.session_state.get("enable_ai_assistant", False):
        render_ai_assistant(sample_image_path)

    render_cover_style_controls()

    if not uploaded_files:
        return

    if st.button("开始处理上传图片", type="primary", use_container_width=True):
        with tempfile.TemporaryDirectory(prefix="semi_utils_upload_") as temp_dir:
            temp_root = Path(temp_dir)
            input_dir = temp_root / "input"
            output_dir = temp_root / "output"
            input_dir.mkdir(parents=True, exist_ok=True)

            source_paths = []
            for uploaded_file in uploaded_files:
                source_path = input_dir / uploaded_file.name
                source_path.write_bytes(uploaded_file.getbuffer())
                source_paths.append(source_path)

            with st.spinner("正在处理图片..."):
                results = run_workbench_processing(source_paths, output_dir, overrides)
                persisted_dir = ROOT_DIR / "output_web"
                persisted_source_dir = persisted_dir / "_source"
                persisted_dir.mkdir(exist_ok=True)
                persisted_source_dir.mkdir(exist_ok=True)
                persisted_results = []
                for result in results:
                    final_path = persisted_dir / result.output_path.name
                    final_source_path = persisted_source_dir / result.source_path.name
                    final_path.write_bytes(result.output_path.read_bytes())
                    final_source_path.write_bytes(result.source_path.read_bytes())
                    result.output_path = final_path
                    result.source_path = final_source_path
                    persisted_results.append(result)

        set_latest_results(persisted_results)
        append_processing_history(
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source_mode": "上传图片",
                "count": len(persisted_results),
                "preset_name": st.session_state.get("preset_template", "保持当前配置"),
                "results": serialize_results(persisted_results),
            }
        )
        st.success(f"处理完成，共输出 {len(persisted_results)} 张图片。")


def process_input_folder(overrides):
    input_files = list_input_images(ROOT_DIR / "input")
    st.caption("`input` 模式会直接读取项目里的 `input/` 目录，并输出到 `output/`。")
    if st.session_state.get("enable_ai_assistant", False):
        render_ai_assistant(input_files[0] if input_files else None)
    if not input_files:
        st.warning("`input/` 目录里还没有图片。")
        return

    st.markdown(
        f'<div class="folder-list">检测到 {len(input_files)} 张图片：<br>{"<br>".join(path.name for path in input_files[:12])}'
        f'{"<br>..." if len(input_files) > 12 else ""}</div>',
        unsafe_allow_html=True,
    )

    render_cover_style_controls()

    if st.button("开始处理 input 文件夹", type="primary", use_container_width=True):
        with st.spinner("正在处理 input 文件夹中的图片..."):
            results = run_workbench_processing(input_files, ROOT_DIR / "output", overrides)
        set_latest_results(results)
        append_processing_history(
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source_mode": "input 文件夹",
                "count": len(results),
                "preset_name": st.session_state.get("preset_template", "保持当前配置"),
                "results": serialize_results(results),
            }
        )
        st.success(f"处理完成，共输出 {len(results)} 张图片。")


def _load_uploaded_images(uploaded_files) -> list[tuple[str, Image.Image]]:
    """把上传的文件读成 (filename, PIL.Image) 列表。"""
    loaded = []
    for uploaded_file in uploaded_files:
        try:
            image = Image.open(io.BytesIO(uploaded_file.getbuffer())).convert("RGB")
            loaded.append((uploaded_file.name, image))
        except Exception:
            continue
    return loaded


def _save_images_to_dir(named_images: list[tuple[str, Image.Image]], out_dir: Path,
                        quality: int = 95) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, image in named_images:
        stem = Path(name).stem
        target = out_dir / f"{stem}.jpg"
        save_jpg(image, target, quality=quality)
        paths.append(target)
    return paths


def _render_image_grid(images: list[Image.Image], captions: list[str] | None = None,
                       columns_per_row: int = 3):
    columns = st.columns(columns_per_row)
    for index, image in enumerate(images):
        with columns[index % columns_per_row]:
            caption = captions[index] if captions and index < len(captions) else None
            st.image(image, use_container_width=True, caption=caption)


def render_split_tool(uploaded_files):
    st.markdown("#### 长图切割 / 九宫格")
    st.caption("把长图、宽幅或一张大图切成多张，滑动时拼成完整画面，适合轮播。")

    mode_value = st.selectbox(
        "切割方式",
        options=[value for _, value in SPLIT_MODES],
        format_func=lambda value: SPLIT_VALUE_TO_LABEL.get(value, value),
        index=2,
        key="xhs_split_mode",
    )

    count = rows = cols = 3
    if mode_value in ("vertical", "horizontal"):
        count = st.slider("切成几张", min_value=2, max_value=12, value=3, key="xhs_split_count")
    else:
        grid_cols = st.columns(2)
        rows = grid_cols[0].number_input("行数", min_value=1, max_value=5, value=3, key="xhs_split_rows")
        cols = grid_cols[1].number_input("列数", min_value=1, max_value=5, value=3, key="xhs_split_cols")

    if not uploaded_files:
        st.info("先在上方上传图片。")
        return

    if st.button("开始切割", type="primary", use_container_width=True, key="xhs_split_run"):
        named_images = _load_uploaded_images(uploaded_files)
        all_pieces: list[tuple[str, Image.Image]] = []
        for name, image in named_images:
            pieces = split_image(image, mode=mode_value, count=int(count),
                                 rows=int(rows), cols=int(cols))
            stem = Path(name).stem
            for idx, piece in enumerate(pieces, start=1):
                all_pieces.append((f"{stem}_{idx:02d}", piece.convert("RGB")))
            image.close()

        if not all_pieces:
            st.warning("没有生成任何切片。")
            return

        out_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT / "split"
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        paths = _save_images_to_dir(all_pieces, out_dir)

        st.success(f"切割完成，共 {len(paths)} 张。")
        st.download_button(
            "下载切割结果 ZIP",
            data=make_zip_bytes(paths),
            file_name="xiaohongshu-split.zip",
            mime="application/zip",
            use_container_width=True,
        )
        _render_image_grid([img for _, img in all_pieces],
                           captions=[name for name, _ in all_pieces])


def render_collage_tool(uploaded_files):
    st.markdown("#### 拼图封面")
    st.caption("把多张照片拼成一张网格封面图，适合做轮播的第一张。")

    grid_cols = st.columns(2)
    rows = grid_cols[0].number_input("行数", min_value=1, max_value=4, value=2, key="xhs_collage_rows")
    cols = grid_cols[1].number_input("列数", min_value=1, max_value=4, value=2, key="xhs_collage_cols")
    size_cols = st.columns(2)
    side = size_cols[0].number_input("输出边长(px)", min_value=200, max_value=4000, value=1080, key="xhs_collage_side")
    gap = size_cols[1].number_input("间距(px)", min_value=0, max_value=200, value=16, key="xhs_collage_gap")

    if not uploaded_files:
        st.info("先在上方上传图片。")
        return

    needed = int(rows) * int(cols)
    st.caption(f"将使用前 {needed} 张图片（已上传 {len(uploaded_files)} 张）。")

    if st.button("生成拼图", type="primary", use_container_width=True, key="xhs_collage_run"):
        named_images = _load_uploaded_images(uploaded_files)
        images = [img for _, img in named_images]
        if not images:
            st.warning("没有可用图片。")
            return
        try:
            collage = make_collage(images, int(rows), int(cols),
                                   output_size=(int(side), int(side)), gap=int(gap))
        except ValueError as exc:
            st.error(f"拼图失败：{exc}")
            for img in images:
                img.close()
            return

        out_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "collage.jpg"
        save_jpg(collage, target)

        st.success("拼图完成。")
        st.image(collage, use_container_width=True)
        with open(target, "rb") as file_obj:
            st.download_button(
                "下载拼图",
                data=file_obj.read(),
                file_name="collage.jpg",
                mime="image/jpeg",
                use_container_width=True,
            )
        collage.close()
        for img in images:
            img.close()


def render_page_number_tool(uploaded_files):
    st.markdown("#### 批量页码角标")
    st.caption("给一组图片打上 1/N、2/N… 的角标，让整组看起来成系列。")

    position = st.selectbox(
        "页码位置",
        options=[value for _, value in BADGE_POSITIONS],
        format_func=lambda value: BADGE_VALUE_TO_LABEL.get(value, value),
        key="xhs_badge_position",
    )

    if not uploaded_files:
        st.info("先在上方上传图片。")
        return

    if st.button("添加页码", type="primary", use_container_width=True, key="xhs_badge_run"):
        named_images = _load_uploaded_images(uploaded_files)
        if not named_images:
            st.warning("没有可用图片。")
            return
        images = [img for _, img in named_images]
        numbered = add_page_numbers(images, position=position)

        out_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT / "numbered"
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        named_results = [(name, result) for (name, _), result in zip(named_images, numbered)]
        paths = _save_images_to_dir(named_results, out_dir)

        st.success(f"完成，共 {len(paths)} 张。")
        st.download_button(
            "下载结果 ZIP",
            data=make_zip_bytes(paths),
            file_name="xiaohongshu-numbered.zip",
            mime="application/zip",
            use_container_width=True,
        )
        _render_image_grid(numbered)
        for img in images:
            img.close()


def render_caption_tool(uploaded_files):
    st.markdown("#### AI 文案 + 话题标签")
    st.caption("上传这组要发布的图片，AI 帮你写小红书标题、正文和话题标签。")

    if not is_openai_available():
        st.warning("未检测到 openai 依赖，AI 功能不可用。请先执行：pip3 install openai")
        return

    st.text_area(
        "发布目标 / 想表达的内容",
        key="xhs_caption_goal",
        placeholder="例如：周末去了海边，想发一组治愈风的胶片色照片。",
    )
    ai_cfg = render_ai_settings("caption")
    st.slider("生成候选数量", min_value=1, max_value=5, key="caption_variants",
              help="一次生成多套标题/正文供挑选。")

    if not uploaded_files:
        st.info("先在上方上传图片。")
        return

    num_send = min(len(uploaded_files), 6)
    usage = estimate_usage(num_send)
    st.caption(
        f"将参考前 {num_send} 张图片 ｜ 预计输入 ~{usage['approx_input_tokens']} tokens（{usage['level']}）"
    )

    if st.button("用 AI 生成文案", type="primary", use_container_width=True, key="xhs_caption_run"):
        api_key = ai_cfg["api_key"]
        if not api_key:
            st.warning("请先填写 API Key。")
            return

        with tempfile.TemporaryDirectory(prefix="semi_utils_caption_") as temp_dir:
            temp_root = Path(temp_dir)
            image_paths = []
            for uploaded_file in uploaded_files:
                path = temp_root / uploaded_file.name
                path.write_bytes(uploaded_file.getbuffer())
                image_paths.append(path)

            try:
                with st.spinner("AI 正在看图写文案..."):
                    caption = generate_xiaohongshu_caption(
                        image_paths=image_paths,
                        user_goal=st.session_state.get("xhs_caption_goal", ""),
                        api_key=api_key,
                        model=ai_cfg["model"],
                        base_url=ai_cfg["base_url"],
                        num_variants=int(st.session_state.get("caption_variants", 1)),
                    )
                st.session_state["xhs_caption_result"] = caption
            except AIServiceError as exc:
                st.error(str(exc))
                return
            except Exception as exc:
                st.error(f"文案生成失败：{exc}")
                return

    caption = st.session_state.get("xhs_caption_result")
    if caption:
        variants = caption.get("variants") or [
            {"title": caption.get("title", ""), "body": caption.get("body", "")}
        ]
        tags = caption.get("tags", [])
        tag_line = " ".join(f"#{tag}" for tag in tags)

        if len(variants) > 1:
            labels = [f"候选 {i + 1}" for i in range(len(variants))]
            chosen_tabs = st.tabs(labels)
            for idx, (tab, variant) in enumerate(zip(chosen_tabs, variants)):
                with tab:
                    title = variant.get("title", "")
                    body = variant.get("body", "")
                    st.text_input("标题", value=title, key=f"xhs_caption_title_{idx}")
                    st.text_area("正文", value=body, height=180, key=f"xhs_caption_body_{idx}")
                    full_text = f"{title}\n\n{body}\n\n{tag_line}"
                    st.download_button(
                        "下载这条文案 (.txt)",
                        data=full_text.encode("utf-8"),
                        file_name=f"xiaohongshu-caption-{idx + 1}.txt",
                        mime="text/plain",
                        key=f"xhs_caption_dl_{idx}",
                        use_container_width=True,
                    )
            st.text_area("话题标签（共用）", value=tag_line, height=80, key="xhs_caption_tags_out")
        else:
            title = variants[0].get("title", "")
            body = variants[0].get("body", "")
            st.text_input("标题", value=title, key="xhs_caption_title_out")
            st.text_area("正文", value=body, height=180, key="xhs_caption_body_out")
            st.text_area("话题标签", value=tag_line, height=80, key="xhs_caption_tags_out")
            full_text = f"{title}\n\n{body}\n\n{tag_line}"
            st.download_button(
                "下载文案 (.txt)",
                data=full_text.encode("utf-8"),
                file_name="xiaohongshu-caption.txt",
                mime="text/plain",
                use_container_width=True,
            )


def render_style_suggestion_tool(uploaded_files):
    st.markdown("#### AI 风格建议（滤镜 / 标签）")
    st.caption("上传这组照片，AI 看图后建议适配的滤镜风格和话题标签，可一键把滤镜应用到当前配置。")

    if not is_openai_available():
        st.warning("未检测到 openai 依赖，AI 功能不可用。请先执行：pip3 install openai")
        return

    st.text_area(
        "发布目标 / 想表达的内容（可选）",
        key="xhs_style_goal",
        placeholder="例如：想发一组治愈风的海边照片，希望色调统一、清新。",
    )
    ai_cfg = render_ai_settings("style")

    if not uploaded_files:
        st.info("先在上方上传图片。")
        return

    num_send = min(len(uploaded_files), 6)
    usage = estimate_usage(num_send)
    st.caption(
        f"将参考前 {num_send} 张图片 ｜ 预计输入 ~{usage['approx_input_tokens']} tokens（{usage['level']}）"
    )

    filter_label_map = {value: label for label, value in FILTER_OPTIONS}

    if st.button("用 AI 建议风格 / 标签", type="primary", use_container_width=True, key="xhs_style_run"):
        api_key = ai_cfg["api_key"]
        if not api_key:
            st.warning("请先填写 API Key。")
            return

        with tempfile.TemporaryDirectory(prefix="semi_utils_style_") as temp_dir:
            temp_root = Path(temp_dir)
            image_paths = []
            for uploaded_file in uploaded_files[:num_send]:
                path = temp_root / uploaded_file.name
                path.write_bytes(uploaded_file.getbuffer())
                image_paths.append(path)

            try:
                with st.spinner("AI 正在看图给风格建议..."):
                    suggestion = suggest_style_and_tags(
                        image_paths=image_paths,
                        user_goal=st.session_state.get("xhs_style_goal", ""),
                        api_key=api_key,
                        model=ai_cfg["model"],
                        base_url=ai_cfg["base_url"],
                    )
                st.session_state["xhs_style_result"] = suggestion
            except AIServiceError as exc:
                st.error(str(exc))
                return
            except Exception as exc:
                st.error(f"风格建议生成失败：{exc}")
                return

    suggestion = st.session_state.get("xhs_style_result")
    if suggestion:
        recommended_filter = suggestion.get("filter", "none")
        filter_label = filter_label_map.get(recommended_filter, recommended_filter)
        reason = suggestion.get("reason", "")
        tags = suggestion.get("tags", [])

        st.markdown(f"**推荐滤镜：** {filter_label}（`{recommended_filter}`）")
        if reason:
            st.info(f"推荐理由：{reason}")

        # 一键采纳滤镜：写回侧边栏调色滤镜状态（需求 9.5）。
        if st.button("采纳这个滤镜", use_container_width=True, key="xhs_style_apply_filter"):
            st.session_state["color_filter"] = recommended_filter
            st.success(f"已把滤镜「{filter_label}」应用到当前配置，可在左侧「调色 / 滤镜」查看。")
            st.rerun()

        if tags:
            tag_line = " ".join(f"#{tag}" for tag in tags)
            st.markdown(f"**推荐标签：** {tag_line}")
            st.text_area("话题标签（可复制）", value=tag_line, height=80, key="xhs_style_tags_out")
        else:
            st.caption("这次没有给出标签建议。")


def render_cover_tool(uploaded_files):
    st.markdown("#### 首图文字卡片")
    st.caption("生成带大标题的封面图，作为轮播第一张。可选纯色背景或用一张图当背景。")

    title = st.text_input("大标题", key="cover_title", placeholder="例如：周末去海边🌊")
    subtitle = st.text_input("副标题（可选）", key="cover_subtitle", placeholder="记录治愈的一天")

    col1, col2 = st.columns(2)
    with col1:
        size_label = st.selectbox("封面尺寸", options=list(COVER_SIZES.keys()), key="cover_size")
        position = st.selectbox(
            "标题位置",
            options=[value for _, value in TITLE_POSITIONS],
            format_func=lambda v: {p[1]: p[0] for p in TITLE_POSITIONS}.get(v, v),
            key="cover_position",
        )
    with col2:
        use_bg = st.toggle("用上传的第一张图作背景", key="cover_use_bg")
        bg_color = st.color_picker("纯色背景颜色", value="#1f1f1f", key="cover_bg_color",
                                   disabled=use_bg)
        text_color = st.color_picker("文字颜色", value="#ffffff", key="cover_text_color")

    if st.button("生成封面", type="primary", use_container_width=True, key="cover_run"):
        if not title.strip():
            st.warning("请先填写大标题。")
            return
        size = COVER_SIZES[size_label]
        background_image = None
        if use_bg:
            if not uploaded_files:
                st.warning("勾选了用图片背景，但还没上传图片。")
                return
            named = _load_uploaded_images(uploaded_files[:1])
            if named:
                background_image = named[0][1]

        cover = make_cover(
            title, subtitle, size=size, background_image=background_image,
            bg_color=bg_color, text_color=text_color, position=position,
        )
        if background_image is not None:
            background_image.close()

        out_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "cover.jpg"
        save_jpg(cover, target)

        st.success("封面已生成。")
        st.image(cover, use_container_width=True)
        with open(target, "rb") as file_obj:
            st.download_button(
                "下载封面",
                data=file_obj.read(),
                file_name="cover.jpg",
                mime="image/jpeg",
                use_container_width=True,
            )
        cover.close()


def render_multi_ratio_tool(uploaded_files):
    st.markdown("#### 一键多比例导出")
    st.caption("把每张图同时导出成多个常用比例，发不同位置直接用。")

    ratio_labels = st.multiselect(
        "选择要导出的比例",
        options=list(MULTI_RATIO_SIZES.keys()),
        default=list(MULTI_RATIO_SIZES.keys())[:2],
        key="multi_ratio_labels",
    )

    crop_mode = st.selectbox(
        "裁切方式",
        options=["crop", "smart"],
        format_func=lambda value: "居中裁切" if value == "crop" else "智能裁切（主体 / 人脸感知）",
        key="multi_ratio_crop_mode",
        help="智能裁切会尽量避开主体 / 人脸；缺少可选依赖时自动回退居中裁切。",
    )

    if not uploaded_files:
        st.info("先在上方上传图片。")
        return
    if not ratio_labels:
        st.info("至少选择一个比例。")
        return

    if st.button("开始导出", type="primary", use_container_width=True, key="multi_ratio_run"):
        sizes = [MULTI_RATIO_SIZES[label] for label in ratio_labels]
        named_images = _load_uploaded_images(uploaded_files)
        all_results: list[tuple[str, Image.Image]] = []
        for name, image in named_images:
            stem = Path(name).stem
            if crop_mode == "smart":
                # 智能裁切：resize_image_by_mode 只懂 padding/crop/stretch，
                # smart 直接逐尺寸调用 smart_crop（主体 / 人脸感知，失败自动回退居中）。
                for (w, h) in sizes:
                    out_img = smart_crop(image, w, h)
                    all_results.append((f"{stem}_{w}x{h}", out_img))
            else:
                for (w, h), out_img in export_multi_ratio(image, sizes, mode="crop"):
                    all_results.append((f"{stem}_{w}x{h}", out_img))
            image.close()

        if not all_results:
            st.warning("没有生成任何图片。")
            return

        out_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT / "multi_ratio"
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        paths = _save_images_to_dir(all_results, out_dir)

        st.success(f"导出完成，共 {len(paths)} 张。")
        st.download_button(
            "下载结果 ZIP",
            data=make_zip_bytes(paths),
            file_name="xiaohongshu-multi-ratio.zip",
            mime="application/zip",
            use_container_width=True,
        )
        _render_image_grid([img for _, img in all_results],
                           captions=[name for name, _ in all_results])


ANNOTATION_STYLE_OPTIONS = [
    ("气泡（圆角底 + 文字）", "bubble"),
    ("纯文字（描边）", "plain"),
    ("价格标签（高亮底）", "price"),
]
ANNOTATION_STYLE_LABELS = {value: label for label, value in ANNOTATION_STYLE_OPTIONS}

COMPOSE_MODE_STACK = "长图拼接"
COMPOSE_MODE_COMPARISON = "前后对比"

COMPARISON_LAYOUT_OPTIONS = [
    ("左右", "lr"),
    ("上下", "tb"),
]
COMPARISON_LAYOUT_LABELS = {value: label for label, value in COMPARISON_LAYOUT_OPTIONS}


def render_annotation_tool(uploaded_files):
    st.markdown("#### 图上标注（文字贴纸）")
    st.caption("给第一张图片加文字气泡、纯文字或价格标签，突出探店 / 好物 / 教程的重点信息。")

    if not uploaded_files:
        st.info("先在上方上传图片，再来这里加标注。")
        return

    st.caption(f"已上传 {len(uploaded_files)} 张，将对第一张图片添加标注。")

    annotation_count = st.slider(
        "标注数量", min_value=1, max_value=3, value=1, key="xhs_anno_count"
    )

    annotations: list[Annotation] = []
    for i in range(int(annotation_count)):
        with st.expander(f"标注 {i + 1}", expanded=(i == 0)):
            text = st.text_input("文字", key=f"xhs_anno_text_{i}", placeholder="例如：必点！")
            style = st.selectbox(
                "样式",
                options=[value for _, value in ANNOTATION_STYLE_OPTIONS],
                format_func=lambda value: ANNOTATION_STYLE_LABELS.get(value, value),
                key=f"xhs_anno_style_{i}",
            )
            pos_cols = st.columns(2)
            x = pos_cols[0].slider(
                "水平位置", min_value=0.0, max_value=1.0, value=0.1, step=0.01,
                key=f"xhs_anno_x_{i}",
            )
            y = pos_cols[1].slider(
                "垂直位置", min_value=0.0, max_value=1.0, value=0.1, step=0.01,
                key=f"xhs_anno_y_{i}",
            )
            color_cols = st.columns(2)
            text_color = color_cols[0].color_picker(
                "文字颜色", value="#ffffff", key=f"xhs_anno_text_color_{i}"
            )
            bg_color = color_cols[1].color_picker(
                "底色 / 描边色", value="#000000", key=f"xhs_anno_bg_color_{i}"
            )
            font_scale = st.slider(
                "字号（相对短边）", min_value=0.02, max_value=0.15, value=0.04, step=0.01,
                key=f"xhs_anno_scale_{i}",
            )
        annotations.append(Annotation(
            text=text,
            x=float(x),
            y=float(y),
            style=style,
            text_color=text_color,
            bg_color=bg_color,
            font_scale=float(font_scale),
        ))

    if st.button("生成标注图", type="primary", use_container_width=True, key="xhs_anno_run"):
        named_images = _load_uploaded_images(uploaded_files)
        if not named_images:
            st.warning("没有可用图片。")
            return
        if not any(ann.text and ann.text.strip() for ann in annotations):
            st.warning("请至少填写一条标注文字。")
            for _, img in named_images:
                img.close()
            return

        name, image = named_images[0]
        result = add_annotations(image, annotations)
        for _, img in named_images:
            img.close()

        out_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT / "annotated"
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{Path(name).stem}_annotated.jpg"
        save_jpg(result, target)

        st.success("标注完成。")
        st.image(result, use_container_width=True)
        with open(target, "rb") as file_obj:
            st.download_button(
                "下载标注图",
                data=file_obj.read(),
                file_name=target.name,
                mime="image/jpeg",
                use_container_width=True,
                key="xhs_anno_download",
            )
        result.close()


def render_compose_tool(uploaded_files):
    st.markdown("#### 长图拼接 / 前后对比")
    st.caption("把多张图竖向拼成长图，或把两张图拼成左右 / 上下对比图。")

    mode = st.radio(
        "拼接模式",
        options=[COMPOSE_MODE_STACK, COMPOSE_MODE_COMPARISON],
        horizontal=True,
        key="xhs_compose_mode",
    )

    if mode == COMPOSE_MODE_STACK:
        opt_cols = st.columns(2)
        gap = opt_cols[0].number_input(
            "图片间距(px)", min_value=0, max_value=200, value=0, key="xhs_compose_stack_gap"
        )
        bg_color = opt_cols[1].color_picker(
            "背景色", value="#ffffff", key="xhs_compose_stack_bg"
        )

        if not uploaded_files:
            st.info("先在上方上传图片。")
            return
        if len(uploaded_files) < 2:
            st.info("长图拼接至少需要 2 张图片，请再上传几张。")
            return

        st.caption(f"将按上传顺序竖向拼接全部 {len(uploaded_files)} 张图片。")
        if st.button("生成长图", type="primary", use_container_width=True, key="xhs_compose_stack_run"):
            named_images = _load_uploaded_images(uploaded_files)
            images = [img for _, img in named_images]
            if len(images) < 2:
                st.warning("可用图片不足 2 张。")
                for img in images:
                    img.close()
                return
            try:
                result = stack_vertical(images, gap=int(gap), bg_color=bg_color)
            except ValueError as exc:
                st.error(f"拼接失败：{exc}")
                for img in images:
                    img.close()
                return

            out_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT / "composed"
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / "stack.jpg"
            save_jpg(result, target)

            st.success("长图拼接完成。")
            st.image(result, use_container_width=True)
            with open(target, "rb") as file_obj:
                st.download_button(
                    "下载长图",
                    data=file_obj.read(),
                    file_name="stack.jpg",
                    mime="image/jpeg",
                    use_container_width=True,
                    key="xhs_compose_stack_download",
                )
            result.close()
            for img in images:
                img.close()
    else:
        layout = st.selectbox(
            "对比布局",
            options=[value for _, value in COMPARISON_LAYOUT_OPTIONS],
            format_func=lambda value: COMPARISON_LAYOUT_LABELS.get(value, value),
            key="xhs_compose_cmp_layout",
        )
        opt_cols = st.columns(2)
        gap = opt_cols[0].number_input(
            "中缝间距(px)", min_value=0, max_value=200, value=8, key="xhs_compose_cmp_gap"
        )
        divider = opt_cols[1].toggle("中缝分隔线", value=True, key="xhs_compose_cmp_divider")
        label_cols = st.columns(2)
        label_a = label_cols[0].text_input("左 / 上标签", value="Before", key="xhs_compose_cmp_label_a")
        label_b = label_cols[1].text_input("右 / 下标签", value="After", key="xhs_compose_cmp_label_b")

        if not uploaded_files:
            st.info("先在上方上传图片。")
            return
        if len(uploaded_files) < 2:
            st.info("前后对比至少需要 2 张图片，请再上传一张。")
            return

        st.caption(f"将使用前 2 张图片做对比（已上传 {len(uploaded_files)} 张）。")
        if st.button("生成对比图", type="primary", use_container_width=True, key="xhs_compose_cmp_run"):
            named_images = _load_uploaded_images(uploaded_files)
            images = [img for _, img in named_images]
            if len(images) < 2:
                st.warning("可用图片不足 2 张。")
                for img in images:
                    img.close()
                return

            labels = None
            if (label_a and label_a.strip()) or (label_b and label_b.strip()):
                labels = (label_a, label_b)
            try:
                result = make_comparison(
                    images[0], images[1], layout=layout,
                    gap=int(gap), divider=bool(divider), labels=labels,
                )
            except ValueError as exc:
                st.error(f"对比图生成失败：{exc}")
                for img in images:
                    img.close()
                return

            out_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT / "composed"
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / "comparison.jpg"
            save_jpg(result, target)

            st.success("对比图完成。")
            st.image(result, use_container_width=True)
            with open(target, "rb") as file_obj:
                st.download_button(
                    "下载对比图",
                    data=file_obj.read(),
                    file_name="comparison.jpg",
                    mime="image/jpeg",
                    use_container_width=True,
                    key="xhs_compose_cmp_download",
                )
            result.close()
            for img in images:
                img.close()


def render_selection_tool(uploaded_files):
    st.markdown("#### 选片 / 打星")
    st.caption("浏览这组照片，勾选要发布的、给心仪的打星，按条件过滤后导出选中。AI 可帮你挑片。")

    if not uploaded_files:
        st.info("先在上方上传图片，再来这里挑片。")
        return

    # 1) 把上传的图片落盘，供基于路径的 selection_service 读取（文件集合变化才重写）。
    src_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT / "_selection_src"
    current_names = [uploaded_file.name for uploaded_file in uploaded_files]
    if st.session_state.get("xhs_selection_src_names") != current_names:
        if src_dir.exists():
            shutil.rmtree(src_dir, ignore_errors=True)
        src_dir.mkdir(parents=True, exist_ok=True)
        for uploaded_file in uploaded_files:
            (src_dir / uploaded_file.name).write_bytes(uploaded_file.getbuffer())
        st.session_state["xhs_selection_src_names"] = current_names
        st.session_state.pop("xhs_selection_ai_reason", None)

    # 2) 构建选片模型（读取失败的图自动跳过，需求 1.7）。
    items = build_selection([src_dir / name for name in current_names])
    if not items:
        st.warning("这组图片都无法读取，换一组试试。")
        return
    valid_names = [item.path.name for item in items]
    valid_paths = [item.path for item in items]

    def _sel_key(name):
        return f"xhs_sel_selected_{name}"

    def _star_key(name):
        return f"xhs_sel_stars_{name}"

    # 选片状态直接以 widget key 持久化在 session_state（唯一可信来源，跨 rerun / 过滤稳定）。
    for name in valid_names:
        st.session_state.setdefault(_sel_key(name), False)
        st.session_state.setdefault(_star_key(name), 0)

    # 3) 应用 AI 推荐：把推荐项标为选中（在控件实例化之前写入 session_state 是允许的）。
    pending = st.session_state.pop("xhs_selection_ai_pending", None)
    if pending:
        for name in pending:
            if name in valid_names:
                st.session_state[_sel_key(name)] = True

    # 4) 从 session_state 读取当前选片状态套回 items，供过滤、计数与导出使用。
    #    控件在下方才渲染，但用户交互后的新值在本次 rerun 开始时已写入 session_state。
    for item in items:
        item.selected = bool(st.session_state.get(_sel_key(item.path.name), False))
        item.stars = int(st.session_state.get(_star_key(item.path.name), 0))

    # 5) 过滤控件 + 导出。
    filter_cols = st.columns([1, 1.4])
    only_selected = filter_cols[0].toggle("仅看已选中", key="xhs_sel_only_selected")
    min_stars = filter_cols[1].slider("最低星级", min_value=0, max_value=5, key="xhs_sel_min_stars")

    visible = filter_items(items, only_selected=only_selected, min_stars=int(min_stars))
    selected_count = sum(1 for item in items if item.selected)
    st.caption(f"共 {len(items)} 张 ｜ 已选 {selected_count} 张 ｜ 当前显示 {len(visible)} 张")

    if st.button("导出选中", type="primary", use_container_width=True, key="xhs_sel_export"):
        if selected_count == 0:
            st.warning("还没有选中任何图片。")
        else:
            out_dir = ROOT_DIR / XIAOHONGSHU_TOOLS_OUTPUT / "selection"
            if out_dir.exists():
                shutil.rmtree(out_dir, ignore_errors=True)
            exported = export_selected(items, out_dir, add_index_prefix=True)
            if not exported:
                st.warning("导出失败，选中的图片都无法读取。")
            else:
                st.success(f"已导出 {len(exported)} 张到 {out_dir}/")
                st.download_button(
                    "下载选中结果 ZIP",
                    data=make_zip_bytes(exported),
                    file_name="xiaohongshu-selection.zip",
                    mime="application/zip",
                    use_container_width=True,
                    key="xhs_sel_export_zip",
                )

    reason = st.session_state.get("xhs_selection_ai_reason")
    if reason:
        st.info(f"AI 选图理由：{reason}")

    # 6) 缩略图网格（基于下采样，需求 1.6）。控件仅用 key 绑定状态，避免与 session_state 冲突。
    if not visible:
        st.caption("没有符合当前过滤条件的图片。")
    else:
        columns_per_row = 3
        columns = st.columns(columns_per_row)
        for index, item in enumerate(visible):
            name = item.path.name
            with columns[index % columns_per_row]:
                try:
                    thumb = make_thumbnail(item.path, max_side=360)
                except SelectionError:
                    continue
                st.image(thumb, use_container_width=True, caption=name)
                st.checkbox("选中", key=_sel_key(name))
                st.selectbox(
                    "星级",
                    options=[0, 1, 2, 3, 4, 5],
                    format_func=lambda n: "未评分" if n == 0 else "★" * n,
                    key=_star_key(name),
                )

    # 7) AI 选图（缺 openai/Key 时提示而不影响上面的手动选片，需求 2.7）。
    st.divider()
    st.markdown("##### AI 智能选图")
    st.caption("让 AI 从这组照片里挑出最适合发小红书的几张，并标注为选中。")

    if not is_openai_available():
        st.warning("未检测到 openai 依赖，AI 选图不可用（不影响上面的手动选片）。请先执行：pip3 install openai")
        return

    target_count = st.number_input(
        "目标张数",
        min_value=1,
        max_value=len(valid_names),
        value=min(9, len(valid_names)),
        key="xhs_sel_target",
    )
    num_send = min(len(valid_names), 12)
    usage = estimate_usage(num_send)
    st.caption(
        f"将参考前 {num_send} 张图片 ｜ 预计输入 ~{usage['approx_input_tokens']} tokens（{usage['level']}）"
    )
    st.text_area(
        "发布目标 / 想表达的内容（可选）",
        key="xhs_sel_goal",
        placeholder="例如：想发一组治愈风的海边照片，优先构图干净、色调统一的。",
    )
    ai_cfg = render_ai_settings("selection")

    if st.button("用 AI 帮我选图", type="primary", use_container_width=True, key="xhs_sel_ai_run"):
        api_key = ai_cfg["api_key"]
        if not api_key:
            st.warning("请先填写 API Key。")
            return
        try:
            with st.spinner("AI 正在看图选片..."):
                result = select_best_images(
                    image_paths=valid_paths,
                    target_count=int(target_count),
                    user_goal=st.session_state.get("xhs_sel_goal", ""),
                    api_key=api_key,
                    model=ai_cfg["model"],
                    base_url=ai_cfg["base_url"],
                )
            order = result.get("order", [])
            recommended = [valid_names[i] for i in order if 0 <= i < len(valid_names)]
            if not recommended:
                st.warning("AI 没有给出可用的推荐，请重试或更换模型。")
                return
            # 仅把推荐项标为选中（下次 rerun 在控件实例化前应用），保留用户已有的手动选片结果。
            st.session_state["xhs_selection_ai_pending"] = recommended
            st.session_state["xhs_selection_ai_reason"] = result.get("reason", "")
            st.success(f"AI 推荐了 {len(recommended)} 张，已标注为选中。")
            st.rerun()
        except AIServiceError as exc:
            # 失败时不动任何已有选片状态（需求 2.6）。
            st.error(str(exc))
        except Exception as exc:
            st.error(f"AI 选图失败：{exc}")


def _persist_preview_sample(uploaded_file) -> Path:
    """把上传的样图落盘到工作目录，供基于路径的 render_preview 读取。"""
    sample_dir = ROOT_DIR / "output_web" / "_preview_sample"
    sample_dir.mkdir(parents=True, exist_ok=True)
    # 清理旧样图，避免目录堆积
    for old in sample_dir.glob("*"):
        if old.name != uploaded_file.name:
            try:
                old.unlink()
            except OSError:
                pass
    sample_path = sample_dir / uploaded_file.name
    sample_path.write_bytes(uploaded_file.getbuffer())
    return sample_path


def resolve_preview_sample(source_mode) -> Path | None:
    """
    选取实时预览的样图：上传模式取第一张上传图（落盘成路径），
    input 模式取 input/ 目录的第一张图；都没有则返回 None（需求 4.5）。
    """
    if source_mode == "上传图片":
        uploaded = st.session_state.get("workbench_uploader")
        if uploaded:
            try:
                return _persist_preview_sample(uploaded[0])
            except Exception:
                return None
        return None

    input_files = list_input_images(ROOT_DIR / "input")
    return input_files[0] if input_files else None


def render_live_preview(overrides, source_mode):
    """
    实时预览（需求 4）：对样图用与正式输出相同的处理链生成下采样预览；
    缓存上次成功预览，出错时回退展示并提示（需求 4.4）；无样图时提示（需求 4.5）。
    """
    with st.expander("实时预览", expanded=True):
        st.caption("调整左侧参数后即时预览效果，基于下采样样图，与最终输出使用同一处理链。")
        sample_path = resolve_preview_sample(source_mode)
        if sample_path is None:
            st.info("还没有样图。先上传图片或在 input/ 放入图片，就能看到参数效果的实时预览。")
            return

        try:
            runtime_config = build_runtime_config(CONFIG_PATH, overrides)
            preview_image = render_preview(sample_path, runtime_config, max_side=700)
            buffer = io.BytesIO()
            preview_image.save(buffer, format="PNG")
            preview_bytes = buffer.getvalue()
            preview_image.close()
            st.session_state["last_preview_bytes"] = preview_bytes
            st.image(preview_bytes, use_container_width=True, caption=f"样图：{sample_path.name}")
        except PreviewError as exc:
            _show_preview_fallback(str(exc))
        except Exception as exc:  # 预览绝不影响正式处理入口
            _show_preview_fallback(str(exc))


def _show_preview_fallback(message: str):
    """预览出错时：提示并回退到上一次成功的预览（需求 4.4）。"""
    st.warning(f"预览生成失败，已保留上一次成功的预览。原因：{message}")
    last = st.session_state.get("last_preview_bytes")
    if last:
        st.image(last, use_container_width=True, caption="上一次成功的预览")
    else:
        st.caption("暂时没有可显示的预览，调整参数或更换样图后重试。")


def render_xiaohongshu_tab():
    st.subheader("小红书多图工具")
    st.caption("这些工具独立于水印处理，直接上传图片即可用。")
    uploaded_files = st.file_uploader(
        "上传图片",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="xhs_uploader",
    )

    tool_tabs = st.tabs([
        "选片", "长图切割 / 九宫格", "拼图封面", "页码角标",
        "首图卡片", "多比例导出", "标注", "长图/对比", "AI 文案标签", "AI 风格建议",
    ])
    with tool_tabs[0]:
        render_selection_tool(uploaded_files)
    with tool_tabs[1]:
        render_split_tool(uploaded_files)
    with tool_tabs[2]:
        render_collage_tool(uploaded_files)
    with tool_tabs[3]:
        render_page_number_tool(uploaded_files)
    with tool_tabs[4]:
        render_cover_tool(uploaded_files)
    with tool_tabs[5]:
        render_multi_ratio_tool(uploaded_files)
    with tool_tabs[6]:
        render_annotation_tool(uploaded_files)
    with tool_tabs[7]:
        render_compose_tool(uploaded_files)
    with tool_tabs[8]:
        render_caption_tool(uploaded_files)
    with tool_tabs[9]:
        render_style_suggestion_tool(uploaded_files)


def main():
    st.set_page_config(page_title="Semi-Utils Frontend", page_icon="🖼️", layout="wide")
    inject_styles()

    default_config = build_runtime_config(CONFIG_PATH)
    overrides = build_overrides(default_config)

    st.markdown(
        """
        <div class="hero">
            <h1>Semi-Utils</h1>
            <p>左侧调整参数，中间选择图片来源，处理完成后在下方查看前后对比并下载结果。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    workbench_tab, xiaohongshu_tab, history_tab = st.tabs(["工作台", "小红书工具", "历史记录"])

    with workbench_tab:
        source_mode = st.segmented_control(
            "图片来源",
            options=["上传图片", "input 文件夹"],
            default="上传图片",
        )

        # 实时预览：放在工作台顶部，调整侧边栏参数即可看到效果（需求 4）。
        # 样图来自 session_state 中的上传控件或 input/ 目录，跨 rerun 稳定。
        render_live_preview(overrides, source_mode)

        if source_mode == "上传图片":
            process_uploaded_files(overrides)
        else:
            process_input_folder(overrides)

        latest_results = deserialize_results(st.session_state.get("latest_results", []))
        if latest_results:
            render_results(latest_results)

    with xiaohongshu_tab:
        render_xiaohongshu_tab()

    with history_tab:
        render_processing_history()


if __name__ == "__main__":
    main()
