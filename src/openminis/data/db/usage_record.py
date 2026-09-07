"""Usage aggregation row.

Ported from: src/android/app/src/main/java/com/openminis/app/data/db/UsageRecord.kt
Original package: com.openminis.app.data.db
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["UsageRecord"]


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """One billable message, joined with the model that produced it.

    ``model_id`` is nullable on purpose: the backing query uses a LEFT JOIN so
    a message whose ``sessions`` row is gone (orphaned by a failed sync or
    migration) still contributes to the totals. Those rows surface as NULL and
    the caller groups them under "Unknown" rather than discarding them.

    ``has_snapshot`` distinguishes "measured" from "estimated": when the
    per-message attribution snapshot is missing, ``model_id`` falls back to the
    session's mutable ``model_id`` column, which carries no history.
    """

    model_id: str | None
    model_display_name: str | None
    provider_type: str | None
    has_snapshot: bool
    token_usage: str
    created_at: int  # milliseconds
    session_id: str

    @property
    def is_estimated(self) -> bool:
        """True when attribution came from the session fallback, not a snapshot."""
        return not self.has_snapshot
