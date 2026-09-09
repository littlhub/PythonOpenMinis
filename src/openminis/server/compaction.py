"""Session compaction + memory extraction.

Kotlin decides *when* to compact from the context window (``ContextPolicy.kt``:
compact once the transcript comes within N tokens of the model's limit). That
needs a trustworthy token count, which the port doesn't have, so the Python
side uses the rule the user actually asked for and can predict: **compact every
20 turns**.

Every pass also distils durable facts into the daily memory log
(``memory/YYYY-MM-DD.md``). Neither the Kotlin nor the iOS original automates
that — both just rely on the model remembering to call ``memory_write`` — so
this is the port's "memory extraction" pass, and it is the reason compaction
here produces two artefacts instead of one.

Compaction is destructive to *context*, not to *data*: the folded originals
stay in the ``messages`` table and are still returned by history/export. Only
what gets sent to the model shrinks.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..core.logging import get_logger
from ..data.db.chat_dao import ChatDao
from ..data.db.compact_marker_entity import CompactMarkerEntity
from ..data.db.message_entity import MessageEntity
from ..data.model import LLMMessage, LLMStreamChunk
from . import chat_store

logger = get_logger(__name__)

__all__ = [
    "COMPACT_EVERY_TURNS",
    "CompactResult",
    "compact_session",
    "maybe_compact",
    "turns_since_last_compact",
]

#: Turns (user messages) that accumulate before the next compaction.
COMPACT_EVERY_TURNS = 20

#: The most recent messages are always kept verbatim so a fresh compaction
#: never folds away the turn the user is still looking at.
KEEP_RECENT_MESSAGES = 2

_SUMMARY_MAX_TOKENS = 4_096

_SYSTEM_PROMPT = (
    "你是一个会话压缩器。把给定的对话压缩成两样东西:一段可继续对话的摘要,"
    "以及值得长期保留的事实。只输出指定格式,不要寒暄或解释。"
)

_INSTRUCTIONS = """请按下面格式输出:

===SUMMARY===
一段中文摘要(200 字以内),覆盖:用户在做什么、已确认的结论、未完成的待办、
正在编辑的文件/路径、以及后续继续对话必须知道的约束。用第三人称或要点陈述,
不要复述寒暄。

===MEMORIES===
需要长期记住的事实,每条一行,以 "- " 开头;不超过 8 条;只包含跨会话仍然成立
的内容(用户偏好、项目约定、环境事实、已确认的结论)。不要写密码/密钥/令牌,
不要写一次性细节。没有值得保留的内容就留空。

以下是待压缩的对话:
"""


@dataclass(frozen=True)
class CompactResult:
    """Outcome of one compaction pass."""

    session_id: str
    compacted: int
    summary: str = ""
    memories: tuple[str, ...] = ()
    marker_id: str | None = None
    skipped: str | None = None

    @property
    def applied(self) -> bool:
        return self.marker_id is not None


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


# -- trigger ----------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """Rough token estimate for CJK-heavy transcripts.

    CJK characters ≈ 1 token each; latin runs ≈ 4 chars/token. Good enough
    for a compaction *budget*: it only decides *when* to fold, not how.
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E7F)
    return cjk + (len(text) - cjk) // 4


async def turns_since_last_compact(session_id: str) -> int:
    """User turns accumulated since the last marker (或会话开头)."""
    await chat_store.ensure_db()
    rows = await chat_store.load_raw_messages(session_id)
    marker = await chat_store.latest_compact_marker(session_id)
    start = chat_store.first_kept_index(rows, marker)
    return sum(1 for r in rows[start:] if r.role == "user")


async def maybe_compact(
    session_id: str,
    provider: Any = None,
    *,
    threshold: int = COMPACT_EVERY_TURNS,
    context_limit: int | None = None,
) -> CompactResult | None:
    """Compact when the session passed ``threshold`` turns since last time —
    or, when ``context_limit`` is given, when the kept transcript's estimated
    tokens reach ~80% of it. Either condition triggers the fold.

    Returns ``None`` when nothing happened (the common case) so callers can log
    only on an actual compaction. Errors are swallowed: a failed compaction must
    never break the turn the user is waiting for.
    """
    try:
        await chat_store.ensure_db()
        rows = await chat_store.load_raw_messages(session_id)
        marker = await chat_store.latest_compact_marker(session_id)
        start = chat_store.first_kept_index(rows, marker)
        recent = rows[start:]
        turns = sum(1 for r in recent if r.role == "user")
        if turns < threshold:
            if context_limit:
                transcript = "".join(
                    chat_store.parts_to_text(r.parts_json) for r in recent
                )
                if estimate_tokens(transcript) < int(context_limit) * 0.8:
                    return None
            else:
                return None
        return await compact_session(session_id, provider=provider)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("compaction skipped for %s: %s", session_id, e)
        return None


# -- compaction -------------------------------------------------------------
async def compact_session(session_id: str, provider: Any = None) -> CompactResult:
    """Fold the older part of a session into a summary + memory entries."""
    await chat_store.ensure_db()
    rows = await chat_store.load_raw_messages(session_id)
    marker = await chat_store.latest_compact_marker(session_id)
    start = chat_store.first_kept_index(rows, marker)
    end = max(start, len(rows) - KEEP_RECENT_MESSAGES)
    region = rows[start:end]

    if len(region) < 2:
        return CompactResult(
            session_id, 0, skipped="可压缩的消息太少", marker_id=None
        )

    owned_provider = provider is None
    if owned_provider:
        provider = await _build_provider()
    try:
        raw = await _summarize(provider, region)
    finally:
        if owned_provider:
            await _aclose(provider)

    summary, memories = _parse(raw)
    if not summary:
        return CompactResult(session_id, 0, skipped="模型没有返回摘要")

    first_kept: MessageEntity | None = rows[end] if end < len(rows) else None
    entity = CompactMarkerEntity(
        id=uuid.uuid4().hex,
        session_id=session_id,
        summary=summary,
        first_kept_sort_order=first_kept.sort_order if first_kept else 0,
        compacted_count=len(region),
        created_at=_now_ms(),
        first_kept_message_id=first_kept.id if first_kept else None,
        last_compacted_message_id=region[-1].id,
        version=2,
    )
    async with chat_store.database().session() as s:
        await ChatDao(s).insert_compact_marker(entity)

    written = _write_memories(session_id, memories)
    # The cached transcript still holds the folded原文 — rebuild it lazily.
    chat_store.drop_runtime(session_id)

    logger.info(
        "compacted %s: %d messages, %d memories, marker=%s",
        session_id, len(region), written, entity.id,
    )
    return CompactResult(
        session_id=session_id,
        compacted=len(region),
        summary=summary,
        memories=memories,
        marker_id=entity.id,
    )


# -- LLM --------------------------------------------------------------------
async def _build_provider() -> Any:
    from ..settings.chat_service import build_chat_setup
    from ..settings.store import SettingsStore

    provider, _runtime, _options, _identity, _conf = build_chat_setup(
        SettingsStore.get()
    )
    return provider


async def _aclose(provider: Any) -> None:
    close = getattr(provider, "aclose", None)
    if callable(close):
        try:
            await close()
        except Exception:  # pragma: no cover - defensive
            logger.debug("provider close failed", exc_info=True)


async def _summarize(provider: Any, region: list[MessageEntity]) -> str:
    transcript = "\n\n".join(
        f"{'用户' if r.role == 'user' else '助手'}: {chat_store.parts_to_text(r.parts_json).strip()}"
        for r in region
        if chat_store.parts_to_text(r.parts_json).strip()
    )
    messages = [LLMMessage(LLMMessage.Role.USER, f"{_INSTRUCTIONS}\n{transcript}")]

    out: list[str] = []
    stream = provider.stream_message(
        messages, _SYSTEM_PROMPT, _SUMMARY_MAX_TOKENS, None, tools=None
    )
    async for chunk in stream:
        if isinstance(chunk, LLMStreamChunk.Text):
            out.append(chunk.text)
    return "".join(out)


def _parse(raw: str) -> tuple[str, tuple[str, ...]]:
    """Split the model's answer into ``(summary, memories)``.

    Tolerates a missing/mis-cased marker: without ``===SUMMARY===`` we treat
    the whole answer as the summary rather than throwing the pass away.
    """
    text = (raw or "").strip()
    if not text:
        return "", ()

    upper = text.upper()
    s_tag, m_tag = "===SUMMARY===", "===MEMORIES==="
    s_at = upper.find(s_tag)
    m_at = upper.find(m_tag)

    if s_at == -1 and m_at == -1:
        return text, ()
    if s_at == -1:
        summary, memory_blob = "", text[m_at + len(m_tag) :]
    elif m_at == -1:
        summary, memory_blob = text[s_at + len(s_tag) :], ""
    else:
        summary = text[s_at + len(s_tag) : m_at]
        memory_blob = text[m_at + len(m_tag) :]

    memories: list[str] = []
    for line in memory_blob.splitlines():
        line = line.strip()
        if line.startswith(("-", "*", "•")):
            line = line[1:].strip()
        if line and not line.startswith("="):
            memories.append(line)
    return summary.strip(), tuple(memories)


# -- memory extraction ------------------------------------------------------
def _write_memories(session_id: str, memories: tuple[str, ...]) -> int:
    """Append extracted facts to today's memory log. Returns entries written."""
    if not memories:
        return 0
    from ..tools.memory_tools import MemoryTools

    body = "\n".join(f"- {m}" for m in memories)
    content = f"## 会话压缩记忆提取（会话 {session_id[:8]}）\n\n{body}\n"
    result = MemoryTools.execute_write(
        json.dumps(
            {"tool_title": "会话压缩记忆提取", "content": content},
            ensure_ascii=False,
        )
    )
    if not result.success:
        logger.warning("memory extraction failed: %s", result.output)
        return 0
    return len(memories)
