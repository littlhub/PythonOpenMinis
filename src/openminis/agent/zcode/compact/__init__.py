"""Compact — context compaction (auto policy, manual summary, local microcompact).

Ported from: packages/core/src/compact/index.ts
Original package: @zcode/core (compact)
"""

from __future__ import annotations

from .manual import (
    COMPACT_PROMPT_TOO_LONG_RETRY_MARKER,
    COMPACT_PROMPT_TOO_LONG_USER_MESSAGE,
    MAX_COMPACT_PROMPT_TOO_LONG_RETRIES,
    BuildManualCompactBoundaryInput,
    CompactModelMessage,
    TokenUsageLike,
    build_manual_compact_boundary,
    create_compact_boundary_id,
    estimate_message_tokens,
    get_messages_to_summarize,
    get_usage_total_tokens,
    has_enough_messages_to_compact,
)
from .microcompact import (
    DEFAULT_MICROCOMPACT_COMPACTABLE_TOOLS,
    DEFAULT_MICROCOMPACT_KEEP_RECENT_TOOL_RESULTS,
    DEFAULT_MICROCOMPACT_MIN_TOKEN_SAVINGS,
    DEFAULT_MICROCOMPACT_THRESHOLD_BUFFER_TOKENS,
    DEFAULT_MICROCOMPACT_THRESHOLD_RATIO,
    MICROCOMPACT_CLEARED_TOOL_RESULT_MESSAGE,
    MICROCOMPACT_CLEARED_TOOL_RESULT_PREFIX,
    LocalMicrocompactBoundaryPayload,
    LocalMicrocompactDecision,
    LocalMicrocompactMessage,
    LocalMicrocompactPolicyConfig,
    LocalMicrocompactResult,
    build_default_microcompact_threshold,
    maybe_local_microcompact_messages,
)
from .policy import (
    AUTOCOMPACT_BUFFER_TOKENS,
    DEFAULT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS,
    DEFAULT_AUTOCOMPACT_THRESHOLD_PERCENT,
    DEFAULT_COMPACT_CONTEXT_WINDOW,
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    AutoCompactDecision,
    AutoCompactPolicyConfig,
    AutoCompactTokenOverride,
    AutoCompactTokenSource,
    get_auto_compact_output_reserve_tokens,
    get_auto_compact_threshold,
    get_effective_context_window_size,
    should_auto_compact,
)
from .prompt import (
    build_compact_prompt,
    build_compact_summary_message,
    format_compact_summary,
)

__all__ = [
    # prompt.ts
    "build_compact_prompt",
    "build_compact_summary_message",
    "format_compact_summary",
    # manual.ts
    "COMPACT_PROMPT_TOO_LONG_RETRY_MARKER",
    "COMPACT_PROMPT_TOO_LONG_USER_MESSAGE",
    "MAX_COMPACT_PROMPT_TOO_LONG_RETRIES",
    "build_manual_compact_boundary",
    "create_compact_boundary_id",
    "estimate_message_tokens",
    "get_messages_to_summarize",
    "get_usage_total_tokens",
    "has_enough_messages_to_compact",
    # policy.ts
    "AUTOCOMPACT_BUFFER_TOKENS",
    "DEFAULT_AUTOCOMPACT_THRESHOLD_PERCENT",
    "DEFAULT_AUTOCOMPACT_OUTPUT_RESERVE_TOKENS",
    "DEFAULT_COMPACT_CONTEXT_WINDOW",
    "MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES",
    "MAX_OUTPUT_TOKENS_FOR_SUMMARY",
    "get_auto_compact_output_reserve_tokens",
    "get_auto_compact_threshold",
    "get_effective_context_window_size",
    "should_auto_compact",
    # microcompact.ts
    "DEFAULT_MICROCOMPACT_COMPACTABLE_TOOLS",
    "DEFAULT_MICROCOMPACT_KEEP_RECENT_TOOL_RESULTS",
    "DEFAULT_MICROCOMPACT_MIN_TOKEN_SAVINGS",
    "DEFAULT_MICROCOMPACT_THRESHOLD_BUFFER_TOKENS",
    "DEFAULT_MICROCOMPACT_THRESHOLD_RATIO",
    "MICROCOMPACT_CLEARED_TOOL_RESULT_MESSAGE",
    "MICROCOMPACT_CLEARED_TOOL_RESULT_PREFIX",
    "build_default_microcompact_threshold",
    "maybe_local_microcompact_messages",
    # types re-exported by the TS barrel
    "BuildManualCompactBoundaryInput",
    "CompactModelMessage",
    "TokenUsageLike",
    "AutoCompactDecision",
    "AutoCompactPolicyConfig",
    "AutoCompactTokenOverride",
    "AutoCompactTokenSource",
    "LocalMicrocompactBoundaryPayload",
    "LocalMicrocompactDecision",
    "LocalMicrocompactMessage",
    "LocalMicrocompactPolicyConfig",
    "LocalMicrocompactResult",
]
