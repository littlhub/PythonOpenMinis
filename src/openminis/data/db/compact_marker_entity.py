"""Compaction marker entity.

Ported from: src/android/app/src/main/java/com/openminis/app/data/db/CompactMarkerEntity.kt
Original package: com.openminis.app.data.db
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

__all__ = ["CompactMarkerEntity"]


class CompactMarkerEntity(Base):
    """Mirrors iOS ``compact_markers`` table.

    Stores LLM-generated summaries of compacted message ranges.
    Used for context window management (compact old messages into a summary).
    """

    __tablename__ = "compact_markers"
    __table_args__ = (
        Index("index_compact_markers_session_id", "session_id"),
        Index("index_compact_markers_first_kept_message_id", "first_kept_message_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        "session_id", String, ForeignKey("sessions.id", ondelete="CASCADE")
    )
    summary: Mapped[str] = mapped_column(String)
    first_kept_sort_order: Mapped[int] = mapped_column("first_kept_sort_order", Integer)
    compacted_count: Mapped[int] = mapped_column("compacted_count", Integer)
    created_at: Mapped[int] = mapped_column("created_at", BigInteger)  # milliseconds
    ui_boundary_sort_order: Mapped[int | None] = mapped_column(
        "ui_boundary_sort_order", Integer, nullable=True
    )
    boundary_message_id: Mapped[str | None] = mapped_column(
        "boundary_message_id", String, nullable=True
    )

    # Phase-A (iOS parity): id-first boundary fields. Preferred over sort_order
    # when present because iCloud-merged messages can produce duplicate
    # sort_order values — message ids stay stable. Sort-order fields above
    # are retained for legacy rows and fallback when ids are missing.
    first_kept_message_id: Mapped[str | None] = mapped_column(
        "first_kept_message_id", String, nullable=True
    )
    last_compacted_message_id: Mapped[str | None] = mapped_column(
        "last_compacted_message_id", String, nullable=True
    )

    #: Marker schema version. 1 = legacy multi-field model (firstKept /
    #: boundary / sortOrder fallback chain). 2 = simplified id-only model:
    #: ``last_compacted_message_id`` is the authoritative anchor — everything
    #: from session start (or the previous marker's anchor + 1) up to and
    #: including this id is folded into ``summary``; everything after is the
    #: active region. Older v1 markers keep working through the legacy
    #: resolution path in ChatViewModel.effectiveAgentHistory().
    #:
    #: Defaults to 1 so rows from prior schema versions read as v1 without
    #: any backfill — matches the SQL ``DEFAULT 1`` set by MIGRATION_7_8.
    version: Mapped[int] = mapped_column(Integer, default=1)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CompactMarkerEntity id={self.id!r} session={self.session_id!r}>"
