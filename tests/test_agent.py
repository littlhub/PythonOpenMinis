"""Agent runtime loop + tool loop detector tests.

The agent runtime tests drive a fake provider so no API key / network is
needed; the detector tests mirror the Kotlin ToolLoopDetector semantics.
"""

import pytest

from openminis.agent.agent_runtime import AgentRuntime, AgentRuntimeOptions, ToolExecutor
from openminis.agent.tool_loop_detector import LoopLevel, ToolLoopConfig, ToolLoopDetector
from openminis.data.model import LLMMessage, LLMStreamChunk, ThinkingLevel
from openminis.data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from openminis.tools.tool_execution_result import ToolExecutionResult


# --------------------------------------------------------------------------
# ToolLoopDetector
# --------------------------------------------------------------------------

def test_detector_initial_check_is_none():
    d = ToolLoopDetector()
    r = d.check("bash", {"command": "ls"})
    assert r.level is LoopLevel.NONE


def test_detector_generic_repeat_warns_and_tool_title_is_ignored():
    d = ToolLoopDetector(ToolLoopConfig(
        warning_threshold=3, critical_threshold=5,
        global_circuit_breaker_threshold=9, unknown_tool_threshold=9,
    ))
    for _ in range(3):
        d.record("bash", {"tool_title": "x", "command": "ls"}, "f1\nf2")
    # Same command, different tool_title -> same args hash -> WARNING.
    r = d.check("bash", {"tool_title": "renamed #2", "command": "ls"})
    assert r.level is LoopLevel.WARNING


def test_detector_unknown_tool_escalates_to_critical():
    d = ToolLoopDetector(ToolLoopConfig(
        warning_threshold=9, critical_threshold=9,
        global_circuit_breaker_threshold=9, unknown_tool_threshold=2,
    ))
    for _ in range(2):
        d.record("bash", {"command": "ls"}, None, "Error: unknown tool: nope")
    r = d.check("nope", {})
    assert r.level is LoopLevel.CRITICAL
    assert r.is_blocking


def test_detector_no_progress_global_circuit_breaker():
    d = ToolLoopDetector(ToolLoopConfig(
        warning_threshold=1, critical_threshold=2,
        global_circuit_breaker_threshold=3, unknown_tool_threshold=9,
    ))
    for _ in range(3):
        d.record("bash", {"command": "ls /empty"}, "")
    r = d.check("bash", {"command": "ls /empty"})
    assert r.level is LoopLevel.CRITICAL


def test_detector_progress_resets_streak():
    d = ToolLoopDetector(ToolLoopConfig(
        warning_threshold=2, critical_threshold=3,
        global_circuit_breaker_threshold=3, unknown_tool_threshold=9,
    ))
    for _ in range(2):
        d.record("bash", {"command": "ls"}, "same")
    d.record("bash", {"command": "ls"}, "DIFFERENT")
    r = d.check("bash", {"command": "ls"})
    assert r.level is not LoopLevel.CRITICAL


def test_detector_warning_throttling():
    d = ToolLoopDetector(ToolLoopConfig(
        warning_threshold=2, critical_threshold=99,
        global_circuit_breaker_threshold=99, unknown_tool_threshold=99,
    ))
    emitted = []
    for i in range(1, 7):
        r = d.record("bash", {"command": "ls"}, "same")
        if r.level is LoopLevel.WARNING:
            emitted.append(i)
    assert emitted == [2, 4, 6]


# --------------------------------------------------------------------------
# AgentRuntime (fake provider, no network)
# --------------------------------------------------------------------------

class _ToolForeverProvider:
    """Always calls the same tool; used to exercise max_turns."""

    def __init__(self) -> None:
        self.n = 0

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=ThinkingLevel.OFF):
        async def gen():
            self.n += 1
            yield LLMStreamChunk.ToolCallComplete(
                f"id{self.n}", "real_tool", {"tool_title": "t"}
            )
            yield LLMStreamChunk.Finished("tool_use")
        return gen()


class _AnswerAfterToolProvider:
    """Turn 1 requests a tool; turn 2 (after the result) gives the answer."""

    def __init__(self) -> None:
        self.calls = 0

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=ThinkingLevel.OFF):
        async def gen():
            self.calls += 1
            if self.calls == 1:
                yield LLMStreamChunk.Text("Let me check.")
                yield LLMStreamChunk.ToolCallComplete(
                    "call_1", "shell_execute", {"tool_title": "list", "command": "ls"}
                )
                yield LLMStreamChunk.Finished("tool_use")
            else:
                yield LLMStreamChunk.Text("Done! The listing is above.")
                yield LLMStreamChunk.Finished("end_turn")
        return gen()


def _shell_tool_def() -> AgentToolDefinition:
    return AgentToolDefinition(
        name="shell_execute", description="run cmd",
        parameters={"command": AgentToolParam("string", "cmd")},
        required=["command"],
    )


async def _fake_shell(args_json: str, session_id: str, **kw) -> ToolExecutionResult:
    return ToolExecutionResult("file1.py\nfile2.py", True)


@pytest.mark.asyncio
async def test_runtime_two_turn_tool_roundtrip():
    rt = AgentRuntime()
    rt.register(ToolExecutor(_shell_tool_def(), _fake_shell))
    seen: list[str] = []

    async def sink(chunk) -> None:
        seen.append(type(chunk).__name__)

    rt.chunk_sink = sink
    msgs = [LLMMessage(LLMMessage.Role.USER, "list files")]
    out, stop = await rt.run(_AnswerAfterToolProvider(), msgs, "s1",
                             AgentRuntimeOptions(thinking_level=ThinkingLevel.OFF))
    assert stop == "end_turn"
    assert len(out) == 4  # user, assistant(tooluse), user(toolresult), assistant
    assert out[1].content_parts[0].text == "Let me check."
    assert isinstance(out[1].content_parts[1].__class__.__name__, str)
    assert out[2].content_parts[0].content == "file1.py\nfile2.py"
    assert not out[2].content_parts[0].is_error
    assert out[3].content_parts[0].text == "Done! The listing is above."
    assert "_ToolCallComplete" in seen


@pytest.mark.asyncio
async def test_runtime_unknown_tool_fed_back():
    rt = AgentRuntime()  # no tools registered

    class Hallucinator:
        def stream_message(self, messages, *a, **kw):
            async def gen():
                last = messages[-1]
                if (last.role is LLMMessage.Role.USER and last.content_parts
                        and getattr(last.content_parts[0], "content", "").startswith("Unknown tool")):
                    yield LLMStreamChunk.Text("I can't use that tool, sorry.")
                    yield LLMStreamChunk.Finished("end_turn")
                else:
                    yield LLMStreamChunk.ToolCallComplete("c1", "nonexistent_tool", {"x": 1})
                    yield LLMStreamChunk.Finished("tool_use")
            return gen()

    msgs = [LLMMessage(LLMMessage.Role.USER, "do it")]
    out, stop = await rt.run(Hallucinator(), msgs, "s", AgentRuntimeOptions())
    assert stop == "end_turn"
    assert any(m.role is LLMMessage.Role.USER and m.content_parts
               and "Unknown tool: nonexistent_tool" in m.content_parts[0].content
               for m in out)


@pytest.mark.asyncio
async def test_runtime_max_turns_guard():
    rt = AgentRuntime()
    rt.register(ToolExecutor(_shell_tool_def(), _fake_shell))
    msgs = [LLMMessage(LLMMessage.Role.USER, "go")]
    out, stop = await rt.run(_ToolForeverProvider(), msgs, "s",
                             AgentRuntimeOptions(max_turns=3))
    assert stop == "max_turns"
    last_text = next(
        (p.text for m in out if m.role is LLMMessage.Role.ASSISTANT
         for p in m.content_parts
         if type(p).__name__ == "Text" and "stopped" in p.text),
        None,
    )
    assert last_text
