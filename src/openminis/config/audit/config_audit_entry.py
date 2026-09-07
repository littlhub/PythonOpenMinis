"""Audit log entry types.

Ported from: src/android/app/src/main/java/com/openminis/app/config/audit/ConfigAuditEntry.kt
Original package: com.openminis.app.config.audit
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConfigAuditActor(Enum):
    """Who initiated the change. Mirrors iOS `ConfigAuditActor`."""

    AGENT = "agent"
    USER = "user"
    AGENT_REVERT = "agent-revert"
    USER_REVERT = "user-revert"

    @classmethod
    def from_raw(cls, s: str | None) -> "ConfigAuditActor":
        for member in cls:
            if member.value == s:
                return member
        return cls.AGENT


class ConfigAuditStatus(Enum):
    """Final disposition of a config-change attempt. Mirrors iOS."""

    APPLIED = "applied"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    REVERTED = "reverted"

    @classmethod
    def from_raw(cls, s: str | None) -> "ConfigAuditStatus | None":
        for member in cls:
            if member.value == s:
                return member
        return None


@dataclass(slots=True)
class ConfigAuditEntry:
    """One row in the rolling audit log.

    Persisted in `minis-config-audit.db`, capped at 1000 most-recent rows.
    """

    id: str
    # Epoch millis (Java convention; iOS uses TimeInterval since 1970).
    at: int
    actor: ConfigAuditActor
    # Active session id at the time of the call. None for user-initiated UI.
    session_id: str | None
    # Top-level scope, e.g. `appearance`, `models`. Drives `--scope` filter.
    scope: str
    # Dot path of the affected field.
    key: str
    old_value_json: str
    new_value_json: str
    # Epoch millis when the user resolved the confirm dialog, or None.
    confirmed_at: int | None
    status: ConfigAuditStatus
    # If this entry IS a revert, the audit id it undid.
    revert_of: str | None
    # Caller-supplied `--caption "..."` text. Optional.
    caption: str | None
