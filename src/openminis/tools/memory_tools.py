"""memory_write + memory_get tools.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/MemoryTools.kt
Original package: com.openminis.app.tools

PORT: the Kotlin implementation is paired with a ``MemoryRepository`` that
manages the on-disk ``memory/YYYY-MM-DD.md`` and ``memory/GLOBAL.md`` files.
This port recreates the file layout and the same two operations inline rather
than introducing a separate repository module — keeps the surface small
while preserving the read / write contract the agent uses.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.context import app_context
from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = [
    "MemoryTools",
    "MemoryWriteTool",
    "MemoryGetTool",
    "memory_write_definition",
    "memory_get_definition",
    "MEMORY_KINDS",
    "normalize_kind",
    "kind_paths",
    "ensure_memory_layout",
    "_memory_dir",
    "_knowledge_dir",
    "_daily_path",
    "_rules_path",
    "_wiki_dir",
    "_safe_topic",
]


#: Maximum characters returned by ``memory_get`` to keep tool results bounded.
#: T-android mirror — 8 KB is enough to surface a daily log without flooding
#: the model.
_MAX_GET_CHARS = 8_000

#: Context lines returned around a keyword match.
_KEYWORD_CONTEXT = 2


# ---------------------------------------------------------------------------
# Tool definitions — keep identical to Kotlin Anthropic schema
# ---------------------------------------------------------------------------
def memory_write_definition() -> AgentToolDefinition:
    return AgentToolDefinition(
        name="memory_write",
        description=(
            "Write a memory entry into persistent, searchable storage.\n"
            "记忆**只分五类**（scope，默认 daily）：\n"
            "- long_term — 长期记忆：跨会话仍成立的结论、项目约定、环境事实；\n"
            "- daily — 每日记忆：今天做了什么、当时的临时上下文；\n"
            "- rules — 特殊规则记忆：必须始终遵守的行为规则/硬性约束；\n"
            "- troubleshooting — 报错问题解决记忆：报错现象 → 根因 → 解法；\n"
            "- preferences — 用户偏好：用户个人的习惯、风格、禁忌。\n"
            "知识（可复用资料、专题文档）**不属于记忆**：用 scope=wiki 写进\n"
            "独立的知识库（knowledge/<topic>.md）。\n"
            "Memories persist across sessions and are keyword-searchable.\n"
            "avoid passwords/API keys/tokens/secrets unless the user "
            "explicitly confirms. Keep entries concise Markdown."
        ),
        parameters={
            "tool_title": AgentToolParam(
                "string",
                "A concise 5-10 word summary of what this tool call does, "
                "shown to the user (e.g. 'Save user preference for Python', "
                "'Note today's project context'). Use the same language as "
                "the user.",
            ),
            "content": AgentToolParam(
                "string",
                "The memory content to write. Use concise Markdown with a "
                "short heading (## Topic) and context about what was "
                "done/learned.",
            ),
            "scope": AgentToolParam(
                "string",
                "记忆分类：long_term / daily（默认）/ rules / troubleshooting "
                "/ preferences；另可用 wiki 写入知识库（不属于记忆五类）。",
                enum_values=[
                    "daily", "long_term", "rules", "troubleshooting",
                    "preferences", "wiki",
                ],
            ),
            "topic": AgentToolParam(
                "string",
                "Required when scope=wiki: the knowledge document name, e.g. "
                "'project-conventions'. Auto-created on first use.",
            ),
        },
        required=["tool_title", "content"],
        property_ordering=["tool_title", "content", "scope", "topic"],
    )


def memory_get_definition() -> AgentToolDefinition:
    return AgentToolDefinition(
        name="memory_get",
        description=(
            "Retrieve memories from persistent storage. Supports "
            "keyword-based fuzzy search across memory files. Searches the "
            "five memory kinds (daily logs, long-term, rules, troubleshooting, "
            "preferences) and can also read the knowledge base. Use this to "
            "recall previous knowledge, user preferences, or past notes "
            "before answering."
        ),
        parameters={
            "tool_title": AgentToolParam(
                "string",
                "A concise 5-10 word summary of what this tool call does, "
                "shown to the user (e.g. 'Recall user preferences', "
                "'Search past notes'). Use the same language as the user.",
            ),
            "scope": AgentToolParam(
                "string",
                "检索范围：'all'（默认，五类记忆全部）或某一类 "
                "daily / long_term / rules / troubleshooting / preferences；"
                "'knowledge' 只查知识库。",
                enum_values=[
                    "all", "daily", "long_term", "rules", "troubleshooting",
                    "preferences", "knowledge",
                ],
            ),
            "keywords": AgentToolParam(
                "string",
                "Space-separated keywords for fuzzy matching (e.g. 'python "
                "preference' or 'API key setup'). All keywords must appear in "
                "a line or its surrounding context for a match. Leave empty "
                "to return full memory files.",
            ),
        },
        required=["tool_title"],
        property_ordering=["tool_title", "scope", "keywords"],
    )


# ---------------------------------------------------------------------------
# Compatibility wrapper so existing build_tool_registry glue still works.
# ---------------------------------------------------------------------------
class MemoryWriteTool:
    NAME = "memory_write"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return memory_write_definition()

    @staticmethod
    def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        return MemoryTools.execute_write(args_json)


class MemoryGetTool:
    NAME = "memory_get"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return memory_get_definition()

    @staticmethod
    def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        return MemoryTools.execute_get(args_json)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _MemoryResult:
    output: str
    success: bool
    tool_title: str = ""


#: 记忆的五个分类 —— **记忆只分这五类**，知识（wiki 主题文档）不放这里，
#: 而是独立的 ``knowledge/`` 目录。两边彻底分开：记忆是「关于我和这段关系」，
#: 知识是「可复用的资料」。
MEMORY_KINDS: tuple[dict[str, str], ...] = (
    {
        "id": "long_term",
        "label": "长期记忆",
        "path": "long-term/LONG_TERM.md",
        "desc": "跨会话长期成立的结论、项目约定、环境事实、重要决策与原因",
    },
    {
        "id": "daily",
        "label": "每日记忆",
        "path": "daily/",
        "desc": "当天做了什么、当时的临时上下文（一天一个文件）",
    },
    {
        "id": "rules",
        "label": "特殊规则记忆",
        "path": "rules/RULES.md",
        "desc": "必须始终遵守的行为规则与硬性约束",
    },
    {
        "id": "troubleshooting",
        "label": "报错问题解决记忆",
        "path": "troubleshooting/TROUBLESHOOTING.md",
        "desc": "踩过的坑：报错现象 → 根因 → 解法（下次直接照着修）",
    },
    {
        "id": "preferences",
        "label": "用户偏好",
        "path": "preferences/USER.md",
        "desc": "用户的个人偏好、习惯、沟通风格与禁忌",
    },
)

#: 五类之外的输入别名 → 规范 id。
_KIND_ALIASES: dict[str, str] = {
    "long_term": "long_term", "long-term": "long_term", "longterm": "long_term",
    "global": "long_term", "长期": "long_term", "长期记忆": "long_term",
    "daily": "daily", "日志": "daily", "每日": "daily", "每日记忆": "daily",
    "rules": "rules", "rule": "rules", "规则": "rules", "特殊规则": "rules",
    "troubleshooting": "troubleshooting", "error": "troubleshooting",
    "errors": "troubleshooting", "fix": "troubleshooting",
    "报错": "troubleshooting", "报错问题": "troubleshooting", "问题解决": "troubleshooting",
    "preferences": "preferences", "preference": "preferences", "user": "preferences",
    "偏好": "preferences", "用户偏好": "preferences",
}


def normalize_kind(scope: str) -> str:
    """把用户/模型给的 scope 归一化成五类 id（未知 → ``daily``）。"""
    return _KIND_ALIASES.get((scope or "").strip().lower(), "daily")


#: 首次创建某类记忆文件时写的标题行（daily 不写标题，正文就是日志）。
_KIND_HEADERS: dict[str, str] = {
    "long_term": "# 长期记忆\n",
    "rules": "# RULES — 行为规则与长期偏好\n",
    "troubleshooting": "# 报错问题解决记忆\n",
    "preferences": "# 用户偏好\n",
}


def _memory_dir() -> Path:
    """``memory/`` 记忆根目录 —— 只放五类记忆。"""
    return app_context().data_dir / "memory"


def _knowledge_dir() -> Path:
    """``knowledge/`` 知识库 —— wiki 主题文档，与记忆分开存放。"""
    return app_context().data_dir / "knowledge"


def _daily_dir() -> Path:
    return _memory_dir() / "daily"


def _daily_path() -> Path:
    # 本地日期（北京时间）：原先用 UTC，晚上 8 点后写的日志会落到"明天"。
    today = datetime.now().strftime("%Y-%m-%d")
    return _daily_dir() / f"{today}.md"


def _long_term_path() -> Path:
    return _memory_dir() / "long-term" / "LONG_TERM.md"


def _preferences_path() -> Path:
    return _memory_dir() / "preferences" / "USER.md"


def _troubleshooting_path() -> Path:
    return _memory_dir() / "troubleshooting" / "TROUBLESHOOTING.md"


def _global_path() -> Path:
    """旧布局的 GLOBAL.md（迁移源；新写法一律走 long_term）。"""
    return _memory_dir() / "GLOBAL.md"


def _wiki_dir() -> Path:
    """旧布局的知识目录（迁移源）。新位置是 ``knowledge/``。"""
    return _memory_dir() / "wiki"


def _rules_path() -> Path:
    """The standing rulebook — behavioural rules + user preferences."""
    return _memory_dir() / "rules" / "RULES.md"


def kind_paths() -> dict[str, Path]:
    """五类记忆各自的落盘文件（daily 是目录，取今天的文件）。"""
    return {
        "long_term": _long_term_path(),
        "daily": _daily_path(),
        "rules": _rules_path(),
        "troubleshooting": _troubleshooting_path(),
        "preferences": _preferences_path(),
    }


def ensure_memory_layout() -> None:
    """建好五类目录，并把旧布局就地归位（幂等）。

    * ``memory/YYYY-MM-DD.md``  → ``memory/daily/``
    * ``memory/GLOBAL.md``      → ``memory/long-term/LONG_TERM.md``（追加）
    * ``memory/wiki/*.md``      → ``knowledge/``

    迁移只做「移动/追加」，不删内容；源不存在时什么都不做。
    """
    for p in kind_paths().values():
        p.parent.mkdir(parents=True, exist_ok=True)

    root = _memory_dir()
    # 旧每日日志 → daily/
    for old in root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"):
        target = _daily_dir() / old.name
        if not target.exists():
            try:
                old.replace(target)
            except OSError:  # pragma: no cover
                logger.warning("daily log migrate failed: %s", old)

    # 旧 GLOBAL.md → long-term/LONG_TERM.md（保序追加）
    legacy = _global_path()
    if legacy.is_file():
        body = legacy.read_text(encoding="utf-8", errors="replace").strip()
        if body:
            lt = _long_term_path()
            existing = lt.read_text(encoding="utf-8", errors="replace") if lt.is_file() else ""
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            block = f"# 长期记忆\n\n> 由 GLOBAL.md 迁移（{stamp}）。\n\n{body}\n"
            lt.write_text((existing + "\n\n" + block) if existing.strip() else block,
                          encoding="utf-8")
        try:
            legacy.unlink()
        except OSError:  # pragma: no cover
            pass

    # 旧 wiki → knowledge/（其中明显是「用户偏好」的按记忆归类收进 preferences/）
    wiki = _wiki_dir()
    if wiki.is_dir():
        kdir = _knowledge_dir()
        kdir.mkdir(parents=True, exist_ok=True)
        pref = _preferences_path()
        pref.parent.mkdir(parents=True, exist_ok=True)
        for p in sorted(wiki.glob("*.md")):
            if p.stem == "index":
                # 索引会被知识库重建，直接丢掉
                p.unlink(missing_ok=True)
                continue
            body = p.read_text(encoding="utf-8", errors="replace").strip()
            if _looks_like_preference(p.stem):
                existing = (
                    pref.read_text(encoding="utf-8", errors="replace")
                    if pref.is_file() else ""
                )
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                block = f"# 用户偏好\n\n> 由旧 wiki「{p.stem}」迁移（{stamp}）。\n\n{body}\n"
                pref.write_text(
                    (existing + "\n\n" + block) if existing.strip() else block,
                    encoding="utf-8",
                )
                p.unlink(missing_ok=True)
                continue
            target = kdir / p.name
            if target.exists():
                p.unlink(missing_ok=True)
                continue
            try:
                p.replace(target)
            except OSError:  # pragma: no cover
                logger.warning("knowledge migrate failed: %s", p)
        try:
            for leftover in wiki.glob("*"):
                leftover.unlink()
            wiki.rmdir()
        except OSError:  # pragma: no cover
            pass


#: 旧 wiki 里文件名带这些字样的，其实是「用户偏好」而不是知识 ——
#: 迁移时归到 preferences/USER.md（用户反馈：记忆和知识混在一起了）。
_PREFERENCE_HINTS = ("偏好", "习惯", "用户交互", "用户特点", "沟通风格", "喜好", "画像")


def _looks_like_preference(stem: str) -> bool:
    return any(h in stem for h in _PREFERENCE_HINTS)


def _label_for(path: Path) -> str:
    """给记忆/知识文件一个展示用的相对标签（memory 内用原相对路径，
    知识库带 ``knowledge/`` 前缀）。"""
    for root, prefix in ((_memory_dir(), ""), (_knowledge_dir(), "knowledge/")):
        try:
            return prefix + path.relative_to(root).as_posix()
        except ValueError:
            continue
    return path.name


def _safe_topic(topic: str) -> str:
    """Turn a free-text wiki topic into a safe file stem."""
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", topic.strip())
    cleaned = cleaned.strip(".-_")
    return cleaned or "untitled"


def _target_path(scope: str, topic: str) -> tuple[Path, bool]:
    """Resolve the storage file for a scope. ``fresh`` = first creation.

    五类记忆各有固定落点；``wiki`` 不再是记忆分类 —— 它写到独立的
    ``knowledge/<topic>.md``（知识库），保持旧调用可用。
    """
    raw = (scope or "daily").strip().lower()
    if raw == "wiki":
        if not topic.strip():
            raise ValueError("scope=wiki 需要提供 topic")
        p = _knowledge_dir() / f"{_safe_topic(topic)}.md"
        return p, not p.exists()
    kind = normalize_kind(raw)
    p = kind_paths()[kind]
    return p, not p.exists()



class MemoryTools:
    """Namespace mirroring the Kotlin ``object MemoryTools``."""

    @staticmethod
    def execute_write(args_json: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
            content = str(args.get("content", ""))
            tool_title = str(args.get("tool_title", "memory_write"))
            if not content.strip():
                return ToolExecutionResult(
                    "Error: Missing required 'content' parameter",
                    False,
                    tool_title=tool_title,
                )
            scope = str(args.get("scope", "daily") or "daily")
            topic = str(args.get("topic", "") or "")
            is_wiki = scope.strip().lower() == "wiki"
            kind = "wiki" if is_wiki else normalize_kind(scope)
            try:
                path, fresh = _target_path(scope, topic)
            except ValueError as e:
                return ToolExecutionResult(str(e), False, tool_title=tool_title)

            path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            header = ""
            if fresh:
                if is_wiki:
                    header = f"# {topic.strip()}\n"
                else:
                    header = _KIND_HEADERS.get(kind, "")
            entry = f"\n## {timestamp}\n\n{content.rstrip()}\n"

            if kind == "daily":
                # Prepend so the freshest entry is at the top of the file.
                existing = path.read_text(encoding="utf-8") if path.exists() else ""
                block = entry if existing else entry.strip() + "\n"
                path.write_text(block + existing, encoding="utf-8")
            else:
                # 其余四类与知识文档按时间追加。
                existing = path.read_text(encoding="utf-8") if path.exists() else ""
                if existing:
                    path.write_text(existing + entry, encoding="utf-8")
                else:
                    path.write_text(header + entry.strip() + "\n", encoding="utf-8")
            label = (
                f"knowledge/{path.name}" if is_wiki
                else path.relative_to(_memory_dir()).as_posix()
            )
            return ToolExecutionResult(
                f"Memory saved to {label} ({len(content)} chars)",
                True,
                tool_title=tool_title,
            )
        except Exception as e:
            logger.exception("memory_write failed")
            return ToolExecutionResult(f"Error: {e}", False)

    @staticmethod

    def execute_get(args_json: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
            keywords = str(args.get("keywords", "")).strip()
            scope = str(args.get("scope", "all")).strip().lower()
            tool_title = str(args.get("tool_title", "memory_get"))

            scope_l = (scope or "all").strip().lower()
            files: list[Path]
            if scope_l == "knowledge":
                kdir = _knowledge_dir()
                files = (
                    sorted(p for p in kdir.rglob("*.md") if p.is_file())
                    if kdir.is_dir() else []
                )
            elif scope_l in ("", "all", "memory"):
                # 'all' = 五类记忆（daily 日志、long-term、rules、
                # troubleshooting、preferences）+ SOUL.md。
                root = _memory_dir()
                files = (
                    sorted(p for p in root.rglob("*.md") if p.is_file())
                    if root.is_dir() else []
                )
            else:
                files = [kind_paths()[normalize_kind(scope_l)]]

            chunks: list[str] = []
            total = 0
            kw_re = _compile_keyword_re(keywords) if keywords else None

            for f in files:
                if not f.exists():
                    continue
                text = f.read_text(encoding="utf-8")
                if kw_re is None:
                    body = text
                else:
                    body = _keyword_filter(text, kw_re)
                if not body.strip():
                    continue
                label = _label_for(f)
                chunk = f"--- {label} ---\n{body.rstrip()}\n"
                if total + len(chunk) > _MAX_GET_CHARS:
                    remaining = _MAX_GET_CHARS - total
                    if remaining <= 0:
                        break
                    chunk = chunk[:remaining] + "\n…(truncated)"
                    chunks.append(chunk)
                    total += len(chunk)
                    break
                chunks.append(chunk)
                total += len(chunk)

            if not chunks:
                if keywords:
                    return ToolExecutionResult(
                        f"No memories matched keywords: {keywords!r}",
                        True,
                        tool_title=tool_title,
                    )
                return ToolExecutionResult(
                    "No memories yet.", True, tool_title=tool_title
                )
            return ToolExecutionResult("\n".join(chunks), True, tool_title=tool_title)
        except Exception as e:
            logger.exception("memory_get failed")
            return ToolExecutionResult(f"Error: {e}", False)


# ---------------------------------------------------------------------------
# Keyword search helpers
# ---------------------------------------------------------------------------
def _compile_keyword_re(keywords: str) -> re.Pattern[str]:
    """All keywords must appear in the same line OR its surrounding context
    window (mirrors the Kotlin fuzzy matcher). Case-insensitive, word-boundary
    free so Chinese lines match too.
    """
    parts = [_escape_re(k) for k in keywords.split() if k.strip()]
    if not parts:
        return re.compile(r"$^")  # matches nothing
    return re.compile("|".join(f"(?=.*?{p})" for p in parts), re.IGNORECASE | re.DOTALL)


def _keyword_filter(text: str, pattern: re.Pattern[str]) -> str:
    """Return the lines that match (and ``_KEYWORD_CONTEXT`` neighbours) so
    short snippets stay readable when the file is large.
    """
    lines = text.splitlines()
    keep = set()
    for i, line in enumerate(lines):
        if pattern.search(line):
            for j in range(max(0, i - _KEYWORD_CONTEXT), min(len(lines), i + _KEYWORD_CONTEXT + 1)):
                keep.add(j)
    return "\n".join(lines[i] for i in sorted(keep))


def _escape_re(s: str) -> str:
    return re.escape(s)
