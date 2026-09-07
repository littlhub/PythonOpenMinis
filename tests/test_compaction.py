"""20-turn compaction + memory extraction.

Compaction folds older turns into a ``compact_markers`` summary and distils
durable facts into the daily memory log. These tests drive it with a stub
provider so no real model is ever called.
"""

from __future__ import annotations

import pytest

from openminis.core import context
from openminis.data.model import LLMStreamChunk
from openminis.server import chat_store, compaction


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated data dir + throwaway chat database."""
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    chat_store.set_database_path(tmp_path / "chat.db")
    yield tmp_path
    chat_store.set_database_path(None)
    context._context = None


class StubProvider:
    """Records the prompt it was given and yields one canned text chunk."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.transcripts: list[str] = []

    async def stream_message(self, messages, system_prompt, max_tokens, temperature,
                             tools=None, thinking_level=None):
        self.transcripts.append(messages[-1].content if messages else "")
        yield LLMStreamChunk.Text(self.reply)


REPLY = """===SUMMARY===
用户在移植 OpenMinis，已完成 config 与 data 模块，当前在补 server 层。
===MEMORIES===
- 项目：OpenMinis Kotlin→Python 移植，工作区在 data_dir/workspace
- 约定：测试用 uv 跑，隔离依赖 MINIS_HOME
"""


async def _seed(session_id: str, turns: int) -> None:
    for i in range(turns):
        await chat_store.append_turn(session_id, "user", f"用户第 {i} 轮")
        await chat_store.append_turn(session_id, "assistant", f"助手第 {i} 轮")


@pytest.mark.asyncio
async def test_turns_since_last_compact_counts_user_turns(env):
    await chat_store.ensure_db()
    sid = (await chat_store.create_session()).id
    await _seed(sid, 3)
    assert await compaction.turns_since_last_compact(sid) == 3


@pytest.mark.asyncio
async def test_maybe_compact_waits_for_the_threshold(env):
    await chat_store.ensure_db()
    sid = (await chat_store.create_session()).id
    await _seed(sid, compaction.COMPACT_EVERY_TURNS - 1)
    provider = StubProvider(REPLY)
    assert await compaction.maybe_compact(sid, provider) is None
    assert provider.transcripts == []
    assert await chat_store.latest_compact_marker(sid) is None


@pytest.mark.asyncio
async def test_compact_writes_marker_and_memories(env):
    await chat_store.ensure_db()
    sid = (await chat_store.create_session()).id
    await _seed(sid, compaction.COMPACT_EVERY_TURNS)

    provider = StubProvider(REPLY)
    result = await compaction.maybe_compact(sid, provider)

    assert result is not None and result.applied
    # 20 turns minus the 2 most recent messages kept verbatim
    assert result.compacted == compaction.COMPACT_EVERY_TURNS * 2 - 2
    assert result.memories == (
        "项目：OpenMinis Kotlin→Python 移植，工作区在 data_dir/workspace",
        "约定：测试用 uv 跑，隔离依赖 MINIS_HOME",
    )
    # The model saw the folded region only.
    assert "用户第 0 轮" in provider.transcripts[0]
    assert "用户第 19 轮" not in provider.transcripts[0]

    marker = await chat_store.latest_compact_marker(sid)
    assert marker is not None
    assert marker.version == 2
    assert marker.summary.startswith("用户在移植 OpenMinis")
    assert marker.last_compacted_message_id

    daily = (env / "memory").glob("*.md")
    bodies = "\n".join(p.read_text(encoding="utf-8") for p in daily)
    assert "会话压缩记忆提取" in bodies
    assert "约定：测试用 uv 跑" in bodies


@pytest.mark.asyncio
async def test_history_starts_at_marker_with_summary(env):
    await chat_store.ensure_db()
    sid = (await chat_store.create_session()).id
    await _seed(sid, compaction.COMPACT_EVERY_TURNS)
    await compaction.compact_session(sid, provider=StubProvider(REPLY))

    history = await chat_store.load_runtime_history(sid)
    assert history[0].content.startswith(chat_store.COMPACT_SUMMARY_PREFIX)
    assert "用户在移植 OpenMinis" in history[0].content
    joined = "\n".join(m.content for m in history)
    # folded away: the oldest turn, kept: the newest one
    assert "用户第 0 轮" not in joined
    assert "用户第 19 轮" in joined


@pytest.mark.asyncio
async def test_compact_skips_when_region_is_tiny(env):
    await chat_store.ensure_db()
    sid = (await chat_store.create_session()).id
    await chat_store.append_turn(sid, "user", "只有一轮")
    result = await compaction.compact_session(sid, provider=StubProvider(REPLY))
    assert not result.applied
    assert result.skipped


@pytest.mark.asyncio
async def test_compact_survives_a_silent_model(env):
    await chat_store.ensure_db()
    sid = (await chat_store.create_session()).id
    await _seed(sid, compaction.COMPACT_EVERY_TURNS)
    result = await compaction.compact_session(sid, provider=StubProvider(""))
    assert not result.applied
    assert await chat_store.latest_compact_marker(sid) is None
