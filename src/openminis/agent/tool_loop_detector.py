"""Tool-loop detector — prevents the LLM from spinning on pointless repeats.

Ported from: src/android/app/src/main/java/com/openminis/app/agent/ToolLoopDetector.kt
Original package: com.openminis.app.agent
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from ..core.logging import get_logger

logger = get_logger("agent.tool_loop_detector")

__all__ = [
    "ToolCallRecord", "LoopLevel", "LoopCheckResult", "ToolLoopConfig",
    "ToolLoopDetector",
]


#: Param keys excluded from ``args_hash_for``. ``tool_title`` is a required UI
#: label that models commonly counter-suffix ("Read X #1", "#2", ...); without
#: this filter every logically-identical call hashes unique and the repeat /
#: circuit-breaker strategies silently fail.
ARGS_HASH_IGNORED_KEYS: frozenset[str] = frozenset({"tool_title"})

_UNKNOWN_TOOL_RE_1 = re.compile(
    r"""unknown tool[:\s]+["']?([a-zA-Z0-9_.\-]+)["']?""",
    re.IGNORECASE,
)
_UNKNOWN_TOOL_RE_2 = re.compile(
    r"""tool\s+["']?([a-zA-Z0-9_.\-]+)["']?\s+(?:not found|is not available)""",
    re.IGNORECASE,
)

_HEX = "0123456789abcdef"


@dataclass(frozen=True)
class ToolCallRecord:
    """One executed (or blocked) tool call inside the sliding window.

    PORT: Kotlin uses ``System.currentTimeMillis()`` for ``timestamp``; kept
    as an int in milliseconds for parity.
    """

    tool_name: str
    args_hash: str
    result_hash: Optional[str] = None
    unknown_tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    timestamp: int = 0
    #: True=执行成功（无 error_message）。effect-tool 重复判定需要区分
    #: 「成功后再跑一遍」与「失败后合理重试」。
    ok: Optional[bool] = None


class LoopLevel(Enum):
    """Kotlin ``enum class Level``."""

    NONE = "none"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class LoopCheckResult:
    """Kotlin ``data class LoopCheckResult``."""

    level: LoopLevel
    message: Optional[str] = None
    warning_key: Optional[str] = None

    @property
    def is_blocking(self) -> bool:
        """CRITICAL means the caller MUST skip tool execution."""
        return self.level == LoopLevel.CRITICAL

    @classmethod
    def none(cls) -> "LoopCheckResult":
        return cls(LoopLevel.NONE)


@dataclass(frozen=True)
class ToolLoopConfig:
    """Kotlin ``data class ToolLoopConfig`` defaults."""

    history_size: int = 30
    warning_threshold: int = 10
    unknown_tool_threshold: int = 10
    critical_threshold: int = 20
    global_circuit_breaker_threshold: int = 30
    #: Query / browse-style tools get a stricter *same-family* guard: N
    #: consecutive calls of any query tool count even when every call has
    #: different arguments (the classic "keep re-searching with new keywords"
    #: spiral that identical-args detection never sees). The family also merges
    #: so the model can't dodge the guard by alternating tools — the news
    #: roundup pattern is exactly ``web_fetch`` a listing, ``browser_use`` an
    #: article, ``web_fetch`` another listing…; all three count as one streak.
    query_tools: tuple[str, ...] = (
        "memory_get", "web_search", "web_fetch", "browser_use", "read_image",
    )
    query_warning_threshold: int = 5
    query_critical_threshold: int = 10
    #: 产出型（effect）工具：执行成功本身就会改变外部世界（生图/发消息/写
    #: 命令产物）。参数完全相同的再次执行只会产出重复产物，用户预期是
    #: 「一张就停」：成功后第 2 次原样重跑直接拦截。失败后的重试不受影响
    #: （ok=False 的记录会打断连续计数）。
    effect_tools: tuple[str, ...] = ("shell_execute", "send")
    effect_repeat_critical: int = 1
    #: effect 工具「任意参数」连续执行兜底：连发多张不同图属正常（4 张内
    #: 不打扰）。硬拦截阈值必须高于 auto wrap-up（≤12 轮）——纯「只有工具
    #: 没有文本」的空转优先走带总结的收尾路径，这个兜底只负责「有文本但
    #: 仍在反复跑命令」的残余场景。
    effect_run_warning: int = 6
    effect_run_critical: int = 14


class ToolLoopDetector:
    """Sliding-window repeat / no-progress detector.

    Kotlin: two hooks — ``check()`` (BEFORE execution; CRITICAL means the
    caller must skip the tool and surface ``message`` as a tool-error result)
    and ``record()`` (AFTER execution; appends to the window and may return a
    throttled WARNING to attach to the tool result).
    """

    def __init__(self, config: ToolLoopConfig = ToolLoopConfig()) -> None:
        self.config = config
        self._history: deque[ToolCallRecord] = deque()
        # warning_key -> last bucket index already emitted (throttling).
        self._warning_buckets: dict[str, int] = {}

    def reset(self) -> None:
        """Drop all in-flight state. Call on session reset / new chat."""
        self._history.clear()
        self._warning_buckets.clear()

    # -- test-only window inspection -----------------------------------------
    def history_snapshot(self) -> list[ToolCallRecord]:
        return list(self._history)

    # ─── before-execution hook ──────────────────────────────────────────────
    def check(self, tool_name: str, params: dict[str, Any]) -> LoopCheckResult:
        """Inspect the about-to-run tool against the sliding window.

        Caller MUST skip tool execution and surface ``message`` as a tool-error
        result when ``level == CRITICAL``.
        """
        args_hash = self._args_hash_for(tool_name, params)

        # 1. unknown_tool_repeat — most specific signal, runs first.
        unknown_streak = self._count_unknown_streak_from_tail(tool_name)
        if unknown_streak >= self.config.unknown_tool_threshold:
            msg = (
                f"[LOOP BLOCKED] CRITICAL: attempted unavailable tool '{tool_name}' "
                f"{unknown_streak} times. Stop retrying that missing tool and answer "
                "without it."
            )
            logger.warning("CRITICAL unknown_tool_repeat tool=%s streak=%s",
                           tool_name, unknown_streak)
            return LoopCheckResult(LoopLevel.CRITICAL, msg)

        no_progress_streak = self._get_no_progress_streak(tool_name, args_hash)

        # 2. global_circuit_breaker — universal backstop, before poll-specific
        #    rule so a runaway non-poll loop can't slip past on lower thresholds.
        if no_progress_streak >= self.config.global_circuit_breaker_threshold:
            msg = (
                f"[LOOP BLOCKED] CRITICAL: {tool_name} has repeated identical "
                f"no-progress outcomes {no_progress_streak} times. Session execution "
                "blocked by global circuit breaker."
            )
            logger.warning("CRITICAL global_circuit_breaker tool=%s streak=%s",
                           tool_name, no_progress_streak)
            return LoopCheckResult(LoopLevel.CRITICAL, msg)

        # 3. known_poll_no_progress — poll tools have a tighter critical bar
        #    because polling without progress is the canonical waste case.
        if self._is_poll_tool(tool_name, params):
            if no_progress_streak >= self.config.critical_threshold:
                msg = (
                    f"[LOOP BLOCKED] CRITICAL: Called {tool_name} {no_progress_streak} "
                    "times with identical no-progress results. Session execution blocked."
                )
                logger.warning("CRITICAL known_poll_no_progress tool=%s streak=%s",
                               tool_name, no_progress_streak)
                return LoopCheckResult(LoopLevel.CRITICAL, msg)
            if no_progress_streak >= self.config.warning_threshold:
                msg = (
                    f"[LOOP WARNING] You have called {tool_name} {no_progress_streak} "
                    "times with no progress. Stop polling and either (1) increase wait "
                    "time, or (2) report the task as failed."
                )
                logger.debug("WARNING known_poll_no_progress tool=%s streak=%s",
                             tool_name, no_progress_streak)
                return LoopCheckResult(LoopLevel.WARNING, msg,
                                       f"poll:{tool_name}:{args_hash}")

        # 4. generic_repeat — non-poll tools only; counts non-consecutive hits
        #    so flaky-but-progressing calls eventually fall out of the window.
        if not self._is_poll_tool(tool_name, params):
            total = sum(1 for r in self._history
                        if r.tool_name == tool_name and r.args_hash == args_hash)
            if total >= self.config.warning_threshold:
                msg = (
                    f"[LOOP WARNING] You have called {tool_name} {total} times "
                    "with identical arguments. If this is not making progress, stop "
                    "retrying and report the task as failed."
                )
                logger.debug("WARNING generic_repeat tool=%s count=%s",
                             tool_name, total)
                return LoopCheckResult(LoopLevel.WARNING, msg,
                                       f"repeat:{tool_name}:{args_hash}")

        # 5. effect_tool_repeat — 产出型工具（shell_execute/send）同参数
        #    且上一次已成功：重复执行只产重复产物，第 2 次原样重跑直接拦截。
        #    注意每次生图输出内容（文件名/时间戳）都不同，result 系规则
        #    （no_progress）永远看不到「无进展」，必须只看参数与 ok。
        if tool_name in self.config.effect_tools:
            streak = self._consecutive_effect_success_streak(tool_name, args_hash)
            if streak >= self.config.effect_repeat_critical:
                msg = (
                    f"[LOOP BLOCKED] CRITICAL: 同一条 {tool_name} 命令（参数完全相同）"
                    f"已成功执行过 {streak} 次，产物已经生成，本次调用已被拦截。"
                    "重复执行只会产生重复结果，不要再次执行——"
                    "直接引用已有产出，向用户总结收尾。"
                )
                logger.warning("CRITICAL effect_tool_repeat tool=%s streak=%s",
                               tool_name, streak)
                return LoopCheckResult(LoopLevel.CRITICAL, msg)
            run_streak = self._consecutive_effect_run_streak(tool_name)
            if run_streak >= self.config.effect_run_critical:
                msg = (
                    f"[LOOP BLOCKED] CRITICAL: {tool_name} 已连续执行 {run_streak} 次。"
                    "任务大概率已完成，停止执行，对照已有产出总结收尾。"
                )
                logger.warning("CRITICAL effect_tool_runaway tool=%s streak=%s",
                               tool_name, run_streak)
                return LoopCheckResult(LoopLevel.CRITICAL, msg)
            if run_streak >= self.config.effect_run_warning:
                msg = (
                    f"[LOOP WARNING] {tool_name} 已连续执行 {run_streak} 次。"
                    "先自检：目标是否已全部完成？完成就立即总结收尾，"
                    "不要继续执行同类命令。"
                )
                key = "effectrun:" + tool_name
                if self._should_emit_warning(key, run_streak):
                    logger.debug("WARNING effect_tool_runaway tool=%s streak=%s",
                                 tool_name, run_streak)
                    return LoopCheckResult(LoopLevel.WARNING, msg, key)

        # 6. query_tool_runaway — memory_get / web_search / web_fetch called
        #    many times in a row *regardless of arguments*. Identical-args
        #    detection (rules 2-4) never fires here because the model shuffles
        #    keywords each call, yet the survey makes no progress — exactly the
        #    "整理知识 → 反复 memory_get" spiral. A different tool in between
        #    resets the streak.
        if tool_name in self.config.query_tools:
            streak = self._consecutive_same_tool_streak(tool_name)
            if streak >= self.config.query_critical_threshold:
                msg = (
                    f"[LOOP BLOCKED] CRITICAL: retrieval/browsing tools "
                    f"({tool_name} and similar like web_fetch/browser_use) have "
                    f"now been called {streak} times in a row with no other tool "
                    "in between. Re-searching does not add information. Stop now "
                    "and answer with what you already have, or tell the user the "
                    "task needs more input."
                )
                logger.warning("CRITICAL query_tool_runaway tool=%s streak=%s",
                               tool_name, streak)
                return LoopCheckResult(LoopLevel.CRITICAL, msg)
            if streak >= self.config.query_warning_threshold:
                msg = (
                    f"[LOOP WARNING] retrieval/browsing tools (incl. "
                    f"{tool_name}) have now been called {streak} times in a row. "
                    "If the last calls did not find new information, stop "
                    "searching and proceed with what you have."
                )
                key = f"queryrun:{tool_name}"
                if self._should_emit_warning(key, streak):
                    logger.debug("WARNING query_tool_runaway tool=%s streak=%s",
                                 tool_name, streak)
                    return LoopCheckResult(LoopLevel.WARNING, msg, key)

        return LoopCheckResult.none()

    def _consecutive_same_tool_streak(self, tool_name: str) -> int:
        """Count trailing records using the *same query-class* tool (any args).

        For any tool in ``config.query_tools`` (the retrieval / browsing family:
        ``web_fetch`` / ``web_search`` / ``browser_use`` / ``memory_get`` …),
        consecutive calls to *any* tool in that family are merged into the streak
        — so the model cannot dodge the runaway guard by alternating between
        them (the canonical ``web_fetch`` listing → ``browser_use`` article →
        ``web_fetch`` listing spiral). Tools outside the query family still
        require a strict name match and act as a reset, so a coder reading files
        between fetches keeps its fetch streak alive only until another query
        tool intervenes.
        """
        is_query = tool_name in self.config.query_tools
        streak = 0
        for rec in reversed(self._history):
            if rec.unknown_tool_name is not None:
                break
            if is_query:
                if rec.tool_name in self.config.query_tools:
                    streak += 1
                else:
                    break
            elif rec.tool_name == tool_name:
                streak += 1
            else:
                break
        return streak

    def _consecutive_effect_success_streak(self, tool_name: str, args_hash: str) -> int:
        """Count trailing *successful* records with identical (tool, args).

        A failed run (ok=False) resets the count — retrying after failure is
        legitimate. Records of other tools also reset it: the effect-tool rule
        only targets back-to-back duplication.
        """
        streak = 0
        for rec in reversed(self._history):
            if rec.unknown_tool_name is not None:
                break
            if rec.tool_name != tool_name or rec.args_hash != args_hash:
                break
            if rec.ok is False:
                break
            streak += 1
        return streak

    def _consecutive_effect_run_streak(self, tool_name: str) -> int:
        """Count trailing records of the same effect tool, *any* args.

        Backstop for near-identical commands whose args differ by a digit
        (e.g. a timestamp), which exact-hash matching never sees.
        """
        streak = 0
        for rec in reversed(self._history):
            if rec.unknown_tool_name is not None:
                break
            if rec.tool_name == tool_name:
                streak += 1
            else:
                break
        return streak

    # ─── after-execution hook ───────────────────────────────────────────────
    def record(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: Optional[str],
        error_message: Optional[str] = None,
        tool_call_id: Optional[str] = None,
    ) -> LoopCheckResult:
        """Append the just-completed call and decide on a throttled warning.

        Critical outcomes are reported by ``check()`` *before* the call;
        ``record()`` only ever returns NONE or WARNING.
        """
        args_hash = self._args_hash_for(tool_name, params)
        result_hash = self._result_hash_for(result, error_message)
        unknown_tool = self._extract_unknown_tool_name(error_message)

        self._history.append(ToolCallRecord(
            tool_name=tool_name,
            args_hash=args_hash,
            result_hash=result_hash,
            unknown_tool_name=unknown_tool,
            tool_call_id=tool_call_id,
            ok=error_message is None,
        ))
        while len(self._history) > self.config.history_size:
            self._history.popleft()

        # Re-evaluate warnings on the post-record window (count now includes
        # the call just appended). Critical paths are check()-only by spec.
        if self._is_poll_tool(tool_name, params):
            streak = self._get_no_progress_streak(tool_name, args_hash)
            if self.config.warning_threshold <= streak < self.config.critical_threshold:
                key = f"poll:{tool_name}:{args_hash}"
                if self._should_emit_warning(key, streak):
                    msg = (
                        f"[LOOP WARNING] You have called {tool_name} {streak} times "
                        "with no progress. Stop polling and either (1) increase wait "
                        "time, or (2) report the task as failed."
                    )
                    return LoopCheckResult(LoopLevel.WARNING, msg, key)
        else:
            total = sum(1 for r in self._history
                        if r.tool_name == tool_name and r.args_hash == args_hash)
            if total >= self.config.warning_threshold:
                key = f"repeat:{tool_name}:{args_hash}"
                if self._should_emit_warning(key, total):
                    msg = (
                        f"[LOOP WARNING] You have called {tool_name} {total} times "
                        "with identical arguments. If this is not making "
                        "progress, stop retrying and report the task as failed."
                    )
                    return LoopCheckResult(LoopLevel.WARNING, msg, key)

        return LoopCheckResult.none()

    # ─── strategy helpers ───────────────────────────────────────────────────
    def _count_unknown_streak_from_tail(self, tool_name: str) -> int:
        """Count *consecutive* tail records targeting the same unknown name."""
        streak = 0
        for rec in reversed(self._history):
            if rec.unknown_tool_name is None:
                break
            if rec.unknown_tool_name == tool_name:
                streak += 1
            else:
                break
        return streak

    def _get_no_progress_streak(self, tool_name: str, args_hash: str) -> int:
        """Count recent records sharing (tool_name, args_hash) AND one result_hash.

        Records of *other* tools are skipped (they don't reset the streak), but
        a result mismatch on the target tool stops counting — that is the
        "progress observed" signal.
        """
        streak = 0
        pinned_hash: Optional[str] = None
        for rec in reversed(self._history):
            if rec.tool_name != tool_name or rec.args_hash != args_hash:
                continue
            if rec.result_hash is None:
                break
            if pinned_hash is None:
                pinned_hash = rec.result_hash
                streak += 1
            elif rec.result_hash == pinned_hash:
                streak += 1
            else:
                break
        return streak

    @staticmethod
    def _is_poll_tool(tool_name: str, params: dict[str, Any]) -> bool:
        if tool_name == "command_status":
            return True
        if tool_name == "process":
            action = str(params.get("action", "")).lower()
            if action in ("poll", "log"):
                return True
        return False

    def _should_emit_warning(self, warning_key: str, current_count: int) -> bool:
        """Throttle to one warning per ``warningThreshold`` bucket of calls."""
        bucket = current_count // self.config.warning_threshold
        last = self._warning_buckets.get(warning_key)
        if last == bucket:
            return False
        self._warning_buckets[warning_key] = bucket
        return True

    # ─── hashing / parsing primitives ───────────────────────────────────────
    def _args_hash_for(self, tool_name: str, params: dict[str, Any]) -> str:
        if any(k in params for k in ARGS_HASH_IGNORED_KEYS):
            filtered = {k: v for k, v in params.items()
                        if k not in ARGS_HASH_IGNORED_KEYS}
        else:
            filtered = params
        return self._sha256(f"{tool_name}:{self._stable_json(filtered)}")

    @staticmethod
    def _stable_json(value: Any) -> str:
        """Keys sorted alphabetically at every nesting level so equal maps
        (regardless of insertion order) hash identically."""

        def stable(v: Any) -> str:
            if v is None:
                return "null"
            if isinstance(v, dict):
                items = sorted((str(k), stable(val)) for k, val in v.items())
                return "{" + ",".join(f"{json.dumps(k)}:{val}"
                                      for k, val in items) + "}"
            if isinstance(v, (list, tuple)):
                return "[" + ",".join(stable(x) for x in v) + "]"
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, (int, float)):
                # Kotlin appends the raw toString() — 1.0 stays "1.0".
                return str(v)
            return json.dumps(str(v), ensure_ascii=False)

        return stable(value)

    @staticmethod
    def _result_hash_for(result: Optional[str], error_message: Optional[str]) -> str:
        """Hash only the success/failure-bearing parts of a tool result.

        The entire output text is folded in — tool results at this layer are
        already sanitized by the underlying tools.
        """
        payload = f"err={error_message or ''}\x00out={result or ''}"
        return ToolLoopDetector._sha256(payload)

    @staticmethod
    def _extract_unknown_tool_name(error_message: Optional[str]) -> Optional[str]:
        if not error_message or not error_message.strip():
            return None
        m = _UNKNOWN_TOOL_RE_1.search(error_message)
        if m:
            return m.group(1)
        m = _UNKNOWN_TOOL_RE_2.search(error_message)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _sha256(s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()
