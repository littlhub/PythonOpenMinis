"""WebSocket 控制帧：生成中「暂停」（stop）。

对应前端发送键旁边的 ⏹：一轮对话跑在后台 task 里，所以 stop 帧能在本轮
生成过程中被读到并取消它；取消后由 stop 处理函数回一个
``{"type": "done", "stopped": true}``。
"""

from __future__ import annotations

import asyncio

import pytest

from openminis.core import context
from openminis.server import chat_store  # noqa: F401  (db init consistency)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    yield tmp_path
    context._context = None


def test_stop_cancels_running_turn(env, monkeypatch):
    from openminis.server import main as server_main

    async def scenario() -> None:
        cancelled = asyncio.Event()

        async def stuck() -> None:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        sent: list[dict] = []

        async def fake_send(_cid: str, payload: dict) -> None:
            sent.append(payload)

        monkeypatch.setattr(server_main, "_safe_send", fake_send)
        server_main._RUNNING_CHATS.clear()
        task = asyncio.create_task(stuck())
        await asyncio.sleep(0)  # 让它真的跑起来
        server_main._RUNNING_CHATS["s1"] = task
        task.add_done_callback(lambda _t: server_main._RUNNING_CHATS.pop("s1", None))

        await server_main._handle_stop("c1", {"sessionId": "s1"})

        assert cancelled.is_set(), "运行中的轮次应被取消"
        assert sent and sent[-1]["type"] == "done"
        assert sent[-1]["stopped"] is True
        assert sent[-1]["sessionId"] == "s1"
        await asyncio.sleep(0)
        assert "s1" not in server_main._RUNNING_CHATS
        server_main._RUNNING_CHATS.clear()

    asyncio.run(scenario())


def test_stop_without_running_turn_still_acks(env, monkeypatch):
    from openminis.server import main as server_main

    async def scenario() -> None:
        sent: list[dict] = []

        async def fake_send(_cid: str, payload: dict) -> None:
            sent.append(payload)

        monkeypatch.setattr(server_main, "_safe_send", fake_send)
        server_main._RUNNING_CHATS.clear()
        await server_main._handle_stop("c1", {"sessionId": "nope"})
        # 没有在跑的轮次也要回一个 stopped，前端才能把 busy 关掉
        assert sent == [{"type": "done", "sessionId": "nope", "stopped": True}]

    asyncio.run(scenario())


def test_duplicate_chat_frame_is_rejected_while_running(env, monkeypatch):
    """同一会话已有轮次在跑 → 直接拒绝（前端负责排队）。"""
    from openminis.server import main as server_main

    async def scenario() -> None:
        sent: list[dict] = []

        async def fake_send(_cid: str, payload: dict) -> None:
            sent.append(payload)

        monkeypatch.setattr(server_main, "_safe_send", fake_send)
        server_main._RUNNING_CHATS.clear()
        running = asyncio.create_task(asyncio.sleep(5))
        server_main._RUNNING_CHATS["s2"] = running
        try:
            await server_main._handle_chat(
                "c1", {"text": "再来一条", "sessionId": "s2"}
            )
            assert sent and sent[-1]["type"] == "error"
            assert "处理中" in sent[-1]["error"]
            assert not running.done()
        finally:
            running.cancel()
            await asyncio.wait({running})
            server_main._RUNNING_CHATS.clear()

    asyncio.run(scenario())
