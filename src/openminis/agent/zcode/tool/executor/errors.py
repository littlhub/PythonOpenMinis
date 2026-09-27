"""Executor errors — tool call error result assembly.

Ported from: packages/core/src/tool/executor/errors.ts
Original package: @zcode/core (tool/executor)

# PORT-STATUS: partial
# PORT: 一处外部依赖尚未移植（不在本次范围）：
#   ``projectExecutionErrorPayload`` ← ``core/src/errors/error-payload.ts``（按模块映射直连其
#   未来落点 ``...errors.error_payload``）。该文件隶属 ``errors/`` 目录、本次未纳入移植批次，
#   此处保留调用位与投影分支，待其落地后即可直接 import。
"""

from __future__ import annotations

import math
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error, is_core_error
from ...errors.error_payload import project_execution_error_payload
from ..types import ExecutableToolCall, ToolExecutionResult, ToolExecutionError, ToolHandlerFailure
from .validation import get_initial_input_validation_model_content

__all__ = [
    "create_error_result",
    "create_tool_handler_failure_error",
    "is_tool_handler_failure",
    "is_tool_handler_failure_error",
    "create_permission_error_result",
]


@dataclass(slots=True)
class ErrorResultOptions:
    """Mirrors the inline ``options`` argument of :func:`create_error_result`.

    # PORT: TS 是匿名内联对象 ``{ preserveReasonFormatting?: boolean }``；Python 需要一个名字。
    """

    preserve_reason_formatting: Optional[bool] = None


def create_error_result(
    toolCall: ExecutableToolCall,
    error: BaseException,
    durationMs: Optional[int] = None,
    options: Optional[ErrorResultOptions] = None,
) -> ToolExecutionResult:
    """Mirrors ``createErrorResult(toolCall, error, durationMs?, options?)``."""
    handlerFailure = (
        _read_handler_failure(error)
        if is_core_error(error)
        else None
    )
    # 根因：通用错误层按 tool name 拼接 provider 文案会反向依赖具体工具。
    # handler 只返回自己的 code/message；这里统一组装 envelope，并保留裸 message 给 UI 和日志。
    modelContent = get_initial_input_validation_model_content(error)
    if modelContent is None and handlerFailure is not None:
        modelContent = f"<tool_use_error>{handlerFailure.message}</tool_use_error>"
    # subagent/turn/model 错误常把真实 provider 原因包在 cause 链里；
    # tool result 是父模型和 UI hover 的共同来源，必须在这里统一投影成可读摘要。
    projectedError = project_execution_error_payload(error)
    reasonSource = (
        _read_reason_source(error)
        if is_core_error(error)
        else None
    )
    # ExitPlanMode / workflow Refine 的用户反馈会暂存在 PermissionDenied.message，
    # 后续还要原样转成 steer 输入；这里不能被展示摘要器截断，否则超长反馈不会触发 input_too_large。
    # 普通拒绝附带的自由文本反馈（preserveReasonFormatting）同理原样保留。
    message = (
        _error_message(error)
        if reasonSource is not None
        or (options is not None and options.preserve_reason_formatting is True)
        else projectedError.message
    )
    projectedCode = _read_attr(projectedError, "code")
    projectedDetail = _read_attr(projectedError, "detail")
    return ToolExecutionResult(
        tool_call_id=toolCall.id,
        tool_name=toolCall.name,
        success=False,
        output=None,
        error=ToolExecutionError(
            type=str(error.type) if is_core_error(error) else _error_name(error),
            message=message,
            code=(
                str(handlerFailure.error_code)
                if handlerFailure is not None
                else (projectedCode if projectedCode else None)
            ),
            detail=projectedDetail if projectedDetail else None,
            reason_source=reasonSource,
            stack=_error_stack(error),
        ),
        model_content=modelContent,
        duration_ms=durationMs if durationMs is not None else 0,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )


def create_tool_handler_failure_error(
    toolCall: ExecutableToolCall,
    failure: ToolHandlerFailure,
) -> BaseException:
    """Mirrors ``createToolHandlerFailureError(toolCall, failure)``."""
    return create_core_error(
        CoreErrorType.TOOL_EXECUTION_FAILED,
        failure.message,
        {
            "context": {
                "code": failure.error_code,
                "toolHandlerFailure": failure,
                "toolCallId": toolCall.id,
                "toolName": toolCall.name,
            },
            "recoverable": True,
        },
    )


def is_tool_handler_failure(value: Any) -> bool:
    """Mirrors ``isToolHandlerFailure(value)``。

    # PORT: TS 的结构化类型守卫在 Python 里同时接受数据类型 ``ToolHandlerFailure`` 与等价的
    plain dict（handler 返回的 output 可能是载荷字典）。
    """
    if isinstance(value, ToolHandlerFailure):
        candidate: Any = value
    elif isinstance(value, dict):
        candidate = value
    else:
        return False

    result = _read_key(candidate, "result")
    error_code = _read_key(candidate, "errorCode", "error_code")
    message = _read_key(candidate, "message")
    return (
        result is False
        and isinstance(error_code, (int, float))
        and not isinstance(error_code, bool)
        and _is_finite(error_code)
        and isinstance(message, str)
    )


def is_tool_handler_failure_error(error: Any) -> bool:
    """Mirrors ``isToolHandlerFailureError(error)``."""
    return is_core_error(error) and is_tool_handler_failure(_read_handler_failure(error))


def create_permission_error_result(
    toolCall: ExecutableToolCall,
    reason: Optional[str],
    context: dict[str, Any],
    options: Optional[ErrorResultOptions] = None,
) -> ToolExecutionResult:
    """Mirrors ``createPermissionErrorResult(toolCall, reason, context, options?)``."""
    return create_error_result(
        toolCall,
        create_core_error(
            CoreErrorType.PERMISSION_DENIED,
            reason if reason is not None else f"Permission denied for {toolCall.name}",
            {
                "context": {
                    **context,
                    "toolName": toolCall.name,
                },
                "recoverable": True,
            },
        ),
        None,
        options,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _read_handler_failure(error: BaseException) -> Any:
    """Mirrors ``error.context?.toolHandlerFailure`` (``undefined`` when absent)."""
    context = getattr(error, "context", None)
    if isinstance(context, dict):
        return context.get("toolHandlerFailure")
    return None


def _read_reason_source(error: BaseException) -> Optional[str]:
    """Mirrors the ``reasonSource === "plan_approval_feedback" | "workflow_refine_feedback"`` guard."""
    context = getattr(error, "context", None)
    if isinstance(context, dict):
        source = context.get("reasonSource")
        if source in ("plan_approval_feedback", "workflow_refine_feedback"):
            return source
    return None


def _read_key(candidate: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(candidate, dict):
            if key in candidate:
                return candidate[key]
        elif hasattr(candidate, key):
            return getattr(candidate, key)
    return None


def _read_attr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _error_message(error: BaseException) -> str:
    """Mirrors ``error.message``.

    # PORT: ``CoreError`` 暴露 ``.message`` 属性；普通异常回落到 ``str(error)``。
    """
    message = getattr(error, "message", None)
    return message if isinstance(message, str) else str(error)


def _error_name(error: BaseException) -> str:
    """Mirrors ``error.name``.

    # PORT: Python 普通异常没有 ``name``；回落到类名（``error.name`` 在 TS 即类名）。
    """
    name = getattr(error, "name", None)
    return name if isinstance(name, str) else type(error).__name__


def _error_stack(error: BaseException) -> Optional[str]:
    """Mirrors ``error.stack``.

    # PORT: TS ``Error.stack`` 是字符串；Python 用 ``traceback.format_exception`` 渲染同一个
    概念（无 traceback 时退化为 ``name: message``）。
    """
    if error.__traceback__ is not None:
        return "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
    return f"{_error_name(error)}: {_error_message(error)}"


def _is_finite(value: float) -> bool:
    """Mirrors ``Number.isFinite``."""
    return math.isfinite(value)
