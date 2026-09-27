"""Manual compact — summarization inputs, message filtering and token estimation.

Ported from: packages/core/src/compact/manual.ts
Original package: @zcode/core (compact)
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Optional, Sequence, TypedDict, Union

from ..contracts.ids import MessageId, QueryId, SessionId, TraceId, TurnId
from ..contracts.model import (
    ModelMessageContent,
    model_message_content_block_to_text,
    model_message_content_to_text,
)
from .rounds import group_by_assistant_started_rounds

__all__ = [
    "TraceContext",
    "CompactTrigger",
    "CompactPhase",
    "CompactReason",
    "CompactPreservedSegment",
    "CompactBoundaryPayload",
    "CompactToolCall",
    "CompactModelMessage",
    "TokenUsageLike",
    "BuildManualCompactBoundaryInput",
    "MAX_COMPACT_PROMPT_TOO_LONG_RETRIES",
    "COMPACT_PROMPT_TOO_LONG_RETRY_MARKER",
    "COMPACT_PROMPT_TOO_LONG_USER_MESSAGE",
    "get_messages_to_summarize",
    "has_enough_messages_to_compact",
    "build_manual_compact_boundary",
    "estimate_message_tokens",
    "get_usage_total_tokens",
    "create_compact_boundary_id",
]


# ---------------------------------------------------------------------------
# PORT: stand-in contract types
# ---------------------------------------------------------------------------
# Declared in ``@zcode/contracts`` but not shipped by ``..contracts`` yet. Transcribed
# 1:1 so this module keeps the exact shapes; replace with contract imports once the
# contracts port grows these modules.


# PORT: 原 ``import { ESTIMATED_TOKEN_CHAR_DIVISOR } from "@zcode/shared"``。
# 该常量定义在 packages/shared/src/usage-stats.ts（仓库根 packages/shared，
# 非 apps/zcode-cli 内），值为 3。Python 侧没有跨包 shared 层，按原值内联。
ESTIMATED_TOKEN_CHAR_DIVISOR = 3

# PORT: TS ``const EMPTY_TOOL_CALL_INPUT_JSON = "{}"``（模块私有，非 export）。
_EMPTY_TOOL_CALL_INPUT_JSON = "{}"


# PORT: ``TraceContext`` — packages/contracts/src/tracing/tracer.ts（tracing 模块尚未移植）。
@dataclass(slots=True)
class TraceContext:
    """Mirrors ``interface TraceContext``."""

    trace_id: TraceId
    query_id: Optional[QueryId] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    parent_id: Optional[str] = None
    session_id: Optional[SessionId] = None
    turn_id: Optional[TurnId] = None
    attributes: Optional[dict[str, Union[str, int, bool]]] = None


# PORT: packages/contracts/src/compact/index.ts 的 ``as const`` 对象 → ``enum.Enum``
# （值保持原字符串，协议兼容）。
class CompactTrigger(str, Enum):
    MANUAL = "manual"
    AUTO = "auto"
    PARTIAL = "partial"
    REACTIVE = "reactive"
    SESSION_MEMORY = "session_memory"


class CompactPhase(str, Enum):
    STANDALONE_TURN = "standalone_turn"
    PRE_REQUEST = "pre_request"
    MID_TURN = "mid_turn"
    REACTIVE = "reactive"


class CompactReason(str, Enum):
    USER_REQUESTED = "user_requested"
    CONTEXT_LIMIT = "context_limit"
    MODEL_DOWNSHIFT = "model_downshift"
    PROVIDER_OVERFLOW = "provider_overflow"


class CompactPreservedSegment(TypedDict):
    """Mirrors ``CompactPreservedSegment`` (``compactPreservedSegmentSchema``)."""

    head_message_id: MessageId
    anchor_message_id: MessageId
    tail_message_id: MessageId


class _CompactBoundaryPayloadRequired(TypedDict):
    """``compactBoundaryPayloadSchema`` 的必填字段。"""

    boundary_id: str
    trigger: CompactTrigger
    pre_compact_token_count: int
    summarized_message_count: int
    summary_message_ids: list[MessageId]
    trace_id: TraceId


class CompactBoundaryPayload(_CompactBoundaryPayloadRequired, total=False):
    """Mirrors ``CompactBoundaryPayload`` (``compactBoundaryPayloadSchema``)."""

    phase: CompactPhase
    compact_reason: CompactReason
    summary_source: str  # "model" | "session_memory"
    post_compact_token_count: int
    true_post_compact_token_count: int
    auto_compact_threshold: int
    will_retrigger_next_turn: bool
    kept_message_count: int
    last_summarized_message_id: MessageId
    preserved_segment: CompactPreservedSegment
    attachment_message_ids: list[MessageId]
    hook_result_message_ids: list[MessageId]
    pre_compact_discovered_tools: list[str]
    custom_instructions: bool
    turn_id: TurnId
    span_id: str
    parent_span_id: str


# ---------------------------------------------------------------------------
# manual.ts — 类型
# ---------------------------------------------------------------------------


# PORT: TS 内联匿名形状 ``{ name: string; input: unknown }``。microcompact.ts 的
# ``LocalMicrocompactMessage`` 把它改写为 ``{ id: string; input: unknown; name: string }``；
# Python 侧用一个类型承载两种形状，``id`` 缺省为 ``None``。
@dataclass(slots=True)
class CompactToolCall:
    """The inline tool-call shape of ``CompactModelMessage``."""

    name: str
    input: Any
    id: Optional[str] = None


@dataclass(slots=True)
class CompactModelMessage:
    """Mirrors ``interface CompactModelMessage``."""

    role: str
    content: ModelMessageContent
    tool_calls: Optional[list[CompactToolCall]] = None


@dataclass(slots=True)
class TokenUsageLike:
    """Mirrors ``interface TokenUsageLike``."""

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None


@dataclass(slots=True)
class BuildManualCompactBoundaryInput:
    """Mirrors ``interface BuildManualCompactBoundaryInput``.

    # PORT: 必填字段上提到可选字段之前（dataclass 字段顺序约束），字段名全部保留。
    """

    boundary_id: str
    pre_compact_token_count: int
    summarized_message_count: int
    summary_message_id: MessageId
    trace_context: TraceContext

    auto_compact_threshold: Optional[int] = None
    compact_reason: Optional[CompactReason] = None
    custom_instructions: Optional[str] = None
    kept_message_count: Optional[int] = None
    last_summarized_message_id: Optional[MessageId] = None
    phase: Optional[CompactPhase] = None
    post_compact_token_count: Optional[int] = None
    preserved_segment: Optional[CompactPreservedSegment] = None
    trigger: Optional[CompactTrigger] = None
    true_post_compact_token_count: Optional[int] = None
    will_retrigger_next_turn: Optional[bool] = None


# ---------------------------------------------------------------------------
# manual.ts — 常量
# ---------------------------------------------------------------------------

MAX_COMPACT_PROMPT_TOO_LONG_RETRIES = 3
COMPACT_PROMPT_TOO_LONG_RETRY_MARKER = "[earlier conversation truncated for compaction retry]"
COMPACT_PROMPT_TOO_LONG_USER_MESSAGE = "Conversation too long to compact automatically. Try /compact again after narrowing the active context."


# ---------------------------------------------------------------------------
# manual.ts — 函数
# ---------------------------------------------------------------------------


def get_messages_to_summarize(
    messages: Sequence[CompactModelMessage],
) -> list[CompactModelMessage]:
    """Mirrors ``getMessagesToSummarize``."""
    # PORT: ``.filter(...).map((message) => ({ ...message }))`` → 列表推导 +
    # ``dataclasses.replace``（等价于 TS 的浅拷贝对象展开）。
    return [replace(message) for message in messages if not _is_context_prefix_message(message)]


def has_enough_messages_to_compact(messages: Sequence[CompactModelMessage]) -> bool:
    """Mirrors ``hasEnoughMessagesToCompact``."""
    messages_to_summarize = get_messages_to_summarize(messages)
    return len(_group_messages_by_compact_round(messages_to_summarize)) >= 2 and any(
        message.role == "assistant" for message in messages_to_summarize
    )


def build_manual_compact_boundary(
    input: BuildManualCompactBoundaryInput,
) -> CompactBoundaryPayload:
    """Mirrors ``buildManualCompactBoundary``."""
    return {
        "boundary_id": input.boundary_id,
        "trigger": input.trigger if input.trigger is not None else CompactTrigger.MANUAL,
        "phase": input.phase,
        "compact_reason": input.compact_reason,
        "summary_source": "model",
        "pre_compact_token_count": input.pre_compact_token_count,
        "post_compact_token_count": input.post_compact_token_count,
        "true_post_compact_token_count": input.true_post_compact_token_count,
        "auto_compact_threshold": input.auto_compact_threshold,
        "will_retrigger_next_turn": input.will_retrigger_next_turn,
        "summarized_message_count": input.summarized_message_count,
        "kept_message_count": (
            input.kept_message_count if input.kept_message_count is not None else 0
        ),
        "last_summarized_message_id": input.last_summarized_message_id,
        "preserved_segment": input.preserved_segment,
        "summary_message_ids": [input.summary_message_id],
        "custom_instructions": input.custom_instructions is not None,
        "trace_id": input.trace_context.trace_id,
        "turn_id": input.trace_context.turn_id,
    }


def estimate_message_tokens(messages: Sequence[CompactModelMessage]) -> int:
    """Mirrors ``estimateMessageTokens``.

    # PORT: TS ``messages.reduce((total, message) => ..., 0)`` → 显式累加循环；
    # 估算公式与除数逐行照搬，不换成 tiktoken 之类。
    """
    total = 0
    for message in messages:
        estimated_character_count = len(
            _model_message_content_to_token_estimate_text(message.content)
        )
        # assistant toolCalls 独立保存在 content 之外，旧估算只读取 content，
        # 大型工具入参会被完整发给 provider，却在 auto compact 和 preflight 中计为 0。
        for tool_call in (message.tool_calls if message.tool_calls is not None else []):
            estimated_character_count += len(
                tool_call.name + _stringify_tool_call_input_for_token_estimate(tool_call.input)
            )
        total += math.ceil(estimated_character_count / ESTIMATED_TOKEN_CHAR_DIVISOR)
    return total


def _stringify_tool_call_input_for_token_estimate(input: Any) -> str:
    try:
        # PORT: TS ``JSON.stringify(input ?? {}) ?? EMPTY_TOOL_CALL_INPUT_JSON``。
        # ``json.dumps`` 恒返回 str，尾部兜底保留以对齐原语义。
        serialized = json.dumps({} if input is None else input)
        return serialized if serialized is not None else _EMPTY_TOOL_CALL_INPUT_JSON
    except Exception:
        # tool_use 解析失败时降级为空对象的 JSON 表示，供 estimator 估算。
        # ZCode 的模型输入仍可能包含未知内容；异常输入不能让本地预算估算中断 compact。
        return _EMPTY_TOOL_CALL_INPUT_JSON


def _model_message_content_to_token_estimate_text(content: ModelMessageContent) -> str:
    if isinstance(content, str):
        return content

    # modelMessageContentToText 是“可见正文”投影，会有意隐藏 reasoning；
    # compact fallback 却把它当作 provider 上下文体积，导致无 usage anchor 时 reasoning 全部计 0。
    # token 估算使用独立投影，避免改变正文、memory、错误文案等既有消费者的语义。
    projected = [
        (
            block.text
            if getattr(block, "type", None) == "reasoning"
            else model_message_content_block_to_text(block)
        )
        for block in content
    ]
    # PORT: ``.filter(Boolean).join("\n\n")`` → 过滤空串后以两个换行连接。
    return "\n\n".join(part for part in projected if part)


def _is_context_prefix_message(message: CompactModelMessage) -> bool:
    # PORT: TS ``<T extends CompactModelMessage>``；调用方只用基类形状，故此处收敛为基类。
    return message.role == "system" or (
        message.role == "user"
        and model_message_content_to_text(message.content).lstrip().startswith("<system-reminder>")
    )


def _group_messages_by_compact_round(
    messages: Sequence[CompactModelMessage],
) -> list[list[CompactModelMessage]]:
    return group_by_assistant_started_rounds(messages, lambda message: message.role)


def get_usage_total_tokens(usage: Optional[TokenUsageLike] = None) -> int:
    """Mirrors ``getUsageTotalTokens``."""
    # PORT: ``usage?.inputTokens ?? (usage?.cacheReadTokens ?? 0) + (usage?.cacheWriteTokens ?? 0)``。
    input_tokens = usage.input_tokens if usage is not None else None
    if input_tokens is None:
        cache_read_tokens = usage.cache_read_tokens if usage is not None else None
        cache_write_tokens = usage.cache_write_tokens if usage is not None else None
        input_tokens = (
            cache_read_tokens if cache_read_tokens is not None else 0
        ) + (cache_write_tokens if cache_write_tokens is not None else 0)

    # PORT: ``usage?.totalTokens ?? inputTokens + (usage?.outputTokens ?? 0)``。
    if usage is not None and usage.total_tokens is not None:
        return usage.total_tokens
    output_tokens = usage.output_tokens if usage is not None else None
    return input_tokens + (output_tokens if output_tokens is not None else 0)


def _generate_random_uuid() -> str:
    """Mirrors ``crypto.randomUUID()``."""
    return str(uuid.uuid4())


def create_compact_boundary_id(random_uuid: Callable[[], str] = _generate_random_uuid) -> str:
    """Mirrors ``createCompactBoundaryId``.

    # PORT: TS 默认参数 ``() => crypto.randomUUID()`` → 模块级函数引用作为默认值
    （函数对象不可变，无可变默认值问题）。
    """
    return f"compact_{random_uuid()}"
