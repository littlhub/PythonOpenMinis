"""Assembly for real chat: turn stored settings into a working
(provider, AgentRuntime, options) trio the WebSocket handler can drive.

Kept separate from ``server.main`` so it can be unit-tested without HTTP.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..agent.agent_runtime import AgentRuntime, AgentRuntimeOptions
from ..data.model import LLMModel
from ..data.model.agent_content_part import ToolUse  # noqa: F401  (re-exported for tests)
from ..provider.anthropic.anthropic_provider import AnthropicProvider
from ..provider.openai.openai_provider import OpenAIProvider
from ..settings.catalog import (
    ENGINE_READY,
    build_tool_registry,
    engine_for,
    lookup_model,
)
from ..settings.store import SettingsStore
from ..soul import SystemPromptBuilder

__all__ = ["ChatSetupError", "build_chat_setup", "identity_system_prompt"]


class ChatSetupError(Exception):
    """Raised when chat cannot start (no provider/key, engine not ported…)."""


def identity_system_prompt(store: SettingsStore) -> str:
    identity = store.active_identity()
    return identity.persona


def _model_for(provider_type: str, model_id: str) -> LLMModel | None:
    """Resolve a stored model string to an ``LLMModel`` instance.

    Catalogued ids map to their rich model; anything else (custom model id or
    gateway model names) is wrapped into a minimal ``LLMModel`` so the request
    really uses the user-provided id instead of silently falling back. Empty
    string → ``None`` (caller picks an engine default).
    """
    if model_id:
        found = lookup_model(model_id)
        if found is not None:
            return found
        return LLMModel(id=model_id, display_name=model_id, provider=provider_type)
    return None


def build_provider(provider_type: str, conf: dict[str, Any]):  # noqa: ANN201
    """Instantiate the LLM provider for a stored provider config.

    Only engines present in :data:`ENGINE_READY` can actually run.
    """
    engine = engine_for(provider_type)
    if engine not in ENGINE_READY:
        raise ChatSetupError(f"厂商 {provider_type} 的引擎尚未移植,暂不能对话")
    api_key = (conf.get("apiKey") or "").strip()
    if not api_key:
        raise ChatSetupError("尚未配置 API Key,请先在 设置 → 模型服务 中填写")
    model = _model_for(provider_type, (conf.get("model") or "").strip())
    base_url = (conf.get("baseUrl") or "").strip()
    if engine == "anthropic":
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            base_path=base_url or AnthropicProvider.DEFAULT_BASE_PATH,
        )
    if engine == "openai":
        return OpenAIProvider(
            api_key=api_key,
            model=model,
            base_url=base_url or "https://api.openai.com/v1",
        )
    raise ChatSetupError(f"未知引擎: {engine}")


def build_chat_setup(  # noqa: ANN201
    store: SettingsStore,
    chunk_sink: Callable[..., Awaitable[None]] | None = None,
):
    """Return ``(provider, runtime, options, identity, provider_conf)`` for the
    active provider + identity, or raise :class:`ChatSetupError` with a
    user-facing message when chat is not configured yet."""
    data = store.load()
    pid = data.get("activeProviderId")
    if not pid:
        raise ChatSetupError(
            "还没有配置模型服务。请打开 设置 → 模型服务,添加厂商 API Key 并设为当前。"
        )
    conf = data["providers"].get(pid)
    if not conf:
        raise ChatSetupError(f"厂商 {pid} 配置不存在")
    provider = build_provider(pid, conf)

    identity = store.active_identity()
    tools = build_tool_registry(identity.effective_tools())
    runtime = AgentRuntime(tools=tools, chunk_sink=chunk_sink)
    options = AgentRuntimeOptions(system_prompt=identity.persona)
    return provider, runtime, options, identity, conf
