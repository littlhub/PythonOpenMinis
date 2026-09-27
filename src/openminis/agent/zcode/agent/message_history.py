"""Message History — maintains conversation context across turns.

Ported from: packages/core/src/agent/message-history.ts
Original package: @zcode/core (agent)

# PORT-STATUS: partial
# ``@zcode/contracts`` types not shipped by ``..contracts`` yet
# (``RuntimeInputPresentation``, ``TokenUsageInfo``). Local stand-ins are marked
# with ``# PORT:`` and should be replaced by contract imports once they land.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional, Protocol, Sequence, Union

from ..contracts.model import (
    ModelCacheControl,
    ModelMessageContent,
    ModelMessageContentBlock,
    ModelReasoningContentBlock,
    ModelTextContentBlock,
    model_message_content_to_text,
)
from ..system_reminder.source import SYSTEM_REMINDER_SOURCES, SystemReminderSource

__all__ = [
    "ToolCallInput",
    "ReasoningContentInput",
    "ModelInputMessage",
    "RuntimeMessageSource",
    "RuntimeMessageMetadata",
    "RuntimeMessageMessageEntry",
    "RuntimeAttachmentEntry",
    "RuntimeMessageEntry",
    "CacheStats",
    "MessageHistory",
    "MessageHistoryImpl",
    "count_context_prefix_messages",
    "system_reminder_runtime_metadata",
    "real_user_runtime_metadata",
    "legacy_synthetic_runtime_metadata",
    "todo_reminder_runtime_metadata",
    "system_reminder_attachment_entry",
    "create_runtime_user_entry",
    "create_runtime_assistant_entry",
    "create_runtime_tool_result_entry",
    "is_known_system_reminder_source",
    "clone_runtime_message_entry",
    "invalidate_runtime_token_usage",
    "is_runtime_attachment_entry",
    "clone_model_input_message",
    "clone_model_message_content",
    "create_message_history",
]


# ---------------------------------------------------------------------------
# PORT: stand-in contract types
# ---------------------------------------------------------------------------
# Declared in ``@zcode/contracts`` but not shipped by ``..contracts`` yet. Transcribed
# 1:1 so this module keeps the exact shapes; replace with contract imports once the
# contracts port grows these modules.

# PORT: ``RuntimeInputPresentation`` (packages/contracts/src/interfaces/
# runtime-input-presentation.ts) is a string enum →
# "user_steer" | "coordinator_steer" | "coordinator_input" | "subagent_reply_steer"
# | "subagent_reply" | "task_notification_steer" | "task_notification".
RuntimeInputPresentation = str


# PORT: ``TokenUsageInfo`` (packages/contracts/src/interfaces/session-store.port.ts)
# ``{ total?; input; output; reasoning; cache: { read; write } }``.
@dataclass(slots=True)
class TokenUsageCache:
    """The inline ``cache`` shape of ``TokenUsageInfo``."""

    read: int
    write: int


@dataclass(slots=True)
class TokenUsageInfo:
    """Mirrors ``interface TokenUsageInfo``.

    # PORT: required TS fields first, optional ``total`` last (dataclass ordering).
    """

    input: int
    output: int
    reasoning: int
    cache: TokenUsageCache
    total: Optional[int] = None


# ``SYSTEM_REMINDER_SOURCES`` / ``SystemReminderSource`` 已改从 ``..system_reminder.source`` 导入
# （见文件头 import）；下方内联 const 数组已删除。


class ModelIdentity(Protocol):
    """PORT: TS ``Pick<Model, "providerId" | "modelId">`` (contracts/model/model.ts).

    The full ``Model`` port has not landed; only the two members the history writer
    reads are declared.
    """

    provider_id: str
    model_id: str


# Tool call from model (simple type, no brand)
@dataclass(slots=True)
class ToolCallInput:
    """Mirrors ``interface ToolCallInput``."""

    id: str
    name: str
    input: Any


ReasoningContentInput = ModelReasoningContentBlock


@dataclass(slots=True)
class ModelInputMessage:
    """Mirrors ``interface ModelInputMessage`` (this module's local variant)."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: ModelMessageContent
    cache_control: Optional[ModelCacheControl] = None
    tool_calls: Optional[list[ToolCallInput]] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    is_error: Optional[bool] = None
    provider_id: Optional[str] = None
    model_id: Optional[str] = None


# PORT: TS string union — kept as ``str`` so the literal values stay verbatim.
RuntimeMessageSource = str  # SystemReminderSource | "shared_context" | "real_user" | "legacy_synthetic"


@dataclass(slots=True)
class RuntimeMessageMetadata:
    """Mirrors ``interface RuntimeMessageMetadata``."""

    source: RuntimeMessageSource
    input_presentation: Optional[RuntimeInputPresentation] = None


@dataclass(slots=True)
class RuntimeMessageMessageEntry:
    """Mirrors ``interface RuntimeMessageMessageEntry``.

    # PORT: ``kind`` (optional ``"message"``) and the optional tail fields are moved
    # after the required ``message`` (dataclass ordering). Names are unchanged.
    """

    message: ModelInputMessage
    kind: Optional[str] = None  # "message"
    metadata: Optional[RuntimeMessageMetadata] = None
    # 已提交 assistant 自己的 provider tokens；不会发送到 provider。
    tokens: Optional[TokenUsageInfo] = None
    # 仅在当前 query 内生效；不得进入 canonical history 或 Session persistence。
    query_scope: Optional[str] = None  # "output_token_continuation"


@dataclass(slots=True)
class RuntimeAttachmentEntry:
    """Mirrors ``interface RuntimeAttachmentEntry``.

    # PORT: ``kind`` keeps its ``"attachment"`` default and moves to the tail so the
    # required ``content`` / ``metadata`` come first (dataclass ordering).
    """

    content: str
    metadata: RuntimeMessageMetadata
    cache_control: Optional[ModelCacheControl] = None
    kind: str = "attachment"


RuntimeMessageEntry = Union[RuntimeMessageMessageEntry, RuntimeAttachmentEntry]


@dataclass(slots=True)
class CacheStats:
    """Mirrors ``interface CacheStats``."""

    total_messages: int
    cached_messages: int
    last_cache_hit: bool
    cache_read_tokens: Optional[int] = None


# ============================================================
# Message History Interface
# ============================================================


class MessageHistory(Protocol):
    """Mirrors ``interface MessageHistory``."""

    # Initialize with optional system prompt or context prefix messages
    def init(
        self,
        system_prompt_or_messages: Optional[
            Union[str, list[Union[ModelInputMessage, RuntimeMessageEntry]]]
        ] = None,
    ) -> None: ...

    # Add user message
    def add_user(
        self, content: ModelMessageContent, metadata: Optional[RuntimeMessageMetadata] = None
    ) -> None: ...

    # Add structured internal context that provider projection renders at request time.
    def add_attachment(self, source: SystemReminderSource, content: str) -> None: ...

    # Add already-built runtime entries while preserving their source metadata.
    def add_entries(self, entries: Sequence[RuntimeMessageEntry]) -> None: ...

    # Add assistant message (may include tool calls)
    def add_assistant(
        self,
        content: str,
        tool_calls: Optional[list[ToolCallInput]] = None,
        reasoning: Optional[list[ReasoningContentInput]] = None,
        model: Optional[ModelIdentity] = None,
        tokens: Optional[TokenUsageInfo] = None,
    ) -> None: ...

    # Add tool result
    def add_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        content: ModelMessageContent,
        success: bool,
        is_error: Optional[bool] = None,
    ) -> None: ...

    # 借用当前权威 entries，只允许同步只读；跨异步边界时由调用方做数组浅快照。
    def borrow_read_only_runtime_entries(self) -> Sequence[RuntimeMessageEntry]: ...

    # 创建可写的防御性副本；Runtime 内部普通只读点应使用 borrowReadOnlyRuntimeEntries。
    def to_runtime_entries(self) -> list[RuntimeMessageEntry]: ...

    # Replace the active provider-visible history after compact/rewind.
    def replace_messages(
        self, messages: Sequence[Union[ModelInputMessage, RuntimeMessageEntry]]
    ) -> None: ...

    # Get current message count
    def get_message_count(self) -> int: ...

    # Cache management
    def get_cache_stats(self) -> CacheStats: ...

    def set_cache_hit(self, tokens: Optional[int] = None) -> None: ...

    def set_cache_miss(self) -> None: ...

    # Reset for new turn
    def reset(self) -> None: ...


# ============================================================
# Message History Implementation
# ============================================================


class MessageHistoryImpl(MessageHistory):
    """Mirrors ``class MessageHistoryImpl implements MessageHistory``."""

    def __init__(self) -> None:
        self._entries: list[RuntimeMessageEntry] = []
        self._cache_stats: CacheStats = CacheStats(
            total_messages=0,
            cached_messages=0,
            last_cache_hit=False,
        )

    def init(
        self,
        system_prompt_or_messages: Optional[
            Union[str, list[Union[ModelInputMessage, RuntimeMessageEntry]]]
        ] = None,
    ) -> None:
        self._entries = []

        if isinstance(system_prompt_or_messages, str) and len(system_prompt_or_messages) > 0:
            self._entries.append(
                RuntimeMessageMessageEntry(
                    message=ModelInputMessage(
                        role="system",
                        content=system_prompt_or_messages,
                    )
                )
            )
        elif isinstance(system_prompt_or_messages, list):
            self._entries.extend(clone_entry_input(item) for item in system_prompt_or_messages)

        self._cache_stats = CacheStats(
            total_messages=len(self._entries),
            cached_messages=count_context_prefix_messages(self._entries),
            last_cache_hit=False,
        )

    def add_user(
        self, content: ModelMessageContent, metadata: Optional[RuntimeMessageMetadata] = None
    ) -> None:
        self._entries.append(create_runtime_user_entry(content, metadata))
        self._cache_stats.total_messages = len(self._entries)

    def add_attachment(self, source: SystemReminderSource, content: str) -> None:
        self._entries.append(system_reminder_attachment_entry(source, content))
        self._cache_stats.total_messages = len(self._entries)

    def add_entries(self, entries: Sequence[RuntimeMessageEntry]) -> None:
        self._entries.extend(clone_runtime_message_entry(entry) for entry in entries)
        self._cache_stats.total_messages = len(self._entries)

    def add_assistant(
        self,
        content: str,
        tool_calls: Optional[list[ToolCallInput]] = None,
        reasoning: Optional[list[ReasoningContentInput]] = None,
        model: Optional[ModelIdentity] = None,
        tokens: Optional[TokenUsageInfo] = None,
    ) -> None:
        reasoning_blocks = (
            [clone_reasoning_block(block) for block in reasoning] if reasoning is not None else []
        )
        entry = RuntimeMessageMessageEntry(
            message=ModelInputMessage(
                role="assistant",
                content=(
                    [
                        *reasoning_blocks,
                        *([ModelTextContentBlock(text=content)] if len(content) > 0 else []),
                    ]
                    if len(reasoning_blocks) > 0
                    else content
                ),
                tool_calls=(
                    [
                        ToolCallInput(id=tc.id, name=tc.name, input=tc.input)
                        for tc in tool_calls
                    ]
                    if tool_calls is not None
                    else None
                ),
                provider_id=model.provider_id if model is not None else None,
                model_id=model.model_id if model is not None else None,
            )
        )
        if tokens is not None:
            entry.tokens = clone_token_usage_info(tokens)
        self._entries.append(entry)
        self._cache_stats.total_messages = len(self._entries)

    def add_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        content: ModelMessageContent,
        success: bool,
        is_error: Optional[bool] = None,
    ) -> None:
        # PORT: TS default parameter ``isError = !success`` — apply it when omitted.
        if is_error is None:
            is_error = not success
        self._entries.append(
            create_runtime_tool_result_entry(tool_call_id, tool_name, content, is_error)
        )
        self._cache_stats.total_messages = len(self._entries)

    def borrow_read_only_runtime_entries(self) -> Sequence[RuntimeMessageEntry]:
        return self._entries

    def to_runtime_entries(self) -> list[RuntimeMessageEntry]:
        return [clone_runtime_message_entry(entry) for entry in self._entries]

    def replace_messages(
        self, messages: Sequence[Union[ModelInputMessage, RuntimeMessageEntry]]
    ) -> None:
        self._entries = [clone_entry_input(message) for message in messages]
        self._cache_stats = CacheStats(
            total_messages=len(self._entries),
            cached_messages=count_context_prefix_messages(self._entries),
            last_cache_hit=False,
        )

    def get_message_count(self) -> int:
        return len(self._entries)

    def get_cache_stats(self) -> CacheStats:
        return replace(self._cache_stats)

    def set_cache_hit(self, tokens: Optional[int] = None) -> None:
        self._cache_stats.last_cache_hit = True
        self._cache_stats.cache_read_tokens = tokens
        # Mark all messages as potentially cached
        self._cache_stats.cached_messages = len(self._entries)

    def set_cache_miss(self) -> None:
        self._cache_stats.last_cache_hit = False
        self._cache_stats.cache_read_tokens = None
        self._cache_stats.cached_messages = count_context_prefix_messages(self._entries)

    def reset(self) -> None:
        context_prefix_messages = self._entries[
            : count_context_prefix_messages(self._entries)
        ]
        self._entries = [clone_runtime_message_entry(entry) for entry in context_prefix_messages]
        self._cache_stats = CacheStats(
            total_messages=len(context_prefix_messages),
            cached_messages=len(context_prefix_messages),
            last_cache_hit=False,
        )


def count_context_prefix_messages(
    messages_or_entries: Sequence[Union[ModelInputMessage, RuntimeMessageEntry]],
) -> int:
    """Mirrors ``countContextPrefixMessages(messagesOrEntries)``."""
    count = 0
    for item in messages_or_entries:
        if is_runtime_attachment_entry(item):
            if item.metadata.source == "context_prefix" or item.metadata.source == "skills_listing":
                count += 1
                continue
            break
        message = message_from_entry_input(item)
        metadata = metadata_from_entry_input(item)
        if message.role == "system":
            count += 1
            continue
        if message.role != "user":
            break
        if metadata is not None:
            if metadata.source == "context_prefix" or metadata.source == "skills_listing":
                count += 1
                continue
            break
        if is_meta_user_context(message.content):
            count += 1
            continue
        break
    return count


def is_meta_user_context(content: ModelMessageContent) -> bool:
    """Mirrors ``isMetaUserContext(content)``."""
    return model_message_content_to_text(content).lstrip().startswith("<system-reminder>")


def system_reminder_runtime_metadata(source: SystemReminderSource) -> RuntimeMessageMetadata:
    """Mirrors ``systemReminderRuntimeMetadata(source)``."""
    return RuntimeMessageMetadata(source=source)


def real_user_runtime_metadata() -> RuntimeMessageMetadata:
    """Mirrors ``realUserRuntimeMetadata()``."""
    return RuntimeMessageMetadata(source="real_user")


def legacy_synthetic_runtime_metadata() -> RuntimeMessageMetadata:
    """Mirrors ``legacySyntheticRuntimeMetadata()``."""
    return RuntimeMessageMetadata(source="legacy_synthetic")


def todo_reminder_runtime_metadata() -> RuntimeMessageMetadata:
    """Mirrors ``todoReminderRuntimeMetadata()``."""
    return RuntimeMessageMetadata(source="todo_reminder")


def system_reminder_attachment_entry(
    source: SystemReminderSource, content: str
) -> RuntimeAttachmentEntry:
    """Mirrors ``systemReminderAttachmentEntry(source, content)``."""
    return RuntimeAttachmentEntry(
        kind="attachment",
        content=content,
        metadata=system_reminder_runtime_metadata(source),
    )


def create_runtime_user_entry(
    content: ModelMessageContent, metadata: Optional[RuntimeMessageMetadata] = None
) -> RuntimeMessageMessageEntry:
    """Mirrors ``createRuntimeUserEntry(content, metadata?)``."""
    return RuntimeMessageMessageEntry(
        message=ModelInputMessage(
            role="user",
            content=content,
        ),
        metadata=clone_runtime_message_metadata(metadata),
    )


def create_runtime_assistant_entry(
    content: str,
    tool_calls: Optional[Sequence[ToolCallInput]] = None,
    reasoning: Optional[Sequence[ReasoningContentInput]] = None,
    model: Optional[ModelIdentity] = None,
    tokens: Optional[TokenUsageInfo] = None,
) -> RuntimeMessageMessageEntry:
    """Mirrors ``createRuntimeAssistantEntry(...)``."""
    reasoning_blocks = (
        [clone_reasoning_block(block) for block in reasoning] if reasoning is not None else []
    )
    entry = RuntimeMessageMessageEntry(
        message=ModelInputMessage(
            role="assistant",
            content=(
                [
                    *reasoning_blocks,
                    *([ModelTextContentBlock(text=content)] if len(content) > 0 else []),
                ]
                if len(reasoning_blocks) > 0
                else content
            ),
            tool_calls=(
                [
                    ToolCallInput(id=tool_call.id, name=tool_call.name, input=tool_call.input)
                    for tool_call in tool_calls
                ]
                if tool_calls is not None
                else None
            ),
            provider_id=model.provider_id if model is not None else None,
            model_id=model.model_id if model is not None else None,
        )
    )
    if tokens is not None:
        entry.tokens = clone_token_usage_info(tokens)
    return entry


def create_runtime_tool_result_entry(
    tool_call_id: str,
    tool_name: str,
    content: ModelMessageContent,
    is_error: bool,
) -> RuntimeMessageMessageEntry:
    """Mirrors ``createRuntimeToolResultEntry(...)``."""
    return RuntimeMessageMessageEntry(
        message=ModelInputMessage(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            is_error=is_error,
        )
    )


def is_known_system_reminder_source(value: Any) -> bool:
    """Mirrors ``isKnownSystemReminderSource(value)``.

    # PORT: predicate form of the TS type guard remains; callers should narrow with
    # ``cast(SystemReminderSource, value)`` where the TS compiler used the guard.
    """
    return isinstance(value, str) and value in SYSTEM_REMINDER_SOURCES


def clone_entry_input(
    input: Union[ModelInputMessage, RuntimeMessageEntry],
) -> RuntimeMessageEntry:
    """Mirrors ``cloneEntryInput(input)`` (module-private upstream)."""
    if is_runtime_message_entry(input):
        return clone_runtime_message_entry(input)
    return RuntimeMessageMessageEntry(message=clone_model_input_message(input))


def clone_runtime_message_entry(entry: RuntimeMessageEntry) -> RuntimeMessageEntry:
    """Mirrors ``cloneRuntimeMessageEntry(entry)``."""
    if entry.kind == "attachment":
        return RuntimeAttachmentEntry(
            kind="attachment",
            content=entry.content,
            cache_control=(
                replace(entry.cache_control) if entry.cache_control is not None else None
            ),
            metadata=clone_runtime_message_metadata(entry.metadata),
        )
    return RuntimeMessageMessageEntry(
        message=clone_model_input_message(entry.message),
        metadata=clone_runtime_message_metadata(entry.metadata),
        tokens=clone_token_usage_info(entry.tokens) if entry.tokens is not None else None,
        query_scope=entry.query_scope,
    )


def invalidate_runtime_token_usage(tokens: TokenUsageInfo) -> TokenUsageInfo:
    """Compact 之后 preserved assistant 的 provider usage 仍属于被替换的旧前缀。
    只对 projection 副本清零，不能改写 SessionStore 中的原始 tokens。

    Mirrors ``invalidateRuntimeTokenUsage(tokens)``.
    """
    return replace(
        tokens,
        total=0,
        input=0,
        output=0,
        reasoning=0,
        cache=TokenUsageCache(
            read=0,
            write=0,
        ),
    )


def clone_token_usage_info(tokens: TokenUsageInfo) -> TokenUsageInfo:
    """Mirrors ``cloneTokenUsageInfo(tokens)`` (module-private upstream)."""
    return replace(tokens, cache=replace(tokens.cache))


def clone_runtime_message_metadata(
    metadata: Optional[RuntimeMessageMetadata],
) -> Optional[RuntimeMessageMetadata]:
    """Mirrors ``cloneRuntimeMessageMetadata(metadata)`` (module-private upstream)."""
    return replace(metadata) if metadata is not None else None


def message_from_entry_input(
    input: Union[ModelInputMessage, RuntimeMessageEntry],
) -> ModelInputMessage:
    """Mirrors ``messageFromEntryInput(input)`` (module-private upstream)."""
    if is_runtime_attachment_entry(input):
        # PORT: TS ``new Error(...)`` → built-in ``RuntimeError``.
        raise RuntimeError(
            "Runtime attachment entries do not have a direct model message representation"
        )
    return input.message if is_runtime_message_entry(input) else input


def metadata_from_entry_input(
    input: Union[ModelInputMessage, RuntimeMessageEntry],
) -> Optional[RuntimeMessageMetadata]:
    """Mirrors ``metadataFromEntryInput(input)`` (module-private upstream)."""
    return input.metadata if is_runtime_message_entry(input) else None


def is_runtime_message_entry(input: Union[ModelInputMessage, RuntimeMessageEntry]) -> bool:
    """Mirrors ``isRuntimeMessageEntry(input)`` (module-private upstream, a type guard).

    # PORT: TS tests structurally — ``"message" in input || ("kind" in input &&
    # input.kind === "attachment")``; over the two dataclasses that is an isinstance test.
    """
    return isinstance(input, (RuntimeMessageMessageEntry, RuntimeAttachmentEntry))


def is_runtime_attachment_entry(input: Union[ModelInputMessage, RuntimeMessageEntry]) -> bool:
    """Mirrors ``isRuntimeAttachmentEntry(input)`` (a type guard upstream)."""
    return is_runtime_message_entry(input) and input.kind == "attachment"


def clone_model_input_message(message: ModelInputMessage) -> ModelInputMessage:
    """Mirrors ``cloneModelInputMessage(message)``."""
    next_message = ModelInputMessage(
        role=message.role,
        content=clone_model_message_content(message.content),
    )
    if message.cache_control is not None:
        next_message.cache_control = replace(message.cache_control)
    if message.tool_calls is not None:
        next_message.tool_calls = [replace(tool_call) for tool_call in message.tool_calls]
    if message.tool_call_id:
        next_message.tool_call_id = message.tool_call_id
    # 空字符串是可恢复调用的 provider 原始名称，不能在 request-local clone 时按 falsy 丢失。
    if message.tool_name is not None:
        next_message.tool_name = message.tool_name
    if message.is_error is not None:
        next_message.is_error = message.is_error
    if message.provider_id:
        next_message.provider_id = message.provider_id
    if message.model_id:
        next_message.model_id = message.model_id
    return next_message


def clone_reasoning_block(block: ReasoningContentInput) -> ReasoningContentInput:
    """Mirrors ``cloneReasoningBlock(block)`` (module-private upstream)."""
    return replace(
        block,
        provider_options=(
            replace(block.provider_options) if block.provider_options is not None else None
        ),
    )


def clone_model_message_content(content: ModelMessageContent) -> ModelMessageContent:
    """Mirrors ``cloneModelMessageContent(content)``."""
    if isinstance(content, str):
        return content
    return [clone_model_message_content_block(block) for block in content]


def clone_model_message_content_block(
    block: ModelMessageContentBlock,
) -> ModelMessageContentBlock:
    """Mirrors ``cloneModelMessageContentBlock(block)`` (module-private upstream)."""
    if getattr(block, "type", None) == "reasoning":
        return clone_reasoning_block(block)
    # PORT: TS ``"source" in block && block.source`` — only image/video/file carry a source.
    source = getattr(block, "source", None)
    if source:
        return replace(block, source=replace(source))
    return replace(block)


# ============================================================
# Factory
# ============================================================


def create_message_history() -> MessageHistory:
    """Mirrors ``createMessageHistory()``."""
    return MessageHistoryImpl()
