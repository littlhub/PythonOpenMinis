"""Core errors — error types for the agent loop.

Ported from: packages/contracts/src/errors/index.ts
Original package: @zcode/contracts (errors)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

__all__ = [
    "CoreErrorType",
    "CoreError",
    "is_core_error",
    "is_retryable",
    "is_recoverable",
    "create_core_error",
    "is_automation_create_limit_error",
    "session_not_found",
    "invalid_turn_phase",
    "tool_not_found",
    "tool_execution_failed",
    "permission_denied",
]


class CoreErrorType(str, Enum):
    """Ported from the ``CoreErrorType`` const object (``as const`` → ``enum.Enum``).

    # PORT: values are kept as the original snake_case strings so they can cross
    # the wire unchanged.
    """

    # Session errors
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_ALREADY_EXISTS = "session_already_exists"
    SESSION_CORRUPTED = "session_corrupted"

    # Turn errors
    TURN_NOT_FOUND = "turn_not_found"
    TURN_IN_PROGRESS = "turn_in_progress"
    INVALID_TURN_PHASE = "invalid_turn_phase"
    TURN_CANCELLED = "turn_cancelled"

    # Model errors
    MODEL_ERROR = "model_error"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_RATE_LIMITED = "model_rate_limited"
    MODEL_CONTEXT_EXCEEDED = "model_context_exceeded"

    # Tool errors
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_CANCELLED = "tool_cancelled"
    TOOL_MAX_CALLS = "tool_max_calls"
    INVALID_INPUT = "invalid_input"

    # Permission errors
    PERMISSION_DENIED = "permission_denied"
    PERMISSION_ESCALATION = "permission_escalation"
    PERMISSION_TIMEOUT = "permission_timeout"

    # State errors
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    EVENT_OUT_OF_ORDER = "event_out_of_order"
    PROJECTION_CORRUPTED = "projection_corrupted"

    # System errors
    STORAGE_ERROR = "storage_error"
    CONFIGURATION_ERROR = "configuration_error"
    CANCELLED = "cancelled"
    UNKNOWN_ERROR = "unknown_error"


class CoreError(Exception):
    """Ports the ``CoreError extends Error`` interface.

    # PORT: TS augments a plain ``Error`` with extra fields. Python subclasses
    # ``Exception`` and sets the same fields; ``message`` is exposed as a property
    # so call sites read identically to the original (``err.message``).
    """

    def __init__(
        self,
        type: CoreErrorType,
        message: str,
        *,
        cause: Optional[BaseException] = None,
        context: Optional[dict[str, Any]] = None,
        recoverable: bool = False,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.name = "CoreError"
        self.type = type
        # PORT: upstream is ``type.toUpperCase().replace(/_/g, "_")`` — the replace is a
        # no-op (underscore → underscore). Kept verbatim so the code string matches.
        self.code = type.value.upper().replace("_", "_")
        self.cause = cause
        self.context = context
        self.recoverable = recoverable
        self.retryable = retryable
        self.timestamp = datetime.now(timezone.utc)

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_core_error(
    type: CoreErrorType,
    message: str,
    options: Optional[dict[str, Any]] = None,
) -> CoreError:
    """Mirrors ``createCoreError(type, message, options?)``.

    ``options`` keys: ``cause``, ``context``, ``recoverable``, ``retryable``.
    """
    options = options or {}
    return CoreError(
        type,
        message,
        cause=options.get("cause"),
        context=options.get("context"),
        recoverable=options.get("recoverable", False),
        retryable=options.get("retryable", False),
    )


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

def is_core_error(error: object) -> bool:
    """Mirrors ``isCoreError(error)``.

    # PORT: TS checks ``error instanceof Error && "type" in error && "code" in error``.
    """
    return isinstance(error, BaseException) and hasattr(error, "type") and hasattr(error, "code")


def is_retryable(error: CoreError) -> bool:
    return error.retryable


def is_recoverable(error: CoreError) -> bool:
    return error.recoverable


# PORT: office automation (create-limit) error detection. The automation system
# is out of scope for the coding-only port, so this always reports False here.
def is_automation_create_limit_error(error: object) -> bool:
    """Return True if *error* is an automation create-limit error.

    PORT: mirrors ``isAutomationCreateLimitError``. In the coding-only port there
    is no automation subsystem, so this conservatively returns ``False``.
    """
    if not isinstance(error, dict):
        return False
    etype = error.get("type")
    if isinstance(etype, str) and etype.upper().endswith("AUTOMATION_CREATE_LIMIT"):
        return True
    return False


# ---------------------------------------------------------------------------
# Specific error creators
# ---------------------------------------------------------------------------

def session_not_found(session_id: str) -> CoreError:
    return create_core_error(
        CoreErrorType.SESSION_NOT_FOUND,
        f"Session not found: {session_id}",
        {"context": {"sessionId": session_id}, "recoverable": True},
    )


def invalid_turn_phase(current: str, expected: list[str]) -> CoreError:
    return create_core_error(
        CoreErrorType.INVALID_TURN_PHASE,
        f"Invalid turn phase: {current}",
        {"context": {"current": current, "expected": expected}, "recoverable": True},
    )


def tool_not_found(tool_name: str) -> CoreError:
    return create_core_error(
        CoreErrorType.TOOL_NOT_FOUND,
        f"Tool not found: {tool_name}",
        {"context": {"toolName": tool_name}, "recoverable": False},
    )


def tool_execution_failed(tool_name: str, cause: Optional[BaseException] = None) -> CoreError:
    return create_core_error(
        CoreErrorType.TOOL_EXECUTION_FAILED,
        f"Tool execution failed: {tool_name}",
        {"cause": cause, "context": {"toolName": tool_name}, "recoverable": True, "retryable": True},
    )


def permission_denied(tool_name: str, reason: Optional[str] = None) -> CoreError:
    return create_core_error(
        CoreErrorType.PERMISSION_DENIED,
        f"Permission denied: {tool_name}",
        {"context": {"toolName": tool_name, "reason": reason}, "recoverable": True, "retryable": False},
    )
