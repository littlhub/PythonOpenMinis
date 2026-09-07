"""Session group ("folder") entity.

Ported from: src/android/app/src/main/java/com/openminis/app/data/db/FolderEntity.kt
Original package: com.openminis.app.data.db
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

__all__ = ["FolderEntity"]


class FolderEntity(Base):
    """``folders`` table.

    [T-android-session-grouping] A session group. Code says "Folder", the UI
    says "Group" — the same deliberate split iOS uses, because ``ModelGroup``
    (LLM fallback routing) already owns the word "Group" at symbol level.

    Mirrors the iOS ``folders`` table (ChatStore.swift:795) column-for-column
    so a future sync layer can map the two without a translation step.

    Why ``name`` is NOT unique
    --------------------------
    Two devices can each create "Work" offline; both are kept, as two rows with
    different UUIDs. Keying on the name instead would turn a rename from a
    one-field edit into an identity change — delete the old key, create a new
    one, migrate every member — which tears across devices: A renames while B
    files under the old name, and B's sessions end up pointing at a key that no
    longer exists. A locally-generated UUID needs no cross-device coordination,
    which is exactly the property offline creation requires.

    Consequence: every name-based lookup must tolerate multiple matches.
    """

    __tablename__ = "folders"

    ORIGIN_MANUAL = "manual"
    ORIGIN_AI = "ai"

    #: iOS caps the description at 100 chars; keep the two in step.
    DESC_MAX_CHARS = 100

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)

    #: Optional icon token. Null = use the composed top-3-category glyphs.
    icon: Mapped[str | None] = mapped_column(String, nullable=True)

    #: Optional theme color token.
    color: Mapped[str | None] = mapped_column(String, nullable=True)

    #: ``"manual"`` | ``"ai"``. Provenance only — nothing branches on it. Kept
    #: so an AI-suggested group stays distinguishable for future UI/telemetry.
    origin: Mapped[str] = mapped_column(String, default=ORIGIN_MANUAL)

    #: Reserved for drag-reorder. Always 0 today; no code path writes it. Kept
    #: as a column so the record shape matches iOS and a later reorder feature
    #: needs no migration.
    sort_index: Mapped[int] = mapped_column("sort_index", Integer, default=0)

    #: Non-null = this group floats above unpinned groups. Milliseconds.
    pinned_at: Mapped[int | None] = mapped_column("pinned_at", BigInteger, nullable=True)

    #: One-sentence description, capped at :attr:`DESC_MAX_CHARS`. Never
    #: rendered in the session list — it is the group picker's row subtitle,
    #: and context for future auto-grouping.
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[int] = mapped_column("created_at", BigInteger)
    #: Tracks RECORD edits (rename / pin / icon) — NOT member activity. A
    #: session moving in or out of the group must not touch it.
    updated_at: Mapped[int] = mapped_column("updated_at", BigInteger)

    @property
    def is_pinned(self) -> bool:
        """Kotlin: ``pinnedAt != null``."""
        return self.pinned_at is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FolderEntity id={self.id!r} name={self.name!r}>"
