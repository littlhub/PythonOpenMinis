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

        return LoopCheckResult.none()

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
