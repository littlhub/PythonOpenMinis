"""``subagent_delegate`` tool — the main agent hands a task to a configured
subagent and gets back its final answer.

Ported semantics from ``agent.tools.subagent`` (first-agent toolset): a worker
agent runs its *own* small loop — its own model (provider type + model id from
the subagent config), its own persona, and its own enabled tools. The inner
loop is silent (no chunk sink), capped at the subagent's ``maxRounds``, and
never exposes the ``subagent_delegate`` tool itself (no infinite delegation).

Configs come from the registry in ``settings.json`` (see
``openminis.agent.subagents``); create them through the 助理 page or
``POST /api/subagents/plan``.
"""

from __future__ import annotations

import json

from ..core.logging import get_logger
from ..data.model import LLMMessage
from ..data.model.agent_content_part import Text
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["SubagentDelegateTool"]


class SubagentDelegateTool:
    """Delegate a task to a configured subagent worker."""

    NAME = "subagent_delegate"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=SubagentDelegateTool.NAME,
            description=(
                "Hand a task to a configured subagent (a specialised worker with "
                "its own model, persona and tools) and return the subagent's final "
                "answer. Use when the task is clearly a specialist's job — writing, "
                "analysis, research — or to parallelise independent subtasks. The "
                "subagent runs by itself and cannot delegate further."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this delegation does, "
                    "shown to the user (e.g. '让写作助手润色报告'). Use the same "
                    "language as the user.",
                ),
                "subagent": AgentToolParam(
                    "string",
                    "The subagent id to delegate to (e.g. 'writer-01'). See the "
                    "助理 page for available ids.",
                ),
                "task": AgentToolParam(
                    "string",
                    "The task to hand over, written for the subagent: context, "
                    "what to produce, and any constraints. Be specific.",
                ),
            },
            required=["tool_title", "subagent", "task"],
            property_ordering=["tool_title", "subagent", "task"],
        )

    @staticmethod
    async def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
        except ValueError as exc:
            return ToolExecutionResult(f"Error: invalid JSON args: {exc}", False,
                                       tool_title=SubagentDelegateTool.NAME)
        sid = str(args.get("subagent", "")).strip()
        task = str(args.get("task", "")).strip()
        tool_title = str(args.get("tool_title", SubagentDelegateTool.NAME))
        if not sid:
            return ToolExecutionResult(
                "Error: 'subagent' is required", False, tool_title=tool_title)
        if not task:
            return ToolExecutionResult(
                "Error: 'task' is required", False, tool_title=tool_title)

        from ..settings.store import SettingsStore
        from ..agent.subagents import SubagentError, run_subagent

        store = SettingsStore.get()
        try:
            body = await run_subagent(store, sid, task, session_id)
        except SubagentError as exc:
            # 配置层面的问题（id 不存在 / 实例缺失 / 引擎不可用）——
            # 这些是模型能自己纠正的（换个 id 或告诉用户），所以只回错误文本。
            return ToolExecutionResult(
                f"Error: {exc}（先在 助理 页创建，或看可用 id）", False,
                tool_title=tool_title,
            )
        except Exception as exc:
            logger.warning("subagent %s run failed: %s", sid, exc)
            return ToolExecutionResult(
                f"Error: subagent {sid} 执行失败: {exc}", False, tool_title=tool_title)

        return ToolExecutionResult(
            f"<subagent:{sid}>\n{body}\n</subagent>", True, tool_title=tool_title)
