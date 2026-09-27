"""后台任务生命周期追踪：登记、轮询、终态等待、结算面通知。

Ported from: packages/core/src/tool/executor/background-tasks.ts
Original package: @zcode/core (tool/executor)

# PORT: 以下依赖尚未移植，按预测路径导入（``# type: ignore``），运行时再补：
#   - ``SessionEventType`` / ``traceContextToLogContext`` —— @zcode/contracts（events / tracing）
#   - ``isSubagentDispatchToolName`` —— ../compat
#   - ``formatTaskNotification`` —— ../../runtime-task/notification
#   - ``describeWorkflowScriptPath`` —— ../handlers/workflow-script-path
#   - workflow artifact 系列 —— ./workflow-artifact、./workflow-published-artifacts
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# PORT: @zcode/contracts（events / tracing），not ported yet。
from ...contracts.events import SessionEventType  # type: ignore
from ...contracts.tracing import trace_context_to_log_context  # type: ignore
# PORT: ../compat，not ported yet。
from ..compat import is_subagent_dispatch_tool_name  # type: ignore
from ..types import ExecutableToolCall
# PORT: ../../runtime-task/notification，not ported yet。
from ...runtime_task.notification import format_task_notification  # type: ignore
# PORT: ../handlers/workflow-script-path，not ported yet。
from ..handlers.workflow_script_path import describe_workflow_script_path  # type: ignore
from .background_task_output import background_task_output_metadata
from .background_task_registry import (
    claim_runtime_background_task_notification,
    is_dynamic_workflow_run_dispatch_tool_name,
    register_runtime_background_task,
    release_runtime_background_task_notification,
    remove_runtime_background_task,
    update_runtime_background_task,
)
from .types import ToolExecutorDeps
from .utils import is_record
# PORT: ./workflow-artifact，not ported yet。
from .workflow_artifact import (  # type: ignore
    build_workflow_reports_manifest_section,
    build_workflow_reports_notification_section,
    serialize_workflow_artifact,
)
# PORT: ./workflow-published-artifacts，not ported yet。
from .workflow_published_artifacts import (  # type: ignore
    build_workflow_artifacts_manifest_section,
    build_workflow_artifacts_notification_section,
    to_published_artifact_summaries,
    WORKFLOW_ARTIFACTS_NOTIFICATION_MAX_LINES,
)


class BackgroundTaskTracker:
    """一个工具的后台生命周期追踪器。五处分派退化成读 lifecycleProvider 的字段。"""

    def __init__(self, deps: ToolExecutorDeps) -> None:
        self.deps = deps
        self.background_pollers: set[str] = set()
        # 轮询 / 最大运行时节点的 asyncio 任务，便于 stop 时取消。
        self._poll_tasks: dict[str, "asyncio.Task[None]"] = {}
        self._max_runtime_tasks: dict[str, "asyncio.Task[None]"] = {}

    async def track_background_task(
        self,
        tool_call: ExecutableToolCall,
        output: Any,
        trace_context: Any,
        turn_id: Optional[str] = None,
    ) -> None:
        if not is_record(output):
            return
        if not _is_background_task_launch(tool_call, output):
            return
        task_id = (
            output["backgroundTaskId"]
            if isinstance(output.get("backgroundTaskId"), str)
            else (output["agentId"] if isinstance(output.get("agentId"), str) else None)
        )
        if not task_id or task_id in self.background_pollers:
            return

        self.background_pollers.add(task_id)
        register_runtime_background_task(self.deps, tool_call, task_id, output, turn_id)
        try:
            await self._emit_background_task_event(
                SessionEventType.BACKGROUND_TASK_STARTED,
                self._background_task_payload(tool_call, task_id, "running", None, output),
                trace_context,
                turn_id,
            )
        except Exception as error:
            self.background_pollers.discard(task_id)
            remove_runtime_background_task(self.deps, tool_call, task_id)
            raise error

        has_snapshot_provider = self._has_background_task_snapshot_provider(tool_call)
        has_direct_waiter = self._has_direct_background_task_waiter(tool_call)
        self._log(
            "info",
            "Background task tracking started",
            trace_context,
            event="background_task.tracking.started",
            hasDirectWaiter=has_direct_waiter,
            hasSnapshotProvider=has_snapshot_provider,
            taskId=task_id,
            toolName=tool_call.name,
        )

        if not has_snapshot_provider and not has_direct_waiter:
            self._log(
                "info",
                "Background task tracking lost without snapshot source",
                trace_context,
                event="background_task.tracking.lost",
                reason="missing_snapshot_source",
                taskId=task_id,
                toolName=tool_call.name,
            )
            update_runtime_background_task(self.deps, tool_call, task_id, "lost")
            self._maybe_enqueue_background_task_notification(
                tool_call, task_id, "lost", None, trace_context, output
            )
            await self._emit_background_task_event(
                SessionEventType.BACKGROUND_TASK_COMPLETED,
                self._background_task_payload(tool_call, task_id, "lost", None, output),
                trace_context,
                turn_id,
            )
            self.background_pollers.discard(task_id)
            return

        # 可暂停状态机的共享标志（用可变容器，便于闭包就地修改）。
        state: dict[str, Any] = {
            "last_snapshot_signature": "",
            "completing": False,
            "polling": False,
            "stopped": False,
        }

        def stop_tracking() -> None:
            state["stopped"] = True
            poll_task = self._poll_tasks.pop(task_id, None)
            if poll_task is not None:
                poll_task.cancel()
            max_task = self._max_runtime_tasks.pop(task_id, None)
            if max_task is not None:
                max_task.cancel()
            self.background_pollers.discard(task_id)

        # subagent 后台 Bash 的最大运行时节点。
        if (
            tool_call.name == "Bash"
            and self.deps.runtime_scope == "subagent"
            and self.deps.subagent_background_bash_max_ms is not None
            and self.deps.execution_port is not None
            and getattr(self.deps.execution_port, "cancel_background_task", None) is not None
        ):
            self._max_runtime_tasks[task_id] = asyncio.ensure_future(
                self._max_runtime_guard(tool_call, task_id, trace_context)
            )

        async def emit_running_update(snapshot: Any) -> None:
            signature = self._background_snapshot_signature(snapshot)
            if signature == state["last_snapshot_signature"]:
                return
            state["last_snapshot_signature"] = signature
            update_runtime_background_task(self.deps, tool_call, task_id, "running", snapshot)
            await self._emit_background_task_event(
                SessionEventType.BACKGROUND_TASK_UPDATED,
                self._background_task_payload(tool_call, task_id, "running", snapshot, output),
                trace_context,
                turn_id,
            )

        async def emit_terminal_snapshot(snapshot: Any) -> None:
            if state["stopped"] or state["completing"]:
                return
            state["completing"] = True
            try:
                if snapshot is None:
                    self._log(
                        "info",
                        "Background task terminal snapshot missing",
                        trace_context,
                        event="background_task.tracking.lost",
                        reason="snapshot_missing",
                        taskId=task_id,
                        toolName=tool_call.name,
                    )
                    update_runtime_background_task(self.deps, tool_call, task_id, "lost")
                    self._maybe_enqueue_background_task_notification(
                        tool_call, task_id, "lost", None, trace_context, output
                    )
                    await self._emit_background_task_event(
                        SessionEventType.BACKGROUND_TASK_COMPLETED,
                        self._background_task_payload(tool_call, task_id, "lost", None, output),
                        trace_context,
                        turn_id,
                    )
                    state["stopped"] = True
                    stop_tracking()
                    return

                if isinstance(snapshot, dict) and snapshot.get("status") == "running":
                    update_runtime_background_task(self.deps, tool_call, task_id, "running", snapshot)
                    await emit_running_update(snapshot)
                    if not has_snapshot_provider:
                        state["stopped"] = True
                        stop_tracking()
                    return

                if self._is_notified_local_agent_snapshot(tool_call, snapshot):
                    self._log(
                        "debug",
                        "Background task terminal notification already handled by subagent",
                        trace_context,
                        event="background_task.tracking.notification_already_handled",
                        taskId=task_id,
                        toolName=tool_call.name,
                    )
                    state["stopped"] = True
                    stop_tracking()
                    return

                self._log(
                    "info",
                    "Background task terminal snapshot observed",
                    trace_context,
                    event="background_task.tracking.terminal",
                    taskId=task_id,
                    taskStatus=snapshot.get("status") if isinstance(snapshot, dict) else None,
                    toolName=tool_call.name,
                )
                snapshot_status = snapshot.get("status") if isinstance(snapshot, dict) else "lost"
                update_runtime_background_task(self.deps, tool_call, task_id, snapshot_status, snapshot)
                self._maybe_enqueue_background_task_notification(
                    tool_call, task_id, snapshot_status, snapshot, trace_context
                )
                await self._emit_background_task_event(
                    SessionEventType.BACKGROUND_TASK_COMPLETED,
                    self._background_task_payload(tool_call, task_id, snapshot_status, snapshot, output),
                    trace_context,
                    turn_id,
                )
                state["stopped"] = True
                stop_tracking()
            finally:
                state["completing"] = False

        async def poll() -> None:
            if state["polling"] or state["stopped"] or not has_snapshot_provider:
                return
            state["polling"] = True
            try:
                snapshot = await self._get_background_task_snapshot(tool_call, task_id)
                if snapshot is None:
                    await emit_terminal_snapshot(None)
                    return
                if isinstance(snapshot, dict) and snapshot.get("status") == "running":
                    await emit_running_update(snapshot)
                    return
                await emit_terminal_snapshot(snapshot)
            except Exception as error:
                self._log(
                    "warn",
                    "Background task polling failed",
                    trace_context,
                    errorMessage=_err_message(error),
                    taskId=task_id,
                )
            finally:
                state["polling"] = False

        async def wait_for_completion() -> None:
            try:
                snapshot = await self._wait_for_background_task_snapshot(tool_call, task_id)
                await emit_terminal_snapshot(snapshot)
            except Exception as error:
                self._log(
                    "warn",
                    "Background task wait failed",
                    trace_context,
                    errorMessage=_err_message(error),
                    taskId=task_id,
                )
                if not has_snapshot_provider:
                    state["stopped"] = True
                    stop_tracking()

        if has_snapshot_provider:
            await poll()
            if not state["stopped"]:
                self._poll_tasks[task_id] = asyncio.ensure_future(self._poll_loop(state, poll))

        if has_direct_waiter and not state["stopped"]:
            asyncio.ensure_future(wait_for_completion())

    async def _poll_loop(self, state: dict[str, Any], poll_fn: Any) -> None:
        """每秒轮询一次（镜像 TS ``setInterval(..., 1000)``）。"""
        try:
            while not state["stopped"]:
                await asyncio.sleep(1)
                if state["stopped"]:
                    break
                await poll_fn()
        except asyncio.CancelledError:
            pass

    async def _max_runtime_guard(
        self, tool_call: ExecutableToolCall, task_id: str, trace_context: Any
    ) -> None:
        """subagent 后台 Bash 超过最大运行时长则取消。"""
        await asyncio.sleep((self.deps.subagent_background_bash_max_ms or 0) / 1000.0)
        self._log(
            "warn",
            "Subagent background Bash exceeded max runtime; cancelling",
            trace_context,
            event="background_task.subagent_bash.max_runtime_exceeded",
            taskId=task_id,
            toolName=tool_call.name,
        )
        try:
            resp = self.deps.execution_port.cancel_background_task(task_id)
            if inspect.isawaitable(resp):
                await resp
        except Exception as error:
            self._log(
                "warn",
                "Subagent background Bash cancellation failed",
                trace_context,
                errorMessage=_err_message(error),
                event="background_task.subagent_bash.cancel_failed",
                taskId=task_id,
                toolName=tool_call.name,
            )

    async def _emit_background_task_event(
        self,
        event_type: Any,
        payload: dict[str, Any],
        trace_context: Any,
        turn_id: Optional[str],
    ) -> None:
        await self.deps.emit_event(
            {
                "id": uuid.uuid4().hex,
                "sessionId": self.deps.session_id,
                "turnId": turn_id,
                "type": event_type,
                "timestamp": datetime.now(timezone.utc),
                "traceId": getattr(trace_context, "trace_id", None),
                "sequenceNumber": 0,
                "payload": payload,
            }
        )

    def _background_task_payload(
        self,
        tool_call: ExecutableToolCall,
        task_id: str,
        status: str,
        snapshot: Any = None,
        output: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        input = tool_call.input if is_record(tool_call.input) else {}
        output_metadata = background_task_output_metadata(snapshot, output)
        return {
            "taskId": task_id,
            "toolCallId": tool_call.id,
            "toolName": tool_call.name,
            # runtime 在事实产生处一次裁决任务类别，生命周期行为不受影响。
            "taskKind": _background_task_kind(tool_call.name),
            "childSessionId": output_metadata.get("childSessionId"),
            "cancellable": status == "running" and self._can_cancel_background_task(tool_call),
            "command": input.get("command") if isinstance(input.get("command"), str) else None,
            # dwf 分派名的展示名与完成通知共用同一条兜底链（含 input.name）。
            "description": (
                _workflow_task_subject(tool_call, task_id, snapshot, output)
                if is_dynamic_workflow_run_dispatch_tool_name(tool_call.name)
                else (
                    input.get("description")
                    if isinstance(input.get("description"), str)
                    else (
                        snapshot.get("description")
                        if isinstance(snapshot, dict) and isinstance(snapshot.get("description"), str)
                        else None
                    )
                )
            ),
            "status": status,
            "pid": snapshot.get("pid") if isinstance(snapshot, dict) and "pid" in snapshot else None,
            "startedAt": snapshot.get("startedAt") if isinstance(snapshot, dict) else None,
            "completedAt": snapshot.get("completedAt") if isinstance(snapshot, dict) else None,
            "outputPath": output_metadata.get("outputFile"),
            "stderrPersistedOutputPath": output_metadata.get("stderrFile"),
            "stdoutPersistedOutputPath": output_metadata.get("stdoutFile"),
            "outputBytes": output_metadata.get("outputBytes"),
            "outputTruncated": output_metadata.get("outputTruncated"),
            "outputTail": output_metadata.get("outputTail"),
            "stderrBytes": output_metadata.get("stderrBytes"),
            "stderrTail": output_metadata.get("stderrTail"),
            "stdoutBytes": output_metadata.get("stdoutBytes"),
            "stdoutTail": output_metadata.get("stdoutTail"),
            "terminalId": task_id,
        }

    def _background_snapshot_signature(self, snapshot: Any) -> str:
        if not isinstance(snapshot, dict):
            return "{}"
        return json.dumps(
            {
                "pid": snapshot.get("pid") if "pid" in snapshot else None,
                "stderrBytes": snapshot.get("stderrBytes") if "stderrBytes" in snapshot else None,
                "stderrTail": snapshot.get("stderrTail") if "stderrTail" in snapshot else None,
                "stdoutBytes": snapshot.get("stdoutBytes") if "stdoutBytes" in snapshot else None,
                "stdoutTail": snapshot.get("stdoutTail") if "stdoutTail" in snapshot else None,
            },
            sort_keys=True,
        )

    def _can_cancel_background_task(self, tool_call: ExecutableToolCall) -> bool:
        return self._lifecycle_provider(tool_call).get("cancellable") is True

    def _is_notified_local_agent_snapshot(self, tool_call: ExecutableToolCall, snapshot: Any) -> bool:
        if not isinstance(snapshot, dict):
            return False
        return (
            is_subagent_dispatch_tool_name(tool_call.name)
            and snapshot.get("type") == "local_agent"
            and snapshot.get("notified") is True
        )

    def _maybe_enqueue_background_task_notification(
        self,
        tool_call: ExecutableToolCall,
        task_id: str,
        status: str,
        snapshot: Any,
        trace_context: Any,
        output: Optional[dict[str, Any]] = None,
    ) -> None:
        if self.deps.enqueue_background_task_notification is None:
            self._log(
                "debug",
                "Background task notification queue unavailable",
                trace_context,
                event="background_task.notification.queue_unavailable",
                taskId=task_id,
                taskStatus=status,
                toolName=tool_call.name,
            )
            return

        # 被修订替代的 run 不发终态通知：停下它的那次 AmendWorkflow 的工具结果就是模型
        # 对这次停止的全部所知，再来一条「你停了 run A，现在去修订它」会把模型送进循环。
        terminal = _workflow_snapshot_terminal(status, snapshot)
        if terminal is not None and terminal.get("stopReason") == "superseded":
            claim_runtime_background_task_notification(self.deps, tool_call, task_id)
            self._log(
                "info",
                "Background task notification suppressed: run superseded",
                trace_context,
                event="background_task.notification.suppressed",
                reason="workflow_run_superseded",
                taskId=task_id,
                taskStatus=status,
                toolName=tool_call.name,
            )
            return

        if (
            self.deps.should_enqueue_background_task_notification is not None
            and self.deps.should_enqueue_background_task_notification(
                {
                    "runtimeScope": self.deps.runtime_scope,
                    "status": status,
                    "taskId": task_id,
                    "toolName": tool_call.name,
                    "traceContext": trace_context,
                }
            )
            is False
        ):
            self._log(
                "info",
                "Background task notification suppressed by runtime policy",
                trace_context,
                event="background_task.notification.suppressed",
                taskId=task_id,
                taskStatus=status,
                toolName=tool_call.name,
            )
            return

        text = self._format_background_task_notification(tool_call, task_id, status, snapshot, output)
        if not text:
            self._log(
                "debug",
                "Background task notification skipped without formatted message",
                trace_context,
                event="background_task.notification.skipped",
                reason="empty_message",
                taskId=task_id,
                taskStatus=status,
                toolName=tool_call.name,
            )
            return
        # completion 只有成功 claim 后才能入队，避免模型同时收到 tool result 和重复通知。
        if not claim_runtime_background_task_notification(self.deps, tool_call, task_id):
            self._log(
                "debug",
                "Background task notification already claimed",
                trace_context,
                event="background_task.tracking.notification_already_handled",
                taskId=task_id,
                taskStatus=status,
                toolName=tool_call.name,
            )
            return

        origin_meta: Any = None
        if tool_call.name == "Bash":
            origin_meta = {
                "backgroundSource": "bash",
                "title": _resolve_bash_background_result_title(tool_call, task_id),
                "workId": task_id,
            }
        elif is_dynamic_workflow_run_dispatch_tool_name(tool_call.name):
            origin_meta = _build_workflow_notification_origin_meta(
                tool_call, task_id, status, snapshot, output
            )

        try:
            self.deps.enqueue_background_task_notification(
                {
                    **({"originMeta": origin_meta} if origin_meta is not None else {}),
                    "taskId": task_id,
                    "text": text,
                    "toolName": tool_call.name,
                    "traceContext": trace_context,
                }
            )
        except Exception as error:
            release_runtime_background_task_notification(self.deps, tool_call, task_id)
            self._log(
                "warn",
                "Background task notification enqueue failed",
                trace_context,
                errorMessage=_err_message(error),
                taskId=task_id,
            )
            return

        self._log(
            "info",
            "Background task notification enqueued",
            trace_context,
            event="background_task.notification.enqueued",
            taskId=task_id,
            taskStatus=status,
            toolName=tool_call.name,
        )

    def _format_background_task_notification(
        self,
        tool_call: ExecutableToolCall,
        task_id: str,
        status: str,
        snapshot: Any,
        output: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        if tool_call.name == "Workflow" or is_dynamic_workflow_run_dispatch_tool_name(tool_call.name):
            return self._format_workflow_task_notification(tool_call, task_id, status, snapshot, output)
        if tool_call.name != "Bash":
            return None

        input = tool_call.input if is_record(tool_call.input) else {}
        command = input.get("command") if isinstance(input.get("command"), str) else None
        description = input.get("description") if isinstance(input.get("description"), str) else None
        result = snapshot.get("result") if isinstance(snapshot, dict) and "result" in snapshot else None
        output_metadata = background_task_output_metadata(snapshot, output)
        notification_status = _normalize_bash_task_notification_status(status)
        summary = _build_background_task_summary(
            {
                "command": command,
                "description": description,
                "exitCode": result.get("exitCode") if isinstance(result, dict) else None,
                "lost": status == "lost",
                "status": notification_status,
            }
        )
        return format_task_notification(
            {
                "description": description,
                "outputFile": output_metadata.get("outputFile"),
                "status": notification_status,
                "summary": summary,
                "taskId": task_id,
                "taskType": "local_bash",
                "toolUseId": tool_call.id,
            }
        )

    def _format_workflow_task_notification(
        self,
        tool_call: ExecutableToolCall,
        task_id: str,
        status: str,
        snapshot: Any,
        launch_output: Optional[dict[str, Any]] = None,
    ) -> str:
        output = (
            snapshot.get("output")
            if isinstance(snapshot, dict) and "output" in snapshot and is_record(snapshot["output"])
            else launch_output
        )
        subject = _workflow_task_subject(tool_call, task_id, snapshot, output)
        notification_status = _normalize_background_task_notification_status(status)
        terminal = _workflow_snapshot_terminal(status, snapshot)
        stop_reason = (
            terminal.get("stopReason")
            if terminal is not None and terminal.get("stopReason") is not None
            else (
                self.deps.runtime_task_registry.get(task_id).stop_initiator
                if status == "cancelled"
                and self.deps.runtime_task_registry is not None
                and self.deps.runtime_task_registry.get(task_id) is not None
                else None
            )
        )
        summary = _build_workflow_task_summary(
            {
                "lost": status == "lost",
                "status": notification_status,
                "runStatus": terminal.get("runStatus") if terminal is not None else None,
                "stopReason": stop_reason,
                "subject": subject,
            }
        )
        is_dynamic_workflow = is_dynamic_workflow_run_dispatch_tool_name(tool_call.name)
        result = (
            serialize_workflow_artifact(snapshot.get("output"))
            if is_dynamic_workflow and isinstance(snapshot, dict) and "output" in snapshot
            else _string_field(output, "response")
        )
        reports = (
            _build_workflow_reports_notification_section(_workflow_snapshot_reports(snapshot))
            if is_dynamic_workflow
            else None
        )
        artifacts = (
            _build_workflow_artifacts_notification_section(
                _workflow_snapshot_artifacts(snapshot), WORKFLOW_ARTIFACTS_NOTIFICATION_MAX_LINES
            )
            if is_dynamic_workflow
            else None
        )
        script_path = _workflow_snapshot_script_path(snapshot) if is_dynamic_workflow else None
        notification: dict[str, Any] = {
            "description": subject,
            **({"deliveryGuidance": True} if is_dynamic_workflow else {}),
            **(
                {"scriptPath": describe_workflow_script_path(script_path, self.deps.get_working_directory())}
                if script_path is not None
                else {}
            ),
            "error": _runtime_string(snapshot.get("error")) if isinstance(snapshot, dict) and "error" in snapshot else None,
            **({} if reports is None else {"reports": reports}),
            **({} if artifacts is None else {"artifacts": artifacts}),
            "result": result,
            "status": notification_status,
            **({} if terminal is None or terminal.get("runStatus") is None else {"runStatus": terminal["runStatus"]}),
            **({} if stop_reason is None else {"stopReason": stop_reason}),
            **({} if terminal is None or terminal.get("failure") is None else {"failure": terminal["failure"]}),
            "summary": summary,
            "taskId": task_id,
            "taskType": "local_workflow",
            "toolUseId": tool_call.id,
        }
        return format_task_notification(notification)

    def _has_background_task_snapshot_provider(self, tool_call: ExecutableToolCall) -> bool:
        return self._lifecycle_provider(tool_call).get("get_snapshot") is not None

    def _has_direct_background_task_waiter(self, tool_call: ExecutableToolCall) -> bool:
        return self._lifecycle_provider(tool_call).get("wait_for_terminal") is not None

    async def _wait_for_background_task_snapshot(
        self, tool_call: ExecutableToolCall, task_id: str
    ) -> Any:
        waiter = self._lifecycle_provider(tool_call).get("wait_for_terminal")
        return await waiter(task_id) if waiter is not None else None

    async def _get_background_task_snapshot(
        self, tool_call: ExecutableToolCall, task_id: str
    ) -> Any:
        provider = self._lifecycle_provider(tool_call).get("get_snapshot")
        return await provider(task_id) if provider is not None else None

    def _lifecycle_provider(self, tool_call: ExecutableToolCall) -> dict[str, Any]:
        """按工具名解析后台生命周期提供者（getSnapshot / waitForTerminal / cancellable）。"""
        deps = self.deps
        name = tool_call.name

        if is_subagent_dispatch_tool_name(name):
            get_task = getattr(deps.subagent_port, "get_task", None) if deps.subagent_port else None
            return {
                **({"get_snapshot": lambda tid: get_task(tid)} if get_task is not None else {}),
                "cancellable": bool(getattr(deps.subagent_port, "stop_task", None))
                if deps.subagent_port is not None
                else False,
            }

        if is_dynamic_workflow_run_dispatch_tool_name(name):
            port = deps.dynamic_workflow_run_port
            if port is None:
                return {}
            return {
                "get_snapshot": lambda tid: port.get_task(tid),
                **(
                    {"wait_for_terminal": lambda tid: port.wait_for_task(tid)}
                    if getattr(port, "wait_for_task", None) is not None
                    else {}
                ),
                "cancellable": getattr(port, "cancel", None) is not None,
            }

        if name == "Workflow":
            get_task = getattr(deps.workflow_port, "get_task", None) if deps.workflow_port else None
            waiter = _get_workflow_task_waiter(deps.workflow_port)
            return {
                **({"get_snapshot": lambda tid: get_task(tid)} if get_task is not None else {}),
                **(
                    {"wait_for_terminal": lambda tid: waiter.wait_for_task(tid)}
                    if waiter is not None
                    else {}
                ),
                "cancellable": False,
            }

        if name == "Bash":
            waiter = _get_background_task_waiter(deps.execution_port)
            get_background_task = getattr(deps.execution_port, "get_background_task", None)
            return {
                **(
                    {"get_snapshot": lambda tid: get_background_task(tid)}
                    if get_background_task is not None
                    else {}
                ),
                **(
                    {"wait_for_terminal": lambda tid: waiter.wait_for_background_task(tid)}
                    if waiter is not None
                    else {}
                ),
                "cancellable": bool(getattr(deps.execution_port, "cancel_background_task", None)),
            }

        # 其余工具沿用 executionPort 的通用后台面（无直接等待者）。
        get_background_task = getattr(deps.execution_port, "get_background_task", None)
        return {
            **(
                {"get_snapshot": lambda tid: get_background_task(tid)}
                if get_background_task is not None
                else {}
            ),
            "cancellable": bool(getattr(deps.execution_port, "cancel_background_task", None)),
        }

    def _log(self, level: str, message: str, trace_context: Any, **fields: Any) -> None:
        logger = self.deps.logger
        if logger is None:
            return
        fn = getattr(logger, level, None)
        if fn is None:
            return
        try:
            fn(message, {**trace_context_to_log_context(trace_context), **fields})
        except Exception:
            try:
                fn(message, fields)
            except Exception:
                pass


def _is_background_task_launch(tool_call: ExecutableToolCall, output: dict[str, Any]) -> bool:
    if output.get("status") == "backgrounded":
        return True
    return is_subagent_dispatch_tool_name(tool_call.name) and output.get("status") == "async_launched"


def _normalize_bash_task_notification_status(status: str) -> str:
    return _normalize_background_task_notification_status(status)


def _normalize_background_task_notification_status(status: str) -> str:
    if status == "completed":
        return "completed"
    if status in ("cancelled", "timed_out", "killed", "stopped"):
        return "killed"
    return "failed"


def _resolve_bash_background_result_title(tool_call: ExecutableToolCall, task_id: str) -> str:
    input = tool_call.input if is_record(tool_call.input) else {}
    description = (_string_field(input, "description") or "").strip()
    command = (_string_field(input, "command") or "").strip()
    return description or command or tool_call.name or task_id


def _build_background_task_summary(input: dict[str, Any]) -> str:
    subject = input.get("description") or input.get("command") or "Bash background command"
    prefix = f'Background command "{subject}"'
    if input.get("lost"):
        return f"{prefix} failed because its in-process state was lost"
    status = input.get("status")
    if status == "completed":
        return f"{prefix} completed" + (
            f" (exit code {input['exitCode']})" if input.get("exitCode") is not None else ""
        )
    if status == "failed":
        return f"{prefix} failed" + (
            f" with exit code {input['exitCode']}" if input.get("exitCode") is not None else ""
        )
    if status == "killed":
        return f"{prefix} was stopped"
    return prefix


def _build_workflow_task_summary(input: dict[str, Any]) -> str:
    subject = input.get("subject") or "Workflow"
    prefix = f'Workflow "{subject}"'
    if input.get("lost"):
        return f"{prefix} failed because its in-process state was lost."
    run_status = input.get("runStatus")
    if run_status == "errored":
        return f"{prefix} errored: the script failed."
    if run_status == "stopped" or input.get("status") == "killed":
        stop_reason = input.get("stopReason")
        if stop_reason == "user":
            return f"{prefix} was stopped by the user."
        if stop_reason == "model":
            return f"{prefix} was stopped by you (TaskStop)."
        if stop_reason == "provider":
            return f"{prefix} was stopped on a provider error."
        if stop_reason == "interrupted":
            return f"{prefix} was stopped: the process that owned it exited."
        if stop_reason == "superseded":
            return f"{prefix} was stopped and superseded by an amended run."
        return f"{prefix} was stopped."
    return f"{prefix} completed." if input.get("status") == "completed" else f"{prefix} failed."


def _workflow_snapshot_terminal(status: str, snapshot: Any) -> Optional[dict[str, Any]]:
    """dwf 快照上的三终态事实（runStatus / stopReason / failure）。"""
    record = snapshot if isinstance(snapshot, dict) else None
    declared = record.get("runStatus") if record is not None else None
    run_status = (
        declared
        if declared in ("completed", "errored", "stopped")
        else (
            "completed"
            if status == "completed"
            else "errored"
            if status == "failed"
            else "stopped"
            if status == "cancelled"
            else None
        )
    )
    if run_status is None:
        return None
    reason = record.get("stopReason") if record is not None else None
    stop_reason = (
        reason
        if run_status == "stopped"
        and reason in ("user", "model", "provider", "interrupted", "superseded")
        else None
    )
    failure = record.get("failure") if is_record(record.get("failure")) else None
    return {
        "runStatus": run_status,
        **({} if stop_reason is None else {"stopReason": stop_reason}),
        **({} if failure is None else {"failure": failure}),
    }


def _workflow_task_subject(
    tool_call: ExecutableToolCall,
    task_id: str,
    snapshot: Any,
    output: Optional[dict[str, Any]],
) -> str:
    input = tool_call.input if is_record(tool_call.input) else {}
    return (
        _string_field(input, "description")
        or (
            _runtime_string(snapshot.get("description"))
            if isinstance(snapshot, dict) and "description" in snapshot
            else None
        )
        or (
            _runtime_string(snapshot.get("name"))
            if isinstance(snapshot, dict) and "name" in snapshot
            else None
        )
        or _string_field(output, "name")
        or _string_field(input, "name")
        or _string_field(input, "scriptPath")
        or task_id
    )


def _runtime_string(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


# manifest 载荷（`WorkflowNotificationMeta`）里各字段的界，发射侧就地截断。
WORKFLOW_NOTIFICATION_SUMMARY_MAX_CHARS = 500
WORKFLOW_NOTIFICATION_RESULT_MAX_CHARS = 4_000
WORKFLOW_NOTIFICATION_ERROR_MAX_CHARS = 2_000


def _build_workflow_notification_origin_meta(
    tool_call: ExecutableToolCall,
    task_id: str,
    status: str,
    snapshot: Any,
    output: Optional[dict[str, Any]],
) -> dict[str, Any]:
    subject = _workflow_task_subject(tool_call, task_id, snapshot, output)
    workflow_notification = _build_workflow_terminal_notification(status, subject, snapshot)
    return {
        "backgroundSource": "workflow",
        "title": subject,
        "workId": task_id,
        **({} if workflow_notification is None else {"workflowNotification": workflow_notification}),
    }


def _build_workflow_terminal_notification(status: str, summary: str, snapshot: Any) -> Optional[dict[str, Any]]:
    """快照级事实 → terminal 判别分支的 manifest 载荷（只在三终态且快照在场时铸造）。"""
    terminal_status = _workflow_terminal_notification_status(status)
    if terminal_status is None or not isinstance(snapshot, dict):
        return None

    terminal = _workflow_snapshot_terminal(status, snapshot)
    meta: dict[str, Any] = {
        "kind": "terminal",
        "status": (terminal or {}).get("runStatus") or terminal_status,
    }
    if terminal is not None and terminal.get("stopReason") is not None:
        meta["stopReason"] = terminal["stopReason"]
    meta["summary"] = summary[:WORKFLOW_NOTIFICATION_SUMMARY_MAX_CHARS]

    output_value = snapshot.get("output") if "output" in snapshot else None
    serialized = serialize_workflow_artifact(output_value)
    if serialized is not None:
        if len(serialized) > WORKFLOW_NOTIFICATION_RESULT_MAX_CHARS:
            meta["result"] = serialized[:WORKFLOW_NOTIFICATION_RESULT_MAX_CHARS]
            meta["resultTruncated"] = True
        else:
            meta["result"] = serialized
        meta["resultForm"] = "prose" if isinstance(output_value, str) else "json"

    error = _runtime_string(snapshot.get("error")) if "error" in snapshot else None
    if error is not None:
        meta["error"] = error[:WORKFLOW_NOTIFICATION_ERROR_MAX_CHARS]

    reports = _build_workflow_reports_manifest_section(_workflow_snapshot_reports(snapshot))
    if reports is not None:
        meta["reports"] = reports

    artifacts_section = _build_workflow_artifacts_manifest_section(_workflow_snapshot_artifacts(snapshot))
    if artifacts_section is not None:
        meta["artifacts"] = artifacts_section.get("artifacts")
        if artifacts_section.get("artifactsTruncated"):
            meta["artifactsTruncated"] = True

    duration_ms = _workflow_notification_duration_ms(snapshot)
    if duration_ms is not None:
        meta["durationMs"] = duration_ms

    return meta


def _workflow_terminal_notification_status(status: str) -> Optional[str]:
    if status == "completed":
        return "completed"
    if status == "failed":
        return "errored"
    if status == "cancelled":
        return "stopped"
    return None


def _workflow_notification_duration_ms(snapshot: Any) -> Optional[int]:
    """``completedAt - startedAt``，两者齐备且差为非负有限数才置。"""
    if not isinstance(snapshot, dict):
        return None
    started_at = snapshot.get("startedAt") if isinstance(snapshot.get("startedAt"), datetime) else None
    completed_at = snapshot.get("completedAt") if isinstance(snapshot.get("completedAt"), datetime) else None
    if started_at is None or completed_at is None:
        return None
    duration_ms = completed_at.timestamp() * 1000 - started_at.timestamp() * 1000
    duration_ms_int = int(duration_ms)
    return duration_ms_int if duration_ms_int >= 0 else None


def _workflow_snapshot_script_path(snapshot: Any) -> Optional[str]:
    if not isinstance(snapshot, dict) or "scriptPath" not in snapshot:
        return None
    return snapshot["scriptPath"] if isinstance(snapshot["scriptPath"], str) and len(snapshot["scriptPath"]) > 0 else None


def _workflow_snapshot_reports(snapshot: Any) -> Optional[list[Any]]:
    if not isinstance(snapshot, dict) or "reports" not in snapshot:
        return None
    return snapshot["reports"] if isinstance(snapshot["reports"], list) else None


def _workflow_snapshot_artifacts(snapshot: Any) -> Any:
    if not isinstance(snapshot, dict) or "artifacts" not in snapshot:
        return None
    return to_published_artifact_summaries(snapshot["artifacts"])


def _string_field(record: Optional[dict[str, Any]], key: str) -> Optional[str]:
    value = record.get(key) if record is not None else None
    return value if isinstance(value, str) else None


def _get_background_task_waiter(execution_port: Any) -> Any:
    if execution_port is None or not hasattr(execution_port, "wait_for_background_task"):
        return None
    if callable(execution_port.wait_for_background_task):
        return execution_port
    return None


def _get_workflow_task_waiter(workflow_port: Any) -> Any:
    if workflow_port is None or not hasattr(workflow_port, "wait_for_task"):
        return None
    if callable(workflow_port.wait_for_task):
        return workflow_port
    return None


def _background_task_kind(tool_name: str) -> str:
    """后台任务的展示类别（面板分组与图标）。"""
    if is_subagent_dispatch_tool_name(tool_name):
        return "subagent"
    return "workflow" if is_dynamic_workflow_run_dispatch_tool_name(tool_name) else "bash"


def _err_message(error: Any) -> str:
    return error.message if isinstance(error, Exception) and hasattr(error, "message") else str(error)
