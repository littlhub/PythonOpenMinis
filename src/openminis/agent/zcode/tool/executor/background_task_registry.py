"""后台任务 runtime-task registry 登记 / 更新 / 通知认领。

Ported from: packages/core/src/tool/executor/background-task-registry.ts
Original package: @zcode/core (tool/executor)

# PORT: ``RuntimeTaskSnapshot`` / ``RuntimeTaskType`` 来自 core/src/runtime-task/registry.ts，
# 尚未移植；本模块只把它们当 loose ``Any``（dict）使用，键名沿用 TS 形状（camelCase）。
# ``isTerminalRuntimeTask`` 同样来自该模块，预测导入路径。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

# PORT: ``RuntimeTaskSnapshot`` / ``RuntimeTaskType`` / ``isTerminalRuntimeTask`` ——
# core/src/runtime-task/registry.ts，not ported yet；按契约把快照当 dict。
from ...runtime_task.registry import (  # type: ignore
    RuntimeTaskSnapshot,
    RuntimeTaskType,
    is_terminal_runtime_task,
)
from ..types import ExecutableToolCall
from .background_task_output import background_task_output_metadata
from .types import ToolExecutorDeps
from .utils import is_record

# PORT: 这三个常量原属 @zcode/contracts（tools/create-workflow.ts、tools/amend-workflow.ts、
# tools/resume-workflow-run.ts），尚未移植；值按 TS 契约原样镜像。
CREATE_WORKFLOW_TOOL_NAME = "CreateWorkflow"
AMEND_WORKFLOW_TOOL_NAME = "AmendWorkflow"
RESUME_WORKFLOW_RUN_TOOL_NAME = "ResumeWorkflowRun"

# PORT: ``serializeWorkflowArtifact`` —— ./workflow-artifact.ts，not ported yet。
from .workflow_artifact import serialize_workflow_artifact  # type: ignore


def is_dynamic_workflow_run_dispatch_tool_name(name: str) -> bool:
    """dwf run 分派谓词：CreateWorkflow / AmendWorkflow / ResumeWorkflowRun 共用一个 run 生命周期。"""
    return (
        name == CREATE_WORKFLOW_TOOL_NAME
        or name == AMEND_WORKFLOW_TOOL_NAME
        or name == RESUME_WORKFLOW_RUN_TOOL_NAME
    )


def register_runtime_background_task(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    task_id: str,
    output: dict[str, Any],
    turn_id: Optional[str] = None,
) -> None:
    """登记 / 更新一条后台任务 runtime 条目（会话回收护栏 + 面板可见性）。"""
    task_type = _runtime_task_type_for_tool_call(tool_call)
    if not task_type or not deps.runtime_task_registry:
        return
    existing: Optional[dict[str, Any]] = deps.runtime_task_registry.get(task_id)
    description = _runtime_task_description(tool_call, output) or _default_runtime_task_description(task_type)
    output_file = background_task_output_metadata(output).get("outputFile")
    # 同进程 cancel 终态 → 未重启即 resume 时，既有条目携带上一轮 claim 的 notified:true，
    # 重建若原样保留，claimRuntimeBackgroundTaskNotification 会据此拒收——恢复 run 的终态模型通知被吞。
    # 因此重臂时整体复位**结算面**；**身份面**维持 existing??new 不动。
    rearm = is_dynamic_workflow_run_dispatch_tool_name(tool_call.name)
    # 新生命 = 在**终态**条目上重臂。它必须走 register() 而非 update()：register 会用当前
    # activeBranchGeneration 重盖 branchGeneration。
    new_life = bool(rearm and existing is not None and is_terminal_runtime_task(existing))
    base: dict[str, Any] = existing if isinstance(existing, dict) else {}
    next_snap: dict[str, Any] = {
        **base,
        "agentId": base.get("agentId") or task_id,
        "agentType": base.get("agentType") or task_type,
        "description": base.get("description") or description,
        "isBackgrounded": True,
        "outputFile": output_file or base.get("outputFile"),
        "parentToolCallId": base.get("parentToolCallId") or tool_call.id,
        "startedAt": base.get("startedAt") or datetime.now(timezone.utc),
        "status": "running",
        "taskId": task_id,
        "taskType": task_type,
        "type": task_type,
        "turnId": base.get("turnId") or turn_id,
        # —— 结算面复位（仅重臂 = 新的一轮生命周期；非重臂的重挂载保留既有结算面）——
        "notified": False if rearm else (base.get("notified") or False),
        "completedAt": None if rearm else base.get("completedAt"),
        "error": None if rearm else base.get("error"),
        "exitCode": None if rearm else base.get("exitCode"),
        "resultText": None if rearm else base.get("resultText"),
        "pid": None if rearm else base.get("pid"),
        # 上一轮是谁停的，与新生命无关。
        "stopInitiator": None if rearm else base.get("stopInitiator"),
    }
    if existing and not new_life:
        deps.runtime_task_registry.update(task_id, lambda current: next_snap)
        return
    # 首次登记或新生命：剥掉继承来的 branchGeneration，交给 register 按当前 active 分支盖章。
    fresh = {k: v for k, v in next_snap.items() if k != "branchGeneration"}
    deps.runtime_task_registry.register(fresh)


def update_runtime_background_task(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    task_id: str,
    status: str,
    snapshot: Optional[Any] = None,
) -> None:
    """把一次快照观测合并进既有 runtime 条目（终态条目不再变动）。"""
    task_type = _runtime_task_type_for_tool_call(tool_call)
    if not task_type or not deps.runtime_task_registry:
        return

    def updater(current: Any) -> dict[str, Any]:
        if is_terminal_runtime_task(current):
            return current
        base = current if isinstance(current, dict) else {}
        return {
            **base,
            "completedAt": _runtime_task_completed_at(status, snapshot) or base.get("completedAt"),
            "description": _runtime_task_description(tool_call, None, snapshot) or base.get("description"),
            "error": _runtime_task_error(snapshot) or base.get("error"),
            "exitCode": _runtime_task_exit_code(snapshot) or base.get("exitCode"),
            "isBackgrounded": True,
            "outputFile": background_task_output_metadata(snapshot).get("outputFile") or base.get("outputFile"),
            "pid": _runtime_task_pid(snapshot) or base.get("pid"),
            # `?? current.resultText`：undefined 绝不清掉已存下的产物。
            "resultText": _runtime_task_result_text(tool_call, status, snapshot) or base.get("resultText"),
            "status": _normalize_runtime_task_status(status),
            "taskType": task_type,
            "type": task_type,
        }

    deps.runtime_task_registry.update(task_id, updater)


def _runtime_task_result_text(
    tool_call: ExecutableToolCall,
    status: str,
    snapshot: Any,
) -> Optional[str]:
    """workflow run 的产物文本（只在 dwf 分派名的**终态**快照上取）。"""
    if not is_dynamic_workflow_run_dispatch_tool_name(tool_call.name):
        return None
    if status == "running":
        return None
    if not isinstance(snapshot, dict) or "output" not in snapshot:
        return None
    return serialize_workflow_artifact(snapshot["output"])


def claim_runtime_background_task_notification(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    task_id: str,
) -> bool:
    """认领一次终态通知令牌；已认领则返回 False（避免重复入队）。"""
    if not _runtime_task_type_for_tool_call(tool_call) or not deps.runtime_task_registry:
        return True
    claimed = False

    def cb(current: Any) -> dict[str, Any]:
        nonlocal claimed
        if isinstance(current, dict) and current.get("notified"):
            return current
        claimed = True
        base = current if isinstance(current, dict) else {}
        return {**base, "notified": True}

    result = deps.runtime_task_registry.update(task_id, cb)
    return True if result is None else claimed


def release_runtime_background_task_notification(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    task_id: str,
) -> None:
    """释放通知令牌（enqueue 失败时回滚 claim）。"""
    if not _runtime_task_type_for_tool_call(tool_call) or not deps.runtime_task_registry:
        return

    def cb(current: Any) -> Any:
        if isinstance(current, dict) and current.get("notified"):
            return {**current, "notified": False}
        return current

    deps.runtime_task_registry.update(task_id, cb)


def remove_runtime_background_task(
    deps: ToolExecutorDeps,
    tool_call: ExecutableToolCall,
    task_id: str,
) -> None:
    """移除一条 runtime 条目。"""
    if not _runtime_task_type_for_tool_call(tool_call) or not deps.runtime_task_registry:
        return
    deps.runtime_task_registry.remove(task_id)


def _runtime_task_type_for_tool_call(tool_call: ExecutableToolCall) -> Optional[RuntimeTaskType]:
    name = tool_call.name
    if name == "Bash":
        return "local_bash"
    if name == "Workflow":
        return "local_workflow"
    # dwf 的两个入口共用一个 taskType：缺席则 registerRuntimeBackgroundTask 直接 return。
    return "local_dynamic_workflow" if is_dynamic_workflow_run_dispatch_tool_name(name) else None


def _default_runtime_task_description(task_type: RuntimeTaskType) -> str:
    if task_type == "local_bash":
        return "Bash background command"
    if task_type == "local_workflow":
        return "Workflow background task"
    if task_type == "local_dynamic_workflow":
        return "Dynamic workflow run"
    if task_type == "local_agent":
        return "Agent background task"
    if task_type == "monitor_mcp":
        return "Monitor background task"
    return "Background task"


def _normalize_runtime_task_status(status: str) -> str:
    if status == "running":
        return "running"
    if status == "completed":
        return "completed"
    if status in ("cancelled", "timed_out"):
        return "killed"
    if status == "lost":
        return "lost"
    return "failed"


def _runtime_task_description(
    tool_call: ExecutableToolCall,
    output: Optional[dict[str, Any]] = None,
    snapshot: Any = None,
) -> Optional[str]:
    input = tool_call.input if is_record(tool_call.input) else {}
    if isinstance(snapshot, dict):
        if "description" in snapshot and isinstance(snapshot["description"], str):
            return snapshot["description"]
        if "name" in snapshot and isinstance(snapshot["name"], str):
            return snapshot["name"]
    if isinstance(output, dict) and isinstance(output.get("name"), str):
        return output["name"]
    if isinstance(input.get("description"), str):
        return input["description"]
    if isinstance(input.get("command"), str):
        return input["command"]
    if isinstance(input.get("name"), str):
        return input["name"]
    if isinstance(input.get("scriptPath"), str):
        return input["scriptPath"]
    return None


def _runtime_task_completed_at(status: str, snapshot: Any) -> Optional[datetime]:
    if status == "running":
        return None
    completed = snapshot.get("completedAt") if isinstance(snapshot, dict) else None
    return completed if completed is not None else datetime.now(timezone.utc)


def _runtime_task_error(snapshot: Any) -> Optional[str]:
    if not isinstance(snapshot, dict) or "error" not in snapshot:
        return None
    error = snapshot["error"]
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        return error.get("message")
    return None


def _runtime_task_exit_code(snapshot: Any) -> Optional[int]:
    if not isinstance(snapshot, dict) or "result" not in snapshot:
        return None
    result = snapshot["result"]
    return result.get("exitCode") if isinstance(result, dict) else None


def _runtime_task_pid(snapshot: Any) -> Optional[int]:
    if not isinstance(snapshot, dict) or "pid" not in snapshot:
        return None
    return snapshot["pid"]
