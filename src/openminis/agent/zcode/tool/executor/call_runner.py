"""单次工具调用执行生命周期（HEART）：输入归一化 → 校验 → PreToolUse →
权限 → 执行（含超时/取消）→ 结果序列化 → PostToolUse → 事件/通知。

Ported from: packages/core/src/tool/executor/call-runner.ts
Original package: @zcode/core (tool/executor)

# PORT: 以下依赖尚未移植，按预测路径导入（``# type: ignore``），运行时再补：
#   - ``OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION`` / ``attestOfficialCuaFrameContent``
#     —— @zcode/zcode-cua/frame-contract
#   - ``hasOfficialCuaFrameAuthority`` —— ../../mcp/image-normalization
#   - ``prepareInitialToolExecutionInput`` / ``normalizeToolExecutionInput`` —— ../input-normalization
#   - ``resolveToolEntryModelContract`` —— ../model-contract
#   - ``mergeToolExecutionTelemetry`` / ``readToolExecutionTelemetry`` —— ../handlers/tool-perf
#   - ``resolveEmbeddedSearchBranchCapability`` —— ../../embedded-search/capability
"""

from __future__ import annotations

import dataclasses
import time
from datetime import datetime, timezone
from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error  # type: ignore
from ...contracts.tracing import (  # type: ignore
    create_child_trace_context,
    create_root_trace_context,
    get_current_trace_context,
    trace_context_to_log_context,
)
# PORT: @zcode/zcode-cua/frame-contract，not ported yet。
from ...zcode_cua.frame_contract import (  # type: ignore
    OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION,
    attest_official_cua_frame_content,
)
# PORT: ../../mcp/image-normalization，not ported yet。
from ...mcp.image_normalization import has_official_cua_frame_authority  # type: ignore
from ...contracts.tools import ToolResultBudget, ToolResultBudgetPreview  # type: ignore
from ...core.abort import AbortController
# PORT: ../input-normalization，not ported yet。
from ..input_normalization import (  # type: ignore
    normalize_tool_execution_input,
    prepare_initial_tool_execution_input,
)
# PORT: ../../embedded-search/capability 已移植（core/src/embedded-search/capability.ts）。
from ...embedded_search.capability import (  # type: ignore
    EmbeddedSearchBranchCapabilityContext,
    resolve_embedded_search_branch_capability,
)
# PORT: ../model-contract，not ported yet。
from ..model_contract import resolve_tool_entry_model_contract  # type: ignore
# PORT: ../handlers/tool-perf，not ported yet。
from ..handlers.tool_perf import (  # type: ignore
    merge_tool_execution_telemetry,
    read_tool_execution_telemetry,
)
from ..types import ExecutableToolCall, ToolEntry, ToolExecutionContext, ToolExecutionResult
from .background_tasks import BackgroundTaskTracker
from .errors import (
    create_error_result,
    create_permission_error_result,
    create_tool_handler_failure_error,
    is_tool_handler_failure,
    is_tool_handler_failure_error,
)
from .events import emit_tool_call_error, emit_tool_call_result, emit_tool_call_started
from .hook_flow import (
    format_hook_additional_contexts,
    run_post_tool_use_failure_hooks,
    run_post_tool_use_hooks,
    run_pre_tool_use_hooks,
)
from .model_status_sink import create_tool_model_status_sink, with_default_tool_model_status_sink
from .permission_capability import resolve_tool_call_capability_flags
from .permission_flow import resolve_tool_permission
from .result_display import create_mcp_tool_display, create_tool_result_display
from .result_serialization import append_hook_additional_contexts, serialize_output
from .telemetry import run_tool_call_with_telemetry
from .timeout import (
    ToolDeadline,
    execute_with_timeout,
    link_abort_signal,
    observe_tool_admission_clock,
    resolve_timeout_ms,
)
from .types import ToolExecuteOptions, ToolExecutorDeps
from .turn_control import (
    with_automation_create_limit_turn_stop,
    with_plan_exit_denied_turn_stop,
    with_terminal_tool_turn_stop,
    with_workflow_refine_denied_follow_up,
)
from .validation import validate_initial_model_tool_input, validate_input, validate_output


async def execute_tool_call(
    deps: ToolExecutorDeps,
    background_tasks: BackgroundTaskTracker,
    tool_call: ExecutableToolCall,
    options: Optional[ToolExecuteOptions] = None,
) -> ToolExecutionResult:
    """工具调用入口：先按隐私/基数边界做遥测名归一，再进入完整生命周期。"""
    total_started_at = int(time.time() * 1000)
    entry = None if _is_empty_tool_name(tool_call.name) else deps.registry.get(tool_call.name)
    canonical_tool_call = (
        dataclasses.replace(tool_call, name=entry.metadata.name)
        if (entry is not None and tool_call.name != entry.metadata.name)
        else tool_call
    )
    # 未注册工具名来自模型输出，不能假定是受控枚举；业务错误保留真实名称，远端 Trace 落入 unknown 桶。
    telemetry_tool_call = canonical_tool_call if entry is not None else dataclasses.replace(
        tool_call, name="unknown"
    )
    return await run_tool_call_with_telemetry(
        deps,
        telemetry_tool_call,
        options,
        lambda telemetry: execute_tool_call_impl(
            deps, background_tasks, tool_call, total_started_at, options, telemetry
        ),
    )


async def execute_tool_call_impl(
    deps: ToolExecutorDeps,
    background_tasks: BackgroundTaskTracker,
    tool_call: ExecutableToolCall,
    total_started_at: int,
    options: Optional[ToolExecuteOptions] = None,
    telemetry: Any = None,
) -> ToolExecutionResult:
    parent_trace_context = (
        options.trace_context
        if options is not None and options.trace_context is not None
        else (
            get_current_trace_context()
            if get_current_trace_context() is not None
            else (
                deps.trace_context
                if deps.trace_context is not None
                else create_root_trace_context(session_id=deps.session_id, turn_id=deps.turn_id)
            )
        )
    )
    empty_tool_name = _is_empty_tool_name(tool_call.name)
    registered_entry = None if empty_tool_name else deps.registry.get(tool_call.name)
    model = options.model if options is not None and options.model is not None else deps.model
    entry = (
        resolve_tool_entry_model_contract(registered_entry, {"model": model})
        if registered_entry is not None
        else None
    )
    canonical_tool_call = (
        dataclasses.replace(tool_call, name=entry.metadata.name)
        if (entry is not None and tool_call.name != entry.metadata.name)
        else tool_call
    )
    trace_context = create_child_trace_context(
        parent_trace_context,
        {
            "session_id": deps.session_id,
            "turn_id": deps.turn_id,
            "attributes": {
                "tool_call_id": canonical_tool_call.id,
                "tool_name": canonical_tool_call.name,
            },
        },
    )
    trace_id = trace_context.trace_id
    turn_id = trace_context.turn_id if trace_context.turn_id is not None else deps.turn_id

    if entry is None:
        result = create_error_result(
            tool_call,
            create_core_error(
                CoreErrorType.TOOL_NOT_FOUND,
                "Model returned an invalid tool call: tool name is empty."
                if empty_tool_name
                else f"Tool not found: {tool_call.name}",
                {
                    "context": {"toolCallId": tool_call.id, "toolName": tool_call.name},
                    "recoverable": False,
                },
            ),
        )
        if empty_tool_name:
            # 空名在 admission 阶段停止会让模型永远收不到配对结果。复用 registry-miss 生命周期，
            # 但 provider 内容严格保留模型返回的原始空白名称。
            result = dataclasses.replace(
                result,
                model_content=f"<tool_use_error>Error: No such tool available: {tool_call.name}</tool_use_error>",
            )
        # registry miss 发生在 handler/ToolCallStarted 之前；旧代码只把失败返回给 provider，
        # 没有发布 ToolCallError，V4 tool row 因而永久停在 inputStreaming。
        await emit_tool_call_error(deps, tool_call.id, trace_context, turn_id, result.error)
        deps.logger and getattr(deps.logger, "warn", None) and deps.logger.warn(
            "Tool call rejected because the tool is not registered",
            {
                **trace_context_to_log_context(trace_context),
                "event": "tool.call.not_found",
                "module": "core.tool.executor",
                "status": "failed",
                "toolCallId": tool_call.id,
                "toolName": tool_call.name,
            },
        )
        telemetry and telemetry.finish_failed("lookup", "configuration", result.error)
        return result

    mode = deps.get_mode()

    if options is not None and options.signal is not None and options.signal.aborted:
        result = create_error_result(
            canonical_tool_call,
            create_core_error(CoreErrorType.TOOL_CANCELLED, "Tool execution cancelled"),
        )
        telemetry and telemetry.finish_cancelled("abort_signal")
        return result

    prepared_initial = prepare_initial_tool_execution_input(
        {"entry": entry, "input": canonical_tool_call.input, "logger": deps.logger}
    )
    execution_input = prepared_initial.input
    initial_input_validation = validate_initial_model_tool_input(
        execution_input, entry, prepared_initial.runtime_validation_issues
    )
    if initial_input_validation:
        result = create_error_result(canonical_tool_call, initial_input_validation)
        # schema 失败与 registry miss 同属 handler/ToolCallStarted 之前的早退。
        await emit_tool_call_error(deps, canonical_tool_call.id, trace_context, turn_id, result.error)
        telemetry and telemetry.finish_failed("validation", "parse", result.error)
        return result

    tool_input_validation = (
        entry.validate_input(execution_input, {"runtime_task_registry": deps.runtime_task_registry})
        if entry.validate_input is not None
        else None
    )
    if tool_input_validation is not None and is_tool_handler_failure(tool_input_validation):
        # tool-specific 语义校验必须在 hook 前结束。
        result = create_error_result(
            canonical_tool_call,
            create_tool_handler_failure_error(canonical_tool_call, tool_input_validation),
        )
        await emit_tool_call_error(deps, canonical_tool_call.id, trace_context, turn_id, result.error)
        telemetry and telemetry.finish_failed("validation", "parse", result.error)
        return result

    # 归一化：把模型发出的入参换成「将要发生的执行事实」。位置刻意在 hook **之前**。
    if entry.resolve_input is not None:
        working_directory = deps.get_working_directory()
        resolution = await entry.resolve_input(
            execution_input,
            {
                **({"working_directory": working_directory} if working_directory is not None else {}),
                "runtime_task_registry": deps.runtime_task_registry,
                **({} if deps.dynamic_workflow_run_port is None else {"dynamic_workflow_run_port": deps.dynamic_workflow_run_port}),
                **({} if deps.model_catalog_port is None else {"model_catalog_port": deps.model_catalog_port}),
                "session_id": deps.session_id,
            },
        )
        if is_tool_handler_failure(resolution):
            # 与 validateInput 同一条生命周期出口：解析不出来是模型该立刻拿回去修的东西。
            result = create_error_result(
                canonical_tool_call,
                create_tool_handler_failure_error(canonical_tool_call, resolution),
            )
            await emit_tool_call_error(deps, canonical_tool_call.id, trace_context, turn_id, result.error)
            telemetry and telemetry.finish_failed("validation", "parse", result.error)
            return result
        execution_input = resolution.input

    pre_tool_hook_result = await run_pre_tool_use_hooks(
        deps,
        canonical_tool_call,
        execution_input,
        entry,
        mode,
        trace_context,
        options.signal if options is not None else None,
    )
    if pre_tool_hook_result.permission_behavior == "deny" or pre_tool_hook_result.prevent_continuation:
        result = _append_pre_tool_additional_contexts_to_error_result(
            with_plan_exit_denied_turn_stop(
                create_permission_error_result(
                    canonical_tool_call,
                    pre_tool_hook_result.hook_permission_decision_reason
                    or pre_tool_hook_result.stop_reason
                    or "Blocked by PreToolUse hook",
                    {
                        "decision": "deny",
                        "mode": mode,
                        "reason": pre_tool_hook_result.hook_permission_decision_reason
                        or pre_tool_hook_result.stop_reason,
                        "source": "hook.PreToolUse",
                    },
                ),
                {
                    "mode": mode,
                    "plan_enabled": deps.session_mode_port.is_plan_enabled()
                    if deps.session_mode_port is not None
                    else None,
                    "tool_name": canonical_tool_call.name,
                },
            ),
            pre_tool_hook_result.additional_contexts,
        )
        telemetry and telemetry.set_permission_decision("denied")
        telemetry and telemetry.finish_denied("policy_denied")
        return result
    if pre_tool_hook_result.updated_input is not None:
        execution_input = normalize_tool_execution_input(
            {
                "entry": entry,
                "input": pre_tool_hook_result.updated_input,
                "logger": deps.logger,
                "source": "hook",
            }
        )
        hook_input_validation = validate_input(execution_input, entry)
        if hook_input_validation:
            # Hook 修改后的输入校验失败会在 handler 前直接返回。
            result = _append_pre_tool_additional_contexts_to_error_result(
                create_error_result(canonical_tool_call, hook_input_validation),
                pre_tool_hook_result.additional_contexts,
            )
            telemetry and telemetry.finish_failed("validation", "parse", result.error)
            return result

    permission_result = await resolve_tool_permission(
        deps,
        canonical_tool_call,
        entry,
        execution_input,
        pre_tool_hook_result,
        mode,
        trace_context,
        options.signal if options is not None else None,
        telemetry,
    )
    if not permission_result.allowed:
        result = _append_pre_tool_additional_contexts_to_error_result(
            with_workflow_refine_denied_follow_up(
                with_plan_exit_denied_turn_stop(
                    permission_result.result,
                    {
                        "mode": mode,
                        "plan_enabled": deps.session_mode_port.is_plan_enabled()
                        if deps.session_mode_port is not None
                        else None,
                        "tool_name": canonical_tool_call.name,
                    },
                ),
                {"tool_name": canonical_tool_call.name},
            ),
            pre_tool_hook_result.additional_contexts,
        )
        if result.error is not None and result.error.type == CoreErrorType.PERMISSION_DENIED:
            telemetry and telemetry.finish_denied("user_denied")
        else:
            telemetry and telemetry.finish_failed(
                "permission",
                _error_category_for_tool_error(result.error.type if result.error is not None else None),
                result.error,
            )
        return result
    execution_input = permission_result.execution_input
    permission_wait_ms = permission_result.permission_wait_ms

    start_time = int(time.time() * 1000)
    # 按**执行入参**解析一次副作用旗标（Bash 的只读命令判定就在这里落定），随 ToolCallStarted 发出。
    await emit_tool_call_started(
        deps,
        canonical_tool_call,
        trace_context,
        turn_id,
        start_time,
        create_mcp_tool_display(entry.metadata.mcp_presentation),
        resolve_tool_call_capability_flags(deps, entry, execution_input),
    )

    deps.logger and getattr(deps.logger, "info", None) and deps.logger.info(
        "Tool call started",
        {
            **trace_context_to_log_context(trace_context),
            "event": "tool.call.started",
            "module": "core.tool.executor",
            "status": "started",
            "toolCallId": canonical_tool_call.id,
            "toolName": canonical_tool_call.name,
        },
    )

    timeout_ms = resolve_timeout_ms(entry, execution_input, deps.default_timeout_ms, {"model": model})
    execution_abort_controller = AbortController()
    unlink_parent_abort = link_abort_signal(
        options.signal if options is not None else None,
        execution_abort_controller,
    )
    # 可暂停的 deadline：本次调用内部的模型请求在准入闸门前排队时暂停计时。
    deadline = ToolDeadline(timeout_ms)
    emit_event = (
        None
        if deps.emit_event is None
        else (
            lambda event: _observe_and_emit(event, canonical_tool_call.id, deadline, deps.emit_event)
        )
    )
    # 闭包槽位：handler 内部回调需要就地改写外层局部变量。
    read_file_state_slot: dict[str, Any] = {"value": None}
    skill_telemetry_slot: dict[str, Any] = {"value": None}
    failure_stage = "handler"

    try:
        model = options.model if options is not None and options.model is not None else deps.model
        bash_shell_selection = deps.get_bash_shell_selection() if deps.get_bash_shell_selection is not None else deps.bash_shell_selection
        embedded_search_decision = resolve_embedded_search_branch_capability(
            EmbeddedSearchBranchCapabilityContext(
                bash_available=deps.registry.has("Bash")
            )
        )
        context = ToolExecutionContext(
            tool_call_id=canonical_tool_call.id,
            trace_id=trace_id,
            abort_signal=execution_abort_controller.signal,
            working_directory=deps.get_working_directory(),
            workspace_root=deps.get_workspace_root(),
            session_id=deps.session_id,
            telemetry=telemetry,
            automation_turn=options.automation_turn if options is not None else None,
            off_peak_turn=options.off_peak_turn if options is not None else None,
            trace_context=trace_context,
            span_id=getattr(trace_context, "span_id", None),
            parent_span_id=getattr(trace_context, "parent_span_id", None),
            background_task_control_port=deps.background_task_control_port,
            emit_event=emit_event,
            execution_port=deps.execution_port,
            browser_control_port=deps.browser_control_port,
            browser_documentation_root=deps.browser_documentation_root,
            file_system_port=deps.file_system_port,
            http_client_port=deps.http_client_port,
            image_processor_port=deps.image_processor_port,
            pdf_document_port=deps.pdf_document_port,
            model=with_default_tool_model_status_sink(
                model,
                create_tool_model_status_sink(
                    {
                        "emit_event": emit_event,
                        "session_id": deps.session_id,
                        "turn_id": turn_id,
                        "trace_id": trace_id,
                    }
                ),
            ),
            subagent_model_override=options.subagent_model_override if options is not None else None,
            skill_port=deps.skill_port,
            subagent_port=deps.subagent_port,
            coordinator_response_port=deps.coordinator_response_port,
            workflow_submit_port=deps.workflow_submit_port,
            workflow_escalate_port=deps.workflow_escalate_port,
            artifact_store=deps.artifact_store,
            automation_port=deps.automation_port,
            off_peak_port=deps.off_peak_port,
            session_store=deps.session_store,
            session_mode_port=deps.session_mode_port,
            workflow_port=deps.workflow_port,
            dynamic_workflow_run_port=deps.dynamic_workflow_run_port,
            dynamic_workflow_snippet_port=deps.dynamic_workflow_snippet_port,
            model_catalog_port=deps.model_catalog_port,
            runtime_task_registry=deps.runtime_task_registry,
            read_file_state=deps.read_file_state,
            record_read_file_state_metadata=lambda metadata: read_file_state_slot.__setitem__("value", metadata),
            record_skill_telemetry_metadata=lambda metadata: skill_telemetry_slot.__setitem__("value", metadata),
            bash_shell_selection=bash_shell_selection,
            embedded_search={
                **({"backend": deps.embedded_search_backend} if deps.embedded_search_backend is not None else {}),
                "enabled": embedded_search_decision.use_embedded_search_branch
                if embedded_search_decision is not None
                else False,
                **({"find_and_grep_enabled": False} if deps.native_search_enhancements_enabled is False else {}),
            },
            set_working_directory=deps.set_working_directory,
            workspace_identity=deps.workspace_identity,
            remote_session_id=deps.remote_session_id,
            client_mode=deps.client_mode,
            delivery_kind=deps.delivery_kind,
            memory_root=deps.get_memory_root() if deps.get_memory_root is not None else None,
            runtime_scope=deps.runtime_scope,
            provider_visible_tool_names=[
                name
                for name in deps.registry.list()
                if (md := deps.registry.get_metadata(name)) is None or md.provider_visible is not False
            ],
            turn_id=turn_id,
        )

        output = await execute_with_timeout(
            entry.handler,
            execution_input,
            context,
            deadline,
            execution_abort_controller,
            entry,
        )
        duration_ms = int(time.time() * 1000) - start_time
        if is_tool_handler_failure(output):
            # handler 用返回值表达可预期业务失败；继续复用原来的 failure hook、事件和日志。
            raise create_tool_handler_failure_error(canonical_tool_call, output)
        validate_output(output, entry)
        # node_repl 同时承载 Browser Use 与 CUA，不能在注册时把整个 server 标成 official。
        model_output_entry = _resolve_model_output_entry(entry, output)
        failure_stage = "serialize"
        serialization = await serialize_output(
            deps,
            output,
            model_output_entry,
            trace_context,
            canonical_tool_call.id,
            execution_abort_controller.signal,
        )
        failure_stage = "post_hook"
        post_tool_hook_result = await run_post_tool_use_hooks(
            deps,
            canonical_tool_call,
            execution_input,
            output,
            serialization.artifact_path,
            trace_context,
            options.signal if options is not None else None,
        )
        serialization = append_hook_additional_contexts(
            serialization,
            [*pre_tool_hook_result.additional_contexts, *post_tool_hook_result.additional_contexts],
            model_output_entry,
        )
        display = create_tool_result_display(
            canonical_tool_call.name,
            output,
            {
                "mcp": entry.metadata.mcp_presentation,
                "officialCua": entry.model_content_protection == OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION,
            },
        )
        perf = merge_tool_execution_telemetry(
            read_tool_execution_telemetry(output),
            {
                "permissionWaitMs": permission_wait_ms,
                # totalMs 是用户感知的工具生命周期。
                "totalMs": int(time.time() * 1000) - total_started_at,
            },
        )

        final_model_content = serialization.model_content or serialization.content
        model_content_protection = (
            attest_official_cua_frame_content(final_model_content, model_output_entry.model_content_protection)
            if model_output_entry.model_content_protection
            else None
        )
        if (
            model_output_entry.model_content_protection
            and isinstance(final_model_content, list)
            and any(getattr(block, "type", None) == "image" for block in final_model_content)
            and not model_content_protection
        ):
            raise create_core_error(
                CoreErrorType.TOOL_EXECUTION_FAILED,
                "Official CUA frame failed final model-content attestation",
                {"recoverable": True},
            )

        result_kwargs: dict[str, Any] = {
            "tool_call_id": canonical_tool_call.id,
            "tool_name": canonical_tool_call.name,
            "success": True,
            "output": output,
            "display": display,
            "model_content": final_model_content,
            "performance": perf,
            "serialization": serialization,
            "duration_ms": duration_ms,
            "started_at": datetime.fromtimestamp(start_time / 1000.0, tz=timezone.utc),
            "completed_at": datetime.now(timezone.utc),
        }
        read_file_state_metadata = read_file_state_slot["value"]
        if read_file_state_metadata is not None:
            result_kwargs["read_file_state_metadata"] = read_file_state_metadata
        result = with_terminal_tool_turn_stop(ToolExecutionResult(**result_kwargs), {"entry": entry})

        await emit_tool_call_result(
            deps,
            canonical_tool_call,
            trace_context,
            turn_id,
            serialization,
            duration_ms,
            display,
            perf,
            skill_telemetry_slot["value"],
        )

        await background_tasks.track_background_task(canonical_tool_call, output, trace_context, turn_id)

        deps.logger and getattr(deps.logger, "info", None) and deps.logger.info(
            "Tool call completed",
            {
                **trace_context_to_log_context(trace_context),
                "durationMs": duration_ms,
                "event": "tool.call.completed",
                "module": "core.tool.executor",
                "status": "completed",
                "toolCallId": canonical_tool_call.id,
                "toolName": canonical_tool_call.name,
            },
        )

        telemetry and telemetry.set_output_bytes(serialization.returned_bytes)
        telemetry and telemetry.set_output_truncated(serialization.truncated)
        telemetry and telemetry.finish_completed()
        return result
    except Exception as error:
        duration_ms = int(time.time() * 1000) - start_time
        failure_hook_result = await run_post_tool_use_failure_hooks(
            deps,
            canonical_tool_call,
            execution_input,
            error,
            trace_context,
            options.signal if options is not None else None,
        )
        result = create_error_result(
            canonical_tool_call,
            error if isinstance(error, Exception) else Exception(str(error)),
            duration_ms,
        )
        base_model_content = None
        if result.error is not None:
            base_model_content = (
                result.model_content
                if (is_tool_handler_failure_error(error) and isinstance(result.model_content, str))
                else result.error.message
            )
        if len(failure_hook_result.additional_contexts) > 0 and base_model_content:
            result = dataclasses.replace(
                result,
                model_content="\n\n".join(
                    [
                        base_model_content,
                        format_hook_additional_contexts(
                            [*pre_tool_hook_result.additional_contexts, *failure_hook_result.additional_contexts]
                        ),
                    ]
                ),
            )
        elif len(pre_tool_hook_result.additional_contexts) > 0 and base_model_content:
            result = dataclasses.replace(
                result,
                model_content="\n\n".join(
                    [
                        base_model_content,
                        format_hook_additional_contexts(pre_tool_hook_result.additional_contexts),
                    ]
                ),
            )
        result = with_automation_create_limit_turn_stop(
            result,
            {"error": error, "tool_name": canonical_tool_call.name},
        )

        # Skill 已解析成功后，serialize/post_hook 仍可能失败；错误事件也要保留 resolved metadata。
        await emit_tool_call_error(
            deps,
            canonical_tool_call.id,
            trace_context,
            turn_id,
            result.error,
            skill_telemetry_slot["value"],
        )

        deps.logger and getattr(deps.logger, "error", None) and deps.logger.error(
            "Tool call failed",
            error,
            {
                **trace_context_to_log_context(trace_context),
                "durationMs": duration_ms,
                "event": "tool.call.failed",
                "module": "core.tool.executor",
                "status": "failed",
                "toolCallId": canonical_tool_call.id,
                "toolName": canonical_tool_call.name,
            },
        )

        if (options is not None and options.signal is not None and options.signal.aborted) or (
            result.error is not None and result.error.type == CoreErrorType.TOOL_CANCELLED
        ):
            telemetry and telemetry.finish_cancelled("abort_signal")
        else:
            telemetry and telemetry.finish_failed(
                failure_stage,
                _error_category_for_tool_error(result.error.type if result.error is not None else None),
                error,
            )
        return result
    finally:
        unlink_parent_abort()


def _resolve_model_output_entry(entry: ToolEntry, output: Any) -> ToolEntry:
    """node_repl 的 CUA 帧保护：仅当 authority 验证过才临时打开原子帧保护。"""
    is_shared_node_repl = entry.metadata.name == "mcp__node_repl__js" or (
        entry.metadata.mcp_presentation is not None
        and entry.metadata.mcp_presentation.server_name == "node_repl"
    )
    if (
        entry.model_content_protection == OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION
        or not is_shared_node_repl
        or not has_official_cua_frame_authority(output)
    ):
        return entry
    new_budget = dataclasses.replace(
        entry.result_budget,
        max_inline_bytes=max(entry.result_budget.max_inline_bytes, 256 * 1024),
        max_model_bytes=max(entry.result_budget.max_model_bytes, 256 * 1024),
        strategy="truncate",
        preview=ToolResultBudgetPreview(direction="head"),
    )
    return dataclasses.replace(
        entry,
        model_content_protection=OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION,
        result_budget=new_budget,
    )


def _is_empty_tool_name(tool_name: str) -> bool:
    return len(tool_name.strip()) == 0


def _error_category_for_tool_error(type: Optional[str]) -> str:
    if type in (
        CoreErrorType.CONFIGURATION_ERROR,
        CoreErrorType.TOOL_NOT_FOUND,
    ):
        return "configuration"
    if type in (
        CoreErrorType.PERMISSION_DENIED,
        CoreErrorType.PERMISSION_ESCALATION,
        CoreErrorType.PERMISSION_TIMEOUT,
    ):
        return "permission"
    if type == CoreErrorType.INVALID_INPUT:
        return "parse"
    if type == CoreErrorType.TOOL_CANCELLED:
        return "cancelled"
    if type == CoreErrorType.TOOL_TIMEOUT:
        return "timeout"
    return "internal"


def _append_pre_tool_additional_contexts_to_error_result(
    result: ToolExecutionResult,
    additional_contexts: list[str],
) -> ToolExecutionResult:
    if result.success or result.error is None or len(additional_contexts) == 0:
        return result
    # PreToolUse deny 和权限拒绝会在 handler 前提前返回，旧逻辑只在 handler 的成功/异常路径
    # 追加 context，导致 Hook 明明返回了 additionalContext，模型却看不到。
    base_model_content = result.model_content if isinstance(result.model_content, str) else result.error.message
    return dataclasses.replace(
        result,
        model_content="\n\n".join(
            [base_model_content, format_hook_additional_contexts(additional_contexts)]
        ),
    )


async def _observe_and_emit(event: Any, tool_call_id: str, deadline: ToolDeadline, emit: Any) -> None:
    """emitEvent 包装：先按本工具调用的模型状态事件暂停/续 deadline，再转发。"""
    observe_tool_admission_clock(event, tool_call_id, deadline)
    await emit(event)
