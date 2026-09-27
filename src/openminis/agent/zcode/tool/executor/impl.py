"""工具执行器实现 + 工厂。

Ported from: packages/core/src/tool/executor/impl.ts
Original package: @zcode/core (tool/executor)
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Optional

# PORT: @zcode/contracts（ids / tracing），not ported yet。
from ...contracts.ids import TurnId  # type: ignore
from ...contracts.tracing import TraceContext  # type: ignore
# PORT: ../../permission/broker，已移植。
from ...permission.broker import create_deny_permission_broker  # type: ignore
from ..scheduler import ToolSchedule
from ..types import ExecutableToolCall, ToolBatchEvent, ToolExecutionResult
from .background_tasks import BackgroundTaskTracker
from .batch_runner import execute_tool_batch, execute_tool_schedule
from .call_runner import execute_tool_call
from .types import (
    ToolBatchExecuteOptions,
    ToolExecuteOptions,
    ToolExecutor,
    ToolExecutorDeps,
    ToolExecutorOptions,
)


class ToolExecutorImpl:
    """``ToolExecutor`` 的实现。"""

    def __init__(self, options: ToolExecutorOptions) -> None:
        deps = ToolExecutorDeps(
            registry=options.registry,
            permission_service=options.permission_service,
            permission_broker=options.permission_broker
            if options.permission_broker is not None
            else create_deny_permission_broker(),
            emit_event=options.emit_event,
            session_id=options.session_id,
            default_timeout_ms=options.default_timeout_ms
            if options.default_timeout_ms is not None
            else 300000,
            read_file_state=options.read_file_state if options.read_file_state is not None else {},
            get_working_directory=(
                options.get_working_directory
                if options.get_working_directory is not None
                else (lambda: options.working_directory or ".")
            ),
            get_workspace_root=(
                options.get_workspace_root
                if options.get_workspace_root is not None
                else _default_workspace_root(options)
            ),
            runtime_scope=options.runtime_scope if options.runtime_scope is not None else "main",
            get_mode=options.get_mode
            if options.get_mode is not None
            else (lambda: options.mode or "build"),
            max_concurrency=options.max_concurrency if options.max_concurrency is not None else 10,
            # —— 可选字段 ——
            agent_telemetry=options.agent_telemetry,
            agent_telemetry_actor_kind=options.agent_telemetry_actor_kind,
            enqueue_background_task_notification=options.enqueue_background_task_notification,
            should_enqueue_background_task_notification=options.should_enqueue_background_task_notification,
            turn_id=options.turn_id,
            permission_timeout_ms=options.permission_timeout_ms,
            logger=options.logger,
            background_task_control_port=options.background_task_control_port,
            execution_port=options.execution_port,
            browser_control_port=options.browser_control_port,
            browser_documentation_root=options.browser_documentation_root,
            file_system_port=options.file_system_port,
            http_client_port=options.http_client_port,
            image_processor_port=options.image_processor_port,
            pdf_document_port=options.pdf_document_port,
            model=options.model,
            embedded_search_backend=options.embedded_search_backend,
            native_search_enhancements_enabled=options.native_search_enhancements_enabled,
            skill_port=options.skill_port,
            subagent_port=options.subagent_port,
            coordinator_response_port=options.coordinator_response_port,
            workflow_submit_port=options.workflow_submit_port,
            workflow_escalate_port=options.workflow_escalate_port,
            artifact_store=options.artifact_store,
            automation_port=options.automation_port,
            off_peak_port=options.off_peak_port,
            session_store=options.session_store,
            session_mode_port=options.session_mode_port,
            workflow_port=options.workflow_port,
            dynamic_workflow_run_port=options.dynamic_workflow_run_port,
            dynamic_workflow_snippet_port=options.dynamic_workflow_snippet_port,
            model_catalog_port=options.model_catalog_port,
            runtime_task_registry=options.runtime_task_registry,
            subagent_background_bash_max_ms=options.subagent_background_bash_max_ms,
            bash_shell_selection=options.bash_shell_selection,
            get_bash_shell_selection=options.get_bash_shell_selection,
            workspace_identity=options.workspace_identity,
            remote_session_id=options.remote_session_id,
            client_mode=options.client_mode,
            delivery_kind=options.delivery_kind,
            set_working_directory=options.set_working_directory,
            get_memory_root=options.get_memory_root,
            trace_context=options.trace_context,
            hook_runner=options.hook_runner,
        )
        self.deps = deps
        self.background_tasks = BackgroundTaskTracker(deps)

    async def execute(
        self,
        tool_call: ExecutableToolCall,
        options: Optional[ToolExecuteOptions] = None,
    ) -> ToolExecutionResult:
        return await execute_tool_call(self.deps, self.background_tasks, tool_call, options)

    async def track_external_background_task(
        self,
        tool_call: ExecutableToolCall,
        output: dict[str, Any],
        trace_context: TraceContext,
        turn_id: Optional[TurnId] = None,
    ) -> None:
        # 与 submit 路径同一个 tracker 实例、同一条 track_background_task。
        await self.background_tasks.track_background_task(tool_call, output, trace_context, turn_id)

    async def execute_batch(
        self,
        tool_calls: list[ExecutableToolCall],
        options: Optional[ToolBatchExecuteOptions] = None,
    ) -> list[ToolExecutionResult]:
        return await execute_tool_batch(
            self.deps,
            lambda tool_call, execute_options: self.execute(tool_call, execute_options),
            tool_calls,
            options,
        )

    async def execute_schedule(
        self,
        tool_calls: list[ExecutableToolCall],
        schedule: ToolSchedule,
        options: Optional[ToolBatchExecuteOptions] = None,
    ) -> AsyncGenerator[ToolBatchEvent, None]:
        agen = execute_tool_schedule(
            self.deps,
            lambda batch, batch_options: self.execute_batch(batch, batch_options),
            tool_calls,
            schedule,
            options,
        )
        async for event in agen:
            yield event


def _default_workspace_root(options: ToolExecutorOptions) -> Any:
    if options.workspace_root is not None:
        return options.workspace_root
    if options.get_working_directory is not None:
        return options.get_working_directory()
    return options.working_directory or "."


def create_tool_executor(options: ToolExecutorOptions) -> ToolExecutor:
    return ToolExecutorImpl(options)
