"""System Reminder Source — source registry, descriptors and wrapping rules.

Ported from: packages/core/src/system-reminder/source.ts
Original package: @zcode/core (system-reminder)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Union

__all__ = [
    "SystemReminderDeliveryChannel",
    "SystemReminderLifecycle",
    "SystemReminderProviderVisibility",
    "SYSTEM_REMINDER_PREFIX_SOURCES",
    "SYSTEM_REMINDER_PERSISTED_SOURCES",
    "SYSTEM_REMINDER_PER_REQUEST_SOURCES",
    "SystemReminderPrefixSource",
    "SystemReminderPersistedSource",
    "SystemReminderPerRequestSource",
    "SystemReminderSource",
    "SystemReminderDescriptorShape",
    "SystemReminderSourceDescriptor",
    "SYSTEM_REMINDER_SOURCES",
    "get_system_reminder_descriptor",
    "is_mid_conversation_system_source",
    "wrap_system_reminder_for_source",
    "wrap_system_reminder",
    "sanitize_system_reminder_body",
]

# PORT: TS 是字符串字面量联合类型；Python 用 ``Literal``（见 ZCORE_PORTING.md §1）。
SystemReminderDeliveryChannel = Literal[
    "request_prefix",
    "current_turn",
    "tool_result",
    "history_continuity",
    "mid_turn_event",
    "real_user",
]

SystemReminderLifecycle = Literal[
    "request_prefix",
    "per_current_turn",
    "runtime_local",
    "tool_result",
    "resume_history",
    "mid_turn_event",
    "real_user",
]

SystemReminderProviderVisibility = Literal["provider_visible", "provider_hidden"]

# PORT: TS ``export const X = [...] as const`` → 模块级 ``tuple`` 常量，
# 元素字符串保持原值（协议兼容）。
SYSTEM_REMINDER_PREFIX_SOURCES: tuple[str, ...] = ("context_prefix", "skills_listing")

SYSTEM_REMINDER_PERSISTED_SOURCES: tuple[str, ...] = (
    "todo_reminder",
    "task_status",
    "tool_result_warning",
    "resume_referenced_session_context",
    "plan_file_reference",
    "resume_goal_state",
    "goal_state_change",
    "plugin_reference",
    "target_continuation",
    "goal_completion_verification",
    "rewind_notice",
    "conversation_fork",
    "selection_side_chat",
    "queued_system_notification",
    "shell_environment_change",
)

SYSTEM_REMINDER_PER_REQUEST_SOURCES: tuple[str, ...] = (
    "incoming_message",
    "hook_context",
    "runtime_mode",
    "plan_mode_exit",
    "output_style",
    "date_change",
    "referenced_session_context",
    "model_anomaly",
    "prompt_attachment",
    "diagnostics",
)

SystemReminderPrefixSource = Literal["context_prefix", "skills_listing"]
SystemReminderPersistedSource = Literal[
    "todo_reminder",
    "task_status",
    "tool_result_warning",
    "resume_referenced_session_context",
    "plan_file_reference",
    "resume_goal_state",
    "goal_state_change",
    "plugin_reference",
    "target_continuation",
    "goal_completion_verification",
    "rewind_notice",
    "conversation_fork",
    "selection_side_chat",
    "queued_system_notification",
    "shell_environment_change",
]
SystemReminderPerRequestSource = Literal[
    "incoming_message",
    "hook_context",
    "runtime_mode",
    "plan_mode_exit",
    "output_style",
    "date_change",
    "referenced_session_context",
    "model_anomaly",
    "prompt_attachment",
    "diagnostics",
]

SystemReminderSource = Union[
    SystemReminderPrefixSource,
    SystemReminderPersistedSource,
    SystemReminderPerRequestSource,
]


@dataclass(slots=True)
class SystemReminderDescriptorShape:
    """Mirrors ``type DescriptorShape = Omit<SystemReminderSourceDescriptor, "source">``.

    # PORT: TS ``Omit`` 无法在 Python 类型层表达，故把去掉 ``source`` 的形状单独
    # 落成一个 dataclass（字段顺序与上游一致）。
    """

    channel: SystemReminderDeliveryChannel
    lifecycle: SystemReminderLifecycle
    is_meta: bool
    provider_visibility: SystemReminderProviderVisibility
    evidence_label: str


@dataclass(slots=True)
class SystemReminderSourceDescriptor(SystemReminderDescriptorShape):
    """Mirrors ``interface SystemReminderSourceDescriptor``."""

    source: SystemReminderSource


# PORT: TS 正则字面量 ``/<\/?system-reminder\b/i`` 与 ``/<\/?system-reminder\b/gi``
# 直接照搬：``\b`` 与 ``re.IGNORECASE`` 语义一致；替换是全量（``subn``）。
SYSTEM_REMINDER_TAG_PATTERN = re.compile(r"</?system-reminder\b", re.IGNORECASE)
SYSTEM_REMINDER_NESTED_TAG_PATTERN = re.compile(r"</?system-reminder\b", re.IGNORECASE)

# PORT: TS ``new Set<SystemReminderSource>([...])`` → ``frozenset``。
SYSTEM_REMINDER_TRAILING_NEWLINE_SOURCES: frozenset[SystemReminderSource] = frozenset(
    {"context_prefix"}
)

NON_MID_CONVERSATION_SYSTEM_SOURCES: frozenset[SystemReminderSource] = frozenset(
    {
        "context_prefix",
        "resume_referenced_session_context",
        # Fork 和辅助对话边界必须位于新问题之前；走 MCS 会被移到问题之后，改变上下文顺序。
        "conversation_fork",
        "selection_side_chat",
        "plan_file_reference",
        "target_continuation",
        "tool_result_warning",
        "goal_completion_verification",
    }
)


def _descriptor(
    channel: SystemReminderDeliveryChannel,
    lifecycle: SystemReminderLifecycle,
    is_meta: bool,
    evidence_label: str,
) -> SystemReminderDescriptorShape:
    """Mirrors ``function descriptor(channel, lifecycle, isMeta, evidenceLabel)``.

    # PORT: TS 中 ``descriptor`` 是函数声明（存在提升），可以写在
    # ``SYSTEM_REMINDER_DESCRIPTORS`` 字面量之后；Python 需先定义再求值 dict 字面量，
    # 故此处前置到描述表之前。
    """
    return SystemReminderDescriptorShape(
        channel=channel,
        lifecycle=lifecycle,
        is_meta=is_meta,
        provider_visibility="provider_visible",
        evidence_label=evidence_label,
    )


SYSTEM_REMINDER_DESCRIPTORS: dict[SystemReminderSource, SystemReminderDescriptorShape] = {
    "incoming_message": _descriptor(
        "mid_turn_event", "mid_turn_event", True, "sr.incoming_message"
    ),
    "context_prefix": _descriptor("request_prefix", "request_prefix", True, "sr.context_prefix"),
    "skills_listing": _descriptor("request_prefix", "request_prefix", True, "sr.skills_listing"),
    "hook_context": _descriptor("current_turn", "per_current_turn", True, "sr.hook_context"),
    "runtime_mode": _descriptor("current_turn", "per_current_turn", True, "sr.runtime_mode"),
    "plan_mode_exit": _descriptor("current_turn", "runtime_local", True, "sr.plan_mode_exit"),
    "output_style": _descriptor("current_turn", "per_current_turn", True, "sr.output_style"),
    "date_change": _descriptor("current_turn", "runtime_local", True, "sr.date_change"),
    "referenced_session_context": _descriptor(
        "current_turn",
        "per_current_turn",
        True,
        "sr.referenced_session_context",
    ),
    # Plugin 对话引用：当轮生成后按
    # model-only synthetic notice 固化，后续只追加、不改写；冷恢复按原文重建以保持缓存前缀。
    "plugin_reference": _descriptor("current_turn", "per_current_turn", True, "sr.plugin_reference"),
    "todo_reminder": _descriptor("current_turn", "per_current_turn", True, "sr.todo_reminder"),
    "task_status": _descriptor("mid_turn_event", "mid_turn_event", True, "sr.task_status"),
    # 只用于 Read 等 tool result 内容内联 warning，不作为 synthetic user notice 持久化。
    "tool_result_warning": _descriptor(
        "tool_result", "tool_result", True, "sr.tool_result_warning"
    ),
    "resume_referenced_session_context": _descriptor(
        "history_continuity",
        "resume_history",
        True,
        "sr.resume_referenced_session_context",
    ),
    "plan_file_reference": _descriptor(
        "history_continuity",
        "resume_history",
        True,
        "sr.plan_file_reference",
    ),
    "resume_goal_state": _descriptor(
        "history_continuity",
        "resume_history",
        True,
        "sr.resume_goal_state",
    ),
    "goal_state_change": _descriptor(
        "mid_turn_event", "mid_turn_event", True, "sr.goal_state_change"
    ),
    "target_continuation": _descriptor("real_user", "real_user", False, "sr.target_continuation"),
    "goal_completion_verification": _descriptor(
        "tool_result",
        "tool_result",
        True,
        "sr.goal_completion_verification",
    ),
    "model_anomaly": _descriptor("mid_turn_event", "mid_turn_event", True, "sr.model_anomaly"),
    "rewind_notice": _descriptor("history_continuity", "resume_history", True, "sr.rewind_notice"),
    "conversation_fork": _descriptor(
        "history_continuity",
        "resume_history",
        True,
        "sr.conversation_fork",
    ),
    "selection_side_chat": _descriptor(
        "history_continuity",
        "resume_history",
        True,
        "sr.selection_side_chat",
    ),
    "prompt_attachment": _descriptor(
        "current_turn", "per_current_turn", True, "sr.prompt_attachment"
    ),
    "queued_system_notification": _descriptor(
        "mid_turn_event",
        "mid_turn_event",
        True,
        "sr.queued_system_notification",
    ),
    "shell_environment_change": _descriptor(
        "mid_turn_event",
        "mid_turn_event",
        True,
        "sr.shell_environment_change",
    ),
    "diagnostics": _descriptor("mid_turn_event", "mid_turn_event", True, "sr.diagnostics"),
}

# PORT: TS ``Object.freeze([...])`` → ``tuple``（不可变、保序）。
SYSTEM_REMINDER_SOURCES: tuple[SystemReminderSource, ...] = (
    *SYSTEM_REMINDER_PREFIX_SOURCES,
    *SYSTEM_REMINDER_PERSISTED_SOURCES,
    *SYSTEM_REMINDER_PER_REQUEST_SOURCES,
)

MID_CONVERSATION_SYSTEM_SOURCES: frozenset[SystemReminderSource] = frozenset(
    source for source in SYSTEM_REMINDER_SOURCES if source not in NON_MID_CONVERSATION_SYSTEM_SOURCES
)


def get_system_reminder_descriptor(source: SystemReminderSource) -> SystemReminderSourceDescriptor:
    """Mirrors ``getSystemReminderDescriptor(source)``."""
    descriptor = SYSTEM_REMINDER_DESCRIPTORS[source]
    return SystemReminderSourceDescriptor(
        source=source,
        channel=descriptor.channel,
        lifecycle=descriptor.lifecycle,
        is_meta=descriptor.is_meta,
        provider_visibility=descriptor.provider_visibility,
        evidence_label=descriptor.evidence_label,
    )


def is_mid_conversation_system_source(source: SystemReminderSource) -> bool:
    """Mirrors ``isMidConversationSystemSource(source)``."""
    return source in MID_CONVERSATION_SYSTEM_SOURCES


def wrap_system_reminder_for_source(
    source: SystemReminderSource,
    body: Union[str, list[str], tuple[str, ...]],
) -> str:
    """Mirrors ``wrapSystemReminderForSource(source, body)``."""
    source_descriptor = get_system_reminder_descriptor(source)
    if source_descriptor.provider_visibility != "provider_visible":
        # PORT: TS ``new Error(...)`` → Python 内置 ``RuntimeError``。
        raise RuntimeError(f"System reminder source {source} is not provider-visible")
    wrapped = wrap_system_reminder(sanitize_system_reminder_body(body))

    return f"{wrapped}\n" if source in SYSTEM_REMINDER_TRAILING_NEWLINE_SOURCES else wrapped


def wrap_system_reminder(body: Union[str, list[str], tuple[str, ...]]) -> str:
    """Mirrors ``wrapSystemReminder(body)``."""
    content = body if isinstance(body, str) else "\n".join(body)
    if len(content) == 0:
        raise RuntimeError("System reminder body cannot be empty")
    if SYSTEM_REMINDER_TAG_PATTERN.search(content) is not None:
        raise RuntimeError("System reminder body must not include nested system-reminder tags")
    return "\n".join(["<system-reminder>", content, "</system-reminder>"])


def sanitize_system_reminder_body(body: Union[str, list[str], tuple[str, ...]]) -> str:
    """Mirrors ``sanitizeSystemReminderBody(body)``."""
    return _escape_nested_system_reminder_tags(body)


def _escape_nested_system_reminder_tags(body: Union[str, list[str], tuple[str, ...]]) -> str:
    """Mirrors ``escapeNestedSystemReminderTags(body)`` (module-private upstream)."""
    content = body if isinstance(body, str) else "\n".join(body)
    # 与 wrapper 的拒绝规则一致，含空格/大小写变体的关闭标签也只中和起始 <。
    return SYSTEM_REMINDER_NESTED_TAG_PATTERN.sub(lambda match: f"&lt;{match.group(0)[1:]}", content)
