"""Auto compact policy — threshold math for context-window compaction.

Ported from: packages/core/src/compact/policy.ts
Original package: @zcode/core (compact)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Optional, Sequence, TypedDict

from .manual import CompactModelMessage, estimate_message_tokens, has_enough_messages_to_compact
from .microcompact import LocalMicrocompactPolicyConfig

__all__ = [
    "DEFAULT_COMPACT_CONTEXT_WINDOW",
    "DEFAULT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS",
    "MAX_OUTPUT_TOKENS_FOR_SUMMARY",
    "AUTOCOMPACT_BUFFER_TOKENS",
    "DEFAULT_AUTOCOMPACT_THRESHOLD_PERCENT",
    "MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES",
    "AutoCompactPolicyConfig",
    "AutoCompactTokenSource",
    "AutoCompactTokenOverride",
    "AutoCompactDecision",
    "get_effective_context_window_size",
    "get_auto_compact_output_reserve_tokens",
    "get_auto_compact_threshold",
    "should_auto_compact",
]

# PORT: 原 ``import { DEFAULT_ZCODE_MODEL_CONTEXT_BUDGET_STRATEGY as DEFAULT_BUDGET_STRATEGY }
# from "@zcode/shared"``。该常量定义在 packages/shared/src/zcode-protocol/index.ts
# （仓库根 packages/shared，非 apps/zcode-cli 内），值为 "preflight-v1"。
# Python 侧没有跨包 shared 层，按原值内联。
DEFAULT_BUDGET_STRATEGY = "preflight-v1"

DEFAULT_COMPACT_CONTEXT_WINDOW = 200_000
# 正常请求默认输出已收敛到 32K，auto compact 必须预留同一目标；
# 否则请求预算和压缩窗口会继续按两套常量计算。
DEFAULT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS = 32_000
# PORT: TS ``const PREFLIGHT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS = 21_000``（模块私有）。
_PREFLIGHT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS = 21_000
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000
AUTOCOMPACT_BUFFER_TOKENS = 13_000
DEFAULT_AUTOCOMPACT_THRESHOLD_PERCENT = 100
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3


@dataclass(slots=True)
class AutoCompactPolicyConfig:
    """Mirrors ``interface AutoCompactPolicyConfig``."""

    enabled: Optional[bool] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    # PORT: TS 内联字符串联合 ``"legacy" | "preflight-v1"``。
    model_context_budget_strategy: Optional[Literal["legacy", "preflight-v1"]] = None
    summary_reserve_tokens: Optional[int] = None
    buffer_tokens: Optional[int] = None
    threshold_percent_override: Optional[int] = None
    max_consecutive_failures: Optional[int] = None
    microcompact: Optional[LocalMicrocompactPolicyConfig] = None


AutoCompactTokenSource = Literal["estimate", "provider_usage"]


@dataclass(slots=True)
class AutoCompactTokenOverride:
    """Mirrors ``interface AutoCompactTokenOverride``.

    # PORT: 必填字段上提到可选字段之前（dataclass 字段顺序约束），字段名全部保留。
    """

    # PORT: TS ``Extract<AutoCompactTokenSource, "provider_usage">``。
    source: Literal["provider_usage"]
    token_count: int

    base_token_count: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    context_usage_token_count: Optional[int] = None
    incremental_token_count: Optional[int] = None
    output_tokens: Optional[int] = None


@dataclass(slots=True)
class AutoCompactDecision:
    """Mirrors ``interface AutoCompactDecision``.

    # PORT: 必填字段上提到可选字段之前（dataclass 字段顺序约束），字段名全部保留。
    """

    should_compact: bool
    token_count: int
    token_source: AutoCompactTokenSource
    estimated_token_count: int
    threshold: int
    context_window: int
    effective_context_window: int
    # PORT: TS 内联字符串联合 ``"legacy" | "preflight-v1"``。
    model_context_budget_strategy: Literal["legacy", "preflight-v1"]
    output_reserve_tokens: int
    threshold_percent: int
    # PORT: TS 内联字符串联合（``reason``）。
    reason: Literal[
        "disabled",
        "not_enough_messages",
        "circuit_breaker",
        "below_threshold",
        "above_threshold",
    ]

    provider_cache_read_tokens: Optional[int] = None
    provider_cache_write_tokens: Optional[int] = None
    provider_base_token_count: Optional[int] = None
    provider_context_usage_token_count: Optional[int] = None
    provider_incremental_token_count: Optional[int] = None
    provider_output_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None


# PORT: TS 匿名入参 ``{ messages: readonly CompactModelMessage[]; config?;
# consecutiveFailures?; tokenOverride? }`` → ``TypedDict``（匿名形状按惯例）。
class _ShouldAutoCompactInputRequired(TypedDict):
    messages: Sequence[CompactModelMessage]


class ShouldAutoCompactInput(_ShouldAutoCompactInputRequired, total=False):
    config: AutoCompactPolicyConfig
    consecutive_failures: int
    token_override: AutoCompactTokenOverride


def get_effective_context_window_size(
    config: Optional[AutoCompactPolicyConfig] = None,
) -> int:
    """Mirrors ``getEffectiveContextWindowSize``.

    # PORT: TS 默认值 ``config: AutoCompactPolicyConfig = {}`` → ``None`` 哨兵后在函数内
    取默认配置（等价于空对象默认值，且避免可变默认值）。
    """
    resolved = config if config is not None else AutoCompactPolicyConfig()
    context_window = _positive_int(resolved.context_window)
    if context_window is None:
        context_window = DEFAULT_COMPACT_CONTEXT_WINDOW
    # provider 的 context window 是 input + output 共享窗口；自动压缩只能让出输入侧，
    # 因此阈值分母必须先扣掉当前模型允许的 output token，而不是继续吃完整 contextWindow。
    reserve = min(get_auto_compact_output_reserve_tokens(resolved), context_window)
    return max(0, context_window - reserve)


def get_auto_compact_output_reserve_tokens(
    config: Optional[AutoCompactPolicyConfig] = None,
) -> int:
    """Mirrors ``getAutoCompactOutputReserveTokens``."""
    resolved = config if config is not None else AutoCompactPolicyConfig()
    max_output_tokens = _positive_int(resolved.max_output_tokens)
    # 旧 legacy 分支为完整模型输出预留窗口，既过早压缩又要求远端选择；现在统一保留至多 21K。
    return min(
        (
            max_output_tokens
            if max_output_tokens is not None
            else DEFAULT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS
        ),
        _PREFLIGHT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS,
    )


def get_auto_compact_threshold(config: Optional[AutoCompactPolicyConfig] = None) -> int:
    """Mirrors ``getAutoCompactThreshold``."""
    effective_context_window = get_effective_context_window_size(config)
    resolved = config if config is not None else AutoCompactPolicyConfig()
    buffer = _positive_int(resolved.buffer_tokens)
    if buffer is None:
        buffer = AUTOCOMPACT_BUFFER_TOKENS
    return max(0, effective_context_window - buffer)


def should_auto_compact(input: ShouldAutoCompactInput) -> AutoCompactDecision:
    """Mirrors ``shouldAutoCompact``."""
    config = input.get("config")
    if config is None:
        config = AutoCompactPolicyConfig()

    context_window = _positive_int(config.context_window)
    if context_window is None:
        context_window = DEFAULT_COMPACT_CONTEXT_WINDOW
    effective_context_window = get_effective_context_window_size(config)
    output_reserve_tokens = min(get_auto_compact_output_reserve_tokens(config), context_window)
    threshold = get_auto_compact_threshold(config)
    threshold_percent = DEFAULT_AUTOCOMPACT_THRESHOLD_PERCENT
    estimated_token_count = estimate_message_tokens(input["messages"])
    token_override = input.get("token_override")
    token_count = (
        token_override.token_count if token_override is not None else estimated_token_count
    )
    token_source = token_override.source if token_override is not None else "estimate"
    # PORT: TS ``const common = {...} satisfies Omit<AutoCompactDecision, "reason" | "shouldCompact">``
    # —— ``satisfies`` 只是编译期校验；Python 侧用 dict 承载后续的对象展开。
    common: dict[str, Any] = {
        "context_window": context_window,
        "effective_context_window": effective_context_window,
        "estimated_token_count": estimated_token_count,
        "provider_cache_read_tokens": (
            token_override.cache_read_tokens if token_override is not None else None
        ),
        "provider_cache_write_tokens": (
            token_override.cache_write_tokens if token_override is not None else None
        ),
        "max_output_tokens": _positive_int(config.max_output_tokens),
        "model_context_budget_strategy": DEFAULT_BUDGET_STRATEGY,
        "output_reserve_tokens": output_reserve_tokens,
        "provider_base_token_count": (
            token_override.base_token_count if token_override is not None else None
        ),
        "provider_context_usage_token_count": (
            token_override.context_usage_token_count if token_override is not None else None
        ),
        "provider_incremental_token_count": (
            token_override.incremental_token_count if token_override is not None else None
        ),
        "provider_output_tokens": (
            token_override.output_tokens if token_override is not None else None
        ),
        "threshold": threshold,
        "threshold_percent": threshold_percent,
        "token_count": token_count,
        "token_source": token_source,
    }

    if config.enabled is False:
        # PORT: ``{ ...common, shouldCompact: false, reason: "disabled" }`` → dict 展开构造。
        return AutoCompactDecision(**common, should_compact=False, reason="disabled")

    if not has_enough_messages_to_compact(input["messages"]):
        return AutoCompactDecision(**common, should_compact=False, reason="not_enough_messages")

    max_failures = _positive_int(config.max_consecutive_failures)
    if max_failures is None:
        max_failures = MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
    consecutive_failures = input.get("consecutive_failures")
    if (consecutive_failures if consecutive_failures is not None else 0) >= max_failures:
        return AutoCompactDecision(**common, should_compact=False, reason="circuit_breaker")

    if token_count < threshold:
        return AutoCompactDecision(**common, should_compact=False, reason="below_threshold")

    return AutoCompactDecision(**common, should_compact=True, reason="above_threshold")


def _positive_int(value: Optional[float]) -> Optional[int]:
    """Mirrors ``positiveInt`` (policy.ts 的同名私有函数)."""
    if value is None or not _is_finite(value) or value < 0:
        return None
    return math.floor(value)


def _is_finite(value: Any) -> bool:
    """Mirrors ``Number.isFinite`` — 非数值一律返回 ``False``。"""
    try:
        return math.isfinite(value)
    except TypeError:
        return False
