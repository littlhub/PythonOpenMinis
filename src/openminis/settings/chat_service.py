"""Assembly for real chat: turn stored settings into a working
(provider, AgentRuntime, options) trio the WebSocket handler can drive.

Kept separate from ``server.main`` so it can be unit-tested without HTTP.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..agent.agent_runtime import MAX_AGENT_TURNS, AgentRuntime, AgentRuntimeOptions
from ..data.model import LLMModel, ThinkingLevel
from ..data.model.agent_content_part import ToolUse  # noqa: F401  (re-exported for tests)
from ..provider.anthropic.anthropic_provider import AnthropicProvider
from ..provider.openai.openai_provider import OpenAIProvider
from ..settings.catalog import (
    ENGINE_READY,
    build_tool_registry,
    engine_for,
    lookup_model,
)
from ..core.logging import get_logger
from ..settings.store import SettingsStore
from ..soul import SystemPromptBuilder

logger = get_logger(__name__)

__all__ = [
    "ChatSetupError",
    "attribution_snapshot",
    "build_chat_setup",
    "identity_system_prompt",
]


def attribution_snapshot(store: SettingsStore, conf: dict[str, Any]) -> dict[str, Any]:
    """Freeze "which model produced this message" for the Usage page.

    Ported from the snapshot columns in
    ``com.openminis.app.data.db.MessageEntity`` (T-token-attribution-snapshot).

    Everything is captured before the request goes out, so later edits to the
    provider config cannot rewrite history. Lives here (not in ``server.main``)
    because both the chat handler and the scheduled runner persist turns and
    must write the same shape — a task that fires at 03:00 is exactly the kind
    of unattended usage the page needs to attribute correctly.
    """
    model_id = str(conf.get("model") or "").strip() or None
    known = lookup_model(model_id) if model_id else None
    instance_id = str(conf.get("id") or "").strip()
    if not instance_id:
        try:
            instance_id = str(store.load().get("activeProviderId") or "").strip()
        except Exception:  # pragma: no cover - settings unreadable
            instance_id = ""
    return {
        "model_id": model_id,
        "model_display_name": (known.display_name if known else model_id),
        "provider_type": str(conf.get("type") or "").strip() or None,
        "provider_instance_id": instance_id or None,
    }


class ChatSetupError(Exception):
    """Raised when chat cannot start (no provider/key, engine not ported…)."""


def active_skills_block(store: SettingsStore) -> str:
    """已激活技能的清单（渐进披露：只给名字 + 一句话）。

    全文不进 prompt —— 38 个技能的 SKILL.md 会把上下文撑爆。模型看到清单后
    用 ``skill_use`` 按需加载需要的那一个。
    """
    try:
        from pathlib import Path

        from ..skills import SkillStore

        active = store.active_skills()
        if not active:
            return ""
        lines: list[str] = []
        for entry in SkillStore().list():
            if entry.name in active or Path(entry.path).name in active:
                desc = (entry.description or "").strip().splitlines()
                head = desc[0] if desc else ""
                lines.append(f"- {entry.name}：{head}" if head else f"- {entry.name}")
        if not lines:
            return ""
        return (
            "\n\n## 可用技能\n"
            "以下技能已激活。当任务匹配某个技能时，先用 skill_use 工具加载它的完整"
            "说明（SKILL.md），再按说明用 shell_execute / file_* 去执行。"
            "技能不是工具，不要把技能名当工具名直接调用。\n"
            + "\n".join(lines)
        )
    except Exception:  # pragma: no cover - 技能目录损坏不该拖垮对话
        logger.debug("active skills block unavailable", exc_info=True)
        return ""


def identity_system_prompt(store: SettingsStore) -> str:
    identity = store.active_identity()
    return identity.persona + active_skills_block(store)


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


def build_provider(provider_id: str, conf: dict[str, Any]):  # noqa: ANN201
    """Instantiate the LLM provider for a stored provider config.

    ``provider_id`` is the instance id; the wire protocol (and thus the
    engine) comes from ``conf["type"]``, so several OpenAI-compatible
    instances each get an OpenAIProvider with their own base URL.
    Only engines present in :data:`ENGINE_READY` can actually run.
    """
    ptype = str(conf.get("type") or provider_id or "")
    engine = engine_for(ptype)
    if engine not in ENGINE_READY:
        raise ChatSetupError(f"厂商 {ptype} 的引擎尚未移植,暂不能对话")
    api_key = (conf.get("apiKey") or "").strip()
    if not api_key:
        raise ChatSetupError("尚未配置 API Key,请先在 设置 → 模型服务 中填写")
    model = _model_for(ptype, (conf.get("model") or "").strip())
    base_url = (conf.get("baseUrl") or "").strip()
    if engine == "anthropic":
        # DEFAULT_BASE_PATH is a module-level constant on the provider module,
        # not a class attribute — reference it through the module.
        from ..provider.anthropic import anthropic_provider as _anthropic_mod

        return AnthropicProvider(
            api_key=api_key,
            model=model,
            base_path=base_url or _anthropic_mod.DEFAULT_BASE_PATH,
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
    *,
    instance_id: str | None = None,
    model_id: str | None = None,
):
    """Return ``(provider, runtime, options, identity, provider_conf)`` for the
    active provider + identity, or raise :class:`ChatSetupError` with a
    user-facing message when chat is not configured yet.

    ``instance_id`` / ``model_id`` override the active config. Scheduled tasks
    need this: a task that pinned a model must keep using it no matter what the
    user later selects as active. Everything else (identity, tools, agent
    options) still comes from the current settings, so the run behaves like a
    normal chat with just the model swapped.
    """
    data = store.load()
    pid = instance_id or data.get("activeProviderId")
    if not pid:
        raise ChatSetupError(
            "还没有配置模型服务。请打开 设置 → 模型服务,添加厂商 API Key 并设为当前。"
        )
    conf = data["providers"].get(pid)
    if not conf:
        raise ChatSetupError(f"厂商 {pid} 配置不存在")
    if model_id:
        # Copy before overriding: ``conf`` is the live settings dict and a
        # mutation here would rewrite the user's stored model choice.
        conf = {**conf, "model": model_id}
    provider = build_provider(pid, conf)

    identity = store.active_identity()
    tools = build_tool_registry(identity.effective_tools())
    runtime = AgentRuntime(tools=tools, chunk_sink=chunk_sink)
    # Agent 对话参数(模型设置):执行步数上限 + 深度思考开关。缺省不深思考,
    # 步数上限用设置里的值(默认 40),保证单次对话不会无限跑工具。
    agent_cfg = store.agent_config()
    options = AgentRuntimeOptions(
        system_prompt=identity.persona + active_skills_block(store),
        max_turns=int(agent_cfg.get("maxToolSteps") or MAX_AGENT_TURNS),
        thinking_level=(
            ThinkingLevel.HIGH if agent_cfg.get("deepThinking") else ThinkingLevel.OFF
        ),
    )
    return provider, runtime, options, identity, conf
