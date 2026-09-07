"""Periodic memory organisation: daily logs → wiki topics + standing rules.

Long-term maintenance pass for the memory store. Given the accumulated daily
logs, the current rulebook and the existing wiki corpus, an LLM pass produces:

* ``memory/rules/RULES.md`` — the standing rulebook (behavioural rules +
  user preferences the agent must always follow), rebuilt from the union of
  what was already there and what the logs surfaced;
* ``memory/wiki/<topic>.md`` — long-lived topic documents, one file per
  topic. Each file is a compact RAG corpus chunk; multi-file layout keeps
  retrieval granular.

The pass is **idempotent-ish**: it always regenerates both artefacts from the
full current state, so re-running is safe. Source daily logs are *not*
deleted — they remain the raw record; organisation is a distillation on top.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.logging import get_logger
from ..data.model import LLMMessage, LLMStreamChunk
from ..tools.memory_tools import (
    _memory_dir,
    _rules_path,
    _safe_topic,
    _wiki_dir,
)

logger = get_logger(__name__)

__all__ = ["OrganizeResult", "organize_memories", "candidate_daily_logs"]

#: How much context the LLM pass ingests before writing the corpus.
_MAX_SOURCE_CHARS = 40_000
_MAX_DAILY_LOGS = 30
_MAX_WIKI_CHARS = 16_000
_MAX_RULES_CHARS = 8_000
_MAX_TOKENS = 6_000

_SYSTEM_PROMPT = (
    "你是记忆整理员。把零散记忆整理成 1) 必须长期遵守的行为规则 2) 按主题组织"
    "的 wiki 知识。只输出指定格式，不要解释。用中文。"
)

_INSTRUCTIONS = """下面是待整理的内容：
- 现有规则（RULES.md）
- 现有长期知识（wiki 各主题文档）
- 近 30 天的日常日志

请输出严格格式：

===RULES===
行为规则与用户偏好，每条一行，以 "- " 开头。先保留仍有效的既有规则，
再补充日志里新出现的、跨会话成立的规则；去重。无则留空。

===WIKI===
长期知识按主题组织为若干段落，每段以 "## 主题名" 开头，随后是要点式正文。
- 主题之间合并重叠、去重；每主题 3~10 行要点；
- 覆盖：项目约定、用户环境、决策与原因、可复用方法、踩坑结论；
- 只保留跨会话仍成立的内容，不写一次性细节与密钥。

请开始输出：
"""


@dataclass(frozen=True)
class OrganizeResult:
    logs_read: int
    rules_lines: int
    wiki_files: list[str]
    skipped: str | None = None

    @property
    def applied(self) -> bool:
        return self.wiki_files or self.rules_lines


def candidate_daily_logs(root: Path | None = None) -> list[Path]:
    """Daily logs living directly under ``memory/`` (YYYY-MM-DD.md)."""
    root = Path(root) if root else _memory_dir()
    if not root.is_dir():
        return []
    logs = [
        p
        for p in root.glob("[0-9][0-9][0-9][0-9]-*.md")
        if re.match(r"^\d{4}-\d{2}-\d{2}\.md$", p.name)
    ]
    logs.sort(reverse=True)
    return logs[:_MAX_DAILY_LOGS]


def _read_bounded(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover
        return ""
    return text[-limit:] if len(text) > limit else text


def _build_source() -> tuple[str, list[Path]]:
    parts: list[str] = []
    logs: list[Path] = []
    budget = _MAX_SOURCE_CHARS

    rules = _rules_path()
    if rules.is_file():
        parts.append(f"【现有规则 RULES.md】\n{_read_bounded(rules, _MAX_RULES_CHARS)}")

    wiki = _wiki_dir()
    if wiki.is_dir():
        for p in sorted(wiki.glob("*.md")):
            chunk = _read_bounded(p, _MAX_WIKI_CHARS // max(1, len(list(wiki.glob('*.md')))))
            if chunk:
                parts.append(f"【wiki 文档 {p.name}】\n{chunk}")

    for p in candidate_daily_logs():
        body = _read_bounded(p, 12_000)
        if not body:
            continue
        spent = sum(len(x) for x in parts)
        if spent + len(body) > budget:
            break
        parts.append(f"【日志 {p.name}】\n{body}")
        logs.append(p)

    return "\n\n".join(parts), logs


def _parse(raw: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Split the model answer into (rules, [(topic, body), ...])."""
    text = (raw or "").strip()
    if not text:
        return [], []
    upper = text.upper()
    r_at = upper.find("===RULES===")
    w_at = upper.find("===WIKI===")
    if r_at == -1 and w_at == -1:
        return [], []

    rules_blob = ""
    wiki_blob = ""
    if r_at != -1 and w_at == -1:
        rules_blob = text[r_at + len("===RULES==="):]
    elif w_at != -1 and r_at == -1:
        wiki_blob = text[w_at + len("===WIKI==="):]
    else:
        rules_blob = text[r_at + len("===RULES==="):w_at]
        wiki_blob = text[w_at + len("===WIKI==="):]

    rules: list[str] = []
    for line in rules_blob.splitlines():
        line = line.strip()
        if line.startswith(("-", "*", "•")):
            line = line[1:].strip()
        if line and not line.startswith("="):
            rules.append(line)

    topics: list[tuple[str, str]] = []
    current_title: str | None = None
    current_lines: list[str] = []
    for line in wiki_blob.splitlines():
        m = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if m:
            if current_title and current_lines:
                topics.append((current_title, "\n".join(current_lines).strip()))
            current_title = m.group(1).strip()
            current_lines = []
        elif current_title:
            current_lines.append(line)
    if current_title and current_lines:
        topics.append((current_title, "\n".join(current_lines).strip()))
    return rules, topics


def _write_rules(rules: list[str]) -> int:
    if not rules:
        return 0
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head = "# RULES — 行为规则与长期偏好\n\n"
    head += f"> 由记忆整理自动生成（{stamp}）；编辑请通过 记忆页 或 memory_write(rules)。\n\n"
    body = "\n".join(f"- {r}" for r in rules)
    path.write_text(head + body + "\n", encoding="utf-8")
    return len(rules)


def _write_wiki(topics: list[tuple[str, str]]) -> list[str]:
    written: list[str] = []
    wiki = _wiki_dir()
    wiki.mkdir(parents=True, exist_ok=True)
    for title, body in topics:
        if not body.strip():
            continue
        fname = f"{_safe_topic(title)}.md"
        content = f"# {title}\n\n{body.strip()}\n"
        (wiki / fname).write_text(content, encoding="utf-8")
        written.append(f"wiki/{fname}")
    _refresh_index()
    return sorted(set(written))


def _refresh_index() -> None:
    """Rebuild wiki/index.md from the current topic files (each file = one
    retrievable corpus chunk — the index the knowledge-wiki skill consults)."""
    wiki = _wiki_dir()
    if not wiki.is_dir():
        return
    lines = ["# 知识库索引", "", "> 自动生成：memory 整理 / knowledge-wiki 技能每次写入后更新。", ""]
    for p in sorted(wiki.glob("*.md")):
        if p.name == "index.md":
            continue
        title = ""
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines()[:5]:
            if raw.startswith("# "):
                title = raw[2:].strip()
                break
        lines.append(f"- [{title or p.stem}]({p.name})")
    if len(lines) > 4:
        (wiki / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _summarize(provider: Any, source: str) -> str:
    messages = [LLMMessage(LLMMessage.Role.USER, f"{_INSTRUCTIONS}\n\n{source}")]
    out: list[str] = []
    stream = provider.stream_message(
        messages, _SYSTEM_PROMPT, _MAX_TOKENS, None, tools=None
    )
    async for chunk in stream:
        if isinstance(chunk, LLMStreamChunk.Text):
            out.append(chunk.text)
    return "".join(out)


async def _build_provider() -> Any:
    from ..settings.chat_service import build_chat_setup
    from ..settings.store import SettingsStore

    provider, _r, _o, _i, _c = build_chat_setup(SettingsStore.get())
    return provider


async def _aclose(provider: Any) -> None:
    close = getattr(provider, "aclose", None)
    if callable(close):
        try:
            await close()
        except Exception:  # pragma: no cover - defensive
            logger.debug("provider close failed", exc_info=True)


async def organize_memories(provider: Any = None) -> OrganizeResult:
    """Run one organisation pass over the memory store."""
    source, logs = _build_source()
    if not source.strip():
        return OrganizeResult(0, 0, [], skipped="没有可整理的记忆")

    owned = provider is None
    if owned:
        provider = await _build_provider()
    try:
        raw = await _summarize(provider, source)
    finally:
        if owned:
            await _aclose(provider)

    rules, topics = _parse(raw)
    if not rules and not topics:
        return OrganizeResult(len(logs), 0, [], skipped="模型没有返回整理结果")

    rule_count = _write_rules(rules)
    wiki_files = _write_wiki(topics)
    logger.info(
        "memory organised: %d logs, %d rules, %d wiki files",
        len(logs), rule_count, len(wiki_files),
    )
    return OrganizeResult(len(logs), rule_count, wiki_files)
