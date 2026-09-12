"""模型能力（capability）—— 多选用途标签。

背景：设置层原先只把模型存成一个纯字符串，没有类型概念，UI 无法区分
「这是对话模型还是识图 / 生图 / 语音模型」。这里用**用途**维度的标签来
标注模型，一个模型可同时带多个标签（多模态 = 对话 + 识图）。

八类标签（顺序即 UI 展示顺序）：

* ``llm``       对话   —— 文本进、文本出
* ``vision``    识图   —— 图像进、文本出（qwen-vl / glm-4v / llava …）
* ``image``     生图   —— 文本进、图像出（dall-e / flux / SD …）
* ``model3d``   3D     —— 三维生成
* ``audio``     音频   —— 音频理解 / 转写（whisper / asr …）
* ``video``     视频   —— 视频理解
* ``audio_gen`` 生音频 —— 语音合成（tts / cosyvoice …）
* ``video_gen`` 生视频 —— 视频生成（sora / kling / veo …）

判定策略：**自动推断 + 可改**。``infer_capabilities`` 按模型 id 关键词猜一组，
用户在界面上勾选覆盖后以覆盖值为准（覆盖存于 provider 实例的 ``modelTypes``，
值是标签列表）。
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "CAP_LLM",
    "CAP_VISION",
    "CAP_IMAGE",
    "CAP_3D",
    "CAP_AUDIO",
    "CAP_VIDEO",
    "CAP_AUDIO_GEN",
    "CAP_VIDEO_GEN",
    "CAPABILITY_ORDER",
    "CAPABILITY_LABELS",
    "SLOT_CAPABILITIES",
    "SLOT_ORDER",
    "SLOT_LABELS",
    "capability_label",
    "capability_labels",
    "infer_capabilities",
    "normalize_capability",
    "normalize_capabilities",
    "is_valid_capability",
    "capabilities_catalog",
    "label_map",
    "resolve_capabilities",
    "pick_slot_model",
    "slot_accepts",
]

# --- the eight capabilities ------------------------------------------------
CAP_LLM = "llm"
CAP_VISION = "vision"
CAP_IMAGE = "image"
CAP_3D = "model3d"
CAP_AUDIO = "audio"
CAP_VIDEO = "video"
CAP_AUDIO_GEN = "audio_gen"
CAP_VIDEO_GEN = "video_gen"

#: Stable display order (UI checkboxes + slots).
CAPABILITY_ORDER: tuple[str, ...] = (
    CAP_LLM,
    CAP_VISION,
    CAP_IMAGE,
    CAP_3D,
    CAP_AUDIO,
    CAP_VIDEO,
    CAP_AUDIO_GEN,
    CAP_VIDEO_GEN,
)

CAPABILITY_LABELS: dict[str, str] = {
    CAP_LLM: "对话",
    CAP_VISION: "识图",
    CAP_IMAGE: "生图",
    CAP_3D: "3D",
    CAP_AUDIO: "音频",
    CAP_VIDEO: "视频",
    CAP_AUDIO_GEN: "生音频",
    CAP_VIDEO_GEN: "生视频",
}

#: Which capabilities may fill each purpose slot, best-first. The resolver
#: walks this list in order so a slot degrades gracefully (a 多模态 model —
#: tagged ``llm`` + ``vision`` — can fill the 识图 slot when no dedicated
#: vision model is configured).
SLOT_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "chat": (CAP_LLM,),
    "vision": (CAP_VISION,),
    "image": (CAP_IMAGE,),
    "model3d": (CAP_3D,),
    "audio": (CAP_AUDIO,),
    "video": (CAP_VIDEO,),
    "audio_gen": (CAP_AUDIO_GEN,),
    "video_gen": (CAP_VIDEO_GEN,),
}

#: Purpose slots in UI display order.
SLOT_ORDER: tuple[str, ...] = (
    "chat",
    "vision",
    "image",
    "model3d",
    "audio",
    "video",
    "audio_gen",
    "video_gen",
)

SLOT_LABELS: dict[str, str] = {
    "chat": "对话",
    "vision": "识图",
    "image": "生图",
    "model3d": "3D",
    "audio": "音频",
    "video": "视频",
    "audio_gen": "生音频",
    "video_gen": "生视频",
}


def capability_label(cap: str, extra_labels: dict[str, str] | None = None) -> str:
    if extra_labels and cap in extra_labels:
        return extra_labels[cap]
    return CAPABILITY_LABELS.get(cap, cap)


def capability_labels(
    caps: Iterable[str], extra_labels: dict[str, str] | None = None
) -> str:
    """Human list, e.g. ``"对话+识图"``."""
    return "+".join(capability_label(c, extra_labels) for c in caps)


def is_valid_capability(value: Any) -> bool:
    return isinstance(value, str) and value in CAPABILITY_LABELS


def slot_accepts(slot: str) -> tuple[str, ...]:
    return SLOT_CAPABILITIES.get(slot, ())


#: Legacy single-value normalisation targets that expand to several tags.
_LEGACY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "multimodal": (CAP_LLM, CAP_VISION),
    "multi-modal": (CAP_LLM, CAP_VISION),
}

_ALIASES: dict[str, str] = {
    "text": CAP_LLM,
    "chat": CAP_LLM,
    "llm_text": CAP_LLM,
    "vlm": CAP_VISION,
    "vision_input": CAP_VISION,
    "image_input": CAP_VISION,
    "image_understanding": CAP_VISION,
    "image_gen": CAP_IMAGE,
    "image_generation": CAP_IMAGE,
    "image_output": CAP_IMAGE,
    "text_to_image": CAP_IMAGE,
    "t2i": CAP_IMAGE,
    "3d": CAP_3D,
    "3d_gen": CAP_3D,
    "three_d": CAP_3D,
    "asr": CAP_AUDIO,
    "speech_to_text": CAP_AUDIO,
    "audio_input": CAP_AUDIO,
    "audio_understanding": CAP_AUDIO,
    "video_input": CAP_VIDEO,
    "video_understanding": CAP_VIDEO,
    "tts": CAP_AUDIO_GEN,
    "text_to_speech": CAP_AUDIO_GEN,
    "audio_output": CAP_AUDIO_GEN,
    "audio_generation": CAP_AUDIO_GEN,
    "video_output": CAP_VIDEO_GEN,
    "video_generation": CAP_VIDEO_GEN,
    "text_to_video": CAP_VIDEO_GEN,
    "t2v": CAP_VIDEO_GEN,
}


def _normalize_one(value: Any, extra: set[str] | None = None) -> str | None:
    """Coerce a single label string to a known capability, else ``None``.

    ``extra`` — user-defined custom type ids that are accepted verbatim.
    """
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    if extra and v in extra:
        return v
    low = v.lower()
    for suffix in ("_input", "_output", "-input", "-output"):
        if low.endswith(suffix):
            low = low[: -len(suffix)]
            break
    if low in CAPABILITY_LABELS:
        return low
    return _ALIASES.get(low)


def normalize_capability(value: Any) -> str | None:
    """Single-label coercion (kept for compatibility); ``None`` if unknown."""
    return _normalize_one(value)


def normalize_capabilities(value: Any, extra_ids: Iterable[str] = ()) -> list[str] | None:
    """Coerce a stored/UI value to a de-duped **ordered** capability list.

    Accepts a list/tuple/set of labels, or a single string (legacy storage).
    Legacy ``"multimodal"`` expands to ``["llm", "vision"]``. ``extra_ids``
    lets user-defined custom type ids pass through unchanged. Returns ``None``
    when the input is not a str/collection at all (caller keeps old value),
    and ``[]`` when it is an empty collection (caller clears).
    """
    extra = {str(x) for x in extra_ids}
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _LEGACY_EXPANSIONS:
            return list(_LEGACY_EXPANSIONS[low])
        one = _normalize_one(value, extra)
        return [one] if one else ([] if not low else None)
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            # a legacy expansion may appear inside a list too
            if isinstance(item, str) and item.strip().lower() in _LEGACY_EXPANSIONS:
                for c in _LEGACY_EXPANSIONS[item.strip().lower()]:
                    if c not in out:
                        out.append(c)
                continue
            one = _normalize_one(item, extra)
            if one and one not in out:
                out.append(one)
        # built-ins in canonical display order, then custom ids (insertion order)
        builtins = [c for c in CAPABILITY_ORDER if c in out]
        customs = [c for c in out if c not in CAPABILITY_ORDER]
        return builtins + customs
    return None


# ---------------------------------------------------------------------------
# Auto-inference from the model id
# ---------------------------------------------------------------------------
#: Checked FIRST — generation ids often also carry understanding-ish words
#: (``gpt-4o-audio`` / ``hunyuan-video``), so generation wins.
_VIDEO_GEN_HINTS: tuple[str, ...] = (
    "sora", "kling", "cogvideo", "hunyuan-video", "hunyuanvideo", "veo",
    "runway", "pika", "luma", "vidu", "seedance", "wan2",
    "mochi", "ltx-video", "dream-machine", "-t2v", "t2v-", "text-to-video",
    "i2v", "video-gen",
)

_AUDIO_GEN_HINTS: tuple[str, ...] = (
    "tts", "text-to-speech", "speech-synthesis", "cosyvoice", "sambert",
    "fish-speech", "fishaudio", "audio-gen", "sovits", "-vits", "musicgen",
    "audioldm", "speech-gen", "text2speech", "voice-clone", "voice-cloning",
    "gpt-4o-audio", "gpt-4o-mini-audio", "-audio-preview",
)

#: 生图 ids.
_IMAGE_HINTS: tuple[str, ...] = (
    "dall-e", "dalle", "gpt-image", "images/generations", "stable-diffusion",
    "stable_diffusion", "sdxl", "sd3", "sd-", "imagen", "midjourney", "mj-",
    "cogview", "kolors", "wanx", "seedream", "seededit", "qwen-image",
    "hunyuan-image", "hunyuanimage", "image-preview", "image-gen", "imagegen",
    "text-to-image", "t2i", "-image", "flux", "ideogram", "recraft",
    "playground-v",
)

#: 3D generation ids.
_3D_HINTS: tuple[str, ...] = (
    "3d", "tripo", "meshy", "instantmesh", "gaussian-splat", "splat",
    "shap-e", "point-e", "zero123", "trellis", "hunyuan3d", "hunyuan-3d",
    "rodin", "genie-3d",
)

#: Audio *understanding* ids (转写 / 语音理解).
_AUDIO_HINTS: tuple[str, ...] = (
    "whisper", "asr", "speech-to-text", "stt", "paraformer", "sensevoice",
    "qwen-audio", "audio-understanding", "-audio-", "speech_recognition",
)

#: Video *understanding* ids.
_VIDEO_HINTS: tuple[str, ...] = (
    "video-llava", "video-llama", "videollama", "qwen-vl-video",
    "video-preview", "video-understanding", "-video-",
)

#: Dedicated image-understanding models.
_VISION_HINTS: tuple[str, ...] = (
    "-vl-", "-vl", "vl-", "-4v", "4v-", "vision", "llava", "internvl",
    "minicpm-v", "pixtral", "moondream", "cogvlm", "qwen2-vl", "qwen3-vl",
    "step-1v", "glm-4v", "glm-4.5v", "glm-5v", "minicpm", "vl-max", "vl-plus",
    "vl-pro", "hunyuan-vision", "ernie-vl", "kimi-vl", "doubao-vision",
)

#: Chat families known to accept image input natively (→ 对话 + 识图).
#: Deliberately CONSERVATIVE: only families whose mainstream chat models
#: genuinely take images. Vendor brand names alone (kimi / doubao / ernie /
#: qwen-max / abab …) are mostly text-only, and marking those multimodal would
#: silently disable the 识图 fallback — the user can always re-classify in UI.
_MULTIMODAL_HINTS: tuple[str, ...] = (
    "gpt-4o", "gpt-4.1", "gpt-5", "chatgpt", "o3", "o4", "gemini",
    "claude", "grok-4", "llama-4", "llama4",
)


def infer_capabilities(model_id: Any) -> list[str]:
    """Best-effort capability tags from a model id (never throws).

    Order matters: 生成类（生视频/生音频/生图/3D）→ 理解类（音频/视频/识图）
    → 对话. Anything unrecognised is a plain text LLM — the safe default
    (the only tag that cannot silently mis-route media).
    """
    if not isinstance(model_id, str):
        return [CAP_LLM]
    lid = model_id.strip().lower()
    if not lid:
        return [CAP_LLM]
    # normalise separators so "gpt_image" / "gpt-image" both match
    flat = lid.replace("_", "-")

    def match(hints: tuple[str, ...]) -> bool:
        return any(h in flat for h in hints)

    if match(_VIDEO_GEN_HINTS):
        return [CAP_VIDEO_GEN]
    if match(_AUDIO_GEN_HINTS):
        return [CAP_AUDIO_GEN]
    if match(_IMAGE_HINTS):
        return [CAP_IMAGE]
    if match(_3D_HINTS):
        return [CAP_3D]
    if match(_AUDIO_HINTS):
        return [CAP_AUDIO]
    if match(_VIDEO_HINTS):
        return [CAP_VIDEO]
    if match(_VISION_HINTS) or match(_MULTIMODAL_HINTS):
        # 能看图，也通常能对话 —— 多模态 = 对话 + 识图
        return [CAP_LLM, CAP_VISION]
    return [CAP_LLM]


def resolve_capabilities(
    model_id: str,
    overrides: dict[str, Any] | None,
    extra_ids: Iterable[str] = (),
) -> tuple[list[str], str]:
    """Return ``(capabilities, source)`` where source is ``"user"``/``"auto"``.

    A user override always wins (even if it disagrees with the heuristic) —
    that is the whole point of "自动推断 + 可改". ``extra_ids`` are the ids of
    user-defined custom types, accepted verbatim in an override.
    """
    if isinstance(overrides, dict) and model_id in overrides:
        norm = normalize_capabilities(overrides.get(model_id), extra_ids)
        if norm:
            return norm, "user"
    return infer_capabilities(model_id), "auto"


def capabilities_catalog(
    custom: Iterable[dict[str, Any]] = (),
) -> list[dict[str, str]]:
    """``[{id, label}]`` for the UI picker: the 8 built-ins + custom types."""
    out: list[dict[str, str]] = [
        {"id": c, "label": CAPABILITY_LABELS[c]} for c in CAPABILITY_ORDER
    ]
    for ct in custom:
        cid = str(ct.get("id") or "").strip()
        if not cid:
            continue
        out.append({
            "id": cid,
            "label": str(ct.get("label") or cid),
            "custom": "1",
        })
    return out


def label_map(custom: Iterable[dict[str, Any]] = ()) -> dict[str, str]:
    """``{capability_id: label}`` covering built-ins + custom types."""
    out = dict(CAPABILITY_LABELS)
    for ct in custom:
        cid = str(ct.get("id") or "").strip()
        if cid:
            out[cid] = str(ct.get("label") or cid)
    return out


def pick_slot_model(
    candidates: Iterable[tuple[str, str, Any]], slot: str
) -> tuple[str, str] | None:
    """Choose the best ``(instance_id, model_id)`` for a purpose slot.

    ``candidates`` is ``[(instance_id, model_id, capabilities), …]`` where
    ``capabilities`` is a label list (a bare string is also accepted). Returns
    the first candidate that the slot accepts (best-first per
    :data:`SLOT_CAPABILITIES`), or ``None`` when nothing fits.
    """
    allowed = SLOT_CAPABILITIES.get(slot, ())
    if not allowed:
        return None
    best: tuple[str, str] | None = None
    best_rank = len(allowed)
    for inst, mid, caps in candidates:  # type: ignore[misc]
        caps_norm = normalize_capabilities(caps) or []
        ranks = [allowed.index(c) for c in caps_norm if c in allowed]
        if not ranks:
            continue
        rank = min(ranks)
        if rank < best_rank:
            best_rank = rank
            best = (inst, mid)
    return best
