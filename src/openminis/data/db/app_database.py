"""App database — engine, session factory and the Room migration chain.

Ported from: src/android/app/src/main/java/com/openminis/app/data/db/AppDatabase.kt
Original package: com.openminis.app.data.db

Room's ``@Database(entities=[...], version=12, exportSchema=true)`` + the
``Migration`` objects become: SQLAlchemy metadata (via :class:`Base`) plus an
explicit migration registry keyed ``(from, to)``.

Schema version is stored in SQLite's ``PRAGMA user_version``, which is exactly
where Room keeps it, so a database file written by the Android app carries a
version this port understands.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from ...core.context import app_context
from ...core.logging import get_logger
from .base import Base
from .chat_session_entity import ChatSessionEntity
from .compact_marker_entity import CompactMarkerEntity
from .folder_entity import FolderEntity
from .message_entity import MessageEntity
from .web_app_shortcut_entity import WebAppShortcutEntity

logger = get_logger(__name__)

__all__ = ["AppDatabase", "get_database", "MIGRATIONS", "DATABASE_VERSION"]

#: Mirrors ``@Database(version = 12)``.
DATABASE_VERSION = 12
DATABASE_NAME = "minis.db"

# ---------------------------------------------------------------------------
# migrations — Kotlin `Migration(from, to) { db.execSQL(...) }` objects.
# SQL is copied verbatim from the original.
# ---------------------------------------------------------------------------
_MIGRATIONS: dict[tuple[int, int], list[str]] = {
    (1, 2): [
        "ALTER TABLE sessions ADD COLUMN last_message TEXT",
        "ALTER TABLE sessions ADD COLUMN model_binding TEXT",
    ],
    (2, 3): [
        "ALTER TABLE messages ADD COLUMN reasoning_content TEXT",
    ],
    # compact_markers: add Phase-A id-first boundary columns. The legacy
    # sort_order columns stay for backfill; when both are present the
    # id-first fields win on lookup (see ChatDao.latestCompactMarker).
    (4, 5): [
        "ALTER TABLE compact_markers ADD COLUMN first_kept_message_id TEXT",
        "ALTER TABLE compact_markers ADD COLUMN last_compacted_message_id TEXT",
        "CREATE INDEX IF NOT EXISTS index_compact_markers_first_kept_message_id ON compact_markers(first_kept_message_id)",
    ],
    # T239: per-session thinking-mode override. Nullable so existing
    # sessions transparently keep "unset" semantics; only sessions where
    # the user explicitly chooses a level start storing a non-null value.
    (5, 6): [
        "ALTER TABLE sessions ADD COLUMN thinking_override TEXT",
    ],
    # T-pwa-1: pwa_shortcuts table backs the home-screen PWA pinning
    # flow. Pure additive migration — no existing entity is modified
    # and no data is rewritten.
    #
    # Superseded by MIGRATION_8_9 below (Pwa -> WebApp rename); kept
    # here so users who already migrated from <=6 land on a
    # consistent state before the rename runs.
    (6, 7): [
        """
        CREATE TABLE IF NOT EXISTS pwa_shortcuts (
            id TEXT NOT NULL PRIMARY KEY,
            html_path TEXT NOT NULL,
            path_scope TEXT NOT NULL,
            scope_context TEXT,
            title TEXT NOT NULL,
            icon_ref TEXT NOT NULL,
            icon_cache_path TEXT,
            created_at INTEGER NOT NULL,
            source_session_id TEXT
        )
        """
    ],
    # compact_markers: add `version` column for marker schema versioning.
    # Mirrors iOS Phase v2 — version=1 = legacy multi-field model,
    # version=2 = simplified id-only anchor model. Existing rows default
    # to 1 so legacy resolution code keeps running for them.
    (7, 8): [
        "ALTER TABLE compact_markers ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
    ],
    # Pwa -> WebApp rename: copy every row from `pwa_shortcuts` into a
    # new `webapp_shortcuts` table with identical schema, then drop
    # the old table. Row contents (UUIDs, html paths, icon refs) are
    # preserved verbatim — only the table name changes — so existing
    # in-app shortcut lists keep showing the same entries.
    #
    # Note: pinned launcher icons created before this rename still
    # carry the old `ACTION_OPEN_PWA` intent action and will be dead
    # after the upgrade (manifest no longer registers it). The user
    # has to re-pin from inside the app. Per
    # `feedback_no_destructive_git` we do NOT silently delete data —
    # the DB row stays, only the launcher-side icon dies.
    (8, 9): [
        """
        CREATE TABLE IF NOT EXISTS webapp_shortcuts (
            id TEXT NOT NULL PRIMARY KEY,
            html_path TEXT NOT NULL,
            path_scope TEXT NOT NULL,
            scope_context TEXT,
            title TEXT NOT NULL,
            icon_ref TEXT NOT NULL,
            icon_cache_path TEXT,
            created_at INTEGER NOT NULL,
            source_session_id TEXT
        )
        """,
        """
        INSERT INTO webapp_shortcuts (
            id, html_path, path_scope, scope_context, title,
            icon_ref, icon_cache_path, created_at, source_session_id
        )
        SELECT
            id, html_path, path_scope, scope_context, title,
            icon_ref, icon_cache_path, created_at, source_session_id
        FROM pwa_shortcuts
        """,
        "DROP TABLE IF EXISTS pwa_shortcuts",
    ],
    # [T-error-persist-android] messages.error_info — persist the terminal
    # error sticker on an assistant turn so the inline error survives a
    # session reload (mirrors iOS messages.error_info). Pure additive,
    # nullable column; existing rows read back NULL (= no error). No data
    # rewrite.
    (9, 10): [
        "ALTER TABLE messages ADD COLUMN error_info TEXT",
    ],
    # [T-android-session-grouping] Session groups. Adds the `folders` table
    # and `sessions.folder_id`.
    #
    # Purely additive: existing sessions read back `folder_id = NULL`
    # (= ungrouped), which is exactly the pre-migration behaviour, so no
    # data is rewritten and a downgrade loses only the grouping.
    #
    # `folder_id` carries NO foreign key on purpose — an id pointing at a
    # group that is not present locally must render as ungrouped rather
    # than fail a constraint (see ChatSessionEntity.folderId). The index is
    # plain and non-unique: many sessions share one group.
    (10, 11): [
        """
        CREATE TABLE IF NOT EXISTS folders (
            id TEXT NOT NULL PRIMARY KEY,
            name TEXT NOT NULL,
            icon TEXT,
            color TEXT,
            origin TEXT NOT NULL DEFAULT 'manual',
            sort_index INTEGER NOT NULL DEFAULT 0,
            pinned_at INTEGER,
            description TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """,
        "ALTER TABLE sessions ADD COLUMN folder_id TEXT",
        "CREATE INDEX IF NOT EXISTS index_sessions_folder_id ON sessions(folder_id)",
    ],
    # [T-token-attribution-snapshot] Per-message model attribution.
    #
    # Four nullable columns, no DEFAULT. `ADD COLUMN` is an O(1) metadata
    # change in SQLite — existing rows are untouched and simply read NULL,
    # which is the signal the Usage page uses to mark a row "estimated"
    # rather than "measured". A `NOT NULL DEFAULT ''` would make old rows
    # indistinguishable from new ones that genuinely have no model.
    (11, 12): [
        "ALTER TABLE messages ADD COLUMN model_id TEXT",
        "ALTER TABLE messages ADD COLUMN model_display_name TEXT",
        "ALTER TABLE messages ADD COLUMN provider_type TEXT",
        "ALTER TABLE messages ADD COLUMN provider_instance_id TEXT",
    ],
    # [T-android-downgrade-compat] Downgrade 12 -> 11. Deliberately a NO-OP.
    #
    # Why this exists
    # ---------------
    # Room resolves a downgrade by calling `onUpgrade(from, to)` and
    # looking for a migration path; when none exists it consults
    # `isMigrationRequired`, which either throws or — with a destructive
    # fallback enabled — calls `dropAllTables`. We enable no fallback, so
    # before this migration existed, installing an older build on top of a
    # newer database threw `IllegalStateException` at first DB access and
    # the app could not start at all. Data survived on disk, but the user
    # experience was "the app is broken".
    #
    # Registering any 12 -> 11 path is enough for Room to proceed; it does
    # not inspect what the migration does. So the cheapest correct answer
    # is to do nothing and let the four extra columns stay.
    #
    # Why NOT `DROP COLUMN`
    # ---------------------
    # Dropping would genuinely delete the attribution captured by the
    # newer build, so a user who downgrades to look at something and then
    # upgrades back would silently lose it — violating the "no data loss"
    # constraint this whole mechanism exists to uphold. Keeping the
    # columns costs a few dozen bytes per row and makes the round trip
    # lossless. (`ALTER TABLE ... DROP COLUMN` also needs SQLite 3.35+ /
    # API 34+, and a full table rebuild below that.)
    #
    # Why leaving the columns is safe for the older build
    # ---------------------------------------------------
    # - Room's generated DAOs bind by column NAME, never by position.
    # - Room's schema validation checks that every column the entity
    #   REQUIRES exists; extra columns in the table are ignored.
    # - `SELECT *` is likewise resolved by name.
    # - The one place this codebase reads a cursor positionally
    #   (`VoiceCorrectionDb`, `ConfigAuditLog`) uses explicit column lists
    #   in separate databases, so the projection is fixed regardless.
    # - Old `INSERT` statements omit the new columns, which is fine
    #   precisely because they are nullable with no DEFAULT.
    #
    # Scope
    # -----
    # This pattern generalises to ADD COLUMN / ADD TABLE / ADD INDEX. It
    # does NOT cover renames, drops, type changes, or changes to the
    # MEANING of existing data (Room cannot detect the last one at all).
    # Those need a real reverse migration — or the version pre-check in
    # DatabaseVersionGuard, which is the backstop for exactly this case.
    (12, 11): [],  # Intentionally empty. See the comment above.
    # 3 -> 4 was declared out of order in the Kotlin companion (after 10_11);
    # ordering in this dict is irrelevant because migrations are looked up by
    # (from, to) key and applied as a chain.
    (3, 4): [
        # sessions: add iOS-parity columns
        "ALTER TABLE sessions ADD COLUMN source TEXT",
        "ALTER TABLE sessions ADD COLUMN memory_enabled INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE sessions ADD COLUMN pinned_at INTEGER",
        "ALTER TABLE sessions ADD COLUMN edit_count INTEGER NOT NULL DEFAULT 0",
        # messages: add iOS-parity columns
        "ALTER TABLE messages ADD COLUMN stream_interrupt_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE messages ADD COLUMN updated_at INTEGER",
        # compact_markers: new table mirroring iOS
        """
        CREATE TABLE IF NOT EXISTS compact_markers (
            id TEXT NOT NULL PRIMARY KEY,
            session_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            first_kept_sort_order INTEGER NOT NULL,
            compacted_count INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            ui_boundary_sort_order INTEGER,
            boundary_message_id TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS index_compact_markers_session_id ON compact_markers(session_id)",
    ],
}

MIGRATIONS = _MIGRATIONS


class AppDatabase:
    """Python stand-in for the Room ``AppDatabase``.

    Holds the async engine, a session factory, and applies the migration
    chain on first open.
    """

    def __init__(self, path: Path | str | None = None, *, echo: bool = False) -> None:
        db_path = Path(path) if path else app_context().database_path(DATABASE_NAME)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self._url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        self.engine: AsyncEngine = create_async_engine(
            self._url, echo=echo, poolclass=StaticPool if ":memory:" in self._url else None
        )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self._enable_foreign_keys()

    # --- pragma ---------------------------------------------------------
    def _enable_foreign_keys(self) -> None:
        """SQLite ignores FKs unless ``PRAGMA foreign_keys=ON`` per connection.

        Room enables this by default; SQLAlchemy needs it wired up.
        """

        @event.listens_for(self.engine.sync_engine, "connect")
        def _on_connect(dbapi_conn: Any, _record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    # --- lifecycle ------------------------------------------------------
    async def initialize(self) -> None:
        """Create tables (fresh DB) then run any pending migrations."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await self._migrate(conn)
        logger.debug("database ready at %s", self.path)

    async def _migrate(self, conn: Any) -> None:
        current = await self._user_version(conn)
        if current >= DATABASE_VERSION:
            return
        if current == 0:
            # Fresh database: `create_all` above already produced the v12
            # schema, so there is nothing to migrate — only stamp the version.
            # Room never sees this case because it always starts at 1.
            await self._set_user_version(conn, DATABASE_VERSION)
            logger.debug("fresh database stamped at version %d", DATABASE_VERSION)
            return
        while current < DATABASE_VERSION:
            statements = _MIGRATIONS.get((current, current + 1))
            if statements is None:
                raise RuntimeError(f"no migration path from {current} to {current + 1}")
            for sql in statements:
                await conn.execute(text(sql.strip()))
            current += 1
            await self._set_user_version(conn, current)
            logger.info("migrated database to version %d", current)

    @staticmethod
    async def _user_version(conn: Any) -> int:
        result = await conn.execute(text("PRAGMA user_version"))
        return int(result.scalar() or 0)

    @staticmethod
    async def _set_user_version(conn: Any, version: int) -> None:
        # PRAGMA does not accept bound parameters — interpolate a literal int.
        await conn.execute(text(f"PRAGMA user_version = {int(version)}"))

    # --- sessions -------------------------------------------------------
    def session(self) -> AsyncSession:
        """Kotlin hands out DAO objects; Python hands out an ``AsyncSession``."""
        return self.session_factory()

    async def close(self) -> None:
        await self.engine.dispose()


_db: AppDatabase | None = None


async def get_database(path: Path | str | None = None) -> AppDatabase:
    """Kotlin: ``AppDatabase.getInstance(context)`` (process-wide singleton)."""
    global _db
    if _db is None:
        _db = AppDatabase(path)
        await _db.initialize()
    return _db


def reset_for_tests() -> None:
    """Test-only escape hatch (no Kotlin counterpart)."""
    global _db
    _db = None


#: Entities registered on the database — mirrors ``@Database(entities=[...])``.
ENTITIES = [
    ChatSessionEntity,
    MessageEntity,
    CompactMarkerEntity,
    WebAppShortcutEntity,
    FolderEntity,
]

_ = Callable, Awaitable  # keep typing imports referenced for future DAO helpers
