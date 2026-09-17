"""[T-tool-cards-persist-and-fold] 工具输出「进上下文」的收敛策略。

背景（用户要求）：工具调用要以**折叠卡片**的形式留在每个会话里、可展开细看，
但它的内容**不该一直完整地占着模型上下文**。

原先这两件事都不成立：

* 前端只在 toolStart/toolEnd 帧到达时把卡片画出来，落库时只写最终一段助手
  文字 —— 刷新/切会话/重启后工具卡全没了；能看见的只有模型自己的复述。
* 而运行时那份真历史（``chat_store._RUNTIME``）里，每一轮的工具输出都是**完整**
  原文，一路带到压缩阈值（默认 30 轮 / 51 万 token）为止。一次 ``read_file``
  读个几万字符、一轮 shell 吐一大屏，上下文就这么被工具输出吃光。

这个模块解决第二件事：把**较旧**的工具输出换成一行说明（保留开头一小段当索引），
并给单条输出一个上限。注意三条硬约束：

1. **只改 content，不动结构** —— 每个 ``tool_use`` 必须仍配一个 ``tool_result``
   块，否则 provider 直接 400（Anthropic 尤其严格）。所以这里从不删块。
2. **幂等** —— 每轮出站都会调用一次；已经折叠/截断过的块打头有标记，不再套娃。
3. **落库那份不受影响** —— 折叠改的是**运行时消息对象**，用户界面上那张卡展示的
   是落库的原文，展开照样看得到完整输出（见 ``server/chat_store.parts_to_runs``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..core.logging import get_logger
from ..data.model import LLMMessage
from ..data.model.agent_content_part import ToolResult

logger = get_logger(__name__)

__all__ = [
    "FOLD_PREFIX",
    "TRUNC_PREFIX",
    "MARK_PREFIXES",
    "FoldStats",
    "fold_tool_outputs",
]

#: 折叠/截断后的正文都以它开头 —— 既让模型一眼看出「这里被我藏了」，
#: 也让下一次调用能识别「已经处理过」，从而保持幂等。
FOLD_PREFIX = "[工具输出已折叠"
TRUNC_PREFIX = "[工具输出已截断"
MARK_PREFIXES = (FOLD_PREFIX, TRUNC_PREFIX)

#: 折叠时保留的开头字符数 —— 就当给模型一个索引（看得出这段是什么），
#: 真要全文它会重新调工具。
FOLD_HEAD_CHARS = 240


@dataclass(slots=True)
class FoldStats:
    """本轮收敛的结果（日志与测试用）。"""

    folded: int = 0
    truncated: int = 0
    saved_chars: int = 0

    @property
    def touched(self) -> bool:
        return bool(self.folded or self.truncated)


def _iter_results(messages: Iterable[LLMMessage]) -> list[ToolResult]:
    out: list[ToolResult] = []
    for msg in messages:
        for part in msg.content_parts or []:
            if isinstance(part, ToolResult):
                out.append(part)
    return out


def _already_shaped(content: str) -> bool:
    return content.lstrip().startswith(MARK_PREFIXES)


def _folded(content: str, name: str) -> str:
    original = len(content)
    head = content.strip()[:FOLD_HEAD_CHARS]
    tail = "…" if original > FOLD_HEAD_CHARS else ""
    return (
        f"{FOLD_PREFIX}：{name} 原文 {original} 字符，早于最近几轮的输出，"
        f"这里只留开头 {len(head)} 字符给上下文让位。\n"
        f"需要完整内容请重新调用该工具（用户也能在会话里展开那张工具卡查看原文）。]\n"
        f"{head}{tail}"
    )


def _truncated(content: str, name: str, max_chars: int) -> str:
    original = len(content)
    head = content[:max_chars]
    return (
        f"{TRUNC_PREFIX}：{name} 原文 {original} 字符，超过单条上限 {max_chars}，"
        f"这里只保留前 {max_chars} 字符。\n"
        f"需要后面的内容请重新调用该工具并把范围缩小（如指定行区间/过滤条件）。]\n"
        f"{head}"
    )


def fold_tool_outputs(
    messages: list[LLMMessage],
    *,
    keep_recent: int = 6,
    max_chars: int = 8000,
) -> FoldStats:
    """就地收敛 ``messages`` 里的工具输出，返回统计。

    ``keep_recent`` —— 最近 N 条工具输出保持完整（0 或负数 = 完全不折叠）。
    ``max_chars``  —— 单条输出去上下文里的字符上限（0 = 不截断）。
    """
    stats = FoldStats()
    results = _iter_results(messages)
    if not results:
        return stats

    if keep_recent and keep_recent > 0 and len(results) > keep_recent:
        cutoff = len(results) - keep_recent
        for part in results[:cutoff]:
            content = part.content or ""
            if not content or _already_shaped(content):
                continue
            new = _folded(content, part.name or "工具")
            stats.saved_chars += len(content) - len(new)
            part.content = new
            stats.folded += 1

    if max_chars and max_chars > 0:
        for part in results:
            content = part.content or ""
            if len(content) <= max_chars or _already_shaped(content):
                continue
            new = _truncated(content, part.name or "工具", max_chars)
            stats.saved_chars += len(content) - len(new)
            part.content = new
            stats.truncated += 1

    if stats.touched:
        logger.info(
            "tool-context folded=%s truncated=%s saved_chars=%s",
            stats.folded, stats.truncated, stats.saved_chars,
        )
    return stats
