"""Session-scoped loop guards + the runtime wiring for ``RepeatGuard``.

KT keeps ONE ``ToolLoopDetector`` per chat session (a ChatViewModel field), so
the sliding window spans the whole conversation. The Python port has to do the
same — a detector recreated on every ``run()`` forgets everything the moment
the user sends the next message, and cross-message repeats ("同一张图/同一条
命令又来了") become invisible.
"""

from __future__ import annotations

import pytest

from openminis.agent.agent_runtime import AgentRuntime, AgentRuntimeOptions, ToolExecutor
from openminis.agent.tool_loop_detector import LoopLevel
from openminis.data.model import LLMMessage, LLMStreamChunk, ThinkingLevel
from openminis.data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from openminis.settings.chat_service import reset_session_guards, session_guards
from openminis.tools.tool_execution_result import ToolExecutionResult

GEN_ARGS = {"command": 'python scripts/image_generation.py "queen"'}


# --------------------------------------------------------------------------
# registry: one (detector, guard) pair per session, window survives runs
# --------------------------------------------------------------------------

def test_session_guards_are_stable_per_session():
    d1, g1 = session_guards("sess-stable")
    d2, g2 = session_guards("sess-stable")
    assert d1 is d2
    assert g1 is g2
    assert session_guards("sess-other")[0] is not d1


def test_sliding_window_survives_across_runs():
    """A repeat issued in a LATER message must still be caught."""
    _d, guard = session_guards("sess-window")
    guard.record("shell_execute", GEN_ARGS, result="saved 20260913_a.png")
    # next user message → same session → same guard → identical command blocked
    _d2, guard2 = session_guards("sess-window")
    assert guard2.check("shell_execute", GEN_ARGS).level is LoopLevel.CRITICAL


def test_reset_drops_the_window():
    _d, guard = session_guards("sess-reset")
    guard.record("shell_execute", GEN_ARGS, result="saved.png")
    reset_session_guards("sess-reset")
    _d2, guard2 = session_guards("sess-reset")
    assert guard2.check("shell_execute", GEN_ARGS).level is LoopLevel.NONE


# --------------------------------------------------------------------------
# runtime wiring: the companion guard can veto a tool call
# --------------------------------------------------------------------------

class _RepeatSameCommandProvider:
    """Asks for the SAME shell command every turn; answers once it is blocked."""

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=ThinkingLevel.OFF):
        async def gen():
            last = messages[-1]
            parts = getattr(last, "content_parts", None) or []
            blocked = any("LOOP BLOCKED" in (getattr(p, "content", "") or "")
                          for p in parts)
            if blocked:
                yield LLMStreamChunk.Text("图已生成，直接收尾。")
                yield LLMStreamChunk.Finished("end_turn")
            else:
                yield LLMStreamChunk.ToolCallComplete(
                    "c1", "shell_execute", {"command": "gen-image"}
                )
                yield LLMStreamChunk.Finished("tool_use")
        return gen()


def _shell_tool_def() -> AgentToolDefinition:
    return AgentToolDefinition(
        name="shell_execute", description="run cmd",
        parameters={"command": AgentToolParam("string", "cmd")},
        required=["command"],
    )


@pytest.mark.asyncio
async def test_runtime_blocks_repeated_effect_command():
    """「一张就停」在 runtime 里生效：第二次同参命令被拦下并回灌给模型。"""
    rt = AgentRuntime()
    runs: list[str] = []

    async def _shell(args_json: str, session_id: str, **kw) -> ToolExecutionResult:
        runs.append(args_json)
        return ToolExecutionResult("saved 20260913.png", True)

    rt.register(ToolExecutor(_shell_tool_def(), _shell))
    msgs = [LLMMessage(LLMMessage.Role.USER, "生成一张图")]
    out, stop = await rt.run(_RepeatSameCommandProvider(), msgs, "sess-runtime",
                             AgentRuntimeOptions())
    assert stop == "end_turn"
    assert len(runs) == 1  # executed exactly once — the repeat was blocked
    blocked = [p for m in out for p in (getattr(m, "content_parts", None) or [])
               if "LOOP BLOCKED" in (getattr(p, "content", "") or "")]
    assert blocked


class _ToolTwiceThenAnswerProvider:
    """Two tool rounds (identical command) then a text answer."""

    def __init__(self) -> None:
        self.calls = 0

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=ThinkingLevel.OFF):
        async def gen():
            self.calls += 1
            if self.calls <= 2:
                yield LLMStreamChunk.ToolCallComplete(
                    f"c{self.calls}", "shell_execute", {"command": "gen-image"}
                )
                yield LLMStreamChunk.Finished("tool_use")
            else:
                yield LLMStreamChunk.Text("完成。")
                yield LLMStreamChunk.Finished("end_turn")
        return gen()


@pytest.mark.asyncio
async def test_kt_mode_keeps_kt_detector_only():
    """原版（kt）：只跑 KT 四策略，同参重复不被拦（阈值 10）。"""
    rt = AgentRuntime()
    runs: list[str] = []

    async def _shell(args_json: str, session_id: str, **kw) -> ToolExecutionResult:
        runs.append(args_json)
        return ToolExecutionResult("saved.png", True)

    rt.register(ToolExecutor(_shell_tool_def(), _shell))
    msgs = [LLMMessage(LLMMessage.Role.USER, "生成一张图")]
    _out, stop = await rt.run(_ToolTwiceThenAnswerProvider(), msgs, "sess-kt",
                              AgentRuntimeOptions(loop_mode="kt"))
    assert stop == "end_turn"
    assert len(runs) == 2  # 原版不拦同参重复


@pytest.mark.asyncio
async def test_react_mode_stops_on_first_repeat():
    """ReAct 增强版：第一次重复就停（同一条命令只执行一次）。"""
    rt = AgentRuntime()
    runs: list[str] = []

    async def _shell(args_json: str, session_id: str, **kw) -> ToolExecutionResult:
        runs.append(args_json)
        return ToolExecutionResult("saved.png", True)

    rt.register(ToolExecutor(_shell_tool_def(), _shell))
    msgs = [LLMMessage(LLMMessage.Role.USER, "生成一张图")]
    _out, stop = await rt.run(_ToolTwiceThenAnswerProvider(), msgs, "sess-react",
                              AgentRuntimeOptions(loop_mode="react"))
    assert stop == "end_turn"
    assert len(runs) == 1


# --------------------------------------------------------------------------
# settings wiring
# --------------------------------------------------------------------------

def test_loop_mode_defaults_to_react(tmp_path, monkeypatch):
    from openminis.settings.store import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    assert store.agent_config()["loopMode"] == "react"
    store.apply_full({"agent": {"loopMode": "kt"}})
    assert store.agent_config()["loopMode"] == "kt"


def test_loop_mode_rejects_unknown_value(tmp_path):
    import pytest as _pytest

    from openminis.settings.store import SettingsError, SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    with _pytest.raises(SettingsError):
        store.apply_full({"agent": {"loopMode": "nope"}})
