"""``skill_use`` tool — pull an activated skill's instructions into context.

Skills in this port are *knowledge bundles* (a ``SKILL.md`` describing a
workflow plus optional scripts), not callable tools. That gap is exactly what
made the model answer "我没有名为 visioncustom 的工具": it saw a skill name,
tried to call it as a tool, and no such tool exists.

The fix is progressive disclosure, the same pattern Claude Code / WorkBuddy
use:

1. the system prompt lists only the **activated** skills (name + one line),
2. ``skill_use('name')`` loads the full ``SKILL.md`` on demand,
3. the model then carries the workflow out with ``shell_execute`` / ``file_*``.

Activation lives in ``settings.json`` under ``activeSkills`` and is edited from
the 技能 page — that is the main agent's "技能调用范围".
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["SkillUseTool"]


class SkillUseTool:
    """Load the full text of an activated skill so the agent can follow it."""

    NAME = "skill_use"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=SkillUseTool.NAME,
            description=(
                "Load the full instructions of an activated skill (its SKILL.md) "
                "into context. Use it whenever a task matches one of the skills "
                "listed under 可用技能 in your system prompt — the returned text "
                "gives the exact workflow, conventions and scripts to follow. "
                "Skills are NOT tools: never call a skill name as a tool directly; "
                "load it here first, then do the work with shell_execute / file_*."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this load is for, shown "
                    "to the user. Use the same language as the user.",
                ),
                "name": AgentToolParam(
                    "string",
                    "The skill to load, exactly as listed under 可用技能 "
                    "(e.g. 'visioncustom').",
                ),
            },
            required=["tool_title", "name"],
            property_ordering=["tool_title", "name"],
        )

    @staticmethod
    async def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
        except ValueError as exc:
            return ToolExecutionResult(
                f"Error: invalid JSON args: {exc}", False,
                tool_title=SkillUseTool.NAME)
        name = str(args.get("name", "")).strip()
        tool_title = str(args.get("tool_title") or SkillUseTool.NAME)
        if not name:
            return ToolExecutionResult(
                "Error: 'name' is required", False, tool_title=tool_title)

        from ..settings.store import SettingsStore
        from ..skills import SkillStore

        store = SettingsStore.get()
        active = store.active_skills()
        try:
            entry = SkillStore().get(name)
        except Exception as exc:  # unreadable dir / bad frontmatter
            logger.warning("skill_use: cannot read %s: %s", name, exc)
            return ToolExecutionResult(
                f"Error: 读取技能 {name} 失败: {exc}", False, tool_title=tool_title)

        if entry is None:
            return ToolExecutionResult(
                f"Error: 没有名为 {name} 的技能。"
                f"已激活的技能: {', '.join(active) or '(无)'}",
                False, tool_title=tool_title)

        if entry.name not in active and Path(entry.path).name not in active:
            return ToolExecutionResult(
                f"Error: 技能 {entry.name} 未激活，请先在 技能 页激活它。"
                f"已激活: {', '.join(active) or '(无)'}",
                False, tool_title=tool_title)

        body = (entry.body or "").strip()
        block = ""
        if entry.scripts:
            listed = "\n".join(f"  - {s}" for s in entry.scripts)
            block = (
                f"\n\n技能自带脚本（需要时用 shell_execute 运行，技能目录："
                f"{entry.path}）:\n{listed}"
            )
        return ToolExecutionResult(
            f"<skill:{entry.name}>\n{body}{block}\n</skill>",
            True, tool_title=tool_title)
