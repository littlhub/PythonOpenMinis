"""Chat session + message persistence for the Web chat UI.

Sessions and turns live in the SQLite database (the same ``sessions`` /
``messages`` tables the ported Room schema defines). The Web chat stores
plain-text turns: ``parts_json`` keeps a minimal
``[{"type":"text","text": …}]`` array, which round-trips through
:func:`parts_to_text`. Sessions auto-title from the first user message and
carry a ``last_message`` preview for the sidebar.

Both the REST endpoints (``server/chat_api``) and the WebSocket chat handler
(``server/main._handle_chat``) go through this module so storage stays
consistent.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..data.db.app_database import AppDatabase
from ..data.db.chat_dao import ChatDao
from ..data.db.chat_session_entity import ChatSessionEntity
from ..data.db.compact_marker_entity import CompactMarkerEntity
from ..data.db.message_entity import MessageEntity
from ..data.model import LLMMessage

__all__ = [
    "ChatSessionInfo",
    "ChatMessageInfo",
    "ensure_db",
    "list_sessions",
    "create_session",
    "get_session",
    "delete_session",
    "load_messages",
    "append_turn",
    "load_runtime_history",
    "drop_runtime",
    "parts_to_text",
    "TITLE_DEFAULT",
]

TITLE_DEFAULT = "新会话"
_TITLE_LEN = 30
_PREVIEW_LEN = 60

_db_ready = False
_db_lock = asyncio.Lock()

#: Process-wide engine for chat persistence (independent of the settings store;
#: tests may point it at a temp file via :func:`set_database_path`).
_engine_db: AppDatabase | None = None


def set_database_path(path: Any) -> None:
    """Point the store at a different SQLite file (used by tests)."""
    global _engine_db, _db_ready
    _engine_db = AppDatabase(path) if path is not None else None
    _db_ready = False


def _get_db() -> AppDatabase:
    global _engine_db
    if _engine_db is None:
        _engine_db = AppDatabase()
    return _engine_db


def database() -> AppDatabase:
    """The shared database instance (compaction writes markers through it)."""
    return _get_db()

#: In-process rich transcript per session (user text + full tool rounds),
#: so consecutive turns in one process keep complete tool context. Lost on
#: restart — history is then rebuilt from the persisted plain-text turns.
_RUNTIME: dict[str, list[LLMMessage]] = {}
_MAX_RUNTIME_SESSIONS = 200


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def parts_to_text(parts_json: str) -> str:
    """Extract the human text from a stored ``parts_json`` payload."""
    try:
        parts = json.loads(parts_json or "[]")
    except json.JSONDecodeError:
        return ""
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            txt = p.get("text")
            if isinstance(txt, str):
                out.append(txt)
        elif isinstance(p, str):
            out.append(p)
    return "\n".join(out)


def _text_parts(text: str) -> str:
    return json.dumps([{"type": "text", "text": text}], ensure_ascii=False)


@dataclass(frozen=True)
class ChatSessionInfo:
    id: str
    title: str
    updatedAt: int
    lastMessage: str
    folderId: str | None = None


@dataclass(frozen=True)
class ChatMessageInfo:
    id: str
    role: str
    text: str
    createdAt: int


async def ensure_db() -> None:
    """Initialise the shared database exactly once per process."""
    global _db_ready
    if _db_ready:
        return
    async with _db_lock:
        if _db_ready:
            return
        _engine = _get_db()
        await _engine.initialize()
        _db_ready = True


def _model_label() -> str:
    """Best-effort label of the active provider/model, without touching
    settings import cycles at module import time."""
    try:
        from ..settings.store import SettingsStore
        from ..settings.catalog import provider_label

        data = SettingsStore.get().load()
        pid = data.get("activeProviderId")
        conf = (data["providers"].get(pid) if pid else None) or {}
        model = conf.get("model") or ""
        if pid:
            label = str(conf.get("label") or "").strip()
            if not label:
                label = provider_label(str(conf.get("type") or pid))
            return f"{label}{(' · ' + model) if model else ''}"
    except Exception:  # pragma: no cover - never fail storage over cosmetics
        pass
    return ""


# -- sessions ----------------------------------------------------------------
async def list_sessions(folder_id: str | None | object = None) -> list[ChatSessionInfo]:
    """List all sessions, optionally filtered by ``folder_id``.

    Pass the sentinel value :data:`UNFILED` (``UNFILED``) to return only
    sessions that have no workspace; pass a real folder id to return only
    sessions in that workspace; pass ``None`` (default) for the unfiltered
    list.
    """
    await ensure_db()
    async with _get_db().session() as s:
        rows = await ChatDao(s).list_sessions_sorted()
        if folder_id is not None:
            target = None if folder_id is UNFILED else str(folder_id)
            rows = [r for r in rows if (r.folder_id or None) == target]
        return [
            ChatSessionInfo(
                id=r.id,
                title=r.title or TITLE_DEFAULT,
                updatedAt=r.updated_at,
                lastMessage=r.last_message or "",
                folderId=r.folder_id,
            )
            for r in rows
        ]


UNFILED: object = object()  # sentinel for "folder_id IS NULL"


async def create_session(
    model_label: str | None = None,
    folder_id: str | None = None,
) -> ChatSessionInfo:
    """Create an empty session and return its info.

    If ``folder_id`` is given the session is filed into that workspace;
    ``None`` (the default) leaves it ungrouped.
    """
    await ensure_db()
    sid = uuid.uuid4().hex
    now = _now_ms()
    entity = ChatSessionEntity(
        id=sid,
        title=TITLE_DEFAULT,
        model_id=model_label or _model_label() or "unset",
        created_at=now,
        updated_at=now,
        last_message="",
        folder_id=folder_id,
    )
    async with _get_db().session() as s:
        await ChatDao(s).insert_session(entity)
    return ChatSessionInfo(
        id=sid, title=TITLE_DEFAULT, updatedAt=now, lastMessage="",
        folderId=folder_id,
    )


async def get_session(session_id: str) -> ChatSessionInfo | None:
    await ensure_db()
    async with _get_db().session() as s:
        row = await ChatDao(s).get_session(session_id)
        if row is None:
            return None
        return ChatSessionInfo(
            id=row.id, title=row.title or TITLE_DEFAULT,
            updatedAt=row.updated_at, lastMessage=row.last_message or "",
            folderId=row.folder_id,
        )


async def delete_session(session_id: str) -> bool:
    await ensure_db()
    async with _get_db().session() as s:
        dao = ChatDao(s)
        if await dao.get_session(session_id) is None:
            return False
        await dao.delete_session(session_id)  # messages cascade
    drop_runtime(session_id)
    return True


# -- runtime transcript cache ------------------------------------------------
def _cache_runtime(session_id: str, messages: list[LLMMessage]) -> None:
    _RUNTIME[session_id] = messages
    if len(_RUNTIME) > _MAX_RUNTIME_SESSIONS:
        # drop the least-recently stored session (dict keeps insertion order)
        _RUNTIME.pop(next(iter(_RUNTIME)))


def drop_runtime(session_id: str) -> None:
    _RUNTIME.pop(session_id, None)


async def load_runtime_history(session_id: str) -> list[LLMMessage]:
    """The mutable transcript the agent loop appends to, per session.

    Prefers the in-process cache; on a miss it rebuilds a plain-text history
    from the DB (merged so ``user``/``assistant`` strictly alternate).

    If the session has been compacted, the rebuild starts at the latest
    marker's boundary and is prefixed with that marker's summary — the folded
    originals stay in the DB (history/export still sees them) but no longer
    consume context window.
    """
    cached = _RUNTIME.get(session_id)
    if cached is not None:
        return cached
    rows = await load_raw_messages(session_id)
    marker = await latest_compact_marker(session_id)
    start = first_kept_index(rows, marker)

    messages: list[LLMMessage] = []
    if marker is not None and start > 0 and marker.summary.strip():
        # Injected as an assistant turn: providers reject a ``system`` role
        # inside the message list (Anthropic in particular).
        messages.append(LLMMessage(LLMMessage.Role.ASSISTANT, compact_summary_text(
            marker.summary
        )))
    for row in rows[start:]:
        role = (
            LLMMessage.Role.ASSISTANT
            if row.role == "assistant"
            else LLMMessage.Role.USER
        )
        text = parts_to_text(row.parts_json).strip()
        if not text:
            continue
        if messages and messages[-1].role == role:
            # merge adjacent same-role turns to keep user/assistant alternating
            prev = messages[-1]
            prev.content = (prev.content + "\n\n" + text).strip()
        else:
            messages.append(LLMMessage(role, text))
    _cache_runtime(session_id, messages)
    return messages


#: Wraps a compacted summary so the model can tell it apart from a real reply.
COMPACT_SUMMARY_PREFIX = "[以下是对本会话较早对话的压缩摘要,原文仍保留在历史记录中]\n\n"


def compact_summary_text(summary: str) -> str:
    """Render a stored summary as the injected transcript entry."""
    return f"{COMPACT_SUMMARY_PREFIX}{summary.strip()}"


# -- messages ----------------------------------------------------------------
async def load_raw_messages(session_id: str) -> list[MessageEntity]:
    """Persisted turns with their ids and sort order (compaction needs both)."""
    await ensure_db()
    async with _get_db().session() as s:
        return await ChatDao(s).load_messages(session_id)


async def latest_compact_marker(session_id: str) -> CompactMarkerEntity | None:
    await ensure_db()
    async with _get_db().session() as s:
        return await ChatDao(s).latest_compact_marker(session_id)


def first_kept_index(
    rows: list[MessageEntity], marker: CompactMarkerEntity | None
) -> int:
    """Index of the first message that is *not* folded into ``marker``."""
    if marker is None:
        return 0
    anchor = marker.last_compacted_message_id
    if anchor:
        for i, row in enumerate(rows):
            if row.id == anchor:
                return i + 1
    # Fallback: markers without a usable anchor fall back to created_at.
    for i, row in enumerate(rows):
        if row.created_at > marker.created_at:
            return i
    return len(rows)


async def load_messages(session_id: str) -> list[ChatMessageInfo]:
    await ensure_db()
    async with _get_db().session() as s:
        rows = await ChatDao(s).load_messages(session_id)
        return [
            ChatMessageInfo(
                id=r.id,
                role=r.role,
                text=parts_to_text(r.parts_json),
                createdAt=r.created_at,
            )
            for r in rows
        ]


async def append_turn(
    session_id: str,
    role: str,
    text: str,
    *,
    model_label: str | None = None,
) -> None:
    """Persist one user/assistant text turn and refresh session metadata
    (auto-title from the first user message, last-message preview, ordering)."""
    await ensure_db()
    text = text.strip()
    now = _now_ms()
    async with _get_db().session() as s:
        dao = ChatDao(s)
        session = await dao.get_session(session_id)
        if session is None:
            return
        order = await dao.next_sort_order(session_id)
        await dao.insert_message(
            MessageEntity(
                id=uuid.uuid4().hex,
                session_id=session_id,
                role=role,
                parts_json=_text_parts(text),
                created_at=now,
                sort_order=order,
            )
        )
        label = model_label or session.model_id or "unset"
        await dao.update_session_model(session_id, label, updated_at=now)
        if role == "user":
            title = session.title
            if not title or title == TITLE_DEFAULT:
                await dao.update_session_title(
                    session_id, _one_line(text)[:_TITLE_LEN] or TITLE_DEFAULT, now
                )
        elif role == "assistant":
            await dao.update_last_message(
                session_id, _one_line(text)[:_PREVIEW_LEN] or None, now
            )


async def update_session_title(session_id: str, title: str) -> None:
    """Rename a session (kept for future rename affordance)."""
    await ensure_db()
    async with _get_db().session() as s:
        await ChatDao(s).update_session_title(
            session_id, _one_line(title)[:_TITLE_LEN] or TITLE_DEFAULT, _now_ms()
        )
