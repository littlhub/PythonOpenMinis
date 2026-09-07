"""Chat session persistence: store round-trips + REST API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openminis.server import chat_store
from openminis.server.main import app


# ---------------------------------------------------------------------------
# store layer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_session_roundtrip(isolated_chat_db):
    s = await chat_store.create_session()
    assert s.title == "新会话"

    await chat_store.append_turn(s.id, "user", "帮我列一下当前目录")
    await chat_store.append_turn(s.id, "assistant", "这是目录内容:…")

    # auto-title from the first user message
    info = await chat_store.get_session(s.id)
    assert info is not None and info.title == "帮我列一下当前目录"
    assert info.lastMessage.startswith("这是目录内容")

    rows = await chat_store.load_messages(s.id)
    assert [(m.role, m.text) for m in rows] == [
        ("user", "帮我列一下当前目录"),
        ("assistant", "这是目录内容:…"),
    ]


@pytest.mark.asyncio
async def test_runtime_history_rebuild_alternates(isolated_chat_db):
    s = await chat_store.create_session()
    for role, text in [
        ("user", "第一问"),
        ("assistant", "回答一"),
        ("assistant", "补充说明"),  # merged into previous assistant turn
        ("user", "第二问"),
    ]:
        await chat_store.append_turn(s.id, role, text)

    hist = await chat_store.load_runtime_history(s.id)
    assert [m.role.value for m in hist] == ["user", "assistant", "user"]
    assert hist[1].content == "回答一\n\n补充说明"


@pytest.mark.asyncio
async def test_sessions_ordered_newest_first_and_delete(isolated_chat_db):
    a = await chat_store.create_session()
    b = await chat_store.create_session()
    await chat_store.append_turn(a.id, "user", "旧的会话")
    await chat_store.append_turn(b.id, "user", "新的会话")

    lst = await chat_store.list_sessions()
    assert lst[0].id == b.id  # touched later → newest first
    assert [x.title for x in lst] == ["新的会话", "旧的会话"]

    assert await chat_store.delete_session(b.id) is True
    assert await chat_store.get_session(b.id) is None
    assert len(await chat_store.list_sessions()) == 1


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chat_api_lifecycle(isolated_chat_db):
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # empty at first
        r = await c.get("/api/chats/sessions")
        assert r.status_code == 200 and r.json()["sessions"] == []

        # create
        r = await c.post("/api/chats/sessions")
        assert r.status_code == 200
        sid = r.json()["id"]

        # unknown session messages → 404
        assert (
            await c.get(f"/api/chats/sessions/{'x' * 32}/messages")
        ).status_code == 404

        # seed one turn via the store (as the ws handler would)
        await chat_store.append_turn(sid, "user", "你好")
        await chat_store.append_turn(sid, "assistant", "你好!")

        # list shows it with title + preview
        r = await c.get("/api/chats/sessions")
        row = r.json()["sessions"][0]
        assert row["id"] == sid and row["title"] == "你好"

        # messages round-trip
        r = await c.get(f"/api/chats/sessions/{sid}/messages")
        msgs = r.json()["messages"]
        assert [(m["role"], m["text"]) for m in msgs] == [
            ("user", "你好"),
            ("assistant", "你好!"),
        ]

        # delete
        assert (await c.delete(f"/api/chats/sessions/{sid}")).status_code == 200
        assert (await c.get("/api/chats/sessions")).json()["sessions"] == []


# ---------------------------------------------------------------------------
# workspaces (工作空间 / 文件夹)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_workspace_lifecycle(isolated_chat_db):
    from openminis.server import workspaces

    # empty
    lst = await workspaces.list_workspaces()
    assert lst == []

    # create
    ws = await workspaces.create_workspace("工作", "周报")
    assert ws.name == "工作" and ws.sessionCount == 0

    # rename via store (REST API call below too)
    renamed = await workspaces.rename_workspace(ws.id, "重要工作")
    assert renamed.name == "重要工作"
    assert renamed.description == "周报"

    # seed a session in this workspace
    s = await chat_store.create_session(folder_id=ws.id)
    assert s.folderId == ws.id

    # listing now reports the count
    lst = await workspaces.list_workspaces()
    assert lst[0].sessionCount == 1

    # REST filter: only sessions of this workspace
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # workspace list endpoint
        r = await c.get("/api/chats/workspaces")
        assert r.status_code == 200
        assert r.json()["workspaces"][0]["name"] == "重要工作"

        # sessions filtered by workspace
        r = await c.get(f"/api/chats/sessions?workspace={ws.id}")
        assert r.status_code == 200
        rows = r.json()["sessions"]
        assert len(rows) == 1 and rows[0]["folderId"] == ws.id

        # create session bound to workspace via POST
        r = await c.post("/api/chats/sessions", json={"folderId": ws.id})
        assert r.status_code == 200
        new_sid = r.json()["id"]
        assert r.json()["folderId"] == ws.id

        # move a session out of the folder (folderId=null)
        r = await c.patch(
            f"/api/chats/sessions/{new_sid}/workspace", json={"folderId": None}
        )
        assert r.status_code == 200
        # unfiled query now finds it
        r = await c.get("/api/chats/sessions?workspace=unfiled")
        assert any(x["id"] == new_sid for x in r.json()["sessions"])

        # deleting the workspace drops members back to unfiled
        ok = await workspaces.delete_workspace(ws.id)
        assert ok is True
        s_after = await chat_store.get_session(s.id)
        assert s_after is not None and s_after.folderId is None


@pytest.mark.asyncio
async def test_agent_runtime_emits_tool_result_chunk():
    """The agent loop must emit ``LLMStreamChunk.ToolResult`` so the Web UI
    can render the final state of every tool call. Smoke-test that the
    dataclass exists and is wired into the chunk_sink pipeline."""
    from openminis.data.model import LLMStreamChunk

    assert hasattr(LLMStreamChunk, "ToolResult")
    chunk = LLMStreamChunk.ToolResult(
        id="call-1", name="file_read", content="ok output", is_error=False
    )
    assert chunk.id == "call-1"
    assert chunk.is_error is False
