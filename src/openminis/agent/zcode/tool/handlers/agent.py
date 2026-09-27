"""Agent tool handler — 子代理派发工具。

Ported from: packages/core/src/tool/handlers/agent.ts
Original package: @zcode/core (tool/handlers)

# PORT-STATUS: partial
#   ``subagent/profile.js``（``formatAgentProfilesForPrompt`` / ``AgentProfile``）尚未移植。
#   本文件按其调用点语义就地还原了内置 profile 列表的格式化为 **临时落点**（见
#   ``format_agent_profiles_for_prompt``）；``SubagentPort`` 本身仍未移植，经
#   ``ToolExecutionContext.subagent_port`` 端口注入（与上游一致，本文件不直接依赖其实现）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from ...contracts.errors import CoreErrorType, create_core_error
from ..types import ToolEntry, ToolHandler
from .ports import (
    AGENT_INPUT_JSON_SCHEMA,
    AGENT_INPUT_SCHEMA,
    AGENT_OUTPUT_SCHEMA,
    TASK_TOOL_NAME,
    AgentErrorCode,
    AgentType,
    TraceContext,
)

__all__ = [
    "agent_tool_entry",
    "task_tool_entry",
    "create_agent_tool_entry",
    "create_task_tool_entry",
    "format_agent_output_for_model",
    "build_agent_provider_description",
    "AgentProfile",
    "format_agent_profiles_for_prompt",
]

logger = logging.getLogger(__name__)

MAX_AGENT_MODEL_BYTES = 120_000

AGENT_TOOL_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "status": {"const": "completed", "type": "string"},
                "agentId": {"type": "string"},
                "agentType": {"type": "string"},
                "prompt": {"type": "string"},
                "content": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"const": "text", "type": "string"},
                            "text": {"type": "string"},
                        },
                        "required": ["type", "text"],
                        "additionalProperties": False,
                    },
                },
                "totalToolUseCount": {"type": "integer", "minimum": 0},
                "totalDurationMs": {"type": "integer", "minimum": 0},
                "totalTokens": {"type": "integer", "minimum": 0},
                "usage": {"type": "object", "additionalProperties": True},
            },
            "required": [
                "status",
                "agentId",
                "prompt",
                "content",
                "totalToolUseCount",
                "totalDurationMs",
            ],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "status": {"const": "async_launched", "type": "string"},
                "agentId": {
                    "type": "string",
                    "description": "The ID of the async agent",
                },
                "description": {
                    "type": "string",
                    "description": "The description of the task",
                },
                "prompt": {
                    "type": "string",
                    "description": "The prompt for the agent",
                },
                "outputFile": {
                    "type": "string",
                    "description": "Path to the output file for checking agent progress",
                },
                "canReadOutputFile": {
                    "type": "boolean",
                    "description": "Whether the calling agent has Read/Bash tools to check progress",
                },
            },
            "required": ["status", "agentId", "description", "prompt", "outputFile"],
            "additionalProperties": False,
        },
    ],
}


# ---------------------------------------------------------------------------
# subagent/profile.ts —— 临时落点
# ---------------------------------------------------------------------------
# PORT: ``packages/core/src/subagent/profile.ts`` 未移植。以下 ``AgentProfile`` 与
# ``format_agent_profiles_for_prompt`` 是它被 agent.ts 用到的两个符号的临时落点：内置
# profile 描述按 ``profile.ts`` / ``general-purpose.ts`` / ``explore.ts`` / ``explore-tools.ts``
# 原文转写，工具面过滤沿用 ``tool-policy.ts`` 的「子代理恒剔除 EnterPlanMode/ExitPlanMode」。

DEFAULT_SUBAGENT_TYPE = "general-purpose"
EXPLORE_AGENT_TYPE = "Explore"

_SUBAGENT_CHILD_FORCED_DISALLOWED_TOOLS = ["EnterPlanMode", "ExitPlanMode"]

_EXPLORE_AGENT_ALLOWED_TOOLS = ["Bash", "Glob", "Grep", "Read", "WebFetch", "WebSearch", "TodoWrite"]
_EXPLORE_AGENT_EMBEDDED_SEARCH_ALLOWED_TOOLS = [
    "Bash",
    "Read",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
]
_EXPLORE_AGENT_DESCRIPTION_TOOL_PRIORITY = [
    "Glob",
    "Grep",
    "Read",
    "Bash",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
]

_GENERAL_PURPOSE_DESCRIPTION = (
    "General-purpose agent for researching complex questions, searching for code, and executing "
    "multi-step tasks. When you are searching for a keyword or file and are not confident that you "
    "will find the right match in the first few tries use this agent to perform the search for you."
)

_EXPLORE_DESCRIPTION = (
    "Read-only search agent for broad fan-out searches - when answering means sweeping many files, "
    "directories, or naming conventions and you only need the conclusion, not the file dumps. It "
    "reads excerpts rather than whole files, so it locates code; it doesn't review or audit it. "
    'Specify search breadth: "medium" for moderate exploration, "very thorough" for multiple '
    "locations and naming conventions."
)


@dataclass(slots=True)
class AgentProfile:
    """Ported from ``interface AgentProfile`` (subagent/profile.ts)。

    # PORT: 只保留格式化描述所需字段；``systemPrompt`` 等其余字段由宿主 profile 加载层提供。
    """

    name: str
    description: str
    source: str  # "built-in" | "project" | "user"
    tools: Optional[Sequence[str]] = None
    disallowed_tools: Optional[Sequence[str]] = None


def _build_explore_allowed_tools(options: dict[str, Any]) -> list[str]:
    """Mirrors ``buildExploreAllowedTools(options)`` (subagent/explore-tools.ts)."""
    return (
        list(_EXPLORE_AGENT_EMBEDDED_SEARCH_ALLOWED_TOOLS)
        if options.get("embeddedSearchEnabled")
        else list(_EXPLORE_AGENT_ALLOWED_TOOLS)
    )


def _format_explore_allowed_tools_for_agent_description(options: dict[str, Any]) -> str:
    """Mirrors ``formatExploreAllowedToolsForAgentDescription(options)``."""
    allowed_tools = _build_explore_allowed_tools(options)
    allowed_tool_set = set(allowed_tools)
    prioritized = [tool for tool in _EXPLORE_AGENT_DESCRIPTION_TOOL_PRIORITY if tool in allowed_tool_set]
    unprioritized = [tool for tool in allowed_tools if tool not in _EXPLORE_AGENT_DESCRIPTION_TOOL_PRIORITY]
    return ", ".join([*prioritized, *unprioritized])


def _filter_subagent_child_tool_names(
    tool_names: Sequence[str], disallowed_tools: Optional[Sequence[str]]
) -> list[str]:
    """Mirrors ``filterSubagentChildToolNames`` (subagent/tool-policy.ts)。

    # PORT: 同上游——子 agent 没有独立的 plan approval 恢复面，所有子 agent 工具面统一剔除
    # EnterPlanMode/ExitPlanMode。
    """
    disallowed = {*_SUBAGENT_CHILD_FORCED_DISALLOWED_TOOLS, *(disallowed_tools or [])}
    return [tool_name for tool_name in tool_names if tool_name not in disallowed]


def _normalize_agent_profiles(profiles: list[AgentProfile]) -> list[AgentProfile]:
    """Mirrors ``normalizeAgentProfiles(profiles)`` (subagent/profile.ts)."""
    active: dict[str, AgentProfile] = {}
    active[DEFAULT_SUBAGENT_TYPE] = AgentProfile(
        name=DEFAULT_SUBAGENT_TYPE,
        description=_GENERAL_PURPOSE_DESCRIPTION,
        source="built-in",
        tools=["*"],
    )
    active[EXPLORE_AGENT_TYPE] = AgentProfile(
        name=EXPLORE_AGENT_TYPE,
        description=_EXPLORE_DESCRIPTION,
        source="built-in",
        tools=list(_EXPLORE_AGENT_ALLOWED_TOOLS),
    )
    for profile in profiles:
        active[profile.name] = profile
    return list(active.values())


def _is_built_in_explore_agent_profile(profile: AgentProfile) -> bool:
    """Mirrors ``isBuiltInExploreAgentProfile(profile)``。

    用户/项目 profile 可以同名覆盖内置 Explore，不能只按名称套用内置行为。
    """
    return profile.name == EXPLORE_AGENT_TYPE and profile.source == "built-in"


def format_agent_profiles_for_prompt(
    profiles: Sequence[AgentProfile],
    options: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Mirrors ``formatAgentProfilesForPrompt(profiles, options?)`` (subagent/profile.ts)."""
    options = options or {}
    active = _normalize_agent_profiles(list(profiles))
    if len(active) == 0:
        return None

    lines = ["Available agent types and the tools they have access to:"]
    for profile in active:
        if _is_built_in_explore_agent_profile(profile):
            tools: Any = _format_explore_allowed_tools_for_agent_description(options)
        elif profile.tools:
            tools = _filter_subagent_child_tool_names(profile.tools, profile.disallowed_tools)
        else:
            tools = None
        tool_text = tools if isinstance(tools, str) else (", ".join(tools) if tools else None)
        suffix = f" (Tools: {tool_text})" if tool_text else ""
        lines.append(f"- {profile.name}: {profile.description}{suffix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# provider 描述
# ---------------------------------------------------------------------------

def build_agent_provider_description(options: Optional[dict[str, Any]] = None) -> str:
    """Mirrors ``buildAgentProviderDescription(options?)``.

    动态工作流灰度门也管**工具描述**：
    关闭时十个工具不注册，但这条 bullet 仍在 Agent 的 provider 描述里写着「CreateWorkflow
    是强制的」，于是模型被指向一个根本不存在的工具，只会白白撞一次 tool_not_found。
    缺省 true：TUI、headless 与既有调用方（包括模块加载期烘焙的 AGENT_PROVIDER_DESCRIPTION）
    行为不变，只有显式 false 才抹掉这一行。
    """
    options = options or {}
    agent_list = format_agent_profiles_for_prompt(
        options.get("profiles") or [],
        {"embeddedSearchEnabled": options.get("embeddedSearchEnabled")},
    )

    sections = [
        "Launch a new agent to handle complex, multi-step tasks. Each agent type has specific capabilities and tools available to it.",
        "",
        agent_list,
        "",
        "When using the Agent tool, specify a subagent_type parameter to select which agent type to use. If omitted, the general-purpose agent is used.",
        "",
        "## When to use",
        "",
        "Reach for this when the task matches an available agent type, when you have independent work to run in parallel, or when answering would mean reading across several files — delegate it and you keep the conclusion, not the file dumps. For a single-fact lookup where you already know the file, symbol, or value, search directly. Once you've delegated a search, don't also run it yourself — wait for the result.",
        "",
        "- The agent's final message is returned to you as the tool result; it is not shown to the user — relay what matters.",
        "- A new Agent call starts fresh, so the prompt must be self-contained.",
        "- `run_in_background: true` runs the agent asynchronously; you'll be notified when it completes.",
        "- When you launch multiple agents for independent work, send them in a single message with multiple tool uses so they run concurrently.",
    ]

    # 只保留「用户点名工作流」这一种情形：工作流一律由用户显式请求触发，与系统提示词其余
    # 部分一致。不能把「结果层层喂给下一步的多代理编排」也划给 CreateWorkflow，
    # 那等于让模型在用户没开口时自行选择工作流。
    if options.get("dynamicWorkflowEnabled") is not False:
        sections.append(
            '- If the user explicitly asks for a workflow ("use a workflow", "使用 workflow", "用工作流", or any phrasing naming workflow/工作流 as the means), the CreateWorkflow tool is mandatory: do not use this tool instead, however small the task.'
        )

    return "\n".join(sections)


AGENT_PROVIDER_DESCRIPTION = build_agent_provider_description()


def format_agent_output_for_model(output: Any) -> str:
    """Mirrors ``formatAgentOutputForModel(output)``."""
    # PORT: ``AgentOutputSchema.safeParse(output)``（zod）——结构校验未移植，改为按 ``status``
    # 做等价判别；判别失败即回退为原始字符串 / JSON 序列化。
    if not isinstance(output, dict):
        return output if isinstance(output, str) else (json.dumps(output) if output is not None else str(output))
    status = output.get("status")
    if status not in ("completed", "async_launched"):
        return output if isinstance(output, str) else json.dumps(output)

    data: dict[str, Any] = output
    if data.get("status") != "async_launched":
        child_text = "\n".join(block.get("text", "") for block in data.get("content", []))
        child_content = [child_text] if child_text.strip() else ["(Subagent completed but returned no output.)"]
        usage_lines: list[str] = []
        if data.get("totalTokens") is not None:
            usage_lines.append(f"subagent_tokens: {data.get('totalTokens')}")
        usage_lines.append(f"tool_uses: {data.get('totalToolUseCount')}")
        usage_lines.append(f"duration_ms: {data.get('totalDurationMs')}")
        return "\n".join(
            [
                *child_content,
                f"agentId: {data.get('agentId')} (use SendMessage with to: '{data.get('agentId')}' to continue this agent)",
                f"<usage>{chr(10).join(usage_lines)}</usage>",
            ]
        )

    launch_lines = [
        "Async agent launched successfully.",
        f"agentId: {data.get('agentId')} (internal ID - do not mention to user. Use SendMessage with to: '{data.get('agentId')}' to continue this agent.)",
        "The agent is working in the background. You will be notified automatically when it completes.",
    ]

    if data.get("canReadOutputFile"):
        return "\n".join(
            [
                *launch_lines,
                "Do not duplicate this agent's work - avoid working with the same files or topics it is using. Work on non-overlapping tasks, or briefly tell the user what you launched and end your response.",
                f"output_file: {data.get('outputFile')}",
                "Do NOT Read or tail this file via the shell tool. If the user asks for progress, say the agent is still running; you'll get a completion notification.",
            ]
        )

    return "\n".join(
        [
            *launch_lines,
            "Briefly tell the user what you launched and end your response. Do not generate any other text - agent results will arrive in a subsequent message.",
        ]
    )


def can_read_background_output_file(tool_names: Optional[Sequence[str]]) -> bool:
    """Mirrors the non-exported ``canReadBackgroundOutputFile(toolNames)``."""
    names = set(tool_names or [])
    return "Read" in names or "Bash" in names


async def agent_handler(input: Any, context: Any) -> Any:
    """Mirrors ``agentHandler: ToolHandler``."""
    # PORT: ``AgentInputSchema.parse(input)`` —— executor 传入的 input 已归一化，透传。
    parsed: dict[str, Any] = input
    agent_type = parsed.get("subagent_type") or AgentType.GENERAL_PURPOSE

    if not context.subagent_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "SubagentPort is not configured for Agent tool",
            {
                "context": {
                    "code": AgentErrorCode.SUBAGENT_UNAVAILABLE,
                    "toolCallId": context.tool_call_id,
                    "toolName": "Agent",
                },
                "recoverable": False,
            },
        )

    request = {
        "sessionId": context.session_id,
        "turnId": context.turn_id,
        "parentToolCallId": context.tool_call_id,
        "agentType": agent_type,
        "description": parsed.get("description"),
        "prompt": parsed.get("prompt"),
        "callerCanReadOutputFile": can_read_background_output_file(context.provider_visible_tool_names),
        "workingDirectory": context.working_directory,
        "workspaceRoot": context.workspace_root,
        "trace": TraceContext(
            trace_id=context.trace_id,
            span_id=context.span_id,
            parent_span_id=context.parent_span_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
        ),
    }
    options: dict[str, Any] = {"signal": context.abort_signal}
    if context.model:
        options["model"] = context.model
    if context.subagent_model_override:
        options["modelOverride"] = context.subagent_model_override

    return await context.subagent_port.launch(
        {**request, "runInBackground": parsed.get("run_in_background") is True},
        options,
    )


def _metadata_for_agent(overrides: Optional[dict[str, Any]] = None) -> Any:
    """构造 Agent 的 ``ToolMetadata``（``agentToolEntry.metadata``）。"""
    from ..types import ToolMetadata

    base: dict[str, Any] = {
        "name": "Agent",
        "description": AGENT_PROVIDER_DESCRIPTION,
        "read_only": True,
        "destructive": False,
        "concurrent_safe": True,
        "max_output_bytes": MAX_AGENT_MODEL_BYTES,
        "side_effect_scope": "session",
        "risk_level": "low",
        "needs_approval": False,
    }
    base.update(overrides or {})
    return ToolMetadata(**base)


def _agent_permission() -> Any:
    from ...contracts.tools import ToolPermissionSpec

    return ToolPermissionSpec(
        permission="subagent",
        reason="Agent launches a child runtime; child tool calls are separately constrained and approved",
        risk_level="low",
        side_effect_scope="session",
        needs_approval=False,
        pattern_sources=["input"],
        always_allow_pattern_sources=["input"],
        deny_priority="beforeAsk",
    )


def _agent_result_budget() -> Any:
    from ...contracts.tools import ToolResultBudget, ToolResultBudgetArtifact, ToolResultBudgetPreview

    return ToolResultBudget(
        max_inline_bytes=MAX_AGENT_MODEL_BYTES,
        max_model_bytes=MAX_AGENT_MODEL_BYTES,
        strategy="artifact",
        preview=ToolResultBudgetPreview(max_bytes=MAX_AGENT_MODEL_BYTES, direction="head"),
        artifact=ToolResultBudgetArtifact(enabled=True, retention="session"),
    )


# PORT: ``agentToolEntry`` 是模块级常量；Python 侧用构造函数保持同一份声明（见下）。
def _build_agent_tool_entry() -> ToolEntry:
    from ...contracts.tools import (
        NoToolTimeoutPolicy,
        ToolCancellationPolicy,
        ToolTracePolicy,
    )

    return ToolEntry(
        capability="Launch a profile-backed subagent; background execution is runtime-configured",
        metadata=_metadata_for_agent(),
        handler=agent_handler,
        format_model_content=format_agent_output_for_model,
        input_schema=AGENT_INPUT_JSON_SCHEMA,
        output_schema=AGENT_TOOL_OUTPUT_SCHEMA,
        runtime_input_schema=AGENT_INPUT_SCHEMA,
        runtime_output_schema=AGENT_OUTPUT_SCHEMA,
        permission=_agent_permission(),
        result_budget=_agent_result_budget(),
        timeout=NoToolTimeoutPolicy(),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="bestEffort",
            user_visible_message=(
                "Agent was cancelled before the subagent returned findings or background launch "
                "completed"
            ),
        ),
        trace=ToolTracePolicy(
            propagate_to_adapters=True,
            record_input="summary",
            record_output="summary",
        ),
    )


agent_tool_entry: ToolEntry = _build_agent_tool_entry()

# PORT: ``taskToolEntry = { ...agentToolEntry, capability, metadata: {...} }`` —— 兼容别名。
task_tool_entry: ToolEntry = _build_agent_tool_entry()
task_tool_entry.capability = "Claude Code-compatible alias for launching a ZCode subagent"
task_tool_entry.metadata = _metadata_for_agent(
    {
        "name": TASK_TOOL_NAME,
        "provider_visible": False,
        "description": "\n".join(
            [
                "Claude Code-compatible alias for the Agent tool. Use this when plugin instructions ask for the Task tool.",
                "",
                agent_tool_entry.metadata.description or "",
            ]
        ),
    }
)


def create_agent_tool_entry(options: Optional[dict[str, Any]] = None) -> ToolEntry:
    """Mirrors ``createAgentToolEntry(options?)``。"""
    entry = _build_agent_tool_entry()
    entry.metadata = _metadata_for_agent(
        {"description": build_agent_provider_description(options or {})}
    )
    return entry


def _create_task_tool_entry_from_agent(entry: ToolEntry) -> ToolEntry:
    """Mirrors the non-exported ``createTaskToolEntryFromAgent(entry)``."""
    entry.capability = "Claude Code-compatible alias for launching a ZCode subagent"
    entry.metadata = _metadata_for_agent(
        {
            "name": TASK_TOOL_NAME,
            "provider_visible": False,
            "description": "\n".join(
                [
                    "Claude Code-compatible alias for the Agent tool. Use this when plugin instructions ask for the Task tool.",
                    "",
                    entry.metadata.description or "",
                ]
            ),
        }
    )
    return entry


def create_task_tool_entry(options: Optional[dict[str, Any]] = None) -> ToolEntry:
    """Mirrors ``createTaskToolEntry(options?)``。"""
    return _create_task_tool_entry_from_agent(create_agent_tool_entry(options or {}))
