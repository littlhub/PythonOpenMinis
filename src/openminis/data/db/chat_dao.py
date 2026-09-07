"""Chat DAO — sessions, messages, folders and compact markers.

Ported from: src/android/app/src/main/java/com/openminis/app/data/db/ChatDao.kt
Original package: com.openminis.app.data.db

Room's ``@Dao`` interface becomes a plain class taking an ``AsyncSession``.
Every ``@Query`` keeps its SQL shape (see the ``# PORT:`` notes) and every
``suspend fun`` becomes ``async def``.

``Flow<T>`` returns — Kotlin's Room re-queries automatically whenever the
underlying table changes (via its invalidation tracker). SQLAlchemy has no
equivalent, so ``observe_*`` methods are async generators that emit once and
then poll. The polling interval is a deliberate stand-in, not a design
decision; swap in SQLAlchemy event listeners when a real reactive layer lands.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from .chat_session_entity import ChatSessionEntity
from .compact_marker_entity import CompactMarkerEntity
from .folder_entity import FolderEntity
from .message_entity import MessageEntity
from .usage_record import UsageRecord

logger = get_logger(__name__)

__all__ = [
    "ChatDao",
    "SessionMetaRow",
    "MessageSearchRow",
    "SessionTailRow",
]

#: Poll interval (seconds) for the ``observe_*`` generators.
OBSERVE_POLL_SECONDS = 1.0


def _now_ms() -> int:
    """Kotlin: ``System.currentTimeMillis()`` (default arg on several queries)."""
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# row projections
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SessionMetaRow:
    """Row projection backing the sessions list/CLI summary (T188).

    The SELECT shape is dynamic (built from optional keyword/date/IN-list
    conditions), so the original uses ``@RawQuery`` + this POJO instead of a
    static ``@Query``. Column names here must exactly match the aliases the
    dynamic SQL emits — Room binds by column name, not by ordinal.
    """

    id: str
    title: str | None
    first_user_msg: str | None
    source: str | None
    created_at: int
    updated_at: int
    msg_count: int


@dataclass(frozen=True, slots=True)
class MessageSearchRow:
    """Row projection backing message search (T188).

    Same dynamic-SQL pattern as :class:`SessionMetaRow` — keyword count varies
    per call, so the WHERE clause is built dynamically and bound with
    positional args.
    """

    session_id: str
    id: str
    role: str
    created_at: int
    parts_json: str


@dataclass(frozen=True, slots=True)
class SessionTailRow:
    """Row projection for :meth:`ChatDao.last_message_tail_per_session`.

    [T-android-session-paused-badge-hardkill] One row = the last message of a
    session (by sort_order), carrying just the fields needed to decide whether
    the agent loop was left interrupted, without loading the full history.
    """

    session_id: str
    role: str
    parts_json: str


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------
class ChatDao:
    """Data access for ``sessions`` / ``messages`` / ``folders`` / ``compact_markers``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- sessions --------------------------------------------------------
    # PORT: `SELECT * FROM sessions ORDER BY updated_at DESC`
    async def list_sessions(self) -> list[ChatSessionEntity]:
        result = await self._s.execute(
            select(ChatSessionEntity).order_by(ChatSessionEntity.updated_at.desc())
        )
        return list(result.scalars().all())

    async def observe_sessions(self) -> AsyncIterator[list[ChatSessionEntity]]:
        """Kotlin: ``observeSessions(): Flow<List<ChatSessionEntity>>``."""
        while True:
            yield await self.list_sessions()
            import asyncio

            await asyncio.sleep(OBSERVE_POLL_SECONDS)

    async def get_session(self, session_id: str) -> ChatSessionEntity | None:
        return await self._s.get(ChatSessionEntity, session_id)

    # PORT: @Insert(onConflict = REPLACE)
    async def insert_session(self, session: ChatSessionEntity) -> None:
        await self._s.merge(session)
        await self._s.commit()

    # PORT: UPDATE sessions SET title = :title, updated_at = :updatedAt WHERE id = :id
    async def update_session_title(self, session_id: str, title: str, updated_at: int) -> None:
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(title=title, updated_at=updated_at)
        )
        await self._s.commit()

    # PORT: SET title = :title, category = COALESCE(:category, category), updated_at = :updatedAt
    async def update_session_title_and_category(
        self, session_id: str, title: str, category: str | None, updated_at: int
    ) -> None:
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(
                title=title,
                category=func.coalesce(category, ChatSessionEntity.category),
                updated_at=updated_at,
            )
        )
        await self._s.commit()

    async def touch_session(self, session_id: str, updated_at: int) -> None:
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(updated_at=updated_at)
        )
        await self._s.commit()

    async def update_last_message(
        self, session_id: str, preview: str | None, updated_at: int
    ) -> None:
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(last_message=preview, updated_at=updated_at)
        )
        await self._s.commit()

    async def update_session_model(
        self, session_id: str, model_id: str, updated_at: int | None = None
    ) -> None:
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(model_id=model_id, updated_at=updated_at or _now_ms())
        )
        await self._s.commit()

    async def update_session_binding(
        self, session_id: str, binding: str, model_id: str, updated_at: int | None = None
    ) -> None:
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(
                model_binding=binding, model_id=model_id, updated_at=updated_at or _now_ms()
            )
        )
        await self._s.commit()

    async def delete_session(self, session_id: str) -> None:
        await self._s.execute(
            delete(ChatSessionEntity).where(ChatSessionEntity.id == session_id)
        )
        await self._s.commit()

    # PORT: full-text-ish search — LIKE over title and message parts_json.
    async def search_sessions(self, pattern: str) -> list[ChatSessionEntity]:
        result = await self._s.execute(
            select(ChatSessionEntity)
            .distinct()
            .outerjoin(MessageEntity, MessageEntity.session_id == ChatSessionEntity.id)
            .where(
                (ChatSessionEntity.title.like(pattern))
                | (MessageEntity.parts_json.like(pattern))
            )
            .order_by(ChatSessionEntity.updated_at.desc())
        )
        return list(result.scalars().all())

    # --- folders (session groups) ----------------------------------------
    # [T-android-session-grouping] "Folder" in code, "Group" in the UI.

    async def _list_folders(self) -> list[FolderEntity]:
        """Ordered ``updated_at DESC`` to match iOS ``listFolders()``.

        Rows with a blank id are skipped — such a row could not be opened,
        filed into, or synced, so surfacing it would only produce a dead card.
        """
        result = await self._s.execute(
            select(FolderEntity)
            .where(FolderEntity.id != "")
            .order_by(FolderEntity.updated_at.desc())
        )
        return list(result.scalars().all())

    async def list_folders(self) -> list[FolderEntity]:
        return await self._list_folders()

    async def observe_folders(self) -> AsyncIterator[list[FolderEntity]]:
        while True:
            yield await self._list_folders()
            import asyncio

            await asyncio.sleep(OBSERVE_POLL_SECONDS)

    async def get_folder(self, folder_id: str) -> FolderEntity | None:
        return await self._s.get(FolderEntity, folder_id)

    async def insert_folder(self, folder: FolderEntity) -> None:
        await self._s.merge(folder)
        await self._s.commit()

    async def rename_folder(
        self, folder_id: str, name: str, description: str | None, updated_at: int
    ) -> None:
        """``description = COALESCE(:description, description)``.

        Passing None LEAVES the stored description alone while an empty string
        clears it — the caller's two intents stay distinguishable, matching iOS
        renameFolder.
        """
        await self._s.execute(
            update(FolderEntity)
            .where(FolderEntity.id == folder_id)
            .values(
                name=name,
                description=func.coalesce(description, FolderEntity.description),
                updated_at=updated_at,
            )
        )
        await self._s.commit()

    async def set_folder_pinned(
        self, folder_id: str, pinned_at: int | None, updated_at: int
    ) -> None:
        """Pin toggles bump ``updated_at`` too, so the change carries a fresh LWW stamp."""
        await self._s.execute(
            update(FolderEntity)
            .where(FolderEntity.id == folder_id)
            .values(pinned_at=pinned_at, updated_at=updated_at)
        )
        await self._s.commit()

    async def delete_folder(self, folder_id: str) -> None:
        await self._s.execute(delete(FolderEntity).where(FolderEntity.id == folder_id))
        await self._s.commit()

    async def session_ids_in_folder(self, folder_id: str) -> list[str]:
        result = await self._s.execute(
            select(ChatSessionEntity.id)
            .where(ChatSessionEntity.folder_id == folder_id)
            .order_by(ChatSessionEntity.updated_at.desc())
        )
        return list(result.scalars().all())

    async def session_count_in_folder(self, folder_id: str) -> int:
        result = await self._s.execute(
            select(func.count())
            .select_from(ChatSessionEntity)
            .where(ChatSessionEntity.folder_id == folder_id)
        )
        return int(result.scalar() or 0)

    async def set_session_folder(self, session_id: str, folder_id: str | None) -> None:
        """Move a session in (non-null) or out (null) of a group.

        Writes ONLY ``folder_id`` — ``updated_at`` is deliberately untouched,
        because an organizational move must not re-sort the session list (which
        orders by ``updated_at DESC``). Filing a months-old chat should not
        shove it to the top of Today.
        """
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(folder_id=folder_id)
        )
        await self._s.commit()

    async def set_session_folder_if_unfiled(self, session_id: str, folder_id: str) -> int:
        """Conditional variant for any future automatic-grouping path.

        The ``AND folder_id IS NULL`` lives in the STATEMENT rather than a
        caller-side read-then-write, so a group the user chose by hand can
        never be overwritten by a machine guess racing it.
        """
        result = await self._s.execute(
            update(ChatSessionEntity)
            .where(
                (ChatSessionEntity.id == session_id)
                & (ChatSessionEntity.folder_id.is_(None))
            )
            .values(folder_id=folder_id)
        )
        await self._s.commit()
        return int(result.rowcount or 0)

    async def clear_folder_for_sessions(self, folder_id: str) -> None:
        """Clear membership for every session of a group — the dissolve half."""
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.folder_id == folder_id)
            .values(folder_id=None)
        )
        await self._s.commit()

    # --- messages --------------------------------------------------------
    async def load_messages(self, session_id: str) -> list[MessageEntity]:
        result = await self._s.execute(
            select(MessageEntity)
            .where(MessageEntity.session_id == session_id)
            .order_by(MessageEntity.sort_order.asc())
        )
        return list(result.scalars().all())

    async def observe_messages(self, session_id: str) -> AsyncIterator[list[MessageEntity]]:
        while True:
            yield await self.load_messages(session_id)
            import asyncio

            await asyncio.sleep(OBSERVE_POLL_SECONDS)

    async def insert_message(self, message: MessageEntity) -> None:
        await self._s.merge(message)
        await self._s.commit()

    async def load_user_messages_since(self, since: int, limit: int) -> list[MessageEntity]:
        """[T-android-voice-correction] User messages newer than ``since`` (epoch ms).

        Filtering in SQL rather than loading all sessions and discarding in
        Python (which is what iOS does) keeps an incremental build proportional
        to what is actually new. ``limit`` bounds a first run over a long
        history.
        """
        result = await self._s.execute(
            select(MessageEntity)
            .where((MessageEntity.role == "user") & (MessageEntity.created_at > since))
            .order_by(MessageEntity.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # PORT: SELECT COALESCE(MAX(sort_order), -1) + 1
    async def next_sort_order(self, session_id: str) -> int:
        result = await self._s.execute(
            select(func.coalesce(func.max(MessageEntity.sort_order), -1) + 1).where(
                MessageEntity.session_id == session_id
            )
        )
        return int(result.scalar() or 0)

    async def delete_messages(self, session_id: str) -> None:
        await self._s.execute(
            delete(MessageEntity).where(MessageEntity.session_id == session_id)
        )
        await self._s.commit()

    async def delete_messages_after(self, session_id: str, keep_count: int) -> None:
        await self._s.execute(
            delete(MessageEntity).where(
                (MessageEntity.session_id == session_id)
                & (MessageEntity.sort_order >= keep_count)
            )
        )
        await self._s.commit()

    async def total_message_count(self) -> int:
        result = await self._s.execute(
            select(func.count()).select_from(MessageEntity)
        )
        return int(result.scalar() or 0)

    async def token_usages(self, session_id: str) -> list[str]:
        result = await self._s.execute(
            select(MessageEntity.token_usage).where(
                (MessageEntity.session_id == session_id)
                & (MessageEntity.token_usage.is_not(None))
            )
        )
        return list(result.scalars().all())

    async def all_usage_records(self) -> list[UsageRecord]:
        """Fetch all token usage records joined with session model_id.

        [T-android-usage-orphan-rows] GH#168 (iOS a192fad0f): LEFT JOIN, not
        INNER JOIN. The Usage page is built entirely from this query, so any row
        it drops is silently missing from the user's totals. With an INNER JOIN,
        a message whose ``sessions`` row is gone — orphaned by a failed sync or
        migration, or a partially-deleted session — vanished from the totals
        even though its ``token_usage`` is still sitting in the table. Those
        tokens were really billed, so the page under-reported with nothing to
        indicate it.

        LEFT JOIN keeps those rows, which is why ``UsageRecord.model_id`` is
        nullable: it comes back NULL for an orphan, and the caller groups those
        under "Unknown" rather than discarding them.

        This does NOT recover usage from sessions the user deleted outright —
        deleting a session removes its message rows too. It only stops
        orphaned-but-present rows from being thrown away.

        [T-token-attribution-snapshot] The model now comes from
        ``COALESCE(m.model_id, s.model_id)``, preferring the per-message
        snapshot written when the turn was persisted. ``s.model_id`` remains
        only as the fallback for rows written before that column existed;
        ``has_snapshot`` is selected alongside so the UI can show fallback rows
        as ESTIMATED rather than passing them off as measured.
        """
        result = await self._s.execute(
            text(
                """
                SELECT COALESCE(m.model_id, s.model_id) AS model_id,
                       m.model_display_name  AS model_display_name,
                       m.provider_type       AS provider_type,
                       (m.model_id IS NOT NULL) AS has_snapshot,
                       m.token_usage AS token_usage,
                       m.created_at AS created_at,
                       m.session_id AS session_id
                FROM messages m LEFT JOIN sessions s ON m.session_id = s.id
                WHERE m.token_usage IS NOT NULL
                """
            )
        )
        return [
            UsageRecord(
                model_id=row.model_id,
                model_display_name=row.model_display_name,
                provider_type=row.provider_type,
                has_snapshot=bool(row.has_snapshot),
                token_usage=row.token_usage,
                created_at=row.created_at,
                session_id=row.session_id,
            )
            for row in result.all()
        ]

    async def last_message_parts(self, session_id: str) -> str | None:
        result = await self._s.execute(
            select(MessageEntity.parts_json)
            .where(MessageEntity.session_id == session_id)
            .order_by(MessageEntity.sort_order.desc())
            .limit(1)
        )
        return result.scalar()

    async def last_message_tail_per_session(self) -> list[SessionTailRow]:
        """[T-android-session-paused-badge-hardkill]

        The last message (role + parts_json) of every session, in one query —
        used at launch to derive which sessions were left interrupted, so the
        PAUSED badge survives a hard process death (where the lifecycle-callback
        push never runs). Picks the row with the max sort_order per session via
        a correlated subquery, mirroring iOS ChatStore.interruptedSessionIds().
        """
        result = await self._s.execute(
            text(
                """
                SELECT m.session_id AS session_id, m.role AS role, m.parts_json AS parts_json
                FROM messages m
                WHERE m.sort_order = (
                    SELECT MAX(m2.sort_order) FROM messages m2 WHERE m2.session_id = m.session_id
                )
                """
            )
        )
        return [
            SessionTailRow(
                session_id=row.session_id, role=row.role, parts_json=row.parts_json
            )
            for row in result.all()
        ]

    # --- session flags ---------------------------------------------------
    async def update_memory_enabled(
        self, session_id: str, enabled: int, updated_at: int | None = None
    ) -> None:
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(memory_enabled=enabled, updated_at=updated_at or _now_ms())
        )
        await self._s.commit()

    async def update_thinking_override(
        self, session_id: str, value: str | None, updated_at: int | None = None
    ) -> None:
        """T239: null clears the explicit choice and falls back to the current
        model/group default; non-null is a ThinkingLevel.name string.
        """
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(thinking_override=value, updated_at=updated_at or _now_ms())
        )
        await self._s.commit()

    async def update_pinned_at(
        self, session_id: str, pinned_at: int | None, updated_at: int | None = None
    ) -> None:
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(pinned_at=pinned_at, updated_at=updated_at or _now_ms())
        )
        await self._s.commit()

    async def update_source(self, session_id: str, source: str | None) -> None:
        await self._s.execute(
            update(ChatSessionEntity)
            .where(ChatSessionEntity.id == session_id)
            .values(source=source)
        )
        await self._s.commit()

    async def increment_stream_interrupt_count(
        self, message_id: str, updated_at: int | None = None
    ) -> None:
        await self._s.execute(
            update(MessageEntity)
            .where(MessageEntity.id == message_id)
            .values(
                stream_interrupt_count=MessageEntity.stream_interrupt_count + 1,
                updated_at=updated_at or _now_ms(),
            )
        )
        await self._s.commit()

    async def update_message_parts(
        self, message_id: str, parts_json: str, updated_at: int | None = None
    ) -> None:
        """Rewrite a single row's parts_json in place.

        Mirrors iOS ChatStore.updateMessageParts. Used by rerunFromToolBlock's
        block-boundary cut: delete_messages_after drops whole rows after the
        boundary, but the kept assistant row itself needs its parts trimmed to
        before the target tool_use — that's an UPDATE of an existing row, not a
        delete.
        """
        await self._s.execute(
            update(MessageEntity)
            .where(MessageEntity.id == message_id)
            .values(parts_json=parts_json, updated_at=updated_at or _now_ms())
        )
        await self._s.commit()

    # [T-error-persist-android]
    async def update_message_error_info(self, message_id: str, error_info: str | None) -> None:
        await self._s.execute(
            update(MessageEntity)
            .where(MessageEntity.id == message_id)
            .values(error_info=error_info)
        )
        await self._s.commit()

    async def update_last_assistant_error(
        self, session_id: str, error_info: str | None
    ) -> None:
        """[T-error-persist-android] Stamp the error sticker onto the LAST
        assistant row of a session.

        The agent loop persists each turn with a fresh random row id while the
        in-memory UI bubble keeps its own id, so setInlineError() can't address
        the row by the in-memory ChatMessage id. Targeting "last assistant row"
        matches both iOS (which attaches the error to
        ``messages.last(where role==.assistant)``) and the load-side merge that
        folds consecutive assistant rows keeping the last row's identity. No-op
        when the session has no assistant row yet.
        """
        await self._s.execute(
            text(
                """
                UPDATE messages SET error_info = :error_info
                WHERE id = (
                    SELECT id FROM messages
                    WHERE session_id = :session_id AND role = 'assistant'
                    ORDER BY sort_order DESC LIMIT 1
                )
                """
            ),
            {"error_info": error_info, "session_id": session_id},
        )
        await self._s.commit()

    # Pinned sessions first, then by updated_at
    async def list_sessions_sorted(self) -> list[ChatSessionEntity]:
        result = await self._s.execute(
            select(ChatSessionEntity).order_by(
                text("CASE WHEN pinned_at IS NOT NULL THEN 0 ELSE 1 END"),
                ChatSessionEntity.pinned_at.desc(),
                ChatSessionEntity.updated_at.desc(),
            )
        )
        return list(result.scalars().all())

    async def observe_sessions_sorted(self) -> AsyncIterator[list[ChatSessionEntity]]:
        while True:
            yield await self.list_sessions_sorted()
            import asyncio

            await asyncio.sleep(OBSERVE_POLL_SECONDS)

    # --- compact markers -------------------------------------------------
    # Append-only: rows are never updated, only inserted and (on session
    # delete) cascade-removed. Lookups prefer id-first columns over the legacy
    # sort-order columns.

    # PORT: @Insert(onConflict = ABORT) — ABORT mirrors SQLAlchemy's default
    # (an integrity error raises), so this is a plain add().
    async def insert_compact_marker(self, marker: CompactMarkerEntity) -> None:
        self._s.add(marker)
        await self._s.commit()

    async def update_compact_marker(self, marker: CompactMarkerEntity) -> None:
        """Replace a marker by id.

        Used by Phase 2.5 self-heal: when the createdAt fallback resolves an
        anchor for an orphaned marker, we rewrite that marker in place — same
        id/summary/createdAt, swapped lcmId + version=2 + cleared legacy
        fields. Mirrors iOS ``ChatStore.updateCompactMarker``.
        """
        await self._s.merge(marker)
        await self._s.commit()

    async def latest_compact_marker(self, session_id: str) -> CompactMarkerEntity | None:
        result = await self._s.execute(
            select(CompactMarkerEntity)
            .where(CompactMarkerEntity.session_id == session_id)
            .order_by(CompactMarkerEntity.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_compact_markers(self, session_id: str) -> list[CompactMarkerEntity]:
        result = await self._s.execute(
            select(CompactMarkerEntity)
            .where(CompactMarkerEntity.session_id == session_id)
            .order_by(CompactMarkerEntity.created_at.asc())
        )
        return list(result.scalars().all())

    async def delete_compact_markers(self, session_id: str) -> None:
        await self._s.execute(
            delete(CompactMarkerEntity).where(CompactMarkerEntity.session_id == session_id)
        )
        await self._s.commit()

    async def delete_compact_marker(self, marker_id: str) -> int:
        """Delete a single compact marker by id (for revert-compact)."""
        result = await self._s.execute(
            delete(CompactMarkerEntity).where(CompactMarkerEntity.id == marker_id)
        )
        await self._s.commit()
        return int(result.rowcount or 0)

    # --- T188: dynamic CLI queries ---------------------------------------
    # PORT: Kotlin uses @RawQuery(observedEntities=...) here. Python builds the
    # SQL as a string and binds params — same caller contract, no annotation
    # magic. Callers must produce columns matching SessionMetaRow.
    async def run_sessions_meta_query(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[SessionMetaRow]:
        result = await self._s.execute(text(sql), params or {})
        return [
            SessionMetaRow(
                id=row.id,
                title=row.title,
                first_user_msg=row.first_user_msg,
                source=row.source,
                created_at=row.created_at,
                updated_at=row.updated_at,
                msg_count=row.msg_count,
            )
            for row in result.all()
        ]

    async def run_message_search_query(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[MessageSearchRow]:
        result = await self._s.execute(text(sql), params or {})
        return [
            MessageSearchRow(
                session_id=row.session_id,
                id=row.id,
                role=row.role,
                created_at=row.created_at,
                parts_json=row.parts_json,
            )
            for row in result.all()
        ]

    async def load_messages_page(
        self, session_id: str, offset: int, limit: int
    ) -> list[MessageEntity]:
        """Paginated message page.

        Sorted by ``sort_order ASC`` (stable insertion order) with
        ``created_at ASC`` as a tie-breaker for messages inserted in the same
        millisecond.
        """
        result = await self._s.execute(
            select(MessageEntity)
            .where(MessageEntity.session_id == session_id)
            .order_by(MessageEntity.sort_order.asc(), MessageEntity.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def load_messages_page_in_range(
        self,
        session_id: str,
        offset: int,
        limit: int,
        start_ms: int | None,
        end_ms: int | None,
    ) -> list[MessageEntity]:
        """[T-android-sessions-cli-messages-daterange] GH#200 (iOS 8f3189a73).

        Date-filtered variant of :meth:`load_messages_page`. ``--start`` /
        ``--end`` were documented in the CLI help and honoured by ``list`` /
        ``search``, but ``messages`` parsed neither and silently returned the
        whole session. Both bounds are inclusive and independently optional —
        a NULL bound means "unbounded on that side", which keeps one query
        serving all four combinations.
        """
        stmt = select(MessageEntity).where(MessageEntity.session_id == session_id)
        if start_ms is not None:
            stmt = stmt.where(MessageEntity.created_at >= start_ms)
        if end_ms is not None:
            stmt = stmt.where(MessageEntity.created_at <= end_ms)
        result = await self._s.execute(
            stmt.order_by(MessageEntity.sort_order.asc(), MessageEntity.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def message_count_for_session(self, session_id: str) -> int:
        """Surface the total count alongside the paginated slice so callers can
        compute ``hasMore``.
        """
        result = await self._s.execute(
            select(func.count())
            .select_from(MessageEntity)
            .where(MessageEntity.session_id == session_id)
        )
        return int(result.scalar() or 0)

    async def message_count_for_session_in_range(
        self, session_id: str, start_ms: int | None, end_ms: int | None
    ) -> int:
        """[T-android-sessions-cli-messages-daterange] Count under the SAME range
        as :meth:`load_messages_page_in_range`.

        Using the unfiltered count alongside a filtered page would make
        ``total`` describe the whole session while the slice covers only the
        filtered subset, so ``hasMore`` would lie — the specific trap called out
        in iOS 8f3189a73.
        """
        stmt = (
            select(func.count())
            .select_from(MessageEntity)
            .where(MessageEntity.session_id == session_id)
        )
        if start_ms is not None:
            stmt = stmt.where(MessageEntity.created_at >= start_ms)
        if end_ms is not None:
            stmt = stmt.where(MessageEntity.created_at <= end_ms)
        result = await self._s.execute(stmt)
        return int(result.scalar() or 0)
