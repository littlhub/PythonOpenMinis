# PORT-STATUS: partial
# Memory Agent Loop — runs the memory extraction subagent turn loop.
#
# Ported from: packages/core/src/memory/memory-agent-loop.ts
# Original package: @zcode/core (memory)
#
# 依赖未移植能力以端口占位保留结构与调用点；下列类型/辅助函数来自尚未移植的模块
# （core/src/runtime/helpers/*、tool/handlers/bash-*、model/auxiliary-model-options.ts、
# 以及 @zcode/contracts 的 Model 端口）。运行时调用处抛 NotImplementedError，结构 1:1 照搬。

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Literal, Optional, Protocol, Sequence, TypedDict, Union

from ..core.abort import AbortController, AbortSignal
from ..tool.types import ExecutableToolCall, ToolExecutionResult
from .memory_file_path import (
    resolve_contained_memory_file_path,
    resolve_safe_memory_file_path,
)


# ---------------------------------------------------------------------------
# PORT: 本地类型占位（@zcode/contracts 与未移植 core 模块的形状）
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ModelReasoningContentBlock:
    """PORT: ``ModelReasoningContentBlock`` 的本地等价（仅保留被读取字段）。"""

    text: str
    type: str = "reasoning"
    provider_options: Optional[Any] = None


@dataclass(slots=True)
class ModelToolCall:
    """PORT: ``ModelToolCall`` 的本地等价（仅保留被读取字段）。"""

    id: str
    name: str
    input: Any = None


@dataclass(slots=True)
class ModelToolContract:
    """PORT: ``ModelToolContract`` 的本地等价（仅保留被读取字段）。"""

    name: str
    side_effect_scope: str = "workspace"


@dataclass(slots=True)
class ModelInputMessage:
    """PORT: ``ModelInputMessage`` 的本地等价（仅保留被读取字段）。"""

    role: str
    content: Any = None
    tool_calls: Optional[list[ModelToolCall]] = None
    cache_control: Optional[Any] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    is_error: Optional[bool] = None


@dataclass(slots=True)
class ModelProperties:
    """PORT: ``Model["properties"]`` 的本地等价。"""

    input_format: str


@dataclass(slots=True)
class ModelTextResult:
    """PORT: ``Model.generateText`` 返回形状的本地等价。"""

    text: str
    reasoning: Sequence[ModelReasoningContentBlock] = ()
    tool_calls: Sequence[ModelToolCall] = ()


@dataclass(slots=True)
class ModelRequest:
    """PORT: ``ModelRequest`` 的本地等价。"""

    abort_signal: Optional[AbortSignal] = None
    messages: Sequence[ModelInputMessage] = ()
    options: Any = None
    tools: Sequence[ModelToolContract] = ()


class Model(Protocol):
    """PORT: @zcode/contracts ``Model`` 端口（未移植）；仅声明本模块读取的方法。"""

    properties: ModelProperties

    async def generate_text(self, request: ModelRequest) -> ModelTextResult: ...


# PORT: 下列辅助函数来自尚未移植的模块；保留签名与调用点，运行时抛 NotImplementedError。
async def model_content_for_tool_result(result: ToolExecutionResult) -> Any:
    # PORT: core/src/runtime/helpers/tool-result.ts ``modelContentForToolResult`` 未移植。
    raise NotImplementedError(
        "PORT: model_content_for_tool_result 来自 core/src/runtime/helpers/tool-result.ts（未移植）"
    )


def is_error_for_tool_result(result: ToolExecutionResult) -> bool:
    # PORT: core/src/runtime/helpers/tool-result.ts ``isErrorForToolResult`` 未移植。
    raise NotImplementedError(
        "PORT: is_error_for_tool_result 来自 core/src/runtime/helpers/tool-result.ts（未移植）"
    )


async def project_messages_for_model_media_policy(
    messages: Sequence[ModelInputMessage], input_format: str
) -> Any:
    # PORT: core/src/runtime/helpers/media-budget.ts ``projectMessagesForModelMediaPolicy`` 未移植。
    raise NotImplementedError(
        "PORT: project_messages_for_model_media_policy 来自 core/src/runtime/helpers/media-budget.ts（未移植）"
    )


def analyze_bash_command(command: str) -> Any:
    # PORT: core/src/tool/handlers/bash-command-parser.ts ``analyzeBashCommand`` 未移植。
    raise NotImplementedError(
        "PORT: analyze_bash_command 来自 core/src/tool/handlers/bash-command-parser.ts（未移植）"
    )


def is_bash_command_permission_safe(analysis: Any) -> bool:
    # PORT: core/src/tool/handlers/bash-command-parser.ts ``isBashCommandPermissionSafe`` 未移植。
    raise NotImplementedError(
        "PORT: is_bash_command_permission_safe 来自 core/src/tool/handlers/bash-command-parser.ts（未移植）"
    )


def is_runtime_read_only_bash_command(command: str, options: dict[str, str]) -> bool:
    # PORT: core/src/tool/handlers/bash-semantics.ts ``isRuntimeReadOnlyBashCommand`` 未移植。
    raise NotImplementedError(
        "PORT: is_runtime_read_only_bash_command 来自 core/src/tool/handlers/bash-semantics.ts（未移植）"
    )


def auxiliary_model_options(model: Model) -> Any:
    # PORT: core/src/model/auxiliary-model-options.ts ``auxiliaryModelOptions`` 未移植。
    raise NotImplementedError(
        "PORT: auxiliary_model_options 来自 core/src/model/auxiliary-model-options.ts（未移植）"
    )


@dataclass(slots=True)
class MemoryAgentLoopResult:
    """Mirrors ``interface MemoryAgentLoopResult``."""

    messages: list[ModelInputMessage]
    turns: int


@dataclass(slots=True)
class MemoryAgentToolPolicyInput:
    """Mirrors the inline argument object of ``evaluateMemoryAgentToolPolicy``."""

    root_dir: str
    tool_call: ModelToolCall
    tools: Sequence[ModelToolContract]
    working_directory: str
    workspace_root: str


class _MemoryAgentAllowedDecision(TypedDict):
    allowed: Literal[True]


class _MemoryAgentDeniedDecision(TypedDict):
    allowed: Literal[False]
    reason: str


MemoryAgentToolPolicyDecision = Union[_MemoryAgentAllowedDecision, _MemoryAgentDeniedDecision]


MEMORY_AGENT_READ_ONLY_TOOLS = frozenset(["Read", "Grep", "Glob"])


async def run_memory_agent_loop(input: dict[str, Any]) -> MemoryAgentLoopResult:
    """Mirrors ``runMemoryAgentLoop(input)``。"""
    messages: list[ModelInputMessage] = [clone_model_message(msg) for msg in input["messages"]]
    turns = 0

    for _ in range(input["maxTurns"]):
        turns += 1
        if input.get("abortSignal") is not None:
            input["abortSignal"].throw_if_aborted()
        # 只在 Memory 初始快照投影会漏掉 Read 等工具后续产生的媒体；每一次
        # provider 请求都必须在 request-local 副本上执行同一套 capability + budget 策略。
        media_projection = await project_messages_for_model_media_policy(
            [clone_model_message(msg) for msg in messages],
            input["model"].properties.input_format,
        )
        request = ModelRequest(
            abort_signal=input.get("abortSignal"),
            messages=media_projection.messages,
            options=auxiliary_model_options(input["model"]),
            # Memory agent 的 provider request 必须保留 Main 的真实工具目录；执行权限只在 tool-use 边界收窄。
            tools=list(input["tools"]),
        )
        response = await input["model"].generate_text(request)
        if input.get("abortSignal") is not None:
            input["abortSignal"].throw_if_aborted()

        tool_calls = list(response.tool_calls)
        messages.append(create_assistant_message(response.text, response.reasoning, tool_calls))
        if len(tool_calls) == 0:
            # TS 在末尾再 ``turns += 1`` 后 break，此处保持同一计数语义。
            turns += 1
            break

        tool_messages = await asyncio_gather_tool_results(input, tool_calls, messages)
        messages.extend(tool_messages)

    return MemoryAgentLoopResult(messages=messages, turns=turns)


async def asyncio_gather_tool_results(
    input: dict[str, Any],
    tool_calls: list[ModelToolCall],
    messages: list[ModelInputMessage],
) -> list[ModelInputMessage]:
    """PORT: 原 TS ``Promise.all(toolCalls.map(...))`` → 顺序 ``await`` 收集（等价语义）。"""
    results: list[ModelInputMessage] = []
    for tool_call in tool_calls:
        decision = evaluate_memory_agent_tool_policy(
            MemoryAgentToolPolicyInput(
                root_dir=input["rootDir"],
                tool_call=tool_call,
                tools=input["tools"],
                working_directory=input["workingDirectory"],
                workspace_root=input["workspaceRoot"],
            )
        )
        if not decision["allowed"]:
            results.append(
                ModelInputMessage(
                    content=decision["reason"],
                    role="tool",
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    is_error=True,
                )
            )
            continue

        # PORT: 原 ``input.executeTool({ id, input, name }, { abortSignal })``。
        result = await input["executeTool"](
            ExecutableToolCall(id=tool_call.id, name=tool_call.name, input=tool_call.input),
            {"abortSignal": input.get("abortSignal")},
        )
        results.append(
            ModelInputMessage(
                content=await model_content_for_tool_result(result),
                role="tool",
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                is_error=is_error_for_tool_result(result),
            )
        )
    return results


def evaluate_memory_agent_tool_policy(
    input: MemoryAgentToolPolicyInput,
) -> MemoryAgentToolPolicyDecision:
    """Mirrors ``evaluateMemoryAgentToolPolicy(input)``。"""
    contract = next(
        (tool for tool in input.tools if tool.name == input.tool_call.name), None
    )
    # catalog miss 被合并进 Memory 权限拒绝会让未注册工具没有先走
    # No such tool 错误处理。此分支只闭合原 call id，不进入工具执行面。
    if contract is None:
        return deny_unavailable_memory_agent_tool(input.tool_call.name)

    if (
        input.tool_call.name == "Agent"
        or input.tool_call.name.startswith("mcp__")
        or contract.side_effect_scope == "network"
    ):
        return deny_memory_agent_tool(input.root_dir)

    if input.tool_call.name in ("Write", "Edit"):
        return (
            {"allowed": True}
            if is_contained_markdown_mutation(input)
            else deny_memory_agent_tool(input.root_dir)
        )

    if input.tool_call.name == "Bash":
        command = string_property(input.tool_call.input, "command")
        # Memory Agent 直接复用既有只读分类器，避免在此处二次收窄安全 env、redirect 和后台执行。
        if command and (
            is_runtime_read_only_bash_command(
                command,
                {
                    "workingDirectory": input.working_directory,
                    "workspaceRoot": input.workspace_root,
                },
            )
            or is_contained_markdown_bash_removal(command, input)
        ):
            return {"allowed": True}
        return deny_memory_agent_bash(input.root_dir)

    if input.tool_call.name in MEMORY_AGENT_READ_ONLY_TOOLS:
        return {"allowed": True}

    return deny_memory_agent_tool(input.root_dir)


def is_contained_markdown_mutation(input: MemoryAgentToolPolicyInput) -> bool:
    """Mirrors ``isContainedMarkdownMutation(input)``。"""
    file_path = string_property(input.tool_call.input, "file_path")
    if not file_path or not file_path.endswith(".md"):
        return False
    try:
        return (
            resolve_safe_memory_file_path(
                {
                    "filePath": file_path,
                    "rootDir": input.root_dir,
                    "workingDirectory": input.working_directory,
                    "workspaceRoot": input.workspace_root,
                }
            )
            is not None
        )
    except Exception:  # noqa: BLE001
        return False


def is_contained_markdown_bash_removal(
    command: str, input: MemoryAgentToolPolicyInput
) -> bool:
    """Mirrors ``isContainedMarkdownBashRemoval(command, input)``。"""
    analysis = analyze_bash_command(command)
    if not is_bash_command_permission_safe(analysis) or len(analysis.commands) != 1:
        return False
    invocation = analysis.commands[0]
    if invocation is None or invocation.argv[0] != "rm":
        return False
    if len(invocation.redirects) > 0 or len(invocation.env_assignments) > 0:
        return False

    after_options = False
    path_count = 0
    for argument in invocation.argv[1:]:
        if not after_options:
            if argument == "--":
                after_options = True
                continue
            if argument.startswith("-"):
                if argument == "--recursive" or re.search(r"^-[a-zA-Z]*[rR]", argument):
                    return False
                continue
        if re.search(r"[*?[]", argument):
            return False
        if not re.search(r"^[A-Za-z]?:?\\", argument) and not argument.startswith("/"):
            # PORT: 原 ``isAbsolute(argument)``；此处用绝对路径判定（含 Windows 盘符）。
            if not _is_absolute(argument):
                return False
        if not argument.endswith(".md"):
            return False
        if resolve_contained_path(argument, input) is None:
            return False
        path_count += 1
    return path_count > 0


def resolve_contained_path(
    file_path: str, input: MemoryAgentToolPolicyInput
) -> Optional[str]:
    """Mirrors ``resolveContainedPath(filePath, input)``。"""
    try:
        return resolve_contained_memory_file_path(
            {
                "filePath": file_path,
                "rootDir": input.root_dir,
                "workingDirectory": input.working_directory,
                "workspaceRoot": input.workspace_root,
            }
        )
    except Exception:  # noqa: BLE001
        return None


def create_assistant_message(
    text: str,
    reasoning: Sequence[ModelReasoningContentBlock],
    tool_calls: Sequence[ModelToolCall],
) -> ModelInputMessage:
    """Mirrors ``createAssistantMessage(text, reasoning, toolCalls)``。"""
    return ModelInputMessage(
        content=assistant_content(text, reasoning),
        role="assistant",
        tool_calls=[ModelToolCall(id=call.id, name=call.name, input=call.input) for call in tool_calls],
    )


def assistant_content(
    text: str, reasoning: Optional[Sequence[ModelReasoningContentBlock]]
) -> Any:
    """Mirrors ``assistantContent(text, reasoning)``。"""
    if not reasoning:
        return text
    return [
        *[
            ModelReasoningContentBlock(
                text=block.text,
                type=block.type,
                provider_options=(
                    dict(block.provider_options) if block.provider_options is not None else None
                ),
            )
            for block in reasoning
        ],
        # PORT: TS ``{ text, type: "text" as const }`` → 本地文本块字典。
        *([{"text": text, "type": "text"}] if text else []),
    ]


def clone_model_message(message: ModelInputMessage) -> ModelInputMessage:
    """Mirrors ``cloneModelMessage(message)``。"""
    content = (
        [dict(block) for block in message.content]
        if isinstance(message.content, list)
        else message.content
    )
    return ModelInputMessage(
        role=message.role,
        content=content,
        cache_control=dict(message.cache_control) if message.cache_control is not None else None,
        tool_calls=(
            [ModelToolCall(id=call.id, name=call.name, input=call.input) for call in message.tool_calls]
            if message.tool_calls is not None
            else None
        ),
    )


def string_property(value: Any, property_name: str) -> Optional[str]:
    """Mirrors ``stringProperty(value, property)``。"""
    if not isinstance(value, dict) or value is None:
        return None
    property_value = value.get(property_name)
    return property_value if isinstance(property_value, str) else None


# 拒绝结果会进入下一轮 provider request，需按基线区分 Bash 与其他工具的固定文案。
def deny_memory_agent_bash(root_dir: str) -> MemoryAgentToolPolicyDecision:
    """Mirrors ``denyMemoryAgentBash(rootDir)``。"""
    return {
        "allowed": False,
        "reason": f"Only read-only shell commands and rm with all paths inside {root_dir} are permitted in this context (ls, find, grep, cat, stat, wc, head, tail, and similar)",
    }


def deny_memory_agent_tool(root_dir: str) -> MemoryAgentToolPolicyDecision:
    """Mirrors ``denyMemoryAgentTool(rootDir)``。"""
    return {
        "allowed": False,
        "reason": f"only Read, Grep, Glob, read-only Bash, and Edit/Write within {root_dir} are allowed",
    }


def deny_unavailable_memory_agent_tool(tool_name: str) -> MemoryAgentToolPolicyDecision:
    """Mirrors ``denyUnavailableMemoryAgentTool(toolName)``。"""
    return {
        "allowed": False,
        "reason": f"<tool_use_error>Error: No such tool available: {tool_name}</tool_use_error>",
    }


def _is_absolute(path: str) -> bool:
    """PORT: 原 ``node:path`` 的 ``isAbsolute``；``os.path.isabs`` 等价。"""
    import os

    return os.path.isabs(path)
