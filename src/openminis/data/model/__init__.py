# PORT-STATUS: partial
# Dependency port for the `provider` module. Ports the subset of
# com.openminis.app.data.model that `provider` (and `providers`) import.
# Mirrors the original Kotlin files: LLMMessage.kt, LLMModel.kt,
# LLMStreamChunk.kt, AgentToolDefinition.kt, LLMError.kt, LLMUsage.kt,
# ProviderConfig.kt.

"""App data model — provider-relevant subset.

Ported from: src/android/app/src/main/java/com/openminis/app/data/model/*.kt
Original package: com.openminis.app.data.model
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

__all__ = [
    "LLMMessage",
    "LLMResponse",
    "LLMMediaAttachment",
    "LLMStreamChunk",
    "AgentToolDefinition",
    "AgentToolParam",
    "LLMError",
    "LLMUsage",
    "LLMModel",
    "ProviderType",
    "ProviderCredential",
    "ThinkingLevel",
    "ProviderInstance",
    "ModelOverrides",
    "ModelEntry",
    "normalize_modality_name",
    "normalize_modalities",
]


# ---------------------------------------------------------------------------
# Helpers (mirror LLMModel.kt top-level extension functions)
# ---------------------------------------------------------------------------

def normalize_modality_name(s: str) -> str:
    """Kotlin: ``String.normalizeModalityName()``.

    [T-android-modality-normalize-case-order] Lowercase FIRST, then strip.
    """
    return s.lower().removesuffix("_input").removesuffix("_output")


def normalize_modalities(mods: Optional[list[str]]) -> Optional[list[str]]:
    """Kotlin: ``List<String>?.normalizeModalities()``."""
    if mods is None:
        return None
    out = [m.normalize_modality_name() for m in mods]
    out = list(dict.fromkeys(out))  # distinct, keep order
    return out if out else None


# ---------------------------------------------------------------------------
# LLMMessage / LLMResponse / LLMMediaAttachment
# ---------------------------------------------------------------------------

class LLMMessage:
    """Ported from LLMMessage.kt.

    Kotlin data class → Python dataclass. ByteArray → bytes, Role → nested Enum.
    """

    class Role(str, Enum):
        USER = "user"
        ASSISTANT = "assistant"

    @dataclass(slots=True)
    class ImagePart:
        data: bytes
        mime_type: str = "image/png"
        # iSH-visible linux path the bytes were originally persisted to, if any.
        linux_path: Optional[str] = None
        # [T-android-vision-group / GH#182] Text a provider substitutes for the
        # pixels when the target model has NO native image input.
        no_vision_placeholder: Optional[str] = None

    @dataclass(slots=True)
    class AudioPart:
        """Mirrors iOS ``LLMMessage.AudioAttachment`` (GH#67)."""
        format: str  # e.g. "wav", "mp3" (OpenAI input_audio.format)
        base64_data: str  # base64-encoded audio bytes

    def __init__(
        self,
        role: "LLMMessage.Role",
        content: str,
        image_parts: list["LLMMessage.ImagePart"] | None = None,
        audio_parts: list["LLMMessage.AudioPart"] | None = None,
        content_parts: list | None = None,
        db_message_id: Optional[str] = None,
        reasoning_content: Optional[str] = None,
    ) -> None:
        self.role = role
        self.content = content
        self.image_parts = image_parts or []
        self.audio_parts = audio_parts or []
        self.content_parts = content_parts or []
        self.db_message_id = db_message_id
        self.reasoning_content = reasoning_content


class LLMMediaAttachment:
    """Mirrors iOS LLMMediaAttachment (LLMTypes.swift)."""

    class MediaType(str, Enum):
        IMAGE = "image"
        AUDIO = "audio"
        VIDEO = "video"

    def __init__(
        self,
        type: "LLMMediaAttachment.MediaType",
        mime_type: str,
        data: bytes,
    ) -> None:
        self.type = type
        self.mime_type = mime_type
        self.data = data


@dataclass(slots=True)
class LLMResponse:
    """Ported from LLMMessage.kt ``data class LLMResponse``."""
    text: str
    stop_reason: Optional[str]
    usage: Optional["LLMUsage"] = None
    media_attachments: list[LLMMediaAttachment] = field(default_factory=list)


# ---------------------------------------------------------------------------
# LLMStreamChunk (sealed class → ABC)
# ---------------------------------------------------------------------------

class LLMStreamChunk:
    """Sealed class LLMStreamChunk (LLMStreamChunk.kt)."""


@dataclass(slots=True)
class _Started(LLMStreamChunk):
    pass


LLMStreamChunk.Started = _Started()  # type: ignore[attr-defined]


@dataclass(slots=True)
class _Text(LLMStreamChunk):
    text: str


LLMStreamChunk.Text = _Text  # type: ignore[attr-defined]


@dataclass(slots=True)
class _Usage(LLMStreamChunk):
    usage: "LLMUsage"


LLMStreamChunk.Usage = _Usage  # type: ignore[attr-defined]


@dataclass(slots=True)
class _Finished(LLMStreamChunk):
    stop_reason: Optional[str]


LLMStreamChunk.Finished = _Finished  # type: ignore[attr-defined]


@dataclass(slots=True)
class _ThinkingDelta(LLMStreamChunk):
    """Thinking/reasoning streaming event."""
    text: str


LLMStreamChunk.ThinkingDelta = _ThinkingDelta  # type: ignore[attr-defined]


@dataclass(slots=True)
class _ReasoningContent(LLMStreamChunk):
    """Opaque accumulated reasoning content (DeepSeek/Kimi/QwQ)."""
    content: str


LLMStreamChunk.ReasoningContent = _ReasoningContent  # type: ignore[attr-defined]


@dataclass(slots=True)
class _ToolUseStart(LLMStreamChunk):
    id: str
    name: str


LLMStreamChunk.ToolUseStart = _ToolUseStart  # type: ignore[attr-defined]


@dataclass(slots=True)
class _ToolInputDelta(LLMStreamChunk):
    id: str
    accumulated: str


LLMStreamChunk.ToolInputDelta = _ToolInputDelta  # type: ignore[attr-defined]


@dataclass(slots=True)
class _ToolCallComplete(LLMStreamChunk):
    id: str
    name: str
    # [T-android-gemini3-thoughtsig / #179] thoughtSignature replayed on
    # historical functionCall. Null for every other provider.
    args: dict
    thought_signature: Optional[str] = None


LLMStreamChunk.ToolCallComplete = _ToolCallComplete  # type: ignore[attr-defined]


@dataclass(slots=True)
class _ToolResult(LLMStreamChunk):
    """Notifies the UI when a tool call the agent dispatched has finished
    executing. Carries the (possibly truncated) result text plus a success
    flag so the chat UI can collapse the card to its final state."""
    id: str
    name: str
    content: str
    is_error: bool


LLMStreamChunk.ToolResult = _ToolResult  # type: ignore[attr-defined]


@dataclass(slots=True)
class _MediaAttachment(LLMStreamChunk):
    """[T-codex-gpt-image2-oauth-android] model-generated media attachment."""
    attachment: LLMMediaAttachment


LLMStreamChunk.MediaAttachment = _MediaAttachment  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# AgentToolDefinition / AgentToolParam
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AgentToolParam:
    type: str
    description: str
    enum_values: Optional[list[str]] = None

    def to_json(self) -> dict:
        """Anthropic/OpenAI field schema entry."""
        out: dict = {"type": self.type, "description": self.description}
        if self.enum_values is not None:
            out["enum"] = list(self.enum_values)
        return out

    def to_gemini_json(self) -> dict:
        out = {"type": self.type.upper(), "description": self.description}
        if self.enum_values is not None:
            out["enum"] = list(self.enum_values)
        return out


@dataclass(slots=True)
class AgentToolDefinition:
    name: str
    description: str
    parameters: dict[str, AgentToolParam]
    required: list[str] = field(default_factory=list)
    property_ordering: Optional[list[str]] = None

    def to_anthropic_json(self) -> dict:
        """{name, description, input_schema: {type:object, properties, required}}."""
        props = {k: v.to_json() for k, v in self.parameters.items()}
        schema: dict = {"type": "object", "properties": props}
        if self.required:
            schema["required"] = list(self.required)
        return {"name": self.name, "description": self.description, "input_schema": schema}

    def to_gemini_json(self) -> dict:
        """{name, description, parameters: {type:OBJECT, properties, required}}."""
        props = {k: v.to_gemini_json() for k, v in self.parameters.items()}
        params: dict = {"type": "OBJECT", "properties": props}
        if self.required:
            params["required"] = list(self.required)
        if self.property_ordering is not None:
            params["propertyOrdering"] = list(self.property_ordering)
        return {"name": self.name, "description": self.description, "parameters": params}

    def to_openai_json(self) -> dict:
        """{type:function, function: {name, description, parameters: {type:object, ...}}}."""
        props = {k: v.to_json() for k, v in self.parameters.items()}
        params: dict = {"type": "object", "properties": props}
        if self.required:
            params["required"] = list(self.required)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": params},
        }


# ---------------------------------------------------------------------------
# LLMUsage
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LLMUsage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    # Total input token count reported by the API for this call.
    latest_context_tokens: int = 0


# ---------------------------------------------------------------------------
# LLMError (sealed class → Exception hierarchy)
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """sealed class LLMError (LLMError.kt)."""

    @property
    def is_network_error(self) -> bool:
        return isinstance(self, _NetworkError)

    @property
    def is_retryable(self) -> bool:
        return isinstance(self, _NetworkError) or isinstance(self, _TransientError)

    @property
    def is_fallbackable(self) -> bool:
        return (
            isinstance(self, _RateLimited)
            or isinstance(self, _InvalidApiKey)
            or isinstance(self, _ProviderError)
        )

    @property
    def fallback_reason(self) -> str:
        if isinstance(self, _RateLimited):
            return "Rate limited"
        if isinstance(self, _InvalidApiKey):
            return "Invalid API key"
        if isinstance(self, _ProviderError):
            return "Provider error"
        if isinstance(self, _TransientError):
            return "Transient error"
        if isinstance(self, _NetworkError):
            return "Network error"
        if isinstance(self, _DecodingError):
            return "Decoding error"
        if isinstance(self, _Cancelled):
            return "Cancelled"
        return "Unknown error"


class _InvalidApiKey(LLMError):
    def __init__(self, detail: str = "") -> None:
        super().__init__("Invalid API key" if not detail else f"Invalid API key: {detail}")


class _NetworkError(LLMError):
    def __init__(self, cause: Exception) -> None:
        super().__init__(f"Network error: {cause}", cause)


class _ProviderError(LLMError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"Provider error: {detail}")


class _DecodingError(LLMError):
    def __init__(self, cause: Exception) -> None:
        super().__init__(f"Decoding error: {cause}", cause)


class _RateLimited(LLMError):
    def __init__(self) -> None:
        super().__init__("Rate limited — please try again later")


class _TransientError(LLMError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"Transient error: {detail}")


class _Cancelled(LLMError):
    def __init__(self) -> None:
        super().__init__("Request was cancelled")


class _Unknown(LLMError):
    def __init__(self, cause: Optional[Exception]) -> None:
        super().__init__(f"Unknown error: {cause.message if cause else None}", cause)


# Convenience aliases so ported call sites read naturally.
LLMError.InvalidApiKey = _InvalidApiKey  # type: ignore[attr-defined]
LLMError.NetworkError = _NetworkError  # type: ignore[attr-defined]
LLMError.ProviderError = _ProviderError  # type: ignore[attr-defined]
LLMError.DecodingError = _DecodingError  # type: ignore[attr-defined]
LLMError.RateLimited = _RateLimited  # type: ignore[attr-defined]
LLMError.TransientError = _TransientError  # type: ignore[attr-defined]
LLMError.Cancelled = _Cancelled  # type: ignore[attr-defined]
LLMError.Unknown = _Unknown  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# ProviderType / ProviderCredential / ThinkingLevel
# ---------------------------------------------------------------------------

class ProviderType(str, Enum):
    anthropic = "Anthropic"
    gemini = "Google Gemini"
    openAI = "OpenAI"
    openRouter = "OpenRouter"
    xAI = "xAI (Grok)"
    # [T-kimi-oauth] Kimi Code (Coding Plan) — RFC 8628 device-code OAuth.
    kimiCode = "Kimi Code"
    # [T-android-provider-type-parity] decode-only / unsupported types.
    openAIResponses = "Responses API (v3)"
    antigravity = "Antigravity"
    unsupported = "Unsupported"

    @property
    def is_usable(self) -> bool:
        return self in (
            ProviderType.anthropic,
            ProviderType.gemini,
            ProviderType.openAI,
            ProviderType.openRouter,
            ProviderType.xAI,
            ProviderType.kimiCode,
            ProviderType.openAIResponses,
        )

    @property
    def built_in_models(self) -> list["LLMModel"]:
        from openminis.data.model import LLMModel  # local import to avoid cycle

        return {
            ProviderType.anthropic: LLMModel.all_anthropic,
            ProviderType.gemini: LLMModel.all_gemini,
            ProviderType.openAI: LLMModel.all_openai,
            ProviderType.openRouter: LLMModel.all_openrouter,
            ProviderType.xAI: LLMModel.all_xai,
            ProviderType.kimiCode: LLMModel.all_kimi,
        }.get(self, [])

    @staticmethod
    def decoded(raw: str) -> "ProviderType":
        """[T-android-provider-type-parity] Never throws; unknown → unsupported."""
        try:
            return ProviderType(raw)
        except ValueError:
            return ProviderType.unsupported


class ProviderCredential(str, Enum):
    apiKey = "apiKey"
    oauth = "oauth"


class ThinkingLevel(Enum):
    """[T-android-thinking-level-arch] New cases MUST be appended at the end.

    rank follows declaration order (matches Kotlin ``ordinal``).
    """
    OFF = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    XHIGH = 4
    MAX = 5
    ULTRA = 6

    @property
    def is_enabled(self) -> bool:
        return self != ThinkingLevel.OFF

    @property
    def display_name(self) -> str:
        return {
            ThinkingLevel.OFF: "Off",
            ThinkingLevel.LOW: "Low",
            ThinkingLevel.MEDIUM: "Medium",
            ThinkingLevel.HIGH: "High",
            ThinkingLevel.XHIGH: "XHigh",
            ThinkingLevel.MAX: "Max",
            ThinkingLevel.ULTRA: "Ultra",
        }[self]

    @property
    def rank(self) -> int:
        return self.value

    @staticmethod
    def decoded(raw: str) -> "ThinkingLevel":
        """Safe deserialization fallback; unknown → XHIGH."""
        try:
            return ThinkingLevel[raw]
        except KeyError:
            return ThinkingLevel.XHIGH


# ---------------------------------------------------------------------------
# LLMModel
# ---------------------------------------------------------------------------

# PORT: the original Kotlin `companion object` constants are assigned onto the
# class after definition. `slots=True` installs member descriptors that reject
# that (dataclass would raise "object attribute is read-only"), so this class
# opts out of __slots__. Hashing is by identity, same as Kotlin's data class
# default for a companion-held singleton set.
@dataclass
class LLMModel:
    id: str
    display_name: str
    provider: str
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_reasoning: Optional[bool] = None
    interleaved_reasoning_field: Optional[str] = None
    reasoning_effort_values: Optional[list[str]] = None
    declares_no_effort_tiers: Optional[bool] = None
    input_modalities: Optional[list[str]] = None
    output_modalities: Optional[list[str]] = None

    # --- properties ------------------------------------------------------
    @property
    def is_text_output(self) -> bool:
        """[T-newchat-default-model-fallback-android]."""
        out = normalize_modalities(self.output_modalities)
        if out is None:
            return True
        return "text" in out

    @property
    def context_window_tokens(self) -> int:
        """Effective context window in tokens; falls back to id heuristic."""
        if self.context_window is not None and self.context_window > 0:
            return self.context_window
        lid = self.id.lower()
        if "claude" in lid:
            if "haiku" in lid:
                return 200_000
            if "claude-2" in lid or "claude-3" in lid:
                return 200_000
            return 1_000_000
        if "gemini" in lid:
            if "1.0" in lid:
                return 32_000
            return 1_000_000
        if "gpt-3.5" in lid:
            return 16_000
        if "gpt-4o" in lid or "gpt-4-turbo" in lid:
            return 128_000
        if "gpt-5" in lid:
            return 400_000
        if "gpt-4" in lid:
            return 8_000
        if "o3" in lid or "o4" in lid:
            return 200_000
        if "codex" in lid:
            return 200_000
        if "deepseek" in lid:
            return 128_000
        if "grok" in lid:
            if "grok-2" in lid or "grok-3" in lid:
                return 131_072
            return 256_000
        return 128_000

    def capability_prompt_fragment(self) -> Optional[str]:
        inputs = normalize_modalities(self.input_modalities) or []
        has_image = "image" in inputs
        has_pdf = "pdf" in inputs
        has_audio = "audio" in inputs
        has_video = "video" in inputs
        if has_image and has_pdf and has_audio and has_video:
            return None
        natives = [n for n, present in (("images", has_image), ("PDFs", has_pdf),
                                         ("audio", has_audio), ("video", has_video)) if present]
        missing = [n for n, present in (("images", has_image), ("PDFs", has_pdf),
                                         ("audio", has_audio), ("video", has_video)) if not present]
        sb = []
        if natives:
            sb.append("You can natively process " + ", ".join(natives) + ". ")
        if missing:
            sb.append("You cannot natively process " + ", ".join(missing))
            sb.append(" — for those formats, call shell_execute with ffmpeg or similar tools to extract text/metadata first.")
        return " ".join(sb).strip() or None

    def agent_behavior_prompt_fragment(self) -> Optional[str]:
        id_lower = self.id.lower()
        provider_lower = self.provider.lower()
        if provider_lower == "google" or "gemini" in id_lower:
            return ("When you need to use a tool, invoke it via the function-calling mechanism directly. "
                    "Do not emit tool invocations as plain text — they will not be executed.")
        is_codex = "codex" in id_lower or re.search(r"gpt-5(?:\.\d+)?-codex", id_lower) is not None
        if is_codex:
            return ("Act autonomously: don't stop at analysis, don't ask for permission on reversible local actions, "
                    "and never announce \"I will use tool X\" without actually calling X. Keep iterating until the "
                    "task is fully complete.")
        return None

    @property
    def catalog_max_thinking_level(self) -> "ThinkingLevel":
        """[T-android-thinking-level-arch] Built-in-tier ceiling only."""
        if self.supports_reasoning is False:
            return ThinkingLevel.OFF
        for lvl in self.selectable_thinking_levels:
            return lvl  # lastOrNull in Kotlin == last; mapping is weakest->strongest
        from openminis.provider.thinking_level_catalog import ThinkingLevelCatalog  # local import
        return ThinkingLevelCatalog.declared_max_level(self.id) or ThinkingLevel.XHIGH

    @property
    def selectable_thinking_levels(self) -> list["ThinkingLevel"]:
        declared = self.reasoning_effort_values
        if not declared:
            return []
        mapping = [
            ("low", ThinkingLevel.LOW),
            ("medium", ThinkingLevel.MEDIUM),
            ("high", ThinkingLevel.HIGH),
            ("xhigh", ThinkingLevel.XHIGH),
            ("max", ThinkingLevel.MAX),
        ]
        s = {x.lower() for x in declared}
        return [lvl for key, lvl in mapping if key in s]

    # --- companion object ------------------------------------------------
    @staticmethod
    def model_display_name(from_id: str) -> str:
        """Heuristic display-name formatter for API model ids."""
        if not from_id or not from_id.strip():
            return from_id
        upper_tokens = {
            "gpt", "glm", "oss", "ai", "xl", "vl", "llm", "moe", "api",
            "hd", "sd", "rp", "sft", "rl", "dpo", "gguf", "fp16", "bf16", "int4", "int8",
        }
        brand_rewrites = {
            "openai": "OpenAI", "deepseek": "DeepSeek", "chatgpt": "ChatGPT",
            "llama": "Llama", "gemma": "Gemma", "phi": "Phi", "mistral": "Mistral",
            "mixtral": "Mixtral", "qwen": "Qwen", "yi": "Yi",
        }
        return " / ".join(
            "-".join(
                (brand_rewrites[token.lower()] if token.lower() in brand_rewrites
                 else token.upper() if token.lower() in upper_tokens
                 else (token if not token else token[0].upper() + token[1:]))
                for token in segment.split("-")
            )
            for segment in from_id.split("/")
        )

    # Anthropic
    claude_fable5: "LLMModel" = None  # type: ignore[assignment]
    claude_opus48: "LLMModel" = None  # type: ignore[assignment]
    claude_opus46: "LLMModel" = None  # type: ignore[assignment]
    claude_sonnet5: "LLMModel" = None  # type: ignore[assignment]
    claude_sonnet46: "LLMModel" = None  # type: ignore[assignment]
    claude_haiku45: "LLMModel" = None  # type: ignore[assignment]
    # Gemini
    gemini3_pro: "LLMModel" = None  # type: ignore[assignment]
    gemini3_flash: "LLMModel" = None  # type: ignore[assignment]
    gemini25_pro: "LLMModel" = None  # type: ignore[assignment]
    gemini25_flash: "LLMModel" = None  # type: ignore[assignment]
    gemini25_flash_lite: "LLMModel" = None  # type: ignore[assignment]
    # OpenAI
    gpt55: "LLMModel" = None  # type: ignore[assignment]
    gpt53_codex: "LLMModel" = None  # type: ignore[assignment]
    gpt52_codex: "LLMModel" = None  # type: ignore[assignment]
    gpt51_codex_max: "LLMModel" = None  # type: ignore[assignment]
    gpt52: "LLMModel" = None  # type: ignore[assignment]
    gpt4o: "LLMModel" = None  # type: ignore[assignment]
    gpt4o_mini: "LLMModel" = None  # type: ignore[assignment]
    o3: "LLMModel" = None  # type: ignore[assignment]
    o4_mini: "LLMModel" = None  # type: ignore[assignment]
    codex_mini: "LLMModel" = None  # type: ignore[assignment]
    # OpenRouter
    or_claude_sonnet4: "LLMModel" = None  # type: ignore[assignment]
    or_gemini25_flash: "LLMModel" = None  # type: ignore[assignment]
    or_gpt4o: "LLMModel" = None  # type: ignore[assignment]
    or_llama_maverick: "LLMModel" = None  # type: ignore[assignment]
    # xAI
    grok46: "LLMModel" = None  # type: ignore[assignment]
    grok45: "LLMModel" = None  # type: ignore[assignment]
    grok43: "LLMModel" = None  # type: ignore[assignment]
    grok420_reasoning: "LLMModel" = None  # type: ignore[assignment]
    grok420_non_reasoning: "LLMModel" = None  # type: ignore[assignment]
    grok420_multi_agent: "LLMModel" = None  # type: ignore[assignment]
    grok_build01: "LLMModel" = None  # type: ignore[assignment]
    grok3_mini: "LLMModel" = None  # type: ignore[assignment]
    grok3_mini_fast: "LLMModel" = None  # type: ignore[assignment]
    grok_composer25_fast: "LLMModel" = None  # type: ignore[assignment]
    grok4_fast: "LLMModel" = None  # type: ignore[assignment]
    grok4_fast_non_reasoning: "LLMModel" = None  # type: ignore[assignment]
    grok_code_fast1: "LLMModel" = None  # type: ignore[assignment]
    # Kimi
    kimi_k3: "LLMModel" = None  # type: ignore[assignment]
    kimi_k2: "LLMModel" = None  # type: ignore[assignment]


# Populate LLMModel companion constants (matches LLMModel.kt).
LLMModel.claude_fable5 = LLMModel("claude-fable-5", "Claude Fable 5", "Anthropic", context_window=1_000_000, max_output_tokens=128_000, supports_reasoning=True)
LLMModel.claude_opus48 = LLMModel("claude-opus-4-8", "Claude Opus 4.8", "Anthropic", context_window=1_000_000, max_output_tokens=128_000, supports_reasoning=True)
LLMModel.claude_opus46 = LLMModel("claude-opus-4-6", "Claude Opus 4.6", "Anthropic", context_window=1_000_000, max_output_tokens=128_000, supports_reasoning=True)
LLMModel.claude_sonnet5 = LLMModel("claude-sonnet-5", "Claude Sonnet 5", "Anthropic", context_window=1_000_000, max_output_tokens=64_000, supports_reasoning=True)
LLMModel.claude_sonnet46 = LLMModel("claude-sonnet-4-6", "Claude Sonnet 4.6", "Anthropic", context_window=1_000_000, max_output_tokens=64_000, supports_reasoning=True)
LLMModel.claude_haiku45 = LLMModel("claude-haiku-4-5", "Claude Haiku 4.5", "Anthropic", context_window=200_000, max_output_tokens=64_000, supports_reasoning=True)
LLMModel.all_anthropic = [LLMModel.claude_fable5, LLMModel.claude_opus48, LLMModel.claude_opus46, LLMModel.claude_sonnet5, LLMModel.claude_sonnet46, LLMModel.claude_haiku45]

LLMModel.gemini3_pro = LLMModel("gemini-3-pro-preview", "Gemini 3 Pro (Preview)", "Google")
LLMModel.gemini3_flash = LLMModel("gemini-3-flash-preview", "Gemini 3 Flash (Preview)", "Google")
LLMModel.gemini25_pro = LLMModel("gemini-2.5-pro", "Gemini 2.5 Pro", "Google")
LLMModel.gemini25_flash = LLMModel("gemini-2.5-flash", "Gemini 2.5 Flash", "Google")
LLMModel.gemini25_flash_lite = LLMModel("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", "Google")
LLMModel.all_gemini = [LLMModel.gemini3_pro, LLMModel.gemini3_flash, LLMModel.gemini25_pro, LLMModel.gemini25_flash, LLMModel.gemini25_flash_lite]

LLMModel.gpt55 = LLMModel("gpt-5.5", "GPT-5.5", "OpenAI", supports_reasoning=True)
LLMModel.gpt53_codex = LLMModel("gpt-5.3-codex", "GPT-5.3 Codex", "OpenAI", supports_reasoning=True)
LLMModel.gpt52_codex = LLMModel("gpt-5.2-codex", "GPT-5.2 Codex", "OpenAI", supports_reasoning=True)
LLMModel.gpt51_codex_max = LLMModel("gpt-5.1-codex-max", "GPT-5.1 Codex Max", "OpenAI", supports_reasoning=True)
LLMModel.gpt52 = LLMModel("gpt-5.2", "GPT-5.2", "OpenAI", supports_reasoning=True)
LLMModel.gpt4o = LLMModel("gpt-4o", "GPT-4o", "OpenAI")
LLMModel.gpt4o_mini = LLMModel("gpt-4o-mini", "GPT-4o Mini", "OpenAI")
LLMModel.o3 = LLMModel("o3", "o3", "OpenAI", supports_reasoning=True)
LLMModel.o4_mini = LLMModel("o4-mini", "o4 Mini", "OpenAI", supports_reasoning=True)
LLMModel.codex_mini = LLMModel("codex-mini-latest", "Codex Mini", "OpenAI", supports_reasoning=True)
LLMModel.all_openai = [LLMModel.gpt55, LLMModel.gpt53_codex, LLMModel.gpt52_codex, LLMModel.gpt51_codex_max, LLMModel.gpt52, LLMModel.gpt4o, LLMModel.gpt4o_mini, LLMModel.o3, LLMModel.o4_mini, LLMModel.codex_mini]

LLMModel.or_claude_sonnet4 = LLMModel("anthropic/claude-sonnet-4", "Claude Sonnet 4", "OpenRouter")
LLMModel.or_gemini25_flash = LLMModel("google/gemini-2.5-flash", "Gemini 2.5 Flash", "OpenRouter")
LLMModel.or_gpt4o = LLMModel("openai/gpt-4o", "GPT-4o", "OpenRouter")
LLMModel.or_llama_maverick = LLMModel("meta-llama/llama-4-maverick", "Llama 4 Maverick", "OpenRouter")
LLMModel.all_openrouter = [LLMModel.or_claude_sonnet4, LLMModel.or_gemini25_flash, LLMModel.or_gpt4o, LLMModel.or_llama_maverick]

LLMModel.grok46 = LLMModel("grok-4.6", "Grok 4.6", "xAI", supports_reasoning=True)
LLMModel.grok45 = LLMModel("grok-4.5", "Grok 4.5", "xAI", supports_reasoning=True)
LLMModel.grok43 = LLMModel("grok-4.3", "Grok 4.3", "xAI", supports_reasoning=True)
LLMModel.grok420_reasoning = LLMModel("grok-4.20-0309-reasoning", "Grok 4.20 Reasoning", "xAI", supports_reasoning=True)
LLMModel.grok420_non_reasoning = LLMModel("grok-4.20-0309-non-reasoning", "Grok 4.20", "xAI")
LLMModel.grok420_multi_agent = LLMModel("grok-4.20-multi-agent-0309", "Grok 4.20 Multi-Agent", "xAI", supports_reasoning=True)
LLMModel.grok_build01 = LLMModel("grok-build-0.1", "Grok Build 0.1", "xAI")
LLMModel.grok3_mini = LLMModel("grok-3-mini", "Grok 3 Mini", "xAI", supports_reasoning=True)
LLMModel.grok3_mini_fast = LLMModel("grok-3-mini-fast", "Grok 3 Mini Fast", "xAI", supports_reasoning=True)
LLMModel.grok_composer25_fast = LLMModel("grok-composer-2.5-fast", "Grok Composer 2.5 Fast", "xAI")
LLMModel.grok4_fast = LLMModel("grok-4-fast", "Grok 4 Fast", "xAI", supports_reasoning=True)
LLMModel.grok4_fast_non_reasoning = LLMModel("grok-4-fast-non-reasoning", "Grok 4 Fast (Non-Reasoning)", "xAI")
LLMModel.grok_code_fast1 = LLMModel("grok-code-fast-1", "Grok Code Fast 1", "xAI", supports_reasoning=True)
LLMModel.all_xai = [LLMModel.grok46, LLMModel.grok45, LLMModel.grok43, LLMModel.grok420_reasoning, LLMModel.grok420_non_reasoning, LLMModel.grok420_multi_agent, LLMModel.grok_build01, LLMModel.grok3_mini, LLMModel.grok3_mini_fast, LLMModel.grok_composer25_fast, LLMModel.grok4_fast, LLMModel.grok4_fast_non_reasoning, LLMModel.grok_code_fast1]

LLMModel.kimi_k3 = LLMModel("kimi-k3", "Kimi K3", "Kimi")
LLMModel.kimi_k2 = LLMModel("kimi-k2", "Kimi K2", "Kimi")
LLMModel.all_kimi = [LLMModel.kimi_k3, LLMModel.kimi_k2]

LLMModel.all_models = (LLMModel.all_anthropic + LLMModel.all_gemini + LLMModel.all_openai
                        + LLMModel.all_openrouter + LLMModel.all_xai + LLMModel.all_kimi)


# ---------------------------------------------------------------------------
# ModelOverrides / ProviderInstance / ModelEntry
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ModelOverrides:
    display_name: Optional[str] = None
    max_output_tokens: Optional[int] = None
    context_window: Optional[int] = None
    supports_reasoning: Optional[bool] = None
    input_modalities: Optional[list[str]] = None
    output_modalities: Optional[list[str]] = None
    max_thinking_level: Optional[ThinkingLevel] = None

    @property
    def is_empty(self) -> bool:
        return (
            self.display_name is None
            and self.max_output_tokens is None
            and self.context_window is None
            and self.supports_reasoning is None
            and self.input_modalities is None
            and self.output_modalities is None
            and self.max_thinking_level is None
        )


@dataclass(slots=True)
class ProviderInstance:
    id: str
    label: str
    provider_type: ProviderType
    credential_type: ProviderCredential
    is_enabled: bool = True
    created_at: int = 0  # epoch millis
    custom_base_url: Optional[str] = None
    append_v1_suffix: bool = True
    custom_user_agent: Optional[str] = None
    use_responses_api: bool = False
    image_endpoint_mode: str = "auto"  # PORT: ImageEndpointMode mirror
    image_endpoint_resolved: Optional[str] = None
    azure_mode: bool = False

    @property
    def effective_base_url(self) -> Optional[str]:
        """Returns the effective API base URL, applying v1 suffix if configured."""
        base = self.custom_base_url.rstrip("/") if self.custom_base_url else None
        if base is None:
            return None
        if self.append_v1_suffix and not base.endswith("/v1"):
            return base + "/v1"
        return base

    @property
    def allows_empty_api_key(self) -> bool:
        return (
            self.credential_type == ProviderCredential.apiKey
            and bool(self.custom_base_url)
            and (self.provider_type == ProviderType.openAI or self.provider_type == ProviderType.anthropic)
        )

    @property
    def supports_image_endpoint_setting(self) -> bool:
        return self.provider_type in (ProviderType.openAI, ProviderType.openRouter, ProviderType.xAI)

    @property
    def supports_azure_mode(self) -> bool:
        return self.provider_type == ProviderType.openAI and self.credential_type == ProviderCredential.apiKey

    @property
    def supports_custom_thinking_rules(self) -> bool:
        if self.provider_type in (ProviderType.anthropic, ProviderType.gemini):
            return False
        codex_oauth = self.credential_type == ProviderCredential.oauth and not self.custom_base_url
        return not self.use_responses_api and not codex_oauth


@dataclass(slots=True)
class ModelEntry:
    provider_instance_id: str
    base_model: LLMModel
    overrides: ModelOverrides = field(default_factory=ModelOverrides)
    is_custom: bool = False
    is_hidden: bool = False
    uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_modified_at: Optional[int] = None

    @property
    def id(self) -> str:
        return self.uuid

    @property
    def model(self) -> LLMModel:
        """Effective model as seen by the rest of the app."""
        o = self.overrides
        if o.is_empty:
            return self.base_model
        return LLMModel(
            id=self.base_model.id,
            display_name=o.display_name or self.base_model.display_name,
            provider=self.base_model.provider,
            context_window=o.context_window if o.context_window is not None else self.base_model.context_window,
            max_output_tokens=o.max_output_tokens if o.max_output_tokens is not None else self.base_model.max_output_tokens,
            supports_reasoning=o.supports_reasoning if o.supports_reasoning is not None else self.base_model.supports_reasoning,
            input_modalities=o.input_modalities if o.input_modalities is not None else self.base_model.input_modalities,
            output_modalities=o.output_modalities if o.output_modalities is not None else self.base_model.output_modalities,
        )

    @property
    def is_user_modified(self) -> bool:
        return self.is_custom or self.is_hidden or not self.overrides.is_empty

    @property
    def effective_max_thinking_level(self) -> "ThinkingLevel":
        if self.overrides.max_thinking_level is not None:
            return self.overrides.max_thinking_level
        return self.model.catalog_max_thinking_level
