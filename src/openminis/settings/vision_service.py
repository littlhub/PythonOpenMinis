"""识图（vision）服务 —— 用「识图槽」绑定的模型描述一张图片。

背景：agent loop 原先会丢弃工具返回的图片字节，``read_image`` 只能把
"路径+尺寸"的文字给模型，模型其实看不到图。现在两条路并存：

* 主对话模型**是多模态**时 —— 运行时把图片作为 image_parts 附到下一次请求
  （见 ``agent_runtime``），模型直接看图；
* 主对话模型**没有视觉**时 —— 走这里：把图片交给「识图槽」绑定的模型
  （``vision`` / ``multimodal`` 能力）描述，把**描述文本**当工具结果返回。
  这正是 Android 的 Vision Group 思路（GH#182）：主模型从不接触像素，
  只读一段明确标注为"不可信数据"的文本，避免图里的文字变成注入指令。

槽位没配或调用失败都不抛异常到 loop：返回可读的提示文本，让模型能如实
告诉用户"图没读成、为什么"，而不是整个回合崩掉。
"""

from __future__ import annotations

import time

from typing import Any

from ..core.logging import get_logger
from ..data.model import LLMMessage, ThinkingLevel
from ..tools.vision_group_resolver import (
    DESCRIBE_PROMPT,
    SYSTEM_PROMPT,
    VisionGroupResolver,
    VisionResult,
)

logger = get_logger(__name__)

__all__ = ["describe_image", "vision_slot_ready"]

#: 单次描述调用的输出上限 —— 描述用不着很长，限制它也就限制了费用。
_MAX_OUTPUT_TOKENS = 2048

#: 连续失败冷却：识图槽连续失败这么多次后进入冷却，冷却期内不再真正调用
#: provider（多为 429 限流，重试只会火上浇油），直接回「别再试」的提示。
_FAIL_STREAK_LIMIT = 2
_COOLDOWN_SECONDS = 30.0

_fail_state = {"streak": 0, "ts": 0.0}

#: 描述结果缓存 —— 同一张图+同一个问题在 TTL 内直接回缓存，不再打识图模型。
#: 背景：主 agent 常对同一张图反复调 read_image，每次都真调 API，撞上限流
#: 就变成「429 → 重试 → 429」的空转。缓存键含 prompt（不同问题不误伤）。
_DESC_CACHE_TTL = 600.0
_DESC_CACHE_MAX = 64
_DESC_CACHE: dict[tuple[str, str], tuple[float, str]] = {}


def _cache_get(key: tuple[str, str]) -> str | None:
    hit = _DESC_CACHE.get(key)
    if hit is None:
        return None
    ts, text = hit
    if time.monotonic() - ts > _DESC_CACHE_TTL:
        _DESC_CACHE.pop(key, None)
        return None
    return text


def _cache_put(key: tuple[str, str], text: str) -> None:
    if len(_DESC_CACHE) >= _DESC_CACHE_MAX:
        # 粗暴淘汰最旧的一条，够用了。
        oldest = min(_DESC_CACHE, key=lambda k: _DESC_CACHE[k][0])
        _DESC_CACHE.pop(oldest, None)
    _DESC_CACHE[key] = (time.monotonic(), text)


def _cooldown_active() -> bool:
    return _fail_state["streak"] >= _FAIL_STREAK_LIMIT and (
        time.monotonic() - _fail_state["ts"]
    ) < _COOLDOWN_SECONDS


def _no_retry_note() -> str:
    return (
        f"（识图服务已连续失败 {_fail_state['streak']} 次，通常是模型限流；"
        "请**停止重试** read_image，先把已有信息回答给用户，并建议稍后再试。）"
    )


def vision_slot_ready(store: Any) -> bool:
    """True when 识图槽 is bound to a usable (instance, model)."""
    try:
        return store.slot_binding("vision") is not None
    except Exception:  # pragma: no cover - settings unreadable
        return False


def _build_vision_provider(store: Any, conf: dict[str, Any], model_id: str):
    """Instantiate the provider for the vision slot's instance + model.

    Imported lazily: ``chat_service`` imports the provider engines and the
    catalog, and this module is reachable from the tool registry — a top-level
    import would risk a cycle.
    """
    from .chat_service import build_provider

    return build_provider(str(conf.get("id") or ""), {**conf, "model": model_id})


#: describe_image 失败时 failure_text 里的可识别标记 —— 用来判断「这条路没走通」。
_FAILURE_MARKERS = (
    "调用失败",
    "无法初始化",
    "返回了空描述",
    "未重试",
    "冷却期",
)


async def _describe_via_subagent(
    store: Any,
    image_path: str | None,
    prompt: str,
    session_id: str,
) -> str | None:
    """换一条路：委派识图子代理（子代理绑定的是**另一个**识图模型）。

    子代理内部按 inline 跑 ``read_image``，像素直达它自己的模型；产出是
    文字，回到主上下文时不带图。没配识图子代理或子代理没拿到路径时返回
    ``None``，调用方保留原 failure 文本。
    """
    if not image_path:
        return None
    try:
        from ..agent.subagents import find_vision_subagent, run_subagent

        sid = find_vision_subagent(store)
        if not sid:
            return None
        ask = f"请用 read_image 查看图片 {image_path}，然后完整描述图片内容。"
        if prompt.strip():
            ask += f"\n用户重点关注：{prompt.strip()}"
        out = await run_subagent(store, sid, ask, session_id or "vision-fallback")
        return out or None
    except Exception:
        logger.debug("vision fallback via subagent failed", exc_info=True)
        return None


async def describe_image_with_fallback(
    store: Any,
    image_bytes: bytes,
    mime_type: str,
    *,
    prompt: str = "",
    image_path: str | None = None,
    session_id: str = "",
) -> str | None:
    """识图槽失败/没配 → 自动切识图子代理（不同的识图模型）；两路都不通
    则返回原 failure 文本。"""
    key = (str(image_path or ""), prompt.strip())
    cached = _cache_get(key)
    if cached is not None:
        return (
            cached
            + "\n（识图结果缓存命中，未重复调用识图模型；"
            "同一张图不要反复调用 read_image，直接引用已有描述回答。）"
        )
    text = await describe_image(
        store, image_bytes, mime_type, prompt=prompt, image_path=image_path
    )
    failed = text is None or any(m in (text or "") for m in _FAILURE_MARKERS)
    if not failed:
        _cache_put(key, text or "")
        return text
    sub = await _describe_via_subagent(store, image_path, prompt, session_id)
    if sub:
        _cache_put(key, sub)
        return sub
    return text


async def describe_image(
    store: Any,
    image_bytes: bytes,
    mime_type: str,
    *,
    prompt: str = "",
    image_path: str | None = None,
) -> str | None:
    """Describe ``image_bytes`` via the 识图槽 model.

    Returns the framed description text (untrusted-data delimiters included),
    or ``None`` when no 识图槽 is configured — the caller then falls back to
    passing the raw bytes through. Never raises: failures come back as the
    resolver's ``failure_text`` so the agent loop stays intact.
    """
    try:
        binding = store.slot_binding("vision")
    except Exception:  # pragma: no cover - settings unreadable
        binding = None
    if binding is None:
        return None
    conf, model_id = binding

    try:
        provider = _build_vision_provider(store, conf, model_id)
    except Exception as exc:
        logger.warning("vision slot provider build failed: %s", exc)
        return VisionGroupResolver.failure_text(
            f"{model_id} 无法初始化（{type(exc).__name__}: {exc}）"
        )

    ask = prompt.strip() or DESCRIBE_PROMPT
    if _cooldown_active():
        return VisionGroupResolver.failure_text(
            f"{model_id} 刚刚连续失败（多为限流），冷却期内本次未重试。" + _no_retry_note()
        )
    try:
        resp = await provider.send_message(
            [LLMMessage(LLMMessage.Role.USER, ask)],
            SYSTEM_PROMPT,
            _MAX_OUTPUT_TOKENS,
            None,
            image_parts=[LLMMessage.ImagePart(data=image_bytes, mime_type=mime_type)],
            tools=None,
            thinking_level=ThinkingLevel.OFF,
        )
    except Exception as exc:
        _fail_state["streak"] += 1
        _fail_state["ts"] = time.monotonic()
        logger.warning("vision describe failed (%s): %s", model_id, exc)
        note = _no_retry_note() if _fail_state["streak"] >= _FAIL_STREAK_LIMIT else ""
        return VisionGroupResolver.failure_text(
            f"{model_id} 调用失败（{type(exc).__name__}: {exc}）{note}"
        )

    _fail_state["streak"] = 0
    text = (getattr(resp, "text", "") or "").strip()
    if not text:
        return VisionGroupResolver.failure_text(f"{model_id} 返回了空描述")
    framed = VisionGroupResolver.framed_description_success(
        VisionResult.Success(description=text, model_name=model_id),
        question=prompt if prompt.strip() else None,
    )
    if image_path:
        framed = f"[{image_path}]\n{framed}"
    return framed
