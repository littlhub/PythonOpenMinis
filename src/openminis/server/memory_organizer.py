"""Periodic memory organisation: daily logs → the four durable memory kinds.

记忆**只分五类**（用户定义）：

1. 长期记忆 ``memory/long-term/LONG_TERM.md``
2. 每日记忆 ``memory/daily/YYYY-MM-DD.md``（原始日志，整理器**不改写**它）
3. 特殊规则记忆 ``memory/rules/RULES.md``
4. 报错问题解决记忆 ``memory/troubleshooting/TROUBLESHOOTING.md``
5. 用户偏好 ``memory/preferences/USER.md``

整理器把每日日志里**跨会话仍成立**的内容蒸馏进第 1、3、4、5 类，每类一个
文件、覆盖式重建（输入里带着既有内容，所以等于「合并去重」而不是丢历史）。

知识（可复用的专题资料）**不属于记忆**，放在独立的 ``knowledge/`` 目录，
由知识库那条线单独维护 —— 这里不再产出 wiki。

每日日志本身不删：它是原始记录，蒸馏只是叠加在上面。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.logging import get_logger
from ..data.model import LLMMessage, LLMStreamChunk
from ..tools.memory_tools import (
    _KIND_HEADERS,
    _memory_dir,
    ensure_memory_layout,
    kind_paths,
)

logger = get_logger(__name__)

__all__ = [
    "OrganizeResult",
    "organize_memories",
    "candidate_daily_logs",
    "note_user_message",
    "message_organize_due",
    "run_message_organize_if_due",
    "mark_organized",
    "auto_organize_due",
    "run_auto_organize_once",
    "auto_organize_loop",
]

#: How much context the LLM pass ingests before writing the corpus.
_MAX_SOURCE_CHARS = 40_000
_MAX_DAILY_LOGS = 30
_MAX_KIND_CHARS = 8_000
_MAX_TOKENS = 6_000

#: 自动整理节流：距上次整理至少隔 20h，且此后有新的每日日志才真正调模型。
#: 检查本身很便宜（只看文件 mtime），后台循环每 6h 醒一次。
_MIN_INTERVAL_SECONDS = 20 * 3600
_AUTO_INTERVAL_SECONDS = 6 * 3600
_STAMP_NAME = ".last-organize"
#: 按提问数触发的状态文件：记录「自上次整理以来的用户提问数」。
_STATE_NAME = ".organize-state.json"

#: 整理器负责的四类（每日记忆是输入，不改写）。
_SECTION_KIND: dict[str, str] = {
    "LONG_TERM": "long_term",
    "RULES": "rules",
    "TROUBLESHOOTING": "troubleshooting",
    "PREFERENCES": "preferences",
}

_SYSTEM_PROMPT = (
    "你是记忆整理员。把零散日志蒸馏进四类长期记忆：长期记忆、特殊规则记忆、"
    "报错问题解决记忆、用户偏好。只输出指定格式，不要解释。用中文。"
)

_INSTRUCTIONS = """下面是待整理的内容：
- 现有的各类记忆文件
- 近 30 天的每日记忆（日志）

记忆**只分五类**（每日记忆=日志原文，不由你改写）：长期记忆、特殊规则记忆、
报错问题解决记忆、用户偏好。请把日志里**跨会话仍成立**的内容蒸馏进这四类，
输出严格格式（保留仍有效的既有条目，补充新出现的，去重）：

===LONG_TERM===
每行一条，以 "- " 开头：项目约定、环境事实、重要决策与原因、可复用结论。

===RULES===
每行一条，以 "- " 开头：必须始终遵守的行为规则与硬性约束。

===TROUBLESHOOTING===
每行一条，以 "- " 开头，三段式：现象：… → 根因：… → 解法：…。

===PREFERENCES===
每行一条，以 "- " 开头：用户的个人偏好、习惯、沟通风格与禁忌。

注意：不写一次性细节与密钥；不写可复用的专题资料/教程（那属于知识库，
不在这里，也不要输出知识类内容）。无内容的部分留空即可。

请开始输出：
"""


@dataclass(frozen=True)
class OrganizeResult:
    logs_read: int
    kinds: dict[str, int] = field(default_factory=dict)
    skipped: str | None = None

    @property
    def rules_lines(self) -> int:
        """兼容旧接口：特殊规则条数。"""
        return int(self.kinds.get("rules", 0))

    @property
    def applied(self) -> bool:
        return any(self.kinds.values())


def candidate_daily_logs(root: Path | None = None) -> list[Path]:
    """每日记忆（``YYYY-MM-DD.md``）：优先 ``memory/daily/``，兼容旧布局根部。"""
    base = Path(root) if root else _memory_dir()
    found: dict[str, Path] = {}
    for d in (base / "daily", base):
        if not d.is_dir():
            continue
        for p in d.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"):
            if re.match(r"^\d{4}-\d{2}-\d{2}\.md$", p.name):
                found.setdefault(p.name, p)
    logs = [found[k] for k in sorted(found, reverse=True)]
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

    for kind, path in kind_paths().items():
        if kind == "daily" or not path.is_file():
            continue
        chunk = _read_bounded(path, _MAX_KIND_CHARS)
        if chunk:
            label = _KIND_HEADERS.get(kind, kind).strip("# \n")
            parts.append(f"【现有 {label}（{path.name}）】\n{chunk}")

    for p in candidate_daily_logs():
        body = _read_bounded(p, 12_000)
        if not body:
            continue
        spent = sum(len(x) for x in parts)
        if spent + len(body) > budget:
            break
        parts.append(f"【每日记忆 {p.name}】\n{body}")
        logs.append(p)

    return "\n\n".join(parts), logs


def _parse_sections(raw: str) -> dict[str, list[str]]:
    """把模型回答按 ``===KIND===`` 段落切成 {记忆分类: [条目…]}。"""
    text = (raw or "").strip()
    out: dict[str, list[str]] = {k: [] for k in _SECTION_KIND}
    if not text:
        return out
    upper = text.upper()
    marks: list[tuple[int, str]] = []
    for key in _SECTION_KIND:
        at = upper.find(f"==={key}===")
        if at != -1:
            marks.append((at, key))
    marks.sort()
    for i, (at, key) in enumerate(marks):
        start = at + len(f"==={key}===")
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        for line in text[start:end].splitlines():
            line = line.strip()
            if line.startswith(("-", "*", "•")):
                line = line[1:].strip()
            if line and not line.startswith("="):
                out[key].append(line)
    return out


def _write_kind(kind: str, lines: list[str]) -> int:
    """覆盖式写一类记忆文件（输入含既有内容 → 等效合并去重）。"""
    lines = [x.strip() for x in lines if x.strip()]
    if not lines:
        return 0
    path = kind_paths()[kind]
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head = _KIND_HEADERS.get(kind, f"# {kind}\n")
    head += f"\n> 由记忆整理自动生成（{stamp}）；编辑请通过「记忆」页或 memory_write。\n\n"
    body = "\n".join(f"- {x}" for x in lines)
    path.write_text(head + body + "\n", encoding="utf-8")
    return len(lines)


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
    ensure_memory_layout()  # 旧布局先归位（幂等），避免把根目录日志漏掉
    source, logs = _build_source()
    if not source.strip():
        return OrganizeResult(0, {}, skipped="没有可整理的记忆")

    owned = provider is None
    if owned:
        provider = await _build_provider()
    try:
        raw = await _summarize(provider, source)
    finally:
        if owned:
            await _aclose(provider)

    sections = _parse_sections(raw)
    if not any(sections.values()):
        return OrganizeResult(len(logs), {}, skipped="模型没有返回整理结果")

    kinds: dict[str, int] = {}
    for key, lines in sections.items():
        kind = _SECTION_KIND[key]
        count = _write_kind(kind, lines)
        if count:
            kinds[kind] = count
    logger.info(
        "memory organised: %d logs → %s",
        len(logs), kinds or "(no change)",
    )
    return OrganizeResult(len(logs), kinds)


# ---------------------------------------------------------------------------
# 自动整理：没有它每日日志只会一直堆着，没人点「整理」按钮就永远不蒸馏
# ---------------------------------------------------------------------------
def _stamp_path(root: Path | None = None) -> Path:
    return (Path(root) if root else _memory_dir()) / _STAMP_NAME


def auto_organize_due(root: Path | None = None) -> bool:
    """是否该跑一次整理：有比上次整理更新的每日日志、且距上次 ≥20h。

    没有每日日志时永远不跑（没什么可整理）；从没整理过则第一次必跑。
    """
    base = Path(root) if root else _memory_dir()
    logs = candidate_daily_logs(base)
    if not logs:
        return False
    try:
        last = _stamp_path(base).stat().st_mtime
    except OSError:
        return True  # 从没整理过
    if time.time() - last < _MIN_INTERVAL_SECONDS:
        return False
    newest = max(p.stat().st_mtime for p in logs)
    return newest > last


def mark_organized(root: Path | None = None) -> None:
    """盖戳：记录这次整理的时间，并把「提问计数」清零（两类触发共用）。"""
    p = _stamp_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8"
    )
    _write_state(root, {"messages_since": 0})


def _state_path(root: Path | None = None) -> Path:
    return (Path(root) if root else _memory_dir()) / _STATE_NAME


def _read_state(root: Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(_state_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(root: Path | None, state: dict[str, Any]) -> None:
    p = _state_path(root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:  # pragma: no cover - 计数失败不该打断对话
        logger.debug("organize state write failed", exc_info=True)


def note_user_message(root: Path | None = None) -> int:
    """用户提问计数 +1，返回自上次整理以来的累计提问数。"""
    state = _read_state(root)
    count = int(state.get("messages_since", 0)) + 1
    state["messages_since"] = count
    _write_state(root, state)
    return count


def message_organize_due(every: int, root: Path | None = None) -> bool:
    """达到设定提问数（``agent.memoryOrganizeEvery``，0=关闭）就该整理。"""
    if every <= 0:
        return False
    return int(_read_state(root).get("messages_since", 0)) >= every


async def run_message_organize_if_due(
    every: int, root: Path | None = None
) -> OrganizeResult | None:
    """提问数触发的一次整理：没到期返回 None；失败保留计数下轮重试。"""
    if not message_organize_due(every, root):
        return None
    try:
        result = await organize_memories()
    except Exception:  # pragma: no cover - 取决于模型配置
        logger.info(
            "message-triggered memory organise failed", exc_info=True
        )
        return None
    mark_organized(root)
    return result


async def run_auto_organize_once(
    root: Path | None = None,
) -> OrganizeResult | None:
    """到期才整理一次；失败不盖戳，留给下个周期重试。"""
    if not auto_organize_due(root):
        return None
    try:
        result = await organize_memories()
    except Exception:  # pragma: no cover - 取决于模型配置，不该打断循环
        logger.info(
            "auto memory organise failed; will retry next cycle",
            exc_info=True,
        )
        return None
    mark_organized(root)
    return result


async def auto_organize_loop(interval_seconds: int = _AUTO_INTERVAL_SECONDS) -> None:
    """后台循环：启动即查一次，之后每 6h 查一次是否到期。绝不该抛异常。"""
    while True:
        try:
            result = await run_auto_organize_once()
        except Exception:  # pragma: no cover - 后台循环绝不能死
            logger.debug("auto organise loop error", exc_info=True)
        else:
            if result is not None:
                logger.info(
                    "auto memory organise: %d logs -> %s",
                    result.logs_read,
                    result.kinds or "(no change)",
                )
        await asyncio.sleep(interval_seconds)
