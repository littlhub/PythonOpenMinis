"""Scheduled task model.

Ported from: src/android/app/src/main/java/com/openminis/app/scheduled/ScheduledTask.kt

Kept deliberately close to the Kotlin data class: the JSON written here is the
same shape the Android app stores, so a tasks file can be read by either side.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

__all__ = [
    "MAX_RUN_HISTORY",
    "RepeatMode",
    "ScheduledRun",
    "ScheduledTask",
    "ScheduledTaskError",
    "TargetMode",
]

MAX_RUN_HISTORY = 50

#: ``Calendar.DAY_OF_WEEK`` values Kotlin uses: 1=Sunday … 7=Saturday.
DAYS_OF_WEEK = tuple(range(1, 8))


class ScheduledTaskError(ValueError):
    """Raised when a task payload cannot be parsed/validated."""


class RepeatMode(str, Enum):
    ONCE = "ONCE"
    DAILY = "DAILY"
    WEEKDAYS = "WEEKDAYS"
    CUSTOM = "CUSTOM"

    @classmethod
    def parse(cls, raw: str | None) -> "RepeatMode":
        if not raw:
            return cls.ONCE
        try:
            return cls(str(raw).upper())
        except ValueError:
            return cls.ONCE


@dataclass(frozen=True, slots=True)
class ScheduledRun:
    """One recorded execution (``ScheduledRun.kt``)."""

    fired_at: int  # epoch ms
    session_id: str | None = None
    preview: str | None = None
    ok: bool = True

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"firedAt": self.fired_at, "ok": self.ok}
        if self.session_id is not None:
            out["sessionId"] = self.session_id
        if self.preview is not None:
            out["preview"] = self.preview
        return out

    @classmethod
    def from_json(cls, o: dict[str, Any]) -> "ScheduledRun":
        return cls(
            fired_at=int(o.get("firedAt") or 0),
            session_id=o.get("sessionId"),
            preview=o.get("preview"),
            ok=bool(o.get("ok", True)),
        )


@dataclass(frozen=True, slots=True)
class TargetMode:
    """What a task does when it fires (``ScheduledTargetMode.kt``).

    Encoded as ``NEW_SESSION`` / ``APPEND_TO:<sid>`` / ``RERUN:<sid>:<mid>``.
    """

    kind: str = "NEW_SESSION"  # NEW_SESSION | APPEND_TO | RERUN
    session_id: str | None = None
    message_id: str | None = None

    def encode(self) -> str:
        if self.kind == "APPEND_TO" and self.session_id:
            return f"APPEND_TO:{self.session_id}"
        if self.kind == "RERUN" and self.session_id:
            return f"RERUN:{self.session_id}:{self.message_id or ''}"
        return "NEW_SESSION"

    @classmethod
    def decode(cls, raw: str | None) -> "TargetMode":
        if not raw or raw == "NEW_SESSION":
            return cls()
        if raw.startswith("APPEND_TO:"):
            return cls("APPEND_TO", raw[len("APPEND_TO:"):])
        if raw.startswith("RERUN:"):
            rest = raw[len("RERUN:"):]
            sid, _, mid = rest.partition(":")
            return cls("RERUN", sid or None, mid or None)
        return cls()

    @property
    def session_id_or_none(self) -> str | None:
        return self.session_id


@dataclass(slots=True)
class ScheduledTask:
    """A user-defined scheduled task (``ScheduledTask.kt``)."""

    label: str
    hour: int = 9
    minute: int = 0
    repeat_mode: RepeatMode = RepeatMode.ONCE
    custom_days: frozenset[int] = frozenset()
    prompt: str = ""
    target_mode: TargetMode = field(default_factory=TargetMode)
    #: Which model this task runs on. ``None`` → whatever is active at fire
    #: time, which means editing the active provider silently changes past
    #: tasks. Ported from ``ScheduledTask.modelId``.
    model_id: str | None = None
    #: Which provider *instance* serves it (``"openAI"``, ``"anthropic"``…).
    #:
    #: Kotlin stores a JSON group/entry binding here because its config is
    #: group-based; the Python port's config is a flat map of instances, so the
    #: instance id IS the binding. Kept as a separate field rather than folded
    #: into ``model_id`` because the same model id can exist on two instances
    #: (two OpenAI-compatible gateways) and only the instance picks the key and
    #: base URL.
    model_binding: str | None = None
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    start_date_ms: int | None = None
    end_date_ms: int | None = None
    last_fired_at: int | None = None
    last_result_preview: str | None = None
    last_result_session_id: str | None = None
    run_history: list[ScheduledRun] = field(default_factory=list)

    # -- validation ------------------------------------------------------
    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23:
            raise ScheduledTaskError("hour 必须在 0-23")
        if not 0 <= self.minute <= 59:
            raise ScheduledTaskError("minute 必须在 0-59")
        self.custom_days = frozenset(d for d in self.custom_days if d in DAYS_OF_WEEK)

    # -- scheduling ------------------------------------------------------
    @staticmethod
    def _start_of_day(ms: int) -> datetime:
        dt = datetime.fromtimestamp(ms / 1000)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    def next_trigger_ms(self, now: int | None = None) -> int | None:
        """Wall-clock epoch ms of the next firing time, or ``None``.

        Mirrors ``ScheduledTask.nextTriggerMs``: local time, respects the
        active window (``start/end_date_ms``) and returns ``None`` when the
        task is disabled or has no valid slot left.
        """
        if not self.enabled:
            return None
        now = int(time.time() * 1000) if now is None else now
        floor = max(now, self.start_date_ms or now)

        candidate = datetime.fromtimestamp(floor / 1000).replace(
            hour=self.hour, minute=self.minute, second=0, microsecond=0
        )
        one_day = 86400 * 1000

        def ts(dt: datetime) -> int:
            return int(dt.timestamp() * 1000)

        if ts(candidate) < floor:
            candidate = datetime.fromtimestamp((ts(candidate) + one_day) / 1000)

        if self.repeat_mode is RepeatMode.WEEKDAYS:
            # Python weekday(): Mon=0 … Sun=6 → weekend is >= 5
            safety = 8
            while candidate.weekday() >= 5 and safety > 0:
                candidate = datetime.fromtimestamp((ts(candidate) + one_day) / 1000)
                safety -= 1
        elif self.repeat_mode is RepeatMode.CUSTOM:
            if not self.custom_days:
                return None
            # Calendar.DAY_OF_WEEK (Sun=1 … Sat=7) → Python weekday (Mon=0)
            # Sun=1 → 6, Mon=2 → 0, Sat=7 → 5
            wanted = {(d - 2) % 7 for d in self.custom_days}
            safety = 8
            while candidate.weekday() not in wanted and safety > 0:
                candidate = datetime.fromtimestamp((ts(candidate) + one_day) / 1000)
                safety -= 1
            if safety <= 0:
                return None

        if self.end_date_ms is not None:
            end_day = self._start_of_day(self.end_date_ms).replace(
                hour=23, minute=59, second=59
            )
            if ts(candidate) > ts(end_day):
                return None
        return ts(candidate)

    def current_slot_ms(self, now: int | None = None) -> int | None:
        """Most recent slot **at or before** ``now`` — what a poller fires.

        ``next_trigger_ms`` always looks forward, so a poller that only asked
        for it would miss every slot: at 08:08 the slot is 2 minutes in the
        future, at 08:10 it has already rolled to tomorrow. Android never hit
        this because AlarmManager fires on the exact millisecond it was given.

        This walks back up to 8 days to find the last matching slot, which also
        lets a server that was down overnight catch up on one missed run.
        """
        if not self.enabled:
            return None
        now = int(time.time() * 1000) if now is None else now
        day = datetime.fromtimestamp(now / 1000).replace(
            hour=self.hour, minute=self.minute, second=0, microsecond=0
        )
        for _ in range(8):
            slot = int(day.timestamp() * 1000)
            if slot > now:
                day -= timedelta(days=1)
                continue
            if not self._matches_weekday(day.weekday()):
                day -= timedelta(days=1)
                continue
            # Never replay a slot from before the task existed: creating a
            # "daily at 23:59" task at 08:00 must not immediately fire
            # yesterday's 23:59. The floor is the START OF THAT DAY rather
            # than the raw timestamp, so catching up on a slot earlier today
            # (server was down, or the task was created at 08:08 for 08:06)
            # still works — matching AlarmManager, which fires immediately
            # when handed a time already in the past.
            floor_day = self._start_of_day(max(self.start_date_ms or 0, self.created_at))
            if slot < int(floor_day.timestamp() * 1000):
                return None
            if self.end_date_ms is not None:
                end_day = self._start_of_day(self.end_date_ms).replace(
                    hour=23, minute=59, second=59
                )
                if slot > int(end_day.timestamp() * 1000):
                    return None
            return slot
        return None

    def _matches_weekday(self, weekday: int) -> bool:
        """``weekday`` is Python's Monday=0 … Sunday=6."""
        if self.repeat_mode is RepeatMode.WEEKDAYS:
            return weekday < 5
        if self.repeat_mode is RepeatMode.CUSTOM:
            if not self.custom_days:
                return False
            # Calendar.DAY_OF_WEEK (Sun=1 … Sat=7) → Python weekday
            return weekday in {(d - 2) % 7 for d in self.custom_days}
        return True

    # -- run history -----------------------------------------------------
    def record_run(
        self,
        ok: bool,
        preview: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Append one execution record (capped at ``MAX_RUN_HISTORY``)."""
        now = int(time.time() * 1000)
        self.run_history.insert(
            0,
            ScheduledRun(
                fired_at=now,
                session_id=session_id,
                preview=(preview or "")[:300] or None,
                ok=ok,
            ),
        )
        del self.run_history[MAX_RUN_HISTORY:]
        self.last_fired_at = now
        self.last_result_preview = (preview or "")[:300] or None
        self.last_result_session_id = session_id

    def with_disabled(self) -> "ScheduledTask":
        """A copy with ``enabled=False`` (used to auto-expire ONCE tasks)."""
        return replace(self, enabled=False)

    # -- (de)serialisation -----------------------------------------------
    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "hour": self.hour,
            "minute": self.minute,
            "repeatMode": self.repeat_mode.value,
            "customDays": ",".join(str(d) for d in sorted(self.custom_days)),
            "prompt": self.prompt,
            "targetMode": self.target_mode.encode(),
            "enabled": self.enabled,
            "createdAt": self.created_at,
        }
        if self.model_id:
            out["modelId"] = self.model_id
        if self.model_binding:
            out["modelBinding"] = self.model_binding
        if self.start_date_ms is not None:
            out["startDateMs"] = self.start_date_ms
        if self.end_date_ms is not None:
            out["endDateMs"] = self.end_date_ms
        if self.last_fired_at is not None:
            out["lastFiredAt"] = self.last_fired_at
        if self.last_result_preview is not None:
            out["lastResultPreview"] = self.last_result_preview
        if self.last_result_session_id is not None:
            out["lastResultSessionId"] = self.last_result_session_id
        if self.run_history:
            out["runHistory"] = [r.to_json() for r in self.run_history]
        return out

    @classmethod
    def from_json(cls, o: dict[str, Any]) -> "ScheduledTask":
        days = frozenset(
            int(d.strip())
            for d in str(o.get("customDays") or "").split(",")
            if d.strip().isdigit()
        )
        return cls(
            id=str(o.get("id") or uuid.uuid4().hex),
            label=str(o.get("label") or ""),
            hour=int(o.get("hour", 9)),
            minute=int(o.get("minute", 0)),
            repeat_mode=RepeatMode.parse(o.get("repeatMode")),
            custom_days=days,
            prompt=str(o.get("prompt") or ""),
            target_mode=TargetMode.decode(o.get("targetMode")),
            model_id=o.get("modelId"),
            model_binding=o.get("modelBinding"),
            enabled=bool(o.get("enabled", True)),
            created_at=int(o.get("createdAt") or int(time.time() * 1000)),
            start_date_ms=o.get("startDateMs"),
            end_date_ms=o.get("endDateMs"),
            last_fired_at=o.get("lastFiredAt"),
            last_result_preview=o.get("lastResultPreview"),
            last_result_session_id=o.get("lastResultSessionId"),
            run_history=[
                ScheduledRun.from_json(r)
                for r in (o.get("runHistory") or [])
                if isinstance(r, dict)
            ],
        )
