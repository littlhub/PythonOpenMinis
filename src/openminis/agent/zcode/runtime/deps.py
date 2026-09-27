"""Runtime dependency re-exports — mirrors TS ``deps.ts``.

Ported from: packages/core/src/runtime/deps.ts
Original package: @zcode/core (runtime/deps)

This module re-exports the contract types, enums, and factory functions that
the runtime kernel consumes. In Python, we re-export from this module's
``__all__`` rather than directly from ``..contracts``.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Brand / identifier types
# ---------------------------------------------------------------------------

from ..contracts.ids import (
    EventId,
    InputHistoryId,
    MessageId,
    PartId,
    ProjectId,
    QueryId,
    SessionId,
    SubagentInteractionRequestOrigin,
    ToolCallId,
    TraceId,
    TurnId,
    WorkspaceId,
    create_event_id,
    create_input_history_id,
    create_message_id,
    create_part_id,
    create_project_id,
    create_query_id,
    create_session_id,
    create_tool_call_id,
    create_trace_id,
    create_turn_id,
    create_workspace_id,
)

from ..contracts.tracing import TraceContext

# ---------------------------------------------------------------------------
# Contract types — errors
# ---------------------------------------------------------------------------

from ..contracts.errors import (
    CoreError,
    CoreErrorType,
    create_core_error,
    is_core_error,
    is_recoverable,
    is_retryable,
    permission_denied,
    session_not_found,
    tool_execution_failed,
    tool_not_found,
)

# ---------------------------------------------------------------------------
# Contract types — model
# ---------------------------------------------------------------------------

from ..contracts.model import (
    JsonSchema,
    Model,
    ModelErrorCode,
    ModelEvent,
    ModelInputFormat,
    ModelInputMessage,
    ModelInvocationContext,
    ModelMessageContent,
    ModelMessageContentBlock,
    ModelNetworkStatusEvent,
    ModelOptions,
    ModelProperties,
    ModelProviderId,
    ModelReasoningContentBlock,
    ModelRetryBudget,
    ModelRetryReason,
    ModelSelection,
    ModelSelectionOrigin,
    ModelSource,
    ModelStatusSink,
    ModelStreamEvent,
    ModelStreamRecoveryStatus,
    ModelTechnologyResponseFormat,
    ModelTextContentBlock,
    ModelToolCall,
    ModelToolContract,
    ModelToolResult,
    ModelTransportKind,
    ModelUsage,
    ModelUsageSummary,
    create_model_provider_id,
    create_model_usage_summary,
    get_model_usage_total_tokens,
    has_model_usage,
    model_message_content_to_text,
    model_message_content_block_to_text,
    model_selection_json_schema,
)

# ---------------------------------------------------------------------------
# Contract types — tools
# ---------------------------------------------------------------------------

from ..contracts.tools import (
    RiskLevel,
    ToolCancellationPolicy,
    ToolContractDeclaration,
    ToolExecutionMode,
    ToolPermissionPatternSource,
    ToolPermissionSpec,
    ToolResultBudget,
    ToolResultBudgetArtifact,
    ToolResultBudgetPreview,
    ToolResultBudgetStrategy,
    ToolSideEffectScope,
    ToolTimeoutPolicy,
    ToolTracePolicy,
    TimedToolTimeoutPolicy,
    NoToolTimeoutPolicy,
    ProviderNativeToolSpec,
    ToolPermissionAskOptions,
)

# Session event types
from ..contracts.events import SessionEvent, SessionEventType
from ..contracts.tools import ToolCall
from ..context.types import SkillLoadOutcome
from .types import (
    SessionEventSink,
    SessionEventStorePort,
    SessionGoal,
    TurnInputIntentMetadata,
)

# PORT: ``SubagentPort`` is a host-injected subagent runner; typed as ``Any`` here
# to avoid a deps <-> internal import cycle (``internal`` also aliases it).
SubagentPort = Any

from ..tool.handlers.ports import TodoItem

__all__ = [
    # IDs
    "EventId",
    "SubagentPort",
    "InputHistoryId",
    "MessageId",
    "PartId",
    "ProjectId",
    "QueryId",
    "SessionId",
    "SubagentInteractionRequestOrigin",
    "ToolCallId",
    "TraceContext",
    "TraceId",
    "TurnId",
    "WorkspaceId",
    "create_event_id",
    "create_input_history_id",
    "create_message_id",
    "create_part_id",
    "create_project_id",
    "create_query_id",
    "create_session_id",
    "create_tool_call_id",
    "create_trace_id",
    "create_turn_id",
    "create_workspace_id",
    "SessionEvent",
    "SessionEventSink",
    "SessionEventType",
    "SessionEventStorePort",
    "SessionGoal",
    "TurnInputIntentMetadata",
    "ToolCall",
    "SkillLoadOutcome",
    "TodoItem",
    # Errors
    "CoreError",
    "CoreErrorType",
    "create_core_error",
    "is_core_error",
    "is_recoverable",
    "is_retryable",
    "permission_denied",
    "session_not_found",
    "tool_execution_failed",
    "tool_not_found",
    # Model
    "JsonSchema",
    "Model",
    "ModelErrorCode",
    "ModelEvent",
    "ModelInputFormat",
    "ModelInputMessage",
    "ModelInvocationContext",
    "ModelMessageContent",
    "ModelMessageContentBlock",
    "ModelNetworkStatusEvent",
    "ModelOptions",
    "ModelProperties",
    "ModelProviderId",
    "ModelReasoningContentBlock",
    "ModelRetryBudget",
    "ModelRetryReason",
    "ModelSelection",
    "ModelSelectionOrigin",
    "ModelSource",
    "ModelStatusSink",
    "ModelStreamEvent",
    "ModelStreamRecoveryStatus",
    "ModelTechnologyResponseFormat",
    "ModelTextContentBlock",
    "ModelToolCall",
    "ModelToolContract",
    "ModelToolResult",
    "ModelTransportKind",
    "ModelUsage",
    "ModelUsageSummary",
    "create_model_provider_id",
    "create_model_usage_summary",
    "get_model_usage_total_tokens",
    "has_model_usage",
    "model_message_content_to_text",
    "model_message_content_block_to_text",
    "model_selection_json_schema",
    # Tools
    "RiskLevel",
    "ToolCancellationPolicy",
    "ToolContractDeclaration",
    "ToolExecutionMode",
    "ToolPermissionPatternSource",
    "ToolPermissionSpec",
    "ToolResultBudget",
    "ToolResultBudgetArtifact",
    "ToolResultBudgetPreview",
    "ToolResultBudgetStrategy",
    "ToolSideEffectScope",
    "ToolTimeoutPolicy",
    "ToolTracePolicy",
    "TimedToolTimeoutPolicy",
    "NoToolTimeoutPolicy",
    "ProviderNativeToolSpec",
    "ToolPermissionAskOptions",
]