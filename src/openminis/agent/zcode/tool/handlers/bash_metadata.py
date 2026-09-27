"""Bash metadata — provider-facing name / description / summary derivation.

Ported from: packages/core/src/tool/handlers/bash-metadata.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from typing import Any, Optional

from .bash_semantics import is_sed_in_place_bash_command

__all__ = [
    "get_bash_description",
    "get_bash_user_facing_name",
    "get_bash_tool_use_summary",
    "get_bash_activity_description",
    "get_bash_auto_classifier_input",
    "read_string_property",
]


def get_bash_description(input: Any) -> str:
    """Mirrors ``getBashDescription(input: unknown)``."""
    description = read_string_property(input, "description")
    return description if description and len(description) > 0 else "Run shell command"


def get_bash_user_facing_name(input: Any = None) -> str:
    """Mirrors ``getBashUserFacingName(input?: unknown)``."""
    command = read_string_property(input, "command")
    if command and is_sed_in_place_bash_command(command):
        return "Update"
    return "Bash"


def get_bash_tool_use_summary(input: Any = None) -> Optional[str]:
    """Mirrors ``getBashToolUseSummary(input?: unknown)``."""
    command = read_string_property(input, "command")
    if not command:
        return None
    description = read_string_property(input, "description")
    return description if description and len(description) > 0 else command


def get_bash_activity_description(input: Any = None) -> str:
    """Mirrors ``getBashActivityDescription(input?: unknown)``."""
    command = read_string_property(input, "command")
    if not command:
        return "Running command"
    description = read_string_property(input, "description")
    return f"Running {description if description and len(description) > 0 else command}"


def get_bash_auto_classifier_input(input: Any) -> Optional[str]:
    """Mirrors ``getBashAutoClassifierInput(input: unknown)``."""
    return read_string_property(input, "command")


def read_string_property(input: Any, key: str) -> Optional[str]:
    """Mirrors ``readStringProperty(input, key)``.

    # PORT: TS ``!input || typeof input !== "object" || !(key in input)`` —— Python 侧
    # 用 ``isinstance(input, dict)`` 承载「是对象且可按键查」，并用 ``key in input`` 等价
    # ``key in input``（TS 的 ``in`` 也覆盖原型链；这里输入的载荷是普通对象/JSON，等价）。
    """
    if not input or not isinstance(input, dict) or key not in input:
        return None
    value = input[key]
    return value if isinstance(value, str) else None
