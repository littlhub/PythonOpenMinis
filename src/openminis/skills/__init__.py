"""Skill bundles + builtin tool manifest.

Ported from: no direct Kotlin counterpart. iOS keeps skills as an on-device
``/var/minis/skills`` directory of ``SKILL.md`` bundles; the Android app has no
equivalent. The Python port adopts the iOS layout so skills are plain files a
user can edit, and adds a generated bundle documenting the builtin tools.
"""

from __future__ import annotations

from .models import SkillEntry, ToolEntry
from .store import BUILTIN_TOOLS_SKILL, SKILL_FILE, SkillError, SkillStore

__all__ = [
    "BUILTIN_TOOLS_SKILL",
    "SKILL_FILE",
    "SkillEntry",
    "SkillError",
    "SkillStore",
    "ToolEntry",
    "skill_store",
]


def skill_store() -> SkillStore:
    """Process-wide store rooted at ``<data_dir>/skills``."""
    return SkillStore()
