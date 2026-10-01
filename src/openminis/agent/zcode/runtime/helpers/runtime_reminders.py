"""Runtime reminders — plan 模式工作流提醒与 todo 提醒。

Ported from: packages/core/src/runtime/helpers/runtime-reminders.ts
Original package: @zcode/core (runtime/helpers)

上游在**组装下一次模型请求**时，按需往消息流里插一段 system-reminder 附件：

* ``runtime_mode`` —— plan 模式还开着时反复提醒（首条给完整四阶段工作流，之后给一句
  稀疏版）；退出那一下再补一条「已退出」。
* ``todo_reminder`` —— 连续若干轮没动 ``TodoWrite`` 就提醒模型去更新代办列表。

上游的判定完全建立在「扫消息历史」上（``getRuntimeModeReminderTurnCount`` 从后往前
数 human turn）。本移植集的 ZCode 循环不保留那种带 ``metadata.source`` 的
``RuntimeMessageEntry`` 数组，所以这里把**文本逐字移植**，把**计数换成等价的显式状态**
（:class:`PlanReminderState` / :class:`TodoReminderState`）：循环每走一轮喂一次
``note_turn``，判定结果与上游一致，但不依赖消息历史的形状。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

__all__ = [
    "RUNTIME_MODE_REMINDER_CONFIG",
    "TODO_REMINDER_CONFIG",
    "PLAN_MODE_FULL_REMINDER",
    "PLAN_MODE_SPARSE_REMINDER",
    "PLAN_MODE_EXIT_REMINDER",
    "build_plan_workflow",
    "build_todo_reminder_body",
    "PlanReminderState",
    "TodoReminderState",
]


#: Mirrors ``RUNTIME_MODE_REMINDER_CONFIG``.
RUNTIME_MODE_REMINDER_CONFIG: dict[str, int] = {
    "TURNS_BETWEEN_ATTACHMENTS": 5,
    "FULL_REMINDER_EVERY_N_ATTACHMENTS": 5,
}

#: Mirrors ``TODO_REMINDER_CONFIG``.
TODO_REMINDER_CONFIG: dict[str, int] = {
    "TURNS_SINCE_WRITE": 10,
    "TURNS_BETWEEN_REMINDERS": 10,
}

#: 上游是 ``EXPLORE_AGENT_TYPE``；本移植集没有 explore 子代理类型常量，
#: 按上游实际取值字面保留（``subagent/explore.ts``）。
_EXPLORE_AGENT_TYPE = "Explore"
_PLAN_RESEARCH_AGENT_COUNT = 3
_ASK_USER_QUESTION_TOOL_NAME = "AskUserQuestion"
_EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"


def build_plan_workflow() -> str:
    """Mirrors ``buildPlanWorkflow()`` —— 四阶段 plan 工作流（完整提醒的主体）。"""
    explore = _EXPLORE_AGENT_TYPE
    ask = _ASK_USER_QUESTION_TOOL_NAME
    exit_tool = _EXIT_PLAN_MODE_TOOL_NAME
    return f"""## Plan Workflow

### Phase 1: Initial Understanding
Goal: Gain a comprehensive understanding of the user's request by reading through code and asking them questions. Critical: In this phase you should only use the {explore} subagent type.

1. Focus on understanding the user's request and the code associated with their request. Actively search for existing functions, utilities, and patterns that can be reused — avoid proposing new code when suitable implementations already exist.

2. **Launch up to {_PLAN_RESEARCH_AGENT_COUNT} {explore} agents IN PARALLEL** (single message, multiple tool calls) to efficiently explore the codebase.
   - Use 1 agent when the task is isolated to known files, the user provided specific file paths, or you're making a small targeted change.
   - Use multiple agents when: the scope is uncertain, multiple areas of the codebase are involved, or you need to understand existing patterns before planning.
   - Quality over quantity - {_PLAN_RESEARCH_AGENT_COUNT} agents maximum, but you should try to use the minimum number of agents necessary (usually just 1)
   - If using multiple agents: Provide each agent with a specific search focus or area to explore. Example: One agent searches for existing implementations, another explores related components, a third investigating testing patterns

### Phase 2: Design
Goal: Design an implementation approach.

**Guidelines:**
- Use the context gathered in Phase 1, including relevant files and code paths.
- Account for the user's requirements and constraints.
- Produce a concrete implementation plan that is detailed enough to execute.
- Consider useful perspectives for the task type:
  - New feature: simplicity vs performance vs maintainability
  - Bug fix: root cause vs workaround vs prevention
  - Refactoring: minimal change vs clean architecture

### Phase 3: Review
Goal: Review the plan(s) from Phase 2 and ensure alignment with the user's intentions.
1. Read the critical files to deepen your understanding
2. Ensure that the plans align with the user's original request
3. Use {ask} to clarify any remaining questions with the user

### Phase 4: Call {exit_tool}
At the very end of your turn, once you have asked the user questions and are happy with your final plan - you should always call {exit_tool} to indicate to the user that you are done planning.
This is critical - your turn should only end with either using the {ask} tool OR calling {exit_tool}. Do not stop unless it's for these 2 reasons

**Important:** Use {ask} ONLY to clarify requirements or choose between approaches. Use {exit_tool} to request plan approval. Do NOT ask about plan approval in any other way - no text questions, no AskUserQuestion. Phrases like "Is this plan okay?", "Should I proceed?", "How does this plan look?", "Any changes before we start?", or similar MUST use {exit_tool}.

NOTE: At any point in time through this workflow you should feel free to ask the user questions or clarifications using the {ask} tool. Don't make large assumptions about user intent. The goal is to present a well researched plan to the user, and tie any loose ends before implementation begins."""


#: Mirrors ``PLAN_MODE_FULL_REMINDER``.
PLAN_MODE_FULL_REMINDER: tuple[str, ...] = (
    "Plan mode is active. The user indicated that they do not want you to execute yet -- you MUST NOT make any edits, run any non-readonly tools (including changing configs or making commits), or otherwise make any changes to the system. This supercedes any other instructions you have received.",
    build_plan_workflow(),
)

#: Mirrors ``PLAN_MODE_SPARSE_REMINDER``.
PLAN_MODE_SPARSE_REMINDER: tuple[str, ...] = (
    f"Plan mode still active (see full instructions earlier in conversation). Read-only. "
    f"Follow 4-phase workflow. End turns with {_ASK_USER_QUESTION_TOOL_NAME} (for clarifications) "
    f"or {_EXIT_PLAN_MODE_TOOL_NAME} (for plan approval). Never ask about plan approval via text "
    f"or AskUserQuestion.",
)

#: Mirrors ``PLAN_MODE_EXIT_REMINDER``.
PLAN_MODE_EXIT_REMINDER: tuple[str, ...] = (
    "## Exited Plan Mode",
    "",
    "You have exited plan mode. You can now make edits, run tools, and take actions.",
)


def build_todo_reminder_body(todos: Sequence[Any]) -> str:
    """Mirrors ``buildTodoReminderBody(todos)``."""
    lines = [
        "The TodoWrite tool hasn't been used recently. If you're working on tasks that would benefit from tracking progress, consider using the TodoWrite tool to add or update the todo list. Also check whether the existing todo list is still accurate and relevant to the current work. This is just a gentle reminder - ignore if not applicable.",
    ]
    if todos:
        lines.append("")
        lines.append("Here are the existing contents of your todo list:")
        lines.append("")
        lines.append("[" + "\n".join(_format_todo_list_for_reminder(todos)) + "]")
    return "\n".join(lines)


def _format_todo_list_for_reminder(todos: Sequence[Any]) -> list[str]:
    out: list[str] = []
    for index, todo in enumerate(todos):
        status = getattr(todo, "status", None)
        content = getattr(todo, "content", None)
        if status is None and isinstance(todo, dict):
            status = todo.get("status")
            content = todo.get("content")
        out.append(f"{index + 1}. [{status}] {content}")
    return out


# ---------------------------------------------------------------------------
# 计数状态（替代上游「扫消息历史」）
# ---------------------------------------------------------------------------
@dataclass
class PlanReminderState:
    """plan 模式提醒的计数状态。

    上游语义（``buildRuntimeModeReminderBody``）：

    * 连续 ``TURNS_BETWEEN_ATTACHMENTS`` 个 human turn 内已经发过提醒 → 不再发；
    * 第 ``n`` 条提醒若 ``n % FULL_REMINDER_EVERY_N_ATTACHMENTS == 1`` → 发**完整**版，
      否则发稀疏版。

    这里把「已经发过几条」与「距上一条过了几个 human turn」显式记下来，循环每轮
    调一次 :meth:`note_turn` 递增，判定逻辑逐条对应。
    """

    reminder_count: int = 0
    turns_since_reminder: Optional[int] = None
    _pending_exit: bool = False

    def note_turn(self) -> None:
        """走过一个 human turn。"""
        if self.turns_since_reminder is None:
            self.turns_since_reminder = 0
        else:
            self.turns_since_reminder += 1

    def note_plan_exited(self) -> None:
        """``ExitPlanMode`` 成功了 —— 记一条待发的「已退出」提醒。"""
        self._pending_exit = True

    def take_exit_reminder(self) -> Optional[str]:
        """取走待发的退出提醒（只会发一次）。"""
        if not self._pending_exit:
            return None
        self._pending_exit = False
        return "\n".join(PLAN_MODE_EXIT_REMINDER)

    def build(self, plan_enabled: bool) -> Optional[str]:
        """Mirrors ``buildRuntimeModeReminderBody(entries, mode, planEnabled)``。"""
        if not plan_enabled:
            return None
        if (
            self.turns_since_reminder is not None
            and self.turns_since_reminder < RUNTIME_MODE_REMINDER_CONFIG["TURNS_BETWEEN_ATTACHMENTS"]
        ):
            return None

        self.reminder_count += 1
        self.turns_since_reminder = 0
        lines = (
            PLAN_MODE_FULL_REMINDER
            if self.reminder_count % RUNTIME_MODE_REMINDER_CONFIG["FULL_REMINDER_EVERY_N_ATTACHMENTS"] == 1
            else PLAN_MODE_SPARSE_REMINDER
        )
        return "\n".join(lines)


@dataclass
class TodoReminderState:
    """todo 提醒的计数状态（Mirrors ``shouldBuildTodoReminder(entries)``）。"""

    turns_since_last_write: int = 0
    turns_since_last_reminder: int = 0
    last_reminder: Optional[str] = None
    _seen_any_write: bool = False

    def note_todo_write(self) -> None:
        self.turns_since_last_write = 0
        self._seen_any_write = True

    def note_reminder(self, body: str) -> None:
        self.turns_since_last_reminder = 0
        self.last_reminder = body

    def note_turn(self) -> None:
        self.turns_since_last_write += 1
        self.turns_since_last_reminder += 1

    def should_build(self) -> bool:
        """Mirrors ``shouldBuildTodoReminder(entries)``。"""
        # 一轮都没写过 todo 时不上提醒：那说明用户只是在问问题，
        # 而不是在执行一项需要跟踪进度的多步任务。
        if not self._seen_any_write:
            return False
        return (
            self.turns_since_last_write >= TODO_REMINDER_CONFIG["TURNS_SINCE_WRITE"]
            and self.turns_since_last_reminder >= TODO_REMINDER_CONFIG["TURNS_BETWEEN_REMINDERS"]
        )


# PORT: 保留导入以便类型检查器识别 ``field`` 的复用点（后续扩展预留）。
_ = field
