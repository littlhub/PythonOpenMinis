"""Core LLM provider interface + stream-level helpers.

Ported from: src/android/app/src/main/java/com/openminis/app/provider/LLMProvider.kt
Original package: com.openminis.app.provider
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Optional

from openminis.core.logging import get_logger
from openminis.data.model import (
    AgentToolDefinition,
    LLMError,
    LLMMessage,
    LLMModel,
    LLMResponse,
    LLMStreamChunk,
    ThinkingLevel,
)

__all__ = ["LLMProvider", "fail_on_silent_empty_completion"]

logger = get_logger(__name__)


class LLMProvider(ABC):
    """Kotlin ``interface LLMProvider`` (LLMProvider.kt)."""

    name: str
    model: LLMModel

    # --- effective max output ceiling ------------------------------------
    def effective_max_output_tokens(self, model: LLMModel) -> int:
        """Effective max output tokens ceiling for the given model.

        Priority: model.maxOutputTokens > provider-level default.
        """
        return model.max_output_tokens or self.default_max_output_tokens

    @property
    def default_max_output_tokens(self) -> int:
        """Provider-level fallback when model.maxOutputTokens is unknown."""
        return 16_384

    @property
    def stream_text_is_monolithic(self) -> bool:
        """[T-android-tool-splits-reply-fix] True when streamed assistant text is
        one monolithic `content` string per response (OpenAI Chat Completions)."""
        return False

    # --- public entry ----------------------------------------------------
    async def send_message(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: Optional[float] = None,
        image_parts: list[LLMMessage.ImagePart] = None,
        tools: list[AgentToolDefinition] = None,
        thinking_level: ThinkingLevel = ThinkingLevel.OFF,
    ) -> LLMResponse:
        """[T-android-thinking-level-arch] PUBLIC entry — every caller invokes this.

        Clamps the requested thinking level to the current model's ceiling ONCE,
        then delegates to send_message_clamped.
        """
        return await self.send_message_clamped(
            messages, system_prompt, max_tokens, temperature,
            image_parts or [], tools or [], self.clamp_thinking_level(thinking_level),
        )

    def stream_message(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: Optional[float] = None,
        image_parts: list[LLMMessage.ImagePart] = None,
        tools: list[AgentToolDefinition] = None,
        thinking_level: ThinkingLevel = ThinkingLevel.OFF,
    ) -> AsyncIterator[LLMStreamChunk]:
        """See send_message — the clamped, provider-implemented counterpart.

        Kotlin ``Flow<LLMStreamChunk>`` → Python ``AsyncIterator[LLMStreamChunk]``.
        """
        return self.stream_message_clamped(
            messages, system_prompt, max_tokens, temperature,
            image_parts or [], tools or [], self.clamp_thinking_level(thinking_level),
        )

    # --- provider-implemented --------------------------------------------
    @abstractmethod
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
        """[T-android-thinking-level-arch] Provider implementations override THIS
        (not send_message). The level is already clamped — do NOT re-clamp."""
        raise NotImplementedError

    @abstractmethod
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
        """See send_message_clamped."""
        raise NotImplementedError

    # --- clamp -----------------------------------------------------------
    def clamp_thinking_level(self, level: ThinkingLevel) -> ThinkingLevel:
        """[T-android-thinking-level-arch] Cap a requested thinking level to the
        current model's ceiling by rank."""
        ceiling = self.model.catalog_max_thinking_level
        return ceiling if level.rank > ceiling.rank else level


async def fail_on_silent_empty_completion(
    chunks: AsyncIterator[LLMStreamChunk],
    provider_name: str,
) -> AsyncIterator[LLMStreamChunk]:
    """[T-android-empty-stream-retry] Detect a silently-truncated stream.

    Async generator mirroring the Kotlin Flow operator. Emits every chunk,
    then if the stream completed with no content AND no finish reason, raises
    LLMError.TransientError (treated as a transient upstream failure).
    """
    saw_content = False
    saw_finish_reason = False
    async for chunk in chunks:
        if isinstance(chunk, LLMStreamChunk.Text):
            if chunk.text:
                saw_content = True
        elif isinstance(chunk, LLMStreamChunk.ThinkingDelta):
            if chunk.text:
                saw_content = True
        elif isinstance(chunk, LLMStreamChunk.ReasoningContent):
            if chunk.content:
                saw_content = True
        elif isinstance(chunk, (
            LLMStreamChunk.ToolUseStart,
            LLMStreamChunk.ToolInputDelta,
            LLMStreamChunk.ToolCallComplete,
            LLMStreamChunk.MediaAttachment,
        )):
            saw_content = True
        elif isinstance(chunk, LLMStreamChunk.Finished):
            if chunk.stop_reason is not None:
                saw_finish_reason = True
        yield chunk
    if not saw_content and not saw_finish_reason:
        logger.warning(
            "%s: stream completed with no content and no finish reason — "
            "treating as transient upstream failure",
            provider_name,
        )
        raise LLMError.TransientError(
            "Server returned an empty response (connection dropped or upstream error)"
        )
