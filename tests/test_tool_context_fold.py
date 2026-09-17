"""[T-tool-cards-persist-and-fold] 工具输出进上下文的收敛。

用户要求：工具调用在会话里折叠展示、可展开细看，但**不完全进上下文**。
这一组测试钉住三件事：

1. 只折「更早的」输出，最近 N 条保持完整；
2. 折叠/截断**只改正文，不改结构**（``tool_use`` 少了配对的 ``tool_result``
   会被 provider 判为非法请求）；
3. 幂等 —— 每轮出站都会调用一次，第二次必须什么都不做。
"""

from __future__ import annotations

import pytest

from openminis.agent.agent_runtime import AgentRuntime, AgentRuntimeOptions, ToolExecutor
from openminis.agent.tool_context import FOLD_PREFIX, TRUNC_PREFIX, fold_tool_outputs
from openminis.data.model import LLMMessage, LLMStreamChunk, ThinkingLevel
from openminis.data.model.agent_content_part import Text, ToolResult, ToolUse
from openminis.data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from openminis.tools.tool_execution_result import ToolExecutionResult


def _history(count: int, body: str = "x" * 500) -> list[LLMMessage]:
    """count 组「助手 tool_use + 用户 tool_result」，结构齐全。"""
    msgs: list[LLMMessage] = [LLMMessage(LLMMessage.Role.USER, "干活")]
    for i in range(count):
        msgs.append(LLMMessage(
            LLMMessage.Role.ASSISTANT, "",
            content_parts=[ToolUse(id=f"t{i}", name="shell_execute", input={"i": i})],
        ))
        msgs.append(LLMMessage(
            LLMMessage.Role.USER, "",
            content_parts=[ToolResult(id=f"t{i}", name="shell_execute", content=body)],
        ))
    return msgs


def _results(msgs: list[LLMMessage]) -> list[ToolResult]:
    return [
        p for m in msgs for p in (m.content_parts or []) if isinstance(p, ToolResult)
    ]


# ---------------------------------------------------------------------------
# fold_tool_outputs
# ---------------------------------------------------------------------------
def test_folds_only_the_older_outputs():
    msgs = _history(5)
    stats = fold_tool_outputs(msgs, keep_recent=2, max_chars=0)

    assert stats.folded == 3
    bodies = [r.content for r in _results(msgs)]
    assert all(b.startswith(FOLD_PREFIX) for b in bodies[:3])
    # 最近两条一字未动
    assert bodies[3:] == ["x" * 500, "x" * 500]
    assert stats.saved_chars > 0


def test_folding_keeps_tool_use_and_result_paired():
    """只改正文，不删块 —— 否则 provider 直接 400。"""
    msgs = _history(4)
    before_uses = [p for m in msgs for p in (m.content_parts or [])
                   if isinstance(p, ToolUse)]
    before = [(m.role, len(m.content_parts or [])) for m in msgs]

    fold_tool_outputs(msgs, keep_recent=1, max_chars=0)

    assert [(m.role, len(m.content_parts or [])) for m in msgs] == before
    after_uses = [p for m in msgs for p in (m.content_parts or [])
                  if isinstance(p, ToolUse)]
    assert [u.id for u in after_uses] == [u.id for u in before_uses]
    for u in before_uses:
        assert any(r.id == u.id for r in _results(msgs)), u.id


def test_folding_is_idempotent():
    msgs = _history(4)
    fold_tool_outputs(msgs, keep_recent=1, max_chars=0)
    first = [r.content for r in _results(msgs)]

    stats = fold_tool_outputs(msgs, keep_recent=1, max_chars=0)

    assert stats.folded == 0 and stats.truncated == 0
    assert [r.content for r in _results(msgs)] == first
    # 不能套娃：正文里只应出现一次折叠标记
    assert first[0].count(FOLD_PREFIX) == 1


def test_folded_text_says_how_to_get_the_full_output():
    msgs = _history(3)
    fold_tool_outputs(msgs, keep_recent=1, max_chars=0)
    folded = _results(msgs)[0].content
    assert FOLD_PREFIX in folded
    assert "shell_execute" in folded
    assert "500" in folded          # 原文长度
    assert "重新调用" in folded      # 给模型一条出路


def test_single_output_cap_truncates_but_keeps_the_head():
    msgs = _history(1, body="HEADER\n" + "y" * 30_000)
    stats = fold_tool_outputs(msgs, keep_recent=6, max_chars=1000)

    assert stats.truncated == 1 and stats.folded == 0
    body = _results(msgs)[0].content
    assert body.startswith(TRUNC_PREFIX)
    assert "HEADER" in body[:400]
    assert len(body) < 2000          # 收敛住了


def test_zero_disables_both_knobs():
    msgs = _history(4, body="z" * 50_000)
    stats = fold_tool_outputs(msgs, keep_recent=0, max_chars=0)
    assert not stats.touched
    assert all(r.content == "z" * 50_000 for r in _results(msgs))


def test_no_tool_results_is_a_noop():
    msgs = [LLMMessage(LLMMessage.Role.USER, "只有文字")]
    assert fold_tool_outputs(msgs).folded == 0


# ---------------------------------------------------------------------------
# 与 agent 循环的接线
# ---------------------------------------------------------------------------
def _echo_tool_def() -> AgentToolDefinition:
    return AgentToolDefinition(
        name="shell_execute", description="run cmd",
        parameters={"command": AgentToolParam("string", "cmd")},
        required=["command"],
    )


#: 一份「像真的」的大输出（约 4 万字符）。刻意不用 "A"*N —— 出站守卫会把
#: 一长串重复字符当成疑似密钥拦下来替换掉，那样测的就不是折叠了。
_BIG = "".join(f"[{i:05d}] building module_{i}.o  ok\n" for i in range(1200))


class _LongOutputThenAnswer:
    """连着调 3 轮工具（每轮返回一大坨），第 4 轮给总结。"""

    def __init__(self, rounds: int = 3) -> None:
        self.rounds = rounds
        self.calls = 0
        #: 每次请求发出时，历史里工具输出的总字符数（用来证明真的收敛了）。
        self.seen_chars: list[int] = []

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=ThinkingLevel.OFF):
        async def gen():
            self.calls += 1
            self.seen_chars.append(sum(
                len(p.content or "")
                for m in messages for p in (m.content_parts or [])
                if isinstance(p, ToolResult)
            ))
            if self.calls <= self.rounds:
                yield LLMStreamChunk.ToolCallComplete(
                    f"c{self.calls}", "shell_execute", {"command": f"step{self.calls}"})
                yield LLMStreamChunk.Finished("tool_use")
            else:
                yield LLMStreamChunk.Text("都跑完了。")
                yield LLMStreamChunk.Finished("end_turn")
        return gen()


async def _big_output(args_json: str, session_id: str, **kw) -> ToolExecutionResult:
    return ToolExecutionResult(_BIG, True)


@pytest.mark.asyncio
async def test_runtime_folds_old_outputs_before_each_request():
    rt = AgentRuntime()
    rt.register(ToolExecutor(_echo_tool_def(), _big_output))
    provider = _LongOutputThenAnswer()
    msgs = [LLMMessage(LLMMessage.Role.USER, "开始")]

    out, stop = await rt.run(
        provider, msgs, "s-fold",
        AgentRuntimeOptions(tool_keep_recent=1, tool_output_max_chars=0),
    )

    assert stop == "end_turn"
    results = _results(out)
    assert len(results) == 3
    # 前两轮的输出被折成一行，最后一轮（最近一条）完整保留。
    assert results[0].content.startswith(FOLD_PREFIX)
    assert results[1].content.startswith(FOLD_PREFIX)
    assert results[2].content == _BIG
    # 第 3 次请求时，历史里的工具输出已经远小于 3×20000（说明折叠真的发生在
    # 发请求之前，而不是收工后补的）。
    assert provider.seen_chars[2] < len(_BIG) * 2


@pytest.mark.asyncio
async def test_runtime_keeps_everything_when_disabled():
    rt = AgentRuntime()
    rt.register(ToolExecutor(_echo_tool_def(), _big_output))
    provider = _LongOutputThenAnswer()
    msgs = [LLMMessage(LLMMessage.Role.USER, "开始")]

    out, _ = await rt.run(
        provider, msgs, "s-nofold",
        AgentRuntimeOptions(tool_keep_recent=0, tool_output_max_chars=0),
    )

    assert all(r.content == _BIG for r in _results(out))


@pytest.mark.asyncio
async def test_runtime_truncates_oversized_single_output():
    rt = AgentRuntime()
    rt.register(ToolExecutor(_echo_tool_def(), _big_output))
    provider = _LongOutputThenAnswer(rounds=1)
    msgs = [LLMMessage(LLMMessage.Role.USER, "开始")]

    out, stop = await rt.run(
        provider, msgs, "s-cap",
        AgentRuntimeOptions(tool_keep_recent=6, tool_output_max_chars=1500),
    )

    assert stop == "end_turn"
    body = _results(out)[0].content
    assert body.startswith(TRUNC_PREFIX)
    assert len(body) < 2500


@pytest.mark.asyncio
async def test_text_parts_are_never_touched():
    """工具输出收敛只管工具输出 —— 别把用户/助手的话也截了。"""
    rt = AgentRuntime()
    rt.register(ToolExecutor(_echo_tool_def(), _big_output))
    provider = _LongOutputThenAnswer(rounds=2)
    msgs = [LLMMessage(LLMMessage.Role.USER, "U" * 5000)]

    out, _ = await rt.run(
        provider, msgs, "s-text",
        AgentRuntimeOptions(tool_keep_recent=1, tool_output_max_chars=100),
    )

    assert out[0].content == "U" * 5000
    assert isinstance(out[-1].content_parts[0], Text)


# ---------------------------------------------------------------------------
# 端到端：真走一遍 WS 对话链路
# ---------------------------------------------------------------------------
_TOOL_OUTPUT = "11 passed in 0.78s"


async def _shell_ok(args_json: str, session_id: str, **kw) -> ToolExecutionResult:
    return ToolExecutionResult(_TOOL_OUTPUT, True)


class _OneToolProvider:
    """第一轮调一次工具，第二轮给总结。"""

    def __init__(self) -> None:
        self.calls = 0

    def stream_message(self, messages, *a, **kw):
        async def gen():
            self.calls += 1
            if self.calls == 1:
                yield LLMStreamChunk.ToolCallComplete(
                    "call_42", "shell_execute", {"command": "pytest -q"})
                yield LLMStreamChunk.Finished("tool_use")
            else:
                yield LLMStreamChunk.Text("跑完了，都过。")
                yield LLMStreamChunk.Finished("end_turn")
        return gen()


@pytest.mark.asyncio
async def test_ws_turn_persists_tool_cards_but_not_into_context(tmp_path, monkeypatch):
    """一轮真对话跑完：工具卡落库（刷新还在），正文里不掺工具原文。"""
    from openminis.core import context
    from openminis.server import chat_store
    from openminis.server import main as server_main

    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    chat_store.set_database_path(tmp_path / "ws_tools.db")
    try:
        runtime = AgentRuntime()
        runtime.register(ToolExecutor(_echo_tool_def(), _shell_ok))

        def fake_setup(store, session_id=None, identity_id=None, on_fallback=None, **_kw):
            return (
                _OneToolProvider(), runtime, AgentRuntimeOptions(),
                "你是助手", {"id": "gw", "model": "m1"},
            )

        monkeypatch.setattr(server_main, "build_chat_setup", fake_setup)
        sent: list[dict] = []

        async def fake_send(_cid: str, payload: dict) -> None:
            sent.append(payload)

        monkeypatch.setattr(server_main, "_safe_send", fake_send)

        await server_main._run_chat("c1", {"text": "跑一下测试"})

        kinds = [f["type"] for f in sent]
        assert "toolStart" in kinds and "toolEnd" in kinds and "done" in kinds
        start = next(f for f in sent if f["type"] == "toolStart")
        assert start["name"] == "shell_execute" and start["sessionId"]
        end = next(f for f in sent if f["type"] == "toolEnd")
        assert end["ok"] is True and end["output"] == _TOOL_OUTPUT

        sessions = await chat_store.list_sessions()
        rows = await chat_store.load_messages(sessions[0].id)
        assistant = [r for r in rows if r.role == "assistant"][-1]
        assert assistant.text == "跑完了，都过。"
        assert assistant.runs and assistant.runs[0]["name"] == "shell_execute"
        assert assistant.runs[0]["input"] == {"command": "pytest -q"}
        assert assistant.runs[0]["output"] == _TOOL_OUTPUT
        assert assistant.runs[0]["ok"] is True

        # 上下文重建：工具输出一个字都不进去
        hist = await chat_store.load_runtime_history(sessions[0].id)
        assert all(_TOOL_OUTPUT not in m.content for m in hist)
    finally:
        context._context = None
        chat_store.set_database_path(None)
