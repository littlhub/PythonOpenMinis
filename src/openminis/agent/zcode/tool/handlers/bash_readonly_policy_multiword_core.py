"""Bash readonly policy — multi-word command policy entries (core).

Ported from: packages/core/src/tool/handlers/bash-readonly-policy-multiword-core.ts
Original package: @zcode/core (tool/handlers)

PORT: TS ``[...] as const satisfies readonly (readonly [string, BashReadonlyCommandPolicy])[]``
→ Python ``list[tuple[str, BashReadonlyCommandPolicy]]``（有序，逐条等价）。
"""

from __future__ import annotations

from .bash_readonly_policy_callbacks import (
    docker_command_is_dangerous,
    gh_command_is_dangerous,
)

# PORT: TS 从 barrel ``./bash-readonly-policy-flags.js`` 导入；Python 侧直接引用具体模块。
from .bash_readonly_policy_flags_process import (
    DOCKER_INSPECT_SAFE_FLAGS,
    DOCKER_LOGS_SAFE_FLAGS,
)
from .bash_readonly_policy_types import BashReadonlyCommandPolicy

READONLY_MULTIWORD_POLICY_ENTRIES_CORE: list[tuple[str, BashReadonlyCommandPolicy]] = [
    (
        "docker inspect",
        BashReadonlyCommandPolicy(
            safe_flags=DOCKER_INSPECT_SAFE_FLAGS,
            additional_command_is_dangerous_callback=docker_command_is_dangerous,
        ),
    ),
    (
        "docker logs",
        BashReadonlyCommandPolicy(
            safe_flags=DOCKER_LOGS_SAFE_FLAGS,
            additional_command_is_dangerous_callback=docker_command_is_dangerous,
        ),
    ),
    (
        "gh auth status",
        BashReadonlyCommandPolicy(
            safe_flags={
                "-a": "none",
                "-h": "string",
                "--active": "none",
                "--hostname": "string",
                "--json": "string",
            },
            additional_command_is_dangerous_callback=gh_command_is_dangerous,
        ),
    ),
]

__all__ = ["READONLY_MULTIWORD_POLICY_ENTRIES_CORE"]
