"""Scheduled tasks — the Python port of ``com.openminis.app.scheduled``.

Ported from: src/android/app/src/main/java/com/openminis/app/scheduled

A scheduled task fires an AI prompt at a wall-clock time, optionally repeating.
On Android the firing is done by ``AlarmManager``; here a small asyncio ticker
inside the FastAPI process polls the store (see :mod:`openminis.scheduled.runner`).
"""

from .model import (
    MAX_RUN_HISTORY,
    RepeatMode,
    ScheduledRun,
    ScheduledTask,
    TargetMode,
)
from .store import ScheduledTaskStore

__all__ = [
    "MAX_RUN_HISTORY",
    "RepeatMode",
    "ScheduledRun",
    "ScheduledTask",
    "ScheduledTaskStore",
    "TargetMode",
]
