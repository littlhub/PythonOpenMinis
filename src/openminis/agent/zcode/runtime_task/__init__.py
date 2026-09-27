"""Runtime task types.

Ported from: packages/core/src/runtime-task/registry.ts (type surface)
Original package: @zcode/core (runtime-task)
"""

from __future__ import annotations

__all__ = [
    "RUNTIME_TASK_TYPES",
    "RuntimeTaskType",
]

# local_dynamic_workflow 与 local_workflow 刻意分开：后者是 legacy `Workflow` 工具（不可取消），
# 前者是动态工作流引擎的 run。
RUNTIME_TASK_TYPES = (
    "local_agent",
    "local_bash",
    "local_workflow",
    "local_dynamic_workflow",
)

#: PORT: upstream ``type RuntimeTaskType = "local_agent" | ...`` (registry.ts).
RuntimeTaskType = str
