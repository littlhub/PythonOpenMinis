"""SQLite-backed rolling log of config changes.

Ported from: src/android/app/src/main/java/com/openminis/app/config/audit/ConfigAuditLog.kt
Original package: com.openminis.app.config.audit
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

from openminis.core.context import app_context
from openminis.core.logging import get_logger

from .config_audit_entry import ConfigAuditActor, ConfigAuditEntry, ConfigAuditStatus

logger = get_logger(__name__)


class ConfigAuditLog:
    """SQLite-backed rolling log of config changes. Mirrors iOS `ConfigAuditLog`.

    Storage: a dedicated `minis-config-audit.db` next to the chat db. Keeping it
    separate means the audit table never gets caught in sync-dirty queries and can be
    wiped independently if it ever corrupts. Capacity: most-recent 1000 rows; older rows
    are pruned in the same transaction as each insert.

    Threading: every method locks on a single mutex, so multi-thread inserts from the
    offload server worker pool are safe. Reads return detached value objects.

    PORT: Android `SQLiteOpenHelper` + `ContentValues` + `Cursor` → Python `sqlite3`
    with a persistent connection (`check_same_thread=False` so the offload pool can call
    in). `Context.applicationContext.filesDir` → `app_context().files_dir`.
    """

    DB_NAME = "minis-config-audit.db"
    DB_VERSION = 1
    MAX_ROWS = 1000
    TABLE = "config_audit"

    COL_ID = "id"
    COL_AT = "at"
    COL_ACTOR = "actor"
    COL_SESSION_ID = "session_id"
    COL_SCOPE = "scope"
    COL_KEY = "key"
    COL_OLD_VALUE = "old_value"
    COL_NEW_VALUE = "new_value"
    COL_CONFIRMED_AT = "confirmed_at"
    COL_STATUS = "status"
    COL_REVERT_OF = "revert_of"
    COL_CAPTION = "caption"

    COLUMNS = (
        f"{COL_ID}, {COL_AT}, {COL_ACTOR}, {COL_SESSION_ID}, {COL_SCOPE}, {COL_KEY}, "
        f"{COL_OLD_VALUE}, {COL_NEW_VALUE}, {COL_CONFIRMED_AT}, {COL_STATUS}, "
        f"{COL_REVERT_OF}, {COL_CAPTION}"
    )

    _INSTANCE: "ConfigAuditLog | None" = None
    _lock = threading.Lock()

    def __init__(self, context=None) -> None:
        path = app_context().files_dir / self.DB_NAME
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()
        self._revision = 0

    def _ensure_schema(self) -> None:
        self._conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE} (
                {self.COL_ID}            TEXT PRIMARY KEY,
                {self.COL_AT}            INTEGER NOT NULL,
                {self.COL_ACTOR}         TEXT NOT NULL,
                {self.COL_SESSION_ID}    TEXT,
                {self.COL_SCOPE}         TEXT NOT NULL,
                {self.COL_KEY}           TEXT NOT NULL,
                {self.COL_OLD_VALUE}     TEXT NOT NULL,
                {self.COL_NEW_VALUE}     TEXT NOT NULL,
                {self.COL_CONFIRMED_AT}  INTEGER,
                {self.COL_STATUS}        TEXT NOT NULL,
                {self.COL_REVERT_OF}     TEXT,
                {self.COL_CAPTION}       TEXT
            )
            """
        )
        self._conn.execute(f"CREATE INDEX IF NOT EXISTS idx_audit_at ON {self.TABLE}({self.COL_AT} DESC)")
        self._conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_audit_scope ON {self.TABLE}({self.COL_SCOPE}, {self.COL_AT} DESC)"
        )
        self._conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_audit_revert_of ON {self.TABLE}({self.COL_REVERT_OF})"
        )
        self._conn.commit()

    # --- revision (mirrors MutableStateFlow<Int> / AtomicInteger) ----------

    @property
    def revision(self) -> int:
        return self._revision

    def _bump_revision(self) -> None:
        self._revision += 1

    # --- writes ------------------------------------------------------------

    def append(self, entry: ConfigAuditEntry) -> None:
        """Insert a new audit entry. Caps the table at MAX_ROWS in the same tx."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                INSERT OR REPLACE INTO {self.TABLE}
                    ({self.COL_ID}, {self.COL_AT}, {self.COL_ACTOR}, {self.COL_SESSION_ID},
                     {self.COL_SCOPE}, {self.COL_KEY}, {self.COL_OLD_VALUE}, {self.COL_NEW_VALUE},
                     {self.COL_CONFIRMED_AT}, {self.COL_STATUS}, {self.COL_REVERT_OF}, {self.COL_CAPTION})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.at,
                    entry.actor.value,
                    entry.session_id,
                    entry.scope,
                    entry.key,
                    entry.old_value_json,
                    entry.new_value_json,
                    entry.confirmed_at,
                    entry.status.value,
                    entry.revert_of,
                    entry.caption,
                ),
            )
            # Rolling cap — drop everything older than the 1000th row.
            cur.execute(
                f"""
                DELETE FROM {self.TABLE} WHERE {self.COL_ID} NOT IN (
                  SELECT {self.COL_ID} FROM {self.TABLE} ORDER BY {self.COL_AT} DESC LIMIT ?
                )
                """,
                (self.MAX_ROWS,),
            )
            self._conn.commit()
        self._bump_revision()

    def mark_reverted(self, entry_id: str) -> None:
        """Mark an entry as `reverted` (preserves history; no row deletion)."""
        with self._lock:
            self._conn.execute(
                f"UPDATE {self.TABLE} SET {self.COL_STATUS} = ? WHERE {self.COL_ID} = ?",
                (ConfigAuditStatus.REVERTED.value, entry_id),
            )
            self._conn.commit()
        self._bump_revision()

    def clear_all(self) -> None:
        """Wipe all rows. Surfaced as a manual UI action; never via CLI."""
        with self._lock:
            self._conn.execute(f"DELETE FROM {self.TABLE}")
            self._conn.commit()
        self._bump_revision()

    # --- reads -------------------------------------------------------------

    def recent(self, limit: int = 200, scope: str | None = None) -> list[ConfigAuditEntry]:
        """Most-recent first. `limit` is clamped to MAX_ROWS."""
        cap = max(1, min(limit, self.MAX_ROWS))
        with self._lock:
            if scope is not None:
                rows = self._conn.execute(
                    f"SELECT {self.COLUMNS} FROM {self.TABLE} WHERE {self.COL_SCOPE} = ? "
                    f"ORDER BY {self.COL_AT} DESC LIMIT ?",
                    (scope, cap),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"SELECT {self.COLUMNS} FROM {self.TABLE} ORDER BY {self.COL_AT} DESC LIMIT ?",
                    (cap,),
                ).fetchall()
        return [self._decode(r) for r in rows]

    def get(self, entry_id: str) -> "ConfigAuditEntry | None":
        with self._lock:
            row = self._conn.execute(
                f"SELECT {self.COLUMNS} FROM {self.TABLE} WHERE {self.COL_ID} = ?",
                (entry_id,),
            ).fetchone()
        return self._decode(row) if row is not None else None

    def usage(self) -> "Usage":
        """Total row count + capacity. UI shows "X / 1000 used"."""
        with self._lock:
            row = self._conn.execute(f"SELECT COUNT(*) FROM {self.TABLE}").fetchone()
        count = row[0] if row is not None else 0
        return Usage(count=count, capacity=self.MAX_ROWS)

    @dataclass
    class Usage:
        count: int
        capacity: int

    def _decode(self, row) -> "ConfigAuditEntry | None":
        status_raw = row[9]
        status = ConfigAuditStatus.from_raw(status_raw)
        if status is None:
            return None
        return ConfigAuditEntry(
            id=row[0],
            at=row[1],
            actor=ConfigAuditActor.from_raw(row[2]),
            session_id=row[3],
            scope=row[4],
            key=row[5],
            old_value_json=row[6],
            new_value_json=row[7],
            confirmed_at=row[8],
            status=status,
            revert_of=row[10],
            caption=row[11],
        )

    # --- singleton plumbing ------------------------------------------------

    @classmethod
    def init(cls, context=None) -> "ConfigAuditLog":
        if cls._INSTANCE is not None:
            return cls._INSTANCE
        with cls._lock:
            if cls._INSTANCE is not None:
                return cls._INSTANCE
            inst = cls(context)
            cls._INSTANCE = inst
            logger.info("audit log opened (cap=%d)", cls.MAX_ROWS)
            return inst

    @classmethod
    def get_instance(cls) -> "ConfigAuditLog":
        """Kotlin `get()` — raises if not initialized."""
        if cls._INSTANCE is None:
            raise RuntimeError(
                "ConfigAuditLog not initialized; call init() from Application.onCreate"
            )
        return cls._INSTANCE
