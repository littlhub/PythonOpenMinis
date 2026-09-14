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


def test_detector_config_invariants_are_enforced():
    """Kotlin's ``init{}`` require()s — the port must reject the same shapes."""
    with pytest.raises(ValueError):
        ToolLoopConfig(warning_threshold=0)
    with pytest.raises(ValueError):
        ToolLoopConfig(warning_threshold=9, critical_threshold=9)
    with pytest.raises(ValueError):
        ToolLoopConfig(critical_threshold=30, global_circuit_breaker_threshold=30)
    with pytest.raises(ValueError):
        # historySize must cover the global circuit breaker window
        ToolLoopConfig(history_size=10, global_circuit_breaker_threshold=30)


def test_detector_unknown_tool_escalates_to_critical():
    d = ToolLoopDetector(ToolLoopConfig(
        warning_threshold=8, critical_threshold=9,
        global_circuit_breaker_threshold=10, unknown_tool_threshold=2,
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
        global_circuit_breaker_threshold=4, unknown_tool_threshold=9,
    ))
    for _ in range(2):
        d.record("bash", {"command": "ls"}, "same")
    d.record("bash", {"command": "ls"}, "DIFFERENT")
    r = d.check("bash", {"command": "ls"})
    assert r.level is not LoopLevel.CRITICAL


def test_detector_warning_throttling():
    d = ToolLoopDetector(ToolLoopConfig(
        warning_threshold=2, critical_threshold=20,
        global_circuit_breaker_threshold=30, unknown_tool_threshold=30,
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


@pytest.mark.asyncio
async def test_runtime_hard_stops_after_repeated_blocks():
    """Model keeps calling a query tool with fresh args forever: after the
    gate starts blocking (query_tool_runaway), 2 consecutive fully-blocked
    rounds must hard-stop the loop with a wrap-up instead of spinning until
    MAX_AGENT_TURNS."""

    class SearchForever:
        def __init__(self) -> None:
            self.n = 0

        def stream_message(self, messages, system_prompt=None, max_tokens=0,
                           temperature=None, image_parts=None, tools=None,
                           thinking_level=ThinkingLevel.OFF):
            async def gen():
                self.n += 1
                last = messages[-1]
                text = (last.content_parts[0].content
                        if last.content_parts and hasattr(last.content_parts[0], "content")
                        else (last.content or ""))
                if "[loop protection]" in text:
                    yield LLMStreamChunk.Text("根据已收集的信息总结如下。")
                    yield LLMStreamChunk.Finished("end_turn")
                else:
                    yield LLMStreamChunk.ToolCallComplete(
                        f"c{self.n}", "web_fetch",
                        {"url": f"https://example.com/page{self.n}"},
                    )
                    yield LLMStreamChunk.Finished("tool_use")
            return gen()

    def _fetch_def() -> AgentToolDefinition:
        return AgentToolDefinition(
            name="web_fetch", description="fetch url",
            parameters={"url": AgentToolParam("string", "url")},
            required=["url"],
        )

    async def _fake_fetch(args_json: str, session_id: str, **kw) -> ToolExecutionResult:
        return ToolExecutionResult("nothing useful here", True)

    rt = AgentRuntime()
    rt.register(ToolExecutor(_fetch_def(), _fake_fetch))
    msgs = [LLMMessage(LLMMessage.Role.USER, "keep searching")]
    out, stop = await rt.run(SearchForever(), msgs, "s", AgentRuntimeOptions())
    assert stop == "tool_loop_blocked"
    # the wrap-up request and the model's summary are both in history
    assert any(m.role is LLMMessage.Role.USER
               and "[loop protection]" in (m.content or "") for m in out)
    assert any(m.role is LLMMessage.Role.ASSISTANT
               and "总结" in (m.content or "") for m in out)


@pytest.mark.asyncio
async def test_runtime_auto_wrap_up_when_never_answering():
    """A ReAct loop that keeps acting but never writes to the user must not
    burn the whole tool budget: the runtime forces a text-only wrap-up so the
    turn closes with an answer (stop == 'auto_wrap_up') instead of dying on
    the terse max_turns marker."""

    class ActForeverNoText:
        def __init__(self) -> None:
            self.n = 0

        def stream_message(self, messages, system_prompt=None, max_tokens=0,
                           temperature=None, image_parts=None, tools=None,
                           thinking_level=ThinkingLevel.OFF):
            async def gen():
                self.n += 1
                last = messages[-1]
                txt = (last.content_parts[0].content
                       if last.content_parts and hasattr(last.content_parts[0], "content")
                       else (last.content or ""))
                if "[auto wrap-up]" in txt:
                    yield LLMStreamChunk.Text("自动总结：新闻要点一二三。")
                    yield LLMStreamChunk.Finished("end_turn")
                else:
                    # Distinct args each round -> no identical-repeat blocker,
                    # so ONLY the wrap-up guard can stop this loop.
                    yield LLMStreamChunk.ToolCallComplete(
                        f"c{self.n}", "shell_execute",
                        {"command": f"step-{self.n}"},
                    )
                    yield LLMStreamChunk.Finished("tool_use")
            return gen()

    rt = AgentRuntime()
    rt.register(ToolExecutor(_shell_tool_def(), _fake_shell))
    msgs = [LLMMessage(LLMMessage.Role.USER, "长任务")]
    out, stop = await rt.run(
        ActForeverNoText(), msgs, "s",
        AgentRuntimeOptions(max_turns=30),  # wrap_up_after == 12
    )
    assert stop == "auto_wrap_up"
    assert any(m.role is LLMMessage.Role.USER
               and "[auto wrap-up]" in (m.content or "") for m in out)
    assert any(m.role is LLMMessage.Role.ASSISTANT
               and "自动总结" in (m.content or "") for m in out)
    # It must close early, not at the hard budget.
    assert len(out) < 30


# --------------------------------------------------------------------------
# kt 模式下也要保留的**产物级规则**（生图一张就停 / 空转收尾）
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kt_mode_still_wraps_up_when_never_answering():
    """kt 模式只是不做启发式拦截，空转收尾是产品行为，必须保留。

    用户反馈：开着 kt 跑了十几轮还是没有总结。
    """

    class ActForeverNoText:
        def __init__(self) -> None:
            self.n = 0

        def stream_message(self, messages, system_prompt=None, max_tokens=0,
                           temperature=None, image_parts=None, tools=None,
                           thinking_level=ThinkingLevel.OFF):
            async def gen():
                self.n += 1
                last = messages[-1]
                txt = (last.content_parts[0].content
                       if last.content_parts and hasattr(last.content_parts[0], "content")
                       else (last.content or ""))
                if "[auto wrap-up]" in txt:
                    yield LLMStreamChunk.Text("自动总结：已完成。")
                    yield LLMStreamChunk.Finished("end_turn")
                else:
                    yield LLMStreamChunk.ToolCallComplete(
                        f"c{self.n}", "shell_execute", {"command": f"step-{self.n}"}
                    )
                    yield LLMStreamChunk.Finished("tool_use")
            return gen()

    rt = AgentRuntime()
    rt.register(ToolExecutor(_shell_tool_def(), _fake_shell))
    out, stop = await rt.run(
        ActForeverNoText(),
        [LLMMessage(LLMMessage.Role.USER, "长任务")],
        "s",
        AgentRuntimeOptions(max_turns=30, loop_mode="kt"),
    )
    assert stop == "auto_wrap_up"
    assert any(m.role is LLMMessage.Role.ASSISTANT and "自动总结" in (m.content or "")
               for m in out)


@pytest.mark.asyncio
async def test_kt_mode_stops_after_one_image():
    """kt 模式下生图「一张就停」照样生效。

    用户实测：开着 kt 连出 12 张图（每张 prompt 都不同，KT 的同参+同结果
    策略永远看不见）。这是产物规则，不该被循环模式关掉。
    """
    ran: list[str] = []

    async def fake_image_shell(args_json: str, session_id: str, **kw):
        ran.append(args_json)
        return ToolExecutionResult("saved /tmp/out.png", True)

    class GenImagesForever:
        def __init__(self) -> None:
            self.n = 0

        def stream_message(self, messages, system_prompt=None, max_tokens=0,
                           temperature=None, image_parts=None, tools=None,
                           thinking_level=ThinkingLevel.OFF):
            async def gen():
                self.n += 1
                yield LLMStreamChunk.ToolCallComplete(
                    f"c{self.n}",
                    "shell_execute",
                    {"command": (
                        "python C:/skills/agnes-image/scripts/image_generation.py "
                        f'"prompt {self.n}"'
                    )},
                )
                yield LLMStreamChunk.Finished("tool_use")
            return gen()

    rt = AgentRuntime()
    rt.register(ToolExecutor(_shell_tool_def(), fake_image_shell))
    out, stop = await rt.run(
        GenImagesForever(),
        [LLMMessage(LLMMessage.Role.USER, "生成一张图")],
        "s",
        AgentRuntimeOptions(max_turns=12, loop_mode="kt"),
    )
    # 只真的跑了一次生图脚本
    assert len(ran) == 1, ran
    assert stop in {"tool_loop_blocked", "auto_wrap_up"}


@pytest.mark.asyncio
async def test_image_budget_block_stops_even_with_other_tools():
    """生图预算用完后，**即使同一轮还有别的工具成功**，也必须停下来收尾。

    用户实测：shell_execute(生图) 被拦 + skill_use 成功的交替轮次里，
    原来的「整轮全被拦」判据永远为假 → blocked_rounds 每轮归零 →
    「预算用完还在跑」，一路转到 max_turns 才甩出一句停止标记。
    """
    gen_ran: list[str] = []

    async def fake_shell(args_json: str, session_id: str, **kw):
        if "image_generation" in args_json:
            gen_ran.append(args_json)
            return ToolExecutionResult("saved /tmp/a.png", True)
        return ToolExecutionResult("side effect ok", True)

    class ImagePlusSide:
        def __init__(self) -> None:
            self.n = 0

        def stream_message(self, messages, system_prompt=None, max_tokens=0,
                           temperature=None, image_parts=None, tools=None,
                           thinking_level=ThinkingLevel.OFF):
            async def gen():
                self.n += 1
                if tools is None:                     # 收尾轮：给一句总结
                    yield LLMStreamChunk.Text("总结：已生成 1 张图。")
                    yield LLMStreamChunk.Finished("end_turn")
                    return
                yield LLMStreamChunk.ToolCallComplete(
                    f"g{self.n}", "shell_execute",
                    {"command": ("python C:/skills/agnes-image/scripts/"
                                 f'image_generation.py "p{self.n}"')},
                )
                yield LLMStreamChunk.ToolCallComplete(
                    f"s{self.n}", "shell_execute",
                    {"command": f"echo side-{self.n}"},
                )
                yield LLMStreamChunk.Finished("tool_use")
            return gen()

    rt = AgentRuntime()
    rt.register(ToolExecutor(_shell_tool_def(), fake_shell))
    out, stop = await rt.run(
        ImagePlusSide(),
        [LLMMessage(LLMMessage.Role.USER, "生成一张图")],
        "s",
        AgentRuntimeOptions(max_turns=12, loop_mode="kt"),
    )
    assert len(gen_ran) == 1, gen_ran          # 只真的生成了一次
    assert stop == "tool_loop_blocked"         # 而不是一路跑到 max_turns
    assert any("总结" in (m.content or "") for m in out)


@pytest.mark.asyncio
async def test_max_turns_ends_with_llm_summary_not_marker():
    """max_turns 用尽时，最后要给**模型自己写的总结**，而不是生硬的停止标记。"""

    class ToolForeverThenSummarize:
        def __init__(self) -> None:
            self.n = 0

        def stream_message(self, messages, system_prompt=None, max_tokens=0,
                           temperature=None, image_parts=None, tools=None,
                           thinking_level=ThinkingLevel.OFF):
            async def gen():
                self.n += 1
                if tools is None:                     # 无工具收尾轮
                    yield LLMStreamChunk.Text("已完成：生成了 1 张图并写好报告。")
                    yield LLMStreamChunk.Finished("end_turn")
                    return
                yield LLMStreamChunk.ToolCallComplete(
                    f"id{self.n}", "shell_execute",
                    {"command": f"step-{self.n}"},
                )
                yield LLMStreamChunk.Finished("tool_use")
            return gen()

    rt = AgentRuntime()
    rt.register(ToolExecutor(_shell_tool_def(), _fake_shell))
    out, stop = await rt.run(
        ToolForeverThenSummarize(),
        [LLMMessage(LLMMessage.Role.USER, "长任务")],
        "s",
        AgentRuntimeOptions(max_turns=3),
    )
    assert stop == "max_turns"
    texts: list[str] = []
    for m in out:
        if m.role is not LLMMessage.Role.ASSISTANT:
            continue
        if m.content:
            texts.append(m.content)
        for p in (m.content_parts or []):
            t = getattr(p, "text", None)
            if t:
                texts.append(t)
    joined = "\n".join(texts)
    assert "已完成" in joined
    assert "reached the maximum number of tool rounds" not in joined
