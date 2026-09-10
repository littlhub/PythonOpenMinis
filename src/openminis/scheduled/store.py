"""JSON-file store for scheduled tasks.

Ported from: src/android/app/src/main/java/com/openminis/app/scheduled/ScheduledTaskStore.kt

Android keeps the list in a SharedPreferences string; the Python port writes
``<data_dir>/scheduled/tasks.json``. Every mutation rewrites the whole file
(the dataset is tiny) — which is exactly why the delete in the UI used to look
broken: it was never persisted anywhere, so a refresh re-created the mock rows.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from ..core.context import app_context
from ..core.logging import get_logger
from .model import ScheduledTask, ScheduledTaskError

logger = get_logger(__name__)

__all__ = ["ScheduledTaskStore"]

TASKS_FILE = "tasks.json"


class ScheduledTaskStore:
    """``ScheduledTaskStore`` — CRUD over the persisted task list.

    The store is process-wide but file-backed: two editors (REST + the
    runner) see the same rows because every call re-reads the file.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else app_context().data_dir / "scheduled" / TASKS_FILE
        self._lock = threading.Lock()

    # -- io --------------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:  # pragma: no cover - corrupt file
            logger.warning("scheduled tasks unreadable (%s): %s", self.path, e)
            return {}
        tasks = data.get("tasks")
        return dict(tasks) if isinstance(tasks, dict) else {}

    def _write(self, tasks: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updatedAt": int(time.time() * 1000),
            "tasks": tasks,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)  # atomic: no half-written file after a crash

    # -- crud ------------------------------------------------------------
    def all(self) -> list[ScheduledTask]:
        with self._lock:
            raw = self._read()
        out: list[ScheduledTask] = []
        for tid, row in raw.items():
            if not isinstance(row, dict):
                continue
            try:
                task = ScheduledTask.from_json(row)
            except (ScheduledTaskError, TypeError, ValueError) as e:
                logger.warning("skip malformed scheduled task %s: %s", tid, e)
                continue
            if task.id != tid:  # keep the map key authoritative
                task.id = str(tid)
            out.append(task)
        return out

    def get(self, task_id: str) -> ScheduledTask | None:
        return next((t for t in self.all() if t.id == task_id), None)

    def upsert(self, task: ScheduledTask) -> ScheduledTask:
        """Insert or replace by id. Returns the stored task."""
        if not task.id:
            raise ScheduledTaskError("task id 不能为空")
        if not str(task.label or "").strip():
            raise ScheduledTaskError("任务名称不能为空")
        with self._lock:
            raw = self._read()
            raw[task.id] = task.to_json()
            self._write(raw)
        return task

    def delete(self, task_id: str) -> bool:
        """Remove a task. Returns ``False`` when it did not exist."""
        with self._lock:
            raw = self._read()
            if task_id not in raw:
                return False
            del raw[task_id]
            self._write(raw)
        return True

    def set_enabled(self, task_id: str, enabled: bool) -> ScheduledTask | None:
        with self._lock:
            raw = self._read()
            row = raw.get(task_id)
            if not isinstance(row, dict):
                return None
            row["enabled"] = bool(enabled)
            raw[task_id] = row
            self._write(raw)
        return self.get(task_id)

    def update(self, task: ScheduledTask) -> bool:
        """Persist a mutated task (run history / last result). False if gone."""
        with self._lock:
            raw = self._read()
            if task.id not in raw:
                return False
            raw[task.id] = task.to_json()
            self._write(raw)
        return True

    def clear(self) -> None:
        with self._lock:
            self._write({})
