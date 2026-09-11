"""Agent runtime — the LLM <-> tool dispatch loop (kernel of the chat).

Ported from: src/android/app/src/main/java/com/openminis/app/ui/chat/ChatViewModel.kt
   (``runAgentLoop`` / ``executeTool`` — UI parts dropped, kernel semantics kept)
Original package: com.openminis.app.ui.chat  →  openminis.agent

The Android original keeps this loop inside a ViewModel because it drives a
Compose screen. In the Python port the *same* loop is the shared kernel used by
the CLI, the TUI and the FastAPI backend — each frontend only supplies a chunk
emitter.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..core.logging import get_logger
from ..data.model import (
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    ThinkingLevel,
)
from ..data.model.agent_content_part import Text, ToolResult, ToolUse
from ..data.model.agent_tool_definition import AgentToolDefinition
from ..tools.tool_execution_result import ToolExecutionResult
from .tool_loop_detector import LoopCheckResult, LoopLevel, ToolLoopDetector

logger = get_logger("agent.runtime")

__all__ = ["AgentRuntime", "AgentChunkSink", "AgentRuntimeOptions", "MAX_AGENT_TURNS"]


def _skill_mistake_hint(name: str) -> str | None:
    """When the model calls a skill name as a tool, explain the right way.

    Skills are knowledge bundles, not tools — so ``visioncustom`` (a skill)
    will never resolve as a tool and the model otherwise stalls or tells the
    user "我没有这个工具". Returns ``None`` when ``name`` isn't a skill at all,
    so the caller falls back to the plain "Unknown tool" message.
    """
    try:
        from ..skills import SkillStore

        entry = SkillStore().get(name)
    except Exception:  # pragma: no cover - broken skill dir must not stall chat
        logger.debug("skill lookup failed while diagnosing %s", name, exc_info=True)
        return None
    if entry is None:
        return None
    return (
        f"「{entry.name}」是技能（skill），不是工具，所以没有这个工具。"
        f"正确做法：调用 skill_use(name=\"{entry.name}\") 加载它的完整说明，"
        f"再按说明用 shell_execute / file_* 执行。"
        f"（若 skill_use 提示未激活，说明该技能当前不在允许范围内，请告知用户去 技能 页激活）"
    )

# Kotlin: ``private const val MAX_AGENT_TURNS = 200`` (ChatViewModel).
MAX_AGENT_TURNS = 200


#: A chunk sink is any async callable accepting one stream chunk. Each
#: frontend wires its own (WebSocket push, TUI event, CLI print…).
AgentChunkSink = Callable[[LLMStreamChunk], Awaitable[None]]


@dataclass(slots=True)
class AgentRuntimeOptions:
    """Per-run knobs (Kotlin passes these through ChatViewModel fields)."""

    system_prompt: Optional[str] = None
    thinking_level: ThinkingLevel = ThinkingLevel.OFF
    temperature: Optional[float] = None
    max_tokens: int = 16_384
    max_turns: int = MAX_AGENT_TURNS
    #: Force a text-only wrap-up after this many *consecutive* tool rounds that
    #: emitted no user-facing text — the "kept calling tools round after round,
    #: never summarized" spiral (ReAct loop that never exits on its own).
    #: ``None`` derives a safe default from ``max_turns`` (see ``run``).
    wrap_up_rounds: Optional[int] = None
    #: Kotlin's per-tool-category timeout defaults (seconds).
    tool_timeout: float = 900.0

    # A ``ToolLoopDetector`` shared across the whole session — pass one in to
    # keep the sliding window across runs. When None a fresh one is created.
    loop_detector: Optional[ToolLoopDetector] = None


@dataclass(slots=True)
class ToolExecutor:
    """One registered tool: JSON-schema definition + async executor.

    Kotlin wires tools via ``when(name) { … }`` in ``executeTool``; the Python
    port replaces that chain with a registry dict. ``executor`` receives the
    raw args JSON plus the session id and returns a ToolExecutionResult.
    """

    definition: AgentToolDefinition
    executor: Callable[..., Awaitable[ToolExecutionResult]]

    @property
    def name(self) -> str:
        return self.definition.name


class AgentRuntime:
    """Runs the message history through the provider until the model stops.

    Loop semantics mirror Kotlin ``runAgentLoop``:

    1. Send the full message history (including every prior turn's tool_use /
       tool_result parts) to ``provider.stream_message``.
    2. Stream chunks out to the sink; accumulate assistant text and any
       completed tool calls.
    3. If the turn produced tool calls, execute them one by one (loop-detector
       gate first, ``record`` after), append assistant tool_use + user
       tool_result messages, and go again.
    4. Otherwise the turn is final — return the accumulated response.
    """

    def __init__(
        self,
        tools: Optional[dict[str, ToolExecutor]] = None,
        chunk_sink: Optional[AgentChunkSink] = None,
    ) -> None:
        self.tools: dict[str, ToolExecutor] = tools or {}
        self.chunk_sink: Optional[AgentChunkSink] = chunk_sink

    # ── tool registry helpers ──────────────────────────────────────────────
    def register(self, tool: ToolExecutor) -> None:
        self.tools[tool.name] = tool

    def tool_definitions(self, include: Optional[set[str]] = None) -> list[AgentToolDefinition]:
        if include is None:
            return [t.definition for t in self.tools.values()]
        return [self.tools[n].definition for n in include if n in self.tools]

    async def _emit(self, chunk: LLMStreamChunk) -> None:
        if self.chunk_sink is not None:
            await self.chunk_sink(chunk)

    # ── main loop ──────────────────────────────────────────────────────────
    async def run(
        self,
        provider: Any,  # LLMProvider
        messages: list[LLMMessage],
        session_id: str,
        options: Optional[AgentRuntimeOptions] = None,
        session_user_env: Optional[dict[str, str]] = None,
    ) -> tuple[list[LLMMessage], Optional[str]]:
        """Run the agent loop over ``messages`` (mutated in place with new turns).

        Returns ``(messages, final_stop_reason)``. ``messages`` grows with every
        tool round, so the caller can persist it and continue later.
        """
        opts = options or AgentRuntimeOptions()
        detector = opts.loop_detector or ToolLoopDetector()
        tool_defs = self.tool_definitions()

        final_text: list[str] = []
        stop_reason: Optional[str] = None
        used_tool_rounds = 0
        last_tool_round_emitted = False
        #: Consecutive rounds in which EVERY tool call was blocked by the loop
        #: gate. Blocked calls never enter the detector history, so without a
        #: runtime counter the model can spin on "blocked → retry → blocked"
        #: forever (only MAX_AGENT_TURNS would stop it). 2 strikes → hard stop.
        blocked_rounds = 0
        #: Consecutive rounds that ran tools but wrote nothing to the user.
        #: The loop's ONLY natural exit is the model declining to call a tool;
        #: when it falls into an act-only rhythm (very common after a research
        #: step: "fetch → fetch → fetch…") that exit is never reached and the
        #: turn dies on the terse max_turns marker with no answer. After
        #: ``wrap_up_after`` such rounds we force a text-only wrap-up so the
        #: turn actually closes with a summary instead of spinning.
        stalled_rounds = 0
        wrap_up_after = (
            opts.wrap_up_rounds if opts.wrap_up_rounds is not None
            else max(6, min(12, opts.max_turns))
        )

        for turn in range(opts.max_turns):
            turn_start = len(messages)
            round_text: list[str] = []
            round_tool_uses: list[ToolUse] = []
            seen_finished = False
            turn_stop_reason: Optional[str] = None

            # ── 1. stream the turn ─────────────────────────────────────────
            try:
                stream = provider.stream_message(
                    messages,
                    opts.system_prompt,
                    opts.max_tokens,
                    opts.temperature,
                    tools=tool_defs,
                    thinking_level=opts.thinking_level,
                )
                async for chunk in stream:
                    await self._emit(chunk)
                    if chunk is LLMStreamChunk.Started:  # singleton instance
                        continue
                    if isinstance(chunk, LLMStreamChunk.Text):
                        round_text.append(chunk.text)
                    elif isinstance(chunk, LLMStreamChunk.ThinkingDelta):
                        # Thinking deltas are surfaced via the sink; the history
                        # keeps them opaque (Kotlin stores reasoning only at the
                        # message level for UI, not in tool rounds).
                        pass
                    elif isinstance(chunk, LLMStreamChunk.ReasoningContent):
                        pass
                    elif isinstance(chunk, LLMStreamChunk.ToolCallComplete):
                        # Older provider code may have emitted ToolUseStart/
                        # ToolInputDelta first — those are transient, only the
                        # completed call is recorded.
                        round_tool_uses.append(ToolUse(
                            id=chunk.id or f"toolu_{time.time_ns()}",
                            name=chunk.name,
                            input=chunk.args or {},
                        ))
                    elif isinstance(chunk, LLMStreamChunk.Finished):
                        seen_finished = True
                        turn_stop_reason = chunk.stop_reason
            except Exception as exc:  # network / api / auth errors
                logger.warning("agent turn %s failed: %s: %s",
                               turn, type(exc).__name__, exc)
                err_msg = LLMMessage(
                    LLMMessage.Role.ASSISTANT,
                    "",
                    content_parts=[
                        Text(f"[agent error: {type(exc).__name__}] {exc}")
                    ],
                )
                messages.append(err_msg)
                return messages, "error"

            # Kotlin tracks the finished reason even when the provider forgets
            # the [DONE] frame — a stop with no tool calls is final regardless.
            if round_tool_uses:
                used_tool_rounds += 1
                last_tool_round_emitted = False
            elif not seen_finished and not round_text:
                # Provider ended the stream without either text or tools — this
                # is the silent-empty case the caller guards against.
                if not round_tool_uses:
                    break

            # ── 2. persist this turn's assistant text / tool_use ───────────
            assistant_parts: list = []
            text_joined = "".join(round_text)
            if text_joined:
                assistant_parts.append(Text(text_joined))
                final_text.append(text_joined)
            for tu in round_tool_uses:
                assistant_parts.append(tu)

            if not assistant_parts and not round_tool_uses:
                # Nothing produced at all — bail to avoid an infinite empty loop.
                break

            if assistant_parts:
                messages.append(LLMMessage(
                    LLMMessage.Role.ASSISTANT,
                    "",
                    content_parts=assistant_parts,
                ))

            # ── 3. no tools → final answer ─────────────────────────────────
            if not round_tool_uses:
                stop_reason = turn_stop_reason or "end_turn"
                return messages, stop_reason

            # ── 4. execute each tool call ──────────────────────────────────
            tool_result_parts: list[ToolResult] = []
            executed_any = False
            blocked_any = False
            for tu in round_tool_uses:
                args_json = json.dumps(tu.input, ensure_ascii=False)
                # Loop-detector gate BEFORE execution. CRITICAL → surface the
                # message as a tool error and do NOT run the tool.
                gate = detector.check(tu.name, tu.input)
                if gate.is_blocking:
                    blocked_any = True
                    logger.warning("agent blocked tool %s: %s", tu.name, gate.message)
                    tool_result_parts.append(ToolResult(
                        id=tu.id, name=tu.name,
                        content=gate.message or "[LOOP BLOCKED]",
                        is_error=True,
                    ))
                    await self._emit(LLMStreamChunk.ToolResult(
                        id=tu.id, name=tu.name,
                        content=gate.message or "[LOOP BLOCKED]",
                        is_error=True,
                    ))
                    continue

                executor = self.tools.get(tu.name)
                if executor is None:
                    # Mirrors Kotlin ``else -> ToolExecutionResult("Unknown
                    # tool: $name", false)`` — the loop detector recognises this
                    # phrasing and escalates repeated hallucinations.
                    # Before giving up: the model often calls a *skill* name as
                    # if it were a tool ("我没有名为 X 的工具" is the user-facing
                    # symptom). Point it at skill_use instead.
                    msg = _skill_mistake_hint(tu.name) or f"Unknown tool: {tu.name}"
                    logger.warning("agent: %s", msg)
                    detector.record(tu.name, tu.input, None, error_message=msg,
                                    tool_call_id=tu.id)
                    tool_result_parts.append(ToolResult(
                        id=tu.id, name=tu.name, content=msg, is_error=True,
                    ))
                    await self._emit(LLMStreamChunk.ToolResult(
                        id=tu.id, name=tu.name, content=msg, is_error=True,
                    ))
                    continue

                try:
                    result = await executor.executor(
                        args_json, session_id,
                        **({"env": session_user_env} if session_user_env else {}),
                    )
                    executed_any = True
                except Exception as exc:
                    logger.warning("tool %s raised %s: %s",
                                   tu.name, type(exc).__name__, exc)
                    result = ToolExecutionResult(
                        f"[tool error: {type(exc).__name__}] {exc}", False,
                        tool_title=tu.name,
                    )

                # Loop-detector record AFTER execution (may attach a warning).
                rec = detector.record(tu.name, tu.input, result.output,
                                      error_message=None if result.success
                                      else result.output,
                                      tool_call_id=tu.id)
                out_text = result.output
                if rec.level == LoopLevel.WARNING and rec.message:
                    out_text = f"{out_text}\n\n{rec.message}"

                # One-line run-trace entry so server logs keep an auditable
                # "which tool ran when" trail (name, success, output size).
                logger.info(
                    "tool_call session=%s name=%s ok=%s chars=%s",
                    session_id, tu.name, result.success, len(out_text or ""),
                )

                if not result.success and not result.output.startswith("[command timed out"):
                    out_text = out_text or "(failed with no output)"

                tool_result_parts.append(ToolResult(
                    id=tu.id, name=tu.name, content=out_text,
                    is_error=not result.success,
                ))
                # Notify the UI chunk sink that this tool call has finished.
                await self._emit(LLMStreamChunk.ToolResult(
                    id=tu.id, name=tu.name, content=out_text,
                    is_error=not result.success,
                ))

            # ── 5. append the tool-result turn and continue ────────────────
            if tool_result_parts:
                messages.append(LLMMessage(
                    LLMMessage.Role.USER,
                    "",
                    content_parts=tool_result_parts,
                ))
                if blocked_any and not executed_any:
                    blocked_rounds += 1
                    if blocked_rounds >= 2:
                        # Hard stop: the model kept calling the same tool even
                        # after a CRITICAL block. Make one final no-tools call
                        # so it wraps up with what it already has, instead of
                        # burning rounds until MAX_AGENT_TURNS.
                        messages.append(LLMMessage(
                            LLMMessage.Role.USER,
                            "[loop protection] Repeated calls to the same tool are no longer bringing new information. "
                            "Please ignore tool calls, directly organize the information already obtained and reply to the user; "
                            "if it is indeed impossible to proceed, please clearly state what additional input is still needed.",
                        ))
                        wrap_up: list[str] = []
                        stream = provider.stream_message(
                            messages,
                            opts.system_prompt,
                            opts.max_tokens,
                            opts.temperature,
                            tools=None,
                            thinking_level=opts.thinking_level,
                        )
                        async for chunk in stream:
                            await self._emit(chunk)
                            if isinstance(chunk, LLMStreamChunk.Text):
                                wrap_up.append(chunk.text)
                        summary = "".join(wrap_up).strip() or (
                            "Task interrupted: tool calls fell into a loop, and no new progress was made."
                        )
                        messages.append(LLMMessage(
                            LLMMessage.Role.ASSISTANT, summary,
                        ))
                        logger.warning(
                            "agent hard-stopped: %s consecutive fully-blocked rounds",
                            blocked_rounds,
                        )
                        return messages, "tool_loop_blocked"
                else:
                    blocked_rounds = 0
            else:
                # Gate blocked every tool — do not loop again with the same
                # request; hand back what we have.
                stop_reason = "tool_blocked"
                return messages, stop_reason

            # ── 5b. wrap-up guard: act-only rounds with no user-facing text ─
            # The loop's only natural exit is the model declining to call a
            # tool. A round that wrote nothing means the model is still
            # "acting", not "answering" — and a research step ("fetch →
            # fetch → fetch…") can drop it into that rhythm forever, so the
            # exit is never reached and the turn dies on the max_turns marker
            # with no answer. Count consecutive silent tool rounds and, once
            # the budget is nearly spent, force a tool-free final call whose
            # text IS the answer — the loop then exits as an answered turn.
            if text_joined:
                stalled_rounds = 0
            else:
                stalled_rounds += 1

            if stalled_rounds >= wrap_up_after:
                messages.append(LLMMessage(
                    LLMMessage.Role.USER,
                    f"[auto wrap-up] 你已经连续 {stalled_rounds} 轮调用工具，"
                    "但没有向用户输出任何内容（任务似乎已完成，但循环没能自行收尾）。"
                    "如果所需信息或操作已经完成，请立即停止调用工具，直接用文本把"
                    "已有结果整理总结回复给用户；如果确实还缺少关键信息或需要用户"
                    "确认，请直接用文本说明还缺什么。本轮不要再调用任何工具。",
                ))
                wrap_up: list[str] = []
                try:
                    stream = provider.stream_message(
                        messages,
                        opts.system_prompt,
                        opts.max_tokens,
                        opts.temperature,
                        tools=None,
                        thinking_level=opts.thinking_level,
                    )
                    async for chunk in stream:
                        await self._emit(chunk)
                        if isinstance(chunk, LLMStreamChunk.Text):
                            wrap_up.append(chunk.text)
                except Exception as exc:  # a failed wrap-up must not lose the turn
                    logger.warning("wrap-up turn failed: %s: %s",
                                   type(exc).__name__, exc)
                summary = "".join(wrap_up).strip() or (
                    "任务已执行多轮工具调用，但未能自动收尾，已为你结束本轮。"
                    "如需继续，请缩小任务范围或补充更明确的目标后再试。"
                )
                messages.append(LLMMessage(LLMMessage.Role.ASSISTANT, summary))
                logger.warning(
                    "agent auto wrap-up after %s consecutive tool-only rounds",
                    stalled_rounds,
                )
                return messages, "auto_wrap_up"

            _ = turn_start

        # ── max_turns exhausted ────────────────────────────────────────────
        logger.warning("agent hit max_turns=%s", opts.max_turns)
        if not last_tool_round_emitted:
            messages.append(LLMMessage(
                LLMMessage.Role.ASSISTANT,
                "",
                content_parts=[Text(
                    "\n\n[stopped: reached the maximum number of tool rounds "
                    f"({opts.max_turns}). Try narrowing the task or asking a "
                    "new question.]"
                )],
            ))
        return messages, "max_turns"
