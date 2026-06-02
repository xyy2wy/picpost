"""
AI 助手服务：调用视觉大模型，根据样图生成批量处理预设、小红书文案。

设计要点：
- 统一走 OpenAI 的 Chat Completions API（``chat.completions.create``）。
  该接口是 OpenAI 与 Google Gemini（OpenAI 兼容端点）共同支持的最大公约数，
  因此可以用同一份代码对接多家服务商，只需切换 base_url 与模型名。
- openai 为可选依赖，采用延迟导入，未安装时本模块仍可被导入（便于纯逻辑测试）。
- 图片在发送前统一下采样为小尺寸 JPEG，显著降低上传体积、token 成本与延迟。
- 优先用 ``response_format=json_object`` 强制模型输出 JSON，不支持时自动回退。
- 统一封装 client 构造、请求调用与错误转换，向用户暴露友好的中文错误信息。
"""
from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from PIL import ImageOps

from services.color import FILTER_PRESETS
from services.processing import LAYOUT_OPTIONS
from services.processing import UNIFORM_RESIZE_MODES


LAYOUT_IDS = [value for _, value in LAYOUT_OPTIONS]
UNIFORM_MODE_IDS = [value for _, value in UNIFORM_RESIZE_MODES]

# 发送给模型前图片下采样的最长边（像素）与 JPEG 质量。
# 1024px 足够模型判断风格与内容，却比原图小一两个数量级。
MAX_IMAGE_SIDE = 1024
ENCODE_QUALITY = 85

# 请求超时与重试（交给 OpenAI SDK 内置机制处理）。
REQUEST_TIMEOUT = 60.0
MAX_RETRIES = 2

# 小红书竖图默认高度（3:4），与处理管线默认值保持一致。
DEFAULT_UNIFORM_WIDTH = 1080
DEFAULT_UNIFORM_HEIGHT = 1440

# 预设建议综合判断时最多参考的图片数量。
MAX_PRESET_IMAGES = 3


# ---------- 服务商与模型目录 ----------

@dataclass(frozen=True)
class AIProvider:
    """一个可对接的服务商配置。"""
    id: str
    label: str
    base_url: str | None          # None 表示使用 OpenAI SDK 默认地址
    api_key_env: str              # 推荐的环境变量名（用于自动读取 Key）
    models: tuple[tuple[str, str], ...]  # (展示名, 模型值)


# 「自定义」占位值，便于 UI 识别并展开自定义模型输入框。
CUSTOM_MODEL_VALUE = "__custom__"

# Gemini 的 OpenAI 兼容端点。
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

AI_PROVIDERS: tuple[AIProvider, ...] = (
    AIProvider(
        id="openai",
        label="OpenAI",
        base_url=None,
        api_key_env="OPENAI_API_KEY",
        models=(
            ("gpt-5（综合最强，较慢）", "gpt-5"),
            ("gpt-5-mini（更快更省）", "gpt-5-mini"),
            ("gpt-4.1（稳健均衡）", "gpt-4.1"),
            ("gpt-4.1-mini（快速经济）", "gpt-4.1-mini"),
            ("gpt-4o（多模态成熟）", "gpt-4o"),
            ("gpt-4o-mini（最省，适合大批量）", "gpt-4o-mini"),
        ),
    ),
    AIProvider(
        id="gemini",
        label="Google Gemini",
        base_url=GEMINI_BASE_URL,
        api_key_env="GEMINI_API_KEY",
        models=(
            ("gemini-2.5-pro（最强推理）", "gemini-2.5-pro"),
            ("gemini-2.5-flash（均衡快速）", "gemini-2.5-flash"),
            ("gemini-2.5-flash-lite（最省最快）", "gemini-2.5-flash-lite"),
            ("gemini-2.0-flash（上一代快速）", "gemini-2.0-flash"),
        ),
    ),
    AIProvider(
        id="custom",
        label="自定义（兼容 OpenAI 接口）",
        base_url=None,
        api_key_env="",
        models=(),
    ),
)

DEFAULT_PROVIDER_ID = "openai"
DEFAULT_MODEL = "gpt-5"


def get_provider(provider_id: str | None) -> AIProvider:
    """按 id 取服务商，找不到时回退到默认服务商。"""
    for provider in AI_PROVIDERS:
        if provider.id == provider_id:
            return provider
    return AI_PROVIDERS[0]


def resolve_model(model: str | None) -> str:
    """把可能为空的模型名归一为有效模型，空值回退到默认模型。"""
    model = (model or "").strip()
    return model or DEFAULT_MODEL


def resolve_base_url(provider_id: str | None, custom_base_url: str | None) -> str | None:
    """
    根据服务商决定最终 base_url：
    - gemini：固定使用其兼容端点（除非用户显式覆盖）
    - custom：使用用户填写的地址
    - openai：默认 None，允许用户覆盖（如代理/中转）
    """
    custom = (custom_base_url or "").strip()
    provider = get_provider(provider_id)
    if provider.id == "gemini":
        return custom or provider.base_url
    if provider.id == "custom":
        return custom or None
    return custom or provider.base_url


class AIServiceError(Exception):
    """AI 服务调用失败，携带面向用户的友好提示。"""


def is_openai_available() -> bool:
    """检测 openai 依赖是否已安装。"""
    try:
        import openai  # noqa: F401
        return True
    except ImportError:
        return False


# ---------- 成本预估 ----------

def estimate_usage(num_images: int) -> dict:
    """
    粗略估算一次请求的输入规模（仅供参考，实际计费以服务商为准）。
    下采样后的单图约对应 ~1.1k tokens，叠加固定文本提示。
    """
    num_images = max(0, int(num_images))
    image_tokens = num_images * 1100
    text_tokens = 600
    total = image_tokens + text_tokens
    if total <= 2000:
        level = "低"
    elif total <= 6000:
        level = "中"
    else:
        level = "偏高"
    return {
        "num_images": num_images,
        "approx_input_tokens": total,
        "level": level,
    }


# ---------- 图片编码 ----------

def _encode_image(image_path: Path) -> str:
    """
    将图片下采样为最长边不超过 MAX_IMAGE_SIDE 的 JPEG，再编码为 data URL。
    统一转成 JPEG 可避免大图直传带来的体积与 token 浪费。
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise AIServiceError(f"图片不存在：{image_path}")

    try:
        with Image.open(image_path) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode != "RGB":
                image = image.convert("RGB")

            width, height = image.size
            scale = min(1.0, MAX_IMAGE_SIDE / max(width, height))
            if scale < 1.0:
                image = image.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    Image.LANCZOS,
                )

            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=ENCODE_QUALITY)
    except AIServiceError:
        raise
    except Exception as exc:
        raise AIServiceError(f"图片读取或编码失败：{image_path.name}（{exc}）") from exc

    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _image_part(image_path: Path) -> dict:
    """构造 Chat Completions 的图片消息片段。"""
    return {"type": "image_url", "image_url": {"url": _encode_image(image_path)}}


def _text_part(text: str) -> dict:
    """构造 Chat Completions 的文本消息片段。"""
    return {"type": "text", "text": text}


def _extract_json(text: str) -> dict:
    """从模型输出中提取 JSON 对象，容忍 ``` 代码围栏和前后多余文本。"""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AIServiceError("AI 未返回合法 JSON，请重试或更换模型。")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AIServiceError(f"AI 返回的内容无法解析为 JSON：{exc}") from exc


# ---------- OpenAI 兼容调用封装 ----------

def _build_client(api_key: str, base_url: str | None):
    """构造 OpenAI client，统一注入超时与重试。base_url 兼容 Gemini 等服务。"""
    if not api_key or not api_key.strip():
        raise AIServiceError("请先填写 API Key。")
    try:
        import openai
    except ImportError as exc:
        raise AIServiceError("未安装 openai 依赖，请先执行：pip3 install openai") from exc

    kwargs: dict[str, Any] = {
        "api_key": api_key.strip(),
        "timeout": REQUEST_TIMEOUT,
        "max_retries": MAX_RETRIES,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return openai.OpenAI(**kwargs)


def _extract_api_error_message(exc) -> str:
    """从 OpenAI SDK 的 APIStatusError 中尽量取出服务端返回的简短错误描述。"""
    # 优先用结构化 body，其次回退到字符串化
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if message:
                return str(message)[:200]
        if body.get("message"):
            return str(body["message"])[:200]
    message = getattr(exc, "message", None)
    if message:
        return str(message)[:200]
    return ""


def _chat_json(client, *, model: str, system_text: str, user_content: list) -> str:
    """
    调用 Chat Completions 并返回文本输出。
    优先用 response_format=json_object 强制 JSON；若服务端不支持则自动回退。
    各类异常统一转换为友好的 AIServiceError。
    """
    import openai

    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_content},
    ]

    def _call(with_json_format: bool):
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if with_json_format:
            kwargs["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    try:
        try:
            response = _call(with_json_format=True)
        except openai.BadRequestError:
            # 某些兼容端点/模型不支持 response_format，去掉后重试一次
            response = _call(with_json_format=False)
    except openai.AuthenticationError as exc:
        raise AIServiceError("API Key 无效或无权限，请检查 Key 与服务商是否匹配。") from exc
    except openai.RateLimitError as exc:
        raise AIServiceError("请求过于频繁或额度不足，请稍后再试。") from exc
    except openai.APITimeoutError as exc:
        raise AIServiceError("请求超时，请检查网络后重试。") from exc
    except openai.APIConnectionError as exc:
        raise AIServiceError("无法连接 AI 服务，请检查网络或 Base URL 是否正确。") from exc
    except openai.NotFoundError as exc:
        raise AIServiceError("模型不存在或当前服务商不支持该模型，请更换模型。") from exc
    except openai.APIStatusError as exc:
        status = getattr(exc, "status_code", None)
        detail = _extract_api_error_message(exc)
        if status == 400:
            # 部分服务商（如 Gemini）会把无效 Key / 参数错误也归到 400
            hint = "请求被拒绝（HTTP 400），请检查 API Key、模型名或 Base URL 是否匹配当前服务商。"
        elif status in (401, 403):
            hint = "API Key 无效或无权限，请检查 Key 与服务商是否匹配。"
        else:
            hint = f"AI 服务返回错误（HTTP {status or '未知'}），请稍后重试。"
        if detail:
            hint = f"{hint}（{detail}）"
        raise AIServiceError(hint) from exc
    except openai.OpenAIError as exc:
        raise AIServiceError(f"AI 调用失败：{exc}") from exc

    try:
        output_text = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        output_text = None
    if not output_text:
        raise AIServiceError("AI 未返回任何内容，请重试或更换模型。")
    return output_text


def _collect_image_paths(image_path: Path | None,
                         image_paths: list[Path] | None,
                         limit: int) -> list[Path]:
    """合并单图/多图入参，去重并限制数量。"""
    paths: list[Path] = []
    if image_paths:
        paths.extend(Path(p) for p in image_paths)
    elif image_path is not None:
        paths.append(Path(image_path))
    if not paths:
        raise AIServiceError("请至少提供一张图片。")
    return paths[:limit]


# ---------- 预设建议 ----------

def suggest_preset_from_image(
    image_path: Path | None = None,
    user_goal: str = "",
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    base_url: str | None = None,
    image_paths: list[Path] | None = None,
) -> dict:
    """
    根据样图与用户目标，推荐一套适合批量处理的参数 preset。
    支持传入多张图片（image_paths）做综合判断；只传 image_path 时为单图模式。
    """
    client = _build_client(api_key, base_url)
    model = resolve_model(model)
    paths = _collect_image_paths(image_path, image_paths, MAX_PRESET_IMAGES)

    layout_ids = ", ".join(LAYOUT_IDS)
    uniform_mode_ids = ", ".join(UNIFORM_MODE_IDS)
    system_text = (
        "你是一个摄影后期参数助手。"
        "根据用户目标和样图，只输出一个 JSON 对象，不要输出解释。"
        "JSON 字段必须包含："
        "layout_type, logo_enable, logo_position, white_margin, white_margin_width, "
        "shadow_enable, equivalent_focal, original_ratio_padding, uniform_enable, "
        "uniform_mode, uniform_width, uniform_height, quality, reason。"
        f"layout_type 只能从这些值里选：{layout_ids}。"
        f"uniform_mode 只能从这些值里选：{uniform_mode_ids}。"
        "如果用户没有明确要求，不要启用阴影。"
        "如果用户强调平台统一尺寸，建议开启 uniform_enable。"
        "面向小红书竖图时，建议 uniform_width=1080、uniform_height=1440（3:4）。"
        "如果提供了多张图片，请给出一套适用于整组图片的稳妥参数。"
    )
    intro = (
        "请根据这组样图和我的目标，推荐一套适用于整组、适合批量处理的 preset。\n"
        if len(paths) > 1
        else "请根据这张样图和我的目标，推荐一套适合批量处理的 preset。\n"
    )
    user_content = [
        _text_part(
            f"{intro}我的目标：{user_goal.strip() or '（未填写，按通用稳妥方案）'}\n请返回 JSON。"
        ),
        *[_image_part(p) for p in paths],
    ]

    output_text = _chat_json(
        client, model=model, system_text=system_text, user_content=user_content
    )
    result = _extract_json(output_text)
    return _normalize_preset(result)


def _normalize_preset(result: dict) -> dict:
    """对模型返回的 preset 做类型与取值范围的兜底归一化。"""
    if result.get("layout_type") not in LAYOUT_IDS:
        result["layout_type"] = "watermark_right_logo"
    if result.get("uniform_mode") not in UNIFORM_MODE_IDS:
        result["uniform_mode"] = "padding"

    result["logo_enable"] = bool(result.get("logo_enable", False))
    result["white_margin"] = bool(result.get("white_margin", False))
    result["shadow_enable"] = bool(result.get("shadow_enable", False))
    result["equivalent_focal"] = bool(result.get("equivalent_focal", False))
    result["original_ratio_padding"] = bool(result.get("original_ratio_padding", False))
    result["uniform_enable"] = bool(result.get("uniform_enable", False))
    result["white_margin_width"] = _safe_int(result.get("white_margin_width"), 3, 0, 30)
    result["uniform_width"] = _safe_int(result.get("uniform_width"), DEFAULT_UNIFORM_WIDTH, 1)
    result["uniform_height"] = _safe_int(result.get("uniform_height"), DEFAULT_UNIFORM_HEIGHT, 1)
    result["quality"] = _safe_int(result.get("quality"), 100, 1, 100)
    result["logo_position"] = result.get("logo_position", "right")
    if result["logo_position"] not in ("left", "right"):
        result["logo_position"] = "right"
    result["reason"] = str(result.get("reason", "")).strip()
    return result


def _safe_int(value, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """把任意值安全转为 int，失败时取默认，并夹到给定范围。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


# ---------- AI 选图 ----------

def select_best_images(
    image_paths: list[Path],
    target_count: int,
    user_goal: str = "",
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    base_url: str | None = None,
    max_images: int = 12,
) -> dict:
    """
    让视觉模型从一组图片里挑出最适合发小红书的 ``target_count`` 张并给出推荐顺序。

    复用 ``_build_client`` / ``_chat_json`` / ``_image_part`` 的下采样与错误处理：
    每张图片前面附带一个 0 基索引的文本标注，模型据此返回 ``order`` 索引数组。

    返回 ``{"order": [int, ...], "reason": str}``，其中 ``order`` 为基于输入顺序的
    0 基索引（仅覆盖前 ``max_images`` 张），因此可直接用来映射回原始 ``image_paths``。
    """
    if not image_paths:
        raise AIServiceError("请至少提供一张图片。")

    client = _build_client(api_key, base_url)
    model = resolve_model(model)

    sent_paths = [Path(p) for p in image_paths[:max_images]]
    n = len(sent_paths)
    target = _safe_int(target_count, 1, 0, n)

    system_text = (
        "你是一个小红书图片策展助手。"
        f"用户会发来 {n} 张带索引的照片，索引从 0 开始编号。"
        f"请从中挑出最适合发小红书的 {target} 张，并给出推荐的展示顺序。"
        "只输出一个 JSON 对象，不要输出任何解释。"
        "JSON 必须且只包含两个字段："
        "order（数组，元素是被选中图片的 0 基索引整数，按推荐展示顺序排列，"
        "长度不超过目标张数，不得重复），"
        "reason（一句话中文理由）。"
        "order 中的每个索引都必须来自用户提供的图片索引范围，禁止编造超出范围的索引。"
    )
    user_content: list = [
        _text_part(
            "请从下面这组照片里挑选最适合发小红书的若干张。\n"
            f"我的发布目标：{user_goal.strip() or '（未填写，按通用稳妥方案）'}\n"
            f"目标张数：{target}\n"
            f"共 {n} 张照片，每张图片前都标注了它的 0 基索引。请返回 JSON。"
        ),
    ]
    for index, path in enumerate(sent_paths):
        user_content.append(_text_part(f"索引 {index}（第 {index + 1} 张）："))
        user_content.append(_image_part(path))

    output_text = _chat_json(
        client, model=model, system_text=system_text, user_content=user_content
    )
    result = _extract_json(output_text)
    return _normalize_selection(result, n=n, target=target)


def _coerce_index(value) -> int | None:
    """把模型返回的单个 order 元素安全转为 int，无法解析时返回 None。"""
    # bool 是 int 的子类，但语义上不是有效索引，单独排除
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                return None
            return int(number) if number.is_integer() else None
    return None


def _normalize_selection(result: dict, n: int, target: int) -> dict:
    """
    把模型返回的 ``order`` 夹取到合法范围（Property 4）：
    - 仅保留可解析为整数、且落在 ``[0, n)`` 的索引；
    - 去重，保留首次出现的顺序；
    - 截断到 ``target`` 张。
    非法/越界/重复/超量项一律安全忽略，绝不抛出异常。
    """
    n = _safe_int(n, 0, 0, None)
    target = _safe_int(target, n, 0, None)

    raw_order = result.get("order") if isinstance(result, dict) else None
    if not isinstance(raw_order, (list, tuple)):
        raw_order = []

    order: list[int] = []
    seen: set[int] = set()
    for item in raw_order:
        if len(order) >= target:
            break
        idx = _coerce_index(item)
        if idx is None or idx < 0 or idx >= n or idx in seen:
            continue
        seen.add(idx)
        order.append(idx)

    reason = ""
    if isinstance(result, dict):
        reason = str(result.get("reason", "")).strip()
    return {"order": order, "reason": reason}


# ---------- AI 风格 / 标签建议 ----------

def _normalize_tags(tags_raw) -> list[str]:
    """
    把模型返回的标签归一化为「去 # 的字符串列表」：
    - 字符串形式（如 "#x #y z"）按空白拆分；
    - 列表/元组形式逐项处理；
    - 其余类型视为空；
    - 每项去掉前导 # 与首尾空白，丢弃空白项。
    """
    if isinstance(tags_raw, str):
        tags_raw = [t.strip() for t in tags_raw.replace("#", " ").split() if t.strip()]
    elif not isinstance(tags_raw, (list, tuple)):
        tags_raw = []
    return [str(t).lstrip("#").strip() for t in tags_raw if str(t).strip()]


def _normalize_style_tags(result: dict) -> dict:
    """
    对风格/标签建议结果做离线可测的归一化：
    - ``filter`` 必须落在 ``FILTER_PRESETS`` 的键集合内，否则回退 ``"none"``（Property 7）；
    - ``tags`` 复用 ``_normalize_tags`` 去 # 归一化为字符串列表；
    - ``reason`` 仅在为字符串时保留（去空白），否则为空串。
    绝不抛出异常，便于不联网验证。
    """
    if not isinstance(result, dict):
        result = {}

    filter_value = result.get("filter")
    if not isinstance(filter_value, str) or filter_value not in FILTER_PRESETS:
        filter_value = "none"

    tags = _normalize_tags(result.get("tags"))

    reason_raw = result.get("reason", "")
    reason = reason_raw.strip() if isinstance(reason_raw, str) else ""

    return {"filter": filter_value, "tags": tags, "reason": reason}


def suggest_style_and_tags(
    image_paths: list[Path],
    user_goal: str = "",
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    base_url: str | None = None,
    max_images: int = 6,
) -> dict:
    """
    根据一张/一组图片，让视觉模型建议「适配的滤镜风格 + 话题标签 + 理由」。

    复用 ``_build_client`` / ``_chat_json`` / ``_image_part`` 的下采样与错误处理。
    返回 ``{"filter": <FILTER_PRESETS 键或 "none">, "tags": [str, ...], "reason": str}``：
    - ``filter`` 经 ``_normalize_style_tags`` 校验必落在现有滤镜集合内，否则回退 ``"none"``（Property 7）；
    - ``tags`` 为去 # 的话题标签列表；
    - ``reason`` 为简短中文理由。
    """
    if not image_paths:
        raise AIServiceError("请至少提供一张图片。")

    client = _build_client(api_key, base_url)
    model = resolve_model(model)

    sent_paths = [Path(p) for p in image_paths[:max_images]]
    filter_ids = ", ".join(FILTER_PRESETS.keys())

    system_text = (
        "你是一个小红书视觉风格顾问。"
        "根据用户提供的一张/一组照片和发布目标，建议最适配的滤镜风格与话题标签。"
        "只输出一个 JSON 对象，不要输出任何解释。"
        "JSON 必须且只包含三个字段：filter、tags、reason。"
        f"filter 只能从下面这些值里选择一个：{filter_ids}。"
        "（none=原图、fresh=清新、film=胶片、warm=暖调、cool=冷调、ins=Ins 风、mono=黑白）"
        "如果没有特别适配的滤镜，就用 none。"
        "tags 是一个数组，给出 6-10 个适合这组照片的话题标签，"
        "标签不要带 # 号，使用中文短词。"
        "reason 用一句话中文说明为什么推荐这个滤镜与方向。"
    )
    user_content: list = [
        _text_part(
            "请根据这组照片和我的发布目标，建议适配的滤镜风格与话题标签。\n"
            f"我的发布目标：{user_goal.strip() or '（未填写，按通用稳妥方案）'}\n"
            "请返回 JSON。"
        ),
        *[_image_part(p) for p in sent_paths],
    ]

    output_text = _chat_json(
        client, model=model, system_text=system_text, user_content=user_content
    )
    result = _extract_json(output_text)
    return _normalize_style_tags(result)


# ---------- 小红书文案 ----------

def generate_xiaohongshu_caption(
    image_paths: list[Path],
    user_goal: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str | None = None,
    max_images: int = 6,
    num_variants: int = 1,
) -> dict:
    """
    根据一组图片生成小红书风格的文案。
    num_variants 控制生成的候选数量（1-5）。
    返回结构（同时保留顶层字段以兼容单候选场景）：
        {
            "title": str,           # 第一个候选标题
            "body": str,            # 第一个候选正文
            "tags": [str, ...],
            "variants": [{"title": str, "body": str}, ...],
        }
    """
    if not image_paths:
        raise AIServiceError("请至少提供一张图片。")

    client = _build_client(api_key, base_url)
    model = resolve_model(model)
    num_variants = _safe_int(num_variants, 1, 1, 5)

    image_contents = [_image_part(path) for path in image_paths[:max_images]]
    goal_text = user_goal.strip() or "分享这组照片"

    if num_variants > 1:
        variant_rule = (
            f"请生成 {num_variants} 套不同风格的候选文案，放在 variants 数组里，"
            "每个元素包含 title（标题，20字以内，可带 emoji）和 body（正文，100-200字，"
            "亲切口语化，分行排版，适当 emoji）。"
            "另外用 tags 字段给出一组通用话题标签（6-10 个，不带 # 号）。"
        )
    else:
        variant_rule = (
            "请把文案放在 variants 数组里（只含 1 个元素），"
            "元素包含 title（标题，20字以内，可带 emoji）和 body（正文，100-200字，"
            "亲切口语化，分行排版，适当 emoji）。"
            "另外用 tags 字段给出话题标签（6-10 个，不带 # 号）。"
        )

    system_text = (
        "你是一个小红书爆款文案写手。"
        "根据用户提供的一组照片和发布目标，生成适合小红书的文案。"
        "只输出一个 JSON 对象，不要输出解释。"
        + variant_rule
        + "风格自然真诚，不要过度营销腔，贴合摄影/生活方式。"
    )
    user_content = [
        _text_part(
            "请根据这组照片和我的发布目标生成小红书文案。\n"
            f"发布目标：{goal_text}\n请返回 JSON。"
        ),
        *image_contents,
    ]

    output_text = _chat_json(
        client, model=model, system_text=system_text, user_content=user_content
    )
    result = _extract_json(output_text)
    return _normalize_caption(result)


def _normalize_caption(result: dict) -> dict:
    """归一化文案返回：统一成 variants 列表 + 顶层首条 + 去 # 的 tags 列表。"""
    variants_raw = result.get("variants")
    variants: list[dict] = []
    if isinstance(variants_raw, list):
        for item in variants_raw:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            body = str(item.get("body", "")).strip()
            if title or body:
                variants.append({"title": title, "body": body})

    # 兼容模型直接返回顶层 title/body（未用 variants）的情况
    if not variants:
        title = str(result.get("title", "")).strip()
        body = str(result.get("body", "")).strip()
        variants.append({"title": title, "body": body})

    tags = _normalize_tags(result.get("tags", []))

    return {
        "title": variants[0]["title"],
        "body": variants[0]["body"],
        "tags": tags,
        "variants": variants,
    }
