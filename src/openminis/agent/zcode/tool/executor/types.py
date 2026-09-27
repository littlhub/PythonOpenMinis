"""Tool executor types — executor options, deps and the ToolExecutor contract.

Ported from: packages/core/src/tool/executor/types.ts
Original package: @zcode/core (tool/executor)

# PORT: 与 ``tool/types.ts`` 重叠的类型（``ExecutableToolCall`` / ``ReadFileStateMap`` /
# ``ToolBatchEvent`` / ``BackgroundTaskControlPort`` / ``ToolExecutionResult`` /
# ``ToolRuntimeScope``）按规则从 ``..types`` 复用，只有 executor 特有的类型在此新建。
#
# PORT: 大量 ``@zcode/contracts`` 端口/载荷类型尚未移植（``*Port``、``Model``、
# ``SessionEvent``、``TraceContext`` …）。按 ``tool/types.py`` 已确立的先例，这些字段保留为
# loose ``Any``，每个字段的 ``# PORT:`` 注记写明其原始声明来源，绝不删字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional, Protocol

from ...contracts.ids import SessionId, TurnId
from ...core.abort import AbortSignal
from ...permission.ports import CollaborationMode, PermissionBrokerPort
from ...permission.service import PermissionService
from ..registry import ToolRegistry
from ..scheduler import ToolSchedule
from ..types import (
    BackgroundTaskControlPort,
    ExecutableToolCall,
    ReadFileStateMap,
    ToolBatchEvent,
    ToolExecutionResult,
    ToolRuntimeScope,
)

__all__ = [
    "BackgroundTaskNotificationCommand",
    "EnqueueBackgroundTaskNotification",
    "BackgroundTaskNotificationPolicyInput",
    "ShouldEnqueueBackgroundTaskNotification",
    "ToolExecutorOptions",
    "ToolExecutor",
    "ToolExecuteOptions",
    "ToolBatchExecuteOptions",
    "ToolExecutorDeps",
]


# -----------------------------------------------
# Background task notifications
# -----------------------------------------------

@dataclass(slots=True)
class BackgroundTaskNotificationCommand:
    """Mirrors ``interface BackgroundTaskNotificationCommand``."""

    text: str
    # PORT: ``TraceContext`` — @zcode/contracts (tracing), not ported yet.
    trace_context: Any
    # PORT: ``BackgroundResultOriginMeta`` — @zcode/contracts, not ported yet.
    origin_meta: Any = None
    task_id: Optional[str] = None
    tool_name: Optional[str] = None


# PORT: TS ``(notification) => undefined`` —— 同步、无返回值。
EnqueueBackgroundTaskNotification = Callable[[BackgroundTaskNotificationCommand], None]


@dataclass(slots=True)
class BackgroundTaskNotificationPolicyInput:
    """Mirrors ``interface BackgroundTaskNotificationPolicyInput``."""

    runtime_scope: ToolRuntimeScope
    status: str
    task_id: str
    tool_name: str
    # PORT: ``TraceContext`` — @zcode/contracts (tracing), not ported yet.
    trace_context: Any


ShouldEnqueueBackgroundTaskNotification = Callable[[BackgroundTaskNotificationPolicyInput], bool]


# -----------------------------------------------
# ToolExecutorOptions
# -----------------------------------------------

@dataclass(slots=True)
class ToolExecutorOptions:
    """Mirrors ``interface ToolExecutorOptions``.

    # PORT: TS 中必填的 ``registry`` / ``permissionService`` / ``emitEvent`` / ``sessionId``
    # 被提到可选字段之前（Python dataclass 的排序要求）；字段名与语义不变。
    """

    # 必填（TS）
    registry: ToolRegistry
    permission_service: PermissionService
    emit_event: Callable[[Any], Awaitable[None]]  # SessionEvent → Awaitable[None]
    session_id: SessionId

    # 可选
    # PORT: ``AgentExecutionTelemetryPort`` — @zcode/contracts, not ported yet.
    agent_telemetry: Any = None
    # PORT: ``AgentTelemetryActorKind`` — @zcode/contracts, not ported yet.
    agent_telemetry_actor_kind: Any = None
    permission_broker: Optional[PermissionBrokerPort] = None
    enqueue_background_task_notification: Optional[EnqueueBackgroundTaskNotification] = None
    should_enqueue_background_task_notification: Optional[ShouldEnqueueBackgroundTaskNotification] = None
    turn_id: Optional[TurnId] = None
    default_timeout_ms: Optional[int] = None
    permission_timeout_ms: Optional[int] = None
    # PORT: ``Logger`` — @zcode/contracts, not ported yet.
    logger: Any = None
    background_task_control_port: Optional[BackgroundTaskControlPort] = None
    # PORT: ``ExecutionPort`` — @zcode/contracts, not ported yet.
    execution_port: Any = None
    # PORT: ``BrowserControlPort`` — @zcode/contracts, not ported yet.
    browser_control_port: Any = None
    browser_documentation_root: Optional[str] = None
    # PORT: ``FileSystemPort`` — @zcode/contracts, not ported yet.
    file_system_port: Any = None
    # PORT: ``HttpClientPort`` — @zcode/contracts, not ported yet.
    http_client_port: Any = None
    # PORT: ``ImageProcessorPort`` — @zcode/contracts, not ported yet.
    image_processor_port: Any = None
    # PORT: ``PdfDocumentPort`` — @zcode/contracts, not ported yet.
    pdf_document_port: Any = None
    # PORT: ``Model`` — @zcode/contracts (model), not ported yet.
    model: Any = None
    # PORT: ``EmbeddedSearchBackend`` — @zcode/contracts, not ported yet.
    embedded_search_backend: Any = None
    native_search_enhancements_enabled: Optional[bool] = None
    # PORT: ``SkillPort`` — @zcode/contracts, not ported yet.
    skill_port: Any = None
    # PORT: ``SubagentPort`` — @zcode/contracts, not ported yet.
    subagent_port: Any = None
    # PORT: ``CoordinatorResponsePort`` — @zcode/contracts, not ported yet.
    coordinator_response_port: Any = None
    # PORT: ``WorkflowSubmitPort`` — @zcode/contracts, not ported yet.
    workflow_submit_port: Any = None
    # actor 的升级端口；存在即为该会话注册 escalate。
    # PORT: ``WorkflowEscalatePort`` — @zcode/contracts, not ported yet.
    workflow_escalate_port: Any = None
    # PORT: ``ToolArtifactStorePort`` — @zcode/contracts, not ported yet.
    artifact_store: Any = None
    # PORT: ``AutomationPort`` — @zcode/contracts, not ported yet.
    automation_port: Any = None
    # PORT: ``OffPeakPort`` — @zcode/contracts, not ported yet.
    off_peak_port: Any = None
    # PORT: ``SessionStorePort`` — @zcode/contracts, not ported yet.
    session_store: Any = None
    # PORT: ``SessionModePort`` — @zcode/contracts, not ported yet.
    session_mode_port: Any = None
    # PORT: ``WorkflowPort`` — @zcode/contracts, not ported yet.
    workflow_port: Any = None
    # workflow run 端口；按工具名查表时 CreateWorkflow 的后台生命周期提供者。
    # PORT: ``DynamicWorkflowRunPort`` — @zcode/contracts, not ported yet.
    dynamic_workflow_run_port: Any = None
    # PORT: ``DynamicWorkflowSnippetPort`` — @zcode/contracts, not ported yet.
    dynamic_workflow_snippet_port: Any = None
    # 模型目录端口；缺席则 ListModels 报能力缺席，CreateWorkflow 的 subagent_model 被拒。
    # PORT: ``ModelCatalogPort`` — @zcode/contracts, not ported yet.
    model_catalog_port: Any = None
    # PORT: ``RuntimeTaskRegistry`` — core/src/runtime-task/registry.ts, not ported yet.
    runtime_task_registry: Any = None
    read_file_state: Optional[ReadFileStateMap] = None
    subagent_background_bash_max_ms: Optional[int] = None
    # PORT: ``ExecutionShellSelection`` — @zcode/contracts, not ported yet.
    bash_shell_selection: Any = None
    get_bash_shell_selection: Optional[Callable[[], Any]] = None
    working_directory: Optional[str] = None
    workspace_root: Optional[str] = None
    workspace_identity: Optional[str] = None
    remote_session_id: Optional[str] = None
    client_mode: Optional[str] = None  # "desktop-continuous" | "web-remote-replayable"
    delivery_kind: Optional[str] = None  # "desktop-continuous" | "web-remote-replayable"
    runtime_scope: Optional[ToolRuntimeScope] = None
    get_working_directory: Optional[Callable[[], str]] = None
    set_working_directory: Optional[Callable[[str], Any]] = None
    get_workspace_root: Optional[Callable[[], str]] = None
    get_memory_root: Optional[Callable[[], Optional[str]]] = None
    # PORT: ``TraceContext`` — @zcode/contracts (tracing), not ported yet.
    trace_context: Any = None
    mode: Optional[CollaborationMode] = None
    get_mode: Optional[Callable[[], CollaborationMode]] = None
    max_concurrency: Optional[int] = None
    # PORT: ``HookRunner`` — core/src/hooks/index.ts, not ported yet.
    hook_runner: Any = None


# -----------------------------------------------
# ToolExecutor contract
# -----------------------------------------------

class ToolExecutor(Protocol):
    """Mirrors ``interface ToolExecutor``.

    # PORT: TS method contract → ``Protocol``（ZCORE_PORTING.md §1）。
    """

    async def execute(
        self,
        tool_call: ExecutableToolCall,
        options: Optional["ToolExecuteOptions"] = None,
    ) -> ToolExecutionResult:
        raise NotImplementedError

    async def execute_batch(
        self,
        tool_calls: list[ExecutableToolCall],
        options: Optional["ToolBatchExecuteOptions"] = None,
    ) -> list[ToolExecutionResult]:
        raise NotImplementedError

    def execute_schedule(
        self,
        tool_calls: list[ExecutableToolCall],
        schedule: ToolSchedule,
        options: Optional["ToolBatchExecuteOptions"] = None,
    ) -> AsyncGenerator[ToolBatchEvent, None]:
        """Mirrors ``executeSchedule(toolCalls, schedule, options?)``.

        # PORT: TS ``AsyncGenerator<ToolBatchEvent, ToolExecutionResult[], void>`` 的第三参数
        # （next 类型）与第二参数（return 类型）在 Python ``AsyncGenerator`` 里无法完整表达；
        # 第二参数保留为 ``None``（发送值未使用），返回类型见 docstring。
        """
        raise NotImplementedError

    async def track_external_background_task(
        self,
        tool_call: ExecutableToolCall,
        output: dict[str, Any],
        trace_context: Any,
        turn_id: Optional[TurnId] = None,
    ) -> None:
        """把一个**不由本回合工具调用启动**的后台任务纳入追踪（dwf run 的 resume 重臂）。

        ``toolCall`` 是调用方合成的描述子（id = 原始 toolCallId、name 决定 per-tool 生命周期
        分派）——tracker 只读它的 id/name/input，不要求一个真实在飞的工具调用。效果与 submit
        路径完全同源：runtime-task registry 登记（会话回收护栏）、BackgroundTaskStarted
        （backgroundWorks 面板 + cancellable）、轮询/终态 waiter、结算通知。同 taskId 重复调用
        由 tracker 的 poller 去重（幂等）。
        """
        raise NotImplementedError


# -----------------------------------------------
# Execute options
# -----------------------------------------------

@dataclass(slots=True)
class ToolExecuteOptions:
    """Mirrors ``interface ToolExecuteOptions``."""

    automation_turn: Optional[bool] = None
    off_peak_turn: Optional[bool] = None
    signal: Optional[AbortSignal] = None
    # PORT: ``TraceContext`` — @zcode/contracts (tracing), not ported yet.
    trace_context: Any = None
    # PORT: ``SubagentRunOptions["modelOverride"]`` — @zcode/contracts, not ported yet.
    subagent_model_override: Any = None
    # PORT: ``Model`` — @zcode/contracts (model), not ported yet.
    model: Any = None


@dataclass(slots=True)
class ToolBatchExecuteOptions(ToolExecuteOptions):
    """Mirrors ``interface ToolBatchExecuteOptions extends ToolExecuteOptions``."""

    max_concurrency: Optional[int] = None


# -----------------------------------------------
# ToolExecutorDeps
# -----------------------------------------------

@dataclass(slots=True)
class ToolExecutorDeps:
    """Mirrors ``interface ToolExecutorDeps``.

    # PORT: TS 必填字段提到可选字段之前（Python dataclass 的排序要求）；字段名与语义不变。
    """

    # 必填（TS）
    registry: ToolRegistry
    permission_service: PermissionService
    permission_broker: PermissionBrokerPort
    emit_event: Callable[[Any], Awaitable[None]]  # SessionEvent → Awaitable[None]
    session_id: SessionId
    default_timeout_ms: int
    read_file_state: ReadFileStateMap
    get_working_directory: Callable[[], str]
    get_workspace_root: Callable[[], str]
    runtime_scope: ToolRuntimeScope
    get_mode: Callable[[], CollaborationMode]
    max_concurrency: int

    # 可选
    # PORT: ``AgentExecutionTelemetryPort`` — @zcode/contracts, not ported yet.
    agent_telemetry: Any = None
    # PORT: ``AgentTelemetryActorKind`` — @zcode/contracts, not ported yet.
    agent_telemetry_actor_kind: Any = None
    enqueue_background_task_notification: Optional[EnqueueBackgroundTaskNotification] = None
    should_enqueue_background_task_notification: Optional[ShouldEnqueueBackgroundTaskNotification] = None
    turn_id: Optional[TurnId] = None
    permission_timeout_ms: Optional[int] = None
    # PORT: ``Logger`` — @zcode/contracts, not ported yet.
    logger: Any = None
    background_task_control_port: Optional[BackgroundTaskControlPort] = None
    # PORT: ``ExecutionPort`` — @zcode/contracts, not ported yet.
    execution_port: Any = None
    # PORT: ``BrowserControlPort`` — @zcode/contracts, not ported yet.
    browser_control_port: Any = None
    browser_documentation_root: Optional[str] = None
    # PORT: ``FileSystemPort`` — @zcode/contracts, not ported yet.
    file_system_port: Any = None
    # PORT: ``HttpClientPort`` — @zcode/contracts, not ported yet.
    http_client_port: Any = None
    # PORT: ``ImageProcessorPort`` — @zcode/contracts, not ported yet.
    image_processor_port: Any = None
    # PORT: ``PdfDocumentPort`` — @zcode/contracts, not ported yet.
    pdf_document_port: Any = None
    # PORT: ``Model`` — @zcode/contracts (model), not ported yet.
    model: Any = None
    # PORT: ``EmbeddedSearchBackend`` — @zcode/contracts, not ported yet.
    embedded_search_backend: Any = None
    native_search_enhancements_enabled: Optional[bool] = None
    # PORT: ``SkillPort`` — @zcode/contracts, not ported yet.
    skill_port: Any = None
    # PORT: ``SubagentPort`` — @zcode/contracts, not ported yet.
    subagent_port: Any = None
    # PORT: ``CoordinatorResponsePort`` — @zcode/contracts, not ported yet.
    coordinator_response_port: Any = None
    # PORT: ``WorkflowSubmitPort`` — @zcode/contracts, not ported yet.
    workflow_submit_port: Any = None
    # actor 的升级端口；存在即为该会话注册 escalate。
    # PORT: ``WorkflowEscalatePort`` — @zcode/contracts, not ported yet.
    workflow_escalate_port: Any = None
    # PORT: ``ToolArtifactStorePort`` — @zcode/contracts, not ported yet.
    artifact_store: Any = None
    # PORT: ``AutomationPort`` — @zcode/contracts, not ported yet.
    automation_port: Any = None
    # PORT: ``OffPeakPort`` — @zcode/contracts, not ported yet.
    off_peak_port: Any = None
    # PORT: ``SessionStorePort`` — @zcode/contracts, not ported yet.
    session_store: Any = None
    # PORT: ``SessionModePort`` — @zcode/contracts, not ported yet.
    session_mode_port: Any = None
    # PORT: ``WorkflowPort`` — @zcode/contracts, not ported yet.
    workflow_port: Any = None
    # workflow run 端口；按工具名查表时 CreateWorkflow 的后台生命周期提供者。
    # PORT: ``DynamicWorkflowRunPort`` — @zcode/contracts, not ported yet.
    dynamic_workflow_run_port: Any = None
    # PORT: ``DynamicWorkflowSnippetPort`` — @zcode/contracts, not ported yet.
    dynamic_workflow_snippet_port: Any = None
    # 模型目录端口；缺席则 ListModels 报能力缺席，CreateWorkflow 的 subagent_model 被拒。
    # PORT: ``ModelCatalogPort`` — @zcode/contracts, not ported yet.
    model_catalog_port: Any = None
    # PORT: ``RuntimeTaskRegistry`` — core/src/runtime-task/registry.ts, not ported yet.
    runtime_task_registry: Any = None
    subagent_background_bash_max_ms: Optional[int] = None
    # PORT: ``ExecutionShellSelection`` — @zcode/contracts, not ported yet.
    bash_shell_selection: Any = None
    get_bash_shell_selection: Optional[Callable[[], Any]] = None
    workspace_identity: Optional[str] = None
    remote_session_id: Optional[str] = None
    client_mode: Optional[str] = None  # "desktop-continuous" | "web-remote-replayable"
    delivery_kind: Optional[str] = None  # "desktop-continuous" | "web-remote-replayable"
    set_working_directory: Optional[Callable[[str], Any]] = None
    get_memory_root: Optional[Callable[[], Optional[str]]] = None
    # PORT: ``TraceContext`` — @zcode/contracts (tracing), not ported yet.
    trace_context: Any = None
    # PORT: ``HookRunner`` — core/src/hooks/index.ts, not ported yet.
    hook_runner: Any = None
