"""Context builder types — section descriptors, build result, builder config.

Ported from: packages/core/src/context/types.ts
Original package: @zcode/core (context)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Awaitable,
    Literal,
    Optional,
    Protocol,
    Sequence,
    TypedDict,
)

from ..contracts.model import (
    JsonSchema,
    ModelId,
    ModelInputMessage,
    ModelProviderId,
    ModelStreamEvent,
    ModelTextResult,
    ModelToolContract,
)
from ..core.abort import AbortSignal

if TYPE_CHECKING:
    # PORT: 原 types.ts 的 `import type { AutoCompactPolicyConfig } from "../compact/index.js"`。
    # Python 侧该类型落在 `openminis.agent.zcode.compact.policy`（由并行任务产出）。
    # 原文件只把它当类型用，故这里同样只在类型检查期解析，不把 compact 的运行时
    # 依赖链拉进 context 模块。
    from ..compact.policy import AutoCompactPolicyConfig

__all__ = [
    # 由 types.ts 从 @zcode/contracts 再导出的类型
    "EnvInfo",
    "PackageManager",
    "ProjectContext",
    "ProjectType",
    "ResolvedUserInstructionSource",
    "ResolvedUserInstructions",
    "UserInstructionsOptions",
    "UserInstructionSourceScope",
    # 契约层快照类型（ContextBuilder 的上游输入面）
    "ContextSourceDiagnostic",
    "ContextSourceSnapshot",
    # ContextBuilderConfig 消费的 contracts 类型
    "SkillMetadata",
    "SkillLoadOutcome",
    "SkillPolicy",
    "SkillDiagnostic",
    "SkillScope",
    "SkillSource",
    "SkillDiagnosticSeverity",
    # `Model` 面（contracts/model/model.ts）
    "Model",
    "ModelOptions",
    "ModelRequest",
    "ModelResult",
    "ModelEvent",
    "ModelProperties",
    "ModelOptionSpecs",
    # context-source / injection 面
    "ContextSource",
    "ContextInjectionTarget",
    "ContextCacheHint",
    "PresentationSurface",
    "ContextSection",
    "ContextMetaUserAttachmentSource",
    "ContextMetaUserAttachment",
    "ContextBuildResult",
    "OutputStylePromptConfig",
    "ContextBuilderConfig",
    "WorkflowActorContext",
    "ContextSectionInput",
    "ContextUserInstructionsRequest",
    "AgentProfile",
]

# ---------------------------------------------------------------------------
# @zcode/contracts 类型（由 types.ts 再导出）
#
# PORT: 原始定义在契约包：
#   - packages/contracts/src/interfaces/context-source.port.ts
#     （EnvInfo / UserInstructionsOptions / UserInstructionSourceScope /
#       ResolvedUserInstructionSource / ResolvedUserInstructions / ProjectType /
#       PackageManager / ProjectContext / ContextSourceSnapshot）
#   - packages/contracts/src/skills/index.ts
#     （SkillMetadata / SkillLoadOutcome）
# Python 侧 contracts 包目前只落了 ids / errors / model / tools，尚未移植这些
# 类型；为让本模块可独立导入，这里按契约源码 1:1 落成 dataclass。
# ---------------------------------------------------------------------------

EnvInfo_PLACEHOLDER = None  # noqa: E305  (占位以免被误读为类型别名)


@dataclass(slots=True)
class EnvInfo:
    """Mirrors ``EnvInfo`` (contracts/interfaces/context-source.port.ts).

    # PORT: 环境快照由宿主侧的 context source port 采集后注入；内核不直接读
    # ``process.cwd()`` / ``process.env``。
    """

    cwd: str
    platform: str
    shell: str
    os_version: str
    node_version: str
    # 执行模型不是环境事实；只能由当前 model step 的 Model 在 Prompt 渲染时提供。
    is_git_repository: Optional[bool] = None
    git_branch: Optional[str] = None
    git_main_branch: Optional[str] = None
    git_user: Optional[str] = None
    git_status: Optional[str] = None  # "clean" | "dirty" | "not_repo"
    git_status_lines: Optional[list[str]] = None
    recent_commits: Optional[list[str]] = None


@dataclass(slots=True)
class UserInstructionsOptions:
    """Mirrors ``UserInstructionsOptions``."""

    working_directory: str
    project_root: Optional[str] = None
    priority_files: Optional[list[str]] = None
    max_bytes: Optional[int] = None


UserInstructionSourceScope = Literal["user", "workspace"]


@dataclass(slots=True)
class ResolvedUserInstructionSource:
    """Mirrors ``ResolvedUserInstructionSource``."""

    scope: UserInstructionSourceScope
    file_path: str
    file_name: str
    content: str
    bytes_read: int
    size_bytes: int
    truncated: bool


@dataclass(slots=True)
class ResolvedUserInstructions:
    """Mirrors ``ResolvedUserInstructions``."""

    file_path: str
    file_name: str
    content: str
    bytes_read: int
    size_bytes: int
    truncated: bool
    sources: Optional[list[ResolvedUserInstructionSource]] = None


ProjectType = Literal["node", "python", "rust", "go", "java", "unknown"]
PackageManager = Literal["npm", "yarn", "pnpm", "bun"]


@dataclass(slots=True)
class ProjectContext:
    """Mirrors ``ProjectContext``."""

    type: ProjectType
    package_manager: Optional[PackageManager] = None
    scripts: Optional[dict[str, str]] = None
    build_files: Optional[list[str]] = None


@dataclass(slots=True)
class ContextSourceDiagnostic:
    """Mirrors ``ContextSourceDiagnostic``."""

    code: str
    message: str
    path: Optional[str] = None


@dataclass(slots=True)
class ContextSourceSnapshot:
    """Mirrors ``ContextSourceSnapshot`` (contracts/interfaces/context-source.port.ts).

    # PORT: types.ts 自身没有导出这个类型，但它与 ``ContextBuilderConfig`` 同属
    # 「context sources → builder」的输入面，原 TS 由 `ContextSourcePort` 产出。
    """

    working_directory: str
    env_info: EnvInfo
    diagnostics: list[ContextSourceDiagnostic]
    current_date: Optional[str] = None
    user_instructions: Optional[ResolvedUserInstructions] = None
    project_context: Optional[ProjectContext] = None


SkillScope = Literal["project", "user", "system", "admin"]
SkillSource = Literal["agents", "zcode", "bundled", "plugin", "remote"]


@dataclass(slots=True)
class SkillPolicy:
    """Mirrors ``SkillPolicy``."""

    allow_implicit_invocation: Optional[bool] = None


@dataclass(slots=True)
class SkillMetadata:
    """Mirrors ``SkillMetadata`` (contracts/skills/index.ts)."""

    name: str
    description: str
    path: str
    directory: str
    root_path: str
    scope: SkillScope
    source: SkillSource
    safe_to_auto_load: bool
    frontmatter_keys: list[str]
    when_to_use: Optional[str] = None
    plugin_name: Optional[str] = None
    # 所属 plugin 的完整 id；非 plugin skill 不设置。
    plugin_id: Optional[str] = None
    qualified_name: Optional[str] = None
    policy: Optional[SkillPolicy] = None


SkillDiagnosticSeverity = Literal["warning", "error"]


@dataclass(slots=True)
class SkillDiagnostic:
    """Mirrors ``SkillDiagnostic`` (contracts/skills/index.ts)."""

    code: str  # SkillDiagnosticCode
    severity: SkillDiagnosticSeverity
    message: str
    path: Optional[str] = None
    skill_name: Optional[str] = None


@dataclass(slots=True)
class SkillLoadOutcome:
    """Mirrors ``SkillLoadOutcome``."""

    skills: list[SkillMetadata]
    diagnostics: list[SkillDiagnostic]
    total_discovered: int


# PORT: AgentProfile 定义在 core/src/subagent/profile.ts，不属于 context 模块，
# 本次未移植。这里只作为 ``ContextBuilderConfig.agent_profiles`` 的类型位占位；
# 不凭空造出 subagent 契约。
AgentProfile = Any


# ---------------------------------------------------------------------------
# Model 面（packages/contracts/src/model/model.ts）
#
# PORT: `Model` 定义在 contracts/model/model.ts。Python 契约包尚未移植该接口
# （只落了 model/index.ts 的 message / usage / request 面），因此按 §1「带行为的
# interface → Protocol」在本模块落成 Protocol。
# `properties` / `optionSpecs` 所依赖的 ModelProperties / ModelOptionSpecs 来自
# `@zcode/shared/model-config`（未移植），保持宽松载荷，不凭空造出 model-config 契约。
# ---------------------------------------------------------------------------

# 仅保留 CLI 公共类型名，字段来自同一数据 Schema，不复制 Provider 配置定义。
ModelProperties = Any
ModelOptionSpecs = Any


@dataclass(slots=True)
class ModelOptions:
    """Mirrors ``ModelOptions``."""

    reasoning_level: Optional[str] = None
    max_output_tokens: Optional[int] = None


@dataclass(slots=True)
class ModelRequest:
    """Mirrors ``ModelRequest``."""

    messages: list[ModelInputMessage]
    tools: Optional[list[ModelToolContract]] = None
    response_json_schema: Optional[JsonSchema] = None
    options: Optional[ModelOptions] = None
    abort_signal: Optional[AbortSignal] = None


# 第一阶段沿用已经完成 Provider SDK 归一化的结果和流事件字段；
# 旧名字只保留在 Adapter 兼容边界，业务调用统一使用下面两个名字。
ModelResult = ModelTextResult
ModelEvent = ModelStreamEvent


class Model(Protocol):
    """Mirrors ``Model`` — provider-neutral model handle."""

    provider_id: ModelProviderId
    model_id: ModelId
    display_name: Optional[str]
    properties: ModelProperties
    option_specs: ModelOptionSpecs
    options: ModelOptions

    def bind(self, options: Optional[ModelOptions] = None) -> "Model": ...

    def generate_text(self, request: ModelRequest) -> Awaitable[ModelResult]: ...

    def stream_text(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...


# -----------------------------------------------
# Context Source
# -----------------------------------------------

ContextSource = Literal[
    "cli_prefix",  # CLI / 产品身份前缀
    "identity",  # Agent 基础描述
    "env_info",  # 环境信息 (cwd, platform, git repo boolean)
    "system_context",  # git snapshot context
    "skills",  # 可用 skills
    "tools",  # 工具定义
    "request_user_context",  # request-level user context provider-visible 组合块
    "memory",  # 长期 memory read path
    "current_date",  # 当前日期
    "custom_system_prompt",  # 自定义 stable system body
    "workflow_actor_identity",  # 动态工作流子代理身份：契约 + persona 叠加
    "subagent_agent_prompt",  # 子 agent 专属身份/任务 prompt
    "subagent_notes",  # 子 agent 通用操作提醒
    "subagent_environment",  # 子 agent 环境和模型上下文
    "dynamic_behavior",  # 动态行为边界
    "session_guidance",  # 当前可用内置能力指导
    "output_style",  # 输出风格
    "context_management",  # 长上下文管理提示
    "desktop_context",  # ZCode Desktop 渲染与交互协议
]

ContextInjectionTarget = Literal["system", "meta_user"]

ContextCacheHint = Literal["stable", "dynamic"]

PresentationSurface = Literal["terminal", "zcode_desktop"]


# -----------------------------------------------
# Context Section
# -----------------------------------------------


@dataclass(slots=True)
class ContextSection:
    """Mirrors ``ContextSection``."""

    name: str  # 人类可读的 section 名称
    source: ContextSource  # 来源标识
    injection_target: ContextInjectionTarget  # 注入位置
    cache_hint: ContextCacheHint  # 缓存稳定性提示
    chars: int  # 字符数
    tokens: int  # 估算 token 数
    content: str  # 完整内容
    preview: str  # 前 100 字符预览


# PORT: ``ContextBuilder.addSection`` 的入参形状：
# ``Omit<ContextSection, "chars" | "tokens" | "injectionTarget" | "cacheHint"> &
#   Partial<Pick<ContextSection, "injectionTarget" | "cacheHint">>``。
# TS 用的是匿名结构类型，Python 侧以 TypedDict 承载；键名按 §1 命名规则转
# snake_case（injection_target / cache_hint）。
class _ContextSectionInputRequired(TypedDict):
    name: str
    source: ContextSource
    content: str
    preview: str


class ContextSectionInput(_ContextSectionInputRequired, total=False):
    injection_target: ContextInjectionTarget
    cache_hint: ContextCacheHint


ContextMetaUserAttachmentSource = Literal["skills_listing", "context_prefix"]


@dataclass(slots=True)
class ContextMetaUserAttachment:
    """Mirrors ``ContextMetaUserAttachment``."""

    source: ContextMetaUserAttachmentSource
    content: str


# -----------------------------------------------
# Context Build Result
# -----------------------------------------------


@dataclass(slots=True)
class ContextBuildResult:
    """Mirrors ``ContextBuildResult``."""

    sections: list[ContextSection]
    total_chars: int
    total_tokens: int
    # ContextBuilder 只组装 system messages
    system_messages: list[ModelInputMessage]
    # 未包裹 <system-reminder> 的 meta user body
    meta_user_attachments: list[ContextMetaUserAttachment]


@dataclass(slots=True)
class OutputStylePromptConfig:
    """Mirrors ``OutputStylePromptConfig``."""

    name: str
    prompt: str
    keep_coding_instructions: Optional[bool] = None


# -----------------------------------------------
# Context Builder Config
# -----------------------------------------------


@dataclass(slots=True)
class ContextBuilderConfig:
    """Mirrors ``ContextBuilderConfig``."""

    working_directory: str
    env_info: EnvInfo
    # 当前步骤的执行对象，不进入 Context Source 或持久化环境快照。
    model: Optional[Model] = None
    presentation_surface: Optional[PresentationSurface] = None
    current_date: Optional[str] = None
    user_instructions: Optional[ResolvedUserInstructions] = None
    project_context: Optional[ProjectContext] = None
    memory_root: Optional[str] = None
    memory_index_content: Optional[str] = None
    skills: Optional[SkillLoadOutcome] = None
    agent_profiles: Optional[Sequence[AgentProfile]] = None
    embedded_search_enabled: Optional[bool] = None
    skill_metadata_budget: Optional[int] = None
    custom_system_prompt: Optional[str] = None
    # 动态工作流子代理（workflow child）的身份输入。在场即走 builder 的第三条路径：
    # 基座段（CLI prefix、安全行、Harness、memory）+ 工作流子代理契约 + persona 叠加，
    # 而不是像 `customSystemPrompt` 那样整段替换。与 `customSystemPrompt` 互斥。
    workflow_actor: Optional[WorkflowActorContext] = None
    language: Optional[str] = None
    output_style: Optional[OutputStylePromptConfig] = None
    compact: Optional[AutoCompactPolicyConfig] = None
    guidance_tool_names: Optional[Sequence[str]] = None


@dataclass(slots=True)
class WorkflowActorContext:
    """工作流子代理的身份输入：有效名（匿名缺席）、作者写的 persona system prompt（可缺席）。
    没有工具档位：每个子代理都有完整工作工具集，契约只有一份文本。
    """

    name: Optional[str] = None
    persona: Optional[str] = None


ContextUserInstructionsRequest = UserInstructionsOptions
