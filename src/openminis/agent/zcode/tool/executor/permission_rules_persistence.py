"""Project permission rules persistence — load/persist project-level permission rules.

Ported from: packages/core/src/tool/executor/permission-rules-persistence.ts
Original package: @zcode/core (tool/executor)

# 从 permission-flow.ts 拆出（后者引入 responder 竞速后超 400 行）。这三个 helper 只与
# sessionStore 的 project permission 存取内聚，与 ask 时序无关。
#
# PORT: ``@zcode/contracts`` 的 ``PermissionRuleset`` / ``PermissionUpdate`` / ``ProjectId`` /
# ``TraceContext`` 及 ``traceContextToLogContext`` 未移植，保留为 ``Any``。``applyPermissionUpdates``
# 来自同包 ``permission_rules.py``。
"""

from __future__ import annotations

from typing import Any, Sequence

from ...contracts.tracing import trace_context_to_log_context  # type: ignore
from .permission_rules import apply_permission_updates
from .types import ToolExecutorDeps

__all__ = ["load_project_permission_ruleset", "persist_project_permission_updates"]


async def load_project_permission_ruleset(deps: ToolExecutorDeps) -> Any:
    if deps.session_store is None:
        return None
    project_id = await resolve_project_id(deps)
    if project_id is None:
        return None
    return deps.session_store.get_project_permission(project_id)


async def persist_project_permission_updates(
    deps: ToolExecutorDeps,
    updates: Sequence[Any],
    trace_context: Any,
) -> None:
    if len(updates) == 0:
        return

    if deps.session_store is None:
        if deps.logger is not None:
            deps.logger.warn(
                "Project permission update skipped without session store",
                {
                    **trace_context_to_log_context(trace_context),
                    "event": "tool.permission.project_update.skipped",
                    "module": "core.tool.executor",
                    "status": "completed",
                },
            )
        return

    project_id = await resolve_project_id(deps)
    if project_id is None:
        if deps.logger is not None:
            deps.logger.warn(
                "Project permission update skipped without persisted session",
                {
                    **trace_context_to_log_context(trace_context),
                    "event": "tool.permission.project_update.skipped",
                    "module": "core.tool.executor",
                    "status": "completed",
                },
            )
        return

    current = await deps.session_store.get_project_permission(project_id) or {"version": 1}
    next_ruleset = apply_permission_updates(current, list(updates))
    await deps.session_store.save_project_permission({"projectID": project_id, "permission": next_ruleset})

    if deps.logger is not None:
        deps.logger.info(
            "Project permission updated",
            {
                **trace_context_to_log_context(trace_context),
                "event": "tool.permission.project_update.saved",
                "module": "core.tool.executor",
                "status": "completed",
                "updateCount": len(updates),
            },
        )


async def resolve_project_id(deps: ToolExecutorDeps) -> Any:
    session = await deps.session_store.get_session(deps.session_id) if deps.session_store is not None else None
    return session.projectID if session is not None else None
