"""REST surface for scheduled tasks.

Ported from: ``com.openminis.app.ui.scheduled.ScheduledTasksViewModel``

The list screen used to render hard-coded rows in React state — deleting one
only mutated local state, so a refresh brought it straight back. These
endpoints back the UI with the persisted store instead.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from ..core.logging import get_logger
from ..scheduled.model import (
    DAYS_OF_WEEK,
    RepeatMode,
    ScheduledTask,
    ScheduledTaskError,
    TargetMode,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/scheduled", tags=["scheduled"])


def _store():
    from ..scheduled.store import ScheduledTaskStore

    return ScheduledTaskStore()


def _view(task: ScheduledTask, now: int | None = None) -> dict[str, Any]:
    now = now or int(time.time() * 1000)
    nxt = task.next_trigger_ms(now)
    return {
        **task.to_json(),
        "nextTriggerMs": nxt,
        "runCount": len(task.run_history),
    }


@router.get("/tasks")
async def scheduled_list() -> dict[str, Any]:
    now = int(time.time() * 1000)
    tasks = _store().all()
    return {
        "tasks": [_view(t, now) for t in tasks],
        "now": now,
        "serverTimeOffsetHint": "epoch ms, local timezone",
    }


@router.post("/tasks")
async def scheduled_create(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        task = _task_from_payload(payload)
    except ScheduledTaskError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if not str(task.prompt or "").strip():
        raise HTTPException(status_code=400, detail="任务提示词不能为空")
    _store().upsert(task)
    return {"task": _view(task)}


@router.put("/tasks/{task_id}")
async def scheduled_update(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    current = store.get(task_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    try:
        merged = _task_from_payload({**current.to_json(), **payload, "id": task_id})
    except ScheduledTaskError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    store.upsert(merged)
    return {"task": _view(merged)}


@router.delete("/tasks/{task_id}")
async def scheduled_delete(task_id: str) -> dict[str, Any]:
    if not _store().delete(task_id):
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return {"ok": True, "id": task_id}


@router.post("/tasks/{task_id}/toggle")
async def scheduled_toggle(task_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    enabled = bool((payload or {}).get("enabled", True))
    task = _store().set_enabled(task_id, enabled)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return {"task": _view(task)}


@router.get("/tasks/{task_id}/runs")
async def scheduled_runs(task_id: str) -> dict[str, Any]:
    task = _store().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return {"runs": [r.to_json() for r in task.run_history]}


# ---------------------------------------------------------------------------
# execution — what the background runner calls when a task fires
# ---------------------------------------------------------------------------
async def execute_prompt(
    prompt: str,
    session_id: str | None = None,
    *,
    model_id: str | None = None,
    instance_id: str | None = None,
) -> tuple[bool, str, str | None]:
    """Run ``prompt`` through the agent kernel and persist the turn.

    Mirrors ``ScheduledAgentRunner``: NEW_SESSION creates a chat, APPEND_TO
    reuses one. Returns ``(ok, preview, session_id)`` — the preview is what
    the task's run-history row shows.

    ``model_id`` / ``instance_id`` come from the task. A scheduled run happens
    unattended, so it must use the model the user pinned when creating it — if
    it silently followed the active provider instead, the task would change
    behaviour (or start failing on an uncredentialed provider) with nobody
    watching.
    """
    from ..data.model import LLMMessage, LLMStreamChunk
    from ..data.model.agent_content_part import Text
    from ..settings.chat_service import (
        ChatSetupError,
        attribution_snapshot,
        build_chat_setup,
    )
    from ..settings.store import SettingsStore
    from . import chat_store

    text = str(prompt or "").strip()
    if not text:
        return False, "任务提示词为空", session_id

    sid = session_id
    if sid and await chat_store.get_session(sid) is None:
        sid = None  # session vanished → fall back to a fresh one
    if not sid:
        created = await chat_store.create_session()
        sid = created.id

    store = SettingsStore.get()
    try:
        provider, runtime, options, _identity, conf = build_chat_setup(
            store, instance_id=instance_id, model_id=model_id
        )
    except ChatSetupError as e:
        return False, f"模型服务未配置: {e}", sid
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("scheduled chat setup failed")
        return False, f"对话初始化失败: {e}", sid

    model_label = (conf.get("model") or "").strip() or None
    snapshot = attribution_snapshot(store, conf)
    # Sum every LLM call of the run: one agent turn can make several (tool
    # rounds) and all of them are billed. Without a sink the scheduled path
    # would be the one place usage silently went unrecorded.
    usage_meter = {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheCreationTokens": 0,
        "cacheReadTokens": 0,
    }

    async def _sink(chunk: object) -> None:
        if isinstance(chunk, LLMStreamChunk.Usage):
            u = chunk.usage
            usage_meter["inputTokens"] += int(u.input_tokens or 0)
            usage_meter["outputTokens"] += int(u.output_tokens or 0)
            usage_meter["cacheCreationTokens"] += int(
                getattr(u, "cache_creation_input_tokens", 0) or 0
            )
            usage_meter["cacheReadTokens"] += int(
                getattr(u, "cache_read_input_tokens", 0) or 0
            )

    runtime.chunk_sink = _sink  # type: ignore[assignment]

    try:
        messages = await chat_store.load_runtime_history(sid)
        messages.append(LLMMessage(LLMMessage.Role.USER, text))
        await chat_store.append_turn(sid, "user", text, model_label=model_label)
        await runtime.run(provider, messages, session_id=f"db-{sid}", options=options)
        tail = messages[-1] if messages else None
        final_text = ""
        if tail is not None and tail.role == LLMMessage.Role.ASSISTANT:
            final_text = "".join(
                p.text for p in (tail.content_parts or []) if isinstance(p, Text)
            )
        if final_text.strip():
            await chat_store.append_turn(
                sid, "assistant", final_text,
                model_label=model_label,
                token_usage=(
                    json.dumps(usage_meter) if any(usage_meter.values()) else None
                ),
                **snapshot,
            )
        return True, final_text.strip()[:300], sid
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("scheduled run failed")
        return False, f"执行失败: {e}", sid
    finally:
        close = getattr(provider, "aclose", None)
        if callable(close):
            try:
                await close()
            except Exception:  # pragma: no cover
                pass


# ---------------------------------------------------------------------------
def _task_from_payload(payload: dict[str, Any]) -> ScheduledTask:
    """Build a task from a REST body (create or update)."""
    days = payload.get("customDays")
    if isinstance(days, str):
        days = [int(d.strip()) for d in days.split(",") if d.strip().isdigit()]
    elif isinstance(days, list):
        days = [int(d) for d in days if str(d).strip().isdigit()]
    else:
        days = []
    tid = str(payload.get("id") or "").strip()
    fields: dict[str, Any] = dict(
        label=str(payload.get("label") or payload.get("name") or "").strip(),
        hour=int(payload.get("hour", 9)),
        minute=int(payload.get("minute", 0)),
        repeat_mode=RepeatMode.parse(payload.get("repeatMode")),
        custom_days=frozenset(d for d in days if d in DAYS_OF_WEEK),
        prompt=str(payload.get("prompt") or ""),
        target_mode=TargetMode.decode(payload.get("targetMode")),
        model_id=(str(payload.get("modelId") or "").strip() or None),
        model_binding=(str(payload.get("modelBinding") or "").strip() or None),
        enabled=bool(payload.get("enabled", True)),
        start_date_ms=payload.get("startDateMs"),
        end_date_ms=payload.get("endDateMs"),
    )
    if tid:  # omitted → the model mints a fresh uuid
        fields["id"] = tid
    return ScheduledTask(**fields)
