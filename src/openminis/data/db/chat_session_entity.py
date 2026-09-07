"""Chat session entity.

Ported from: src/android/app/src/main/java/com/openminis/app/data/db/ChatSessionEntity.kt
Original package: com.openminis.app.data.db
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

__all__ = ["ChatSessionEntity"]


class ChatSessionEntity(Base):
    """``sessions`` table.

    [T-android-session-grouping] The ``folder_id`` index is declared here so the
    entity and MIGRATION_10_11 agree — Room validates the live schema against
    the entity on open, and an index present in one but not the other aborts
    startup with an IllegalStateException.

    Non-unique on purpose: many sessions share one group.
    """

    __tablename__ = "sessions"
    __table_args__ = (Index("index_sessions_folder_id", "folder_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    model_id: Mapped[str] = mapped_column("model_id", String)
    created_at: Mapped[int] = mapped_column("created_at", BigInteger)  # milliseconds
    updated_at: Mapped[int] = mapped_column("updated_at", BigInteger)  # milliseconds
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    last_message: Mapped[str | None] = mapped_column(String, nullable=True)
    model_binding: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- iOS parity fields -------------------------------------------------
    #: e.g. "shortcut", "share"
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    #: 1=on, 0=off
    memory_enabled: Mapped[int] = mapped_column(Integer, default=1)
    #: milliseconds, null=not pinned
    pinned_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: message edit counter
    edit_count: Mapped[int] = mapped_column(Integer, default=0)

    #: T239: per-session thinking-mode override. None = unset (use the
    #: current model/group default — i.e. existing pre-T239 behaviour, which
    #: is OFF on Android today). Non-null is one of ThinkingLevel.name
    #: ("OFF"/"LOW"/"MEDIUM"/"HIGH"/"XHIGH") and represents an explicit user
    #: choice that survives cold-start.
    thinking_override: Mapped[str | None] = mapped_column(String, nullable=True)

    # [T-android-session-grouping] Group membership. NULL = ungrouped.
    #
    # Deliberately NOT a declared foreign key. A folder_id pointing at a group
    # that does not exist locally is a legitimate transient state, not
    # corruption: a future sync could deliver the session before its group, and
    # a group dissolved on another device leaves references behind until that
    # change arrives. Such orphans render as ungrouped (see
    # SessionListViewModel's grouping pass) instead of failing a constraint or
    # making the session vanish. Same rule as iOS (ChatStore.swift:610).
    #
    # NOTE for anyone adding list diffing: this field MUST participate in
    # equality. Moving a session between groups changes nothing else — not even
    # ``updated_at``, by design — so a differ that ignores it keeps drawing the
    # row in its old section.
    #
    # PORT: SQLAlchemy would happily enforce an FK here; we intentionally omit
    # it to preserve the original's "orphans are legal" semantics.
    folder_id: Mapped[str | None] = mapped_column(String, nullable=True)

    @property
    def is_pinned(self) -> bool:
        """Kotlin: ``pinnedAt != null``."""
        return self.pinned_at is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ChatSessionEntity id={self.id!r} title={self.title!r}>"
