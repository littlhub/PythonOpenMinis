"""Turn Machine — state machine for turn lifecycle.

Ported from: packages/core/src/agent/turn-machine.ts
Original package: @zcode/core (agent)
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, TypedDict

from ..contracts.errors import CoreErrorType, create_core_error
from ..contracts.ids import SessionId, ToolCallId, TraceId, TurnId, create_turn_id
from ..contracts.model import ModelMessageContent, model_message_content_to_text
from .turn_state import (
    ModelMessage,
    ModelRequestState,
    PendingTurnInput,
    PermissionDecision,
    PermissionRequestState,
    PermissionResultState,
    ToolCall,
    ToolCallState,
    ToolResultState,
    ToolScheduleState,
    TurnErrorState,
    TurnResultType,
    TurnState,
)
from .turn_state import TurnPhase as Phase
from .turn_state import can_transition_to, create_turn_state, is_terminal_phase

__all__ = [
    "ToolCompletionResult",
    "TurnMachine",
    "TurnMachineImpl",
]


class ToolCompletionResult(TypedDict):
    """Mirrors the inline TS shape ``{ success: boolean; content: ModelMessageContent }``."""

    success: bool
    content: ModelMessageContent


# -----------------------------------------------
# Turn Machine
# -----------------------------------------------


class TurnMachine(Protocol):
    """Mirrors ``interface TurnMachine``."""

    state: TurnState

    def start(self) -> TurnState: ...

    def start_model_request(self, model: str, messages: list[ModelMessage]) -> TurnState: ...

    def receive_model_response(self, content: str) -> TurnState: ...

    def add_streaming_content(self, content: str) -> TurnState: ...

    def schedule_tools(
        self, tool_calls: list[ToolCall], schedule: ToolScheduleState
    ) -> TurnState: ...

    def start_tool_execution(self) -> TurnState: ...

    def complete_tool(
        self, tool_call_id: ToolCallId, result: ToolCompletionResult
    ) -> TurnState: ...

    def queue_pending_input(self, input: PendingTurnInput) -> TurnState: ...

    def drain_pending_inputs(self) -> dict[str, Any]: ...

    def request_permission(self, request: PermissionRequestState) -> TurnState: ...

    def resolve_permission(
        self,
        tool_call_id: ToolCallId,
        decision: PermissionDecision,
        modified_input: Any = None,
    ) -> TurnState: ...

    def aggregate_results(self) -> TurnState: ...

    def complete(self, response: str, result_type: TurnResultType = "success") -> TurnState: ...

    def fail(self, error: TurnErrorState) -> TurnState: ...

    def get_next_phase(self) -> Phase: ...

    def is_complete(self) -> bool: ...


class TurnMachineImpl(TurnMachine):
    """Mirrors ``class TurnMachineImpl implements TurnMachine``."""

    def __init__(self, state: TurnState) -> None:
        self.state = state

    @staticmethod
    def create(
        session_id: SessionId,
        turn_number: int,
        input: str,
        trace_id: Optional[TraceId] = None,
        turn_id: Optional[TurnId] = None,
    ) -> "TurnMachineImpl":
        """Mirrors ``static create(...)``."""
        state = create_turn_state(
            turn_id if turn_id is not None else create_turn_id(),
            session_id,
            turn_number,
            # PORT: ``crypto.randomUUID() as TraceId`` → ``uuid.uuid4()``.
            trace_id if trace_id is not None else TraceId(str(uuid.uuid4())),
            input,
        )
        return TurnMachineImpl(state)

    def _transition(self, phase: Phase) -> TurnState:
        """Mirrors ``private transition(phase)``.

        # PORT: TS ``{ ...this.state, phase }`` is a shallow copy; ``replace`` is the
        # dataclass equivalent. ``this.state`` is intentionally never reassigned, as
        # in the original.
        """
        if not can_transition_to(self.state.phase, phase):
            raise create_core_error(
                CoreErrorType.INVALID_TURN_PHASE,
                f"Cannot transition from {self.state.phase.value} to {phase.value}",
                {
                    "context": {"current": self.state.phase.value, "target": phase.value},
                    "recoverable": True,
                },
            )
        return replace(self.state, phase=phase)

    def start(self) -> TurnState:
        return self._transition(Phase.PROCESSING_INPUT)

    def start_model_request(self, model: str, messages: list[ModelMessage]) -> TurnState:
        if self.state.phase != Phase.PROCESSING_INPUT and self.state.phase != Phase.AGGREGATING_RESULTS:
            raise create_core_error(
                CoreErrorType.INVALID_TURN_PHASE,
                "Must be in ProcessingInput or AggregatingResults phase",
                {
                    "context": {"current": self.state.phase.value},
                    "recoverable": True,
                },
            )

        state = self._transition(Phase.AWAITING_MODEL_RESPONSE)
        return replace(
            state,
            model_request=ModelRequestState(
                model=model,
                messages=messages,
            ),
        )

    def receive_model_response(self, content: str) -> TurnState:
        state = self._transition(Phase.STREAMING)
        return replace(
            state,
            streaming_content=state.streaming_content + content,
        )

    def add_streaming_content(self, content: str) -> TurnState:
        state = self._transition(Phase.STREAMING)
        return replace(
            state,
            streaming_content=state.streaming_content + content,
        )

    def schedule_tools(self, tool_calls: list[ToolCall], schedule: ToolScheduleState) -> TurnState:
        state = self._transition(Phase.SCHEDULING_TOOLS)
        return replace(
            state,
            tool_calls=[
                ToolCallState(
                    id=ToolCallId(tc.id),
                    name=tc.name,
                    input=tc.input,
                    status="scheduled",
                    scheduled_at=datetime.now(timezone.utc),
                )
                for tc in tool_calls
            ],
            scheduled_tools=schedule,
        )

    def start_tool_execution(self) -> TurnState:
        needs_permission = any(tc.status == "waiting_permission" for tc in self.state.tool_calls)
        next_phase = Phase.AWAITING_PERMISSION if needs_permission else Phase.EXECUTING_TOOLS

        state = self._transition(next_phase)
        return replace(
            state,
            tool_calls=[
                replace(
                    tc,
                    status=(
                        tc.status if tc.status == "waiting_permission" else "running"
                    ),
                    started_at=(
                        tc.started_at
                        if tc.status == "waiting_permission"
                        else datetime.now(timezone.utc)
                    ),
                )
                for tc in state.tool_calls
            ],
        )

    def complete_tool(self, tool_call_id: ToolCallId, result: ToolCompletionResult) -> TurnState:
        error_message = (
            None if result["success"] else model_message_content_to_text(result["content"])
        )
        updated_tool_calls = [
            (
                replace(
                    tc,
                    status="completed" if result["success"] else "failed",
                    completed_at=datetime.now(timezone.utc),
                    result=ToolResultState(
                        success=result["success"],
                        content=result["content"],
                    ),
                )
                if tc.id == tool_call_id
                else tc
            )
            for tc in self.state.tool_calls
        ]

        return replace(
            self.state,
            tool_calls=updated_tool_calls,
            tool_results=[
                *self.state.tool_results,
                ToolResultState(
                    success=result["success"],
                    content=result["content"],
                    error=(
                        None
                        if result["success"]
                        else TurnErrorState(
                            type="tool_error",
                            message=error_message if error_message is not None else "",
                            recoverable=True,
                        )
                    ),
                ),
            ],
        )

    def queue_pending_input(self, input: PendingTurnInput) -> TurnState:
        return replace(
            self.state,
            pending_inputs=[*self.state.pending_inputs, input],
        )

    def drain_pending_inputs(self) -> dict[str, Any]:
        return {
            "inputs": self.state.pending_inputs,
            "state": replace(
                self.state,
                pending_inputs=[],
            ),
        }

    def request_permission(self, request: PermissionRequestState) -> TurnState:
        state = self._transition(Phase.AWAITING_PERMISSION)
        return replace(
            state,
            tool_calls=[
                (
                    replace(tc, status="waiting_permission")
                    if tc.id == request.tool_call_id
                    else tc
                )
                for tc in state.tool_calls
            ],
            pending_permissions=[*state.pending_permissions, request],
        )

    def resolve_permission(
        self,
        tool_call_id: ToolCallId,
        decision: PermissionDecision,
        modified_input: Any = None,
    ) -> TurnState:
        updated_tool_calls = [
            (
                replace(
                    tc,
                    status="permission_denied" if decision == "deny" else tc.status,
                    input=modified_input if modified_input is not None else tc.input,
                )
                if tc.id == tool_call_id
                else tc
            )
            for tc in self.state.tool_calls
        ]

        return replace(
            self.state,
            tool_calls=updated_tool_calls,
            pending_permissions=[
                p for p in self.state.pending_permissions if p.tool_call_id != tool_call_id
            ],
            resolved_permissions=[
                *self.state.resolved_permissions,
                PermissionResultState(
                    tool_call_id=tool_call_id,
                    decision=decision,
                    modified_input=modified_input,
                    resolved_at=datetime.now(timezone.utc),
                ),
            ],
        )

    def aggregate_results(self) -> TurnState:
        return self._transition(Phase.AGGREGATING_RESULTS)

    def complete(self, response: str, result_type: TurnResultType = "success") -> TurnState:
        state = self._transition(Phase.COMPLETING)
        return replace(
            state,
            final_response=response,
            result_type=result_type,
            completed_at=datetime.now(timezone.utc),
        )

    def fail(self, error: TurnErrorState) -> TurnState:
        # PORT: TS assigns ``phase`` directly here instead of going through
        # ``transition``, so an otherwise-illegal transition still "succeeds".
        return replace(
            self.state,
            phase=Phase.ERROR,
            error=error,
            completed_at=datetime.now(timezone.utc),
        )

    def get_next_phase(self) -> Phase:
        phase = self.state.phase
        tool_calls = self.state.tool_calls
        streaming_content = self.state.streaming_content

        if phase == Phase.STREAMING and len(tool_calls) > 0:
            return Phase.SCHEDULING_TOOLS

        if phase == Phase.STREAMING and len(tool_calls) == 0 and streaming_content:
            return Phase.COMPLETING

        if phase == Phase.EXECUTING_TOOLS:
            pending_tools = [
                tc for tc in tool_calls if tc.status == "running" or tc.status == "waiting_permission"
            ]
            if len(pending_tools) == 0:
                return Phase.AGGREGATING_RESULTS

        if phase == Phase.AGGREGATING_RESULTS:
            failed_tools = [
                tc for tc in tool_calls if tc.status == "failed" or tc.status == "permission_denied"
            ]
            if len(failed_tools) > 0:
                return Phase.COMPLETING
            return Phase.AWAITING_MODEL_RESPONSE

        return phase

    def is_complete(self) -> bool:
        return is_terminal_phase(self.state.phase)
