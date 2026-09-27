"""Turn control — fold conditional turn-stop signals into a tool result.

Ported from: packages/core/src/tool/executor/turn-control.ts
Original package: @zcode/core (tool/executor)

# PORT: ``isAutomationCreateLimitError`` / ``CoreErrorType`` / 各 tool 名常量来自 ``@zcode/contracts``
# （未移植），保留为 ``Any``；``ToolEntry`` / ``ToolExecutionResult`` 来自 ``..types``。
"""

from __future__ import annotations

from typing import Any, Optional

from ...contracts.errors import (  # type: ignore
    CoreErrorType,
    is_automation_create_limit_error,
)
from ...contracts.tools import (  # type: ignore
    AMEND_WORKFLOW_TOOL_NAME,
    CREATE_WORKFLOW_TOOL_NAME,
    EXIT_PLAN_MODE_TOOL_NAME,
)
from ...permission.ports import CollaborationMode  # type: ignore
from ..types import ToolEntry, ToolExecutionResult

__all__ = [
    "with_automation_create_limit_turn_stop",
    "with_plan_exit_denied_turn_stop",
    "with_workflow_refine_denied_follow_up",
    "with_terminal_tool_turn_stop",
]

DEFAULT_EXIT_PLAN_DENIED_MESSAGE = f"Permission denied for {EXIT_PLAN_MODE_TOOL_NAME}"
EXIT_PLAN_DENIED_BY_USER_MESSAGE = "The plan was not approved by the user."
WORKFLOW_REFINE_DENIED_BY_USER_MESSAGE = "The workflow run was not approved by the user."
AUTOMATION_CREATE_LIMIT_MODEL_MESSAGE = (
    "Automation creation was not performed because the global retained-task limit of 20 was reached. "
    + "This limit cannot be recovered automatically in the current turn. Do not list, delete, overwrite, "
    + "retry, or use another tool. Reply once in the user's language that they must manually delete an "
    + "existing task on the Automations page and then retry."
)


def with_automation_create_limit_turn_stop(
    result: ToolExecutionResult,
    input: dict[str, Any],
) -> ToolExecutionResult:
    if (
        result.success
        or input.get("toolName") != "CronCreate"
        or not is_automation_create_limit_error(input.get("error"))
    ):
        return result

    # 创建上限是需要用户手动释放名额的产品边界，不是 Agent 可恢复错误。
    # 给模型隐藏带有"Delete..."诱导性的原始错误，并请求 executor 取消当前 step 的后续工具。
    return _merge(
        result,
        {
            "modelContent": AUTOMATION_CREATE_LIMIT_MODEL_MESSAGE,
            "turnControl": {
                "reason": "automation_create_limit",
                "stopTurnAfterResult": True,
            },
        },
    )


def with_plan_exit_denied_turn_stop(
    result: ToolExecutionResult,
    input: dict[str, Any],
) -> ToolExecutionResult:
    if (
        not (input.get("planEnabled") or input.get("mode") == "plan")
        or input.get("toolName") != EXIT_PLAN_MODE_TOOL_NAME
        or result.success
    ):
        return result

    feedback = _read_plan_exit_denied_feedback(result)
    if feedback:
        return _merge(
            result,
            {
                "followUpUserInput": {
                    "input": feedback,
                    "reasonSource": "plan_approval_feedback",
                },
                # feedback 会通过 steer 成为真实 user message；tool_result 只能表达计划被拒绝，
                # 不能承诺反馈一定跟随，否则 steer 被拒绝时 provider 会看到不存在的后续 user message。
                "modelContent": EXIT_PLAN_DENIED_BY_USER_MESSAGE,
            },
        )

    # 拒绝退出计划代表用户要继续讨论，不能把它当普通工具错误继续喂给模型自我重写计划。
    return _merge(
        result,
        {
            "turnControl": {
                "reason": "plan_exit_denied",
                "stopTurnAfterResult": True,
            },
        },
    )


def _read_plan_exit_denied_feedback(result: ToolExecutionResult) -> Optional[str]:
    if result.error is None or result.error.type != CoreErrorType.PERMISSION_DENIED:
        return None
    # project rule / hook / broker 也可能返回 deny + reason；
    # 只有 ExitPlanMode 审批自定义输入带上的专用 source 才能被解释为用户修改意见。
    if result.error.reason_source != "plan_approval_feedback":
        return None
    message = result.error.message.strip()
    if not message or message == DEFAULT_EXIT_PLAN_DENIED_MESSAGE:
        return None
    return message


def with_workflow_refine_denied_follow_up(
    result: ToolExecutionResult,
    input: dict[str, Any],
) -> ToolExecutionResult:
    if (
        (input.get("toolName") != CREATE_WORKFLOW_TOOL_NAME and input.get("toolName") != AMEND_WORKFLOW_TOOL_NAME)
        or result.success
    ):
        return result
    feedback = _read_workflow_refine_denied_feedback(result)
    if not feedback:
        return result
    return _merge(
        result,
        {
            "followUpUserInput": {
                "input": feedback,
                "reasonSource": "workflow_refine_feedback",
            },
            # tool_result 只能表达运行未获批准，不能承诺反馈一定跟随，
            # 否则 steer 被拒绝时 provider 会看到不存在的后续 user message。
            "modelContent": WORKFLOW_REFINE_DENIED_BY_USER_MESSAGE,
        },
    )


def _read_workflow_refine_denied_feedback(result: ToolExecutionResult) -> Optional[str]:
    if result.error is None or result.error.type != CoreErrorType.PERMISSION_DENIED:
        return None
    # 同 readPlanExitDeniedFeedback：hook / project rule 的 deny + reason 不带该专用 source，
    # 不得被升级成用户消息。
    if result.error.reason_source != "workflow_refine_feedback":
        return None
    message = result.error.message.strip()
    return message or None


def with_terminal_tool_turn_stop(
    result: ToolExecutionResult,
    input: dict[str, Any],
) -> ToolExecutionResult:
    # submit_result 之类的终态工具在其 ToolEntry.metadata 上声明 stopTurnOnSuccess=true：它们的
    # 成功结果表示一次终态提交，必须结束 actor 的 turn。这里读声明式 metadata，而不是按工具名硬编码
    # ——终态是一种一般性的内在能力（任何未来的终态工具都可复用）。
    entry = input.get("entry")
    if entry is None or not result.success or getattr(entry.metadata, "stop_turn_on_success", None) is not True:
        return result
    return _merge(
        result,
        {
            "turnControl": {
                "reason": "subagent_terminal",
                "stopTurnAfterResult": True,
            },
        },
    )


def _merge(result: ToolExecutionResult, patch: dict[str, Any]) -> ToolExecutionResult:
    # PORT: TS ``{ ...result, ...patch }``。Python dataclass 不支持展开，逐字段覆盖已知字段，
    # 其余字段原样保留。
    from dataclasses import replace

    return replace(result, **patch)
