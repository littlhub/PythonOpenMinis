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
    "image_context_discipline",
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
        skill_store = SkillStore()
        lines: list[str] = []
        for entry in skill_store.list():
            if entry.name in active or Path(entry.path).name in active:
                desc = (entry.description or "").strip().splitlines()
                head = desc[0] if desc else ""
                lines.append(
                    f"- {entry.name}：{head}（SKILL.md：{entry.path}）"
                    if head
                    else f"- {entry.name}（SKILL.md：{entry.path}）"
                )
        if not lines:
            return ""
        return (
            "\n\n## 可用技能\n"
            "以下技能已激活。当任务匹配某个技能时，先用 skill_use 工具加载它的完整"
            "说明（SKILL.md），再按说明用 shell_execute / file_* 去执行。"
            "技能不是工具，不要把技能名当工具名直接调用。技能目录可读（ls / "
            "search_files / file_read 都能访问），路径见各行标注，**不要凭空猜测"
            "技能文件位置**。\n"
            + "\n".join(lines)
            + "\n\n【技能失败降级】若某个技能执行失败（依赖未安装、脚本缺失、"
            "环境未配置、外部服务不可达），**不要反复重试、也不要猜别的路径**："
            "① 如实告诉用户该技能缺什么、需要在技能目录/配置里补什么；"
            "② 图片生成类任务此时改用 image_gen 工具（走「生图」模型槽），"
            "图片理解类任务改用 read_image（走「识图」模型槽），同样能完成任务。"
        )
    except Exception:  # pragma: no cover - 技能目录损坏不该拖垮对话
        logger.debug("active skills block unavailable", exc_info=True)
        return ""


#: Appended to every identity's persona. A short, locale-aware retrieval
#: discipline so the agent reaches for real retrieval before hallucinating
#: URLs — the "news task that fetched Google News / BBC / CNN and never
#: searched a domestic source" regression. The order matters: search first,
#: fetch next, guess only as a last resort, and prefer sources matching the
#: user's language/region.
RETRIEVAL_DISCIPLINE = (
    "\n\n【联网检索纪律】需要联网获取信息时，按以下顺序："
    "① 先用 web_search 检索（若它提示未配置，就跳过它继续下一步）；"
    "② 再用 web_fetch 抓取上一步得到的链接，或你确知的、"
    "与用户语言/地区一致的来源（中文用户优先国内权威来源）；"
    "③ 不要凭记忆直接猜 Google News、BBC、CNN 这类国外新闻站首页；"
    "④ 只有在确实找不到合适来源时，才退化为直接猜测 URL 去抓取。"
)


def identity_system_prompt(store: SettingsStore) -> str:
    identity = store.active_identity()
    try:
        subagent_on = bool(store.agent_config().get("subagentEnabled") or False)
    except Exception:  # pragma: no cover - settings unreadable
        subagent_on = True
    return (
        identity.persona
        + RETRIEVAL_DISCIPLINE
        + image_context_discipline(store)
        + (SUBAGENT_PLAN_DISCIPLINE if subagent_on else "")
        + COMPLETION_JUDGMENT_DISCIPLINE
        + active_skills_block(store)
    )


#: 图片进上下文只有一种方式：**只传路径**。理解走哪条路由
#: ``agent.imageVisionSubagent`` 开关决定：
#:
#: * 关（默认）—— read_image → 识图槽文字描述，与其它工具同一条路；
#: * 开 —— 第一步先规划，再用 subagent_delegate 委派识图子代理
#:   （用子代理自己的模型看图）。
IMAGE_SLOT_DISCIPLINE = (
    "\n\n【图片处理】图片不会直接把像素放进你的上下文，你只会拿到图片的"
    "**路径与尺寸**。需要看懂图片时，用 read_image 工具（会把图片交给"
    "「识图」模型，返回文字描述）。若提示无法读取，就如实告诉用户去"
    " 设置 → 模型服务 → 用途分槽 配置「识图」模型，或请用户用文字描述图片，"
    "**不要凭路径猜测图片内容**。"
)

IMAGE_SUBAGENT_DISCIPLINE = (
    "\n\n【图片处理】图片不会直接把像素放进你的上下文，你只会拿到图片的"
    "**路径与尺寸**。需要看懂图片时，第一步先规划这一步做什么、交给谁，"
    "再用 subagent_delegate 委派给识图子代理（用子代理自己的模型看图），"
    "task 里原样带上图片路径，让它调用 read_image 并回报图像内容。"
    "若委派失败或没有识图子代理，退回 read_image；两者都无法读取时如实"
    "告诉用户，**不要凭路径猜测图片内容**。"
)

IMAGE_INLINE_DISCIPLINE = (
    "\n\n【图片处理】图片可能直接随请求提供（多模态）。若只拿到路径而没有图片，"
    "用 read_image 获取内容。"
)


def image_context_discipline(store: SettingsStore) -> str:
    """按 ``agent.imageContextMode`` 与 ``agent.imageVisionSubagent`` 选图片纪律。"""
    try:
        mode = str(store.agent_config().get("imageContextMode") or "path").lower()
    except Exception:  # pragma: no cover - settings unreadable
        mode = "path"
    if mode == "inline":
        return IMAGE_INLINE_DISCIPLINE
    try:
        via_subagent = bool(store.agent_config().get("imageVisionSubagent") or False)
    except Exception:  # pragma: no cover
        via_subagent = False
    return IMAGE_SUBAGENT_DISCIPLINE if via_subagent else IMAGE_SLOT_DISCIPLINE


#: 子代理助理开启时的协作纪律：接手多步任务时**第一步先规划代办**，再把
#: 合适的条目分配给子代理执行 —— 不一股脑全委派，也不全部自己扛。
SUBAGENT_PLAN_DISCIPLINE = (
    "\n\n【子代理协作】子代理助理已开启。接手超过一步的任务时，第一步先"
    "**规划代办清单**：把目标拆成可验证的条目，逐一判断哪些适合委派给哪个"
    "子代理（技能/工具对口的才派），哪些自己直接做；委派时用 subagent_delegate，"
    "task 写清目标、边界与所需上下文。执行中按清单逐项推进并交代进展，"
    "不要把所有事都推给子代理，也不要明明对口却全部自己扛。"
)

#: 完成判断 —— 每次工具/子代理结果回来，先自检「是否已经能回答」，能答就
#: 立即收尾。背景：实测同一张图成功识图后模型仍连打 4 次 read_image 不作答。
COMPLETION_JUDGMENT_DISCIPLINE = (
    "\n\n【完成判断】每拿到一次工具或子代理的结果，先停下来自检："
    "用户的目标是否已经达成？已有信息是否足以直接回答？**足以回答就立即"
    "作答收尾**，不要再调用任何工具。禁止：对同一路径重复调用 read_image、"
    "重复委派同一个子代理、在结果已经完整的情况下继续加调工具。"
    "只有发现确有缺口（缺哪张图/哪份文件/哪个数据）才发起下一次调用，"
    "并在一句话里说明还缺什么。"
)


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
    # Agent 对话参数(模型设置):执行步数上限 + 深度思考 + 子代理助理开关。
    agent_cfg = store.agent_config()
    enabled_ids = list(identity.effective_tools())
    if agent_cfg.get("subagentEnabled", True):
        # 子代理委派是**能力开关**而不是身份工具：用户存的 enabled_tools 可能
        # 早于 subagent_delegate 出现（老 settings.json），照搬会让主 agent
        # 根本没有委派工具，而「识图交给子代理」这条链就断了。这里补齐。
        if "subagent_delegate" not in enabled_ids:
            enabled_ids.append("subagent_delegate")
    else:
        # 关闭子代理助理：subagent_delegate 从 schema 与执行器里一并移除，
        # 主模型连尝试的机会都没有（与 memory 开关同一思路）。
        enabled_ids = [t for t in enabled_ids if t != "subagent_delegate"]
    tools = build_tool_registry(enabled_ids)
    runtime = AgentRuntime(tools=tools, chunk_sink=chunk_sink)
    image_mode = str(agent_cfg.get("imageContextMode") or "path")
    options = AgentRuntimeOptions(
        system_prompt=identity_system_prompt(store),
        max_turns=int(agent_cfg.get("maxToolSteps") or MAX_AGENT_TURNS),
        thinking_level=(
            ThinkingLevel.HIGH if agent_cfg.get("deepThinking") else ThinkingLevel.OFF
        ),
        # 图片默认不进上下文（只留路径，看图交给识图槽/子代理）
        image_context_mode=image_mode,
    )
    return provider, runtime, options, identity, conf
