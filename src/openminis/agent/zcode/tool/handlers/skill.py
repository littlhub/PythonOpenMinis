"""Skill tool — load local skill instructions into the session context.

Ported from: packages/core/src/tool/handlers/skill.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

import re
from typing import Any

from ...contracts.errors import CoreErrorType, create_core_error
from ..types import ToolEntry
from .ports import (
    SKILL_INPUT_JSON_SCHEMA,
    SKILL_OUTPUT_JSON_SCHEMA,
    SKILL_TOOL_NAME,
)

MAX_SKILL_BYTES = 100_000

_SKILL_DESCRIPTION = """Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available skills match. Skills provide specialized capabilities and domain knowledge.

When users reference a "slash command" or "/<something>", they are referring to a skill. Use this tool to invoke it.

How to invoke:
- Set `skill` to the exact name of an available skill (no leading slash). For plugin-namespaced skills use the fully qualified `plugin:skill` form.
- Set `args` to pass optional arguments.

Important:
- Available skills are listed in system-reminder messages in the conversation
- Only invoke a skill that appears in that list, or one the user explicitly typed as `/<name>` in their message. Never guess or invent a skill name from training data; otherwise do not call this tool
- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response about the task
- NEVER mention a skill without actually calling this tool
- Do not invoke a skill that is already running
- Do not use this tool for built-in CLI commands (like /help, /clear, etc.)
- If you see a <command-name> tag in the current conversation turn, the skill has ALREADY been loaded - follow the instructions directly instead of calling this tool again
"""


async def skill_handler(input: Any, context: Any) -> Any:
    """Mirrors ``skillHandler: ToolHandler``."""
    parsed = input if isinstance(input, dict) else {}
    skill = parsed.get("skill")
    skill_port = context.skill_port

    if not skill_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "SkillPort is not configured for Skill tool",
            {
                "context": {"toolCallId": context.tool_call_id, "toolName": "Skill"},
                "recoverable": False,
            },
        )

    loaded = await skill_port.load_skill(
        {
            "name": skill,
            "workingDirectory": context.working_directory,
            "maxBytes": MAX_SKILL_BYTES,
            "trace": {
                "traceId": context.trace_id,
                "spanId": context.span_id,
                "parentSpanId": context.parent_span_id,
                "sessionId": context.session_id,
                "turnId": context.turn_id,
            },
        },
        {"signal": context.abort_signal},
    )

    recorder = getattr(context, "record_skill_telemetry_metadata", None)
    if recorder is not None:
        metadata = loaded.metadata
        payload: dict[str, Any] = {"source": metadata.source}
        if metadata.qualified_name:
            payload["qualifiedName"] = metadata.qualified_name
        if metadata.plugin_id:
            payload["pluginId"] = metadata.plugin_id
        recorder(payload)

    lines = [
        f'<skill_content name="{loaded.metadata.name}">',
        f"# Skill: {loaded.metadata.name}",
        "",
        _expand_skill_context_variables(loaded.content, loaded.base_directory),
        "",
        f"Base directory for this skill: {loaded.base_directory}",
        "Relative paths in this skill are relative to this base directory.",
        "[Skill content truncated]" if loaded.truncated else "",
        "</skill_content>",
    ]
    return "\n".join(line for line in lines if len(line) > 0)


def _expand_skill_context_variables(content: str, base_directory: str) -> str:
    # 只有 Skill 工具加载后才有明确的当前 skill 目录，因此变量替换限定在这里完成。
    return re.sub(r"\$\{(?:CLAUDE_SKILL_DIR|ZCODE_SKILL_DIR)\}", lambda _m: base_directory, content)


def _build_skill_tool_entry() -> ToolEntry:
    return ToolEntry(
        capability="Load local skill instructions into the current session context",
        metadata={
            "name": SKILL_TOOL_NAME,
            "description": _SKILL_DESCRIPTION,
            "read_only": True,
            "destructive": False,
            "concurrent_safe": True,
            "timeout_ms": 30000,
            "max_output_bytes": MAX_SKILL_BYTES,
            "side_effect_scope": "session",
            "risk_level": "low",
            "needs_approval": False,
        },
        handler=skill_handler,
        input_schema=SKILL_INPUT_JSON_SCHEMA,
        output_schema=SKILL_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=SKILL_INPUT_JSON_SCHEMA,
        runtime_output_schema=SKILL_OUTPUT_JSON_SCHEMA,
        permission={
            "permission": "skill",
            "reason": "Skill loads local instructions into session context",
            "risk_level": "low",
            "side_effect_scope": "session",
            "needs_approval": False,
            "pattern_sources": ["toolName"],
            "always_allow_pattern_sources": ["toolName"],
            "deny_priority": "beforeAsk",
        },
        result_budget={
            "max_inline_bytes": MAX_SKILL_BYTES,
            "max_model_bytes": MAX_SKILL_BYTES,
            "strategy": "truncate",
            "preview": {"max_bytes": MAX_SKILL_BYTES, "direction": "head"},
        },
        timeout={"default_ms": 30000, "max_ms": 30000, "allow_call_override": False},
        cancellation={
            "supported": True,
            "cleanup": "none",
            "user_visible_message": "Skill loading was cancelled before content was returned",
        },
        trace={
            "propagate_to_adapters": True,
            "record_input": "summary",
            "record_output": "summary",
        },
    )


skill_tool_entry: ToolEntry = _build_skill_tool_entry()
