"""Built-in tool handlers — coding-agent tool surface assembly.

Ported from: packages/core/src/tool/handlers/index.ts
Original package: @zcode/core (tool/handlers)

# PORT-STATUS: partial —— 按 ZCORE_PORTING.md 的**边界约束**（只移植编程部分，
# 不移植办公/编排部分），``builtInTools`` 被裁剪为编程 agent 必需的工具面。

被裁掉的成员分两类，均有据可依：

1. **办公 / 编排类**（不在编程 agent 必需范围内）：``create_workflow`` / ``amend_workflow`` /
   ``save_workflow`` / ``list_saved_workflows`` / ``eval_workflow_snippet`` /
   ``list_workflow_runs`` / ``get_workflow_run`` / ``resume_workflow_run`` /
   ``resolve_workflow_question`` / ``list_models``（动态工作流簇）、``Cron*``（cron.ts）、
   ``OffPeak*``（off-peak.ts）、``node_repl`` 的 ``js``。
2. **多 agent 协议类**（尚无对应 Python 端口）：``send_message`` / ``respond_to_coordinator`` /
   ``submit_result`` / ``escalate`` / ``read_session_context``。

因此 ``registerBuiltInTools`` 的对应开关（``includeSendMessage`` / ``includeRespondToCoordinator`` /
``includeSubmitResult`` / ``submitResultSchema`` / ``includeEscalate`` / ``includeWorkflow`` /
``includeAutomation`` / ``includeOffPeak`` / ``includeDynamicWorkflow`` / ``includeNodeRepl`` /
``includeBrowserUse``）也随之不存在——名字若保留却永不生效，只会误导调用方。
见文件末尾 ``EXCLUDED_*`` 常量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, Sequence

from ..bash_timeout_policy import BashTimeoutPolicy
from ..compat import is_subagent_dispatch_tool_name
from ..registry import ToolRegistryRegisterOptions
from ..tool_visibility import create_tool_rule_name_set
from ..types import ToolEntry
from .agent import (
    TASK_TOOL_NAME,
    agent_tool_entry,
    create_agent_tool_entry,
    create_task_tool_entry,
    task_tool_entry,
)
from .ask_user_question import ask_user_question_tool_entry
from .bash import BashToolEntryOptions, bash_tool_entry, create_bash_tool_entry
from .edit import edit_tool_entry
from .glob import glob_tool_entry
from .goal_read import goal_read_tool_entry
from .grep import grep_tool_entry
from .plan_mode import (
    create_enter_plan_mode_tool_entry,
    enter_plan_mode_tool_entry,
    exit_plan_mode_tool_entry,
)
from .ports import (
    ENTER_PLAN_MODE_TOOL_NAME,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    SKILL_TOOL_NAME,
)
from .read import read_tool_entry
from .skill import skill_tool_entry
from .task_output import task_output_tool_entry
from .task_stop import task_stop_tool_entry
from .todo import todo_read_tool_entry, todo_write_tool_entry
from .webfetch import web_fetch_tool_entry
from .websearch import web_search_tool_entry
from .write import write_tool_entry

__all__ = [
    "built_in_tools",
    "register_built_in_tools",
    "RegisterBuiltInToolsOptions",
    "BuiltInToolRegistry",
    "resolve_built_in_tool_entry_for_branch",
    "EXCLUDED_OFFICE_TOOL_NAMES",
    "EXCLUDED_COORDINATION_TOOL_NAMES",
]

# direct 分支保留 Glob/Grep 工具实现；embedded search 分支由 register_built_in_tools
# 统一隐藏 Glob/Grep，并通过 Bash find/grep 接管搜索。
built_in_tools: list[ToolEntry] = [
    read_tool_entry,
    write_tool_entry,
    edit_tool_entry,
    # applyPatchToolEntry,  # PORT: apply-patch.ts 未移植（无对应 handler）。
    bash_tool_entry,
    glob_tool_entry,
    grep_tool_entry,
    web_fetch_tool_entry,
    web_search_tool_entry,
    todo_read_tool_entry,
    todo_write_tool_entry,
    # ``GoalRead`` 在上游只有契约与 tool-identity 登记（无 handler），本包按契约补全。
    # 家庭归属见 ``packages/shared/src/tool-identity.ts`` 的 ``goal`` family。
    goal_read_tool_entry,
    enter_plan_mode_tool_entry,
    exit_plan_mode_tool_entry,
    ask_user_question_tool_entry,
    task_output_tool_entry,
    task_stop_tool_entry,
    agent_tool_entry,
    task_tool_entry,
    skill_tool_entry,
]

#: 办公 / 编排类内置工具：按边界约束不移植（也无 handler 可注册）。
EXCLUDED_OFFICE_TOOL_NAMES: frozenset[str] = frozenset(
    [
        # 动态工作流簇（create-workflow / amend-workflow / save-workflow /
        # list-saved-workflows / eval-workflow-snippet / list-workflow-runs /
        # get-workflow-run / resume-workflow-run / resolve-workflow-question / list-models）
        "CreateWorkflow",
        "AmendWorkflow",
        "SaveWorkflow",
        "ListSavedWorkflows",
        "EvalWorkflowSnippet",
        "ListWorkflowRuns",
        "GetWorkflowRun",
        "ResumeWorkflowRun",
        "ResolveWorkflowQuestion",
        "ListModels",
        # cron.ts
        "CronCreate",
        "CronList",
        "CronUpdate",
        "CronDelete",
        # off-peak.ts
        "OffPeakCreate",
        "OffPeakList",
        # node-repl.ts
        "js",
    ]
)

#: 多 agent 协议类内置工具：上游存在，但本包尚无对应 handler 端口。
EXCLUDED_COORDINATION_TOOL_NAMES: frozenset[str] = frozenset(
    [
        "SendMessage",
        "RespondToCoordinator",
        "submit_result",
        "escalate",
        "ReadSessionContext",
    ]
)


@dataclass(slots=True)
class RegisterBuiltInToolsOptions:
    """Mirrors the coding-only subset of ``interface RegisterBuiltInToolsOptions``.

    # PORT: 保留下来的键与上游同名同义；被裁掉的键见模块 docstring。
    """

    bash_timeout_policy: Optional[BashTimeoutPolicy] = None
    include_skill: Optional[bool] = None
    include_agent: Optional[bool] = None
    embedded_search_enabled: Optional[bool] = None
    # PORT: ``AgentProfile``（subagent/profile.ts）在本仓是 ``Any``——agent.py 的
    # ``build_agent_provider_description`` 只把它透传给 profile 格式化器。
    agent_profiles: Optional[Sequence[Any]] = None
    allowed_tools: Optional[Sequence[str]] = None
    disallowed_tools: Optional[Sequence[str]] = None
    silent_duplicate_warnings: Optional[bool] = None


class BuiltInToolRegistry(Protocol):
    """``registerBuiltInTools`` 需要的那一小片 registry 接口。"""

    def register(
        self,
        entry: ToolEntry,
        options: Optional[ToolRegistryRegisterOptions] = None,
    ) -> None: ...


def register_built_in_tools(
    registry: BuiltInToolRegistry,
    options: Optional[RegisterBuiltInToolsOptions] = None,
) -> None:
    """Mirrors ``registerBuiltInTools(registry, options = {})``."""
    if options is None:
        options = RegisterBuiltInToolsOptions()

    # PORT: TS ``options.allowedTools ? new Set(...) : undefined``——**不做**别名归一；
    # ``disallowedTools`` 走 ``createToolRuleNameSet``，会归一 ``web_search`` 别名。
    allowed_tools = set(options.allowed_tools) if options.allowed_tools else None
    disallowed_tools = create_tool_rule_name_set(options.disallowed_tools)

    for entry in built_in_tools:
        name = entry.metadata.name
        if options.embedded_search_enabled is True and name in (GLOB_TOOL_NAME, GREP_TOOL_NAME):
            continue
        if allowed_tools is not None and name not in allowed_tools:
            continue
        if disallowed_tools is not None and name in disallowed_tools:
            continue
        if is_subagent_dispatch_tool_name(name) and options.include_agent is not True:
            continue
        if name == SKILL_TOOL_NAME and options.include_skill is False:
            continue
        registry.register(
            resolve_built_in_tool_entry_for_branch(entry, options),
            ToolRegistryRegisterOptions(
                silent_duplicate_warning=options.silent_duplicate_warnings
            ),
        )


def resolve_built_in_tool_entry_for_branch(
    entry: ToolEntry,
    options: RegisterBuiltInToolsOptions,
) -> ToolEntry:
    """Mirrors the non-exported ``resolveBuiltInToolEntryForBranch(entry, options)``."""
    name = entry.metadata.name
    if name == "Bash":
        return create_bash_tool_entry(
            BashToolEntryOptions(
                bash_timeout_policy=options.bash_timeout_policy,
                embedded_search_enabled=options.embedded_search_enabled,
            )
        )
    # PORT: 上游的灰度门同时管工具面和**描述**——Agent / Task 的描述里有一条「工作流请求
    # 必须改用 CreateWorkflow」，关闭时那个工具不存在，留着只会把模型指向不存在的工具。
    # 本包裁掉了动态工作流整簇，故 ``dynamicWorkflowEnabled`` 恒为 False，与实际工具面一致。
    if name == "Agent":
        return create_agent_tool_entry(
            {
                "embeddedSearchEnabled": options.embedded_search_enabled,
                "profiles": options.agent_profiles,
                "dynamicWorkflowEnabled": False,
            }
        )
    if name == TASK_TOOL_NAME:
        return create_task_tool_entry(
            {
                "embeddedSearchEnabled": options.embedded_search_enabled,
                "profiles": options.agent_profiles,
                "dynamicWorkflowEnabled": False,
            }
        )
    if name == ENTER_PLAN_MODE_TOOL_NAME:
        return create_enter_plan_mode_tool_entry(
            {"embeddedSearchEnabled": options.embedded_search_enabled}
        )
    return entry
