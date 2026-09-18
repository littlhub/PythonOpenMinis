"""[T-live-stream] 干活时的实时可见性 —— 网页端与 IM 两端都不该「憋到最后」。

用户原话：「工具调用没有实时流式显示，只在最后一轮结束才显示」。查下来后端其实是
**实时发帧**的（``ToolCallComplete`` 一产生就推 ``toolStart``，每个工具跑完就推
``toolEnd``），问题出在两端「看不见」：

1. **网页端**：一轮对话可能由**机器人通道**发起，帧只发给那个虚拟订阅者
   （``bot:…``），浏览器一个字节都收不到 —— 只能等回合结束重新拉历史。
   现在 ``_safe_send`` 会把这类帧抄送给所有浏览器客户端（前端按 sessionId 分桶，
   不关心的会话自然忽略）。
2. **IM 端**：平台的「正在输入」只活几秒，而带工具的回合常跑几分钟。现在跑一轮
   期间会**持续刷新**输入状态（不算被动回复次数，不占额度）。
"""

from __future__ import annotations

from typing import Any

import pytest

from openminis.server import main as server_main


class _FakeWS:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.frames.append(payload)


@pytest.mark.asyncio
async def test_bot_frames_are_broadcast_to_browsers(monkeypatch):
    """机器人通道跑的会话，帧要抄送一份给浏览器，否则网页端只能等回合结束。"""
    manager = server_main.ConnectionManager()
    browser = _FakeWS()
    bot_sink = _FakeWS()
    manager.active["c1"] = browser            # 浏览器
    manager.active["bot:qq-bot:group:G1"] = bot_sink   # 这一轮的发起者（机器人订阅者）
    monkeypatch.setattr(server_main, "manager", manager)

    await server_main._safe_send(
        "bot:qq-bot:group:G1",
        {"type": "toolStart", "id": "t1", "name": "read_image", "sessionId": "s1"},
    )
    # 发起者收一份（本来就发它），浏览器再抄一份
    assert [f["type"] for f in bot_sink.frames] == ["toolStart"]
    assert [f["type"] for f in browser.frames] == ["toolStart"]


@pytest.mark.asyncio
async def test_browser_frames_are_not_broadcast(monkeypatch):
    """浏览器发起的回合不用再抄送 —— 它自己就收到了，抄送等于重复推。"""
    manager = server_main.ConnectionManager()
    a, b = _FakeWS(), _FakeWS()
    manager.active["c1"] = a
    manager.active["c2"] = b
    monkeypatch.setattr(server_main, "manager", manager)

    await server_main._safe_send("c1", {"type": "delta", "text": "hi", "sessionId": "s1"})
    assert len(a.frames) == 1
    assert b.frames == []


@pytest.mark.asyncio
async def test_frames_without_session_are_not_broadcast(monkeypatch):
    """没有 sessionId 的帧（连接级提示）不该乱抄 —— 前端无从分桶。"""
    manager = server_main.ConnectionManager()
    browser = _FakeWS()
    manager.active["c1"] = browser
    monkeypatch.setattr(server_main, "manager", manager)

    await server_main._safe_send("bot:x", {"type": "notice", "text": "hi"})
    assert browser.frames == []


@pytest.mark.asyncio
async def test_broadcast_survives_a_dead_browser(monkeypatch):
    """某个连接坏了不能影响别人收到帧。"""

    class _DeadWS:
        async def send_json(self, payload: dict[str, Any]) -> None:
            raise RuntimeError("socket closed")

    manager = server_main.ConnectionManager()
    alive = _FakeWS()
    manager.active["c1"] = _DeadWS()
    manager.active["c2"] = alive

    await manager.broadcast_web({"type": "delta", "text": "x", "sessionId": "s"})
    assert len(alive.frames) == 1
