"""Permission Broker — async client permission coordination.

Ported from: packages/core/src/permission/broker.ts
Original package: @zcode/core (permission)

# PORT: TS ``requestPermission`` 是普通方法、同步创建 ``Promise`` 并注册 pending 记录（不声明
# ``async``）；Python 侧照此实现为同步 ``def``，返回 ``asyncio.Future``（本身可 ``await``），
# 以保持「调用即登记」的时序语义。``setTimeout`` → ``loop.call_later``；``AbortSignal`` 监听器
# → ``core.abort.AbortSignal.add_event_listener`` / ``remove_event_listener``。
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Union

from ..contracts.errors import CoreErrorType, create_core_error
from .ports import (
    PermissionBrokerPort,
    PermissionBrokerRequest,
    PermissionBrokerRequestOptions,
    PermissionBrokerResult,
)

__all__ = [
    "ManualPermissionBrokerOptions",
    "DenyPermissionBroker",
    "ManualPermissionBroker",
    "create_deny_permission_broker",
    "create_manual_permission_broker",
]


@dataclass(slots=True)
class ManualPermissionBrokerOptions:
    """Mirrors ``ManualPermissionBrokerOptions``."""

    on_request: Optional[
        Callable[[PermissionBrokerRequest], Union[Awaitable[None], None]]
    ] = None


@dataclass(slots=True)
class _PendingBrokerRecord:
    """Mirrors ``PendingBrokerRecord``."""

    request: PermissionBrokerRequest
    resolve: Callable[[PermissionBrokerResult], None]
    reject: Callable[[BaseException], None]


class DenyPermissionBroker:
    """Mirrors ``DenyPermissionBroker``.

    # PORT: TS ``implements PermissionBrokerPort``；Python 采用结构化匹配（鸭子类型），
    # 不显式继承 Protocol。
    """

    async def request_permission(
        self,
        request: PermissionBrokerRequest,
        options: Optional[PermissionBrokerRequestOptions] = None,
    ) -> PermissionBrokerResult:
        """Mirrors ``requestPermission(request)``."""
        return PermissionBrokerResult(
            decision="deny",
            reason=f"No permission client configured for {request.tool_name}",
            resolved_at=datetime.now(timezone.utc),
        )


class ManualPermissionBroker:
    """Mirrors ``ManualPermissionBroker``."""

    def __init__(self, options: Optional[ManualPermissionBrokerOptions] = None) -> None:
        self._options: ManualPermissionBrokerOptions = (
            options if options is not None else ManualPermissionBrokerOptions()
        )
        self._pending: dict[str, _PendingBrokerRecord] = {}

    def request_permission(
        self,
        request: PermissionBrokerRequest,
        options: Optional[PermissionBrokerRequestOptions] = None,
    ) -> "asyncio.Future[PermissionBrokerResult]":
        """Mirrors ``requestPermission(request, options?)``。

        # PORT: 返回 ``asyncio.Future`` 而非 ``async def``——原 TS 同步创建 Promise 并登记
        # pending，调用方可随后同步调用 ``resolvePermission``。
        """
        key = request.request_id
        if key in self._pending:
            return self._rejected_future(
                create_core_error(
                    CoreErrorType.INVALID_STATE_TRANSITION,
                    f"Permission request already pending: {key}",
                    {
                        "context": {"requestId": key, "toolCallId": request.tool_call_id},
                        "recoverable": True,
                    },
                )
            )

        loop = asyncio.get_running_loop()
        future: "asyncio.Future[PermissionBrokerResult]" = loop.create_future()
        settled = False
        timeout_handle: Optional[asyncio.TimerHandle] = None

        def cleanup() -> None:
            nonlocal timeout_handle
            if timeout_handle is not None:
                timeout_handle.cancel()
                timeout_handle = None
            if options is not None and options.signal is not None:
                options.signal.remove_event_listener(abort_handler)
            self._pending.pop(key, None)

        def settle(result: PermissionBrokerResult) -> None:
            nonlocal settled
            if settled:
                return
            settled = True
            cleanup()
            if result.resolved_at is None:
                result.resolved_at = datetime.now(timezone.utc)
            if not future.done():
                future.set_result(result)

        def fail(error: BaseException) -> None:
            nonlocal settled
            if settled:
                return
            settled = True
            cleanup()
            if not future.done():
                future.set_exception(error)

        def abort_handler() -> None:
            fail(
                create_core_error(
                    CoreErrorType.TOOL_CANCELLED,
                    "Permission request cancelled",
                    {
                        "context": {
                            "requestId": key,
                            "toolCallId": request.tool_call_id,
                            "toolName": request.tool_name,
                        },
                        "recoverable": True,
                    },
                )
            )

        if options is not None and options.signal is not None and options.signal.aborted:
            abort_handler()
            return future

        if options is not None and options.timeout_ms is not None:
            timeout_ms = options.timeout_ms

            def _on_timeout() -> None:
                fail(
                    create_core_error(
                        CoreErrorType.PERMISSION_TIMEOUT,
                        f"Permission request timed out after {timeout_ms}ms",
                        {
                            "context": {
                                "requestId": key,
                                "timeoutMs": timeout_ms,
                                "toolCallId": request.tool_call_id,
                                "toolName": request.tool_name,
                            },
                            "recoverable": True,
                        },
                    )
                )

            timeout_handle = loop.call_later(timeout_ms / 1000, _on_timeout)

        if options is not None and options.signal is not None:
            options.signal.add_event_listener(abort_handler)
        self._pending[key] = _PendingBrokerRecord(request=request, resolve=settle, reject=fail)

        # TS: ``Promise.resolve(this.options.onRequest?.(request)).catch(fail);``
        on_request = self._options.on_request
        if on_request is not None:
            try:
                outcome = on_request(request)
            except BaseException as error:  # noqa: BLE001 — mirrors ``.catch(fail)``
                fail(error)
            else:
                if inspect.isawaitable(outcome):

                    async def _await_outcome(pending: Any = outcome) -> None:
                        try:
                            await pending
                        except BaseException as error:  # noqa: BLE001 — mirrors ``.catch(fail)``
                            fail(error)

                    loop.create_task(_await_outcome())

        return future

    def resolve_permission(self, request_id_or_tool_call_id: str, result: PermissionBrokerResult) -> bool:
        """Mirrors ``resolvePermission(requestIdOrToolCallId, result)``."""
        record = self._find_pending_record(request_id_or_tool_call_id)
        if record is None:
            return False

        record.resolve(result)
        return True

    def reject_permission(self, request_id_or_tool_call_id: str, error: BaseException) -> bool:
        """Mirrors ``rejectPermission(requestIdOrToolCallId, error)``."""
        record = self._find_pending_record(request_id_or_tool_call_id)
        if record is None:
            return False

        record.reject(error)
        return True

    def get_pending_request(self, request_id_or_tool_call_id: str) -> Optional[PermissionBrokerRequest]:
        """Mirrors ``getPendingRequest(requestIdOrToolCallId)``."""
        record = self._find_pending_record(request_id_or_tool_call_id)
        return record.request if record is not None else None

    def list_pending_requests(self) -> list[PermissionBrokerRequest]:
        """Mirrors ``listPendingRequests()``."""
        return [record.request for record in self._pending.values()]

    # PORT: TS ``private findPendingRecord`` → 下划线私有方法。
    def _find_pending_record(self, request_id_or_tool_call_id: str) -> Optional[_PendingBrokerRecord]:
        """Mirrors ``findPendingRecord(requestIdOrToolCallId)``."""
        direct = self._pending.get(request_id_or_tool_call_id)
        if direct is not None:
            return direct

        for record in self._pending.values():
            if record.request.tool_call_id == request_id_or_tool_call_id:
                return record
        return None

    def _rejected_future(self, error: BaseException) -> "asyncio.Future[PermissionBrokerResult]":
        """Mirrors ``Promise.reject(error)`` — 立刻可 await 且会抛出的已拒绝 Future。"""
        loop = asyncio.get_running_loop()
        future: "asyncio.Future[PermissionBrokerResult]" = loop.create_future()
        future.set_exception(error)
        return future


def create_deny_permission_broker() -> PermissionBrokerPort:
    """Mirrors ``createDenyPermissionBroker()``."""
    return DenyPermissionBroker()


def create_manual_permission_broker(
    options: Optional[ManualPermissionBrokerOptions] = None,
) -> ManualPermissionBroker:
    """Mirrors ``createManualPermissionBroker(options?)``."""
    return ManualPermissionBroker(options)
