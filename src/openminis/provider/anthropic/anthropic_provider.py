"""Anthropic Messages API provider.

Ported from: src/android/app/src/main/java/com/openminis/app/provider/anthropic/AnthropicProvider.kt
Original package: com.openminis.app.provider.anthropic

# PORT: transport mapping
- OkHttp -> ``httpx.AsyncClient`` (per-instance, same 30 s connect / 10 min read
  timeouts as the Kotlin builder).
- SSE parsing stays line-oriented: the Kotlin ``BufferedReader`` loop over
  ``data: {...}`` frames becomes ``resp.aiter_lines()``. The event shapes
  (``message_start`` / ``content_block_start`` / ``content_block_delta`` /
  ``content_block_stop`` / ``message_delta``) are byte-for-byte the same.
- ``Flow<LLMStreamChunk>`` -> async generator; ``callbackFlow { send(x) }`` ->
  ``yield x``.
- ``[T-android-empty-stream-retry]`` is preserved by piping the generator through
  ``fail_on_silent_empty_completion``.
"""

from __future__ import annotations

import base64
import json
import re
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

__all__ = ["AnthropicProvider", "sanitize_tool_id"]

logger = get_logger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE_PATH = "https://api.anthropic.com"

# OkHttp timeouts in the Kotlin builder: connect 30 s, read 10 min, write 30 s.
CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 600.0
WRITE_TIMEOUT = 30.0

# Anthropic requires tool ids to match ^[a-zA-Z0-9_-]+$. OpenAI Responses can
# emit ids like `call_abc|fc_def` (pipe), so echo every id through this.
_INVALID_TOOL_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_tool_id(tool_id: str) -> str:
    """Kotlin ``sanitizeToolId`` — force an id into Anthropic's charset."""
    return _INVALID_TOOL_ID_CHARS.sub("_", tool_id or "")


class AnthropicProvider(LLMProvider):
    """Kotlin ``class AnthropicProvider(...) : LLMProvider``."""

    def __init__(
        self,
        api_key: str,
        model: Optional[LLMModel] = None,
        base_path: str = DEFAULT_BASE_PATH,
        is_oauth: bool = False,
        custom_user_agent: Optional[str] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model or LLMModel.claude_haiku45
        self.base_path = base_path.rstrip("/")
        self.is_oauth = is_oauth
        self.custom_user_agent = custom_user_agent

        self.name = "Anthropic"
        # [T-android-enhanced-cache] 1-hour prompt-cache TTL. Off by default.
        self.enhanced_cache = False

        # Use Bearer auth for custom (non-Anthropic) base URLs or OAuth.
        self.is_custom_endpoint = self.base_path != DEFAULT_BASE_PATH

        self._client: Optional[httpx.AsyncClient] = None

    # --- LLMProvider surface ---------------------------------------------
    @property
    def default_max_output_tokens(self) -> int:
        return 64_000

    def _client_instance(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT, write=WRITE_TIMEOUT),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # --- cache control ----------------------------------------------------
    def _ephemeral_cache_control(self) -> dict[str, Any]:
        """Default 5-minute TTL; enhanced adds ``ttl: "1h"``."""
        cc: dict[str, Any] = {"type": "ephemeral"}
        if self.enhanced_cache:
            cc["ttl"] = "1h"
        return cc

    # --- non-streaming ----------------------------------------------------
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
        body = self._build_request_body(
            messages, system_prompt, max_tokens, stream=False,
            temperature=temperature, image_parts=image_parts,
            tools=tools, thinking_level=thinking_level,
        )
        url, headers = self._build_request(body)
        client = self._client_instance()
        resp = await client.post(url, headers=headers, json=body)
        text = resp.text
        if resp.status_code >= 400:
            raise self._map_http_error(resp.status_code, text)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise LLMError.ProviderError(f"Invalid JSON: {exc}") from exc
        return self._parse_response(payload)

    # --- streaming --------------------------------------------------------
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
        body = self._build_request_body(
            messages, system_prompt, max_tokens, stream=True,
            temperature=temperature, image_parts=image_parts,
            tools=tools, thinking_level=thinking_level,
        )
        url, headers = self._build_request(body)
        client = self._client_instance()

        current_tool_id: Optional[str] = None
        current_tool_name: Optional[str] = None
        tool_input_buffer: list[str] = []

        try:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    error_body = (await resp.aread()).decode(errors="replace")
                    raise self._map_http_error(resp.status_code, error_body)

                async for raw_line in resp.aiter_lines():
                    line = raw_line.rstrip("\r\n")
                    if not line.startswith("data: "):
                        continue
                    payload_str = line[len("data: "):]
                    if payload_str == "[DONE]":
                        break
                    try:
                        event = json.loads(payload_str)
                    except ValueError:
                        continue

                    event_type = event.get("type")

                    if event_type == "message_start":
                        yield LLMStreamChunk.Started
                        usage = (event.get("message") or {}).get("usage")
                        if usage:
                            yield LLMStreamChunk.Usage(self._parse_usage(usage))

                    elif event_type == "content_block_start":
                        block = event.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            current_tool_id = block.get("id", "")
                            current_tool_name = block.get("name", "")
                            tool_input_buffer = []
                            yield LLMStreamChunk.ToolUseStart(current_tool_id, current_tool_name)

                    elif event_type == "content_block_delta":
                        delta = event.get("delta") or {}
                        dtype = delta.get("type")
                        if dtype == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield LLMStreamChunk.Text(text)
                        elif dtype == "thinking_delta":
                            thinking = delta.get("thinking", "")
                            if thinking:
                                yield LLMStreamChunk.ThinkingDelta(thinking)
                        elif dtype == "input_json_delta":
                            partial = delta.get("partial_json", "")
                            if partial and current_tool_id is not None:
                                tool_input_buffer.append(partial)
                                yield LLMStreamChunk.ToolInputDelta(
                                    current_tool_id, "".join(tool_input_buffer)
                                )

                    elif event_type == "content_block_stop":
                        if current_tool_id is not None and current_tool_name is not None:
                            args = self._safe_json("".join(tool_input_buffer))
                            yield LLMStreamChunk.ToolCallComplete(
                                current_tool_id, current_tool_name, args
                            )
                            current_tool_id = None
                            current_tool_name = None
                            tool_input_buffer = []

                    elif event_type == "message_delta":
                        usage = event.get("usage")
                        if usage:
                            yield LLMStreamChunk.Usage(self._parse_usage(usage))
                        stop_reason = (event.get("delta") or {}).get("stop_reason") or None
                        yield LLMStreamChunk.Finished(stop_reason)

        except httpx.HTTPError as exc:
            raise self._map_error(exc) from exc

    # --- request building -------------------------------------------------
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
        """Kotlin ``buildRequestBody``."""
        model_id = self.model.id
        body: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if (
            temperature is not None
            and not thinking_level.is_enabled
            and not self.model_rejects_temperature(model_id)
        ):
            body["temperature"] = temperature

        # Thinking / extended thinking. Two protocol shapes:
        #   - Claude 4.6+ (adaptive): thinking.type="adaptive" + output_config.effort
        #   - Claude <=4.5 (legacy): thinking.type="enabled" + budget_tokens
        # # PORT: Android delegates the shape decision to
        # ``ThinkingRuleResolver.anthropicThinkingShape``; that resolver is not
        # ported yet, so the adaptive/legacy branch below (backed by
        # ``model_uses_adaptive_thinking``) owns the decision here, matching the
        # Kotlin body's behaviour byte-for-byte.
        logger.info("[resolve] provider=anthropic model=%s level=%s adaptive=%s",
                    model_id, thinking_level.name,
                    self.model_uses_adaptive_thinking(model_id))

        if thinking_level.is_enabled:
            if self.model_uses_adaptive_thinking(model_id):
                # [T-anthropic-thinking-display] Explicitly request summarized
                # thinking — newer models default `display` to "omitted", which
                # returns an empty thinking block (only a signature).
                body["thinking"] = {"type": "adaptive", "display": "summarized"}
                body["output_config"] = {"effort": self.thinking_effort(thinking_level)}
            else:
                budget = self.thinking_budget(max_tokens, thinking_level)
                if budget > 0:
                    body["thinking"] = {"type": "enabled", "budget_tokens": budget}
                    if not self.model_rejects_temperature(model_id):
                        body["temperature"] = 1
        elif self.model_uses_adaptive_thinking(model_id):
            # Adaptive-generation models think by DEFAULT when the request
            # carries no thinking field — "off" must be sent explicitly.
            body["thinking"] = {"type": "disabled"}

        system_blocks = self._resolve_system_prompt(system_prompt)
        if system_blocks is not None:
            body["system"] = system_blocks

        # Tools: eager_input_streaming on every tool; cache_control on the LAST
        # tool (first of the four prompt-cache breakpoints Anthropic honors).
        if tools:
            tools_array = []
            for i, tool in enumerate(tools):
                tool_json = tool.to_anthropic_json()
                tool_json["eager_input_streaming"] = True
                if i == len(tools) - 1:
                    tool_json["cache_control"] = self._ephemeral_cache_control()
                tools_array.append(tool_json)
            body["tools"] = tools_array
            body["tool_choice"] = {"type": "auto"}

        raw_messages = self._build_messages(messages, image_parts)
        merged = self._merge_consecutive_same_role(raw_messages)
        self._inject_message_cache_control(merged)
        body["messages"] = merged
        return body

    def _build_request(self, body: dict[str, Any]) -> tuple[str, dict[str, str]]:
        """Kotlin ``buildRequest`` — URL + headers (no OkHttp Body object).

        T192: strip a trailing ``/v1`` from a custom base so we never produce
        ``.../v1/v1/messages`` for Anthropic-compatible endpoints.
        """
        clean_base = self.base_path.rstrip("/")
        if clean_base.endswith("/v1"):
            clean_base = clean_base[:-3].rstrip("/")
        url = f"{clean_base}/v1/messages"

        headers: dict[str, str] = {
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        beta_flags: list[str] = []
        if self.is_oauth:
            # [T-anthropic-redact-thinking] "redact-thinking-2026-02-12" is
            # deliberately OMITTED — it returns an empty `thinking` string with
            # only a signature, so reasoning runs but no text is shown.
            beta_flags.extend([
                "claude-code-20250219",
                "oauth-2025-04-20",
                "interleaved-thinking-2025-05-14",
                "prompt-caching-scope-2026-01-05",
                "effort-2025-11-24",
                "context-management-2025-06-27",
                "extended-cache-ttl-2025-04-11",
            ])
        elif "thinking" in body:
            if body.get("thinking", {}).get("type") == "adaptive":
                beta_flags.append("effort-2025-11-24")
            else:
                beta_flags.append("interleaved-thinking-2025-05-14")
        if self.enhanced_cache and not self.is_oauth:
            beta_flags.append("extended-cache-ttl-2025-04-11")
        if beta_flags:
            headers["anthropic-beta"] = ",".join(beta_flags)

        if self.is_oauth:
            # Stainless / CLI fingerprint — Anthropic's backend pairs UA +
            # X-Stainless-* to decide whether the request is the official CLI.
            headers["User-Agent"] = "claude-cli/2.1.195 (external, cli)"
            headers["X-Stainless-Lang"] = "js"
            headers["X-Stainless-Package-Version"] = "0.106.0"
            headers["X-Stainless-OS"] = "Linux"
            headers["X-Stainless-Arch"] = "arm64"
            headers["X-Stainless-Runtime"] = "node"
            headers["X-Stainless-Runtime-Version"] = "v24.18.0"
            headers["X-Stainless-Retry-Count"] = "0"
            headers["X-Stainless-Timeout"] = "600"
            headers["X-App"] = "cli"
            headers["Anthropic-Dangerous-Direct-Browser-Access"] = "true"
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif not self.api_key:
            # [T-empty-key-compat-endpoints] Keyless third-party endpoint: send
            # NO auth header rather than a malformed empty one strict relays reject.
            pass
        elif self.is_custom_endpoint:
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            headers["x-api-key"] = self.api_key

        if self.custom_user_agent:
            headers["User-Agent"] = self.custom_user_agent
        return url, headers

    # --- system prompt ----------------------------------------------------
    def _resolve_system_prompt(self, user_prompt: Optional[str]) -> Optional[list[dict]]:
        """Kotlin ``resolveSystemPrompt``.

        # PORT: the OAuth path in Kotlin force-prepends the Claude Code prefix
        # from ``ClaudeOAuthManager``. That constant lives in the auth module,
        # which is not ported yet; we import it lazily and fall back to the
        # API-key shape when unavailable.
        """
        if not user_prompt:
            return None
        if self.is_oauth:
            prefix = self._claude_code_prefix()
            if prefix:
                tail = user_prompt.replace(prefix, "", 1).strip() if prefix in user_prompt else user_prompt
                blocks: list[dict] = [{"type": "text", "text": prefix}]
                if tail:
                    blocks.append({
                        "type": "text",
                        "text": tail,
                        "cache_control": self._ephemeral_cache_control(),
                    })
                return blocks
        return [{
            "type": "text",
            "text": user_prompt,
            "cache_control": self._ephemeral_cache_control(),
        }]

    @staticmethod
    def _claude_code_prefix() -> Optional[str]:
        """Claude Code identifier prompt required by Anthropic's OAuth gate."""
        try:
            from openminis.auth.claude_oauth_manager import (  # type: ignore
                ANTHROPIC_OAUTH_IDENTIFIER_PROMPT,
            )
            return ANTHROPIC_OAUTH_IDENTIFIER_PROMPT
        except Exception:
            return None

    # --- messages ---------------------------------------------------------
    def _build_messages(
        self,
        raw_messages: list[LLMMessage],
        image_parts: list[LLMMessage.ImagePart],
    ) -> list[dict]:
        """Kotlin ``buildMessages``."""
        messages = self._strip_orphan_tool_results(raw_messages)
        echo_thinking = self._should_echo_interleaved_thinking()
        out: list[dict] = []
        for msg in messages:
            obj: dict[str, Any] = {"role": msg.role.value}

            if msg.content_parts:
                content: list[dict] = []
                # [T-android-anthropic-thinking-echo] (#70) Synthesized thinking
                # block must LEAD the assistant turn (Anthropic requires it
                # before text/tool_use). Fall back to "" — a field-presence
                # placeholder — so compat proxies don't 400 on turns whose
                # reasoning was never captured.
                if echo_thinking and msg.role == LLMMessage.Role.ASSISTANT:
                    content.append({
                        "type": "thinking",
                        "thinking": msg.reasoning_content or "",
                    })
                for part in msg.content_parts:
                    content.extend(self._content_part_to_blocks(part))
                obj["content"] = content
            else:
                obj["content"] = msg.content
            out.append(obj)

        # Attach freshly-composed image parts to the last user turn.
        if image_parts and out and out[-1]["role"] == "user":
            self._attach_image_parts(out[-1], image_parts)
        return out

    def _content_part_to_blocks(self, part: AgentContentPart) -> list[dict]:
        if isinstance(part, Text):
            return [{"type": "text", "text": part.text}]
        if isinstance(part, ToolUse):
            return [{
                "type": "tool_use",
                "id": sanitize_tool_id(part.id),
                "name": part.name,
                "input": part.input,
            }]
        if isinstance(part, ToolResult):
            blocks: list[dict] = [{"type": "text", "text": part.content}]
            if part.image_data and part.image_mime_type:
                safe_bytes, safe_mime = self._compress_under_budget(
                    part.image_data, part.image_mime_type
                )
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": safe_mime,
                        "data": base64.b64encode(safe_bytes).decode("ascii"),
                    },
                })
            result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": sanitize_tool_id(part.id),
                "content": blocks,
            }
            if part.is_error:
                result["is_error"] = True
            return [result]
        if isinstance(part, ImageData):
            safe_bytes, safe_mime = self._compress_under_budget(part.data, part.mime_type)
            return [{
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": safe_mime,
                    "data": base64.b64encode(safe_bytes).decode("ascii"),
                },
            }]
        return []

    @staticmethod
    def _compress_under_budget(data: bytes, mime: str) -> tuple[bytes, str]:
        """T-imgsize: re-encode history images that blew the per-image cap."""
        try:
            from openminis.provider.image_budget import ImageBudget
            safe = ImageBudget.compress_under_budget(data)
            return safe, (mime if safe is data else "image/jpeg")
        except Exception:
            return data, mime

    def _attach_image_parts(self, msg: dict, image_parts: list[LLMMessage.ImagePart]) -> None:
        content = msg.get("content")
        if not isinstance(content, list):
            content = [{"type": "text", "text": content or ""}]
        for part in image_parts:
            safe_bytes, safe_mime = self._compress_under_budget(part.data, part.mime_type)
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": safe_mime,
                    "data": base64.b64encode(safe_bytes).decode("ascii"),
                },
            })
        msg["content"] = content

    def _should_echo_interleaved_thinking(self) -> bool:
        """Kotlin ``shouldEchoInterleavedThinking``.

        Only custom (non-api.anthropic.com) endpoints need the synthesized
        thinking block — see the Kotlin comment at the call site.
        """
        return self.is_custom_endpoint

    @staticmethod
    def _strip_orphan_tool_results(messages: list[LLMMessage]) -> list[LLMMessage]:
        """Kotlin ``stripOrphanToolResults``.

        Drop ``tool_result`` parts whose ``tool_use_id`` does not match a
        ``tool_use`` seen earlier. Anthropic 400s with "unexpected tool_use_id
        ... no corresponding tool_use block".
        """
        known_ids: set[str] = set()
        for msg in messages:
            for part in msg.content_parts:
                if isinstance(part, ToolUse):
                    known_ids.add(sanitize_tool_id(part.id))

        out: list[LLMMessage] = []
        for msg in messages:
            if not any(isinstance(p, ToolResult) for p in msg.content_parts):
                out.append(msg)
                continue
            kept = [
                p for p in msg.content_parts
                if not (isinstance(p, ToolResult) and sanitize_tool_id(p.id) not in known_ids)
            ]
            if len(kept) != len(msg.content_parts):
                # Operate on a copy — the caller's stored history is untouched.
                copy = LLMMessage(
                    role=msg.role,
                    content=msg.content,
                    image_parts=list(msg.image_parts),
                    audio_parts=list(msg.audio_parts),
                    content_parts=kept,
                    db_message_id=msg.db_message_id,
                    reasoning_content=msg.reasoning_content,
                )
                out.append(copy)
            else:
                out.append(msg)
        return out

    @staticmethod
    def _merge_consecutive_same_role(messages: list[dict]) -> list[dict]:
        """Kotlin ``mergeConsecutiveSameRole``.

        Anthropic rejects two consecutive messages with the same role. Assistant
        turns are additionally reordered: text/image first, then tool_use
        (Anthropic rejects text appearing after tool_use in the same message).
        """
        merged: list[dict] = []
        for msg in messages:
            prev = merged[-1] if merged else None
            if prev is not None and prev.get("role") == msg.get("role"):
                blocks: list[dict] = []
                AnthropicProvider._collect_content_blocks(prev, blocks)
                AnthropicProvider._collect_content_blocks(msg, blocks)
                role = prev.get("role")
                if role == "assistant":
                    textish = [
                        b for b in blocks
                        if b.get("type") not in ("thinking", "tool_use")
                    ]
                    tool_use = [b for b in blocks if b.get("type") == "tool_use"]
                    thinking = [b for b in blocks if b.get("type") == "thinking"]
                    blocks = thinking + textish + tool_use
                prev["content"] = blocks
            else:
                merged.append(json.loads(json.dumps(msg)))
        return merged

    @staticmethod
    def _collect_content_blocks(msg: dict, out: list[dict]) -> None:
        content = msg.get("content")
        if isinstance(content, list):
            out.extend(content)
        elif content is not None:
            out.append({"type": "text", "text": content})

    def _inject_message_cache_control(self, messages: list[dict]) -> None:
        """Kotlin ``injectMessageCacheControl`` — cache the last 2 user turns."""
        user_indexes = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        for idx in user_indexes[-2:]:
            msg = messages[idx]
            content = msg.get("content")
            if isinstance(content, list):
                if content:
                    content[-1]["cache_control"] = self._ephemeral_cache_control()
            else:
                msg["content"] = [{
                    "type": "text",
                    "text": content or "",
                    "cache_control": self._ephemeral_cache_control(),
                }]

    # --- thinking helpers (Kotlin companion object) -----------------------
    @staticmethod
    def parse_claude_version(model_id: str) -> Optional[tuple[int, int]]:
        """Kotlin ``parseClaudeVersion`` — (major, minor) or None."""
        lower = (model_id or "").lower()
        if "claude" not in lower:
            return None
        match = re.search(r"[-/]?(\d+)(?:[-.](\d+))?(?:$|[^0-9])", lower)
        if match is None:
            return None
        try:
            major = int(match.group(1))
        except ValueError:
            return None
        try:
            minor = int(match.group(2))
        except (TypeError, ValueError):
            minor = 0
        return major, minor

    @classmethod
    def model_rejects_temperature(cls, model_id: str) -> bool:
        """Claude >= 4.6 rejects the ``temperature`` parameter."""
        parsed = cls.parse_claude_version(model_id)
        if parsed is None:
            return False
        major, minor = parsed
        return major > 4 or (major == 4 and minor >= 6)

    @classmethod
    def model_uses_adaptive_thinking(cls, model_id: str) -> bool:
        """Claude 4.6+ uses adaptive thinking (``output_config.effort``)."""
        parsed = cls.parse_claude_version(model_id)
        if parsed is None:
            return False
        major, minor = parsed
        return major > 4 or (major == 4 and minor >= 6)

    @classmethod
    def supports_thinking(cls, model_id: str) -> bool:
        """[T-android-claude-opus48-thinking-toggle] Claude 3.7+."""
        parsed = cls.parse_claude_version(model_id)
        if parsed is None:
            return False
        major, minor = parsed
        return major > 4 or major == 4 or (major == 3 and minor >= 7)

    @staticmethod
    def thinking_budget(max_tokens: int, level: ThinkingLevel) -> int:
        """Kotlin ``thinkingBudget`` — legacy (<=4.5) budget_tokens path."""
        if level == ThinkingLevel.OFF:
            cap = 0
        elif level == ThinkingLevel.LOW:
            cap = 8192
        elif level == ThinkingLevel.MEDIUM:
            cap = 32768
        elif level == ThinkingLevel.HIGH:
            cap = min(max_tokens, 65536)
        else:  # XHIGH / MAX / ULTRA take the full budget
            cap = max_tokens
        clamped = min(cap, max_tokens)
        # Anthropic requires budget_tokens STRICTLY LESS THAN max_tokens.
        return max_tokens - 1 if clamped >= max_tokens and max_tokens > 1 else clamped

    @staticmethod
    def thinking_effort(level: ThinkingLevel) -> str:
        """Kotlin ``thinkingEffort`` — adaptive effort strings (Claude 4.6+)."""
        if level in (ThinkingLevel.XHIGH, ThinkingLevel.MAX, ThinkingLevel.ULTRA):
            return "max"
        return {
            ThinkingLevel.OFF: "low",  # unreached: caller checks is_enabled
            ThinkingLevel.LOW: "low",
            ThinkingLevel.MEDIUM: "medium",
            ThinkingLevel.HIGH: "high",
        }.get(level, "low")

    # --- response parsing -------------------------------------------------
    @staticmethod
    def _parse_response(payload: dict) -> LLMResponse:
        text = "".join(
            block.get("text", "")
            for block in (payload.get("content") or [])
            if block.get("type") == "text"
        )
        stop_reason = payload.get("stop_reason") or None
        usage = payload.get("usage")
        return LLMResponse(
            text=text,
            stop_reason=stop_reason,
            usage=AnthropicProvider._parse_usage(usage) if usage else None,
        )

    @staticmethod
    def _parse_usage(usage: dict) -> LLMUsage:
        input_tokens = usage.get("input_tokens", 0)
        return LLMUsage(
            input_tokens=input_tokens,
            output_tokens=usage.get("output_tokens", 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens") or None,
            cache_read_input_tokens=usage.get("cache_read_input_tokens") or None,
            latest_context_tokens=input_tokens,
        )

    @staticmethod
    def _safe_json(text: str) -> dict:
        try:
            parsed = json.loads(text) if text.strip() else {}
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    # --- errors -----------------------------------------------------------
    @staticmethod
    def _map_http_error(status_code: int, body: str) -> LLMError:
        if status_code in (401, 403):
            return LLMError.InvalidApiKey()
        if status_code == 429:
            return LLMError.RateLimited()

        try:
            payload = json.loads(body)
            error = payload.get("error") or {}
            error_type = error.get("type", "error")
            message = f"[{error_type}] {error.get('message', body)}"
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
