"""Skills Section Builder — available skill listing as a meta user attachment.

Ported from: packages/core/src/context/sections/skills.ts
Original package: @zcode/core (context/sections)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..types import ContextSection, SkillLoadOutcome, SkillMetadata
from ..utils import estimate_tokens

DEFAULT_SKILL_METADATA_BUDGET = 20_000
MAX_DESCRIPTION_CHARS = 250


@dataclass(slots=True)
class SkillsSectionOptions:
    """Mirrors ``SkillsSectionOptions``."""

    outcome: SkillLoadOutcome
    metadata_budget: Optional[int] = None


def build_skills_section(options: SkillsSectionOptions) -> Optional[ContextSection]:
    if len(options.outcome.skills) == 0:
        return None

    content = _build_skills_content(
        options.outcome.skills,
        options.metadata_budget
        if options.metadata_budget is not None
        else DEFAULT_SKILL_METADATA_BUDGET,
    )

    return ContextSection(
        name="Skills",
        source="skills",
        injection_target="meta_user",
        cache_hint="dynamic",
        chars=len(content),
        tokens=estimate_tokens(content),
        content=content,
        preview=content[:100],
    )


def _build_skills_content(skills: list[SkillMetadata], budget: int) -> str:
    lines = [
        "The following skills are available for use with the Skill tool:",
        "",
    ]

    # PORT: TS ``[...skills].sort((a, b) => skillDisplayName(a).localeCompare(skillDisplayName(b)))``。
    # Python 用 ``sorted(key=...)`` 表达同样的「复制后排序」；注意
    # ``localeCompare`` 是 locale 感知排序，``sorted`` 按码位排序，非 ASCII 技能名
    # 的先后顺序与 JS 相比可能不同。
    sorted_skills = sorted(skills, key=_skill_display_name)
    skill_lines = [_format_skill_line(skill, MAX_DESCRIPTION_CHARS) for skill in sorted_skills]
    full = "\n".join([*lines, *skill_lines])
    if len(full) <= budget:
        return full

    names_only = [
        f"- {_skill_display_name(skill)}{_bare_alias_suffix(skill)} (file: {skill.path})"
        for skill in sorted_skills
    ]
    return "\n".join([*lines, *names_only])


def _format_skill_line(skill: SkillMetadata, max_description_chars: int) -> str:
    description = (
        f"{skill.description} - {skill.when_to_use}"
        if skill.when_to_use
        else skill.description
    )
    trimmed = (
        f"{description[: max_description_chars - 1]}..."
        if len(description) > max_description_chars
        else description
    )
    return f"- {_skill_display_name(skill)}: {trimmed}{_bare_alias_suffix(skill)} (file: {skill.path})"


def _skill_display_name(skill: SkillMetadata) -> str:
    return skill.qualified_name if skill.qualified_name is not None else skill.name


def _bare_alias_suffix(skill: SkillMetadata) -> str:
    return (
        f" (also loadable as {skill.name})"
        if skill.qualified_name and skill.qualified_name != skill.name
        else ""
    )
