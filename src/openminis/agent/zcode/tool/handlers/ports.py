"""Handler contract landing — 未移植契约符号的临时落点。

Ported from: packages/contracts/src/tools/*.ts（agent / task-output / task-stop /
todo / plan-mode / ask-user-question / skill / send-message /
respond-to-coordinator / submit-result / list-models）、
packages/contracts/src/tracing/tracer.ts、
packages/core/src/runtime-task/registry.ts、packages/core/src/tool/compat.ts、
packages/shared/src/model-selection.ts（源码不在本仓，按调用点语义还原）。

# PORT: 临时落点（沿用 ``permission/ports.py`` 的先例）。``tool/handlers/*`` 依赖的这批
# 契约类型、工具名常量与 schema，在 ZCode 里来自 ``@zcode/contracts``（每个工具一个模块）、
# core 的 ``runtime-task/registry.ts`` / ``tool/compat.ts``，以及 ``@zcode/shared``；这些
# 模块在本包尚未移植，故先集中落在本文件，供各 handler 引用。逐项真实来源见各定义上方
# 的 ``# PORT:`` 注释。
#
# zod 运行时校验（``*Schema.parse`` / ``safeParse``）无法直接搬运：这里对每个工具保留
# provider 可见的 JSON Schema（``*_JSON_SCHEMA``），并把 zod 对象名（``*_SCHEMA``）映射到
# 同一份 JSON Schema 作为占位。executor 传入的 input 已由契约层校验/归一化，handler 侧
# 只保留原代码里那些**带转换或精化**的解析（在对应 handler 内以 ``# PORT:`` 就地还原）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional, TypedDict, Union

from ...contracts.errors import CoreErrorType, create_core_error
from ...contracts.ids import SessionId, ToolCallId, TraceId, TurnId

__all__ = [
    # 工具名常量
    "TASK_TOOL_NAME",
    "GLOB_TOOL_NAME",
    "GREP_TOOL_NAME",
    "WEBFETCH_TOOL_NAME",
    "WEBSEARCH_TOOL_NAME",
    "SKILL_TOOL_NAME",
    "ENTER_PLAN_MODE_TOOL_NAME",
    "EXIT_PLAN_MODE_TOOL_NAME",
    # glob 契约
    "GlobInput",
    "GlobOutput",
    "GLOB_INPUT_JSON_SCHEMA",
    "GLOB_INPUT_SCHEMA",
    "GLOB_OUTPUT_JSON_SCHEMA",
    "GLOB_OUTPUT_SCHEMA",
    # grep 契约
    "GrepInput",
    "GrepOutput",
    "GrepFileEntry",
    "GrepOutput",
    "GREP_INPUT_JSON_SCHEMA",
    "GREP_INPUT_SCHEMA",
    "GREP_OUTPUT_JSON_SCHEMA",
    "GREP_OUTPUT_SCHEMA",
    # webfetch 契约
    "WebFetchInput",
    "WebFetchOutput",
    "WEBFETCH_INPUT_JSON_SCHEMA",
    "WEBFETCH_INPUT_SCHEMA",
    "WEBFETCH_OUTPUT_JSON_SCHEMA",
    "WEBFETCH_OUTPUT_SCHEMA",
    # skill 契约
    "SkillRuntimeInput",
    "SKILL_INPUT_JSON_SCHEMA",
    "SKILL_OUTPUT_JSON_SCHEMA",
    "TASK_OUTPUT_TOOL_NAME",
    "TASK_STOP_TOOL_NAME",
    "ASK_USER_QUESTION_TOOL_NAME",
    "SEND_MESSAGE_TOOL_NAME",
    "RESPOND_TO_COORDINATOR_TOOL_NAME",
    "SUBMIT_RESULT_TOOL_NAME",
    "LIST_MODELS_TOOL_NAME",
    # 枚举 / 常量对象
    "AgentType",
    "AgentErrorCode",
    "TodoStatus",
    "TodoPriority",
    "TOOL_JSON_SCHEMA_VERSION",
    "TASK_OUTPUT_ALIASES",
    "TASK_OUTPUT_PROVIDER_DESCRIPTION",
    "PLAN_MODE_MAX_PLAN_CHARS",
    "SEND_MESSAGE_MAX_CONTENT_CHARS",
    "RESPOND_TO_COORDINATOR_MAX_CONTENT_CHARS",
    # tracing / 通用
    "TraceContext",
    "ModelMessageContent",
    "JsonSchema",
    "SubmitViolation",
    # agent 契约
    "AgentInput",
    "AgentTextContentBlock",
    "AgentCompletedOutput",
    "AgentBackgroundedOutput",
    "AgentOutput",
    "AGENT_INPUT_JSON_SCHEMA",
    "AGENT_INPUT_SCHEMA",
    "AGENT_OUTPUT_SCHEMA",
    # task-output 契约
    "TaskOutputInput",
    "TaskOutputTask",
    "TaskOutputResult",
    "TASK_OUTPUT_INPUT_JSON_SCHEMA",
    "TASK_OUTPUT_INPUT_SCHEMA",
    "TASK_OUTPUT_RESULT_JSON_SCHEMA",
    "TASK_OUTPUT_RESULT_SCHEMA",
    # task-stop 契约
    "TaskStopInput",
    "TaskStopOutput",
    "TASK_STOP_INPUT_JSON_SCHEMA",
    "TASK_STOP_INPUT_SCHEMA",
    "TASK_STOP_OUTPUT_JSON_SCHEMA",
    "TASK_STOP_OUTPUT_SCHEMA",
    # todo 契约
    "TodoItem",
    "TodoSummary",
    "TodoReadOutput",
    "TodoWriteInput",
    "TodoWriteOutput",
    "TODO_READ_INPUT_JSON_SCHEMA",
    "TODO_READ_INPUT_SCHEMA",
    "TODO_READ_OUTPUT_JSON_SCHEMA",
    "TODO_READ_OUTPUT_SCHEMA",
    "TODO_WRITE_INPUT_JSON_SCHEMA",
    "TODO_WRITE_INPUT_SCHEMA",
    "TODO_WRITE_OUTPUT_JSON_SCHEMA",
    "TODO_WRITE_OUTPUT_SCHEMA",
    "format_todo_state_for_model",
    # plan-mode 契约
    "EnterPlanModeOutput",
    "ExitPlanModeInput",
    "ExitPlanModeOutput",
    "ExitPlanModeAllowedPrompt",
    "ENTER_PLAN_MODE_INPUT_JSON_SCHEMA",
    "ENTER_PLAN_MODE_INPUT_SCHEMA",
    "ENTER_PLAN_MODE_OUTPUT_JSON_SCHEMA",
    "EXIT_PLAN_MODE_INPUT_JSON_SCHEMA",
    "EXIT_PLAN_MODE_OUTPUT_JSON_SCHEMA",
    # ask-user-question 契约
    "AskUserQuestion",
    "AskUserQuestionOption",
    "AskUserQuestionAnnotation",
    "AskUserQuestionOutput",
    "ASK_USER_QUESTION_INPUT_JSON_SCHEMA",
    "ASK_USER_QUESTION_OUTPUT_JSON_SCHEMA",
    "ASK_USER_QUESTION_ANSWERED_INPUT_SCHEMA",
    # skill 契约
    "SkillRuntimeInput",
    "SKILL_INPUT_JSON_SCHEMA",
    "SKILL_OUTPUT_JSON_SCHEMA",
    # send-message 契约
    "SendMessageInput",
    "SendMessageOutput",
    "SEND_MESSAGE_INPUT_JSON_SCHEMA",
    "SEND_MESSAGE_OUTPUT_JSON_SCHEMA",
    # respond-to-coordinator 契约
    "RespondToCoordinatorInput",
    "RespondToCoordinatorOutput",
    "RESPOND_TO_COORDINATOR_INPUT_JSON_SCHEMA",
    "RESPOND_TO_COORDINATOR_OUTPUT_JSON_SCHEMA",
    # submit-result 契约
    "SubmitResultInput",
    "SubmitResultOutput",
    "SUBMIT_RESULT_INPUT_JSON_SCHEMA",
    "SUBMIT_RESULT_OUTPUT_JSON_SCHEMA",
    "typed_submit_result_input_schema",
    # list-models 契约
    "ListModelsOutput",
    "LIST_MODELS_INPUT_JSON_SCHEMA",
    "LIST_MODELS_OUTPUT_JSON_SCHEMA",
    # runtime-task/registry
    "RuntimeTaskSnapshot",
    "BackgroundExecutionSnapshot",
    "TaskOutputFileRead",
    # model-selection / model-catalog
    "ModelCatalogEntry",
    "ModelSelection",
    "ZCODE_MODEL_REASONING_SEPARATOR",
    "format_model_picker_value",
    "parse_model_picker_value",
    # 端口占位：未移植能力
    "FileSystemPortError",
    "create_file_system_error",
    "is_file_system_port_error",
    "write_approved_plan_file",
    "assert_not_off_peak_turn",
    "OFF_PEAK_SEND_MESSAGE_HINT_SUFFIX",
    # bash 契约（packages/contracts/src/tools/bash.ts）
    "BASH_TOOL_NAME",
    "MAX_BASH_TIMEOUT_MS",
    "TRUE_BOOLEAN_STRINGS",
    "FALSE_BOOLEAN_STRINGS",
    "BASH_DESCRIPTION_FIELD_PROMPT",
    "BashInput",
    "BashOutput",
    "BashToolCall",
    "BashToolResult",
    "BackgroundTask",
    "BASH_ERROR_CODE",
    "BashErrorCode",
    "ShellPermissionRule",
    "ShellPermissionPolicy",
    "BASH_INPUT_JSON_SCHEMA",
    "BASH_INPUT_SCHEMA",
    "BASH_OUTPUT_JSON_SCHEMA",
    "BASH_OUTPUT_SCHEMA",
    "semantic_number",
    "semantic_boolean",
    # FileSystemPort 搜索契约
    # （packages/contracts/src/interfaces/file-system.port.ts）
    "FileSystemOperationOptions",
    "FileSystemTextSearchOutputMode",
    "FileSystemSearchFilesRequest",
    "FileSystemSearchFilesResult",
    "FileSystemSearchTextRequest",
    "FileSystemSearchTextEntry",
    "FileSystemSearchTextResult",
    # 工具执行遥测契约（packages/contracts/src/tools/performance.ts）
    "ToolCommandStatus",
    "CommandExecutionTelemetry",
    "FileSystemExecutionTelemetry",
    "PatchExecutionTelemetry",
    "ToolExecutionTelemetryDetail",
    "ToolExecutionTelemetry",
]


# ---------------------------------------------------------------------------
# 工具名常量
# ---------------------------------------------------------------------------

# PORT: 真实来源 packages/core/src/tool/compat.ts（``AGENT_TOOL_NAME`` / ``TASK_TOOL_NAME``）。
TASK_TOOL_NAME = "Task"

# PORT: 真实来源 packages/contracts/src/tools/task-output.ts（``TASK_OUTPUT_TOOL_NAME``）。
TASK_OUTPUT_TOOL_NAME = "TaskOutput"
# 真实来源同文件的 ``TASK_OUTPUT_ALIASES``。
TASK_OUTPUT_ALIASES: list[str] = [
    "AgentOutputTool",
    "BashOutputTool",
    "AgentOutput",
    "BashOutput",
]

# PORT: 真实来源 packages/contracts/src/tools/task-stop.ts（``TASK_STOP_TOOL_NAME``）。
TASK_STOP_TOOL_NAME = "TaskStop"

# PORT: 真实来源 packages/contracts/src/tools/ask-user-question.ts。
ASK_USER_QUESTION_TOOL_NAME = "AskUserQuestion"

# PORT: 真实来源 packages/contracts/src/tools/send-message.ts。
SEND_MESSAGE_TOOL_NAME = "SendMessage"
SEND_MESSAGE_MAX_CONTENT_CHARS = 20_000

# PORT: 真实来源 packages/contracts/src/tools/respond-to-coordinator.ts。
RESPOND_TO_COORDINATOR_TOOL_NAME = "RespondToCoordinator"
RESPOND_TO_COORDINATOR_MAX_CONTENT_CHARS = 20_000

# PORT: 真实来源 packages/contracts/src/tools/submit-result.ts。
SUBMIT_RESULT_TOOL_NAME = "submit_result"

# PORT: 真实来源 packages/contracts/src/tools/list-models.ts。
LIST_MODELS_TOOL_NAME = "ListModels"

# PORT: 真实来源 packages/contracts/src/tools/json-schema.ts（``TOOL_JSON_SCHEMA_VERSION``）。
TOOL_JSON_SCHEMA_VERSION = "https://json-schema.org/draft/2020-12/schema"

# PORT: 真实来源 packages/contracts/src/tools/plan-mode.ts（``PLAN_MODE_MAX_PLAN_CHARS``）。
PLAN_MODE_MAX_PLAN_CHARS = 20_000


# ---------------------------------------------------------------------------
# 枚举 / ``as const`` 常量对象
# ---------------------------------------------------------------------------

class AgentType:
    """Ported from the ``AgentType`` ``as const`` object (tools/agent.ts).

    # PORT: ``as const`` 对象 → 模块级常量类（值保持原字符串，协议兼容）。
    """

    GENERAL_PURPOSE = "general-purpose"
    EXPLORE = "Explore"


class AgentErrorCode:
    """Ported from the ``AgentErrorCode`` ``as const`` object (tools/agent.ts)."""

    SUBAGENT_UNAVAILABLE = "agent_subagent_unavailable"
    BACKGROUND_UNAVAILABLE = "agent_background_unavailable"
    UNKNOWN_AGENT_TYPE = "agent_unknown_type"
    CHILD_RUNTIME_FAILED = "agent_child_runtime_failed"


class TodoStatus:
    """Ported from the ``TodoStatus`` ``as const`` object (tools/todo.ts)."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TodoPriority:
    """Ported from the ``TodoPriority`` ``as const`` object (tools/todo.ts)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# tracing / 通用载荷类型
# ---------------------------------------------------------------------------

# PORT: 这里原先另立了一份 ``TraceContext``。``contracts/tracing.py`` 已有权威定义
# （字段一致，仅 ``query_id`` 用 ``QueryId`` 而非 ``str``），故删除本地副本、改为再导出，
# 避免同名前向类型两份定义导致 ``isinstance`` / 相等判断失配。
from ...contracts.tracing import ExecutionContext, TraceContext  # noqa: E402


# PORT: 真实来源 packages/contracts/src/model/index.ts（``ModelMessageContent`` /
# ``JsonSchema``）；model 契约模块未移植，这里保留宽松别名。
ModelMessageContent = Union[str, list[Any]]
JsonSchema = dict[str, Any]


# ---------------------------------------------------------------------------
# agent 契约（packages/contracts/src/tools/agent.ts）
# ---------------------------------------------------------------------------

class AgentInput(TypedDict, total=False):
    """Ported from ``AgentInputSchema`` / ``type AgentInput`` (tools/agent.ts)."""

    description: str
    prompt: str
    subagent_type: str
    run_in_background: bool


@dataclass(slots=True)
class AgentTextContentBlock:
    """Ported from ``interface AgentTextContentBlock``."""

    type: str  # "text"
    text: str


@dataclass(slots=True)
class AgentCompletedOutput:
    """Ported from ``interface AgentCompletedOutput``."""

    status: str  # "completed"
    agent_id: str
    agent_type: str
    description: str
    prompt: str
    content: list[AgentTextContentBlock]
    total_tool_use_count: int
    total_duration_ms: int
    total_tokens: Optional[int] = None
    usage: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class AgentBackgroundedOutput:
    """Ported from ``interface AgentBackgroundedOutput``."""

    status: str  # "async_launched"
    is_async: bool
    agent_id: str
    agent_type: str
    description: str
    prompt: str
    child_session_id: str
    background_task_id: str
    output_file: str
    can_read_output_file: bool


AgentOutput = Union[AgentCompletedOutput, AgentBackgroundedOutput]

# PORT: ``AgentInputJsonSchema = toToolJsonSchema(AgentInputSchema)``；此处按 zod 定义
# 手工转写 provider 可见的 JSON Schema。
AGENT_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "A short (3-5 word) description of the task",
        },
        "prompt": {
            "type": "string",
            "description": "The task for the agent to perform",
        },
        "subagent_type": {
            "type": "string",
            "description": "The type of specialized agent to use for this task",
        },
        "run_in_background": {
            "type": "boolean",
            "description": (
                "Set to true to run this agent in the background. You will be notified when it "
                "completes."
            ),
        },
    },
    "required": ["description", "prompt"],
    "additionalProperties": False,
}

# PORT: zod ``AgentInputSchema`` 无法搬运；executor 传入的 input 已归一化，运行时校验在契约层。
AGENT_INPUT_SCHEMA = AGENT_INPUT_JSON_SCHEMA

# PORT: ``AgentOutputJsonSchema = toToolJsonSchema(AgentOutputSchema)``（zod union
# ``AgentCompletedOutputSchema | AgentBackgroundedOutputSchema``）。handler 里的 ``safeParse``
# 分支按 ``status`` 判别（见 agent.py）。
AGENT_OUTPUT_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "status": {"const": "completed", "type": "string"},
                "agentId": {"type": "string"},
                "agentType": {"type": "string"},
                "description": {"type": "string"},
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
                "usage": {"type": "object"},
            },
            "required": [
                "status",
                "agentId",
                "agentType",
                "description",
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
                "isAsync": {"const": True, "type": "boolean"},
                "agentId": {"type": "string"},
                "agentType": {"type": "string"},
                "description": {"type": "string"},
                "prompt": {"type": "string"},
                "childSessionId": {"type": "string"},
                "backgroundTaskId": {"type": "string"},
                "outputFile": {"type": "string"},
                "canReadOutputFile": {"type": "boolean"},
            },
            "required": [
                "status",
                "isAsync",
                "agentId",
                "agentType",
                "description",
                "prompt",
                "childSessionId",
                "backgroundTaskId",
                "outputFile",
                "canReadOutputFile",
            ],
            "additionalProperties": False,
        },
    ],
}


# ---------------------------------------------------------------------------
# task-output 契约（packages/contracts/src/tools/task-output.ts）
# ---------------------------------------------------------------------------

TASK_OUTPUT_PROVIDER_DESCRIPTION = """DEPRECATED: Background tasks return their output file path in the tool result, and you receive a <task-notification> with the same path when the task completes.
- For bash tasks: prefer using the Read tool on that output file path — it contains stdout/stderr.
- For local_agent tasks: use the Agent tool result directly. Do NOT Read the .output file — it is a symlink to the full subagent conversation transcript (JSONL) and will overflow your context window.
- For remote_agent tasks: prefer using the Read tool on the output file path — it contains the streamed remote session output (same as bash).

- Retrieves output from a running or completed task (background shell, agent, or remote session)
- Takes a task_id parameter identifying the task
- Returns the task output along with status information
- Use block=true (default) to wait for task completion
- Use block=false for non-blocking check of current status
- Task IDs can be found using the /tasks command
- Works with all task types: background shells, async agents, and remote sessions"""


class TaskOutputInput(TypedDict, total=False):
    """Ported from ``TaskOutputInputSchema`` / ``type TaskOutputInput``."""

    task_id: str
    block: bool
    timeout: int


@dataclass(slots=True)
class TaskOutputTask:
    """Ported from ``TaskOutputTaskSchema`` / ``type TaskOutputTask``."""

    task_id: str
    task_type: str
    status: str
    description: str
    output: str
    exit_code: Optional[int] = None
    error: Optional[str] = None
    prompt: Optional[str] = None
    result: Optional[str] = None
    output_file: Optional[str] = None


@dataclass(slots=True)
class TaskOutputResult:
    """Ported from ``TaskOutputResultSchema`` / ``type TaskOutputResult``."""

    retrieval_status: str  # "success" | "not_ready" | "timeout"
    task: Optional[TaskOutputTask]


# PORT: ``TaskOutputInputJsonSchema`` —— 注意 provider schema 仍要求显式传入 task_id/block/timeout。
TASK_OUTPUT_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "The task ID to get output from"},
        "block": {"type": "boolean", "description": "Whether to wait for completion"},
        "timeout": {
            "type": "number",
            "minimum": 0,
            "maximum": 600_000,
            "description": "Max wait time in ms",
        },
    },
    "required": ["task_id", "block", "timeout"],
    "additionalProperties": False,
}

TASK_OUTPUT_INPUT_SCHEMA = TASK_OUTPUT_INPUT_JSON_SCHEMA

TASK_OUTPUT_RESULT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "retrieval_status": {"type": "string", "enum": ["success", "not_ready", "timeout"]},
        "task": {
            "type": ["object", "null"],
            "properties": {
                "task_id": {"type": "string"},
                "task_type": {"type": "string"},
                "status": {"type": "string"},
                "description": {"type": "string"},
                "output": {"type": "string"},
                "exitCode": {"type": ["number", "null"]},
                "error": {"type": "string"},
                "prompt": {"type": "string"},
                "result": {"type": "string"},
                "outputFile": {"type": "string"},
            },
            "required": ["task_id", "task_type", "status", "description", "output"],
            "additionalProperties": False,
        },
    },
    "required": ["retrieval_status", "task"],
    "additionalProperties": False,
}

TASK_OUTPUT_RESULT_SCHEMA = TASK_OUTPUT_RESULT_JSON_SCHEMA


# ---------------------------------------------------------------------------
# task-stop 契约（packages/contracts/src/tools/task-stop.ts）
# ---------------------------------------------------------------------------

class TaskStopInput(TypedDict, total=False):
    """Ported from ``TaskStopInputSchema`` / ``type TaskStopInput``."""

    task_id: str
    shell_id: str


class TaskStopOutput(TypedDict, total=False):
    """Ported from ``TaskStopOutputSchema`` / ``type TaskStopOutput``."""

    message: str
    task_id: str
    task_type: str
    command: str


TASK_STOP_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "The ID of the background task to stop"},
        "shell_id": {"type": "string", "description": "Deprecated: use task_id instead"},
    },
    "additionalProperties": False,
}

TASK_STOP_INPUT_SCHEMA = TASK_STOP_INPUT_JSON_SCHEMA

TASK_STOP_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "task_id": {"type": "string"},
        "task_type": {"type": "string"},
        "command": {"type": "string"},
    },
    "required": ["message", "task_id", "task_type"],
    "additionalProperties": False,
}

TASK_STOP_OUTPUT_SCHEMA = TASK_STOP_OUTPUT_JSON_SCHEMA


# ---------------------------------------------------------------------------
# todo 契约（packages/contracts/src/tools/todo.ts）
# ---------------------------------------------------------------------------

class TodoItem(TypedDict, total=False):
    """Ported from ``TodoItemSchema`` / ``type TodoItem``."""

    content: str
    status: str
    priority: str


class TodoSummary(TypedDict, total=False):
    """Ported from ``interface TodoSummary``."""

    total: int
    pending: int
    inProgress: int
    completed: int


class TodoReadOutput(TypedDict, total=False):
    """Ported from ``interface TodoReadOutput``."""

    todos: list[TodoItem]


class TodoWriteInput(TypedDict, total=False):
    """Ported from ``TodoWriteInputSchema`` / ``type TodoWriteInput``."""

    todos: list[TodoItem]


class TodoWriteOutput(TypedDict, total=False):
    """Ported from ``interface TodoWriteOutput``."""

    oldTodos: list[TodoItem]
    todos: list[TodoItem]
    summary: TodoSummary


_TODO_ITEM_JSON = {
    "type": "object",
    "properties": {
        "content": {"type": "string", "minLength": 1, "description": "Brief description of the task"},
        "status": {
            "type": "string",
            "enum": ["pending", "in_progress", "completed"],
            "description": "Current status of the task",
        },
        "priority": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "Priority level of the task",
        },
    },
    "required": ["content", "status", "priority"],
    "additionalProperties": False,
}

TODO_READ_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
TODO_READ_INPUT_SCHEMA = TODO_READ_INPUT_JSON_SCHEMA

TODO_READ_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {"todos": {"type": "array", "items": _TODO_ITEM_JSON}},
    "required": ["todos"],
    "additionalProperties": False,
}
TODO_READ_OUTPUT_SCHEMA = TODO_READ_OUTPUT_JSON_SCHEMA

TODO_WRITE_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "items": _TODO_ITEM_JSON,
            "description": (
                "The complete updated todo list. At most one item may be in_progress at a time."
            ),
        }
    },
    "required": ["todos"],
    "additionalProperties": False,
}
TODO_WRITE_INPUT_SCHEMA = TODO_WRITE_INPUT_JSON_SCHEMA

TODO_WRITE_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "oldTodos": {"type": "array", "items": _TODO_ITEM_JSON},
        "todos": {"type": "array", "items": _TODO_ITEM_JSON},
        "summary": {
            "type": "object",
            "properties": {
                "total": {"type": "number"},
                "pending": {"type": "number"},
                "inProgress": {"type": "number"},
                "completed": {"type": "number"},
            },
            "required": ["total", "pending", "inProgress", "completed"],
            "additionalProperties": False,
        },
    },
    "required": ["oldTodos", "todos", "summary"],
    "additionalProperties": False,
}
TODO_WRITE_OUTPUT_SCHEMA = TODO_WRITE_OUTPUT_JSON_SCHEMA


def format_todo_state_for_model(todos: list[TodoItem]) -> str:
    """Mirrors ``formatTodoStateForModel(todos)`` (tools/todo.ts)。

    # PORT: 真实归属 ``@zcode/contracts``（tools/todo.ts）；该契约模块未移植，先落在本文件。
    """
    if len(todos) == 0:
        return ""

    lines = ["Current session todo state (authoritative):"]
    for index, todo in enumerate(todos):
        lines.append(f"{index + 1}. [{todo.get('status')}][{todo.get('priority')}] {todo.get('content')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# plan-mode 契约（packages/contracts/src/tools/plan-mode.ts）
# ---------------------------------------------------------------------------

class EnterPlanModeOutput(TypedDict, total=False):
    """Ported from ``EnterPlanModeOutputSchema`` / ``type EnterPlanModeOutput``."""

    message: str
    previousMode: str
    mode: str
    planEnabled: bool
    previousPlanEnabled: bool


class ExitPlanModeAllowedPrompt(TypedDict, total=False):
    """Ported from ``ExitPlanModeAllowedPromptSchema`` / ``type ExitPlanModeAllowedPrompt``."""

    tool: str
    prompt: str


class ExitPlanModeInput(TypedDict, total=False):
    """Ported from ``ExitPlanModeInputSchema`` / ``type ExitPlanModeInput``."""

    plan: str
    allowedPrompts: list[ExitPlanModeAllowedPrompt]


class ExitPlanModeOutput(TypedDict, total=False):
    """Ported from ``ExitPlanModeOutputSchema`` / ``type ExitPlanModeOutput``."""

    plan: Optional[str]
    approved: bool
    previousMode: str
    planEnabled: bool
    previousPlanEnabled: bool
    mode: str
    allowedPrompts: list[ExitPlanModeAllowedPrompt]


ENTER_PLAN_MODE_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
ENTER_PLAN_MODE_INPUT_SCHEMA = ENTER_PLAN_MODE_INPUT_JSON_SCHEMA

ENTER_PLAN_MODE_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "message": {"type": "string", "minLength": 1, "description": "Confirmation that plan mode was entered."},
        "previousMode": {
            "type": "string",
            "enum": ["plan", "build", "edit", "yolo", "auto"],
            "description": "Session mode before EnterPlanMode ran.",
        },
        "mode": {
            "type": "string",
            "enum": ["plan", "build", "edit", "yolo", "auto"],
            "description": "Current permission mode.",
        },
        "planEnabled": {"type": "boolean"},
        "previousPlanEnabled": {"type": "boolean"},
    },
    "required": ["message", "previousMode", "mode"],
    "additionalProperties": False,
}

EXIT_PLAN_MODE_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "plan": {
            "type": "string",
            "minLength": 1,
            "maxLength": PLAN_MODE_MAX_PLAN_CHARS,
            "description": "The implementation plan to present to the user for approval.",
        },
        "allowedPrompts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": ["Bash"], "description": "The tool this prompt applies to"},
                    "prompt": {
                        "type": "string",
                        "description": 'Semantic description of the action, e.g. "run tests", "install dependencies"',
                    },
                },
                "required": ["tool", "prompt"],
                "additionalProperties": False,
            },
            "description": (
                "Prompt-based permissions needed to implement the plan. These describe categories "
                "of actions rather than specific commands."
            ),
        },
    },
    "required": ["plan"],
    # zod ``.catchall(z.unknown())`` → 允许额外键。
    "additionalProperties": True,
}

EXIT_PLAN_MODE_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "plan": {"type": ["string", "null"], "description": "The plan that was approved by the user."},
        "approved": {"const": True, "description": "True when the user approved exiting plan mode."},
        "previousMode": {
            "type": "string",
            "enum": ["plan", "build", "edit", "yolo", "auto"],
            "description": "Previous permission mode.",
        },
        "planEnabled": {"type": "boolean"},
        "previousPlanEnabled": {"type": "boolean"},
        "mode": {
            "type": "string",
            "enum": ["build", "edit", "yolo", "auto"],
            "description": "Current session mode after exiting plan mode.",
        },
        "allowedPrompts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": ["Bash"]},
                    "prompt": {"type": "string"},
                },
                "required": ["tool", "prompt"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["plan", "approved", "previousMode", "mode"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# ask-user-question 契约（packages/contracts/src/tools/ask-user-question.ts）
# ---------------------------------------------------------------------------

class AskUserQuestionOption(TypedDict, total=False):
    """Ported from ``AskUserQuestionOptionSchema`` / ``type AskUserQuestionOption``."""

    label: str
    description: str
    preview: str


class AskUserQuestion(TypedDict, total=False):
    """Ported from ``AskUserQuestionSchema`` / ``type AskUserQuestion``."""

    question: str
    header: str
    options: list[AskUserQuestionOption]
    multiSelect: bool


class AskUserQuestionAnnotation(TypedDict, total=False):
    """Ported from ``AskUserQuestionAnnotationSchema``."""

    preview: str
    notes: str


class AskUserQuestionOutput(TypedDict, total=False):
    """Ported from ``AskUserQuestionOutputSchema`` / ``type AskUserQuestionOutput``."""

    questions: list[AskUserQuestion]
    answers: dict[str, str]
    annotations: dict[str, AskUserQuestionAnnotation]


_ASK_OPTION_JSON = {
    "type": "object",
    "properties": {
        "label": {
            "type": "string",
            "description": (
                "The display text for this option that the user will see and select. Should be "
                "concise (1-5 words) and clearly describe the choice."
            ),
        },
        "description": {
            "type": "string",
            "description": (
                "Explanation of what this option means or what will happen if chosen. Useful for "
                "providing context about trade-offs or implications."
            ),
        },
        "preview": {
            "type": "string",
            "description": (
                "Optional preview content rendered when this option is focused. Use for mockups, "
                "code snippets, or visual comparisons that help users compare options. See the "
                "tool description for the expected content format."
            ),
        },
    },
    "required": ["label", "description"],
    "additionalProperties": False,
}

_ASK_QUESTION_JSON = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": (
                'The complete question to ask the user. Should be clear, specific, and end with a '
                'question mark. Example: "Which library should we use for date formatting?" If '
                'multiSelect is true, phrase it accordingly, e.g. "Which features do you want to '
                'enable?"'
            ),
        },
        "header": {
            "type": "string",
            "description": (
                'Very short label displayed as a chip/tag (max 12 chars). Examples: "Auth method", '
                '"Library", "Approach".'
            ),
        },
        "options": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "items": _ASK_OPTION_JSON,
            "description": (
                "The available choices for this question. Must have 2-4 options. Each option should "
                "be a distinct, mutually exclusive choice (unless multiSelect is enabled). There "
                "should be no 'Other' option, that will be provided automatically."
            ),
        },
        "multiSelect": {
            "type": "boolean",
            "default": False,
            "description": (
                "Set to true to allow the user to select multiple options instead of just one. Use "
                "when choices are not mutually exclusive."
            ),
        },
    },
    "required": ["question", "header", "options", "multiSelect"],
    "additionalProperties": False,
}

_ASK_ANNOTATION_JSON = {
    "type": "object",
    "properties": {
        "preview": {
            "type": "string",
            "description": "The preview content of the selected option, if the question used previews.",
        },
        "notes": {"type": "string", "description": "Free-text notes the user added to their selection."},
    },
    "additionalProperties": False,
}

ASK_USER_QUESTION_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": _ASK_QUESTION_JSON,
            "description": "Questions to ask the user (1-4 questions)",
        },
        "answers": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "User answers collected by the permission component",
        },
        "annotations": {
            "type": "object",
            "additionalProperties": _ASK_ANNOTATION_JSON,
            "description": (
                "Optional per-question annotations from the user (e.g., notes on preview "
                "selections). Keyed by question text."
            ),
        },
        "metadata": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        'Optional identifier for the source of this question (e.g., "remember" for '
                        "/remember command). Used for analytics tracking."
                    ),
                }
            },
            "additionalProperties": False,
            "description": (
                "Optional metadata for tracking and analytics purposes. Not displayed to user."
            ),
        },
    },
    "required": ["questions"],
    "additionalProperties": False,
}

ASK_USER_QUESTION_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "questions": {"type": "array", "minItems": 1, "maxItems": 4, "items": _ASK_QUESTION_JSON},
        "answers": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "User answers keyed by question text.",
        },
        "annotations": {
            "type": "object",
            "additionalProperties": _ASK_ANNOTATION_JSON,
            "description": "Optional per-question annotations keyed by question text.",
        },
    },
    "required": ["questions", "answers"],
    "additionalProperties": False,
}

# PORT: ``AskUserQuestionAnsweredInputSchema`` 是 ``AskUserQuestionInputSchema`` 的
# ``superRefine`` 增强（answers 必须已收集、逐条非空白）。精化逻辑在 ask_user_question.py
# 内就地还原；这里保留同一份 JSON Schema 占位。
ASK_USER_QUESTION_ANSWERED_INPUT_SCHEMA = ASK_USER_QUESTION_INPUT_JSON_SCHEMA


# ---------------------------------------------------------------------------
# skill 契约（packages/contracts/src/tools/skill.ts）
# ---------------------------------------------------------------------------

class SkillRuntimeInput(TypedDict, total=False):
    """Ported from ``SkillInputSchema`` / ``type SkillRuntimeInput``."""

    skill: str
    args: str


SKILL_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "skill": {
            "type": "string",
            "description": "The name of a skill from the available-skills list. Do not guess names.",
        },
        "args": {"type": "string", "description": "Optional arguments for the skill"},
    },
    "required": ["skill"],
    "additionalProperties": False,
}

SKILL_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "oneOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string"},
                "baseDirectory": {"type": "string"},
                "truncated": {"type": "boolean"},
            },
            "required": ["name", "content", "baseDirectory", "truncated"],
            "additionalProperties": False,
        },
    ],
}


# ---------------------------------------------------------------------------
# send-message 契约（packages/contracts/src/tools/send-message.ts）
# ---------------------------------------------------------------------------

class SendMessageInput(TypedDict, total=False):
    """Ported from ``SendMessageInputSchema`` / ``type SendMessageInput``."""

    to: str
    summary: str
    message: str


class SendMessageOutput(TypedDict, total=False):
    """Ported from ``SendMessageOutputSchema`` / ``type SendMessageOutput``."""

    status: str
    messageId: str
    agentId: str
    delivery: str
    error: str
    message: str
    outputFile: str
    taskId: str


SEND_MESSAGE_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "to": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "Recipient: local agent ID returned by Agent (format agent_<uuid>).",
        },
        "summary": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "A 5-10 word summary shown as a preview in the UI.",
        },
        "message": {
            "type": "string",
            "minLength": 1,
            "maxLength": SEND_MESSAGE_MAX_CONTENT_CHARS,
            "description": "Plain text message content",
        },
    },
    "required": ["to", "summary", "message"],
    "additionalProperties": False,
}

SEND_MESSAGE_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "failed"]},
        "messageId": {"type": "string"},
        "agentId": {"type": "string"},
        "delivery": {"type": "string", "enum": ["queued", "steered", "resumed_background"]},
        "error": {"type": "string"},
        "message": {"type": "string"},
        "outputFile": {"type": "string"},
        "taskId": {"type": "string"},
    },
    "required": ["status", "messageId"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# respond-to-coordinator 契约
# （packages/contracts/src/tools/respond-to-coordinator.ts）
# ---------------------------------------------------------------------------

class RespondToCoordinatorInput(TypedDict, total=False):
    """Ported from ``RespondToCoordinatorInputSchema``."""

    summary: str
    message: str


class RespondToCoordinatorOutput(TypedDict, total=False):
    """Ported from ``RespondToCoordinatorOutputSchema``."""

    status: str
    responseId: str
    message: str
    error: str


RESPOND_TO_COORDINATOR_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "Short summary of the response.",
        },
        "message": {
            "type": "string",
            "minLength": 1,
            "maxLength": RESPOND_TO_COORDINATOR_MAX_CONTENT_CHARS,
            "description": "Response or progress update for the coordinator.",
        },
    },
    "required": ["summary", "message"],
    "additionalProperties": False,
}

RESPOND_TO_COORDINATOR_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "failed"]},
        "responseId": {"type": "string"},
        "message": {"type": "string"},
        "error": {"type": "string"},
    },
    "required": ["status", "responseId", "message"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# submit-result 契约（packages/contracts/src/tools/submit-result.ts）
# ---------------------------------------------------------------------------

class SubmitResultInput(TypedDict, total=False):
    """Ported from ``SubmitResultInputSchema`` / ``type SubmitResultInput``."""

    result: Any


class SubmitResultOutput(TypedDict, total=False):
    """Ported from ``SubmitResultOutputSchema`` / ``type SubmitResultOutput``."""

    status: str


@dataclass(slots=True)
class SubmitViolation:
    """Ported from ``SubmitViolation``（@zcode/contracts）。

    # PORT: 真实来源 packages/contracts/src/interfaces/dynamic-workflow-run.port.ts；
    # 该契约模块未移植，先落在本文件。
    """

    path: str
    expected: str
    got: str


SUBMIT_RESULT_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "result": {
            "description": (
                "The structured result for this ask, matching the JSON schema given in the ask "
                "instructions."
            )
        }
    },
    "required": ["result"],
    "additionalProperties": False,
}

SUBMIT_RESULT_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {"status": {"const": "accepted"}},
    "required": ["status"],
    "additionalProperties": False,
}


def typed_submit_result_input_schema(result_schema: JsonSchema) -> JsonSchema:
    """Mirrors ``typedSubmitResultInputSchema(resultSchema)`` (tools/submit-result.ts)。"""
    return {
        "type": "object",
        "properties": {
            "result": {
                "description": "The structured result for this ask.",
                **result_schema,
            }
        },
        "required": ["result"],
        "additionalProperties": False,
        "$schema": TOOL_JSON_SCHEMA_VERSION,
    }


# ---------------------------------------------------------------------------
# list-models 契约（packages/contracts/src/tools/list-models.ts）
# ---------------------------------------------------------------------------

class ListModelsEntry(TypedDict, total=False):
    """Ported from ``ListModelsEntrySchema`` / ``type ListModelsEntry``."""

    id: str
    providerId: str
    modelId: str
    providerLabel: str
    reasoningLevels: list[str]
    defaultReasoningLevel: str
    contextWindow: float
    disabledReason: str


class ListModelsOutput(TypedDict, total=False):
    """Ported from ``ListModelsOutputSchema`` / ``type ListModelsOutput``."""

    current: str
    models: list[ListModelsEntry]


LIST_MODELS_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

LIST_MODELS_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "current": {"type": "string"},
        "models": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "providerId": {"type": "string"},
                    "modelId": {"type": "string"},
                    "providerLabel": {"type": "string"},
                    "reasoningLevels": {"type": "array", "items": {"type": "string"}},
                    "defaultReasoningLevel": {"type": "string"},
                    "contextWindow": {"type": "number"},
                    "disabledReason": {"type": "string"},
                },
                "required": ["id", "providerId", "modelId", "reasoningLevels"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["models"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# runtime-task/registry（packages/core/src/runtime-task/registry.ts）
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RuntimeTaskSnapshot:
    """Ported from ``interface RuntimeTaskSnapshot``（``extends SubagentTaskSnapshot``）。

    # PORT: ``runtime-task/registry.ts`` 未移植；这里只保留 handlers 读取到的字段，
    # 字段名按本包约定转 snake_case，语义不变。
    """

    task_id: str
    type: str
    status: str
    description: str
    output_file: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
    prompt: Optional[str] = None
    # workflow run 产物的序列化文本；TaskOutput 的投影只读得到 registry 条目。
    result_text: Optional[str] = None
    # 标记完成结果是否已成功交付（completion notification 的 claim）。
    notified: Optional[bool] = None
    output: Optional[Any] = None
    parent_session_id: Optional[SessionId] = None
    branch_generation: Optional[int] = None
    is_backgrounded: Optional[bool] = None
    task_type: Optional[str] = None
    stop_initiator: Optional[str] = None  # "user" | "model"
    usage: Optional[Any] = None


# PORT: 这里原先放的是 ``BackgroundExecutionSnapshot`` 的**局部投影**（只保留
# ``ExecutionPort.getBackgroundTask`` 读到的字段）。``interfaces/execution.port.ts``
# 已于本轮移植到 ``contracts/execution.py``，局部投影随之删除，改为从真实契约再导出，
# 避免同名类型出现两份定义。
from ...contracts.execution import (  # noqa: E402  (分区内再导出，见文件头说明)
    BackgroundExecutionSnapshot,
)


@dataclass(slots=True)
class TaskOutputFileRead:
    """Mirrors ``interface TaskOutputFileRead`` (task-output-bash.ts)."""

    available: bool
    content: str
    truncated: bool


# ---------------------------------------------------------------------------
# model-catalog / model-selection
# （packages/contracts/src/interfaces/model-catalog.port.ts、@zcode/shared/model-selection.ts）
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ModelCatalogEntry:
    """Ported from ``ModelCatalogEntry``（@zcode/contracts）。

    # PORT: ``interfaces/model-catalog.port.ts`` 未移植，先落在本文件。
    """

    provider_id: str
    model_id: str
    reasoning_levels: list[str]
    provider_label: Optional[str] = None
    default_reasoning_level: Optional[str] = None
    context_window: Optional[float] = None
    disabled_reason: Optional[str] = None
    # 当前会话正在用的那一条（``[current]`` 标记的来源）。
    current: Optional[bool] = None


@dataclass(slots=True)
class ModelSelectionOptions:
    """``ModelSelection.options`` 的形状。"""

    reasoning_level: Optional[str] = None


@dataclass(slots=True)
class ModelSelection:
    """Ported from ``ModelSelection``（@zcode/shared/model-selection.ts）。"""

    provider_id: str
    model_id: str
    options: Optional[ModelSelectionOptions] = None


# PORT: 真实来源 ``@zcode/shared``（源码不在本仓）；按 model-reference.ts 的调用点还原。
# provider 分隔符为 ``/``，档位分隔符为 ``$``。
ZCODE_MODEL_REASONING_SEPARATOR = "$"


def format_model_picker_value(selection: ModelSelection) -> str:
    """Mirrors ``formatModelPickerValue(selection)``（@zcode/shared/model-selection.ts）。

    # PORT: ``@zcode/shared`` 未移植，按 picker 形 ``providerId/modelId[$level]`` 还原文案。
    """
    canonical = f"{selection.provider_id}/{selection.model_id}"
    level = selection.options.reasoning_level if selection.options is not None else None
    if level:
        canonical += f"{ZCODE_MODEL_REASONING_SEPARATOR}{level}"
    return canonical


class ModelPickerParseError(Exception):
    """Thrown by :func:`parse_model_picker_value` on a malformed picker value."""


def parse_model_picker_value(canonical: str) -> ModelSelection:
    """Mirrors ``parseModelPickerValue(canonical)``（@zcode/shared/model-selection.ts）。

    # PORT: ``@zcode/shared`` 未移植，按 model-reference.ts 用到的形（``provider/model`` 与
    # 可选 ``$level``）还原；畸形串抛 :class:`ModelPickerParseError`。
    """
    provider_separator_index = canonical.find("/")
    if provider_separator_index <= 0 or provider_separator_index >= len(canonical) - 1:
        raise ModelPickerParseError(f"Malformed model picker value: {canonical}")

    provider_id = canonical[:provider_separator_index]
    rest = canonical[provider_separator_index + 1 :]
    level_separator_index = rest.find(ZCODE_MODEL_REASONING_SEPARATOR)
    if level_separator_index < 0:
        return ModelSelection(provider_id=provider_id, model_id=rest)
    if level_separator_index == 0 or level_separator_index >= len(rest) - 1:
        raise ModelPickerParseError(f"Malformed model picker value: {canonical}")
    return ModelSelection(
        provider_id=provider_id,
        model_id=rest[:level_separator_index],
        options=ModelSelectionOptions(reasoning_level=rest[level_separator_index + 1 :]),
    )


# ---------------------------------------------------------------------------
# glob 契约（packages/contracts/src/tools/glob.ts）
# ---------------------------------------------------------------------------

# PORT: 真实来源 packages/contracts/src/tools/glob.ts。
GLOB_TOOL_NAME = "Glob"

class GlobInput(TypedDict, total=False):
    """Ported from ``GlobInputSchema`` / ``type GlobInput``."""

    pattern: str
    path: str


class GlobOutput(TypedDict, total=False):
    """Ported from ``GlobOutputSchema`` / ``type GlobOutput``."""

    filenames: list[str]
    numFiles: int
    truncated: bool
    durationMs: int


GLOB_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": 'Glob pattern to match files (e.g. "**/*.js" or "src/**/*.ts")',
        },
        "path": {
            "type": "string",
            "description": "Optional root path to search from",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}

GLOB_INPUT_SCHEMA = GLOB_INPUT_JSON_SCHEMA

GLOB_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "filenames": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Matching file paths sorted by modification time",
        },
        "numFiles": {"type": "integer", "minimum": 0},
        "truncated": {"type": "boolean"},
        "durationMs": {"type": "integer", "minimum": 0},
    },
    "required": ["filenames", "numFiles", "truncated", "durationMs"],
    "additionalProperties": False,
}

GLOB_OUTPUT_SCHEMA = GLOB_OUTPUT_JSON_SCHEMA


# ---------------------------------------------------------------------------
# grep 契约（packages/contracts/src/tools/grep.ts）
# ---------------------------------------------------------------------------

# PORT: 真实来源 packages/contracts/src/tools/grep.ts。
GREP_TOOL_NAME = "Grep"

# PORT: Python's class-body TypedDict rejects non-identifier keys (e.g. "-C"),
# so this schema uses the functional syntax to keep every flag key verbatim.
GrepInput = TypedDict(
    "GrepInput",
    {
        "pattern": str,
        "path": Optional[str],
        "glob": Optional[str],
        "output_mode": Optional[str],  # "content" | "files_with_matches" | "count"
        "context": Optional[int],
        "-C": Optional[int],
        "-B": Optional[int],
        "-A": Optional[int],
        "-n": Optional[bool],
        "-o": Optional[bool],
        "-i": Optional[bool],
        "type": Optional[str],
        "head_limit": Optional[int],
        "offset": Optional[int],
        "multiline": Optional[bool],
    },
    total=False,
)


# ---------------------------------------------------------------------------
# FileSystemPort 搜索契约
# （packages/contracts/src/interfaces/file-system.port.ts）
# ---------------------------------------------------------------------------
# PORT: 这批类型早先在本文件就地落成（当时的临时落点）。``contracts/file_system.py``
# 现已按 ``interfaces/file-system.port.ts`` 完整移植（含此前全仓无归属的
# ``FileSystemStatResult`` / ``FileSystemRevision`` / ``FileSystemPort`` 等），故此处改为
# 再导出，避免两份定义漂移。
from ...contracts.file_system import (  # noqa: E402
    FileSystemOperationOptions,
    FileSystemSearchFilesRequest,
    FileSystemSearchFilesResult,
    FileSystemSearchTextEntry,
    FileSystemSearchTextRequest,
    FileSystemSearchTextResult,
    FileSystemTextSearchOutputMode,
)

# PORT: 兼容别名——上游 grep.ts 的形参类型写作 ``FileSystemSearchTextEntry``；
# 本文件早先以 ``GrepFileEntry`` 落点，保留别名以免破坏既有引用点。
GrepFileEntry = FileSystemSearchTextEntry


class GrepOutput(TypedDict, total=False):
    """Ported from ``GrepOutputSchema`` / ``type GrepOutput``."""

    mode: str
    filenames: list[str]
    numFiles: int
    numMatches: Optional[int]
    numLines: Optional[int]
    content: Optional[str]
    truncated: bool
    appliedLimit: Optional[int]
    appliedOffset: Optional[int]
    durationMs: int


GREP_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Regex pattern to search for",
        },
        "path": {
            "type": "string",
            "description": "Optional path to search in",
        },
        "glob": {
            "type": "string",
            "description": "Optional glob pattern to filter files (e.g. '**/*.tsx')",
        },
        "output_mode": {
            "type": "string",
            "enum": ["content", "files_with_matches", "count"],
            "description": "Output mode: 'content' (matching lines), 'files_with_matches' (paths only), 'count'",
        },
        "context": {
            "type": "integer",
            "description": "Lines of context before and after match",
        },
        "-C": {
            "type": "integer",
            "description": "Context lines (shorthand)",
        },
        "-B": {
            "type": "integer",
            "description": "Lines before match",
        },
        "-A": {
            "type": "integer",
            "description": "Lines after match",
        },
        "-n": {
            "type": "boolean",
            "description": "Show line numbers",
        },
        "-o": {
            "type": "boolean",
            "description": "Show only matching part",
        },
        "-i": {
            "type": "boolean",
            "description": "Case insensitive",
        },
        "type": {
            "type": "string",
            "description": "Filter by file type (e.g. 'js', 'py', 'rust')",
        },
        "head_limit": {
            "type": "integer",
            "description": "Maximum number of results",
        },
        "offset": {
            "type": "integer",
            "description": "Result offset for pagination",
        },
        "multiline": {
            "type": "boolean",
            "description": "Patterns can span multiple lines",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}

GREP_INPUT_SCHEMA = GREP_INPUT_JSON_SCHEMA

GREP_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "mode": {"type": "string"},
        "filenames": {"type": "array", "items": {"type": "string"}},
        "numFiles": {"type": "integer", "minimum": 0},
        "numMatches": {"type": "integer"},
        "numLines": {"type": "integer"},
        "content": {"type": "string"},
        "truncated": {"type": "boolean"},
        "appliedLimit": {"type": "integer"},
        "appliedOffset": {"type": "integer"},
        "durationMs": {"type": "integer", "minimum": 0},
    },
    "required": ["mode", "filenames", "numFiles", "truncated", "durationMs"],
    "additionalProperties": False,
}

GREP_OUTPUT_SCHEMA = GREP_OUTPUT_JSON_SCHEMA


# ---------------------------------------------------------------------------
# webfetch 契约（packages/contracts/src/tools/webfetch.ts）
# ---------------------------------------------------------------------------

# PORT: 真实来源 packages/contracts/src/tools/webfetch.ts。
WEBFETCH_TOOL_NAME = "WebFetch"
DEFAULT_WEBFETCH_TIMEOUT_MS = 60_000
MAX_WEBFETCH_MODEL_BYTES = 100_000


class WebFetchInput(TypedDict, total=False):
    """Ported from ``WebFetchInputSchema`` / ``type WebFetchInput``."""

    url: str
    prompt: str


class WebFetchOutput(TypedDict, total=False):
    """Ported from ``WebFetchOutputSchema`` / ``type WebFetchOutput``."""

    url: str
    finalUrl: str
    status: int
    statusText: str
    contentType: str
    bytes: int
    durationMs: int
    result: str
    cacheHit: bool
    truncated: bool
    redirects: list[str]
    artifactUri: Optional[str]
    artifactPath: Optional[str]


WEBFETCH_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "URL to fetch",
        },
        "prompt": {
            "type": "string",
            "description": "Question to answer about the fetched content",
        },
    },
    "required": ["url", "prompt"],
    "additionalProperties": False,
}

WEBFETCH_INPUT_SCHEMA = WEBFETCH_INPUT_JSON_SCHEMA

WEBFETCH_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "finalUrl": {"type": "string"},
        "status": {"type": "integer"},
        "statusText": {"type": "string"},
        "contentType": {"type": "string"},
        "bytes": {"type": "integer"},
        "durationMs": {"type": "integer", "minimum": 0},
        "result": {"type": "string"},
        "cacheHit": {"type": "boolean"},
        "truncated": {"type": "boolean"},
        "redirects": {"type": "array", "items": {"type": "string"}},
        "artifactUri": {"type": "string"},
        "artifactPath": {"type": "string"},
    },
    "required": ["url", "finalUrl", "status", "statusText", "contentType", "bytes", "durationMs", "result", "cacheHit", "truncated", "redirects"],
    "additionalProperties": False,
}

WEBFETCH_OUTPUT_SCHEMA = WEBFETCH_OUTPUT_JSON_SCHEMA


# ---------------------------------------------------------------------------
# websearch 契约（packages/contracts/src/tools/websearch.ts）
# ---------------------------------------------------------------------------

# PORT: 真实来源 packages/contracts/src/tools/websearch.ts。
WEBSEARCH_TOOL_NAME = "WebSearch"


class WebSearchInput(TypedDict, total=False):
    """Ported from ``WebSearchInputSchema`` / ``type WebSearchInput``."""

    query: str
    allowed_domains: Optional[list[str]]
    blocked_domains: Optional[list[str]]


class WebSearchOutput(TypedDict, total=False):
    """Ported from ``WebSearchOutputSchema`` / ``type WebSearchOutput``."""

    query: str
    results: list[dict[str, Any]]
    durationMs: int


WEBSEARCH_INPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query"},
        "allowed_domains": {"type": "array", "items": {"type": "string"}},
        "blocked_domains": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["query"],
    "additionalProperties": False,
}

WEBSEARCH_INPUT_SCHEMA = WEBSEARCH_INPUT_JSON_SCHEMA

WEBSEARCH_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "$schema": TOOL_JSON_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "results": {"type": "array", "items": {"type": "object"}},
        "durationMs": {"type": "integer", "minimum": 0},
    },
    "required": ["query", "results", "durationMs"],
    "additionalProperties": False,
}

WEBSEARCH_OUTPUT_SCHEMA = WEBSEARCH_OUTPUT_JSON_SCHEMA


# ---------------------------------------------------------------------------
# plan-mode 契约（packages/contracts/src/tools/plan-mode.ts）
# ---------------------------------------------------------------------------

# PORT: 真实来源 packages/contracts/src/tools/plan-mode.ts。
ENTER_PLAN_MODE_TOOL_NAME = "EnterPlanMode"
EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"


# ---------------------------------------------------------------------------
# skill 契约（packages/contracts/src/tools/skill.ts）
# ---------------------------------------------------------------------------

# PORT: 真实来源 packages/contracts/src/tools/skill.ts。
SKILL_TOOL_NAME = "Skill"


# ---------------------------------------------------------------------------
# 端口占位：未移植能力
# ---------------------------------------------------------------------------

# PORT: 原先此处是 ``FileSystemPortError`` 的本地替身（只有 ``message`` + ``code``）。
# ``contracts/file_system.py`` 已按 ``interfaces/file-system.port.ts`` 忠实移植该 class
# （``code`` / ``path`` / ``cause`` + ``name``），故改为再导出权威定义。
from ...contracts.file_system import (  # noqa: E402
    FileSystemPortError,
    create_file_system_error,
    is_file_system_port_error,
)


async def write_approved_plan_file(input: dict[str, Any]) -> dict[str, str]:
    """Mirrors ``writeApprovedPlanFile(input)``
    (packages/core/src/runtime/helpers/plan-file-continuity.ts)。

    # PORT: ``runtime/helpers/plan-file-continuity.ts`` 未移植。保留签名与端口占位，
    # 真实实现在宿主 runtime 层；这里按规则抛 ``NotImplementedError``。
    """
    raise NotImplementedError(
        "writeApprovedPlanFile: packages/core/src/runtime/helpers/plan-file-continuity.ts "
        "尚未移植，暂以端口占位"
    )


# PORT: 真实来源 packages/core/src/tool/handlers/off-peak.ts（``assertNotOffPeakTurn``）。
# off-peak.ts 属 scope: non-coding（办公自动化），不整体翻译；这里保留该被依赖的守卫语义。
OFF_PEAK_SEND_MESSAGE_HINT_SUFFIX = (
    "Spawn a new foreground Agent with the full context instead of resuming a completed one."
)


def assert_not_off_peak_turn(
    context: Any,
    tool_name: str,
    options: Optional[dict[str, Any]] = None,
) -> None:
    """Mirrors ``assertNotOffPeakTurn(context, toolName, options?)``
    (packages/core/src/tool/handlers/off-peak.ts)。
    """
    if not context.off_peak_turn:
        return
    hint = f" {options['hint']}" if options and options.get("hint") else ""
    raise create_core_error(
        CoreErrorType.PERMISSION_DENIED,
        f"{tool_name} is not allowed while running an idle-time task.{hint}",
        {
            "context": {
                "toolCallId": context.tool_call_id,
                "toolName": tool_name,
            },
            "recoverable": options.get("recoverable", False) if options else False,
            "retryable": False,
        },
    )


# ===========================================================================
# Read / Write / Edit tool constants & schemas
# Ported from packages/contracts/src/tools/{read,write,edit}.ts
# ===========================================================================

READ_MAX_FILE_SIZE_BYTES = 256 * 1024
READ_MAX_OUTPUT_TOKENS = 25_000
READ_DEFAULT_MAX_LINES = 2_000
READ_IMAGE_MAX_BASE64_BYTES = 5 * 1024 * 1024
READ_IMAGE_TARGET_BYTES = (READ_IMAGE_MAX_BASE64_BYTES * 3) // 4
READ_IMAGE_MAX_DIMENSION = 2000
READ_IMAGE_MAX_INPUT_BYTES = 20 * 1024 * 1024
READ_IMAGE_TOKEN_TO_BASE64_CHAR_RATIO = 0.125

READ_TOOL_NAME = "Read"
WRITE_TOOL_NAME = "Write"
EDIT_TOOL_NAME = "Edit"

READ_INPUT_JSON_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string", "description": "The absolute path to the file to read"},
        "offset": {
            "type": "number",
            "description": "The line number to start reading from. Only provide if the file is too large to read at once",
        },
        "limit": {
            "type": "number",
            "description": "The number of lines to read. Only provide if the file is too large to read at once.",
        },
        "pages": {"type": "string"},
    },
    "required": ["file_path"],
    "additionalProperties": False,
}
READ_INPUT_SCHEMA = READ_INPUT_JSON_SCHEMA

READ_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "filePath": {"type": "string"},
        "content": {"type": "string"},
        "numLines": {"type": "number"},
        "startLine": {"type": "number"},
        "totalLines": {"type": "number"},
    },
    "required": ["type"],
}
READ_OUTPUT_SCHEMA = READ_OUTPUT_JSON_SCHEMA

WRITE_INPUT_JSON_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "The absolute path to the file to write (must be absolute, not relative)",
        },
        "content": {"type": "string", "description": "The content to write to the file"},
    },
    "required": ["file_path", "content"],
    "additionalProperties": False,
}
WRITE_INPUT_SCHEMA = WRITE_INPUT_JSON_SCHEMA

WRITE_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["create", "update"]},
        "filePath": {"type": "string"},
        "content": {"type": "string"},
        "structuredPatch": {"type": "array"},
        "originalFile": {"type": ["string", "null"]},
    },
    "required": ["type", "filePath", "content", "structuredPatch", "originalFile"],
}
WRITE_OUTPUT_SCHEMA = WRITE_OUTPUT_JSON_SCHEMA

WRITE_ERROR_CODE = {
    "FILE_NOT_READ": "write_file_not_read",
    "PARTIAL_READ": "write_partial_read",
    "STALE_FILE": "write_stale_file",
    "PERMISSION_DENIED": "write_permission_denied",
    "SECRET_DETECTED": "write_secret_detected",
    "IO_ERROR": "write_io_error",
    "DIRECTORY_NOT_FOUND": "write_directory_not_found",
}

EDIT_INPUT_JSON_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string", "description": "The absolute path to the file to modify"},
        "old_string": {"type": "string", "description": "The text to replace"},
        "new_string": {
            "type": "string",
            "description": "The text to replace it with (must be different from old_string)",
        },
        "replace_all": {
            "type": "boolean",
            "description": "Replace all occurrences of old_string (default false)",
            "default": False,
        },
    },
    "required": ["file_path", "old_string", "new_string"],
    "additionalProperties": False,
}
EDIT_INPUT_SCHEMA = EDIT_INPUT_JSON_SCHEMA

EDIT_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "filePath": {"type": "string"},
        "oldString": {"type": "string"},
        "newString": {"type": "string"},
        "originalFile": {"type": "string"},
        "structuredPatch": {"type": "array"},
        "userModified": {"type": "boolean"},
        "replaceAll": {"type": "boolean"},
    },
    "required": ["filePath", "oldString", "newString", "originalFile", "structuredPatch"],
}
EDIT_OUTPUT_SCHEMA = EDIT_OUTPUT_JSON_SCHEMA

EDIT_ERROR_CODE = {
    "NO_CHANGE": 1,
    "FILE_EXISTS_NO_OLD_STRING": 3,
    "FILE_NOT_EXIST": 4,
    "NOTEBOOK_FILE": 5,
    "FILE_NOT_READ": 6,
    "STALE_FILE": 7,
    "OLD_STRING_NOT_FOUND": 8,
    "AMBIGUOUS_REPLACE": 9,
    "FILE_TOO_LARGE": 10,
    "INVALID_PATH": 13,
}


# ---------------------------------------------------------------------------
# bash 契约
# （packages/contracts/src/tools/bash.ts）
# ---------------------------------------------------------------------------
# Reference: shell tool input / output shape

# PORT: 真实来源同上文件的 ``MAX_BASH_TIMEOUT_MS``。
MAX_BASH_TIMEOUT_MS = 600_000
# 真实来源同文件的 ``TRUE_BOOLEAN_STRINGS`` / ``FALSE_BOOLEAN_STRINGS``。
TRUE_BOOLEAN_STRINGS: frozenset[str] = frozenset(["true", "1", "yes", "y", "on"])
FALSE_BOOLEAN_STRINGS: frozenset[str] = frozenset(["false", "0", "no", "n", "off"])
# PORT: 真实来源同文件的 ``BASH_DESCRIPTION_FIELD_PROMPT``（TS 用 ``[...].join("\n")``，
# Python 用 ``"\n".join([...])``）。
BASH_DESCRIPTION_FIELD_PROMPT = "\n".join(
    [
        'Clear, concise description of what this command does in active voice. Never use words like "complex" or "risk" in the description - just describe what it does.',
        "",
        "For simple commands (git, npm, standard CLI tools), keep it brief (5-10 words):",
        '- ls → "List files in current directory"',
        '- git status → "Show working tree status"',
        '- npm install → "Install package dependencies"',
        "",
        "For commands that are harder to parse at a glance (piped commands, obscure flags, etc.), add enough context to clarify what it does:",
        '- find . -name "*.tmp" -exec rm {} \\; → "Find and delete all .tmp files recursively"',
        '- git reset --hard origin/main → "Discard all local changes and match remote main"',
        "- curl -s url | jq '.data[]' → \"Fetch JSON from URL and extract data array elements\"",
    ]
)

# PORT: 真实来源 packages/contracts/src/tool/... 无；工具名在 core 侧常量表。
# ``Bash`` 是 provider 可见名，直接取自 bash.ts 的工具身份（``name: "Bash"``）。
BASH_TOOL_NAME = "Bash"


@dataclass(slots=True)
class BashInput:
    """Mirrors ``type BashInput = z.infer<typeof BashInputSchema>``."""

    command: str
    # 可选超时（毫秒，上限 600000）。
    timeout: Optional[int] = None
    # 该命令做什么的简明主动语态描述。
    description: Optional[str] = None
    # true 表示后台运行；稍后用 Read 读输出。
    run_in_background: Optional[bool] = None
    # true 表示危险地绕过 sandbox 模式、不加沙箱运行命令。
    # PORT: 字段名保持 TS 原样（provider 可见的 JSON key 就是 camelCase）。
    dangerously_disable_sandbox: Optional[bool] = None


@dataclass(slots=True)
class BashOutput:
    """Mirrors ``interface BashOutput``."""

    # 命令的标准输出。
    stdout: str
    # 命令的标准错误输出。
    stderr: str
    # 结构化执行状态。
    # "completed" | "failed" | "timed_out" | "cancelled" | "spawn_error" | "backgrounded"
    status: Optional[str] = None
    # 进程退出码。非零退出在此表示，而不抛异常。
    exit_code: Optional[int] = None
    # 进程退出信号，可用时给出。
    signal: Optional[str] = None
    # 是否是超时策略终止了命令。
    timed_out: Optional[bool] = None
    # 是否是取消终止了命令。
    cancelled: Optional[bool] = None
    # stdout 是否为内联返回被截断。
    stdout_truncated: Optional[bool] = None
    # stderr 是否为内联返回被截断。
    stderr_truncated: Optional[bool] = None
    # 观察到的 stdout 总字节数。
    stdout_bytes: Optional[int] = None
    # 观察到的 stderr 总字节数。
    stderr_bytes: Optional[int] = None
    # 大型 MCP 工具输出的原始输出文件路径。
    raw_output_path: Optional[str] = None
    # 命令是否被中断。
    interrupted: bool = False
    # stdout 是否含图像数据。
    is_image: Optional[bool] = None
    # 命令后台运行时的后台任务 ID。
    background_task_id: Optional[str] = None
    # 用户用 Ctrl+B 手动后台化时为 true。
    backgrounded_by_user: Optional[bool] = None
    # assistant 模式自动后台化了一个长时间阻塞命令时为 true。
    assistant_auto_backgrounded: Optional[bool] = None
    # sandbox 模式是否被绕过。
    # PORT: TS 字段名 ``dangerouslyDisableSandbox``，保留原名以便与 provider 契约一致。
    dangerously_disable_sandbox: Optional[bool] = None
    # 对具有特殊含义的非错误退出码的语义解释。
    return_code_interpretation: Optional[str] = None
    # 命令是否预期成功时无输出。
    no_output_expected: Optional[bool] = None
    # 结构化内容块。
    structured_content: Optional[list[Any]] = None
    # 输出过大无法内联时，落在 tool-results 目录的完整输出路径。
    persisted_output_path: Optional[str] = None
    # stdout 写入 execution artifact 时的持久化路径。
    stdout_persisted_output_path: Optional[str] = None
    # stderr 写入 execution artifact 时的持久化路径。
    stderr_persisted_output_path: Optional[str] = None
    # 输出过大无法内联时的总字节数。
    persisted_output_size: Optional[int] = None
    # stdout 写入 execution artifact 时的字节数。
    stdout_persisted_output_size: Optional[int] = None
    # stderr 写入 execution artifact 时的字节数。
    stderr_persisted_output_size: Optional[int] = None
    # 列出本次命令期间 mtime 变动的 readFileState 条目的、面向模型的说明。
    stale_read_file_state_hint: Optional[str] = None
    # gh 命令触发 GitHub API rate limit 时，追加给模型的 system-reminder。
    gh_rate_limit_hint: Optional[str] = None
    # PORT: TS 在 handler 返回的**对象字面量**上用 ``defineProperty`` 挂一个**非枚举**
    # ``perf``（``attachToolExecutionTelemetry``），因此它不在 ``interface BashOutput``
    # 里。本包 handler 返回 ``BashOutput`` dataclass，``slots=True`` 使挂载式写入失败，
    # 故落成显式字段——``BASH_OUTPUT_JSON_SCHEMA`` 已声明 ``perf`` 属性，语义一致。
    perf: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class BashToolCall:
    """Mirrors ``interface BashToolCall``."""

    id: ToolCallId
    input: BashInput
    trace_id: TraceId
    started_at: datetime
    # PORT: TS 字面量类型 ``name: "Bash"``。
    name: str = BASH_TOOL_NAME


@dataclass(slots=True)
class BashToolResult:
    """Mirrors ``interface BashToolResult``."""

    tool_call_id: ToolCallId
    output: BashOutput
    trace_id: TraceId
    duration_ms: int


@dataclass(slots=True)
class BackgroundTask:
    """Mirrors ``interface BackgroundTask``."""

    id: str
    command: str
    started_at: datetime
    # "running" | "completed" | "failed" | "cancelled"
    status: str
    description: Optional[str] = None
    exit_code: Optional[int] = None
    output_path: Optional[str] = None
    stderr_persisted_output_path: Optional[str] = None
    stdout_persisted_output_path: Optional[str] = None
    completed_at: Optional[datetime] = None


# PORT: TS 是 ``as const`` 对象 + 同名字符串联合类型。按本文件既有约定
# （WRITE_ERROR_CODE / EDIT_ERROR_CODE）落成 dict 常量，供调用点按名取码。
BASH_ERROR_CODE: dict[str, str] = {
    "COMMAND_NOT_PARSABLE": "bash_command_not_parsable",
    "COMMAND_TOO_COMPLEX": "bash_command_too_complex",
    "COMMAND_INJECTION_RISK": "bash_command_injection_risk",
    "READONLY_CHECK_FAILED": "bash_readonly_check_failed",
    "UNC_PATH_BLOCKED": "bash_unc_path_blocked",
    "HIGH_RISK_COMBINATION": "bash_high_risk_combination",
    "SANDBOX_VIOLATION": "bash_sandbox_violation",
    "TIMEOUT": "bash_timeout",
    "USER_INTERRUPTED": "bash_user_interrupted",
    "PROCESS_START_FAILED": "bash_process_start_failed",
    "NON_ZERO_EXIT": "bash_non_zero_exit",
    "OUTPUT_TOO_LARGE": "bash_output_too_large",
    "PERMISSION_DENIED": "bash_permission_denied",
}

# PORT: TS 里 ``type BashErrorCode`` 与 ``const BashErrorCode`` 同名（声明合并）。
# Python 用一个别名指向 dict，保持 ``BashErrorCode`` 这个名字可用。
BashErrorCode = BASH_ERROR_CODE


@dataclass(slots=True)
class ShellPermissionRule:
    """Mirrors ``type ShellPermissionRule``."""

    # "allow" | "deny"
    type: str
    pattern: str
    description: Optional[str] = None


@dataclass(slots=True)
class ShellPermissionPolicy:
    """Mirrors ``interface ShellPermissionPolicy``."""

    rules: list[ShellPermissionRule]
    allow_readonly: bool = False
    sandbox_enabled: bool = False
    dangerous_disable_sandbox_allowed: bool = False


# -----------------------------------------------
# Input Schema（zod → JSON Schema）
# -----------------------------------------------

BASH_INPUT_JSON_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "The command to execute"},
        "timeout": {
            "type": "number",
            "description": f"Optional timeout in milliseconds (max {MAX_BASH_TIMEOUT_MS})",
        },
        "description": {"type": "string", "description": BASH_DESCRIPTION_FIELD_PROMPT},
        "run_in_background": {
            "type": "boolean",
            "description": "Set to true to run this command in the background.",
        },
        "dangerouslyDisableSandbox": {
            "type": "boolean",
            "description": "Set to this to true to dangerously override sandbox mode and run commands without sandboxing.",
        },
    },
    "required": ["command"],
    # PORT: zod ``.strict()`` → ``additionalProperties: False``
    "additionalProperties": False,
}
BASH_INPUT_SCHEMA = BASH_INPUT_JSON_SCHEMA

BASH_OUTPUT_JSON_SCHEMA: JsonSchema = {
    "type": "object",
    "properties": {
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
        "status": {
            "type": "string",
            "enum": [
                "completed",
                "failed",
                "timed_out",
                "cancelled",
                "spawn_error",
                "backgrounded",
            ],
        },
        "exitCode": {"type": "integer"},
        "signal": {"type": "string"},
        "timedOut": {"type": "boolean"},
        "cancelled": {"type": "boolean"},
        "stdoutTruncated": {"type": "boolean"},
        "stderrTruncated": {"type": "boolean"},
        "stdoutBytes": {"type": "integer", "minimum": 0},
        "stderrBytes": {"type": "integer", "minimum": 0},
        "rawOutputPath": {"type": "string"},
        "interrupted": {"type": "boolean"},
        "isImage": {"type": "boolean"},
        "backgroundTaskId": {"type": "string"},
        "backgroundedByUser": {"type": "boolean"},
        "assistantAutoBackgrounded": {"type": "boolean"},
        "dangerouslyDisableSandbox": {"type": "boolean"},
        "returnCodeInterpretation": {"type": "string"},
        "noOutputExpected": {"type": "boolean"},
        "structuredContent": {"type": "array", "items": {}},
        "persistedOutputPath": {"type": "string"},
        "stdoutPersistedOutputPath": {"type": "string"},
        "stderrPersistedOutputPath": {"type": "string"},
        "persistedOutputSize": {"type": "integer", "minimum": 0},
        "stdoutPersistedOutputSize": {"type": "integer", "minimum": 0},
        "stderrPersistedOutputSize": {"type": "integer", "minimum": 0},
        "staleReadFileStateHint": {"type": "string"},
        "ghRateLimitHint": {"type": "string"},
        # PORT: ``ToolExecutionTelemetrySchema``（packages/contracts/src/tools/performance.ts）
        # 尚未移植；executor 侧以 dict 承载遥测，这里保留对象形状占位。
        "perf": {"type": "object"},
    },
    "required": ["stdout", "stderr", "interrupted"],
    "additionalProperties": False,
}
BASH_OUTPUT_SCHEMA = BASH_OUTPUT_JSON_SCHEMA


def semantic_number(value: Any) -> Any:
    """Mirrors the non-exported ``semanticNumber(schema)`` zod preprocess.

    # PORT: TS 返回一个 ``z.ZodEffects``；Python 里 zod 不存在，故保留**预处理语义**
    # （字符串数字 → number，非法输入原样透传交给 schema 层），供调用点在归一化阶段使用。
    """
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if len(trimmed) == 0:
        return value
    try:
        parsed = float(trimmed)
    except ValueError:
        return value
    # PORT: JS ``Number.isFinite`` —— NaN / ±Infinity 视为非法，原样透传。
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return value
    # 整数值回归 int，保持与 zod ``z.number()`` 的整数读法一致。
    return int(parsed) if parsed.is_integer() else parsed


def semantic_boolean(value: Any) -> Any:
    """Mirrors the non-exported ``semanticBoolean()`` zod preprocess.

    # PORT: 同 ``semantic_number``——保留预处理语义而非 zod 包装。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
        return value
    if not isinstance(value, str):
        return value

    normalized = value.strip().lower()
    if normalized in TRUE_BOOLEAN_STRINGS:
        return True
    if normalized in FALSE_BOOLEAN_STRINGS:
        return False
    return value




# ---------------------------------------------------------------------------
# 工具执行遥测契约
# （packages/contracts/src/tools/performance.ts）
# ---------------------------------------------------------------------------
# PORT: zod schema ``.strict()`` → ``TypedDict``。选 ``TypedDict`` 而非 ``@dataclass``：
# 该载荷随 ToolCallResult 事件落存储、在 ``tool_perf`` 里按 JSON 对象读写（``perf.get(...)``），
# 与 TS 运行时「普通对象字面量」同形。§1「仅作宽松载荷的 interface → TypedDict」。

# PORT: ``z.enum([...])`` → ``Literal``（值保持原字符串）。
ToolCommandStatus = Literal[
    "completed",
    "failed",
    "timed_out",
    "cancelled",
    "spawn_error",
    "backgrounded",
]


class CommandExecutionTelemetry(TypedDict, total=False):
    """Ported from ``CommandExecutionTelemetrySchema``."""

    runMs: int
    firstOutputMs: int
    noOutputMs: int
    exitCode: int
    timedOut: bool
    outputBytes: int
    # 只允许公开 Registry 中的可执行文件名或固定低基数桶；禁止放入原始命令或参数。
    category: str
    name: str
    count: int
    # 本地诊断字段；远端 Trace Exporter 必须显式忽略，避免成为高基数远端维度。
    hash: str


class FileSystemExecutionTelemetry(TypedDict, total=False):
    """Ported from ``FileSystemExecutionTelemetrySchema``."""

    readMs: int
    writeMs: int
    fileCount: int
    totalBytes: int
    maxFileBytes: int
    # PORT: ``z.enum(["local", "remote", "unknown"])``
    workspaceKind: str


class PatchExecutionTelemetry(TypedDict, total=False):
    """Ported from ``PatchExecutionTelemetrySchema``."""

    matchMs: int
    hunkCount: int
    matchAttempts: int


class ToolExecutionTelemetryDetail(TypedDict, total=False):
    """Ported from ``ToolExecutionTelemetryDetailSchema``.

    PORT: TS 是 ``z.discriminatedUnion("kind", [...])`` 三支联合；Python 用单个
    ``TypedDict`` 叠加三支字段、以 ``kind`` 作判别键（与 BashOutputSchema 等处的
    「判别联合 → 单 dataclass + kind 字段」保持一致）。
    """

    # "command" | "filesystem" | "patch"
    kind: str
    command: CommandExecutionTelemetry
    filesystem: FileSystemExecutionTelemetry
    patch: PatchExecutionTelemetry


class ToolExecutionTelemetry(TypedDict, total=False):
    """Ported from ``ToolExecutionTelemetrySchema``.

    工具执行结果摘要，随 ToolCallResult 事件落本地存储；命令专属字段只能进入判别 detail，
    避免非命令工具伪造 exitCode 等不适用事实。
    """

    totalMs: int
    permissionWaitMs: int
    detail: ToolExecutionTelemetryDetail


