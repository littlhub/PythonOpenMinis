"""Message entity.

Ported from: src/android/app/src/main/java/com/openminis/app/data/db/MessageEntity.kt
Original package: com.openminis.app.data.db
"""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

__all__ = ["MessageEntity"]


class MessageEntity(Base):
    """``messages`` table.

    Foreign key to ``sessions(id)`` with ``ON DELETE CASCADE``, plus the
    ``(session_id, sort_order)`` index — both mirror the original ``@Entity``.
    """

    __tablename__ = "messages"
    __table_args__ = (
        Index("index_messages_session_id_sort_order", "session_id", "sort_order"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        "session_id", String, ForeignKey("sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String)
    parts_json: Mapped[str] = mapped_column("parts_json", String)
    created_at: Mapped[int] = mapped_column("created_at", BigInteger)  # milliseconds
    token_usage: Mapped[str | None] = mapped_column("token_usage", String, nullable=True)
    sort_order: Mapped[int] = mapped_column("sort_order", Integer)
    reasoning_content: Mapped[str | None] = mapped_column(
        "reasoning_content", String, nullable=True
    )

    # --- iOS parity fields -------------------------------------------------
    stream_interrupt_count: Mapped[int] = mapped_column(
        "stream_interrupt_count", Integer, default=0
    )
    updated_at: Mapped[int | None] = mapped_column(
        "updated_at", BigInteger, nullable=True
    )  # milliseconds

    # [T-error-persist-android] Terminal error sticker for an assistant turn
    # (mirrors iOS messages.error_info / ChatMessage.error). Null for normal
    # rows; device-local, never synced to iCloud.
    error_info: Mapped[str | None] = mapped_column("error_info", String, nullable=True)

    # [T-token-attribution-snapshot] Which model actually produced this
    # message, captured AT WRITE TIME as an immutable snapshot.
    #
    # Why a snapshot and not a reference: the Usage page used to derive the
    # model by joining ``sessions.model_id``, a single mutable column rewritten
    # on every model switch (including automatic failover). With no time
    # dimension in the join, a session's whole history was re-attributed to
    # whatever model it currently points at — switch to grok and 1B deepseek
    # tokens moved to grok; switch back and they moved back.
    #
    # These columns record what actually served the request, so past usage
    # stops depending on present configuration. ``model_display_name`` and
    # ``provider_type`` are stored too, not just the id, so a provider the user
    # later deletes still renders as "the model I used" instead of collapsing.
    model_id: Mapped[str | None] = mapped_column("model_id", String, nullable=True)
    model_display_name: Mapped[str | None] = mapped_column(
        "model_display_name", String, nullable=True
    )
    provider_type: Mapped[str | None] = mapped_column("provider_type", String, nullable=True)
    provider_instance_id: Mapped[str | None] = mapped_column(
        "provider_instance_id", String, nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MessageEntity id={self.id!r} role={self.role!r} order={self.sort_order}>"
