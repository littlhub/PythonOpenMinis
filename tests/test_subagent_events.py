"""群聊可视化：子代理活动 → 前端事件。

主代理委派子代理时，子代理的内层循环本来是**静默**的（用户只看到一张
`subagent_delegate` 工具卡）。`openminis.agent.subagent_events` 用 contextvar
把内层循环接出去，前端把它们渲染成群聊气泡；`agent.subagents.group_block`
则把「拉进群的子代理」写进系统提示，让主代理认识这些同事。

这里覆盖：事件总线的空操作语义、房间号取自外层工具调用、过程事件的映射、
「分配项目」的上下文，以及群成员提示块只列真实存在的 id。
"""

from __future__ import annotations

import json

import pytest

from openminis.agent.subagent_events import (
    active,
    current_tool_use,
    emit,
    project_for,
    push_tool_use,
    reset_emitter,
    reset_tool_use,
    set_emitter,
    set_projects,
)
from openminis.agent.agent_runtime import AgentRuntime, AgentRuntimeOptions, ToolExecutor
from openminis.agent.subagents import group_block, upsert_subagent
from openminis.data.model import LLMStreamChunk, LLMMessage
from openminis.data.model.agent_tool_definition import (
    AgentToolDefinition,
    AgentToolParam,
)
from openminis.settings import chat_service
from openminis.settings.store import SettingsStore
from openminis.tools.subagent_tool import SubagentDelegateTool
from openminis.tools.tool_execution_result import ToolExecutionResult


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = SettingsStore(path=tmp_path / "settings.json")
    monkeypatch.setattr(SettingsStore, "get", classmethod(lambda cls: s))
    return s


def _configure(store):
    store.apply_full({
        "providers": [{"type": "anthropic", "apiKey": "sk-test",
                       "model": "claude-sonnet-5", "baseUrl": ""}],
        "activeProviderId": "anthropic",
    })


def _add_writer(store, tools=None):
    return upsert_subagent(store, {
        "id": "writer", "name": "写作助手", "emoji": "✍️",
        "providerType": "anthropic", "model": "claude-sonnet-5",
        "tools": tools or [],
    })


class _TextProvider:
    """子代理只说一句话就收尾。"""

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=None):
        async def gen():
            yield LLMStreamChunk.Text("子代理完成")
            yield LLMStreamChunk.Finished("end_turn")
        return gen()


class _ToolThenAnswerProvider:
    """第一轮要一个工具（未知工具也会产生 tool_result），第二轮给答案。"""

    def __init__(self, tool_name: str = "definitely_not_a_tool") -> None:
        self.calls = 0
        self.tool_name = tool_name

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=None):
        async def gen():
            self.calls += 1
            if self.calls == 1:
                yield LLMStreamChunk.ToolCallComplete(
                    "sub-call-1", self.tool_name, {"tool_title": "t"}
                )
                yield LLMStreamChunk.Finished("tool_use")
            else:
                yield LLMStreamChunk.Text("查完了")
                yield LLMStreamChunk.Finished("end_turn")
        return gen()


# ---------------------------------------------------------------------------
# 事件总线：没听众时必须是彻底的空操作
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_emit_without_listener_is_noop():
    assert active() is False
    # 不抛异常、也没有副作用 —— 子代理照旧静默跑（测试/CLI/识图链路都走这条）。
    await emit({"type": "subagentDelta", "text": "x"})
    assert active() is False


@pytest.mark.asyncio
async def test_emitter_receives_events_and_resets():
    got: list[dict] = []

    async def sink(ev):
        got.append(ev)

    token = set_emitter(sink)
    assert active() is True
    await emit({"type": "subagentStart", "id": "r1"})
    reset_emitter(token)
    assert got == [{"type": "subagentStart", "id": "r1"}]
    assert active() is False
    # 收工之后再推不该再进 sink
    await emit({"type": "subagentEnd", "id": "r1"})
    assert len(got) == 1


@pytest.mark.asyncio
async def test_broken_listener_does_not_break_the_subagent():
    async def bad(_ev):
        raise RuntimeError("ws gone")

    token = set_emitter(bad)
    try:
        await emit({"type": "subagentDelta", "text": "x"})  # must not raise
    finally:
        reset_emitter(token)


# ---------------------------------------------------------------------------
# 房间号 = 外层那次 subagent_delegate 调用的 id
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_runtime_exposes_tool_use_to_the_executor():
    """工具执行期间 ``current_tool_use()`` 必须是**这次**调用的 id ——
    子代理靠它把气泡挂到正确的位置。"""
    seen: dict[str, str] = {}

    async def probe(args_json, session_id, **kw):
        seen.update(current_tool_use())
        return ToolExecutionResult("ok", True, tool_title="probe")

    tool = ToolExecutor(
        AgentToolDefinition(
            name="subagent_delegate", description="d",
            parameters={"tool_title": AgentToolParam("string", "t")},
            required=["tool_title"],
        ),
        probe,
    )
    rt = AgentRuntime(tools={"subagent_delegate": tool})
    msgs = [LLMMessage(LLMMessage.Role.USER, "hi")]
    await rt.run(
        _ToolThenAnswerProvider("subagent_delegate"), msgs, "s1",
        AgentRuntimeOptions(max_turns=4),
    )
    assert seen == {"id": "sub-call-1", "name": "subagent_delegate"}
    # 跑完就该还原，别泄漏到下一次调用
    assert current_tool_use() == {}


def test_push_reset_tool_use_roundtrip():
    token = push_tool_use("call-9", "subagent_delegate")
    assert current_tool_use() == {"id": "call-9", "name": "subagent_delegate"}
    reset_tool_use(token)
    assert current_tool_use() == {}


# ---------------------------------------------------------------------------
# 子代理过程 → 事件
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_subagent_streams_group_events(store, monkeypatch):
    _configure(store)
    _add_writer(store)
    monkeypatch.setattr(chat_service, "build_provider",
                        lambda t, c: _TextProvider())

    events: list[dict] = []

    async def sink(ev):
        events.append(ev)

    token = set_emitter(sink)
    tool_use = push_tool_use("call-1", "subagent_delegate")
    try:
        res = await SubagentDelegateTool.execute(
            json.dumps({"tool_title": "让写作助手润色",
                        "subagent": "writer", "task": "写一篇文章"}),
            "s1")
    finally:
        reset_tool_use(tool_use)
        reset_emitter(token)

    assert res.success is True, res.output
    kinds = [e["type"] for e in events]
    assert kinds[0] == "subagentStart"
    assert kinds[-1] == "subagentEnd"
    assert "subagentDelta" in kinds

    start = events[0]
    assert start["id"] == "call-1"                 # 房间号 = 外层工具调用
    assert start["subagentId"] == "writer"
    assert start["name"] == "写作助手"
    assert start["emoji"] == "✍️"
    assert "写一篇文章" in start["task"]
    assert start["project"] == ""                   # 没分配项目

    assert "".join(e["text"] for e in events if e["type"] == "subagentDelta") \
        == "子代理完成"
    end = events[-1]
    assert end["ok"] is True
    assert "子代理完成" in end["text"]
    # 每条事件都带足「谁在说」，前端才能按发言人分气泡
    for ev in events:
        assert ev["id"] == "call-1"
        assert ev["subagentId"] == "writer"


@pytest.mark.asyncio
async def test_run_subagent_events_silent_without_listener(store, monkeypatch):
    """没有听众时子代理必须照旧静默跑通（不能因为没装出口就报错）。"""
    _configure(store)
    _add_writer(store)
    monkeypatch.setattr(chat_service, "build_provider",
                        lambda t, c: _TextProvider())
    res = await SubagentDelegateTool.execute(
        json.dumps({"tool_title": "t", "subagent": "writer", "task": "x"}), "s1")
    assert res.success is True
    assert "子代理完成" in res.output


@pytest.mark.asyncio
async def test_run_subagent_emits_its_own_tool_activity(store, monkeypatch):
    """子代理自己调工具的过程也要可见 —— 否则只有一个最终答复。"""
    _configure(store)
    _add_writer(store)
    monkeypatch.setattr(chat_service, "build_provider",
                        lambda t, c: _ToolThenAnswerProvider())

    events: list[dict] = []

    async def sink(ev):
        events.append(ev)

    token = set_emitter(sink)
    try:
        await SubagentDelegateTool.execute(
            json.dumps({"tool_title": "t", "subagent": "writer", "task": "查"}),
            "s1")
    finally:
        reset_emitter(token)

    starts = [e for e in events if e["type"] == "subagentToolStart"]
    ends = [e for e in events if e["type"] == "subagentToolEnd"]
    assert starts and ends
    assert starts[0]["name"] == "definitely_not_a_tool"
    assert starts[0]["callId"] == "sub-call-1"
    assert ends[0]["ok"] is False          # 未知工具 → 失败，但过程仍然可见


# ---------------------------------------------------------------------------
# 分配项目
# ---------------------------------------------------------------------------
def test_projects_contextvar():
    assert project_for("writer") is None
    set_projects({"writer": "C:/proj/a"})
    assert project_for("writer") == "C:/proj/a"
    assert project_for("other") is None
    set_projects(None)
    assert project_for("writer") is None


@pytest.mark.asyncio
async def test_project_assignment_reaches_the_subagent(store, monkeypatch):
    """成员被分配了项目 → 事件里带上项目目录（前端气泡显示 📁）。"""
    _configure(store)
    _add_writer(store)
    monkeypatch.setattr(chat_service, "build_provider",
                        lambda t, c: _TextProvider())

    events: list[dict] = []

    async def sink(ev):
        events.append(ev)

    token = set_emitter(sink)
    set_projects({"writer": "C:/proj/a"})
    try:
        await SubagentDelegateTool.execute(
            json.dumps({"tool_title": "t", "subagent": "writer", "task": "x"}),
            "s1")
    finally:
        set_projects(None)
        reset_emitter(token)

    assert events[0]["project"] == "C:/proj/a"


# ---------------------------------------------------------------------------
# 群成员提示块
# ---------------------------------------------------------------------------
def test_group_block_lists_real_members_only(store):
    _configure(store)
    _add_writer(store)
    block = group_block(store, ["writer", "ghost-01", ""])
    assert "群聊成员" in block
    assert "`writer`" in block
    assert "写作助手" in block
    assert "ghost-01" not in block          # 编造/陈旧的 id 直接丢掉
    assert "subagent_delegate" in block     # 告诉主代理怎么委派


def test_group_block_empty_when_nobody_is_real(store):
    _configure(store)
    assert group_block(store, ["nope"]) == ""
    assert group_block(store, []) == ""


# ---------------------------------------------------------------------------
# 后端接线：一轮真实对话里，「群成员」进系统提示、「成员项目」进子代理上下文
# ---------------------------------------------------------------------------
@pytest.fixture()
def env(tmp_path, monkeypatch):
    from openminis.core import context

    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(
        context.AppContext(data_dir=tmp_path, cache_dir=tmp_path)
    )
    yield tmp_path
    context._context = None


def _stub_run_chat(monkeypatch, captured: dict):
    """把 _run_chat 的外围依赖全部换掉，只留**接线**这段被测。"""
    from openminis.core import context
    from openminis.server import main as server_main

    class _Provider:
        async def aclose(self):
            return None

    class _Runtime:
        async def run(self, provider, messages, session_id, options=None, **kw):
            captured["system_prompt"] = options.system_prompt or ""
            captured["session_id"] = session_id
            captured["project"] = project_for("writer")
            messages.append(LLMMessage(LLMMessage.Role.ASSISTANT, "ok"))
            return messages, "end_turn"

    class _Bundle:
        prompt = "hi"
        text = "hi"
        image_parts = None

    monkeypatch.setattr(
        server_main, "build_chat_setup",
        lambda s, **kw: (_Provider(), _Runtime(),
                         AgentRuntimeOptions(system_prompt="BASE"), {}, {}),
    )
    monkeypatch.setattr(
        server_main, "build_attachment_context",
        lambda s, t: _async_value(_Bundle()),
    )
    monkeypatch.setattr(server_main, "attribution_snapshot", lambda s, c: {})
    monkeypatch.setattr(server_main.compaction, "maybe_compact",
                        lambda *a, **k: _async_value(None))
    monkeypatch.setattr(server_main, "collect_recent_images",
                        lambda **k: [])
    assert context.app_context() is not None


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_run_chat_injects_group_block_and_member_project(env, store, monkeypatch):
    from openminis.server import chat_store, main as server_main, workspaces

    _configure(store)
    _add_writer(store)

    proj = env / "proj-a"
    proj.mkdir()
    ws = await workspaces.create_workspace("项目A")
    workspaces.set_workspace_path(ws.id, str(proj))

    sent: list[dict] = []

    async def fake_send(_cid: str, payload: dict) -> None:
        sent.append(payload)

    monkeypatch.setattr(server_main, "_safe_send", fake_send)
    captured: dict = {}
    _stub_run_chat(monkeypatch, captured)
    server_main._RUNNING_CHATS.clear()

    await server_main._run_chat("c1", {
        "text": "写一篇文章",
        "session_id": "",
        "participants": ["writer", "ghost"],
        "memberProjects": {"writer": ws.id},
    })

    # 群成员进了系统提示（真实 id 在、编造的丢弃）
    assert "BASE" in captured["system_prompt"]
    assert "群聊成员" in captured["system_prompt"]
    assert "`writer`" in captured["system_prompt"]
    assert "ghost" not in captured["system_prompt"]
    # 成员项目解析成了真实目录
    assert captured["project"] == str(proj)
    # 子代理会话 key 挂在主会话下面
    assert captured["session_id"].startswith("db-")
    assert sent and sent[-1]["type"] == "done"
    # 这一轮结束后分配表归零，不会漏给下一轮
    assert project_for("writer") is None


# ---------------------------------------------------------------------------
# 后端接线：一轮真实对话里，「群成员」进系统提示、「成员项目」进子代理上下文
# ---------------------------------------------------------------------------
@pytest.fixture()
def env(tmp_path, monkeypatch):
    from openminis.core import context

    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(
        context.AppContext(data_dir=tmp_path, cache_dir=tmp_path)
    )
    yield tmp_path
    context._context = None


def _stub_run_chat(monkeypatch, captured: dict):
    """把 _run_chat 的外围依赖全部换掉，只留**接线**这段被测。"""
    from openminis.core import context
    from openminis.server import main as server_main

    class _Provider:
        async def aclose(self):
            return None

    class _Runtime:
        async def run(self, provider, messages, session_id, options=None, **kw):
            captured["system_prompt"] = options.system_prompt or ""
            captured["session_id"] = session_id
            captured["project"] = project_for("writer")
            messages.append(LLMMessage(LLMMessage.Role.ASSISTANT, "ok"))
            return messages, "end_turn"

    class _Bundle:
        prompt = "hi"
        text = "hi"
        image_parts = None

    monkeypatch.setattr(
        server_main, "build_chat_setup",
        lambda s, **kw: (_Provider(), _Runtime(),
                         AgentRuntimeOptions(system_prompt="BASE"), {}, {}),
    )
    monkeypatch.setattr(
        server_main, "build_attachment_context",
        lambda s, t: _async_value(_Bundle()),
    )
    monkeypatch.setattr(server_main, "attribution_snapshot", lambda s, c: {})
    monkeypatch.setattr(server_main.compaction, "maybe_compact",
                        lambda *a, **k: _async_value(None))
    monkeypatch.setattr(server_main, "collect_recent_images",
                        lambda **k: [])
    assert context.app_context() is not None


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_run_chat_injects_group_block_and_member_project(env, store, monkeypatch):
    from openminis.server import chat_store, main as server_main, workspaces

    _configure(store)
    _add_writer(store)

    proj = env / "proj-a"
    proj.mkdir()
    ws = await workspaces.create_workspace("项目A")
    workspaces.set_workspace_path(ws.id, str(proj))

    sent: list[dict] = []

    async def fake_send(_cid: str, payload: dict) -> None:
        sent.append(payload)

    monkeypatch.setattr(server_main, "_safe_send", fake_send)
    captured: dict = {}
    _stub_run_chat(monkeypatch, captured)
    server_main._RUNNING_CHATS.clear()

    await server_main._run_chat("c1", {
        "text": "写一篇文章",
        "session_id": "",
        "participants": ["writer", "ghost"],
        "memberProjects": {"writer": ws.id},
    })

    # 群成员进了系统提示（真实 id 在、编造的丢弃）
    assert "BASE" in captured["system_prompt"]
    assert "群聊成员" in captured["system_prompt"]
    assert "`writer`" in captured["system_prompt"]
    assert "ghost" not in captured["system_prompt"]
    # 成员项目解析成了真实目录
    assert captured["project"] == str(proj)
    # 子代理会话 key 挂在主会话下面
    assert captured["session_id"].startswith("db-")
    assert sent and sent[-1]["type"] == "done"
    # 这一轮结束后分配表归零，不会漏给下一轮
    assert project_for("writer") is None
