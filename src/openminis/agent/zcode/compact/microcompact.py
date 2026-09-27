"""Local microcompact — clears old tool-result content under token pressure / idle.

Ported from: packages/core/src/compact/microcompact.ts
Original package: @zcode/core (compact)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Generic, Optional, Sequence, TypedDict, TypeVar, cast

from ..contracts.ids import ToolCallId, TraceId, TurnId
from ..contracts.model import ModelMessageContent, model_message_content_to_text
from .manual import CompactModelMessage, estimate_message_tokens

__all__ = [
    "MicrocompactTrigger",
    "MicrocompactStrategy",
    "MicrocompactBoundaryPayload",
    "LocalMicrocompactBoundaryPayload",
    "LocalMicrocompactPolicyConfig",
    "LocalMicrocompactMessage",
    "LocalMicrocompactDecision",
    "LocalMicrocompactResult",
    "MICROCOMPACT_CLEARED_TOOL_RESULT_PREFIX",
    "MICROCOMPACT_CLEARED_TOOL_RESULT_MESSAGE",
    "DEFAULT_MICROCOMPACT_KEEP_RECENT_TOOL_RESULTS",
    "DEFAULT_MICROCOMPACT_MIN_TOKEN_SAVINGS",
    "DEFAULT_MICROCOMPACT_THRESHOLD_RATIO",
    "DEFAULT_MICROCOMPACT_THRESHOLD_BUFFER_TOKENS",
    "DEFAULT_MICROCOMPACT_COMPACTABLE_TOOLS",
    "build_default_microcompact_threshold",
    "maybe_local_microcompact_messages",
]


# ---------------------------------------------------------------------------
# PORT: stand-in contract types
# ---------------------------------------------------------------------------
# Declared in ``@zcode/contracts`` (packages/contracts/src/compact/index.ts) but not
# shipped by ``..contracts`` yet. Transcribed 1:1 so this module keeps the exact
# shapes; replace with contract imports once the contracts port grows these modules.


class MicrocompactTrigger(str, Enum):
    TIME_BASED = "time_based"
    TOKEN_PRESSURE = "token_pressure"


class MicrocompactStrategy(str, Enum):
    LOCAL_TOOL_RESULT_CLEAR = "local_tool_result_clear"


class _MicrocompactBoundaryPayloadRequired(TypedDict):
    """``microcompactBoundaryPayloadSchema`` 的必填字段。"""

    trigger: MicrocompactTrigger
    strategy: MicrocompactStrategy
    pre_microcompact_token_count: int
    post_microcompact_token_count: int
    tokens_saved: int
    cleared_tool_call_ids: list[ToolCallId]
    kept_tool_call_ids: list[ToolCallId]
    cleared_message_count: int
    trace_id: TraceId


class MicrocompactBoundaryPayload(_MicrocompactBoundaryPayloadRequired, total=False):
    """Mirrors ``MicrocompactBoundaryPayload``."""

    turn_id: TurnId


class LocalMicrocompactBoundaryPayload(TypedDict):
    """Mirrors ``LocalMicrocompactBoundaryPayload``.

    # PORT: TS ``Omit<MicrocompactBoundaryPayload, "traceId" | "turnId">``。Python 无法
    # 结构化 Omit，故显式声明去掉 ``traceId`` / ``turnId`` 后的字段集。
    """

    trigger: MicrocompactTrigger
    strategy: MicrocompactStrategy
    pre_microcompact_token_count: int
    post_microcompact_token_count: int
    tokens_saved: int
    cleared_tool_call_ids: list[ToolCallId]
    kept_tool_call_ids: list[ToolCallId]
    cleared_message_count: int


# ---------------------------------------------------------------------------
# microcompact.ts — 常量
# ---------------------------------------------------------------------------

MICROCOMPACT_CLEARED_TOOL_RESULT_PREFIX = "[Old tool result content cleared]"
MICROCOMPACT_CLEARED_TOOL_RESULT_MESSAGE = "[Old tool result content cleared]"
DEFAULT_MICROCOMPACT_KEEP_RECENT_TOOL_RESULTS = 5
# PORT: TS ``const DEFAULT_MICROCOMPACT_IDLE_THRESHOLD_MINUTES = 60``（模块私有）。
_DEFAULT_MICROCOMPACT_IDLE_THRESHOLD_MINUTES = 60
DEFAULT_MICROCOMPACT_MIN_TOKEN_SAVINGS = 256
DEFAULT_MICROCOMPACT_THRESHOLD_RATIO = 0.9
DEFAULT_MICROCOMPACT_THRESHOLD_BUFFER_TOKENS = 2_000
# PORT: TS ``as const`` 数组（``readonly``）→ 元组。
DEFAULT_MICROCOMPACT_COMPACTABLE_TOOLS: tuple[str, ...] = (
    "Read",
    "Bash",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "Edit",
    "Write",
    "ApplyPatch",
)


# ---------------------------------------------------------------------------
# microcompact.ts — 类型
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LocalMicrocompactPolicyConfig:
    """Mirrors ``interface LocalMicrocompactPolicyConfig``."""

    enabled: Optional[bool] = None
    threshold_tokens: Optional[int] = None
    idle_threshold_minutes: Optional[int] = None
    keep_recent_tool_results: Optional[int] = None
    compactable_tool_names: Optional[Sequence[str]] = None
    clear_error_results: Optional[bool] = None
    min_token_savings: Optional[int] = None


# PORT: TS ``extends CompactModelMessage`` 并改写 ``toolCalls`` 为
# ``Array<{ id: string; input: unknown; name: string }>``；``id`` 已并入 ``CompactToolCall``。
@dataclass(slots=True)
class LocalMicrocompactMessage(CompactModelMessage):
    """Mirrors ``interface LocalMicrocompactMessage``."""

    is_error: Optional[bool] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None


LocalMicrocompactReason = str  # "disabled" | "not_triggered" | "no_candidates" | "nothing_to_clear" | "below_min_savings" | "applied"


@dataclass(slots=True)
class LocalMicrocompactDecision:
    """Mirrors ``interface LocalMicrocompactDecision``."""

    estimated_token_count: int
    reason: LocalMicrocompactReason
    threshold_tokens: Optional[int] = None
    trigger: Optional[MicrocompactTrigger] = None


# PORT: TS ``<T extends LocalMicrocompactMessage>`` 泛型 → ``TypeVar(bound=...)``。
T = TypeVar("T", bound=LocalMicrocompactMessage)


@dataclass(slots=True)
class LocalMicrocompactResult(Generic[T]):
    """Mirrors ``interface LocalMicrocompactResult<T>``."""

    decision: LocalMicrocompactDecision
    messages: list[T]
    payload: Optional[LocalMicrocompactBoundaryPayload] = None


@dataclass(slots=True)
class _ToolResultCandidate:
    """Mirrors non-exported ``interface ToolResultCandidate``."""

    index: int
    tool_call_id: str


# PORT: TS 匿名入参 ``{ config?; lastAssistantCompletedAtMs?; messages: readonly T[];
# nowMs? }``。匿名形状按惯例用 ``TypedDict``，但 ``TypedDict`` 无法表达泛型 ``T``，
# 故此处用泛型 ``dataclass`` 承载。
@dataclass(slots=True)
class MaybeLocalMicrocompactMessagesInput(Generic[T]):
    messages: Sequence[T]
    config: Optional[LocalMicrocompactPolicyConfig] = None
    last_assistant_completed_at_ms: Optional[int] = None
    now_ms: Optional[int] = None


# PORT: TS 匿名入参 ``{ config; estimatedTokenCount; lastAssistantCompletedAtMs?; nowMs?;
# thresholdTokens? }`` → ``TypedDict``。
class _ResolveMicrocompactTriggerInputRequired(TypedDict):
    config: LocalMicrocompactPolicyConfig
    estimated_token_count: int


class _ResolveMicrocompactTriggerInput(_ResolveMicrocompactTriggerInputRequired, total=False):
    last_assistant_completed_at_ms: int
    now_ms: int
    threshold_tokens: int


# ---------------------------------------------------------------------------
# microcompact.ts — 函数
# ---------------------------------------------------------------------------


def build_default_microcompact_threshold(auto_compact_threshold: int) -> int:
    """Mirrors ``buildDefaultMicrocompactThreshold``."""
    # PORT: ``Math.floor`` / ``Math.min`` / ``Math.max`` → ``math.floor`` / ``min`` / ``max``。
    ratio_threshold = math.floor(auto_compact_threshold * DEFAULT_MICROCOMPACT_THRESHOLD_RATIO)
    buffer_threshold = auto_compact_threshold - DEFAULT_MICROCOMPACT_THRESHOLD_BUFFER_TOKENS
    return max(0, min(ratio_threshold, buffer_threshold))


def maybe_local_microcompact_messages(
    input: MaybeLocalMicrocompactMessagesInput[T],
) -> LocalMicrocompactResult[T]:
    """Mirrors ``maybeLocalMicrocompactMessages``."""
    config = input.config if input.config is not None else LocalMicrocompactPolicyConfig()
    messages = [_clone_local_microcompact_message(message) for message in input.messages]
    estimated_token_count = estimate_message_tokens(messages)
    threshold_tokens = _positive_int(config.threshold_tokens)

    if config.enabled is False:
        return LocalMicrocompactResult(
            decision=LocalMicrocompactDecision(
                estimated_token_count=estimated_token_count,
                reason="disabled",
                threshold_tokens=threshold_tokens,
            ),
            messages=messages,
        )

    trigger = _resolve_microcompact_trigger(
        {
            "config": config,
            "estimated_token_count": estimated_token_count,
            "last_assistant_completed_at_ms": input.last_assistant_completed_at_ms,
            "now_ms": input.now_ms,
            "threshold_tokens": threshold_tokens,
        }
    )
    if trigger is None:
        return LocalMicrocompactResult(
            decision=LocalMicrocompactDecision(
                estimated_token_count=estimated_token_count,
                reason="not_triggered",
                threshold_tokens=threshold_tokens,
            ),
            messages=messages,
        )

    candidate_groups = _collect_compactable_tool_result_groups(messages, config)
    if len(candidate_groups) == 0:
        return LocalMicrocompactResult(
            decision=LocalMicrocompactDecision(
                estimated_token_count=estimated_token_count,
                reason="no_candidates",
                threshold_tokens=threshold_tokens,
                trigger=trigger,
            ),
            messages=messages,
        )

    keep_count = _positive_int(config.keep_recent_tool_results)
    if keep_count is None:
        keep_count = DEFAULT_MICROCOMPACT_KEEP_RECENT_TOOL_RESULTS
    bounded_keep_count = max(1, keep_count)
    clear_group_count = max(0, len(candidate_groups) - bounded_keep_count)
    if clear_group_count == 0:
        return LocalMicrocompactResult(
            decision=LocalMicrocompactDecision(
                estimated_token_count=estimated_token_count,
                reason="nothing_to_clear",
                threshold_tokens=threshold_tokens,
                trigger=trigger,
            ),
            messages=messages,
        )

    # PORT: 两层 ``.slice().flat()`` → 嵌套列表推导。
    to_clear = [candidate for group in candidate_groups[:clear_group_count] for candidate in group]
    to_keep = [candidate for group in candidate_groups[clear_group_count:] for candidate in group]
    for candidate in to_clear:
        # PORT: TS ``const message = messages[candidate.index]; if (!message) continue;``
        # （越界时得到 ``undefined``）；Python 需显式边界判定。
        message = (
            messages[candidate.index]
            if 0 <= candidate.index < len(messages)
            else None
        )
        if message is None:
            continue
        # PORT: ``{ ...message, content: ... }`` → ``dataclasses.replace``。
        messages[candidate.index] = replace(
            message, content=_build_cleared_tool_result_content()
        )

    post_token_count = estimate_message_tokens(messages)
    tokens_saved = max(0, estimated_token_count - post_token_count)
    min_savings = _positive_int(config.min_token_savings)
    if min_savings is None:
        min_savings = DEFAULT_MICROCOMPACT_MIN_TOKEN_SAVINGS
    if tokens_saved < min_savings:
        return LocalMicrocompactResult(
            decision=LocalMicrocompactDecision(
                estimated_token_count=estimated_token_count,
                reason="below_min_savings",
                threshold_tokens=threshold_tokens,
                trigger=trigger,
            ),
            messages=[_clone_local_microcompact_message(message) for message in input.messages],
        )

    return LocalMicrocompactResult(
        decision=LocalMicrocompactDecision(
            estimated_token_count=estimated_token_count,
            reason="applied",
            threshold_tokens=threshold_tokens,
            trigger=trigger,
        ),
        messages=messages,
        payload={
            "cleared_message_count": len(to_clear),
            # PORT: TS ``candidate.toolCallId as ToolCallId`` → ``ToolCallId(...)``（NewType 调用）。
            "cleared_tool_call_ids": [ToolCallId(candidate.tool_call_id) for candidate in to_clear],
            "kept_tool_call_ids": [ToolCallId(candidate.tool_call_id) for candidate in to_keep],
            "post_microcompact_token_count": post_token_count,
            "pre_microcompact_token_count": estimated_token_count,
            "strategy": MicrocompactStrategy.LOCAL_TOOL_RESULT_CLEAR,
            "tokens_saved": tokens_saved,
            "trigger": trigger,
        },
    )


def _resolve_microcompact_trigger(
    input: _ResolveMicrocompactTriggerInput,
) -> Optional[MicrocompactTrigger]:
    idle_threshold_minutes = _positive_int(input.get("idle_threshold_minutes"))
    if idle_threshold_minutes is None:
        idle_threshold_minutes = _DEFAULT_MICROCOMPACT_IDLE_THRESHOLD_MINUTES

    last_assistant_completed_at_ms = input.get("last_assistant_completed_at_ms")
    if last_assistant_completed_at_ms is not None and _is_finite(last_assistant_completed_at_ms):
        # PORT: TS ``input.nowMs ?? Date.now()``；``Date.now()`` → ``int(time.time() * 1000)``。
        now_ms = input.get("now_ms")
        elapsed_ms = (
            now_ms if now_ms is not None else int(time.time() * 1000)
        ) - last_assistant_completed_at_ms
        if elapsed_ms > idle_threshold_minutes * 60_000:
            return MicrocompactTrigger.TIME_BASED

    threshold_tokens = input.get("threshold_tokens")
    if threshold_tokens is not None and input["estimated_token_count"] >= threshold_tokens:
        return MicrocompactTrigger.TOKEN_PRESSURE

    return None


def _collect_compactable_tool_result_groups(
    messages: Sequence[T],
    config: LocalMicrocompactPolicyConfig,
) -> list[list[_ToolResultCandidate]]:
    # PORT: ``new Set(config.compactableToolNames ?? DEFAULT_...)`` → ``set(...)``；
    # ``??`` 而非 ``||``：显式传入空数组时不得回落到默认值。
    compactable_tools: set[str] = set(
        config.compactable_tool_names
        if config.compactable_tool_names is not None
        else DEFAULT_MICROCOMPACT_COMPACTABLE_TOOLS
    )
    clear_error_results = config.clear_error_results is True
    groups: list[list[_ToolResultCandidate]] = []
    current_group: Optional[list[_ToolResultCandidate]] = None

    def flush_current_group() -> None:
        nonlocal current_group
        if current_group is not None and len(current_group) > 0:
            groups.append(current_group)
        current_group = None

    for index, message in enumerate(messages):
        if message.role == "assistant" and message.tool_calls and len(message.tool_calls) > 0:
            flush_current_group()
            current_group = []
            continue

        if message.role != "tool":
            continue
        if not message.tool_call_id or not message.tool_name:
            continue
        if message.tool_name not in compactable_tools:
            continue
        if message.is_error and not clear_error_results:
            continue
        if _is_microcompact_cleared_tool_result_content(message.content):
            continue
        if _has_media_tool_result_content(message.content):
            continue

        if current_group is None:
            # PORT: 与 TS 一致——``currentGroup`` 为 ``undefined`` 时新建独立分组，
            # 且**不**把它赋回 ``currentGroup``（后续候选同样各自成组）。
            groups.append(
                [_ToolResultCandidate(index=index, tool_call_id=message.tool_call_id)]
            )
            continue

        current_group.append(
            _ToolResultCandidate(index=index, tool_call_id=message.tool_call_id)
        )

    flush_current_group()
    return groups


def _build_cleared_tool_result_content() -> ModelMessageContent:
    return MICROCOMPACT_CLEARED_TOOL_RESULT_MESSAGE


def _is_microcompact_cleared_tool_result_content(content: ModelMessageContent) -> bool:
    return model_message_content_to_text(content) == MICROCOMPACT_CLEARED_TOOL_RESULT_MESSAGE


def _has_media_tool_result_content(content: ModelMessageContent) -> bool:
    # PORT: TS ``Array.isArray(content)`` → ``isinstance(content, list)``。
    if not isinstance(content, list):
        return False
    # PORT: TS ``block.type === "image" || "video" || "file"``（含 ``!block`` /
    # ``typeof block !== "object"`` / ``"type" in block`` 守卫）→ ``getattr`` 取值判空。
    # video 与 image/file 同为受保护媒体：Read 视频结果漏判会被 microcompact 清掉。
    return any(
        getattr(block, "type", None) in ("image", "video", "file") for block in content
    )


def _clone_local_microcompact_message(message: T) -> T:
    """Mirrors ``cloneLocalMicrocompactMessage``.

    # PORT: TS ``{ ...message, content, toolCalls: message.toolCalls?.map((tc) => ({ ...tc })) }``
    # → ``dataclasses.replace`` + 逐项拷贝（``toolCalls`` 缺席时保持 ``None``）。
    """
    return replace(
        message,
        content=_clone_content(message.content),
        tool_calls=(
            [replace(tool_call) for tool_call in message.tool_calls]
            if message.tool_calls is not None
            else None
        ),
    )


def _clone_content(content: ModelMessageContent) -> ModelMessageContent:
    if isinstance(content, str):
        return content
    # PORT: TS ``"source" in block && block.source`` /
    # ``"providerOptions" in block && block.providerOptions`` → ``getattr`` 取值判空；
    # ``{ ...x }`` → ``dataclasses.replace``（``providerOptions`` → ``provider_options``）。
    cloned: list[Any] = []
    for block in content:
        source = getattr(block, "source", None)
        if source is not None:
            cloned.append(replace(block, source=replace(source)))
            continue
        provider_options = getattr(block, "provider_options", None)
        if provider_options is not None:
            cloned.append(replace(block, provider_options=dict(provider_options)))
            continue
        cloned.append(replace(block))
    # PORT: TS 侧为 ``as ModelMessageContent``（``cloned`` 为具体内容块列表）。
    return cast(ModelMessageContent, cloned)


def _positive_int(value: Optional[float]) -> Optional[int]:
    """Mirrors ``positiveInt`` (microcompact.ts 的同名私有函数)."""
    if value is None or not _is_finite(value) or value < 0:
        return None
    return math.floor(value)


def _is_finite(value: Any) -> bool:
    """Mirrors ``Number.isFinite`` — 非数值一律返回 ``False``。"""
    try:
        return math.isfinite(value)
    except TypeError:
        return False
