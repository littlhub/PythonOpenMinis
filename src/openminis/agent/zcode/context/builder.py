"""Context Builder — system prompt assembly.

Ported from: packages/core/src/context/builder.ts
Original package: @zcode/core (context)

# PORT-STATUS: partial
# workflowActor 分支依赖 `sections/workflow-actor.ts`，该文件按 ZCORE_PORTING.md
# §0.0 属办公编排、明确排除移植，因此该分支以「端口占位 + NotImplementedError」
# 处理（见 `build_workflow_actor_identity_section`）。编程 agent 内核不会走到该分支。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from ..contracts.model import ModelCacheControl, ModelInputMessage
from .dynamic_sections import (
    build_context_management_section,
    build_dynamic_behavior_section,
    build_output_style_section,
    build_session_guidance_section,
)
from .sections.cli_prefix import build_cli_prefix_section
from .sections.current_date import build_current_date_section
from .sections.desktop import build_desktop_context_section
from .sections.env_info import build_env_info_section, build_git_system_context_section
from .sections.identity import build_identity_section
from .sections.memory import build_memory_section
from .sections.request_user_context import build_request_user_context_section
from .sections.skills import SkillsSectionOptions, build_skills_section
from .types import (
    ContextBuildResult,
    ContextBuilderConfig,
    ContextCacheHint,
    ContextInjectionTarget,
    ContextMetaUserAttachment,
    ContextSection,
    ContextSectionInput,
    ContextSource,
    EnvInfo,
    WorkflowActorContext,
)
from .utils import estimate_tokens

__all__ = [
    "ContextBuilder",
    "create_context_builder",
    "build_context_meta_user_body",
    "build_skills_meta_user_body",
]

# PORT: `ToolRegistry` 定义在 core/src/tool/registry.ts，未随本次移植落下。
# `setToolRegistry` 保留兼容入口但忽略其参数（见其 docstring），因此这里用宽松
# 类型占位即可，不凭空造出 registry 契约。
ToolRegistry = Any

# PORT: TS ``const EPHEMERAL_CACHE_CONTROL = { type: "ephemeral" as const }``。
EPHEMERAL_CACHE_CONTROL = ModelCacheControl(type="ephemeral")

# Skill 工具的注册名（与 tool/handlers/skill.ts 的 metadata.name 同字面；contracts 没有常量）。
SKILL_TOOL_NAME = "Skill"


# -----------------------------------------------
# Context Builder
# -----------------------------------------------


class ContextBuilder:
    # PORT: 原 TS 字段 `private config` / `private customSections` → 下划线前缀。
    def __init__(self, config: ContextBuilderConfig) -> None:
        self._config = config
        self._custom_sections: list[ContextSection] = []

    def set_tool_registry(self, _registry: ToolRegistry) -> "ContextBuilder":
        """保留兼容入口。工具说明由 model request 的 tools 字段承载，不再镜像进 system prompt。"""
        return self

    def set_env_info(self, env_info: EnvInfo) -> "ContextBuilder":
        self._config = replace(self._config, env_info=env_info)
        return self

    def add_section(self, section: ContextSectionInput) -> "ContextBuilder":
        """添加自定义 section（用于后续扩展）"""
        injection_target = section.get("injection_target")
        cache_hint = section.get("cache_hint")
        content = section["content"]
        self._custom_sections.append(
            ContextSection(
                name=section["name"],
                source=section["source"],
                # PORT: 原 `section.injectionTarget ?? "system"` / `?? "dynamic"`。
                injection_target=(
                    "system" if injection_target is None else injection_target
                ),
                cache_hint="dynamic" if cache_hint is None else cache_hint,
                chars=len(content),
                tokens=estimate_tokens(content),
                preview=section["preview"],
            )
        )
        return self

    def build(self) -> ContextBuildResult:
        """构建 context，返回结构化结果"""
        sections: list[ContextSection] = []
        active_output_style = (
            self._config.output_style
            if self._config.output_style is not None
            and len(self._config.output_style.prompt.strip()) > 0
            else None
        )
        custom_system_prompt = (
            self._config.custom_system_prompt.strip()
            if self._config.custom_system_prompt is not None
            else None
        )
        has_custom_system_prompt = bool(custom_system_prompt)
        # 工作流子代理身份：第三条路径。与
        # customSystemPrompt 互斥——两者同在只可能是接线错误（persona 该经 workflowActor 进来，
        # 不该再塞 systemPrompt），大声失败而不是默默二选一。
        workflow_actor = self._config.workflow_actor
        if workflow_actor is not None and has_custom_system_prompt:
            # PORT: TS `throw new Error(...)`；契约层无对应错误码，按 Python 语义抛 ValueError。
            raise ValueError(
                "ContextBuilder: workflowActor and customSystemPrompt are mutually exclusive"
            )
        is_workflow_actor = workflow_actor is not None

        # 1. CLI / product prefix. Keep this as the short leading identity block.
        # 「You are ZCode, an interactive coding agent」对一个
        # 只对脚本说话、可能连读文件工具都没有的子代理是错的身份，且走在正确身份段前面。
        if not is_workflow_actor:
            sections.append(build_cli_prefix_section())

        # 2. Stable agent behavior or custom prompt body
        if has_custom_system_prompt:
            sections.append(
                _create_section(
                    name="Custom System Prompt",
                    source="custom_system_prompt",
                    injection_target="system",
                    cache_hint="stable",
                    content=f"\n{custom_system_prompt}" if custom_system_prompt else "",
                )
            )
        elif workflow_actor is not None:
            sections.append(build_workflow_actor_identity_section(workflow_actor))
        else:
            sections.append(build_identity_section(active_output_style))

        # 3. Dynamic system context
        # custom prompt 不是只替换
        # stable body，而是跳过默认 system prompt 体系和 systemContext；否则用户提供
        # custom prompt 后仍会混入 Session Guidance / output style 等动态 system 段。
        # 工作流子代理跳过其中面向「与用户对话」的三段（desktop、Dynamic Behavior、session
        # guidance——契约里已把 Report outcomes faithfully 搬过去），保留 memory 与其后各段。
        if not has_custom_system_prompt:
            if not is_workflow_actor and self._config.presentation_surface == "zcode_desktop":
                sections.append(build_desktop_context_section())

            # behaviour part right after stable sp...
            if not is_workflow_actor:
                sections.append(build_dynamic_behavior_section())

            # Session-specific guidance
            session_guidance_section = (
                None
                if is_workflow_actor
                else build_session_guidance_section(
                    (
                        self._config.guidance_tool_names
                        if self._config.guidance_tool_names is not None
                        else []
                    ),
                    (
                        len(self._config.skills.skills)
                        if self._config.skills is not None
                        else 0
                    )
                    > 0,
                )
            )
            if session_guidance_section:
                sections.append(session_guidance_section)

            # Memory
            if self._config.memory_root:
                memory_section = build_memory_section(self._config.memory_root)
                if memory_section:
                    sections.append(memory_section)
            sections.append(build_env_info_section(self._config.env_info, self._config.model))

            # Output Style
            output_style_section = build_output_style_section(active_output_style)
            if output_style_section:
                sections.append(output_style_section)

            # Context Management
            sections.append(build_context_management_section())

            git_system_context_section = build_git_system_context_section(self._config.env_info)
            if git_system_context_section:
                sections.append(git_system_context_section)

        # 4. Skills appear as a meta user system-reminder, matching provider block layout.
        # guidanceToolNames 是 runtime 当下的
        # 工具表；一个 Skill 工具未注册的工作流子代理被告知「以下技能可经 Skill 工具使用」，
        # 只会让它相信自己有一个没有的工具。表缺席（测试 / 旧调用方）时保持既有行为。
        if self._config.skills is not None and self._skill_tool_available():
            skills_section = build_skills_section(
                SkillsSectionOptions(
                    outcome=self._config.skills,
                    metadata_budget=self._config.skill_metadata_budget,
                )
            )
            if skills_section:
                sections.append(skills_section)

        # 5. Meta user context: workspace instructions/project memory first, date second.
        request_user_context_section = build_request_user_context_section(
            user_instructions=self._config.user_instructions,
            memory_index_content=self._config.memory_index_content,
            memory_root=self._config.memory_root,
        )
        if request_user_context_section:
            sections.append(request_user_context_section)

        current_date_section = build_current_date_section(self._config.current_date)
        if current_date_section:
            sections.append(current_date_section)

        # 6. Custom sections
        sections.extend(self._custom_sections)

        ordered_sections = _order_sections_for_injection(sections)

        # 计算总计
        total_chars = sum(section.chars for section in ordered_sections)
        total_tokens = sum(section.tokens for section in ordered_sections)

        system_messages = self._assemble_system_messages(ordered_sections)
        meta_user_attachments = self._assemble_meta_user_attachments(ordered_sections)

        return ContextBuildResult(
            sections=ordered_sections,
            total_chars=total_chars,
            total_tokens=total_tokens,
            system_messages=system_messages,
            meta_user_attachments=meta_user_attachments,
        )

    def _skill_tool_available(self) -> bool:
        names = self._config.guidance_tool_names
        return names is None or SKILL_TOOL_NAME in names

    def _assemble_system_messages(
        self, sections: list[ContextSection]
    ) -> list[ModelInputMessage]:
        messages: list[ModelInputMessage] = []

        cli_prefix_content = _build_section_content(
            [
                section
                for section in sections
                if section.injection_target == "system" and section.source == "cli_prefix"
            ]
        )
        if cli_prefix_content:
            messages.append(
                ModelInputMessage(
                    role="system",
                    content=cli_prefix_content,
                    cache_control=EPHEMERAL_CACHE_CONTROL,
                )
            )

        stable_body_content = _build_section_content(
            [
                section
                for section in sections
                if section.injection_target == "system"
                and section.cache_hint == "stable"
                and section.source != "cli_prefix"
            ]
        )
        if stable_body_content:
            messages.append(
                ModelInputMessage(
                    role="system",
                    content=stable_body_content,
                    cache_control=EPHEMERAL_CACHE_CONTROL,
                )
            )

        dynamic_system_content = _build_section_content(
            [
                section
                for section in sections
                if section.injection_target == "system" and section.cache_hint == "dynamic"
            ]
        )
        if dynamic_system_content:
            messages.append(
                ModelInputMessage(
                    role="system",
                    # ZCode by design：Main Agent 的 dynamic system block 自带左边界，所有 provider 保持一致。
                    content=f"\n\n{dynamic_system_content}",
                    cache_control=EPHEMERAL_CACHE_CONTROL,
                )
            )

        return messages

    def _assemble_meta_user_attachments(
        self, sections: list[ContextSection]
    ) -> list[ContextMetaUserAttachment]:
        attachments: list[ContextMetaUserAttachment] = []

        skills_content = build_skills_meta_user_body(
            [
                section
                for section in sections
                if section.injection_target == "meta_user" and section.source == "skills"
            ]
        )
        if skills_content:
            attachments.append(
                ContextMetaUserAttachment(source="skills_listing", content=skills_content)
            )

        context_content = build_context_meta_user_body(
            [
                section
                for section in sections
                if section.injection_target == "meta_user" and section.source != "skills"
            ]
        )
        if context_content:
            attachments.append(
                ContextMetaUserAttachment(source="context_prefix", content=context_content)
            )

        return attachments


def _order_sections_for_injection(sections: list[ContextSection]) -> list[ContextSection]:
    return [
        *[
            section
            for section in sections
            if section.injection_target == "system" and section.cache_hint == "stable"
        ],
        *[
            section
            for section in sections
            if section.injection_target == "system" and section.cache_hint == "dynamic"
        ],
        *[
            section
            for section in sections
            if section.injection_target == "meta_user" and section.cache_hint == "stable"
        ],
        *[
            section
            for section in sections
            if section.injection_target == "meta_user" and section.cache_hint == "dynamic"
        ],
    ]


def build_context_meta_user_body(sections: list[ContextSection]) -> Optional[str]:
    if len(sections) == 0:
        return None

    return "\n".join(
        [
            "As you answer the user's questions, you can use the following context:",
            _build_section_content(sections),
            "",
            "      IMPORTANT: this context may or may not be relevant to your tasks. You should not respond to this context unless it is highly relevant to your task.",
        ]
    )


def build_skills_meta_user_body(sections: list[ContextSection]) -> Optional[str]:
    content = _build_section_content(sections)
    if not content:
        return None

    return content


def _build_section_content(sections: list[ContextSection]) -> str:
    return "\n\n".join(section.content for section in sections)


def _create_section(
    *,
    name: str,
    source: ContextSource,
    injection_target: ContextInjectionTarget,
    cache_hint: ContextCacheHint,
    content: str,
) -> ContextSection:
    """PORT: 原 ``createSection(input: {...})`` 取单个对象字面量；Python 侧改用同名
    关键字参数表达同一结构，避免为一个匿名类型再造一层。
    """
    return ContextSection(
        name=name,
        source=source,
        injection_target=injection_target,
        cache_hint=cache_hint,
        chars=len(content),
        tokens=estimate_tokens(content),
        preview=content[:100],
    )


def build_workflow_actor_identity_section(actor: WorkflowActorContext) -> ContextSection:
    """端口占位：`sections/workflow-actor.ts` 未移植（scope: non-coding）。

    # PORT: 原实现位于 core/src/context/sections/workflow-actor.ts，属动态工作流
    # （办公编排），按 ZCORE_PORTING.md §0.0 明确排除。builder 的第三路径分支照搬
    # 保留（类型 `WorkflowActorContext` 与分支顺序不变），但编程 agent 内核不会走到
    # 这里；一旦被调用即显式失败，而不是静默降级成另一条身份路径。
    """
    raise NotImplementedError(
        "buildWorkflowActorIdentitySection: context/sections/workflow-actor.ts "
        "is not ported (scope: non-coding)"
    )


# -----------------------------------------------
# Factory
# -----------------------------------------------


def create_context_builder(config: ContextBuilderConfig) -> ContextBuilder:
    return ContextBuilder(config)
