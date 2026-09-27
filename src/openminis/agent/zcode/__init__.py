"""ZCode programming-agent logic kernel (Python port).

Ported from: apps/zcode-cli/packages/core + packages/contracts (TypeScript)
See ZCORE_PORTING.md for the translation rules.

This package is the built-in programming agent kernel for OpenMinis. It lives
beside ``openminis.agent.agent_runtime`` and does not replace it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# PORT: 入口类原名为 agent.py，与已移植的 agent/ 包（turn_state/turn_machine/
# message_history）冲突，故命名为 agent_entry.py。
#
# PORT: 上游 index.ts 为静态 re-export；这里改用惰性导出（PEP 562），使内核在
# "部分模块尚未移植" 时仍能逐模块 import 与验证；对外 API
# （``from openminis.agent.zcode import ZCodeAgent``）保持不变。
_ENTRY_EXPORTS = frozenset(
    {
        "ZCodeAgent",
        "AgentContext",
        "AgentResponse",
        "AgentPorts",
        "CollaborationMode",
    }
)

if TYPE_CHECKING:  # pragma: no cover
    from .agent_entry import (  # noqa: F401
        AgentContext,
        AgentPorts,
        AgentResponse,
        CollaborationMode,
        ZCodeAgent,
    )


def __getattr__(name: str):
    if name in _ENTRY_EXPORTS:
        from . import agent_entry

        return getattr(agent_entry, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_ENTRY_EXPORTS)
