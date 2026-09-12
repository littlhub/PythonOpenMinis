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
        logger.warning("vision describe failed (%s): %s", model_id, exc)
        return VisionGroupResolver.failure_text(
            f"{model_id} 调用失败（{type(exc).__name__}: {exc}）"
        )

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
