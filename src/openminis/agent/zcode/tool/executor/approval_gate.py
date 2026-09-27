"""Tool approval gate — fold a tool's prepareApproval into the ask decision.

Ported from: packages/core/src/tool/executor/approval-gate.ts
Original package: @zcode/core (tool/executor)

# PORT: ``ToolResultDisplayPayload`` / ``PermissionOptionsPolicy`` 来自 ``@zcode/contracts``（未移植），
# 保留为 ``Any``。``traceContextToLogContext`` 来自 ``@zcode/contracts``。
"""

from __future__ import annotations

from typing import Any, Optional

from ...contracts.tracing import trace_context_to_log_context  # type: ignore
from ..types import ExecutableToolCall, ToolEntry
from .types import ToolExecutorDeps

__all__ = ["resolve_tool_approval"]


class ResolvedToolApproval:
    """Mirrors ``interface ResolvedToolApproval``."""

    def __init__(
        self,
        gate: str,  # "ask" | "proceed"
        display: Any = None,
        options_policy: Any = None,
    ) -> None:
        self.gate = gate
        self.display = display
        self.options_policy = options_policy


def resolve_options_policy(
    allow_always: Any,  # false | "session" | undefined
) -> Any:  # PermissionOptionsPolicy | undefined
    # PORT: TS ``allowAlways: false | "session" | undefined``。``false`` 是布尔值，必须显式区分
    # ``undefined``（``None``）与 ``"session"`` 字符串。
    if allow_always is False:
        return "no-always-allow"
    if allow_always == "session":
        return "session-always-allow"
    return None


def resolve_tool_approval(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    entry: ToolEntry,
    execution_input: Any,
    trace_context: Any,
) -> ResolvedToolApproval:
    # ``permission`` 类型上是必填，但 executor 也会被只声明了一部分字段的 entry 驱动
    # （测试桩、动态注册的工具）。周边代码靠 spread 而不是读字段来容忍这一点，gate 同理。
    permission = getattr(entry, "permission", None)
    ask_options = permission.ask_options if permission is not None else None
    options_policy = resolve_options_policy(
        ask_options.allow_always if ask_options is not None else None
    )

    prepare_approval = getattr(entry, "prepare_approval", None)
    if prepare_approval is None:
        return ResolvedToolApproval("ask", None, options_policy)

    try:
        # 工作目录与 handler 拿到的是同一个来源（deps 的 getWorkingDirectory 在 impl.ts 里已把
        # 静态 workingDirectory 兜进去），否则预览会去看一个目录、执行会去写另一个。
        gate = prepare_approval(execution_input)
        if gate.get("gate") == "proceed":
            return ResolvedToolApproval("proceed")
        return ResolvedToolApproval("ask", gate.get("display"), options_policy)
    except Exception as error:  # noqa: BLE001
        # Bug 预防：负责生成预览的钩子绝不能决定"用户是否被询问"。向执行侧 fail-open 等于
        # 静默运行了一个未获批准的工具，所以这里 ask 照旧成立，只让预览降级。
        if deps.logger is not None:
            deps.logger.warn(
                "Tool approval preview failed; asking without a preview",
                {
                    **trace_context_to_log_context(trace_context),
                    "error": error.message if isinstance(error, Exception) and hasattr(error, "message") else str(error),
                    "event": "tool.permission.approval_preview_failed",
                    "module": "core.tool.executor",
                    "status": "failed",
                    "toolCallId": tool_call.id,
                    "toolName": tool_call.name,
                },
            )
        return ResolvedToolApproval("ask", None, options_policy)
