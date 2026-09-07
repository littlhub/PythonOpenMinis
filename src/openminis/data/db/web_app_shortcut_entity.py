"""Web app shortcut entity.

Ported from: src/android/app/src/main/java/com/openminis/app/data/db/WebAppShortcutEntity.kt
Original package: com.openminis.app.data.db

The Kotlin source lives alongside the other entities in ``data/db``; the table
is created by MIGRATION_8_9 (``pwa_shortcuts`` -> ``webapp_shortcuts`` rename).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

__all__ = ["WebAppShortcutEntity"]


class WebAppShortcutEntity(Base):
    """``webapp_shortcuts`` table — home-screen web app pins.

    Renamed from ``pwa_shortcuts`` in MIGRATION_8_9; column set is identical
    to the original ``CREATE TABLE`` in MIGRATION_6_7.
    """

    __tablename__ = "webapp_shortcuts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    html_path: Mapped[str] = mapped_column("html_path", String)
    path_scope: Mapped[str] = mapped_column("path_scope", String)
    scope_context: Mapped[str | None] = mapped_column("scope_context", String, nullable=True)
    title: Mapped[str] = mapped_column(String)
    icon_ref: Mapped[str] = mapped_column("icon_ref", String)
    icon_cache_path: Mapped[str | None] = mapped_column(
        "icon_cache_path", String, nullable=True
    )
    created_at: Mapped[int] = mapped_column("created_at", BigInteger)  # milliseconds
    source_session_id: Mapped[str | None] = mapped_column(
        "source_session_id", String, nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<WebAppShortcutEntity id={self.id!r} title={self.title!r}>"
