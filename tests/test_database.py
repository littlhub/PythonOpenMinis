"""Tests for the ported Room database layer."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from openminis.data.db.app_database import DATABASE_VERSION, AppDatabase
from openminis.data.db.chat_session_entity import ChatSessionEntity
from openminis.data.db.folder_entity import FolderEntity
from openminis.data.db.message_entity import MessageEntity


@pytest.fixture
def db() -> AppDatabase:
    return AppDatabase(":memory:")


def test_fresh_database_stamps_version(db: AppDatabase) -> None:
    async def run() -> int:
        await db.initialize()
        async with db.engine.begin() as conn:
            from sqlalchemy import text

            result = await conn.execute(text("PRAGMA user_version"))
            return int(result.scalar() or 0)

    assert asyncio.run(run()) == DATABASE_VERSION


def test_session_and_message_roundtrip(db: AppDatabase) -> None:
    async def run() -> tuple[int, int]:
        await db.initialize()
        async with db.session() as s:
            s.add(
                ChatSessionEntity(
                    id="s1", title="t", model_id="m", created_at=1, updated_at=2
                )
            )
            await s.commit()
            s.add(
                MessageEntity(
                    id="m1",
                    session_id="s1",
                    role="user",
                    parts_json="[]",
                    created_at=1,
                    sort_order=0,
                )
            )
            await s.commit()
            sessions = (await s.execute(select(ChatSessionEntity))).scalars().all()
            messages = (await s.execute(select(MessageEntity))).scalars().all()
            return len(sessions), len(messages)

    assert asyncio.run(run()) == (1, 1)


def test_folder_pinning_semantics(db: AppDatabase) -> None:
    async def run() -> tuple[bool, bool]:
        await db.initialize()
        async with db.session() as s:
            plain = FolderEntity(id="f1", name="Work", created_at=1, updated_at=1)
            pinned = FolderEntity(
                id="f2", name="Pinned", pinned_at=99, created_at=1, updated_at=1
            )
            s.add_all([plain, pinned])
            await s.commit()
            rows = (await s.execute(select(FolderEntity))).scalars().all()
            by_id = {r.id: r for r in rows}
            return by_id["f1"].is_pinned, by_id["f2"].is_pinned

    assert asyncio.run(run()) == (False, True)


def test_cascade_delete_removes_messages(db: AppDatabase) -> None:
    """ForeignKey(ondelete=CASCADE) mirrors the Room @Entity declaration."""

    async def run() -> int:
        await db.initialize()
        async with db.session() as s:
            s.add(
                ChatSessionEntity(
                    id="s1", title="t", model_id="m", created_at=1, updated_at=2
                )
            )
            await s.commit()
            s.add(
                MessageEntity(
                    id="m1",
                    session_id="s1",
                    role="user",
                    parts_json="[]",
                    created_at=1,
                    sort_order=0,
                )
            )
            await s.commit()

            from sqlalchemy import delete

            await s.execute(delete(ChatSessionEntity).where(ChatSessionEntity.id == "s1"))
            await s.commit()
            remaining = (await s.execute(select(MessageEntity))).scalars().all()
            return len(remaining)

    assert asyncio.run(run()) == 0
