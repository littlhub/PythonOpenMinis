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
        from ..agent.subagents import get_subagent

        store = SettingsStore.get()
        cfg = get_subagent(store, sid)
        if cfg is None:
            return ToolExecutionResult(
                f"Error: subagent 不存在: {sid}（先在 助理 页创建，或看可用 id）",
                False, tool_title=tool_title,
            )

        # -- build the subagent's own provider ------------------------------
        # cfg.providerId names a provider *instance*; legacy configs only have
        # providerType (protocol) → fall back to the first instance of it.
        from ..settings.chat_service import build_provider

        pid = str(cfg.get("providerId") or cfg.get("providerType") or "")
        data = store.load()
        conf = dict(data["providers"].get(pid) or {})
        if not conf:
            conf = next(
                (dict(c) for c in store.provider_instances()
                 if c.get("type") == pid),
                {},
            )
        if not conf:
            return ToolExecutionResult(
                f"Error: subagent {sid} 的模型服务实例不存在: {pid or '(空)'}",
                False, tool_title=tool_title,
            )
        conf["model"] = cfg.get("model") or conf.get("model", "")
        try:
            provider = build_provider(pid, conf)
        except Exception as exc:  # missing key / unported engine
            return ToolExecutionResult(
                f"Error: subagent {sid} 的模型服务不可用: {exc}",
                False, tool_title=tool_title,
            )

        # -- its own tool registry (never the delegate itself) --------------
        from ..settings.catalog import build_tool_registry

        inner_tools = {
            name: t for name, t in build_tool_registry(cfg.get("tools") or []).items()
            if name != SubagentDelegateTool.NAME
        }
        persona = cfg.get("persona") or f"你是「{cfg.get('name', sid)}」。用中文回复。"

        from ..agent.agent_runtime import AgentRuntime, AgentRuntimeOptions

        runtime = AgentRuntime(tools=inner_tools)  # silent inner loop
        messages: list[LLMMessage] = [LLMMessage(LLMMessage.Role.USER, task)]
        try:
            _, stop_reason = await runtime.run(
                provider, messages, session_id=f"{session_id}:sub:{sid}",
                options=AgentRuntimeOptions(
                    system_prompt=persona,
                    max_turns=max(1, min(int(cfg.get("maxRounds", 6)), 12)),
                ),
            )
        except Exception as exc:
            logger.warning("subagent %s run failed: %s", sid, exc)
            return ToolExecutionResult(
                f"Error: subagent {sid} 执行失败: {exc}", False, tool_title=tool_title)
        finally:
            close = getattr(provider, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:  # pragma: no cover
                    pass

        # collect the assistant text produced after the user message
        parts: list[str] = []
        for msg in messages[1:]:
            if msg.role != LLMMessage.Role.ASSISTANT:
                continue
            for part in msg.content_parts:
                if isinstance(part, Text) and part.text.strip():
                    parts.append(part.text)
        body = "\n\n".join(parts).strip()
        if not body:
            body = f"(subagent 未产出文字，stop_reason={stop_reason})"
        return ToolExecutionResult(
            f"<subagent:{sid}>\n{body}\n</subagent>", True, tool_title=tool_title)
