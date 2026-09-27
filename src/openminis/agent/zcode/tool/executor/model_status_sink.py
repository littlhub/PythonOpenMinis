"""Tool model status sink — default model-network-status outlet for in-tool model requests.

Ported from: packages/core/src/tool/executor/model-status-sink.ts
Original package: @zcode/core (tool/executor)

# PORT: ``@zcode/contracts`` 的 ``SessionEventType`` / ``Model`` / ``ModelNetworkStatusEvent`` /
# ``ModelStatusSink`` 及 ``../../runtime/methods/runtime-model`` 的 ``withModelInvocationContext`` /
# ``getCurrentModelInvocationContext`` 尚未移植；保留为 ``Any``。``ToolExecutionContext`` 来自
# ``..types``。emitEvent 经 ``context.emitEvent``。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ...contracts.events import SessionEventType  # type: ignore
from ...contracts.tracing import TraceContext  # type: ignore
from ..types import ToolExecutionContext

__all__ = ["create_tool_model_status_sink", "with_default_tool_model_status_sink"]


def create_tool_model_status_sink(
    context: Any,
) -> Any:
    """``context`` 是 ``Pick<ToolExecutionContext, "emitEvent" | "sessionId" | "turnId" | "traceId">``。"""
    if context.get("emitEvent") is None:
        return None

    def publish(status_event: Any) -> Any:
        return context["emitEvent"](
            {
                "id": uuid.uuid4().hex,
                "sessionId": context["sessionId"],
                "turnId": context.get("turnId"),
                "type": SessionEventType.MODEL_NETWORK_STATUS,
                "timestamp": datetime.now(timezone.utc),
                "traceId": context.get("traceId"),
                "sequenceNumber": 0,
                "payload": status_event,
            }
        )

    return {"publish": publish}


def with_default_tool_model_status_sink(model: Any, sink: Any) -> Any:
    """调用点未设 ``statusSink`` 时补默认出口；设了的保留（默认值不压调用层）。"""
    if model is None or sink is None:
        return model
    # PORT: 上游来源是 ``../../runtime/methods/runtime-model.js``（不是 zcode-cua）。
    from ...runtime.methods.runtime_model import (  # type: ignore
        with_model_invocation_context,
    )

    return with_model_invocation_context(model, _sink_apply(sink))


def _sink_apply(sink: Any) -> Any:
    def fn() -> Any:
        current = _get_current_invocation_context()
        return {} if current is not None and current.get("statusSink") is None else {"statusSink": sink}

    return fn


def _get_current_invocation_context() -> Any:
    # PORT: ``getCurrentModelInvocationContext()`` 来自 ``../../runtime/methods/runtime-model.js``；
    # runtime 尚未移植时不阻断调用，降级返回 None。
    try:
        from ...runtime.methods.runtime_model import (  # type: ignore
            get_current_model_invocation_context,
        )

        return get_current_model_invocation_context()
    except Exception:  # noqa: BLE001
        return None
