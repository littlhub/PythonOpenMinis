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
            for tu in round_tool_uses:
                args_json = json.dumps(tu.input, ensure_ascii=False)
                # Loop-detector gate BEFORE execution. CRITICAL → surface the
                # message as a tool error and do NOT run the tool.
                gate = detector.check(tu.name, tu.input)
                if gate.is_blocking:
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
                    msg = f"Unknown tool: {tu.name}"
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
            else:
                # Gate blocked every tool — do not loop again with the same
                # request; hand back what we have.
                stop_reason = "tool_blocked"
                return messages, stop_reason

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
