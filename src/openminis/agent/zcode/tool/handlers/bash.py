"""Bash tool handler — command execution through the execution adapter.

Ported from: packages/core/src/tool/handlers/bash.ts
Original package: @zcode/core (tool/handlers)

这是 Bash 工具的**装配点**：把模型给的 ``{command, timeout, description,
run_in_background, dangerouslyDisableSandbox}`` 变成 ``ExecutionRequest``，走
``ExecutionPort`` 执行，再把 ``ExecutionResult`` 折成 provider 可见的 ``BashOutput``，
最后挂上 stale-read 提示与 read-state 回填（``bash-read-file-state``）。
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from ...contracts.events import SessionEvent, SessionEventType
from ...contracts.execution import (
    BackgroundExecutionStartResult,
    ExecutionCommand,
    ExecutionEmbeddedSearchPrelude,
    ExecutionEvent,
    ExecutionOutputLimit,
    ExecutionRequest,
    ExecutionResult,
    ExecutionRunOptions,
    ExecutionSandboxPolicy,
)
from ...contracts.tools import (
    TimedToolTimeoutPolicy,
    ToolCancellationPolicy,
    ToolPermissionSpec,
    ToolResultBudget,
    ToolResultBudgetArtifact,
    ToolResultBudgetPreview,
    ToolTracePolicy,
)
from ...contracts.tracing import TraceContext
from ...embedded_search.shell import (
    should_inject_embedded_search_bash_prelude,
    supports_embedded_search_shell_selection,
)
from ..bash_timeout_policy import (
    DEFAULT_BASH_TIMEOUT_POLICY,
    BashTimeoutPolicy,
    resolve_bash_timeout_ms,
)
from ..path_policy import ToolWorkspaceDirectoryOptions, resolve_tool_working_directory
from ..types import (
    ToolEntry,
    ToolMetadata,
    ToolRuntimePermissionCapability,
    ToolRuntimePermissionCapabilityContext,
)
from .bash_background_lifecycle import supports_bash_background_lifecycle
from .bash_background_policy import is_bash_auto_background_eligible
from .bash_command_permission_policy import resolve_bash_permission_rule_policy
from .bash_cwd_policy import BashCwdPolicyInput, decide_bash_cwd_policy
from .bash_metadata import (  # noqa: F401  (re-export, mirrors the TS `export { … } from`)
    get_bash_activity_description,
    get_bash_auto_classifier_input,
    get_bash_description,
    get_bash_tool_use_summary,
    get_bash_user_facing_name,
    read_string_property,
)
from .bash_model_content import (
    format_bash_model_content,
    format_persisted_bash_model_content,
)
from .bash_output import (
    BashProgressTiming,
    ToBashOutputOptions,
    create_bash_background_performance_telemetry,
    create_empty_bash_performance_telemetry,
    to_bash_output,
)
from .bash_prompt import BashProviderDescriptionInput, create_bash_provider_description
from .bash_read_file_state import (
    BashReadFileStateEffectsInput,
    apply_bash_read_file_state_effects,
)
from .bash_semantics import is_runtime_read_only_bash_command
from .ports import (
    BASH_INPUT_JSON_SCHEMA,
    BASH_INPUT_SCHEMA,
    BASH_OUTPUT_JSON_SCHEMA,
    BASH_OUTPUT_SCHEMA,
    BASH_TOOL_NAME,
    BashInput,
    semantic_boolean,
    semantic_number,
)
from .tool_perf import (
    attach_tool_execution_telemetry,
    classify_command,
    classify_safe_command_identity,
)

__all__ = [
    "bash_handler",
    "bash_tool_entry",
    "create_bash_handler",
    "create_bash_tool_entry",
    "execute_bash_handler",
    "resolve_bash_permission_capability",
    "BashToolEntryOptions",
    "MAX_INLINE_OUTPUT_BYTES",
    "MAX_RUNTIME_PERSISTED_OUTPUT_BYTES",
    "BASH_PROVIDER_DESCRIPTION",
]

MAX_INLINE_OUTPUT_BYTES = 30_000
MAX_RUNTIME_PERSISTED_OUTPUT_BYTES = 5 * 1024 * 1024 * 1024

BASH_PROVIDER_DESCRIPTION = create_bash_provider_description(
    BashProviderDescriptionInput(
        default_timeout_ms=DEFAULT_BASH_TIMEOUT_POLICY.default_timeout_ms,
        max_timeout_ms=DEFAULT_BASH_TIMEOUT_POLICY.max_timeout_ms,
    )
)


def resolve_bash_permission_capability(
    input: Any,
    context: Optional[ToolRuntimePermissionCapabilityContext] = None,
) -> Optional[ToolRuntimePermissionCapability]:
    """Mirrors the non-exported ``resolveBashPermissionCapability(input, context?)``."""
    command = read_string_property(input, "command")
    if not command or not is_runtime_read_only_bash_command(command, context):
        return None
    return ToolRuntimePermissionCapability(
        destructive=False,
        needs_approval=False,
        read_only=True,
        risk_level="low",
        side_effect_scope="none",
        # PORT: 字面量 ``{ needsApproval: false; riskLevel: "low"; sideEffectScope: "none" }``
        # → 局部 permission 覆盖，保持原字段名（camelCase 是 provider 契约的一部分）。
        permission={
            "needsApproval": False,
            "riskLevel": "low",
            "sideEffectScope": "none",
        },
    )


async def bash_handler(input: Any, context: Any) -> dict[str, Any]:
    """Mirrors ``const bashHandler: ToolHandler``."""
    return await execute_bash_handler(input, context, DEFAULT_BASH_TIMEOUT_POLICY)


def create_bash_handler(timeout_policy: BashTimeoutPolicy) -> Any:
    """Mirrors the non-exported ``createBashHandler(timeoutPolicy)``."""

    async def handler(input: Any, context: Any) -> dict[str, Any]:
        return await execute_bash_handler(input, context, timeout_policy)

    return handler


async def execute_bash_handler(
    input: Any,
    context: Any,
    timeout_policy: BashTimeoutPolicy,
) -> dict[str, Any]:
    """Mirrors the non-exported ``executeBashHandler(input, context, timeoutPolicy)``."""
    parsed = _parse_bash_input(input)
    execution_port = context.execution_port

    if not execution_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "ExecutionPort is not configured for Bash tool",
            {
                "context": {
                    "toolCallId": context.tool_call_id,
                    "toolName": "Bash",
                },
                "recoverable": False,
            },
        )

    if len(parsed.command.strip()) == 0:
        command_telemetry = start_bash_command_telemetry(parsed, context)
        _call_method(command_telemetry, "finish_completed")
        return empty_bash_output(parsed)

    request = create_execution_request(parsed, context, timeout_policy)
    progress_timing = BashProgressTiming()
    command_telemetry = start_bash_command_telemetry(parsed, context)
    run_options = create_execution_run_options(context, progress_timing, command_telemetry)
    # 后台命令完成后 runTaskNotificationBatch 会另起一轮通知 turn，该 turn 不带
    # turnExecutionModel，闲时 turn 结束/失败后就会落到用户自己的套餐上跑完整 agent loop。
    # 与 subagent runner 的 BACKGROUND_UNAVAILABLE 对称：闲时 turn 拒绝显式后台，也关闭超时自动转后台。
    background_disabled = context.off_peak_turn is True
    if parsed.run_in_background and background_disabled:
        raise create_core_error(
            CoreErrorType.TOOL_EXECUTION_FAILED,
            "Idle-time tasks do not support background commands. Run this command in the "
            "foreground without run_in_background.",
            {
                "context": {
                    "toolCallId": context.tool_call_id,
                    "toolName": "Bash",
                },
                "recoverable": True,
            },
        )
    eligible_for_auto_background = (
        not background_disabled and is_bash_auto_background_eligible(parsed)
    )
    background_lifecycle_port = (
        execution_port if supports_bash_background_lifecycle(execution_port) else None
    )

    if parsed.run_in_background and not background_lifecycle_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "ExecutionPort does not support the Bash background lifecycle",
            {
                "context": {
                    "toolCallId": context.tool_call_id,
                    "toolName": "Bash",
                },
                "recoverable": False,
            },
        )

    async def run_command() -> Any:
        if parsed.run_in_background and background_lifecycle_port is not None:
            return await background_lifecycle_port.run_bash_with_background_lifecycle(
                request,
                {"mode": "explicit"},
                run_options,
            )
        if eligible_for_auto_background and background_lifecycle_port is not None:
            return await background_lifecycle_port.run_bash_with_background_lifecycle(
                request,
                {"mode": "auto_on_timeout"},
                run_options,
            )
        return {
            "kind": "foreground",
            "result": await execution_port.run(request, run_options),
        }

    if command_telemetry is not None:

        async def instrumented() -> Any:
            observed = await run_command()
            if _read_field(observed, "kind") == "backgrounded":
                _call_method(command_telemetry, "finish_backgrounded")
            else:
                finish_bash_command_telemetry(
                    command_telemetry, _read_field(observed, "result")
                )
            return observed

        run_result = await _run_in_telemetry_scope(command_telemetry, instrumented)
    else:
        run_result = await run_command()

    if _read_field(run_result, "kind") == "backgrounded":
        return to_backgrounded_bash_output(_read_field(run_result, "task"), parsed)

    result = _read_field(run_result, "result")
    cwd_decision = decide_bash_cwd_policy(
        BashCwdPolicyInput(
            status=result.status,
            exit_code=result.exit_code,
            resolved_cwd=result.resolved_cwd,
            workspace_root=context.workspace_root,
            runtime_scope=context.runtime_scope,
        )
    )
    if cwd_decision.next_working_directory:
        # 主线程 Bash 成功后会保留项目内 cwd；
        # 离开项目边界时 reset 回原始工作区，并把 reset 文案放进 Bash stderr。
        set_working_directory = getattr(context, "set_working_directory", None)
        if callable(set_working_directory):
            await _maybe_await(set_working_directory(cwd_decision.next_working_directory))
    output = await to_bash_output(
        result,
        parsed,
        context,
        ToBashOutputOptions(
            progress_timing=progress_timing,
            stderr_suffix=cwd_decision.stderr_suffix,
        ),
    )
    await apply_bash_read_file_state_effects(
        BashReadFileStateEffectsInput(
            command=parsed.command,
            context=context,
            output=output,
            result=result,
        )
    )
    return output


def empty_bash_output(input: BashInput) -> dict[str, Any]:
    """Mirrors the non-exported ``emptyBashOutput(input)``.

    PORT: 返回形态与 ``to_bash_output`` 一致——TS 的 ``BashOutput`` 是 interface，handler
    用**对象字面量**（camelCase 键）满足它；本包据此返回 camelCase ``dict``，
    直接可作为 ``BASH_OUTPUT_JSON_SCHEMA`` 的载荷。
    """
    return attach_tool_execution_telemetry(
        {
            "stdout": "",
            "stderr": "",
            "interrupted": False,
            "isImage": False,
            "noOutputExpected": False,
            "status": "completed",
            "dangerouslyDisableSandbox": input.dangerously_disable_sandbox,
        },
        create_empty_bash_performance_telemetry(input),
    )


def to_backgrounded_bash_output(
    task: BackgroundExecutionStartResult,
    input: BashInput,
) -> dict[str, Any]:
    """Mirrors the non-exported ``toBackgroundedBashOutput(task, input)``."""
    return attach_tool_execution_telemetry(
        {
            "stdout": "",
            "stderr": "",
            "interrupted": False,
            "status": "backgrounded",
            "backgroundTaskId": task.task_id,
            "rawOutputPath": task.output_path,
            "persistedOutputPath": task.output_path,
            "stdoutPersistedOutputPath": task.stdout_persisted_output_path,
            "stderrPersistedOutputPath": task.stderr_persisted_output_path,
            "dangerouslyDisableSandbox": input.dangerously_disable_sandbox,
        },
        create_bash_background_performance_telemetry(input),
    )


def create_execution_run_options(
    context: Any,
    progress_timing: Optional[BashProgressTiming] = None,
    telemetry: Any = None,
) -> ExecutionRunOptions:
    """Mirrors the non-exported ``createExecutionRunOptions(context, ...)``."""

    async def on_event(event: ExecutionEvent) -> None:
        if event.type != "progress":
            return
        if (
            progress_timing is not None
            and progress_timing.first_output_ms is None
            and (event.stdout_bytes or 0) + (event.stderr_bytes or 0) > 0
        ):
            # PORT: JS ``Math.round`` 是「四舍五入远离零」；Python ``round`` 是银行家舍入，
            # 故用 ``floor(x + 0.5)`` 复刻（ZCORE_PORTING.md §1）。
            progress_timing.first_output_ms = max(
                0, math.floor((event.elapsed_ms or 0) + 0.5)
            )
            _call_method(telemetry, "mark_first_output")
        await emit_progress_event(event, context)

    return ExecutionRunOptions(signal=context.abort_signal, on_event=on_event)


def start_bash_command_telemetry(input: BashInput, context: Any) -> Any:
    """Mirrors the non-exported ``startBashCommandTelemetry(input, context)``."""
    telemetry = getattr(context, "telemetry", None)
    start_command = getattr(telemetry, "start_command", None)
    if not callable(start_command):
        return None
    identity = classify_safe_command_identity(input.command)
    return start_command(
        {
            "category": command_category(input.command),
            "commandCount": identity.get("count") if identity.get("count") is not None else 0,
            "safeName": identity.get("name") if identity.get("name") is not None else "other",
            "sandboxed": input.dangerously_disable_sandbox is not True,
            "shellKind": command_shell_kind(getattr(context, "bash_shell_selection", None)),
        }
    )


def finish_bash_command_telemetry(telemetry: Any, result: ExecutionResult) -> None:
    """Mirrors the non-exported ``finishBashCommandTelemetry(telemetry, result)``."""
    if telemetry is None:
        return
    if result.exit_code is not None:
        _call_method(telemetry, "set_exit_code", result.exit_code)
    if result.signal:
        _call_method(telemetry, "set_signal", result.signal)
    _call_method(
        telemetry,
        "set_output_bytes",
        (result.stdout.bytes or 0) + (result.stderr.bytes or 0),
    )
    _call_method(telemetry, "set_timed_out", result.timed_out)

    if result.timed_out:
        _call_method(telemetry, "mark_termination_requested", "timeout")
        _call_method(telemetry, "finish_failed", "timeout", "timeout", result.error)
    elif result.cancelled:
        _call_method(telemetry, "mark_termination_requested", "cancelled")
        _call_method(telemetry, "finish_cancelled", "abort_signal")
    elif result.status == "spawn_error":
        _call_method(telemetry, "finish_failed", "spawn", "configuration", result.error)
    elif result.status == "failed" and result.error is not None:
        # 非零退出码是命令事实（例如 grep 未匹配），不等同于执行框架失败。
        # 只有 Adapter 明确提供结构化 failure 时才污染 command failure rate。
        _call_method(telemetry, "finish_failed", "execute", "internal", result.error)
    else:
        _call_method(telemetry, "finish_completed")


def command_category(command: str) -> str:
    """Mirrors the non-exported ``commandCategory(command)``."""
    classified = classify_command(command)
    if classified == "git":
        return "git"
    if classified == "package":
        return "package_manager"
    if classified == "build":
        return "build"
    if classified == "test":
        return "test"
    if classified == "network":
        return "network"
    return "shell"


def command_shell_kind(selection: Any) -> Optional[str]:
    """Mirrors the non-exported ``commandShellKind(selection)``."""
    display = _read_field(selection, "display")
    display_name = _read_field(display, "name")
    normalized = display_name.lower() if isinstance(display_name, str) else None
    if normalized is not None and "powershell" in normalized:
        return "powershell"
    if normalized is not None and "zsh" in normalized:
        return "zsh"
    if normalized is not None and "bash" in normalized:
        return "bash"
    if _read_field(selection, "dialect") == "cmd":
        return "cmd"
    if _read_field(selection, "dialect") == "posix":
        return "sh"
    return None if selection is None else "other"


async def emit_progress_event(event: ExecutionEvent, context: Any) -> None:
    """Mirrors the non-exported ``emitProgressEvent(event, context)``."""
    emit_event = getattr(context, "emit_event", None)
    if not callable(emit_event):
        return

    await _maybe_await(
        emit_event(
            SessionEvent(
                # PORT: ``crypto.randomUUID() as any`` → ``str(uuid.uuid4())``。
                id=str(uuid.uuid4()),
                session_id=context.session_id,
                turn_id=context.turn_id,
                type=SessionEventType.TOOL_CALL_PROGRESS,
                timestamp=event.timestamp,
                trace_id=context.trace_id,
                sequence_number=0,
                payload={
                    "toolCallId": context.tool_call_id,
                    "toolName": "Bash",
                    "elapsedMs": event.elapsed_ms,
                    "pid": event.pid,
                    "stdoutBytes": event.stdout_bytes,
                    "stderrBytes": event.stderr_bytes,
                    "outputBytes": (event.stdout_bytes or 0) + (event.stderr_bytes or 0),
                    "outputPreview": event.output_preview,
                    "stdoutTail": event.stdout_tail,
                    "stderrTail": event.stderr_tail,
                },
            )
        )
    )


def create_execution_request(
    input: BashInput,
    context: Any,
    timeout_policy: BashTimeoutPolicy,
) -> ExecutionRequest:
    """Mirrors the non-exported ``createExecutionRequest(input, context, timeoutPolicy)``."""
    shell_selection = getattr(context, "bash_shell_selection", None)
    embedded_search = getattr(context, "embedded_search", None)
    bash_prelude: Optional[ExecutionEmbeddedSearchPrelude] = None
    if (
        should_inject_embedded_search_bash_prelude()
        and _read_field(embedded_search, "enabled") is True
        and _read_field(embedded_search, "backend")
        and supports_embedded_search_shell_selection(shell_selection)
    ):
        bash_prelude = ExecutionEmbeddedSearchPrelude(
            backend=_read_field(embedded_search, "backend"),
            # PORT: TS ``...(findAndGrepEnabled === false ? {findAndGrepEnabled:false} : {})``
            # —— 只有显式 ``false`` 才下发该键。
            find_and_grep_enabled=(
                False
                if _read_field(embedded_search, "find_and_grep_enabled") is False
                else None
            ),
        )
    return ExecutionRequest(
        command=ExecutionCommand(
            mode="shell",
            command=input.command,
            shell_profile="posix-bash",
            shell_override=shell_selection if shell_selection else None,
        ),
        cwd=resolve_tool_working_directory(
            None,
            ToolWorkspaceDirectoryOptions(
                operation="execute",
                working_directory=context.working_directory,
                workspace_root=context.workspace_root,
            ),
        ),
        bash_prelude=bash_prelude,
        # PORT: TS ``input.run_in_background ? undefined : true``。
        capture_cwd_after_success=(
            None if input.run_in_background else True
        ),
        timeout_ms=resolve_bash_timeout_ms(input.timeout, timeout_policy),
        output_limit=ExecutionOutputLimit(
            max_inline_bytes=MAX_INLINE_OUTPUT_BYTES,
            max_buffer_bytes=MAX_INLINE_OUTPUT_BYTES,
            max_persisted_bytes=MAX_RUNTIME_PERSISTED_OUTPUT_BYTES,
            persist_output="always" if input.run_in_background else "on_truncate",
        ),
        sandbox=ExecutionSandboxPolicy(
            # PORT: TS ``!input.dangerouslyDisableSandbox``——``undefined`` 取反为 ``true``。
            enabled=not input.dangerously_disable_sandbox,
            dangerously_disable_sandbox=input.dangerously_disable_sandbox,
        ),
        trace=TraceContext(
            trace_id=context.trace_id,
            span_id=context.span_id,
            parent_span_id=context.parent_span_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
            attributes={
                "toolCallId": context.tool_call_id,
                "toolName": "Bash",
            },
        ),
    )


def create_bash_timeout_budget_resolver(timeout_policy: BashTimeoutPolicy) -> Any:
    """Mirrors the non-exported ``createBashTimeoutBudgetResolver(timeoutPolicy)``."""

    def resolve(input: Any) -> int:
        # 旧 watchdog 直接读取 raw timeout，导致 0 被压成 1ms，且字符串数字绕过
        # Bash policy。这里和 handler 共用 timeout || default / max 解析后再加 cleanup grace。
        #
        # PORT: TS 用 ``BashInputSchema.safeParse`` 判定；本包在 executor 已校验过的前提下
        # 只重放 timeout 的语义化归一（``semanticNumber``），解析失败即视为未提供。
        timeout = _safe_parse_timeout(input)
        return resolve_bash_timeout_ms(timeout, timeout_policy)

    return resolve


# ---------------------------------------------------------------------------
# Tool entry
# ---------------------------------------------------------------------------

BASH_TOOL_METADATA = ToolMetadata(
    name=BASH_TOOL_NAME,
    description=BASH_PROVIDER_DESCRIPTION,
    read_only=False,
    destructive=False,
    concurrent_safe=False,
    timeout_ms=DEFAULT_BASH_TIMEOUT_POLICY.default_timeout_ms,
    max_output_bytes=10_000_000,
    side_effect_scope="system",
    risk_level="high",
    needs_approval=True,
)


def _bash_permission() -> ToolPermissionSpec:
    return ToolPermissionSpec(
        permission="bash",
        reason="Bash can run subprocesses and may affect workspace, git, network, or system state",
        risk_level="high",
        side_effect_scope="system",
        needs_approval=True,
        pattern_sources=["command"],
        always_allow_pattern_sources=["command"],
        deny_priority="beforeAsk",
    )


def _bash_result_budget() -> ToolResultBudget:
    return ToolResultBudget(
        max_inline_bytes=MAX_INLINE_OUTPUT_BYTES,
        max_model_bytes=30_000,
        strategy="artifact",
        preview=ToolResultBudgetPreview(max_bytes=30_000, direction="tail"),
        artifact=ToolResultBudgetArtifact(enabled=True, retention="session"),
    )


def _bash_timeout_policy_value(timeout_policy: BashTimeoutPolicy) -> TimedToolTimeoutPolicy:
    return TimedToolTimeoutPolicy(
        default_ms=timeout_policy.default_timeout_ms,
        max_ms=timeout_policy.max_timeout_ms,
        allow_call_override=True,
        cleanup_grace_ms=6_000,
    )


bash_tool_entry: ToolEntry = ToolEntry(
    capability="Execute platform shell commands through the execution adapter",
    metadata=BASH_TOOL_METADATA,
    format_model_content=format_bash_model_content,
    format_persisted_model_content=format_persisted_bash_model_content,
    handler=bash_handler,
    resolve_timeout_budget_ms=create_bash_timeout_budget_resolver(
        DEFAULT_BASH_TIMEOUT_POLICY
    ),
    resolve_permission_capability=resolve_bash_permission_capability,
    resolve_permission_rule_policy=resolve_bash_permission_rule_policy,
    input_schema=BASH_INPUT_JSON_SCHEMA,
    output_schema=BASH_OUTPUT_JSON_SCHEMA,
    runtime_input_schema=BASH_INPUT_SCHEMA,
    runtime_output_schema=BASH_OUTPUT_SCHEMA,
    permission=_bash_permission(),
    result_budget=_bash_result_budget(),
    timeout=_bash_timeout_policy_value(DEFAULT_BASH_TIMEOUT_POLICY),
    cancellation=ToolCancellationPolicy(
        supported=True,
        cleanup="bestEffort",
        user_visible_message="Bash was cancelled and the child process was asked to stop",
    ),
    trace=ToolTracePolicy(
        propagate_to_adapters=True,
        record_input="summary",
        record_output="summary",
    ),
)


@dataclass(slots=True)
class BashToolEntryOptions:
    """Mirrors the inline ``options`` shape of ``createBashToolEntry``."""

    bash_timeout_policy: Optional[BashTimeoutPolicy] = None
    embedded_search_enabled: Optional[bool] = None


def create_bash_tool_entry(
    options: Optional[BashToolEntryOptions] = None,
) -> ToolEntry:
    """Mirrors ``createBashToolEntry(options = {})``."""
    if options is None:
        options = BashToolEntryOptions()
    timeout_policy = options.bash_timeout_policy or DEFAULT_BASH_TIMEOUT_POLICY
    metadata = replace(
        BASH_TOOL_METADATA,
        description=create_bash_provider_description(
            BashProviderDescriptionInput(
                default_timeout_ms=timeout_policy.default_timeout_ms,
                embedded_search_enabled=options.embedded_search_enabled,
                max_timeout_ms=timeout_policy.max_timeout_ms,
            )
        ),
        timeout_ms=timeout_policy.default_timeout_ms,
    )
    return replace(
        bash_tool_entry,
        handler=create_bash_handler(timeout_policy),
        input_schema=create_bash_input_json_schema(timeout_policy),
        resolve_timeout_budget_ms=create_bash_timeout_budget_resolver(timeout_policy),
        metadata=metadata,
        timeout=_bash_timeout_policy_value(timeout_policy),
    )


def create_bash_input_json_schema(timeout_policy: BashTimeoutPolicy) -> dict[str, Any]:
    """Mirrors the non-exported ``createBashInputJsonSchema(timeoutPolicy)``."""
    schema = BASH_INPUT_JSON_SCHEMA
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    timeout_property = properties.get("timeout")
    if not isinstance(timeout_property, dict):
        return schema

    return {
        **schema,
        "properties": {
            **properties,
            "timeout": {
                **timeout_property,
                "description": (
                    "Optional timeout in milliseconds "
                    f"(max {timeout_policy.max_timeout_ms})"
                ),
            },
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_bash_input(input: Any) -> BashInput:
    """Mirrors ``BashInputSchema.parse(input)``.

    PORT: zod 在此做两件事——**校验**与**语义化预处理**（``semanticNumber(timeout)``、
    ``semanticBoolean(runInBackground / dangerouslyDisableSandbox)``）。executor 已用
    ``runtime_input_schema`` 校验过一次，故这里只重放那两处预处理，使字符串数字、
    ``"true"``/``"0"`` 这类模型常见写法与上游一致地落成正确类型。
    """
    raw: dict[str, Any] = input if isinstance(input, dict) else {}
    command = raw.get("command")
    timeout = semantic_number(raw.get("timeout"))
    description = raw.get("description")
    return BashInput(
        command=command if isinstance(command, str) else "",
        timeout=timeout if isinstance(timeout, int) else None,
        description=description if isinstance(description, str) else None,
        run_in_background=_as_boolean(raw.get("run_in_background")),
        dangerously_disable_sandbox=_as_boolean(raw.get("dangerouslyDisableSandbox")),
    )


def _safe_parse_timeout(input: Any) -> Optional[int]:
    raw: dict[str, Any] = input if isinstance(input, dict) else {}
    value = semantic_number(raw.get("timeout"))
    return value if isinstance(value, int) else None


def _as_boolean(value: Any) -> Optional[bool]:
    coerced = semantic_boolean(value)
    return coerced if isinstance(coerced, bool) else None


def _read_field(obj: Any, name: str) -> Any:
    """Read ``obj[name]`` whether ``obj`` is a mapping or an object."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _call_method(obj: Any, name: str, *args: Any) -> Any:
    """Call ``obj.<name>(*args)`` when present.

    PORT: TS 用 ``telemetry?.markX()`` / 直接调用断言非空；本包 telemetry 是宿主注入的
    ``Any``，方法可能缺席（例如只实现了 ``CommandExecutionSpanWriter`` 的子集），
    缺席时静默跳过——遥测永远不该让 Bash 失败。
    """
    method = getattr(obj, name, None)
    if not callable(method):
        return None
    return method(*args)


async def _run_in_telemetry_scope(telemetry: Any, run: Any) -> Any:
    """Mirrors ``commandTelemetry.run(async () => { … })``.

    PORT: TS 的 span writer ``run(thunk)`` 包住整段并测量时长；本包把同一语义映射为
    传入 async 可调用对象。宿主 writer 未实现 ``run`` 时退化为直接执行（不测量）。
    """
    runner = getattr(telemetry, "run", None)
    if not callable(runner):
        return await run()
    return await _maybe_await(runner(run))


async def _maybe_await(value: Any) -> Any:
    """``await`` only when the value is awaitable."""
    if hasattr(value, "__await__"):
        return await value
    return value
