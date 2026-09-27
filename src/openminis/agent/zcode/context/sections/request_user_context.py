"""Request User Context Section Builder — instructions / project memory block.

Ported from: packages/core/src/context/sections/request-user-context.ts
Original package: @zcode/core (context/sections)

# PORT-STATUS: partial
# 本文件自身已 1:1 翻译完成；唯一未落定的是对 `memory/index-content.ts` 的依赖：
# 它按目录映射应落在 `openminis.agent.zcode.memory.index_content`
# （`format_project_memory_index_content`），由 memory 模块的移植任务产出。
# 在该模块落地前，本模块无法完成运行时导入。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# PORT: 原 `import { formatProjectMemoryIndexContent } from "../../memory/index-content.js"`。
from ...memory.index_content import format_project_memory_index_content
from ..types import (
    ContextSection,
    ResolvedUserInstructionSource,
    ResolvedUserInstructions,
)
from ..utils import estimate_tokens


def build_request_user_context_section(
    *,
    user_instructions: Optional[ResolvedUserInstructions] = None,
    memory_index_content: Optional[str] = None,
    memory_root: Optional[str] = None,
) -> Optional[ContextSection]:
    """原 TS 入参是一个内联对象字面量类型
    ``{ userInstructions?; memoryIndexContent?; memoryRoot? }``，Python 侧用同名
    关键字参数表达（避免为一个匿名结构再造一个具名类型）。
    """
    content = _build_request_user_context_content(
        user_instructions=user_instructions,
        memory_index_content=memory_index_content,
        memory_root=memory_root,
    )
    if not content:
        return None

    return ContextSection(
        name="Request User Context",
        source="request_user_context",
        injection_target="meta_user",
        cache_hint="dynamic",
        chars=len(content),
        tokens=estimate_tokens(content),
        content=content,
        preview=content[:100],
    )


def _build_request_user_context_content(
    *,
    user_instructions: Optional[ResolvedUserInstructions] = None,
    memory_index_content: Optional[str] = None,
    memory_root: Optional[str] = None,
) -> Optional[str]:
    sections: list[str] = []

    instruction_content = (
        _build_instruction_content(user_instructions)
        if user_instructions is not None
        else None
    )
    if instruction_content:
        sections.append(instruction_content)

    project_memory_index_content = _build_project_memory_index_content(
        memory_root,
        memory_index_content,
    )
    if project_memory_index_content:
        sections.append(project_memory_index_content)

    if len(sections) == 0:
        return None

    return "\n".join(
        [
            # 聚合字段标题不能绑定到 AGENTS.md，否则仅有 Project Memory 时缺少标题。

            "# agentsMd",
            "Codebase and user instructions are shown below. Be sure to adhere to these instructions. IMPORTANT: These instructions OVERRIDE any default behavior and you MUST follow them exactly as written.",
            "",
            "\n\n".join(sections),
        ]
    )


def _build_project_memory_index_content(
    memory_root: Optional[str],
    index_content: Optional[str],
) -> Optional[str]:
    if not memory_root or index_content is None:
        return None
    formatted = format_project_memory_index_content(index_content)
    if not formatted:
        return None

    # PORT: 原 ``join(memoryRoot, "MEMORY.md")``（node:path）→ ``pathlib.PurePath``。
    memory_index_path = Path(memory_root) / "MEMORY.md"

    return "\n".join(
        [
            f"Contents of {memory_index_path} (user's auto-memory, persists across conversations):",
            "",
            formatted,
        ]
    )


def _build_instruction_content(instructions: ResolvedUserInstructions) -> Optional[str]:
    sections = [
        content
        for content in (
            _build_instruction_source_content(source)
            for source in _normalized_instruction_sources(instructions)
        )
        if content is not None
    ]

    return "\n\n".join(sections) if len(sections) > 0 else None


def _normalized_instruction_sources(
    instructions: ResolvedUserInstructions,
) -> list[ResolvedUserInstructionSource]:
    if instructions.sources is not None and len(instructions.sources) > 0:
        return instructions.sources

    return [
        ResolvedUserInstructionSource(
            scope="workspace",
            file_path=instructions.file_path,
            file_name=instructions.file_name,
            content=instructions.content,
            bytes_read=instructions.bytes_read,
            size_bytes=instructions.size_bytes,
            truncated=instructions.truncated,
        ),
    ]


def _build_instruction_source_content(source: ResolvedUserInstructionSource) -> Optional[str]:
    body = (
        f"{source.content}\n\n[File truncated: {source.file_name}]"
        if source.truncated
        else source.content
    )
    trimmed_body = body.strip()
    if not trimmed_body:
        return None

    return "\n".join(
        [
            f"Contents of {source.file_path} ({_format_instruction_source_scope(source.scope)}):",
            "",
            trimmed_body,
        ]
    )


def _format_instruction_source_scope(scope: str) -> str:
    return "user default instructions" if scope == "user" else "workspace instructions"
