"""OpenAI Chat Completions provider (+ Responses API scaffold).

Ported from: src/android/app/src/main/java/com/openminis/app/provider/openai/OpenAIProvider.kt
Original package: com.openminis.app.provider.openai

# PORT: scope note
The Android original is 3704 lines and spans Chat Completions, the Responses
API, Azure routing, Codex-backend image generation (gpt-image-2) and the Images
API. This port implements the **Chat Completions path end-to-end** — the
protocol used by the official OpenAI API and by the overwhelming majority of
OpenAI-compatible relays (DeepSeek, Kimi, GLM, Moonshot, OpenRouter, custom
vanity endpoints) — plus the shared send/stream scaffolding. The Responses API
streaming parser is marked with ``# PORT: responses-api …`` stubs where the
Kotlin branches off; wiring it in later only needs filling those branches.

Key behaviours preserved from Kotlin:
- ``[T-android-think-prefix-stream]`` ThinkPrefixStreamParser splits a
  ``<think>…</think>`` PREFIX of ``content`` into ThinkingDelta + Text.
- ``reasoning_content`` deltas stream as ThinkingDelta and are accumulated so
  the exact server value round-trips on the next turn (incl. DeepSeek's
  legitimate empty string, ``[T249/T257]`` history).
- Tool calls streamed by index; every started accumulator is flushed as
  ToolCallComplete at stream end (even without a ``[DONE]`` sentinel).
- ``[T-android-mistral-reasoning-422]`` reasoning_content echo is suppressed on
  Mistral; ``[T-mimo-reasoning-echo]`` echoes ``""`` rather than a learnable
  placeholder when no reasoning was captured.
- ``data:`` with or without the optional space is tolerated (some compatible
  servers emit ``data:{...}`` with no space).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import httpx

from openminis.core.logging import get_logger
from openminis.data.model import (
    AgentToolDefinition,
    LLMError,
    LLMMessage,
    LLMModel,
    LLMResponse,
    LLMStreamChunk,
    LLMUsage,
    ThinkingLevel,
)
from openminis.data.model.agent_content_part import (
    AgentContentPart,
    ImageData,
    Text,
    ToolResult,
    ToolUse,
)
from openminis.provider.llm_provider import LLMProvider, fail_on_silent_empty_completion

__all__ = ["OpenAIProvider", "ThinkPrefixStreamParser", "cap_chat_tool_call_id"]

logger = get_logger(__name__)

# OkHttp timeouts in the Kotlin builder.
CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 600.0
WRITE_TIMEOUT = 30.0

# Chat tool-call ids must fit OpenAI's column; OpenAI emits 24-char call_ ids
# but relays echo longer ones (e.g. Anthropic ids). Cap defensively.
_MAX_CHAT_TOOL_CALL_ID = 60


def cap_chat_tool_call_id(tool_id: str) -> str:
    """Kotlin ``capChatToolCallId`` — ids longer than OpenAI tolerates are cut."""
    return tool_id if tool_id is None or len(tool_id) <= _MAX_CHAT_TOOL_CALL_ID else tool_id[:_MAX_CHAT_TOOL_CALL_ID]


# ---------------------------------------------------------------------------
# <think> prefix splitter
# ---------------------------------------------------------------------------

@dataclass
class _ThinkSegment:
    thinking: str = ""
    visible: str = ""


class ThinkPrefixStreamParser:
    """Kotlin ``ThinkPrefixStreamParser`` (ThinkPrefixStreamParser.kt).

    Splits a ``<think>…</think>`` PREFIX off the text stream. Handles ALL
    models: a turn without the prefix passes through verbatim. Withheld
    trailing whitespace is dropped by design.
    """

    # Matches an OPENING tag only. A literal full tag on one line is treated as
    # a single open+close and split in finish_turn.
    _OPEN_RE = re.compile(r"<think(?:\s[^>]*)?>")

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False
        self._opened = False
        self._closed = False

    def feed(self, text: str) -> _ThinkSegment:
        seg = _ThinkSegment()
        if self._closed:
            # Everything after </think> is visible.
            seg.visible = text
            return seg
        self._buf += text
        if self._opened:
            close = self._buf.find("</think>")
            if close >= 0:
                seg.thinking = self._buf[:close]
                seg.visible = self._buf[close + len("</think>"):]
                self._closed = True
                self._buf = ""
            else:
                # hold; but if buffer is huge, still in think -> no visible
                self._buf = _truncate_keep_head(self._buf)
            return seg
        # not opened yet
        m = self._OPEN_RE.search(self._buf)
        if m is None:
            # No opening tag anywhere. Content is visible — but only if it does
            # not contain a bare "<" that could start a tag across chunks.
            if "<" not in self._buf:
                seg.visible = self._buf
                self._buf = ""
            return seg
        self._opened = True
        self._buf = self._buf[m.end():]
        return self.feed("")  # continue processing remainder

    def finish_turn(self) -> _ThinkSegment:
        seg = _ThinkSegment()
        if self._opened and not self._closed:
            # Unterminated <think> — treat the whole remainder as thinking.
            seg.thinking = self._buf
        elif not self._closed:
            # Never opened: everything left is visible (guard: strip a lone
            # dangling "<" that never completed).
            seg.visible = self._buf
            if "<" in seg.visible:
                seg.visible = seg.visible.split("<")[0]
        self._buf = ""
        self._opened = self._closed = False
        return seg


def _truncate_keep_head(s: str, limit: int = 64_000) -> str:
    return s if len(s) <= limit else s[:limit]


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------

@dataclass
class _ToolCallAccumulator:
    """Kotlin ``ToolCallAccumulator`` (Chat Completions, keyed by index)."""

    id: str = ""
    name: str = ""
    args: list[str] = field(default_factory=list)
    started: bool = False

    @property
    def args_str(self) -> str:
        return "".join(self.args)


class OpenAIProvider(LLMProvider):
    """Kotlin ``class OpenAIProvider`` — Chat Completions first.

    # PORT: Azure / OAuth token plumbing and the codex/Images branches are not
    # ported. ``is_oauth`` is kept as a routing flag but no token refresh runs;
    # API-key auth is the supported path.
    """

    def __init__(
        self,
        api_key: str,
        model: Optional[LLMModel] = None,
        base_url: str = "https://api.openai.com/v1",
        is_oauth: bool = False,
        use_responses_api: bool = False,
        custom_user_agent: Optional[str] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model or LLMModel.gpt_4o_mini  # type: ignore[attr-defined]
        # # PORT: Kotlin keeps an "apiBase" WITHOUT /v1 and appends per endpoint.
        self.base_url = base_url.rstrip("/")
        self.is_oauth = is_oauth
        self.use_responses_api = use_responses_api
        self.custom_user_agent = custom_user_agent
        self.name = "OpenAI"

        host = (self.base_url or "").lower()
        self.is_openrouter = "openrouter" in host
        self.is_mistral = "mistral" in host or (model and "mistral" in (model.id or "").lower())
        # [OpenMinis#191] Top-level cache_control passthrough for OpenRouter→Claude.
        self.needs_openrouter_anthropic_cache_control = self.is_openrouter and (
            model is not None and (model.id or "").startswith("anthropic/")
        )

        self._client: Optional[httpx.AsyncClient] = None

    # --- flags ------------------------------------------------------------
    @property
    def uses_chat_completions_api(self) -> bool:
        """Kotlin: ``forceChatCompletions || (!isOAuth && !useResponsesAPI)``."""
        return not self.is_oauth and not self.use_responses_api

    @property
    def stream_text_is_monolithic(self) -> bool:
        return self.uses_chat_completions_api

    @property
    def default_max_output_tokens(self) -> int:
        return 16_384

    # --- client -----------------------------------------------------------
    def _client_instance(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT, write=WRITE_TIMEOUT),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # --- send (via stream, like Kotlin) -----------------------------------
    async def send_message_clamped(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: Optional[float],
        image_parts: list[LLMMessage.ImagePart],
        tools: list[AgentToolDefinition],
        thinking_level: ThinkingLevel,
    ) -> LLMResponse:
        text_buf: list[str] = []
        stop_reason: Optional[str] = None
        usage: Optional[LLMUsage] = None
        media: list = []
        async for chunk in self.stream_message_clamped(
            messages, system_prompt, max_tokens, temperature,
            image_parts, tools, thinking_level,
        ):
            if isinstance(chunk, LLMStreamChunk.Text):
                text_buf.append(chunk.text)
            elif isinstance(chunk, LLMStreamChunk.Usage):
                usage = chunk.usage
            elif isinstance(chunk, LLMStreamChunk.Finished):
                stop_reason = chunk.stop_reason
            elif isinstance(chunk, LLMStreamChunk.MediaAttachment):
                media.append(chunk.attachment)
        return LLMResponse("".join(text_buf), stop_reason, usage, media)

    def stream_message_clamped(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: Optional[float],
        image_parts: list[LLMMessage.ImagePart],
        tools: list[AgentToolDefinition],
        thinking_level: ThinkingLevel,
    ) -> AsyncIterator[LLMStreamChunk]:
        raw = self._raw_stream_message(
            messages, system_prompt, max_tokens, temperature,
            image_parts, tools, thinking_level,
        )
        return fail_on_silent_empty_completion(raw, self.name)

    # --- streaming --------------------------------------------------------
    async def _raw_stream_message(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: Optional[float],
        image_parts: list[LLMMessage.ImagePart],
        tools: list[AgentToolDefinition],
        thinking_level: ThinkingLevel,
    ) -> AsyncIterator[LLMStreamChunk]:
        if not self.uses_chat_completions_api:
            # # PORT: responses-api — not ported yet. Chat Completions is the
            # # supported path; force the caller onto it rather than silently
            # # emitting an empty stream.
            logger.warning("OpenAIProvider: Responses API requested but not ported; "
                           "falling back to Chat Completions semantics")
        body = self._build_request_body(
            messages, system_prompt, max_tokens, stream=True,
            temperature=temperature, image_parts=image_parts,
            tools=tools, thinking_level=thinking_level,
        )
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()

        client = self._client_instance()
        tool_accs: dict[int, _ToolCallAccumulator] = {}
        reasoning_accum: list[str] = []
        saw_reasoning_field = False
        finish_reason: Optional[str] = None
        sent_finished = False
        think_parser = ThinkPrefixStreamParser()

        try:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    error_body = (await resp.aread()).decode(errors="replace")
                    raise self._map_http_error(resp.status_code, error_body)

                async for raw_line in resp.aiter_lines():
                    line = raw_line.rstrip("\r\n")
                    # Tolerate `data:` with or without the optional space — some
                    # OpenAI-compatible servers emit `data:{...}` with no space.
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):]
                    if payload.startswith(" "):
                        payload = payload[1:]
                    if payload == "[DONE]":
                        # Flush whatever the parser still holds, then the tail.
                        seg = think_parser.finish_turn()
                        if seg.thinking:
                            reasoning_accum.append(seg.thinking)
                            yield LLMStreamChunk.ThinkingDelta(seg.thinking)
                        if seg.visible:
                            yield LLMStreamChunk.Text(seg.visible)
                        for c in await self._tail_chunks(tool_accs, reasoning_accum, finish_reason):
                            yield c
                        sent_finished = True
                        break

                    try:
                        event = json.loads(payload)
                    except ValueError:
                        logger.warning("OpenAIProvider: SSE JSON parse failed: %s payload=%s",
                                       "", payload[:300])
                        continue

                    choices = event.get("choices")
                    if choices:
                        choice = choices[0]
                        delta = choice.get("delta") or {}

                        # reasoning_content → ThinkingDelta + accumulate (exact
                        # server value, incl. DeepSeek's legitimate "").
                        d = delta
                        has_rc_key = "reasoning_content" in d
                        has_reasoning_key = "reasoning" in d
                        if has_rc_key or has_reasoning_key:
                            saw_reasoning_field = True
                            rc = d.get("reasoning_content", "") or ""
                            if rc:
                                reasoning_accum.append(rc)
                                yield LLMStreamChunk.ThinkingDelta(rc)
                            else:
                                # Keep the field-presence flag even on "" (T249).
                                saw_reasoning_field = True

                        # content → <think> splitter
                        content_text = d.get("content", "")
                        if content_text:
                            seg = think_parser.feed(content_text)
                            if seg.thinking:
                                reasoning_accum.append(seg.thinking)
                                yield LLMStreamChunk.ThinkingDelta(seg.thinking)
                            if seg.visible:
                                yield LLMStreamChunk.Text(seg.visible)

                        # tool calls keyed by index
                        tool_calls = d.get("tool_calls")
                        if tool_calls:
                            for tc in tool_calls:
                                idx = tc.get("index", 0)
                                acc = tool_accs.setdefault(idx, _ToolCallAccumulator())
                                tid = tc.get("id") or ""
                                fn = tc.get("function") or {}
                                if tid:
                                    acc.id = tid
                                if fn.get("name"):
                                    acc.name = fn["name"]
                                if fn.get("arguments"):
                                    acc.args.append(fn["arguments"])
                                if not acc.started and acc.id and acc.name:
                                    acc.started = True
                                    yield LLMStreamChunk.ToolUseStart(acc.id, acc.name)
                                if acc.id and acc.args_str:
                                    yield LLMStreamChunk.ToolInputDelta(acc.id, acc.args_str)

                        fr = choice.get("finish_reason")
                        if fr:
                            finish_reason = fr

                    usage = event.get("usage")
                    if usage:
                        yield LLMStreamChunk.Usage(self._parse_chat_completions_usage(usage))

            # -- stream tail (also reached without [DONE]) -----------------
            if not sent_finished:
                # flush <think> remainder
                seg = think_parser.finish_turn()
                if seg.thinking:
                    reasoning_accum.append(seg.thinking)
                    yield LLMStreamChunk.ThinkingDelta(seg.thinking)
                if seg.visible:
                    yield LLMStreamChunk.Text(seg.visible)
                for c in await self._tail_chunks(tool_accs, reasoning_accum, finish_reason):
                    yield c

        except httpx.HTTPError as exc:
            raise self._map_error(exc) from exc

    # --- stream tail helpers ----------------------------------------------
    async def _tail_chunks(
        self,
        tool_accs: dict[int, _ToolCallAccumulator],
        reasoning_accum: list[str],
        finish_reason: Optional[str],
    ) -> list[LLMStreamChunk]:
        """Flush everything a completed stream still holds (Kotlin tail).

        Order matches Kotlin: flush the ``<think>`` parser remainder, emit
        ToolCallComplete for every started accumulator, emit ReasoningContent if
        any reasoning was captured, then Finished — even when the stream ended
        WITHOUT a ``[DONE]`` sentinel.
        """
        chunks: list[LLMStreamChunk] = []

        for acc in tool_accs.values():
            if not acc.started:
                continue
            try:
                args = json.loads(acc.args_str) if acc.args_str.strip() else {}
            except ValueError:
                args = {}
            if not isinstance(args, dict):
                args = {"value": args}
            chunks.append(LLMStreamChunk.ToolCallComplete(acc.id, acc.name, args))
        tool_accs.clear()

        if reasoning_accum:
            chunks.append(LLMStreamChunk.ReasoningContent("".join(reasoning_accum)))
        chunks.append(LLMStreamChunk.Finished(finish_reason))
        return chunks

    # --- request building -------------------------------------------------
    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "content-type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.custom_user_agent:
            headers["User-Agent"] = self.custom_user_agent
        return headers

    def _build_request_body(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str],
        max_tokens: int,
        stream: bool,
        temperature: Optional[float],
        image_parts: list[LLMMessage.ImagePart],
        tools: list[AgentToolDefinition],
        thinking_level: ThinkingLevel,
    ) -> dict[str, Any]:
        """Kotlin ``buildRequestBody`` (Chat Completions shape)."""
        body: dict[str, Any] = {
            "model": self.model.id,
            "stream": stream,
        }
        if self.is_openrouter:
            body["max_tokens"] = max_tokens
        else:
            body["max_completion_tokens"] = max_tokens

        if temperature is not None:
            body["temperature"] = temperature
        if stream and not self.is_openrouter:
            body["stream_options"] = {"include_usage": True}

        # # PORT: thinking — Android delegates to ThinkingRuleResolver (not
        # # ported). Inline approximation: enabled levels map to a clamped
        # # reasoning_effort; the DeepSeek-style explicit "disabled" signal is
        # # sent as reasoning_effort absent + no other thinking keys.
        if not self.is_mistral:
            self._inject_thinking_params(body, thinking_level)

        # [OpenMinis#191] OpenRouter→Anthropic prompt-cache passthrough.
        if self.needs_openrouter_anthropic_cache_control:
            body["cache_control"] = {"type": "ephemeral"}

        if tools:
            body["tools"] = [t.to_openai_json() for t in tools]
            body["tool_choice"] = "auto"

        body["messages"] = self._build_messages(
            messages, system_prompt, image_parts, thinking_level
        )
        return body

    def _inject_thinking_params(self, body: dict[str, Any], level: ThinkingLevel) -> None:
        """Reasoning-effort injection (simplified resolver substitute)."""
        if not level.is_enabled:
            return
        effort = {
            ThinkingLevel.LOW: "low",
            ThinkingLevel.MEDIUM: "medium",
            ThinkingLevel.HIGH: "high",
            ThinkingLevel.XHIGH: "xhigh",
            ThinkingLevel.MAX: "max",
            ThinkingLevel.ULTRA: "max",
        }.get(level, "medium")
        effort = self.clamp_effort(effort, self.model.reasoning_effort_values)
        body["reasoning_effort"] = effort

    @staticmethod
    def clamp_effort(effort: str, values: Optional[list[str]]) -> str:
        """Kotlin ``clampEffort`` — snap to the nearest declared tier at or
        below the request; only step up if no lower tier exists."""
        if not values:
            return effort
        if effort in values:
            return effort
        ladder = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
        try:
            want = ladder.index(effort)
        except ValueError:
            return effort
        declared = sorted(
            (i, v) for i, v in enumerate(ladder) if v in values
        )
        if not declared:
            return effort
        for i, v in reversed(declared):
            if i <= want:
                return v
        return declared[0][1]

    def _build_messages(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str],
        image_parts: list[LLMMessage.ImagePart],
        thinking_level: ThinkingLevel,
    ) -> list[dict]:
        """Kotlin message flattening (Chat Completions)."""
        out: list[dict] = []
        if system_prompt is not None:
            out.append({"role": "system", "content": system_prompt})

        # Reasoning echo rules — see Kotlin comments for the vendor rationale.
        model_always_reasons = self.model.supports_reasoning is True
        model_may_reason = self.model.supports_reasoning is not False
        forbid_reasoning_field = self.is_mistral
        include_reasoning = (
            (thinking_level.is_enabled or model_always_reasons)
            and model_may_reason and not forbid_reasoning_field
        )
        echo_reasoning = include_reasoning
        placeholder_allowed = include_reasoning

        # Track which content_parts carry image bytes so a model without native
        # vision can swap in a text placeholder.
        supports_images = self._model_has_image_input()

        # Structured-part messages first.
        for msg in messages:
            if not msg.content_parts:
                continue
            if msg.role == LLMMessage.Role.ASSISTANT:
                obj: dict[str, Any] = {"role": "assistant"}
                if echo_reasoning:
                    rc = msg.reasoning_content
                    if rc is not None:
                        obj["reasoning_content"] = rc
                    elif placeholder_allowed:
                        # "" satisfies field-presence without a learnable marker.
                        obj["reasoning_content"] = ""
                text_parts = [p for p in msg.content_parts if isinstance(p, Text)]
                if text_parts:
                    obj["content"] = "".join(p.text for p in text_parts)
                tool_use_parts = [p for p in msg.content_parts if isinstance(p, ToolUse)]
                if tool_use_parts:
                    obj["tool_calls"] = [
                        {
                            "id": cap_chat_tool_call_id(p.id),
                            "type": "function",
                            "function": {
                                "name": p.name,
                                "arguments": json.dumps(p.input, ensure_ascii=False),
                            },
                        }
                        for p in tool_use_parts
                    ]
                out.append(obj)
            elif msg.role == LLMMessage.Role.USER:
                tool_results = [p for p in msg.content_parts if isinstance(p, ToolResult)]
                text_parts = [p for p in msg.content_parts if isinstance(p, Text)]
                image_parts_in_msg = [p for p in msg.content_parts if isinstance(p, ImageData)]

                for tr in tool_results:
                    out.append({
                        "role": "tool",
                        "tool_call_id": cap_chat_tool_call_id(tr.id),
                        "content": tr.content,
                    })
                # Non-tool user content (text/image) as one user message.
                if text_parts or image_parts_in_msg:
                    content_arr = []
                    for p in text_parts:
                        content_arr.append({"type": "text", "text": p.text})
                    if image_parts_in_msg and supports_images:
                        for p in image_parts_in_msg:
                            content_arr.append(self._image_block(p.data, p.mime_type))
                    elif image_parts_in_msg:
                        content_arr.append({
                            "type": "text",
                            "text": p.no_vision_placeholder or "[Image omitted — this model has no vision]",
                        })
                    out.append({"role": "user", "content": content_arr})

        # Legacy plain-message loop (content-only messages).
        for msg in messages:
            if msg.content_parts:
                continue  # already emitted above
            if msg.role == LLMMessage.Role.ASSISTANT:
                obj = {"role": "assistant", "content": msg.content}
                if echo_reasoning:
                    rc = msg.reasoning_content
                    if rc is not None:
                        obj["reasoning_content"] = rc
                    elif placeholder_allowed:
                        obj["reasoning_content"] = ""
                out.append(obj)
            else:
                out.append({"role": "user", "content": msg.content})

        # Attach freshly-composed image parts to the final user message.
        if image_parts:
            last_user = next((m for m in reversed(out) if m["role"] == "user"), None)
            if last_user is not None and supports_images:
                if isinstance(last_user.get("content"), str):
                    last_user["content"] = [{"type": "text", "text": last_user["content"]}]
                if isinstance(last_user.get("content"), list):
                    for p in image_parts:
                        last_user["content"].append(self._image_block(p.data, p.mime_type))

        # Drop empty assistant content entirely (server rejects "" content when
        # only tool_calls are present — covered because content key is absent).
        return out

    @staticmethod
    def _image_block(data: bytes, mime_type: str) -> dict:
        import base64
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,"
                       + base64.b64encode(data).decode("ascii"),
            },
        }

    def _model_has_image_input(self) -> bool:
        mods = _normalize_modalities(self.model.input_modalities)
        if mods is None:
            return True  # unknown → assume yes (matches Kotlin default)
        return "image" in mods or "image_input" in mods

    # --- usage / errors ---------------------------------------------------
    @staticmethod
    def _parse_chat_completions_usage(usage: dict) -> LLMUsage:
        prompt_tokens = usage.get("prompt_tokens", 0)
        return LLMUsage(
            input_tokens=prompt_tokens,
            output_tokens=usage.get("completion_tokens", 0),
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
            latest_context_tokens=prompt_tokens,
        )

    @staticmethod
    def _map_http_error(status_code: int, body: str) -> LLMError:
        if status_code in (401, 403):
            return LLMError.InvalidApiKey()
        if status_code == 429:
            return LLMError.RateLimited()
        try:
            payload = json.loads(body)
            err = payload.get("error") or {}
            message = f"[{err.get('type', 'error')}] {err.get('message', body)}"
        except (ValueError, AttributeError):
            message = f"HTTP {status_code}: {body[:500]}"
        if status_code in (500, 502, 503, 504, 529):
            return LLMError.TransientError(message)
        return LLMError.ProviderError(message)

    @staticmethod
    def _map_error(error: Exception) -> LLMError:
        if isinstance(error, LLMError):
            return error
        if isinstance(error, (httpx.ConnectError, httpx.ReadError, httpx.WriteError,
                              httpx.ConnectTimeout, httpx.ReadTimeout)):
            return LLMError.NetworkError(error)
        return LLMError.Unknown(error)


def _normalize_modalities(mods: Optional[list[str]]) -> Optional[list[str]]:
    if not mods:
        return None
    out: list[str] = []
    for m in mods:
        lowered = str(m).lower()
        out.append(lowered)
    return out or None
