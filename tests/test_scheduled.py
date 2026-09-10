"""Scheduled tasks: model / store / REST / runner.

The list screen used to render hard-coded rows in React state — deleting one
only mutated local state, so a refresh brought it back. These tests pin the
behaviour that replaced it: every mutation goes through the file-backed store.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from openminis.scheduled.model import (
    RepeatMode,
    ScheduledTask,
    ScheduledTaskError,
    TargetMode,
)
from openminis.scheduled.runner import ScheduledRunner
from openminis.scheduled.store import ScheduledTaskStore
from openminis.server.main import app


@pytest.fixture()
def store(tmp_path):
    return ScheduledTaskStore(path=tmp_path / "scheduled" / "tasks.json")


def _ms(hour: int, minute: int = 0, day_offset: int = 0) -> int:
    """Epoch ms for today's (or +N days) HH:MM local time."""
    import datetime as dt

    day = dt.date.today() + dt.timedelta(days=day_offset)
    when = dt.datetime.combine(day, dt.time(hour, minute))
    return int(when.timestamp() * 1000)


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
def test_next_trigger_daily_rolls_to_tomorrow_when_time_passed():
    now = _ms(10, 0)
    task = ScheduledTask(label="早报", hour=8, minute=0, repeat_mode=RepeatMode.DAILY)
    nxt = task.next_trigger_ms(now)
    assert nxt is not None and nxt > now
    import datetime as dt

    assert dt.datetime.fromtimestamp(nxt / 1000).hour == 8


def test_disabled_task_has_no_next_trigger():
    task = ScheduledTask(label="x", enabled=False)
    assert task.next_trigger_ms() is None


def test_custom_without_days_has_no_trigger():
    task = ScheduledTask(label="x", repeat_mode=RepeatMode.CUSTOM)
    assert task.next_trigger_ms() is None


def test_custom_days_are_calendar_day_of_week():
    """Calendar.DAY_OF_WEEK: 1=Sun … 7=Sat → Python weekday Mon=0."""
    task = ScheduledTask(
        label="x", hour=9, repeat_mode=RepeatMode.CUSTOM, custom_days=frozenset({2})  # Monday
    )
    import datetime as dt

    nxt = task.next_trigger_ms()
    assert nxt is not None
    assert dt.datetime.fromtimestamp(nxt / 1000).weekday() == 0


def test_end_date_disables_future_slot():
    today_end = _ms(23, 59, day_offset=0)
    task = ScheduledTask(
        label="x", hour=9, repeat_mode=RepeatMode.DAILY, end_date_ms=today_end
    )
    # 09:00 already passed today → candidate is tomorrow, past the window
    now = _ms(10, 0)
    assert task.next_trigger_ms(now) is None


def test_target_mode_roundtrip():
    assert TargetMode.decode("NEW_SESSION").kind == "NEW_SESSION"
    assert TargetMode.decode("APPEND_TO:abc").session_id == "abc"
    rerun = TargetMode.decode("RERUN:sess:msg")
    assert (rerun.kind, rerun.session_id, rerun.message_id) == ("RERUN", "sess", "msg")
    assert rerun.encode() == "RERUN:sess:msg"


def test_run_history_is_capped():
    task = ScheduledTask(label="x")
    for _ in range(60):
        task.record_run(True, "ok")
    assert len(task.run_history) == 50
    assert task.last_fired_at is not None


def test_invalid_hour_rejected():
    with pytest.raises(ScheduledTaskError):
        ScheduledTask(label="x", hour=24)


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
def test_store_roundtrip_and_delete_persists(store):
    task = ScheduledTask(label="每日早报", hour=8, prompt="hi")
    store.upsert(task)
    assert [t.label for t in store.all()] == ["每日早报"]

    # a fresh store (simulating a restart / page refresh) sees the same row
    assert len(ScheduledTaskStore(path=store.path).all()) == 1

    assert store.delete(task.id) is True
    assert store.all() == []
    # ... and it stays deleted after a "refresh"
    reloaded = ScheduledTaskStore(path=store.path)
    assert reloaded.all() == []
    assert json.loads(store.path.read_text(encoding="utf-8"))["tasks"] == {}


def test_delete_missing_returns_false(store):
    assert store.delete("nope") is False


def test_toggle_enabled_persists(store):
    task = ScheduledTask(label="x")
    store.upsert(task)
    assert store.set_enabled(task.id, False) is not None
    assert ScheduledTaskStore(path=store.path).get(task.id).enabled is False


def test_malformed_row_is_skipped(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"tasks": {"bad": {"hour": 99}, "ok": ScheduledTask(label="好").to_json()}}),
        encoding="utf-8",
    )
    assert [t.label for t in store.all()] == ["好"]


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(store, monkeypatch):
    monkeypatch.setattr(
        "openminis.server.scheduled_api._store", lambda: store
    )
    with TestClient(app) as c:
        yield c


def test_rest_create_list_delete(client):
    r = client.post(
        "/api/scheduled/tasks",
        json={"label": "每日早报", "hour": 8, "minute": 30,
              "repeatMode": "DAILY", "prompt": "总结新闻"},
    )
    assert r.status_code == 200, r.text
    tid = r.json()["task"]["id"]

    listed = client.get("/api/scheduled/tasks").json()["tasks"]
    assert [t["label"] for t in listed] == ["每日早报"]
    assert listed[0]["nextTriggerMs"] is not None

    assert client.delete(f"/api/scheduled/tasks/{tid}").status_code == 200
    assert client.get("/api/scheduled/tasks").json()["tasks"] == []
    assert client.delete(f"/api/scheduled/tasks/{tid}").status_code == 404


def test_rest_rejects_empty_prompt(client):
    r = client.post("/api/scheduled/tasks", json={"label": "x", "prompt": "  "})
    assert r.status_code == 400


def test_rest_toggle_and_runs(client):
    tid = client.post(
        "/api/scheduled/tasks", json={"label": "x", "prompt": "y"}
    ).json()["task"]["id"]
    r = client.post(f"/api/scheduled/tasks/{tid}/toggle", json={"enabled": False})
    assert r.json()["task"]["enabled"] is False
    assert client.get(f"/api/scheduled/tasks/{tid}/runs").json()["runs"] == []


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_runner_fires_due_task_once(store):
    calls: list[str] = []

    async def fake_execute(prompt: str, session_id: str | None, **_kw):
        calls.append(prompt)
        return True, "done", "sess-1"

    import datetime as dt

    slot = dt.datetime.now() - dt.timedelta(minutes=2)  # already due today
    task = ScheduledTask(label="due", hour=slot.hour, minute=slot.minute, prompt="go")
    store.upsert(task)

    runner = ScheduledRunner(store=store, execute_fn=fake_execute)
    assert await runner.tick() == 1
    assert calls == ["go"]

    stored = store.get(task.id)
    assert stored.repeat_mode is RepeatMode.ONCE
    assert stored.enabled is False          # one-shot auto-disables
    assert stored.last_result_session_id == "sess-1"
    assert len(stored.run_history) == 1

    # second tick must not re-fire
    assert await runner.tick() == 0
    assert calls == ["go"]


@pytest.mark.asyncio
async def test_runner_skips_future_and_disabled(store):
    async def never(prompt: str, session_id: str | None, **_kw):
        raise AssertionError("should not fire")

    future = ScheduledTask(label="later", hour=23, minute=59, repeat_mode=RepeatMode.DAILY)
    off = ScheduledTask(label="off", hour=0, minute=0, enabled=False)
    store.upsert(future)
    store.upsert(off)
    runner = ScheduledRunner(store=store, execute_fn=never)
    assert await runner.tick() == 0


@pytest.mark.asyncio
async def test_runner_passes_the_tasks_pinned_model(store):
    """定时任务是无人值守的:必须用创建时钉住的模型,而不是「当前」模型。

    runner 只负责把 task 上的两个字段透传给执行器 —— 一旦这里丢了,
    execute_prompt 就会退回活动配置,任务行为会随用户切换模型而改变,
    而且是在没人看着的时候。
    """
    seen: list[dict] = []

    async def fake_execute(prompt, session_id, *, model_id=None, instance_id=None):
        seen.append({"model_id": model_id, "instance_id": instance_id})
        return True, "ok", "s1"

    import datetime as dt

    slot = dt.datetime.now() - dt.timedelta(minutes=2)
    task = ScheduledTask(
        label="pinned",
        hour=slot.hour,
        minute=slot.minute,
        prompt="go",
        model_id="agnes-2.5-flash",
        model_binding="openAI",
    )
    store.upsert(task)

    runner = ScheduledRunner(store=store, execute_fn=fake_execute)
    assert await runner.tick() == 1
    assert seen == [{"model_id": "agnes-2.5-flash", "instance_id": "openAI"}]


@pytest.mark.asyncio
async def test_runner_passes_none_when_no_model_pinned(store):
    """没钉模型时显式传 None(而不是漏传),执行器才能区分「跟随当前」与
    「参数丢了」这两种情况。"""
    seen: list[tuple[str | None, str | None]] = []

    async def fake_execute(prompt, session_id, *, model_id=None, instance_id=None):
        seen.append((model_id, instance_id))
        return True, "ok", "s1"

    import datetime as dt

    slot = dt.datetime.now() - dt.timedelta(minutes=2)
    task = ScheduledTask(label="free", hour=slot.hour, minute=slot.minute, prompt="go")
    store.upsert(task)

    runner = ScheduledRunner(store=store, execute_fn=fake_execute)
    await runner.tick()
    assert seen == [(None, None)]


def test_rest_keeps_the_pinned_model(client):
    """模型选择必须落盘并回读 —— 否则表单填了、刷新就没了。"""
    r = client.post(
        "/api/scheduled/tasks",
        json={
            "label": "pinned",
            "hour": 7,
            "minute": 15,
            "repeatMode": "DAILY",
            "prompt": "早报",
            "modelId": "agnes-2.5-flash",
            "modelBinding": "openAI",
        },
    )
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["modelId"] == "agnes-2.5-flash"
    assert task["modelBinding"] == "openAI"

    listed = client.get("/api/scheduled/tasks").json()["tasks"]
    assert listed[0]["modelId"] == "agnes-2.5-flash"
    assert listed[0]["modelBinding"] == "openAI"


def test_rest_treats_blank_model_as_follow_current(client):
    """空字符串要归一化成 null,不能存成 "" —— 那会让「跟随当前」变成
    「pin 一个叫空字符串的模型」。"""
    r = client.post(
        "/api/scheduled/tasks",
        json={
            "label": "free",
            "hour": 7,
            "minute": 15,
            "repeatMode": "DAILY",
            "prompt": "早报",
            "modelId": "   ",
            "modelBinding": "",
        },
    )
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task.get("modelId") is None
    assert task.get("modelBinding") is None


@pytest.mark.asyncio
async def test_runner_passes_the_tasks_pinned_model(store):
    """定时任务是无人值守的:必须用创建时钉住的模型,而不是「当前」模型。

    runner 只负责把 task 上的两个字段透传给执行器 —— 一旦这里丢了,
    execute_prompt 就会退回活动配置,任务行为会随用户切换模型而改变,
    而且是在没人看着的时候。
    """
    seen: list[dict] = []

    async def fake_execute(prompt, session_id, *, model_id=None, instance_id=None):
        seen.append({"model_id": model_id, "instance_id": instance_id})
        return True, "ok", "s1"

    import datetime as dt

    slot = dt.datetime.now() - dt.timedelta(minutes=2)
    task = ScheduledTask(
        label="pinned",
        hour=slot.hour,
        minute=slot.minute,
        prompt="go",
        model_id="agnes-2.5-flash",
        model_binding="openAI",
    )
    store.upsert(task)

    runner = ScheduledRunner(store=store, execute_fn=fake_execute)
    assert await runner.tick() == 1
    assert seen == [{"model_id": "agnes-2.5-flash", "instance_id": "openAI"}]


@pytest.mark.asyncio
async def test_runner_passes_none_when_no_model_pinned(store):
    """没钉模型时显式传 None(而不是漏传),执行器才能区分「跟随当前」与
    「参数丢了」这两种情况。"""
    seen: list[tuple[str | None, str | None]] = []

    async def fake_execute(prompt, session_id, *, model_id=None, instance_id=None):
        seen.append((model_id, instance_id))
        return True, "ok", "s1"

    import datetime as dt

    slot = dt.datetime.now() - dt.timedelta(minutes=2)
    task = ScheduledTask(label="free", hour=slot.hour, minute=slot.minute, prompt="go")
    store.upsert(task)

    runner = ScheduledRunner(store=store, execute_fn=fake_execute)
    await runner.tick()
    assert seen == [(None, None)]


def test_rest_keeps_the_pinned_model(client):
    """模型选择必须落盘并回读 —— 否则表单填了、刷新就没了。"""
    r = client.post(
        "/api/scheduled/tasks",
        json={
            "label": "pinned",
            "hour": 7,
            "minute": 15,
            "repeatMode": "DAILY",
            "prompt": "早报",
            "modelId": "agnes-2.5-flash",
            "modelBinding": "openAI",
        },
    )
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task["modelId"] == "agnes-2.5-flash"
    assert task["modelBinding"] == "openAI"

    listed = client.get("/api/scheduled/tasks").json()["tasks"]
    assert listed[0]["modelId"] == "agnes-2.5-flash"
    assert listed[0]["modelBinding"] == "openAI"


def test_rest_treats_blank_model_as_follow_current(client):
    """空字符串要归一化成 null,不能存成 "" —— 那会让「跟随当前」变成
    「pin 一个叫空字符串的模型」。"""
    r = client.post(
        "/api/scheduled/tasks",
        json={
            "label": "free",
            "hour": 7,
            "minute": 15,
            "repeatMode": "DAILY",
            "prompt": "早报",
            "modelId": "   ",
            "modelBinding": "",
        },
    )
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task.get("modelId") is None
    assert task.get("modelBinding") is None
