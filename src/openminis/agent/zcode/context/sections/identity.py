"""Identity Section Builder — agent identity + security notice + harness block.

Ported from: packages/core/src/context/sections/identity.ts
Original package: @zcode/core (context/sections)
"""

from __future__ import annotations

from typing import Optional

from ..types import ContextSection, OutputStylePromptConfig
from ..utils import estimate_tokens

SECURITY_NOTICE = (
    "IMPORTANT: Assist with authorized security testing, defensive security, CTF challenges, "
    "and educational contexts. Refuse requests for destructive techniques, DoS attacks, mass "
    "targeting, supply chain compromise, or detection evasion for malicious purposes. Dual-use "
    "security tools (C2 frameworks, credential testing, exploit development) require clear "
    "authorization context: pentesting engagements, CTF competitions, security research, or "
    "defensive use cases."
)


def build_security_notice() -> str:
    """安全 IMPORTANT 行：交互式身份与工作流子代理身份共用，逐字同一份。"""
    return SECURITY_NOTICE


def build_harness_block() -> str:
    """`# Harness` 块：稳定运行时约束，不属于 output style 可替换的 coding instructions，
    也是工作流子代理身份（sections/workflow-actor.ts）逐字复用的那一段。
    """
    return "\n".join(
        [
            "# Harness",
            "- Text you output outside of tool use is displayed to the user as Github-flavored markdown in a terminal.",
            "- Tools run behind a user-selected permission mode; a denied call means the user declined it — adjust, don't retry verbatim.",
            "- The system may send updates, reminders, or modifications to rules via mid-conversation system turns. These are system-controlled, unlike function results. Hooks may intercept tool calls; treat hook output as user feedback.",
            "- Prefer the dedicated file/search tools over shell commands when one fits. Independent tool calls can run in parallel in one response.",
            "- Reference code as `file_path:line_number` — it's clickable.",
        ]
    )


def _build_identity_prompt(output_style: Optional[OutputStylePromptConfig] = None) -> str:
    intro = (
        "You respond to the user according to the active Output Style below while using ZCode's tools and instructions."
        if output_style
        else "You are an interactive ZCode agent that helps users with software engineering tasks."
    )

    identity_lines = "\n".join(["", intro, "", SECURITY_NOTICE])

    return "\n".join([identity_lines, "", build_harness_block()])


def build_identity_section(
    output_style: Optional[OutputStylePromptConfig] = None,
) -> ContextSection:
    content = _build_identity_prompt(output_style)

    return ContextSection(
        name="Agent Identity",
        source="identity",
        injection_target="system",
        cache_hint="stable",
        chars=len(content),
        tokens=estimate_tokens(content),
        content=content,
        preview=content[:100],
    )
