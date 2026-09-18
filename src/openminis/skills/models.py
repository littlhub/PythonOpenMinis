"""Skill + builtin-tool descriptors.

Ported from: no direct Kotlin counterpart — the Android app ships its
capabilities inside the APK, while iOS keeps ``/var/minis/skills`` as an
on-device directory of ``SKILL.md`` bundles. This module models that bundle
so the Python port can install, list and serve skills the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["SkillEntry", "ToolEntry"]


@dataclass(frozen=True)
class SkillEntry:
    """One installed skill (a directory containing ``SKILL.md``).

    ``source`` is ``builtin`` for skills bundled with the app and ``user`` for
    anything installed later. ``generated`` marks synthetic bundles (the
    builtin-tool manifest) — those are rewritten on every install pass and
    cannot be uninstalled.
    """

    name: str
    description: str
    path: str
    source: str = "user"
    generated: bool = False
    scripts: tuple[str, ...] = ()
    #: SKILL.md 里声明的环境变量名（``metadata.requires.env`` 等）。
    #: 声明了不等于配好了 —— 界面据此把「还差哪一项」指出来，值本身存在
    #: ``sandbox.envExtra`` 里，由 shell 注入。
    env: tuple[str, ...] = ()
    body: str = ""


@dataclass(frozen=True)
class ToolEntry:
    """A builtin tool exposed to the model (name + schema summary)."""

    name: str
    description: str
    parameters: dict[str, str] = field(default_factory=dict)
    required: tuple[str, ...] = ()
