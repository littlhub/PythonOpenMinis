"""GoalRead tool handler — 读取当前会话的 goal 状态。

Ported from: packages/contracts/src/tools/target.ts（**仅契约**）
             packages/core/src/tool/handlers/*（**上游没有对应 handler**）
Original package: @zcode/contracts (tools)

# PORT: 这是一个**有意的补全**，不是 1:1 搬运。上游把 ``GoalRead`` 登记进了
# ``ZCODE_KNOWN_TOOL_NAMES``（``packages/shared/src/tool-identity.ts``，family ``goal``）
# 并给出了完整的输入/输出 schema（``GoalReadInputSchema`` / ``GoalReadOutputSchema``），
# 但 ``core/src/tool/handlers/`` 下**没有 goal-read.ts**，工具注册表里也没有它的 entry
# ——它要么由未在本仓快照中的插件注入，要么是上游尚未接线的半成品。
#
# 本移植集按契约把 handler 补上：输入为空对象，输出 ``{"goal": SessionGoal | null}``，
# 模型可见文本复用契约里的 ``formatGoalStateForModel``（``target.ts``，已逐字移植到
# ``integration/goal.py``）。这样 ``GoalRead`` 在语义上与契约完全一致，只是补上了
# 上游缺失的那一段执行体。
"""

from __future__ import annotations

from typing import Any, Optional

from ...contracts.tools import ToolCancellationPolicy
from ..types import ToolEntry

__all__ = [
    "GOAL_READ_TOOL_NAME",
    "GOAL_READ_TOOL_DESCRIPTION",
    "GOAL_READ_INPUT_JSON_SCHEMA",
    "GOAL_READ_OUTPUT_JSON_SCHEMA",
    "goal_read_handler",
    "format_goal_read_model_content",
    "goal_read_tool_entry",
]

GOAL_READ_TOOL_NAME = "GoalRead"

GOAL_READ_TOOL_DESCRIPTION = (
    "Read the current session goal (the long-running objective the user asked you to pursue).\n"
    "\n"
    "- Returns the objective, its status (active | paused | budget_limited | complete), "
    "token usage against the budget, and elapsed time.\n"
    "- Use it when you need the authoritative statement of what you are working toward, "
    "for example after a context compaction or when the goal state may have changed.\n"
    "- Returns `null` when no goal is set for this session."
)

MAX_GOAL_READ_MODEL_BYTES = 100_000

# PORT: 契约里 ``GoalReadInputSchema = z.object({}).strict()``。
GOAL_READ_INPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

# PORT: 契约里 ``GoalReadOutputSchema = z.object({ goal: SessionGoalSchema.nullable() }).strict()``。
# ``SessionGoal`` 的完整字段面见 ``integration/goal.GoalState.as_dict``。
GOAL_READ_OUTPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {
            "type": ["object", "null"],
            "properties": {
                "sessionID": {"type": "string"},
                "targetID": {"type": "string"},
                "objective": {"type": "string"},
                "summaryTitle": {"type": ["string", "null"]},
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "budget_limited", "complete"],
                },
                "tokenBudget": {"type": ["integer", "null"]},
                "tokensUsed": {"type": "integer"},
                "timeUsedSeconds": {"type": "integer"},
                "activeInputId": {"type": ["string", "null"]},
                "activeRunStartedAtMs": {"type": ["integer", "null"]},
                "activeRunLastSeenAtMs": {"type": ["integer", "null"]},
                "time": {
                    "type": "object",
                    "properties": {
                        "created": {"type": "integer"},
                        "updated": {"type": "integer"},
                    },
                },
            },
            "required": [
                "sessionID",
                "targetID",
                "objective",
                "summaryTitle",
                "status",
                "tokenBudget",
                "tokensUsed",
                "timeUsedSeconds",
                "time",
            ],
        }
    },
    "required": ["goal"],
    "additionalProperties": False,
}


def _resolve_goal(context: Any) -> Any:
    """取当前会话的 goal；端口缺席时退回进程级默认存储。

    与 ``TodoRead`` 对 ``session_store`` 的处理不同：读 goal 是**纯查询**，端口没配上
    也仍有唯一正确答案（该会话名下没有目标），所以这里不抛 ``ConfigurationError``
    ——报错会让模型以为自己问错了，而它其实只是「这台宿主没开 goal」。
    """
    port = getattr(context, "goal_port", None)
    if port is not None:
        read = getattr(port, "read_goal", None)
        if callable(read):
            return read()
    try:
        from ...integration.goal import get_goal_store

        return get_goal_store().read(str(getattr(context, "session_id", "") or ""))
    except Exception:  # pragma: no cover - 存储不可用时按「没有目标」回答
        return None


async def goal_read_handler(input: Any, context: Any) -> Any:
    """读取会话 goal。

    # PORT: ``GoalReadInputSchema.parse(input)`` —— 空对象，executor 已归一化，透传。
    """
    goal = _resolve_goal(context)
    return {"goal": goal.as_dict() if goal is not None else None}


def format_goal_read_model_content(output: Any) -> str:
    """模型可见文本 —— 复用契约的 ``formatGoalStateForModel``。

    没有目标时**不能**回空串：模型需要一个明确的「无目标」信号，否则会把空输出
    当成工具失败并反复重试。
    """
    from ...integration.goal import GoalState, format_goal_state_for_model

    raw = (output or {}).get("goal") if isinstance(output, dict) else None
    if not raw:
        return "No goal is set for this session."
    goal = GoalState(
        session_id=str(raw.get("sessionID") or ""),
        target_id=str(raw.get("targetID") or ""),
        objective=str(raw.get("objective") or ""),
        summary_title=raw.get("summaryTitle"),
        status=str(raw.get("status") or "active"),
        token_budget=raw.get("tokenBudget"),
        tokens_used=int(raw.get("tokensUsed") or 0),
        time_used_seconds=int(raw.get("timeUsedSeconds") or 0),
        active_input_id=raw.get("activeInputId"),
        active_run_started_at_ms=raw.get("activeRunStartedAtMs"),
        active_run_last_seen_at_ms=raw.get("activeRunLastSeenAtMs"),
        created_at_ms=int((raw.get("time") or {}).get("created") or 0),
        updated_at_ms=int((raw.get("time") or {}).get("updated") or 0),
    )
    return format_goal_state_for_model(goal)


def _goal_read_permission() -> Any:
    from ...contracts.tools import ToolPermissionSpec

    return ToolPermissionSpec(
        permission="goal.read",
        reason="GoalRead only reads the session goal state",
        risk_level="low",
        side_effect_scope="none",
        needs_approval=False,
        pattern_sources=["tool_name"],
        always_allow_pattern_sources=["tool_name"],
        deny_priority="beforeAsk",
    )


def _goal_read_result_budget() -> Any:
    from ...contracts.tools import ToolResultBudget, ToolResultBudgetPreview

    return ToolResultBudget(
        max_inline_bytes=MAX_GOAL_READ_MODEL_BYTES,
        max_model_bytes=MAX_GOAL_READ_MODEL_BYTES,
        strategy="truncate",
        preview=ToolResultBudgetPreview(max_bytes=MAX_GOAL_READ_MODEL_BYTES, direction="head"),
    )


def _goal_read_timeout() -> Any:
    from ...contracts.tools import TimedToolTimeoutPolicy

    return TimedToolTimeoutPolicy(default_ms=30000, max_ms=30000, allow_call_override=False)


def _goal_read_trace() -> Any:
    from ...contracts.tools import ToolTracePolicy

    return ToolTracePolicy(
        propagate_to_adapters=False,
        record_input="summary",
        record_output="summary",
    )


def _build_goal_read_tool_entry() -> ToolEntry:
    return ToolEntry(
        capability="Read the session-long goal objective and its progress",
        metadata={
            "name": GOAL_READ_TOOL_NAME,
            "description": GOAL_READ_TOOL_DESCRIPTION,
            "read_only": True,
            "destructive": False,
            "concurrent_safe": True,
            "timeout_ms": 30000,
            "max_output_bytes": MAX_GOAL_READ_MODEL_BYTES,
            "side_effect_scope": "none",
            "risk_level": "low",
            "needs_approval": False,
        },
        handler=goal_read_handler,
        format_model_content=format_goal_read_model_content,
        input_schema=GOAL_READ_INPUT_JSON_SCHEMA,
        output_schema=GOAL_READ_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=GOAL_READ_INPUT_JSON_SCHEMA,
        runtime_output_schema=GOAL_READ_OUTPUT_JSON_SCHEMA,
        permission=_goal_read_permission(),
        result_budget=_goal_read_result_budget(),
        timeout=_goal_read_timeout(),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="none",
            user_visible_message="GoalRead was cancelled before goal state was returned",
        ),
        trace=_goal_read_trace(),
    )


goal_read_tool_entry: ToolEntry = _build_goal_read_tool_entry()

# PORT: 保留导入以便类型检查器识别 ``Optional`` 的复用点（后续扩展预留）。
_ = Optional
