"""Tool types — core tool types for registry and executor.

Ported from: packages/core/src/tool/types.ts
Original package: @zcode/core (tool)

# PORT: ``types.ts`` is a pure *type* module upstream. Python keeps every
# declaration so the registry/executor ports read side by side with the
# original: ``interface`` → ``@dataclass``, behavioural contracts → ``Protocol``,
# discriminated unions → one dataclass variant per member plus a ``Union`` alias,
# inline anonymous object shapes → ``TypedDict``, and type aliases kept verbatim.
#
# # PORT: Ports and payload types owned by ``@zcode/contracts`` that are outside
# the set of contract modules ported so far (``*Port`` interfaces, session /
# telemetry payloads, ``Model`` …) are kept as loose ``Any`` fields. No field is
# dropped — each loose field carries a ``# PORT:`` note naming its original
# declaration. This mirrors the precedent already set in ``contracts/model.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import (
    Any,
    Awaitable,
    Callable,
    Literal,
    Optional,
    Protocol,
    Sequence,
    TypedDict,
    Union,
)

from ..contracts.ids import SessionId, TraceId, TurnId
from ..contracts.model import JsonSchema, ModelMessageContent, ModelToolSideEffectScope
from ..contracts.tools import (
    ProviderNativeToolSpec,
    RiskLevel,
    ToolCancellationPolicy,
    ToolContractDeclaration,
    ToolExecutionMode,
    ToolResultBudgetStrategy,
    ToolTimeoutPolicy,
)
from ..core.abort import AbortSignal

__all__ = [
    # Metadata
    "ToolMetadata",
    "ToolMcpPresentation",
    # Execution context
    "ToolRuntimeScope",
    "BackgroundTaskControlStopOptions",
    "BackgroundTaskControlStopResult",
    "BackgroundTaskControlPort",
    "ToolExecutionContext",
    "ToolEmbeddedSearchContext",
    "ReadFileStateEntry",
    "ReadFileStateMap",
    # Handler
    "ToolHandlerFailure",
    "ToolInputValidationContext",
    "ToolInputValidationSuccess",
    "ToolInputValidationResult",
    "ToolInputResolutionContext",
    "ToolInputResolutionSuccess",
    "ToolInputResolutionResult",
    "ToolHandler",
    # Entry
    "ToolEntry",
    "ToolModelContractProjection",
    "ToolApprovalGate",
    "ToolApprovalGateProceed",
    "ToolApprovalGateAsk",
    "ToolPermissionRulePolicy",
    "ToolPersistedModelContentInput",
    "ToolRuntimePermissionCapability",
    "ToolRuntimePermissionCapabilityContext",
    "ToolExecutionModelContext",
    # Execution results
    "ToolExecutionResult",
    "ToolExecutionError",
    "ToolExecutionFollowUpUserInput",
    "ToolExecutionTurnControl",
    "ToolResultSerialization",
    "ToolBatchResult",
    # Executable tool call
    "ExecutableToolCall",
    # Batch events
    "ToolBatchEvent",
    "ToolBatchStartEvent",
    "ToolBatchCompleteEvent",
    "ToolStartEvent",
    "ToolCompleteEvent",
    "ToolErrorEvent",
]


# -----------------------------------------------
# Tool Metadata
# -----------------------------------------------

@dataclass(slots=True)
class ToolMcpPresentation:
    """MCP discovery 的可信展示来源；只参与 UI 投影，不参与权限判定。

    # PORT: anonymous inline object on ``ToolMetadata.mcpPresentation``; given a
    # name here because Python needs one.
    """

    server_name: str
    tool_name: str
    description: Optional[str] = None
    # 来自声明 zcode_official 鉴权的 MCP server；仅用于信任其结果里的结构化标识。
    official: Optional[bool] = None


@dataclass(slots=True)
class ToolMetadata:
    """Mirrors ``interface ToolMetadata``.

    # PORT: TS declares the optionals before the required operators
    # (``readOnly`` / ``destructive`` / ``concurrentSafe``); Python dataclasses
    # require non-default fields first, so the required fields are listed up
    # front. Names and semantics are unchanged.
    """

    name: str
    read_only: bool
    destructive: bool
    concurrent_safe: bool
    side_effect_scope: ModelToolSideEffectScope
    risk_level: RiskLevel
    needs_approval: bool

    description: Optional[str] = None
    model_instructions: Optional[Sequence[str]] = None
    allowed_in_plan_mode: Optional[bool] = None
    requires_user_interaction: Optional[bool] = None
    timeout_ms: Optional[int] = None
    max_output_bytes: Optional[int] = None
    provider_visible: Optional[bool] = None
    # 声明该工具是“成功即终止 turn”的终态工具：一旦返回成功结果，executor 就在该结果上挂
    # turnControl 终止当前 turn。这是工具的内在能力声明（像 concurrentSafe/destructive），
    # 由 executor 读取，而不是在调用点按工具名猜测。submit_result 用它实现 actor 的终态提交。
    stop_turn_on_success: Optional[bool] = None
    mcp_presentation: Optional[ToolMcpPresentation] = None


# -----------------------------------------------
# Tool Execution Context
# -----------------------------------------------

ToolRuntimeScope = str  # "main" | "subagent"


@dataclass(slots=True)
class BackgroundTaskControlStopOptions:
    """Mirrors ``interface BackgroundTaskControlStopOptions``."""

    strict: bool
    # 谁在停：TaskStop 填 "model"，终态通知据此措辞。
    initiator: Optional[str] = None  # "user" | "model"
    # PORT: ``TraceContext`` — @zcode/contracts (telemetry), not ported yet.
    trace_context: Any = None


@dataclass(slots=True)
class BackgroundTaskControlStopResult:
    """Mirrors ``interface BackgroundTaskControlStopResult``."""

    ok: bool
    task_id: str
    command: Optional[str] = None
    reason: Optional[str] = None  # "background_task_cancel_not_supported" | "background_task_not_found" | "background_task_not_running"
    status: Optional[str] = None
    type: Optional[str] = None


class BackgroundTaskControlPort(Protocol):
    """Mirrors ``interface BackgroundTaskControlPort``.

    # PORT: TS method contract → ``Protocol`` (see ZCORE_PORTING.md §5).
    """

    async def stop_background_task(
        self,
        task_id: str,
        options: BackgroundTaskControlStopOptions,
    ) -> BackgroundTaskControlStopResult:
        raise NotImplementedError


@dataclass(slots=True)
class ToolExecutionContext:
    """Mirrors ``interface ToolExecutionContext``.

    # PORT: required fields (``toolCallId`` / ``traceId`` / ``abortSignal`` /
    # ``workingDirectory`` / ``workspaceRoot`` / ``sessionId``) are hoisted above
    # the optionals for dataclass ordering; every TS field is present.
    """

    tool_call_id: str
    trace_id: TraceId
    abort_signal: AbortSignal
    working_directory: str
    workspace_root: str
    session_id: SessionId

    # 当前 Tool 的实时观测写入器。Handler 只能通过窄接口写事实，不能接触原始 OTel Span。
    # PORT: ``ToolExecutionSpanWriter`` — @zcode/contracts, not ported yet.
    telemetry: Any = None
    # 当前工具调用是否属于 automation 派发轮；写工具 handler 用它做最终权限校验。
    automation_turn: Optional[bool] = None
    # 当前工具调用是否属于闲时任务派发轮；OffPeakCreate handler 用它做最终拒绝。
    off_peak_turn: Optional[bool] = None
    # PORT: ``TraceContext`` — @zcode/contracts, not ported yet.
    trace_context: Any = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    background_task_control_port: Optional[BackgroundTaskControlPort] = None
    # PORT: ``SessionEvent`` — @zcode/contracts, not ported yet.
    emit_event: Optional[Callable[[Any], Awaitable[None]]] = None
    # PORT: ``ExecutionPort`` — @zcode/contracts, not ported yet.
    execution_port: Any = None
    # browser-use 控制端口；node_repl 的 agent.browsers.* 经此执行。缺省则 browser 不可用。
    # PORT: ``BrowserControlPort`` — @zcode/contracts, not ported yet.
    browser_control_port: Any = None
    # 官方 browser-use plugin docs 资产目录；只在 browser-use 启用时用于 agent.browsers.documentation()。
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
    # Core Server 对前台 child 的 Selection override。
    # PORT: ``SubagentRunOptions["modelOverride"]`` — @zcode/contracts, not ported yet.
    subagent_model_override: Any = None
    # PORT: ``SkillPort`` — @zcode/contracts, not ported yet.
    skill_port: Any = None
    # PORT: ``SubagentPort`` — @zcode/contracts, not ported yet.
    subagent_port: Any = None
    # PORT: ``CoordinatorResponsePort`` — @zcode/contracts, not ported yet.
    coordinator_response_port: Any = None
    # 工作流 actor 提交终态结果并等待引擎裁决的端口；仅在 workflow actor 会话注入。
    # PORT: ``WorkflowSubmitPort`` — @zcode/contracts, not ported yet.
    workflow_submit_port: Any = None
    # 工作流 actor 升级阻塞问题并等待主代理作答的端口；仅在 workflow actor 会话注入。
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
    # workflow run 提交端口；缺席则 CreateWorkflow 回占位诊断而不启动。
    # PORT: ``DynamicWorkflowRunPort`` — @zcode/contracts, not ported yet.
    dynamic_workflow_run_port: Any = None
    # dwf snippet 同步执行端口；缺席则 EvalWorkflowSnippet 报能力缺席的业务失败。
    # PORT: ``DynamicWorkflowSnippetPort`` — @zcode/contracts, not ported yet.
    dynamic_workflow_snippet_port: Any = None
    # 模型目录端口；缺席则 ListModels 报能力缺席，CreateWorkflow 的 subagent_model 被拒。
    # PORT: ``ModelCatalogPort`` — @zcode/contracts, not ported yet.
    model_catalog_port: Any = None
    # PORT: ``RuntimeTaskRegistry`` — core/src/runtime-task/registry.ts, not ported yet.
    runtime_task_registry: Any = None
    read_file_state: Optional[ReadFileStateMap] = None
    # PORT: ``PersistedReadFileStateMetadata`` — core/src/tool/read-file-state-metadata.ts,
    # not ported yet.
    record_read_file_state_metadata: Optional[Callable[[Any], None]] = None
    # 记录 Skill resolved metadata；仅用于 telemetry，不改变模型可见结果。
    # PORT: ``SkillTelemetryMetadata`` — @zcode/contracts, not ported yet.
    record_skill_telemetry_metadata: Optional[Callable[[Any], None]] = None
    # PORT: ``ExecutionShellSelection`` — @zcode/contracts, not ported yet.
    bash_shell_selection: Any = None
    embedded_search: Optional[ToolEmbeddedSearchContext] = None
    set_working_directory: Optional[Callable[[str], Union[Awaitable[None], None]]] = None
    workspace_identity: Optional[str] = None
    remote_session_id: Optional[str] = None
    client_mode: Optional[str] = None  # "desktop-continuous" | "web-remote-replayable"
    delivery_kind: Optional[str] = None  # "desktop-continuous" | "web-remote-replayable"
    memory_root: Optional[str] = None
    runtime_scope: Optional[ToolRuntimeScope] = None
    provider_visible_tool_names: Optional[Sequence[str]] = None
    turn_id: Optional[TurnId] = None


@dataclass(slots=True)
class ToolEmbeddedSearchContext:
    """Mirrors ``interface ToolEmbeddedSearchContext``."""

    enabled: bool
    # PORT: ``EmbeddedSearchBackend`` — @zcode/contracts, not ported yet.
    backend: Any = None
    find_and_grep_enabled: Optional[bool] = None


@dataclass(slots=True)
class ReadFileStateEntry:
    """Mirrors ``interface ReadFileStateEntry``."""

    path: str
    content: str
    is_partial_view: bool
    read_at: datetime
    offset: Optional[int] = None
    limit: Optional[int] = None
    source_tool: Optional[str] = None  # "Read" | "Write" | "Edit"
    revision_id: Optional[str] = None
    mtime_ms: Optional[int] = None
    size_bytes: Optional[int] = None


ReadFileStateMap = dict[str, ReadFileStateEntry]


# -----------------------------------------------
# Tool Handler
# -----------------------------------------------

@dataclass(slots=True)
class ToolHandlerFailure:
    """tool handler 用该返回值表达可预期业务失败；成功 output 不使用此保留形状。

    # PORT: TS literal discriminant ``result: false`` is modelled as an
    # ``init=False`` field fixed to ``False``.
    """

    error_code: int
    message: str
    result: bool = field(default=False, init=False)


@dataclass(slots=True)
class ToolInputValidationContext:
    """Mirrors ``interface ToolInputValidationContext``."""

    # PORT: ``RuntimeTaskRegistry`` — core/src/runtime-task/registry.ts, not ported yet.
    runtime_task_registry: Any = None


class ToolInputValidationSuccess(TypedDict):
    """``{ result: true }`` — successful input-validation outcome.

    # PORT: anonymous inline member of ``ToolInputValidationResult``; modelled as
    # a ``TypedDict`` because it is a loose payload (ZCORE_PORTING.md §1).
    """

    result: Literal[True]


ToolInputValidationResult = Union[ToolInputValidationSuccess, ToolHandlerFailure]


@dataclass(slots=True)
class ToolInputResolutionContext:
    """{@link ToolEntry.resolveInput} 的上下文。窄到只有解析真正需要的东西——工作目录是「项目内
    的东西住在哪」的唯一入口，再多给就会把一个归一化钩子变成第二个执行入口。

    Mirrors ``interface ToolInputResolutionContext``.
    """

    working_directory: Optional[str] = None
    # PORT: ``RuntimeTaskRegistry`` — core/src/runtime-task/registry.ts, not ported yet.
    runtime_task_registry: Any = None
    # workflow run 端口与本会话 id：AmendWorkflow 用它们把 `run_id` 解析成「前驱是不是本会话的、
    # 还在不在跑」的事实块。仍然只读——
    # 归一化钩子不因此变成第二个执行入口。
    # PORT: ``DynamicWorkflowRunPort`` — @zcode/contracts, not ported yet.
    dynamic_workflow_run_port: Any = None
    # 模型目录端口：CreateWorkflow / AmendWorkflow 用它把 `subagent_model` 解析成规范形，解不出来
    # 在确认窗**之前**就作为业务失败退回。
    # 同步、只读，与 `dynamicWorkflowRunPort` 同一条理由待在这里。缺席时给了字段即被拒——
    # 不静默放行一个宿主解不了的字符串。
    # PORT: ``ModelCatalogPort`` — @zcode/contracts, not ported yet.
    model_catalog_port: Any = None
    session_id: Optional[str] = None


class ToolInputResolutionSuccess(TypedDict):
    """``{ result: true; input: unknown }`` — successful input-resolution outcome.

    # PORT: anonymous inline member of ``ToolInputResolutionResult``.
    """

    result: Literal[True]
    input: Any


ToolInputResolutionResult = Union[ToolInputResolutionSuccess, ToolHandlerFailure]

# PORT: TS generic ``ToolHandler<TInput, TOutput>`` — Python erases the type
# parameters into a single ``Callable`` alias (see task spec).
ToolHandler = Callable[[Any, "ToolExecutionContext"], Awaitable[Any]]


# -----------------------------------------------
# Tool Entry
# -----------------------------------------------

@dataclass(slots=True, kw_only=True)
class ToolEntry(ToolContractDeclaration):
    """Mirrors ``interface ToolEntry extends ToolContractDeclaration``.

    # PORT: modelled as a dataclass subclass of ``ToolContractDeclaration``. The
    # fields the TS interface re-declares identically (``executionMode``,
    # ``providerNative``, ``inputSchema``, ``timeout``, ``cancellation``) are
    # inherited from the base declaration instead of being repeated.
    # ``kw_only=True`` is required so the two TS-required additions
    # (``metadata`` / ``handler``) can follow the base's defaulted fields.
    """

    # Host-issued atomicity policy for model content. Only an authority-verified
    # registration path may set this; executor code must never infer it from a
    # tool name or from model-visible content.
    # PORT: ``ModelContentProtection["kind"]`` — @zcode/contracts, not ported yet.
    model_content_protection: Optional[str] = None
    # Optional provider-visible character threshold for tools whose upstream contract
    # budgets UTF-16 characters rather than UTF-8 bytes.
    max_model_chars: Optional[int] = None
    # MIME type for provider-visible model content persisted by the result budget.
    # Defaults to the handler result shape when omitted.
    result_artifact_content_type: Optional[str] = None
    aliases: Optional[Sequence[str]] = None
    metadata: Optional[ToolMetadata] = None
    # 只由宿主验证后的可信来源写入；不能从模型可见的 MCP 名称或 descriptor 推导。
    # PORT: ``PermissionCapabilityGroup`` — @zcode/contracts, not ported yet.
    permission_capability_group: Any = None
    handler: Optional[ToolHandler] = None
    # 当前 turn 模型能力对 provider descriptor 与 executor schema 的同源投影。
    resolve_model_contract: Optional[
        Callable[[ToolExecutionModelContext], "ToolModelContractProjection"]
    ] = None
    validate_input: Optional[
        Callable[[Any, ToolInputValidationContext], ToolInputValidationResult]
    ] = None
    # 把模型发出的入参**归一化成将要发生的执行事实**。executor 在 `validateInput` 之后、
    # PreToolUse hook 之前调用，返回值直接替换 `executionInput`。
    #
    # 位置就是全部的意义。此后 hook、项目权限规则、权限事件载荷、`prepareApproval`、handler
    # 读到的都是同一份归一化输入，于是三件事一次到位：
    #   1. 策略不被绕开——一条扫描脚本的 PreToolUse hook 在 saved run 上也能看到真正的脚本；
    #   2. 跨版本可见——入参通道对每个客户端版本都是无 schema 的透传，而 display 通道不是；
    #   3. 确认与执行同字节——只解析一次，那份字节一路带到 handler，不存在批准 A 跑 B。
    #
    # 因此返回值必须仍然满足 `inputSchema` / `runtimeInputSchema`（hook 改写后 executor 会
    # 再校验一次）。解析失败回 {@link ToolHandlerFailure}，executor 在 hook 之前收口——那是
    # 业务失败，不是基础设施故障，不该先打断用户一次确认。
    resolve_input: Optional[
        Callable[
            [Any, ToolInputResolutionContext],
            Union[Awaitable[ToolInputResolutionResult], ToolInputResolutionResult],
        ]
    ] = None
    format_model_content: Optional[Callable[[Any], ModelMessageContent]] = None
    format_persisted_model_content: Optional[
        Callable[[ToolPersistedModelContentInput], Optional[ModelMessageContent]]
    ] = None
    resolve_timeout_budget_ms: Optional[
        Callable[[Any, Optional[ToolExecutionModelContext]], Optional[int]]
    ] = None
    resolve_permission_capability: Optional[
        Callable[
            [Any, Optional[ToolRuntimePermissionCapabilityContext]],
            Optional[ToolRuntimePermissionCapability],
        ]
    ] = None
    resolve_permission_rule_policy: Optional[
        Callable[
            [Any, Optional[ToolRuntimePermissionCapabilityContext]],
            Optional[ToolPermissionRulePolicy],
        ]
    ] = None
    # Last word on an `ask` decision, owned by the tool. Runs after the permission service
    # has already decided to ask, so it can only narrow the ask to a pass (`proceed`) or
    # enrich it with a preview — it can never turn an allow into an ask.
    #
    # Synchronous like the other permission hooks: it inspects the input the executor
    # already holds and must not perform I/O on the approval path. A tool that needs to
    # read the world before it can build a preview belongs in {@link resolveInput}, which
    # runs earlier, is async, and whose result the whole downstream chain shares.
    prepare_approval: Optional[Callable[[Any], ToolApprovalGate]] = None
    runtime_input_schema: Any = None
    runtime_output_schema: Any = None

    def __post_init__(self) -> None:
        """Normalize a literal-``metadata`` payload into ``ToolMetadata``.

        PORT: TS ``ToolMetadata`` 是结构化类型，handler 直接写字面量。本仓的 handler 同样
        按字面量形态传 ``dict``（15 处），而 registry / executor 全程按属性访问
        （``entry.metadata.name`` / ``.mcp_presentation`` / ``.provider_visible`` …）。
        结构化类型不该在入口报错，故在此一次性归一。
        """
        if isinstance(self.metadata, dict):
            self.metadata = _coerce_tool_metadata(self.metadata)


# PORT: handler 字面量只写实际提供的键，其余留给 ``ToolMetadata`` 的默认值。
_TOOL_METADATA_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "read_only",
        "destructive",
        "concurrent_safe",
        "side_effect_scope",
        "risk_level",
        "needs_approval",
        "description",
        "model_instructions",
        "allowed_in_plan_mode",
        "requires_user_interaction",
        "timeout_ms",
        "max_output_bytes",
        "provider_visible",
        "stop_turn_on_success",
        "mcp_presentation",
    }
)


def _coerce_tool_metadata(payload: dict[str, Any]) -> ToolMetadata:
    """Build ``ToolMetadata`` from an object-literal payload (snake or camel keys)."""
    kwargs: dict[str, Any] = {}
    for key, value in payload.items():
        snake = _to_snake_case(key)
        if snake in _TOOL_METADATA_FIELDS:
            kwargs[snake] = value
    return ToolMetadata(**kwargs)


def _to_snake_case(name: str) -> str:
    out: list[str] = []
    for char in name:
        if char.isupper():
            out.append("_")
            out.append(char.lower())
        else:
            out.append(char)
    return "".join(out).lstrip("_")


class ToolModelContractProjection(TypedDict, total=False):
    """Return shape of ``ToolEntry.resolveModelContract``.

    # PORT: anonymous inline object ``{ description?: string; inputSchema?: JsonSchema }``.
    """

    description: str
    input_schema: JsonSchema


@dataclass(slots=True)
class ToolApprovalGateProceed:
    """``{ gate: "proceed" }`` — mirrors ``ToolApprovalGate``'s ``proceed`` member."""

    gate: str = field(default="proceed", init=False)


@dataclass(slots=True)
class ToolApprovalGateAsk:
    """``{ gate: "ask"; display? }`` — mirrors ``ToolApprovalGate``'s ``ask`` member."""

    # PORT: ``ToolResultDisplayPayload`` — @zcode/contracts, not ported yet.
    display: Any = None
    gate: str = field(default="ask", init=False)


ToolApprovalGate = Union[ToolApprovalGateProceed, ToolApprovalGateAsk]


@dataclass(slots=True)
class ToolPermissionRulePolicy:
    """Mirrors ``interface ToolPermissionRulePolicy``."""

    # PORT: ``PermissionRuleBehavior`` / ``PermissionRuleValue`` — @zcode/contracts,
    # not ported yet; kept loose on the callable signature.
    evaluate_rules: Callable[[Any, Sequence[Any]], bool]
    # PORT: ``PermissionUpdate`` — @zcode/contracts, not ported yet.
    suggested_permission_updates: list[Any]


@dataclass(slots=True)
class ToolPersistedModelContentInput:
    """Mirrors ``interface ToolPersistedModelContentInput``."""

    output: Any
    content: str
    persisted_path: str
    original_bytes: int


@dataclass(slots=True)
class ToolRuntimePermissionCapability:
    """Mirrors ``interface ToolRuntimePermissionCapability``."""

    allowed_in_plan_mode: Optional[bool] = None
    destructive: Optional[bool] = None
    needs_approval: Optional[bool] = None
    read_only: Optional[bool] = None
    requires_user_interaction: Optional[bool] = None
    risk_level: Optional[RiskLevel] = None
    side_effect_scope: Optional[ModelToolSideEffectScope] = None
    # PORT: ``Partial<ToolContractDeclaration["permission"]>`` — @zcode/contracts,
    # not ported yet; a partial ``ToolPermissionSpec`` payload.
    permission: Any = None


@dataclass(slots=True)
class ToolRuntimePermissionCapabilityContext:
    """Mirrors ``interface ToolRuntimePermissionCapabilityContext``."""

    runtime_scope: Optional[ToolRuntimeScope] = None
    working_directory: Optional[str] = None
    workspace_root: Optional[str] = None


@dataclass(slots=True)
class ToolExecutionModelContext:
    """Mirrors ``interface ToolExecutionModelContext``."""

    # PORT: ``Model`` — @zcode/contracts (model), not ported yet.
    model: Any = None


# -----------------------------------------------
# Execution Results
# -----------------------------------------------

@dataclass(slots=True)
class ToolExecutionError:
    """``error?`` payload of ``ToolExecutionResult``.

    # PORT: anonymous inline object; given a name here because Python needs one.
    """

    type: str
    message: str
    code: Optional[str] = None
    detail: Optional[str] = None
    # PORT: ``PermissionBrokerReasonSource`` — @zcode/contracts, not ported yet.
    reason_source: Any = None
    stack: Optional[str] = None


@dataclass(slots=True)
class ToolExecutionFollowUpUserInput:
    """Mirrors ``interface ToolExecutionFollowUpUserInput``."""

    input: str
    # PORT: ``PermissionBrokerReasonSource`` — @zcode/contracts, not ported yet.
    reason_source: Any


@dataclass(slots=True)
class ToolExecutionTurnControl:
    """Mirrors ``interface ToolExecutionTurnControl``."""

    reason: str  # "automation_create_limit" | "plan_exit_denied" | "subagent_terminal"
    stop_turn_after_result: bool


@dataclass(slots=True)
class ToolResultSerialization:
    """Mirrors ``interface ToolResultSerialization``."""

    content: str
    original_bytes: int
    # 实际进入模型请求的字节。对受保护 CUA 结构化帧，序列化文本里 image 块
    # 只渲染为短占位符，但真实 base64 栅格原样发送——因此这里 = 序列化文本
    # 字节 + 真实媒体载荷，成本/用量观测（setOutputBytes、turn-tool-usage、
    # usage-observability）不得少计图片。该 aggregate 是唯一公开计量状态。
    returned_bytes: int
    truncated: bool
    budget_strategy: ToolResultBudgetStrategy
    model_content: Optional[ModelMessageContent] = None
    artifact_path: Optional[str] = None


@dataclass(slots=True)
class ToolExecutionResult:
    """Mirrors ``interface ToolExecutionResult``."""

    tool_call_id: str
    tool_name: str
    success: bool
    output: Any
    duration_ms: int
    started_at: datetime
    completed_at: datetime
    turn_control: Optional[ToolExecutionTurnControl] = None
    follow_up_user_input: Optional[ToolExecutionFollowUpUserInput] = None
    # PORT: ``ToolResultDisplayPayload`` — @zcode/contracts, not ported yet.
    display: Any = None
    model_content: Optional[ModelMessageContent] = None
    # PORT: ``PersistedReadFileStateMetadata`` — core/src/tool/read-file-state-metadata.ts,
    # not ported yet.
    read_file_state_metadata: Any = None
    serialization: Optional[ToolResultSerialization] = None
    # Executor 汇总后的内部性能事实；不进入模型可见 Tool Output。
    # PORT: ``ToolExecutionTelemetry`` —— 定义已落地在 ``handlers/ports.py``
    # （mirrors ``packages/contracts/src/tools/performance.ts``）；此处保持 ``Any`` 以免
    # ``tool/types`` 反向依赖 ``tool/handlers``（上游 types.ts 也从 contracts 直接导入）。
    performance: Any = None
    error: Optional[ToolExecutionError] = None


@dataclass(slots=True)
class ToolBatchResult:
    """Mirrors ``interface ToolBatchResult``."""

    tool_call_id: str
    results: list[ToolExecutionResult]
    all_succeeded: bool


# -----------------------------------------------
# Executable Tool Call
# -----------------------------------------------

@dataclass(slots=True)
class ExecutableToolCall:
    """Mirrors ``interface ExecutableToolCall``."""

    id: str
    name: str
    input: Any


# -----------------------------------------------
# Batch Events
# -----------------------------------------------

@dataclass(slots=True)
class ToolBatchStartEvent:
    """``{ type: "batch_start"; parallelGroupIndex; toolCallIds }``."""

    parallel_group_index: int
    tool_call_ids: list[str]
    type: str = field(default="batch_start", init=False)


@dataclass(slots=True)
class ToolBatchCompleteEvent:
    """``{ type: "batch_complete"; parallelGroupIndex; results }``."""

    parallel_group_index: int
    results: list[ToolExecutionResult]
    type: str = field(default="batch_complete", init=False)


@dataclass(slots=True)
class ToolStartEvent:
    """``{ type: "tool_start"; toolCallId }``."""

    tool_call_id: str
    type: str = field(default="tool_start", init=False)


@dataclass(slots=True)
class ToolCompleteEvent:
    """``{ type: "tool_complete"; result }``."""

    result: ToolExecutionResult
    type: str = field(default="tool_complete", init=False)


@dataclass(slots=True)
class ToolErrorEvent:
    """``{ type: "error"; error; toolCallId? }``."""

    # PORT: TS ``Error`` → Python ``BaseException``.
    error: BaseException
    tool_call_id: Optional[str] = None
    type: str = field(default="error", init=False)


ToolBatchEvent = Union[
    ToolBatchStartEvent,
    ToolBatchCompleteEvent,
    ToolStartEvent,
    ToolCompleteEvent,
    ToolErrorEvent,
]
