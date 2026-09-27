"""Model protocol — provider-neutral model contracts.

Ported from: packages/contracts/src/model/index.ts
Original package: @zcode/contracts (model)

Only the provider-neutral surface consumed by the programming-agent kernel is
ported here. Telemetry-only types (``ModelApiCallObservation`` and friends) are
kept as loose ``Any`` placeholders — see ``# PORT:`` notes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Union

from .ids import QueryId, SessionId, TraceId, TurnId

__all__ = [
    "JsonSchema",
    "ModelProviderId",
    "ModelId",
    "ModelProtocolError",
    "create_model_provider_id",
    "create_model_id",
    "ModelRequestSessionType",
    "ModelRetryBudget",
    "ModelErrorCode",
    "ModelTransportKind",
    "ModelRetryReason",
    "ModelFailureReason",
    "ModelStreamRecoveryStatus",
    "ModelRequestStartedStatusEvent",
    "ModelRequestQueuedStatusEvent",
    "ModelRequestAdmittedStatusEvent",
    "ModelRequestCompletedStatusEvent",
    "ModelRequestFailedStatusEvent",
    "ModelRetryScheduledStatusEvent",
    "ModelStreamStalledStatusEvent",
    "ModelTelemetryMilestoneStatusEvent",
    "ModelNetworkStatusEvent",
    "ModelStatusSink",
    "ModelMessageRole",
    "ModelToolCall",
    "AttachmentKind",
    "AttachmentRef",
    "ModelTextContentBlock",
    "ModelReasoningContentBlock",
    "ModelImageContentBlock",
    "ModelVideoContentBlock",
    "ModelFileContentBlock",
    "ModelResourceLinkContentBlock",
    "ModelMessageContentBlock",
    "ModelMessageContent",
    "ModelCacheControl",
    "ModelInputMessage",
    "model_message_content_to_text",
    "model_message_content_block_to_text",
    "ModelToolExecutionContext",
    "ModelToolSideEffectScope",
    "ModelToolContract",
    "ModelToolChoice",
    "ModelServerToolUsage",
    "ModelUsage",
    "ModelUsageSummary",
    "get_model_usage_total_tokens",
    "get_model_usage_context_tokens",
    "get_model_usage_input_window_tokens",
    "has_model_usage",
    "create_model_usage_summary",
    "ModelRequestSettings",
    "ModelTextRequest",
    "ModelSource",
    "ModelToolResult",
    "ModelTextResult",
    "ModelStreamEvent",
    "ModelRequestTarget",
    "ModelRequestAdmissionTicket",
    "ModelRequestAdmission",
    "model_selection_json_schema",
    "attachment_ref_json_schema",
    "model_message_content_block_json_schema",
    "model_message_content_json_schema",
    "model_input_message_json_schema",
    "model_tool_choice_json_schema",
    "model_text_request_json_schema",
    "model_network_status_event_json_schema",
    "DiffHunk",
    "NodeReplCuaAppDisplay",
    "ToolResultDisplayPayload",
    "Model",
    "ModelEvent",
    "ModelInputFormat",
    "ModelInvocationContext",
    "ModelOptions",
    "ModelProperties",
    "ModelSelection",
    "ModelSelectionOrigin",
    "ModelTechnologyResponseFormat",
]

JsonSchema = dict[str, Any]

# ---------------------------------------------------------------------------
# Branded model identifiers
# ---------------------------------------------------------------------------

# PORT: TS ``string & { readonly __brand: "ModelProviderId" }`` — non-empty at runtime.
ModelProviderId = str
ModelId = str


@dataclass(slots=True)
class Model:
    """A model selection — provider + model id (+ optional settings).

    PORT: mirrors the ``Model`` interface from ``packages/contracts/src/model``.
    """

    provider: ModelProviderId
    model: ModelId
    settings: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class ModelEvent:
    """A model-stream event emitted during a model turn.

    PORT: mirrors the ``ModelEvent`` union/interface from
    ``packages/contracts/src/model``. Kept as a loose payload container.
    """

    kind: str
    data: Any = None


# PORT: TS ``ModelInputFormat`` is a string union ("text" | "image" | "structured").
ModelInputFormat = str


@dataclass(slots=True)
class ModelInvocationContext:
    """Context carried through a single model invocation.

    PORT: mirrors ``ModelInvocationContext``. Wraps the trace/session identity so
    telemetry and status sinks can attribute model calls.
    """

    trace_id: Optional[TraceId] = None
    session_id: Optional[SessionId] = None
    turn_id: Optional[TurnId] = None


@dataclass(slots=True)
class ModelOptions:
    """Model invocation options (reasoning effort, thinking budget, etc.).

    PORT: mirrors ``ModelOptions`` — a loose bag of provider-neutral knobs.
    """

    reasoning_effort: Optional[str] = None
    thinking_budget_tokens: Optional[int] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelProperties:
    """Static properties of a model (capabilities, limits).

    PORT: mirrors ``ModelProperties`` — whether the model supports streaming,
    reasoning, tool use, etc.
    """

    supports_streaming: bool = False
    supports_tools: bool = True
    supports_reasoning: bool = False
    max_context_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None


@dataclass(slots=True)
class ModelSelection:
    """A (possibly overridden) model selection for a turn/session.

    PORT: mirrors ``ModelSelection`` — a resolved model choice that may carry an
    override on top of the session default.
    """

    provider: ModelProviderId
    model: ModelId
    base_model: Optional[ModelId] = None
    settings: Optional[dict[str, Any]] = None


class ModelSelectionOrigin(str, Enum):
    """Origin of a model selection override.

    PORT: mirrors ``ModelSelectionOrigin`` (``as const`` → ``enum.Enum``).
    """

    USER = "user"
    SYSTEM = "system"
    DEFAULT = "default"
    SUBAGENT = "subagent"


# PORT: TS ``ModelTechnologyResponseFormat`` is a string union ("text" | "json").
ModelTechnologyResponseFormat = str


def create_model_provider_id(provider_id: str) -> ModelProviderId:
    """Mirrors ``createModelProviderId`` — trims and rejects empty ids."""
    normalized = provider_id.strip()
    if len(normalized) == 0:
        raise ModelProtocolError(
            ModelErrorCode.INVALID_MODEL_SELECTION, "Model provider id is empty"
        )
    return normalized


def create_model_id(model_id: str) -> ModelId:
    """Mirrors ``createModelId`` — trims and rejects empty ids."""
    normalized = model_id.strip()
    if len(normalized) == 0:
        raise ModelProtocolError(ModelErrorCode.INVALID_MODEL_SELECTION, "Model id is empty")
    return normalized


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ModelRequestSessionType(str, Enum):
    MAIN = "main"
    OTHER = "other"
    SUBAGENT = "subagent"


class ModelRetryBudget(str, Enum):
    """Retry budget tier (runtime-only).

    - ``default``: the adapter's resolved ``maxAttempts`` (10 retries by default).
    - ``unbounded``: transient failures are retried without a ceiling (same backoff
      curve, capped at 60s then probed forever); permanent failures still throw.
      Used by workflow actors: a model error is never a workflow error, the only
      exit is a user cancel.
    """

    DEFAULT = "default"
    UNBOUNDED = "unbounded"


class ModelErrorCode(str, Enum):
    INVALID_MODEL_SELECTION = "invalid_model_selection"
    MODEL_CONFIG_MISSING = "model_config_missing"
    PROVIDER_NOT_FOUND = "provider_not_found"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    MODEL_NOT_FOUND = "model_not_found"
    INVALID_MODEL_REQUEST = "invalid_model_request"
    INVALID_MODEL_RESPONSE = "invalid_model_response"
    MODEL_REQUEST_FAILED = "model_request_failed"
    MODEL_REQUEST_AUTH_MISSING = "model_request_auth_missing"
    MODEL_REQUEST_CANCELLED = "model_request_cancelled"
    MODEL_REQUEST_TIMEOUT = "model_request_timeout"
    MODEL_RATE_LIMITED = "model_rate_limited"
    MODEL_CONTEXT_EXCEEDED = "model_context_exceeded"


class ModelTransportKind(str, Enum):
    HTTP = "http"
    SSE = "sse"
    WEBSOCKET = "websocket"


class ModelRetryReason(str, Enum):
    RATE_LIMITED = "rate_limited"
    PROVIDER_OVERLOADED = "provider_overloaded"
    SERVER_ERROR = "server_error"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    STREAM_IDLE_TIMEOUT = "stream_idle_timeout"
    STALE_CONNECTION = "stale_connection"
    AUTH_REFRESH = "auth_refresh"
    # Anthropic 明确拒绝历史 thinking signature 后，对请求副本清理并立即重试一次。
    REASONING_SIGNATURE_REPAIR = "reasoning_signature_repair"
    # off-peak 闲时排队（429/3105+Retry-After）：豁免重试预算、无限探测（仅 idle plan provider）。
    OFFPEEK_QUEUED = "offpeak_queued"


class ModelFailureReason(str, Enum):
    """``ModelFailureReason`` spreads ``ModelRetryReason`` and adds terminal reasons."""

    RATE_LIMITED = "rate_limited"
    PROVIDER_OVERLOADED = "provider_overloaded"
    SERVER_ERROR = "server_error"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    STREAM_IDLE_TIMEOUT = "stream_idle_timeout"
    STALE_CONNECTION = "stale_connection"
    AUTH_REFRESH = "auth_refresh"
    REASONING_SIGNATURE_REPAIR = "reasoning_signature_repair"
    OFFPEEK_QUEUED = "offpeak_queued"

    AUTH_FAILED = "auth_failed"
    CANCELLED = "cancelled"
    CONTEXT_EXCEEDED = "context_exceeded"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROXY_ERROR = "proxy_error"
    TLS_ERROR = "tls_error"
    UNKNOWN = "unknown"


class ModelProtocolError(Exception):
    """Mirrors ``class ModelProtocolError extends Error``."""

    def __init__(
        self, code: ModelErrorCode, message: str, context: Optional[dict[str, Any]] = None
    ) -> None:
        super().__init__(message)
        self.name = "ModelProtocolError"
        self.code = code
        self.context = context


# ---------------------------------------------------------------------------
# Network status events
# ---------------------------------------------------------------------------

class ModelStreamRecoveryStatus:
    """Mirrors ``ModelStreamRecoveryStatus``."""

    __slots__ = ("attempt_id", "retry_number", "max_retries", "recovered_from_request_id", "anchor_id")

    def __init__(
        self,
        attempt_id: str,
        retry_number: int,
        max_retries: int,
        recovered_from_request_id: Optional[str] = None,
        anchor_id: Optional[str] = None,
    ) -> None:
        self.attempt_id = attempt_id
        self.retry_number = retry_number
        self.max_retries = max_retries
        self.recovered_from_request_id = recovered_from_request_id
        self.anchor_id = anchor_id


@dataclass(slots=True)
class ModelNetworkStatusBase:
    """Base fields shared by every ``ModelNetworkStatusEvent`` variant."""

    timestamp: str
    trace_id: TraceId
    request_id: str
    provider_id: ModelProviderId
    model_id: ModelId
    transport: ModelTransportKind
    attempt: int
    # 本次请求的重试预算总尝试数（含首次）。0 = 无上限（ModelRetryBudget.Unbounded）。
    max_attempts: int
    query_id: Optional[QueryId] = None
    session_id: Optional[SessionId] = None
    turn_id: Optional[TurnId] = None
    parent_session_id: Optional[SessionId] = None
    tool_call_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    query_source: Optional[str] = None
    base_url: Optional[str] = None
    provider_kind: Optional[str] = None
    stream_recovery: Optional[ModelStreamRecoveryStatus] = None
    request_headers: Optional[dict[str, str]] = None
    response_headers: Optional[dict[str, str]] = None
    request_header_count: Optional[int] = None
    response_header_count: Optional[int] = None
    # PORT: TS type is ``ResolvedModelApiCallObservation`` (telemetry contract, not
    # part of the kernel). Kept as a loose payload so the field survives round-trips.
    model_call: Any = None


@dataclass(slots=True)
class ModelRequestStartedStatusEvent(ModelNetworkStatusBase):
    type: str = field(default="model_request_started", init=False)


@dataclass(slots=True)
class ModelRequestQueuedStatusEvent(ModelNetworkStatusBase):
    """Runner's ``tryAcquire`` missed → queued. Runtime observation only."""

    type: str = field(default="model_request_queued", init=False)


@dataclass(slots=True)
class ModelRequestAdmittedStatusEvent(ModelNetworkStatusBase):
    """Ticket acquired → admitted, with the queued duration."""

    queued_ms: int = 0
    type: str = field(default="model_request_admitted", init=False)


@dataclass(slots=True)
class ModelRequestCompletedStatusEvent(ModelNetworkStatusBase):
    duration_ms: int = 0
    finish_reason: Optional[str] = None
    usage: Optional["ModelUsage"] = None
    provider_request_id: Optional[str] = None
    time_to_first_provider_event_ms: Optional[int] = None
    time_to_first_content_ms: Optional[int] = None
    time_to_first_text_ms: Optional[int] = None
    stream_max_idle_ms: Optional[int] = None
    stream_stall_count: Optional[int] = None
    stream_output_committed: Optional[bool] = None
    type: str = field(default="model_request_completed", init=False)


@dataclass(slots=True)
class ModelRequestFailedStatusEvent(ModelNetworkStatusBase):
    message: str = ""
    retryable: bool = False
    reason: ModelFailureReason = ModelFailureReason.UNKNOWN
    duration_ms: Optional[int] = None
    status_code: Optional[int] = None
    error_code: Optional[ModelErrorCode] = None
    provider_error_code: Optional[str] = None
    provider_error_message: Optional[str] = None
    provider_request_id: Optional[str] = None
    retry_after_ms: Optional[int] = None
    # PORT: ``ModelApiErrorPhase`` lives in the telemetry contract; kept loose.
    error_phase: Any = None
    exception_type: Optional[str] = None
    stream_output_committed: Optional[bool] = None
    type: str = field(default="model_request_failed", init=False)


@dataclass(slots=True)
class ModelRetryScheduledStatusEvent(ModelNetworkStatusBase):
    message: str = ""
    delay_ms: int = 0
    next_attempt: int = 0
    reason: ModelRetryReason = ModelRetryReason.SERVER_ERROR
    status_code: Optional[int] = None
    error_code: Optional[ModelErrorCode] = None
    provider_error_code: Optional[str] = None
    provider_error_message: Optional[str] = None
    provider_request_id: Optional[str] = None
    retry_after_ms: Optional[int] = None
    type: str = field(default="model_retry_scheduled", init=False)


@dataclass(slots=True)
class ModelStreamStalledStatusEvent(ModelNetworkStatusBase):
    message: str = ""
    idle_ms: int = 0
    timeout_ms: int = 0
    type: str = field(default="model_stream_stalled", init=False)


@dataclass(slots=True)
class ModelTelemetryMilestoneStatusEvent(ModelNetworkStatusBase):
    """Provider milestones consumed by live observability sinks only."""

    elapsed_ms: int = 0
    type: str = "model_first_provider_event"


ModelNetworkStatusEvent = Union[
    ModelRequestQueuedStatusEvent,
    ModelRequestAdmittedStatusEvent,
    ModelRequestStartedStatusEvent,
    ModelRequestCompletedStatusEvent,
    ModelRequestFailedStatusEvent,
    ModelRetryScheduledStatusEvent,
    ModelStreamStalledStatusEvent,
    ModelTelemetryMilestoneStatusEvent,
]


class ModelStatusSink:
    """Mirrors the ``ModelStatusSink`` interface.

    # PORT: TS is an interface with ``publish`` + optional ``publishFailure``.
    # Python uses a Protocol; concrete implementations may be plain objects.
    """

    def publish(self, event: ModelNetworkStatusEvent) -> Union[None, Awaitable[None]]:
        raise NotImplementedError

    def publish_failure(
        self, event: ModelRequestFailedStatusEvent, error: Any
    ) -> Union[None, Awaitable[None]]:
        """Optional hook — mirrors ``publishFailure?``. Defaults to a no-op."""
        return None


# ---------------------------------------------------------------------------
# Message content
# ---------------------------------------------------------------------------

ModelMessageRole = str  # "system" | "user" | "assistant" | "tool"


@dataclass(slots=True)
class ModelToolCall:
    id: str
    name: str
    input: Any
    provider_executed: Optional[bool] = None


AttachmentKind = str  # "local_file" | "resource" | "inline"


@dataclass(slots=True)
class AttachmentRef:
    id: str
    kind: AttachmentKind
    uri: Optional[str] = None
    path: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    placeholder: Optional[str] = None


@dataclass(slots=True)
class ModelTextContentBlock:
    text: str
    type: str = field(default="text", init=False)


@dataclass(slots=True)
class ModelReasoningContentBlock:
    text: str
    provider_options: Optional[dict[str, Any]] = None
    type: str = field(default="reasoning", init=False)


@dataclass(slots=True)
class ModelImageContentBlock:
    media_type: str
    data_url: str
    detail: Optional[str] = None  # "auto" | "low" | "high" | "original"
    source: Optional[AttachmentRef] = None
    type: str = field(default="image", init=False)


@dataclass(slots=True)
class ModelVideoContentBlock:
    """视频输入内容块（provider-neutral，与 image 同构；只承载 base64 dataUrl）。"""

    media_type: str
    data_url: str
    source: Optional[AttachmentRef] = None
    type: str = field(default="video", init=False)


@dataclass(slots=True)
class ModelFileContentBlock:
    media_type: str
    name: Optional[str] = None
    uri: Optional[str] = None
    data_url: Optional[str] = None
    text: Optional[str] = None
    source: Optional[AttachmentRef] = None
    type: str = field(default="file", init=False)


@dataclass(slots=True)
class ModelResourceLinkContentBlock:
    uri: str
    name: Optional[str] = None
    title: Optional[str] = None
    type: str = field(default="resource_link", init=False)


ModelMessageContentBlock = Union[
    ModelTextContentBlock,
    ModelReasoningContentBlock,
    ModelImageContentBlock,
    ModelVideoContentBlock,
    ModelFileContentBlock,
    ModelResourceLinkContentBlock,
]

ModelMessageContent = Union[str, list[ModelMessageContentBlock]]


@dataclass(slots=True)
class ModelCacheControl:
    type: str = "ephemeral"
    ttl: Optional[str] = None  # "5m" | "1h"
    scope: Optional[str] = None  # "global" | "org"


@dataclass(slots=True)
class ModelInputMessage:
    role: ModelMessageRole
    content: ModelMessageContent
    cache_control: Optional[ModelCacheControl] = None
    tool_calls: Optional[list[ModelToolCall]] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    is_error: Optional[bool] = None
    provider_id: Optional[ModelProviderId] = None
    model_id: Optional[ModelId] = None


def model_message_content_to_text(content: ModelMessageContent) -> str:
    """Mirrors ``modelMessageContentToText``."""
    if isinstance(content, str):
        return content
    return "\n\n".join(
        block_text for block_text in (model_message_content_block_to_text(b) for b in content) if block_text
    )


def model_message_content_block_to_text(block: ModelMessageContentBlock) -> str:
    """Mirrors ``modelMessageContentBlockToText``."""
    block_type = getattr(block, "type", None)

    if block_type == "text":
        return block.text
    if block_type == "reasoning":
        return ""
    if block_type in ("image", "video"):
        return _attachment_placeholder("Attached", block.media_type, _source_placeholder(block.source))
    if block_type == "file":
        if block.text is not None and len(block.text) > 0:
            return block.text
        name = block.name if block.name is not None else _source_placeholder(block.source)
        return _attachment_placeholder("Attached", block.media_type, name)
    if block_type == "resource_link":
        return f"[Resource: {block.title or block.name or block.uri}]"

    # PORT: TS switch is exhaustive over the union; unknown shapes fall through to "".
    return ""


def _source_placeholder(source: Optional[AttachmentRef]) -> Optional[str]:
    return source.placeholder if source is not None else None


def _attachment_placeholder(prefix: str, media_type: str, name: Optional[str]) -> str:
    return (
        f"[{prefix} {media_type}: {name}]"
        if name is not None and len(name) > 0
        else f"[{prefix} {media_type}]"
    )


# ---------------------------------------------------------------------------
# Tool execution surface
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ModelToolExecutionContext:
    tool_call_id: str
    abort_signal: Any = None  # AbortSignal
    trace_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


ModelToolSideEffectScope = str  # "none" | "workspace" | "git" | "network" | "system" | "session" | "userInteraction"


@dataclass(slots=True)
class ModelToolContract:
    name: str
    input_schema: JsonSchema
    description: Optional[str] = None
    capability: Optional[str] = None
    execution_mode: Optional[str] = None  # ToolExecutionMode
    provider_native: Any = None  # ProviderNativeToolSpec
    output_schema: Optional[JsonSchema] = None
    strict: Optional[bool] = None
    read_only: Optional[bool] = None
    destructive: Optional[bool] = None
    concurrent_safe: Optional[bool] = None
    requires_user_interaction: Optional[bool] = None
    max_output_bytes: Optional[int] = None
    timeout_ms: Optional[int] = None
    needs_approval: Optional[bool] = None
    side_effect_scope: Optional[ModelToolSideEffectScope] = None
    permission: Any = None  # ToolPermissionSpec
    result_budget: Any = None  # ToolResultBudget
    execute: Optional[Callable[[Any, ModelToolExecutionContext], Union[Awaitable[Any], Any]]] = None


ModelToolChoice = Union[str, dict[str, str]]  # "auto" | "none" | "required" | {"type","toolName"}


# ---------------------------------------------------------------------------
# Usage accounting
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ModelServerToolUsage:
    web_search_requests: Optional[int] = None
    web_fetch_requests: Optional[int] = None


@dataclass(slots=True)
class ModelUsage:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    server_tool_use: Optional[ModelServerToolUsage] = None


@dataclass(slots=True)
class ModelUsageSummary:
    source: str
    model_request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int
    web_search_requests: int
    web_fetch_requests: int


def get_model_usage_total_tokens(usage: Optional[ModelUsage] = None) -> int:
    """Mirrors ``getModelUsageTotalTokens``."""
    if usage is None:
        return 0
    input_tokens = usage.input_tokens
    if input_tokens is None:
        input_tokens = (usage.cache_read_tokens or 0) + (usage.cache_write_tokens or 0)
    if usage.total_tokens is not None:
        return usage.total_tokens
    return input_tokens + (usage.output_tokens or 0)


def get_model_usage_context_tokens(usage: Optional[ModelUsage] = None) -> Optional[int]:
    """Mirrors ``getModelUsageContextTokens``."""
    if usage is None:
        return None

    input_tokens = get_model_usage_input_window_tokens(usage)
    output_tokens = _non_negative_integer(usage.output_tokens) or 0
    context_tokens = (input_tokens or 0) + output_tokens
    if context_tokens > 0:
        return context_tokens

    return _positive_integer(usage.total_tokens)


def get_model_usage_input_window_tokens(usage: Optional[ModelUsage] = None) -> Optional[int]:
    """Mirrors ``getModelUsageInputWindowTokens``.

    [T] AI SDK v6 的 Anthropic inputTokens 已经是普通输入 + cache read/write 的 total input。
    这里再叠 cacheReadTokens 会把 context meter 和 compact 阈值放大一截。
    """
    if usage is None:
        return None

    input_tokens = _positive_integer(usage.input_tokens)
    if input_tokens is not None:
        return input_tokens

    total_tokens = _positive_integer(usage.total_tokens)
    if total_tokens is not None:
        output_tokens = _non_negative_integer(usage.output_tokens) or 0
        return max(0, total_tokens - output_tokens)

    cache_tokens = (_non_negative_integer(usage.cache_read_tokens) or 0) + (
        _non_negative_integer(usage.cache_write_tokens) or 0
    )
    return cache_tokens if cache_tokens > 0 else None


def has_model_usage(usage: Optional[ModelUsage] = None) -> bool:
    """Mirrors ``hasModelUsage``."""
    if usage is None:
        return False
    return (
        usage.input_tokens is not None
        or usage.output_tokens is not None
        or usage.total_tokens is not None
        or usage.cache_read_tokens is not None
        or usage.cache_write_tokens is not None
        or usage.reasoning_tokens is not None
        or (usage.server_tool_use is not None and usage.server_tool_use.web_search_requests is not None)
        or (usage.server_tool_use is not None and usage.server_tool_use.web_fetch_requests is not None)
    )


def _positive_integer(value: Optional[float]) -> Optional[int]:
    """Mirrors ``positiveInteger`` — ``undefined`` when not finite or <= 0."""
    if value is None or not _is_finite(value):
        return None
    integer = int(value // 1) if value >= 0 else -int((-value) // 1)
    return integer if integer > 0 else None


def _non_negative_integer(value: Optional[float]) -> Optional[int]:
    """Mirrors ``nonNegativeInteger`` — ``undefined`` when not finite or < 0."""
    if value is None or not _is_finite(value):
        return None
    integer = int(value // 1) if value >= 0 else -int((-value) // 1)
    return integer if integer >= 0 else None


def _is_finite(value: float) -> bool:
    try:
        return value == value and value not in (float("inf"), float("-inf"))
    except TypeError:  # pragma: no cover - defensive, mirrors Number.isFinite on non-numbers
        return False


def create_model_usage_summary(usages: list[ModelUsage]) -> Optional[ModelUsageSummary]:
    """Mirrors ``createModelUsageSummary`` — folds usages with a reduce."""
    real_usages = [u for u in usages if has_model_usage(u)]
    if len(real_usages) == 0:
        return None

    summary = ModelUsageSummary(
        source="provider",
        model_request_count=0,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        web_search_requests=0,
        web_fetch_requests=0,
    )
    for usage in real_usages:
        summary = ModelUsageSummary(
            source="provider",
            model_request_count=summary.model_request_count + 1,
            input_tokens=summary.input_tokens + (usage.input_tokens or 0),
            output_tokens=summary.output_tokens + (usage.output_tokens or 0),
            total_tokens=summary.total_tokens + get_model_usage_total_tokens(usage),
            cache_read_tokens=summary.cache_read_tokens + (usage.cache_read_tokens or 0),
            cache_write_tokens=summary.cache_write_tokens + (usage.cache_write_tokens or 0),
            reasoning_tokens=summary.reasoning_tokens + (usage.reasoning_tokens or 0),
            web_fetch_requests=summary.web_fetch_requests
            + ((usage.server_tool_use.web_fetch_requests or 0) if usage.server_tool_use else 0),
            web_search_requests=summary.web_search_requests
            + ((usage.server_tool_use.web_search_requests or 0) if usage.server_tool_use else 0),
        )
    return summary


# ---------------------------------------------------------------------------
# Requests / results
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ModelRequestSettings:
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    stop_sequences: Optional[list[str]] = None
    seed: Optional[int] = None


@dataclass(slots=True)
class ModelTextRequest(ModelRequestSettings):
    messages: list[ModelInputMessage] = field(default_factory=list)
    tools: Optional[list[ModelToolContract]] = None
    tool_choice: Optional[ModelToolChoice] = None
    response_json_schema: Optional[JsonSchema] = None
    provider_options: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    abort_signal: Any = None  # AbortSignal
    status_sink: Optional[ModelStatusSink] = None
    trace_context: Any = None  # TraceContext
    model_call: Any = None
    model_request_session_type: Optional[ModelRequestSessionType] = None
    model_retry_budget: Optional[ModelRetryBudget] = None
    model_request_admission: Optional["ModelRequestAdmission"] = None
    stream_idle_timeout_retry_number: Optional[int] = None
    stream_recovery: Optional[ModelStreamRecoveryStatus] = None
    preserve_provider_stream_boundaries: Optional[bool] = None


@dataclass(slots=True)
class ModelSource:
    source_type: str  # "url" | "document"
    id: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    media_type: Optional[str] = None
    filename: Optional[str] = None
    provider_metadata: Optional[dict[str, Any]] = None
    type: str = field(default="source", init=False)


@dataclass(slots=True)
class ModelToolResult:
    id: str
    name: str
    input: Any
    output: Any
    provider_executed: Optional[bool] = None
    provider_metadata: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class ModelTextResult:
    text: str
    finish_reason: str
    usage: ModelUsage
    reasoning: Optional[list[ModelReasoningContentBlock]] = None
    tool_calls: Optional[list[ModelToolCall]] = None
    tool_results: Optional[list[ModelToolResult]] = None
    sources: Optional[list[ModelSource]] = None
    provider_metadata: Optional[dict[str, Any]] = None


# PORT: TS is a 20-member discriminated union. Python models each variant as a
# dict-shaped payload because the kernel only ever pattern-matches on ``type``.
ModelStreamEvent = dict[str, Any]


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ModelRequestTarget:
    """The minimal model identity the admission gate sees: provider + model."""

    provider_id: str
    model_id: str


class ModelRequestAdmissionTicket:
    """Admission ticket for one model attempt (runtime-only).

    # PORT: extends ``ModelStatusSink`` upstream; here it only declares the
    # ``release`` contract plus the sink methods it inherits.
    """

    def release(self) -> None:
        raise NotImplementedError

    def publish(self, event: ModelNetworkStatusEvent) -> Union[None, Awaitable[None]]:
        raise NotImplementedError


class ModelRequestAdmission:
    """Admission port for model requests (runtime-only).

    ``try_acquire`` is the synchronous fast path; a miss means the runner must
    ``acquire`` and report queueing. Missing ``try_acquire`` means the runner
    can never distinguish "queued" from "admitted immediately", so it emits
    neither event — behaviour stays byte-identical to the original.
    """

    def try_acquire(self, input: dict[str, Any]) -> Optional[ModelRequestAdmissionTicket]:
        """Mirrors optional ``tryAcquire?`` — default: no fast path."""
        return None

    async def acquire(self, input: dict[str, Any]) -> ModelRequestAdmissionTicket:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# JSON schemas
# ---------------------------------------------------------------------------

model_selection_json_schema: JsonSchema = {
    "type": "object",
    "required": ["providerId", "modelId"],
    "additionalProperties": False,
    "properties": {
        "providerId": {"type": "string", "minLength": 1},
        "modelId": {"type": "string", "minLength": 1},
        "options": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "reasoningLevel": {"type": "string", "minLength": 1},
                "maxOutputTokens": {"type": "number", "minimum": 1},
            },
        },
    },
}

attachment_ref_json_schema: JsonSchema = {
    "type": "object",
    "required": ["id", "kind"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "kind": {"enum": ["local_file", "resource", "inline"]},
        "uri": {"type": "string"},
        "path": {"type": "string"},
        "mimeType": {"type": "string"},
        "sizeBytes": {"type": "number"},
        "sha256": {"type": "string"},
        "placeholder": {"type": "string"},
    },
}

model_message_content_block_json_schema: JsonSchema = {
    "oneOf": [
        {
            "type": "object",
            "required": ["type", "text"],
            "additionalProperties": False,
            "properties": {"type": {"enum": ["text"]}, "text": {"type": "string"}},
        },
        {
            "type": "object",
            "required": ["type", "text"],
            "additionalProperties": False,
            "properties": {
                "type": {"enum": ["reasoning"]},
                "text": {"type": "string"},
                "providerOptions": {"type": "object"},
            },
        },
        {
            "type": "object",
            "required": ["type", "mediaType", "dataUrl"],
            "additionalProperties": False,
            "properties": {
                "type": {"enum": ["image"]},
                "mediaType": {"type": "string", "minLength": 1},
                "dataUrl": {"type": "string", "minLength": 1},
                "detail": {"enum": ["auto", "low", "high", "original"]},
                "source": attachment_ref_json_schema,
            },
        },
        {
            "type": "object",
            "required": ["type", "mediaType", "dataUrl"],
            "additionalProperties": False,
            "properties": {
                "type": {"enum": ["video"]},
                "mediaType": {"type": "string", "minLength": 1},
                "dataUrl": {"type": "string", "minLength": 1},
                "source": attachment_ref_json_schema,
            },
        },
        {
            "type": "object",
            "required": ["type", "mediaType"],
            "additionalProperties": False,
            "properties": {
                "type": {"enum": ["file"]},
                "mediaType": {"type": "string", "minLength": 1},
                "name": {"type": "string"},
                "uri": {"type": "string"},
                "dataUrl": {"type": "string"},
                "text": {"type": "string"},
                "source": attachment_ref_json_schema,
            },
        },
        {
            "type": "object",
            "required": ["type", "uri"],
            "additionalProperties": False,
            "properties": {
                "type": {"enum": ["resource_link"]},
                "uri": {"type": "string", "minLength": 1},
                "name": {"type": "string"},
                "title": {"type": "string"},
            },
        },
    ],
}

model_message_content_json_schema: JsonSchema = {
    "oneOf": [
        {"type": "string"},
        {"type": "array", "items": model_message_content_block_json_schema},
    ],
}

model_input_message_json_schema: JsonSchema = {
    "type": "object",
    "required": ["role", "content"],
    "additionalProperties": False,
    "properties": {
        "role": {"enum": ["system", "user", "assistant", "tool"]},
        "content": model_message_content_json_schema,
        "cacheControl": {
            "type": "object",
            "required": ["type"],
            "additionalProperties": False,
            "properties": {
                "type": {"enum": ["ephemeral"]},
                "ttl": {"enum": ["5m", "1h"]},
                "scope": {"enum": ["global", "org"]},
            },
        },
        "toolCalls": {"type": "array"},
        "toolCallId": {"type": "string"},
        "toolName": {"type": "string"},
        "isError": {"type": "boolean"},
        "providerId": {"type": "string", "minLength": 1},
        "modelId": {"type": "string", "minLength": 1},
    },
}

model_tool_choice_json_schema: JsonSchema = {
    "oneOf": [
        {"enum": ["auto", "none", "required"]},
        {
            "type": "object",
            "required": ["type", "toolName"],
            "additionalProperties": False,
            "properties": {
                "type": {"enum": ["tool"]},
                "toolName": {"type": "string", "minLength": 1},
            },
        },
    ],
}

model_text_request_json_schema: JsonSchema = {
    "type": "object",
    "required": ["messages"],
    "additionalProperties": False,
    "properties": {
        "messages": {"type": "array", "items": model_input_message_json_schema},
        "tools": {"type": "array"},
        "toolChoice": model_tool_choice_json_schema,
        "temperature": {"type": "number"},
        "maxOutputTokens": {"type": "number"},
        "topP": {"type": "number"},
        "topK": {"type": "number"},
        "presencePenalty": {"type": "number"},
        "frequencyPenalty": {"type": "number"},
        "stopSequences": {"type": "array", "items": {"type": "string"}},
        "seed": {"type": "number"},
        "responseJsonSchema": {"type": "object"},
        "providerOptions": {"type": "object"},
        "metadata": {"type": "object"},
    },
}

model_network_status_event_json_schema: JsonSchema = {
    "type": "object",
    "required": [
        "type",
        "timestamp",
        "traceId",
        "requestId",
        "model",
        "transport",
        "attempt",
        "maxAttempts",
    ],
    "additionalProperties": True,
    "properties": {
        "type": {
            "enum": [
                "model_request_started",
                "model_request_completed",
                "model_request_failed",
                "model_retry_scheduled",
                "model_stream_stalled",
            ]
        },
        "timestamp": {"type": "string", "minLength": 1},
        "traceId": {"type": "string", "minLength": 1},
        "sessionId": {"type": "string", "minLength": 1},
        "turnId": {"type": "string", "minLength": 1},
        "querySource": {"type": "string", "minLength": 1},
        "requestId": {"type": "string", "minLength": 1},
        "model": model_selection_json_schema,
        "transport": {"enum": [m.value for m in ModelTransportKind]},
        "attempt": {"type": "number", "minimum": 1},
        # 0 = 无上限重试预算，故下界是 0 而不是 1。
        "maxAttempts": {"type": "number", "minimum": 0},
        "delayMs": {"type": "number", "minimum": 0},
        "durationMs": {"type": "number", "minimum": 0},
        "idleMs": {"type": "number", "minimum": 0},
        "nextAttempt": {"type": "number", "minimum": 1},
        "reason": {"enum": [m.value for m in ModelFailureReason]},
        "retryable": {"type": "boolean"},
        "message": {"type": "string"},
        "statusCode": {"type": "number"},
        "requestHeaders": {"type": "object", "additionalProperties": {"type": "string"}},
        "responseHeaders": {"type": "object", "additionalProperties": {"type": "string"}},
        "requestHeaderCount": {"type": "number", "minimum": 0},
        "responseHeaderCount": {"type": "number", "minimum": 0},
        "streamRecovery": {
            "type": "object",
            "required": ["attemptId", "retryNumber", "maxRetries"],
            "additionalProperties": False,
            "properties": {
                "attemptId": {"type": "string", "minLength": 1},
                "retryNumber": {"type": "number", "minimum": 1},
                "maxRetries": {"type": "number", "minimum": 0},
                "recoveredFromRequestId": {"type": "string", "minLength": 1},
                "anchorId": {"type": "string", "minLength": 1},
            },
        },
        "timeoutMs": {"type": "number", "minimum": 0},
    },
}


# ---------------------------------------------------------------------------
# Tool result display payloads
# ---------------------------------------------------------------------------
# PORT: these mirror the display-payload types from
# ``packages/contracts/src/model/display.ts``. They are imported by
# ``tool/executor/result_display.py``. Field sets are a faithful 1:1 of the
# original interfaces; only the display-path payloads used by the coding agent.

@dataclass(slots=True)
class DiffHunk:
    """A single unified-diff hunk.

    PORT: mirrors ``DiffHunk`` — ``header`` is the ``@@ ... @@`` line and
    ``lines`` are the body lines (each carrying its ``+``/``-``/`` `` prefix).
    """

    header: str
    lines: list[str]


@dataclass(slots=True)
class NodeReplCuaAppDisplay:
    """Display metadata for a Node REPL CUA target app.

    PORT: mirrors ``NodeReplCuaAppDisplay``. The Node REPL host is platform-
    specific and excluded from the coding-only port; the payload type is kept
    for display compatibility.
    """

    app_name: str
    window_title: Optional[str] = None


@dataclass(slots=True)
class ToolResultDisplayPayload:
    """Generic tool-result display payload.

    PORT: mirrors ``ToolResultDisplayPayload`` — carries the rendered ``content``
    plus optional ``metadata`` (e.g. MCP presentation, CUA display).
    """

    content: Any
    metadata: dict[str, Any] = field(default_factory=dict)
