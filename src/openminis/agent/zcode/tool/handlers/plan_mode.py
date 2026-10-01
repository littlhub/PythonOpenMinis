"""Plan mode tool handlers — enter/exit plan mode.

Ported from: packages/core/src/tool/handlers/plan-mode.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error, is_core_error
from ..types import ToolEntry, ToolHandler
from .plan_mode_prompts import (
    ENTER_PLAN_MODE_PROVIDER_DESCRIPTION,
    EXIT_PLAN_MODE_MODEL_INSTRUCTIONS,
    create_enter_plan_mode_provider_description,
)
from .ports import (
    ENTER_PLAN_MODE_INPUT_JSON_SCHEMA,
    ENTER_PLAN_MODE_OUTPUT_JSON_SCHEMA,
    ENTER_PLAN_MODE_TOOL_NAME,
    EXIT_PLAN_MODE_INPUT_JSON_SCHEMA,
    EXIT_PLAN_MODE_OUTPUT_JSON_SCHEMA,
    EXIT_PLAN_MODE_TOOL_NAME,
)

__all__ = [
    "enter_plan_mode_handler",
    "exit_plan_mode_handler",
    "enter_plan_mode_tool_entry",
    "exit_plan_mode_tool_entry",
    "create_enter_plan_mode_tool_entry",
    "write_approved_plan_file",
]

MAX_PLAN_MODE_MODEL_BYTES = 100_000

EXIT_PLAN_MODE_DESCRIPTION = EXIT_PLAN_MODE_MODEL_INSTRUCTIONS[0]


def _transition_field(transition: Any, name: str) -> Any:
    """从端口返回的转换结果里读一个字段。

    端口是**宿主注入**的，契约上是 ``EnterPlanModeTransitionResult`` 这类**对象**
    （见 ``contracts/session_mode.py``，对应上游 TS 的 ``{ mode, previousMode, … }``
    对象字面量）。但 TS 的「对象字面量」在 Python 里既可以落成 dataclass，也可以
    落成 ``dict`` —— 本 handler 原先只按 ``dict`` 读（``transition.get("mode")``），
    于是任何一个按契约返回对象的端口实现都会在这里炸
    ``AttributeError: '…Result' object has no attribute 'get'``。

    两种形状都接受，字段名同时认 snake_case 与 camelCase。端口缺失（``None``）时
    返回 ``None``，与 TS 里读一个不存在的属性同义。
    """
    if transition is None:
        return None
    if isinstance(transition, dict):
        if name in transition:
            return transition[name]
        camel = _to_camel(name)
        return transition.get(camel)
    if hasattr(transition, name):
        return getattr(transition, name)
    camel = _to_camel(name)
    return getattr(transition, camel, None)


def _to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


# ---------------------------------------------------------------------------
# 已移植的 plan-file-continuity 落点（runtime/helpers/plan-file-continuity.ts）
# ---------------------------------------------------------------------------
# PORT: ``writeApprovedPlanFile`` 来自 ``packages/core/src/runtime/helpers/
# plan-file-continuity.ts``，本移植集未单独移植该 runtime helper；这里就地还原其
# 等价实现（原子写 + 路径 sanitize），保持 ExitPlanMode 的持久化语义。读侧
# ``readApprovedPlanFileReferenceEntry`` 不在 plan-mode handler 调用链上，省略。

PLAN_FILE_REFERENCE_MAX_BYTES = 20_000 * 4 + 1024  # PLAN_MODE_MAX_PLAN_CHARS * 4 + 1024


def _sanitize_plan_file_session_id(session_id: str) -> str:
    """Mirrors ``sanitizePlanFileSessionId(sessionId)``."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(session_id).strip()).strip("-")
    if not sanitized:
        raise create_core_error(
            CoreErrorType.INVALID_INPUT,
            "Session id cannot produce a plan file name",
            {"recoverable": False},
        )
    return sanitized


def _resolve_approved_plan_file_path(session_id: str, workspace_root: str) -> str:
    """Mirrors ``resolveApprovedPlanFilePath(input)``."""
    return str(
        Path(workspace_root)
        / ".zcode"
        / "plans"
        / f"plan-{_sanitize_plan_file_session_id(session_id)}.md"
    )


async def write_approved_plan_file(input: dict[str, Any]) -> dict[str, str]:
    """Mirrors ``writeApprovedPlanFile(input)`` (plan-file-continuity.ts)."""
    plan: str = input.get("plan", "")
    if not plan.strip():
        raise create_core_error(
            CoreErrorType.INVALID_INPUT,
            "ExitPlanMode plan cannot be empty",
            {"recoverable": True},
        )

    path = _resolve_approved_plan_file_path(input["session_id"], input["workspace_root"])
    await input["file_system_port"].write_text_file(
        {
            "atomic": True,
            "content": plan,
            "create_parents": True,
            "encoding": "utf8",
            "path": path,
            "trace": input.get("trace_context"),
        },
        {"signal": input.get("abort_signal")},
    )
    return {"path": path}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def enter_plan_mode_handler(input: Any, context: Any) -> Any:
    """Mirrors ``enterPlanModeHandler: ToolHandler``."""
    # PORT: ``EnterPlanModeInputSchema.parse(input)`` —— executor 已归一化，透传。
    _assert_session_mode_port(context, ENTER_PLAN_MODE_TOOL_NAME)

    transition = await context.session_mode_port.enter_plan_mode(
        {
            "tool_call_id": context.tool_call_id,
            "trace_context": {
                "trace_id": context.trace_id,
                "span_id": context.span_id,
                "parent_span_id": context.parent_span_id,
                "turn_id": context.turn_id,
            },
        }
    )

    return {
        "message": "Entered plan mode. You should now focus on exploring the codebase and designing an implementation approach.",
        "mode": _transition_field(transition, "mode"),
        "previous_mode": _transition_field(transition, "previous_mode"),
        "plan_enabled": _transition_field(transition, "plan_enabled"),
        "previous_plan_enabled": _transition_field(transition, "previous_plan_enabled"),
    }


async def exit_plan_mode_handler(input: Any, context: Any) -> Any:
    """Mirrors ``exitPlanModeHandler: ToolHandler``."""
    parsed: dict[str, Any] = input
    _assert_session_mode_port(context, EXIT_PLAN_MODE_TOOL_NAME)

    session_mode_port = context.session_mode_port
    is_plan_enabled = (
        session_mode_port.is_plan_enabled()
        if getattr(session_mode_port, "is_plan_enabled", None) is not None
        else session_mode_port.get_mode() == "plan"
    )
    if not is_plan_enabled:
        raise create_core_error(
            CoreErrorType.INVALID_STATE_TRANSITION,
            "You are not in plan mode. This tool is only for exiting plan mode after writing a plan. If your plan was already approved, continue with implementation.",
            {
                "context": {
                    "tool_call_id": context.tool_call_id,
                    "tool_name": EXIT_PLAN_MODE_TOOL_NAME,
                },
                "recoverable": True,
            },
        )

    await _persist_approved_plan_file_before_exit_plan_mode(
        {
            "context": context,
            "plan": parsed.get("plan", ""),
        }
    )

    transition = await session_mode_port.exit_plan_mode(
        {
            "tool_call_id": context.tool_call_id,
            "trace_context": {
                "trace_id": context.trace_id,
                "span_id": context.span_id,
                "parent_span_id": context.parent_span_id,
                "turn_id": context.turn_id,
            },
        }
    )

    return {
        "allowed_prompts": parsed.get("allowed_prompts"),
        "approved": True,
        "plan_enabled": _transition_field(transition, "plan_enabled"),
        "previous_plan_enabled": _transition_field(transition, "previous_plan_enabled"),
        "mode": _transition_field(transition, "mode"),
        "plan": parsed.get("plan", ""),
        "previous_mode": _transition_field(transition, "previous_mode"),
    }


def _assert_session_mode_port(context: Any, tool_name: str) -> None:
    """Mirrors ``assertSessionModePort(context, toolName)``."""
    if context.session_mode_port:
        return

    raise create_core_error(
        CoreErrorType.CONFIGURATION_ERROR,
        f"SessionModePort is not configured for {tool_name}",
        {
            "context": {
                "tool_call_id": context.tool_call_id,
                "tool_name": tool_name,
            },
            "recoverable": False,
        },
    )


async def _persist_approved_plan_file_before_exit_plan_mode(input: dict[str, Any]) -> None:
    """Mirrors ``persistApprovedPlanFileBeforeExitPlanMode(input)``."""
    context = input["context"]
    if not context.file_system_port:
        return

    try:
        await write_approved_plan_file(
            {
                "abort_signal": context.abort_signal,
                "file_system_port": context.file_system_port,
                "plan": input["plan"],
                "session_id": context.session_id,
                "trace_context": _create_plan_mode_tool_trace_context(context),
                "workspace_root": context.workspace_root,
            }
        )
    except Exception as error:
        if _is_plan_file_persistence_cancellation(error, context.abort_signal):
            raise create_core_error(
                CoreErrorType.TOOL_CANCELLED,
                "ExitPlanMode was cancelled before plan mode was exited",
                {
                    "cause": error if isinstance(error, BaseException) else None,
                    "context": {
                        "tool_call_id": context.tool_call_id,
                        "tool_name": EXIT_PLAN_MODE_TOOL_NAME,
                    },
                    "recoverable": True,
                },
            )


def _create_plan_mode_tool_trace_context(context: Any) -> dict[str, Any]:
    """Mirrors ``createPlanModeToolTraceContext(context)``."""
    return {
        "trace_id": context.trace_id,
        "span_id": context.span_id,
        "parent_span_id": context.parent_span_id,
        "turn_id": context.turn_id,
    }


def _is_plan_file_persistence_cancellation(error: Any, abort_signal: Any) -> bool:
    """Mirrors ``isPlanFilePersistenceCancellation(error, abortSignal)``.

    # PORT: 原 ``isFileSystemPortError(error) && error.code === "cancelled"`` 依赖
    # @zcode/contracts 的 FileSystemPortError（未移植）；以 is_core_error + code 等价判别。
    """
    return abort_signal.aborted or (
        is_core_error(error) and getattr(error, "code", None) == "cancelled"
    )


def format_enter_plan_mode_model_content(output: Any) -> str:
    """Mirrors ``formatEnterPlanModeModelContent(output)``."""
    result = output
    return f"""{result.get("message", "")}

In plan mode, you should:
1. Thoroughly explore the codebase to understand existing patterns
2. Identify similar features and architectural approaches
3. Consider multiple approaches and their trade-offs
4. Use AskUserQuestion if you need to clarify the approach
5. Design a concrete implementation strategy
6. When ready, use ExitPlanMode to present your plan for approval

Remember: DO NOT write or edit any files yet. This is a read-only exploration and planning phase."""


def format_exit_plan_mode_model_content(output: Any) -> str:
    """Mirrors ``formatExitPlanModeModelContent(output)``."""
    result = output
    plan = (result.get("plan") or "").strip()
    if not plan:
        return "User has approved exiting plan mode. You can now proceed."

    return f"""User has approved your plan. You can now start coding. Start with updating your todo list if applicable.

## Approved Plan:
{plan}"""


# ---------------------------------------------------------------------------
# ToolEntry 构造
# ---------------------------------------------------------------------------

def _plan_mode_permission(permission: str, reason: str, needs_approval: bool = True) -> Any:
    from ...contracts.tools import ToolPermissionSpec

    return ToolPermissionSpec(
        permission=permission,
        reason=reason,
        risk_level="low",
        side_effect_scope="session",
        needs_approval=needs_approval,
        pattern_sources=["tool_name", "input"],
        always_allow_pattern_sources=["tool_name"],
        deny_priority="beforeAsk",
    )


def _plan_mode_result_budget() -> Any:
    from ...contracts.tools import ToolResultBudget, ToolResultBudgetPreview

    return ToolResultBudget(
        max_inline_bytes=MAX_PLAN_MODE_MODEL_BYTES,
        max_model_bytes=MAX_PLAN_MODE_MODEL_BYTES,
        strategy="truncate",
        preview=ToolResultBudgetPreview(max_bytes=MAX_PLAN_MODE_MODEL_BYTES, direction="head"),
    )


def _plan_mode_timeout() -> Any:
    from ...contracts.tools import TimedToolTimeoutPolicy

    return TimedToolTimeoutPolicy(default_ms=30000, max_ms=30000, allow_call_override=False)


def _plan_mode_trace() -> Any:
    from ...contracts.tools import ToolTracePolicy

    return ToolTracePolicy(
        propagate_to_adapters=False,
        record_input="summary",
        record_output="summary",
    )


def _build_enter_plan_mode_tool_entry() -> ToolEntry:
    from ...contracts.tools import ToolCancellationPolicy

    return ToolEntry(
        capability="Enter read-only planning mode before implementation",
        requires_user_interaction=False,
        metadata={
            "name": ENTER_PLAN_MODE_TOOL_NAME,
            "description": ENTER_PLAN_MODE_PROVIDER_DESCRIPTION,
            "read_only": False,
            "destructive": False,
            "concurrent_safe": False,
            "requires_user_interaction": False,
            "timeout_ms": 30000,
            "max_output_bytes": MAX_PLAN_MODE_MODEL_BYTES,
            "side_effect_scope": "session",
            "risk_level": "low",
            "needs_approval": False,
        },
        handler=enter_plan_mode_handler,
        format_model_content=format_enter_plan_mode_model_content,
        input_schema=ENTER_PLAN_MODE_INPUT_JSON_SCHEMA,
        output_schema=ENTER_PLAN_MODE_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=ENTER_PLAN_MODE_INPUT_JSON_SCHEMA,
        runtime_output_schema=ENTER_PLAN_MODE_OUTPUT_JSON_SCHEMA,
        permission=_plan_mode_permission("plan.enter", "EnterPlanMode changes session mode to plan", False),
        result_budget=_plan_mode_result_budget(),
        timeout=_plan_mode_timeout(),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="EnterPlanMode was cancelled before plan mode was entered",
        ),
        trace=_plan_mode_trace(),
    )


def _build_exit_plan_mode_tool_entry() -> ToolEntry:
    from ...contracts.tools import ToolCancellationPolicy

    return ToolEntry(
        capability="Request user approval for the plan and exit planning mode before coding",
        requires_user_interaction=True,
        metadata={
            "name": EXIT_PLAN_MODE_TOOL_NAME,
            "description": EXIT_PLAN_MODE_DESCRIPTION,
            "read_only": False,
            "destructive": False,
            "concurrent_safe": False,
            "requires_user_interaction": True,
            "timeout_ms": 30000,
            "max_output_bytes": MAX_PLAN_MODE_MODEL_BYTES,
            "side_effect_scope": "session",
            "risk_level": "low",
            "needs_approval": True,
        },
        handler=exit_plan_mode_handler,
        format_model_content=format_exit_plan_mode_model_content,
        input_schema=EXIT_PLAN_MODE_INPUT_JSON_SCHEMA,
        output_schema=EXIT_PLAN_MODE_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=EXIT_PLAN_MODE_INPUT_JSON_SCHEMA,
        runtime_output_schema=EXIT_PLAN_MODE_OUTPUT_JSON_SCHEMA,
        permission=_plan_mode_permission(
            "plan.exit",
            "ExitPlanMode changes session mode after user plan approval",
        ),
        result_budget=_plan_mode_result_budget(),
        timeout=_plan_mode_timeout(),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="ExitPlanMode was cancelled before plan mode was exited",
        ),
        trace=_plan_mode_trace(),
    )


enter_plan_mode_tool_entry: ToolEntry = _build_enter_plan_mode_tool_entry()
exit_plan_mode_tool_entry: ToolEntry = _build_exit_plan_mode_tool_entry()


def create_enter_plan_mode_tool_entry(options: Optional[dict[str, Any]] = None) -> ToolEntry:
    """Mirrors ``createEnterPlanModeToolEntry(options?)``."""
    options = options or {}
    entry = _build_enter_plan_mode_tool_entry()
    # PORT: TS ``{ ...entry.metadata, description }`` 展开对象字面量；本包 ``metadata`` 是
    # ``ToolMetadata`` dataclass（``ToolEntry.__post_init__`` 已归一），故用 ``replace``。
    entry.metadata = replace(
        entry.metadata,
        description=create_enter_plan_mode_provider_description(options),
    )
    return entry
