"""Background ticker that fires due scheduled tasks.

Ported from: src/android/app/src/main/java/com/openminis/app/scheduled/ScheduledTaskManager.kt

Android uses ``AlarmManager`` + a ``BroadcastReceiver``; a server process has
neither, so the port polls. The loop is deliberately dumb: every
``POLL_SECONDS`` it asks each enabled task for its next trigger and fires the
ones already due, then re-persists the task (run history, last result, and —
for ONE-SHOT tasks — ``enabled=False``).
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from ..core.logging import get_logger
from .model import RepeatMode, ScheduledTask
from .store import ScheduledTaskStore

logger = get_logger(__name__)

__all__ = ["ScheduledRunner", "POLL_SECONDS"]

POLL_SECONDS = 30

#: ``(prompt, session_id | None, *, model_id, instance_id) -> (ok, preview, session_id)``
#:
#: The model kwargs carry the task's pinned model through to the executor. They
#: are keyword-only so an executor that ignores them (a test double, a dry run)
#: keeps working with a plain two-argument signature.
ExecuteFn = Callable[..., Awaitable[tuple[bool, str, str | None]]]


class ScheduledRunner:
    """Poll the store and execute due tasks.

    ``execute_fn`` is injected so tests can run the scheduling logic without
    touching the model. When it is ``None`` the runner still ticks but only
    records that a task was due (useful for dry runs).
    """

    def __init__(
        self,
        store: ScheduledTaskStore | None = None,
        execute_fn: ExecuteFn | None = None,
        poll_seconds: int = POLL_SECONDS,
    ) -> None:
        self.store = store or ScheduledTaskStore()
        self.execute_fn = execute_fn
        self.poll_seconds = max(5, poll_seconds)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="scheduled-runner")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=self.poll_seconds + 5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None

    # -- loop ------------------------------------------------------------
    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - never kill the loop
                logger.exception("scheduled tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                continue

    async def tick(self) -> int:
        """Fire everything due right now. Returns how many tasks ran."""
        now = int(time.time() * 1000)
        due: list[tuple[ScheduledTask, int]] = []
        for task in self.store.all():
            slot = task.current_slot_ms(now)
            if slot is None or slot > now:
                continue
            if task.last_fired_at and task.last_fired_at >= slot:
                continue  # already fired for this slot
            due.append((task, slot))

        for task, _slot in due:
            await self._fire(task)
        return len(due)

    async def _fire(self, task: ScheduledTask) -> None:
        session_id = task.target_mode.session_id_or_none
        ok, preview, used_session = True, "", session_id
        if self.execute_fn is not None:
            try:
                ok, preview, used_session = await self.execute_fn(
                    task.prompt,
                    session_id,
                    model_id=task.model_id,
                    instance_id=task.model_binding,
                )
            except Exception as e:  # pragma: no cover - defensive
                logger.exception("scheduled task %s failed", task.id)
                ok, preview = False, f"执行失败: {e}"
        else:  # pragma: no cover - dry run
            preview = "(未配置执行器)"
        task.record_run(ok, preview, used_session)
        if task.repeat_mode is RepeatMode.ONCE:
            task.enabled = False
        if not self.store.update(task):
            logger.warning("scheduled task %s vanished mid-run", task.id)
