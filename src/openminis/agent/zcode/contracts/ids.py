"""Shared identifier types used across the protocol.

Ported from: packages/contracts/src/interfaces/shared.ts
Original package: @zcode/contracts (interfaces)

# PORT: TS brands these as ``string & { readonly __brand: "SessionId" }`` to get
# nominal typing. Python uses ``NewType`` for the same purpose: the value is a
# plain ``str`` at runtime (so ids cross JSON boundaries untouched) but static
# checkers keep the identities apart.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import NewType, Optional

__all__ = [
    "SessionId",
    "TurnId",
    "EventId",
    "TraceId",
    "QueryId",
    "ToolCallId",
    "MessageId",
    "PartId",
    "InputHistoryId",
    "ProjectId",
    "WorkspaceId",
    "SubagentInteractionRequestOrigin",
    "create_session_id",
    "create_turn_id",
    "create_event_id",
    "create_trace_id",
    "create_query_id",
    "create_tool_call_id",
    "create_message_id",
    "create_part_id",
    "create_input_history_id",
    "create_project_id",
    "create_workspace_id",
]

SessionId = NewType("SessionId", str)
TurnId = NewType("TurnId", str)
EventId = NewType("EventId", str)
TraceId = NewType("TraceId", str)
QueryId = NewType("QueryId", str)
ToolCallId = NewType("ToolCallId", str)
MessageId = NewType("MessageId", str)
PartId = NewType("PartId", str)
InputHistoryId = NewType("InputHistoryId", str)
ProjectId = NewType("ProjectId", str)
WorkspaceId = NewType("WorkspaceId", str)


class SubagentInteractionRequestOrigin:
    """Origin descriptor for an interaction raised by a subagent.

    Ported from: ``SubagentInteractionRequestOrigin`` (shared.ts).
    """

    __slots__ = (
        "kind",
        "agent_id",
        "agent_type",
        "child_session_id",
        "child_turn_id",
        "description",
        "parent_session_id",
        "parent_tool_call_id",
        "parent_turn_id",
    )

    def __init__(
        self,
        *,
        agent_id: str,
        agent_type: str,
        child_session_id: "SessionId | str",
        parent_session_id: "SessionId | str",
        child_turn_id: Optional["TurnId | str"] = None,
        description: Optional[str] = None,
        parent_tool_call_id: Optional["ToolCallId | str"] = None,
        parent_turn_id: Optional["TurnId | str"] = None,
    ) -> None:
        # ``kind`` is the discriminant of the TS union; only one member exists today.
        self.kind = "subagent"
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.child_session_id = child_session_id
        self.child_turn_id = child_turn_id
        self.description = description
        self.parent_session_id = parent_session_id
        self.parent_tool_call_id = parent_tool_call_id
        self.parent_turn_id = parent_turn_id


# Alias kept from the TS source: ``export type InteractionRequestOrigin = SubagentInteractionRequestOrigin``.
InteractionRequestOrigin = SubagentInteractionRequestOrigin


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _new_uuid() -> str:
    """Mirrors ``crypto.randomUUID()``."""
    return str(uuid.uuid4())


def _create_sortable_id_segment() -> str:
    """Mirrors ``createSortableIdSegment()``.

    TS: ```${Date.now().toString(36)}_${crypto.randomUUID()}` ``.
    # PORT: ``int(time.time() * 1000)`` equals ``Date.now()``; base-36 encoding
    # is kept so message ids sort lexicographically the same way they do upstream.
    """

    def _to_base36(value: int) -> str:
        if value == 0:
            return "0"
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        out = []
        while value:
            value, rem = divmod(value, 36)
            out.append(digits[rem])
        return "".join(reversed(out))

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return f"{_to_base36(now_ms)}_{_new_uuid()}"


def create_session_id(id: Optional[str] = None) -> SessionId:
    return SessionId(f"sess_{id if id is not None else _new_uuid()}")


def create_turn_id(id: Optional[str] = None) -> TurnId:
    return TurnId(f"turn_{id if id is not None else _new_uuid()}")


def create_event_id(id: Optional[str] = None) -> EventId:
    return EventId(f"evt_{id if id is not None else _new_uuid()}")


def create_trace_id() -> TraceId:
    return TraceId(_new_uuid())


def create_query_id(id: Optional[str] = None) -> QueryId:
    return QueryId(f"query_{id if id is not None else _new_uuid()}")


def create_tool_call_id(id: Optional[str] = None) -> ToolCallId:
    return ToolCallId(f"tool_{id if id is not None else _new_uuid()}")


def create_message_id(id: Optional[str] = None) -> MessageId:
    return MessageId(f"msg_{id if id is not None else _create_sortable_id_segment()}")


def create_part_id(id: Optional[str] = None) -> PartId:
    return PartId(f"part_{id if id is not None else _create_sortable_id_segment()}")


def create_input_history_id(id: Optional[str] = None) -> InputHistoryId:
    return InputHistoryId(f"input_{id if id is not None else _create_sortable_id_segment()}")


def create_project_id(id: Optional[str] = None) -> ProjectId:
    return ProjectId(f"proj_{id if id is not None else _new_uuid()}")


def create_workspace_id(id: Optional[str] = None) -> WorkspaceId:
    return WorkspaceId(f"ws_{id if id is not None else _new_uuid()}")
