"""[T-subagent-log-persist] 子代理过程落库。

背景（用户的真实反馈）：工具调用切窗口就不见了。根因不是主代理那条路
（它已经随消息落库），而是**子代理**那条路 —— 子代理的发言与它调用的工具原先
只以 WebSocket 帧存在，刷新页面、重连后重拉历史、重启后端之后整段消失。开着
子代理时，绝大部分工具调用其实都发生在子代理里。

这一组测试钉住：

1. 事件流能被正确折叠成「一次委派 = 一段过程」（顺序、拼接、工具配对）；
2. 落库/读回往返完整，且**不进模型上下文**（正文不在 text part 里）；
3. 真走一遍 WS 对话链路：子代理过程落库，顺序在最终答复之前。
"""

from __future__ import annotations

import pytest

from openminis.server.sub_turn_log import SubTurnRecorder


def _ev(kind: str, **kw) -> dict:
    base = {
        "id": "room1",
        "subagentId": "sub-1",
        "name": "研究员",
        "emoji": "🔍",
        "project": "/tmp/proj",
        "type": kind,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# SubTurnRecorder：事件流 → 过程
# ---------------------------------------------------------------------------
def test_folds_one_delegation_into_a_turn():
    rec = SubTurnRecorder()
    rec.on_event(_ev("subagentStart", task="查一下今天的新闻"))
    rec.on_event(_ev("subagentDelta", text="我先搜"))
    rec.on_event(_ev("subagentDelta", text="一圈。"))
    rec.on_event(_ev("subagentToolStart", callId="t1", name="web_search",
                     input={"q": "新闻"}))
    rec.on_event(_ev("subagentToolEnd", callId="t1", name="web_search",
                     ok=True, output="2026-09-17 新闻…"))

    turns = rec.turns()
    assert len(turns) == 1
    turn = turns[0]
    assert turn.speaker["name"] == "研究员"
    assert turn.speaker["emoji"] == "🔍"
    assert turn.room_id == "room1"
    assert turn.task == "查一下今天的新闻"
    assert turn.text == "我先搜一圈。"
    assert len(turn.runs) == 1
    run = turn.runs[0]
    assert run["id"] == "t1" and run["name"] == "web_search"
    assert run["input"] == {"q": "新闻"}
    assert run["ok"] is True and run["output"] == "2026-09-17 新闻…"
    assert run["ms"] >= 0


def test_two_delegations_stay_separate_and_ordered():
    rec = SubTurnRecorder()
    rec.on_event(_ev("subagentStart", task="A", id="roomA", subagentId="s-a"))
    rec.on_event(_ev("subagentDelta", text="甲", id="roomA", subagentId="s-a"))
    rec.on_event(_ev("subagentStart", task="B", id="roomB", subagentId="s-b"))
    rec.on_event(_ev("subagentDelta", text="乙", id="roomB", subagentId="s-b"))

    turns = rec.turns()
    assert [t.text for t in turns] == ["甲", "乙"]
    assert [t.task for t in turns] == ["A", "B"]


def test_event_without_start_still_keeps_the_process():
    """先收到过程（理论上不会）也不能丢 —— 补一段出来。"""
    rec = SubTurnRecorder()
    rec.on_event(_ev("subagentToolStart", callId="t9", name="shell", input={}))
    turns = rec.turns()
    assert len(turns) == 1
    assert turns[0].runs[0]["name"] == "shell"


def test_tool_end_without_matching_start_is_ignored():
    rec = SubTurnRecorder()
    rec.on_event(_ev("subagentStart"))
    rec.on_event(_ev("subagentToolEnd", callId="nope", name="shell", ok=True,
                     output="x"))
    assert rec.turns()[0].runs == []


def test_plain_frames_are_ignored():
    rec = SubTurnRecorder()
    rec.on_event({"type": "delta", "text": "主代理在说话"})
    rec.on_event({"type": "toolStart", "id": "x"})
    rec.on_event(None)
    assert rec.turns() == []


def test_long_output_is_clipped():
    rec = SubTurnRecorder()
    rec.on_event(_ev("subagentStart"))
    rec.on_event(_ev("subagentToolStart", callId="t1", name="shell", input={}))
    rec.on_event(_ev("subagentToolEnd", callId="t1", name="shell", ok=True,
                     output="A" * 20_000))
    body = rec.turns()[0].runs[0]["output"]
    assert len(body) < 20_000
    assert body.startswith("A" * 100)
    assert "已截断" in body


def test_running_tool_has_no_ok_yet():
    """还没回来时只记调用 —— 前端靠 ok 缺省判断「执行中」。"""
    rec = SubTurnRecorder()
    rec.on_event(_ev("subagentStart"))
    rec.on_event(_ev("subagentToolStart", callId="t1", name="shell", input={}))
    run = rec.turns()[0].runs[0]
    assert "ok" not in run and "output" not in run


# ---------------------------------------------------------------------------
# 落库 / 读回
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sub_turn_roundtrip_and_out_of_context(tmp_path, monkeypatch):
    """落库 → 读回能还原；但历史重建（进上下文那条路）看不见它。"""
    from openminis.core import context
    from openminis.server import chat_store

    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    chat_store.set_database_path(tmp_path / "sub.db")

    sess = (await chat_store.create_session()).id
    await chat_store.append_turn(sess, "user", "查一下新闻")
    await chat_store.append_sub_turn(
        sess,
        speaker={"subagentId": "sub-1", "name": "研究员", "emoji": "🔍"},
        task="查新闻",
        room_id="room1",
        text="我搜到了三条。",
        runs=[{"id": "t1", "name": "web_search", "input": {"q": "x"},
               "ok": True, "output": "结果"}],
    )
    await chat_store.append_turn(sess, "assistant", "总结如下")

    rows = await chat_store.load_messages(sess)
    assert [r.role for r in rows] == ["user", "assistant", "assistant"]
    sub = rows[1]
    assert sub.sub is not None
    assert sub.sub["text"] == "我搜到了三条。"
    assert sub.sub["task"] == "查新闻"
    assert sub.sub["roomId"] == "room1"
    assert sub.sub["speaker"]["name"] == "研究员"
    # 正文不写进 text part —— 所以界面上取不到「正文」、模型也读不到。
    assert sub.text == ""
    assert [r["name"] for r in (sub.runs or [])] == ["web_search"]

    # 进模型上下文的那条路：子代理正文与工具输出都不该出现。
    chat_store.drop_runtime(sess)
    history = await chat_store.load_runtime_history(sess)
    joined = "\n".join(m.content for m in history)
    assert "我搜到了三条。" not in joined
    assert "web_search" not in joined
    assert "总结如下" in joined


@pytest.mark.asyncio
async def test_sub_turn_does_not_hijack_session_preview(tmp_path, monkeypatch):
    """子代理那句自言自语不该变成侧边栏的会话预览。"""
    from openminis.core import context
    from openminis.server import chat_store

    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    chat_store.set_database_path(tmp_path / "sub2.db")

    sess = (await chat_store.create_session()).id
    await chat_store.append_turn(sess, "user", "干活")
    await chat_store.append_sub_turn(
        sess, speaker={"name": "研究员"}, task="t", room_id="r",
        text="我这就去做", runs=None,
    )
    info = await chat_store.get_session(sess)
    assert info is not None
    assert "我这就去做" not in (info.lastMessage or "")


# ---------------------------------------------------------------------------
# 端到端：真走一遍 WS 对话链路
# ---------------------------------------------------------------------------
class _SubEmittingRuntime:
    """假的运行时：跑的时候把子代理事件吐出来，然后给个最终答复。"""

    async def run(self, provider, messages, session_id=None, options=None):
        from openminis.agent.subagent_events import emit
        from openminis.data.model import LLMMessage
        from openminis.data.model.agent_content_part import Text

        await emit(_ev("subagentStart", task="查新闻"))
        await emit(_ev("subagentDelta", text="正在搜"))
        await emit(_ev("subagentToolStart", callId="t1", name="web_search",
                       input={"q": "新闻"}))
        await emit(_ev("subagentToolEnd", callId="t1", name="web_search",
                       ok=True, output="三条结果"))
        # 真运行时把流式 chunk 组装成 content_parts；假运行时也得照做，
        # 否则 _run_chat 取不到正文（它只读 Text part）。
        messages.append(
            LLMMessage(LLMMessage.Role.ASSISTANT, "", content_parts=[Text("总结如下")])
        )
        return messages, None


@pytest.mark.asyncio
async def test_ws_turn_persists_subagent_process(tmp_path, monkeypatch):
    from openminis.core import context
    from openminis.server import chat_store
    from openminis.server import main as server_main

    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    chat_store.set_database_path(tmp_path / "ws_sub.db")
    try:
        runtime = _SubEmittingRuntime()
        seen_setup: dict[str, object] = {}

        def fake_setup(store, session_id=None, identity_id=None, on_fallback=None, **_kw):
            from openminis.agent.agent_runtime import AgentRuntimeOptions

            seen_setup["session_id"] = session_id
            seen_setup["identity_id"] = identity_id
            return (object(), runtime, AgentRuntimeOptions(), "你是助手",
                    {"id": "gw", "model": "m1"})

        monkeypatch.setattr(server_main, "build_chat_setup", fake_setup)
        sent: list[dict] = []

        async def fake_send(_cid: str, payload: dict) -> None:
            sent.append(payload)

        monkeypatch.setattr(server_main, "_safe_send", fake_send)

        # 带了 identityId（通道插件指定「由哪个 agent 接待」）就要原样传下去
        await server_main._run_chat("c1", {"text": "查一下新闻", "identityId": "coder"})
        assert seen_setup["identity_id"] == "coder"

        # 推流照旧（前端实时还是能看到过程）
        kinds = [f["type"] for f in sent]
        for want in ("subagentStart", "subagentDelta", "subagentToolStart",
                     "subagentToolEnd", "done"):
            assert want in kinds

        sessions = await chat_store.list_sessions()
        rows = await chat_store.load_messages(sessions[0].id)
        # 顺序：提问 → 子代理过程 → 最终答复
        assert [r.role for r in rows] == ["user", "assistant", "assistant"]
        sub = rows[1]
        assert sub.sub is not None
        assert sub.sub["text"] == "正在搜"
        assert sub.sub["task"] == "查新闻"
        assert [r["name"] for r in (sub.runs or [])] == ["web_search"]
        assert sub.runs[0]["output"] == "三条结果"
        assert rows[2].text == "总结如下"
    finally:
        chat_store.drop_runtime("c1")
