"""Assembly for real chat: turn stored settings into a working
(provider, AgentRuntime, options) trio the WebSocket handler can drive.

Kept separate from ``server.main`` so it can be unit-tested without HTTP.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..agent.agent_runtime import MAX_AGENT_TURNS, AgentRuntime, AgentRuntimeOptions
from ..agent.repeat_guard import RepeatGuard
from ..agent.tool_loop_detector import ToolLoopDetector
from ..data.model import LLMModel, ThinkingLevel
from ..data.model.agent_content_part import ToolUse  # noqa: F401  (re-exported for tests)
from ..provider.anthropic.anthropic_provider import AnthropicProvider
from ..provider.fallback import FallbackCandidate, FallbackChainProvider
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
    "close_provider_cache",
    "current_time_block",
    "identity_system_prompt",
    "image_context_discipline",
    "reset_provider_cache",
    "reset_session_guards",
    "session_guards",
]


# ── 会话级循环防护 ──────────────────────────────────────────────────────────
# KT 在 ChatViewModel 上持有**一个** ToolLoopDetector，滑动窗口跨整段对话累积。
# Python 侧原先每次 run() 都新建实例 —— 等于每轮失忆，跨消息的「同一张图/同一
# 条命令又来了」永远看不见。这里按 session_id 缓存一对（KT 检测器 + 本项目
# 补充护栏），由 build_chat_setup(session_id=…) 注入到 AgentRuntimeOptions。
_SESSION_GUARDS: dict[str, tuple[ToolLoopDetector, RepeatGuard]] = {}
#: 简单的容量上限：会话被删/永不再用时不必显式清理（按插入序淘汰最旧的）。
_SESSION_GUARD_LIMIT = 256


def session_guards(session_id: str) -> tuple[ToolLoopDetector, RepeatGuard]:
    """Return the (detector, repeat_guard) pair owned by ``session_id``."""
    guards = _SESSION_GUARDS.get(session_id)
    if guards is None:
        if len(_SESSION_GUARDS) >= _SESSION_GUARD_LIMIT:
            _SESSION_GUARDS.pop(next(iter(_SESSION_GUARDS)))
        guards = (ToolLoopDetector(), RepeatGuard())
        _SESSION_GUARDS[session_id] = guards
    return guards


def reset_session_guards(session_id: str) -> None:
    """Drop a session's sliding window (session deleted / context cleared)."""
    _SESSION_GUARDS.pop(session_id, None)


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
    "④ 只有在确实找不到合适来源时，才退化为直接猜测 URL 去抓取；"
    "⑤ 检索词必须来自用户**本轮**的要求（或本轮图片里的内容）："
    "不要凭上下文惯性把上一轮的话题再搜一遍，也不要把新闻/财经门户首页"
    "当成默认抓取目标。"
    "\n【时效纪律】涉及「今天/今日/最新/最近」的内容时："
    "① web_search 必须传 time_range（今天=day、本周=week、本月=month），"
    "不传就默认不限时间、会捞到陈旧页面；"
    "② 引用前先核对页面日期：日期早于今天的，必须如实说明是「X月X日」的消息，"
    "**绝不能把昨天或更早的新闻说成今天发生**；"
    "③ 若检索结果里没有当天的新消息，就直接告诉用户「截至今天 X 日，"
    "暂未检索到当天的新消息，最近一条是 X 月 X 日的……」，不要拿旧闻凑数；"
    "④ 也不要凭训练数据里的旧信息编造今日新闻。"
)


def current_time_block() -> str:
    """把「现在是什么时候」写进系统提示。

    模型没有时钟：不告诉它今天几号，它就无法判断检索结果是不是「今天的」，
    会把昨天的新闻当成今日最新报给用户（用户实测踩过）。
    """
    from datetime import datetime

    now = datetime.now()
    weekday = "周一周二周三周四周五周六周日"[now.weekday() * 2: now.weekday() * 2 + 2]
    return (
        f"\n\n【当前时间】现在是 {now.strftime('%Y-%m-%d %H:%M')}（{weekday}，"
        "北京时间）。判断「今天/最新」时以这个时间为准；"
        "检索结果里日期早于今天的，都不是今天的新消息。"
    )


#: 生图纪律。两个实测故障各占一半：
#: ① 「只要一张图，模型却串行跑了 4 次脚本」→ 张数在**一次调用**里声明清楚；
#: ② 「图生成了但用户看不到」→ 产物必须用可渲染的路径交出去。
IMAGE_GEN_DISCIPLINE = (
    "\n\n【生成图片】动手前先判断**需要几张** —— 用户说几张就几张：说一张做一张、"
    "说两张做两张；用户没明确说数量就按 1 张。张数写在**这一次调用**里："
    "image_gen 工具用 `count` 参数（如 `count: 2`）、技能脚本用 `--count N`"
    "（如 `--count 2`）—— 运行时会在这一次调用里按声明的张数生成。"
    "**不要**用同一条或相近参数的命令反复重跑凑数（会被循环护栏拦下）；"
    "若确实要一批互不相同的内容，可以在一轮里给出多个互不依赖的调用（会并发执行）。"
    "生成成功后必须把产物交给用户：优先用 send 工具，或在回复里单独成行写 "
    "`![简短说明](图片的绝对路径)`。只写「已生成」而不给可渲染的路径，"
    "用户在界面上是看不到图的。"
    "\n【生图选哪个】默认用内置 image_gen 工具（走「生图」模型槽）。"
    "若设置了「生图走子代理」（agent.imageSubagent=开），image_gen 会自动"
    "委派给匹配到的生图子代理生成 —— 你照常调 image_gen 即可，**不要**再手动"
    "用 subagent_delegate 去派生图。"
    "只有用户明确提到魔搭 / modelscope（例如说「魔搭生图」）时，"
    "才改用 modelscope-image 技能：先用 skill_use 加载它的 SKILL.md，"
    "再按其说明用 shell_execute 执行。"
)

#: 并发工具纪律：一轮对话里的多个工具调用会**并发执行**，所以互不依赖的
#: 动作要一次给全（4 张图 = 1 轮并发，而不是 4 轮串行）。每多一轮就多一次
#: 模型往返（实测该接口首包 0.7–15s），这是省时间最直接的一条。
PARALLEL_TOOL_DISCIPLINE = (
    "\n\n【并行调用】一次回复里可以同时给出**多个互不依赖**的工具调用"
    "（运行时会把它们并发执行，总耗时只等于最慢的那一个）。"
    "因此：需要读多张图、查多个文件、搜多个关键词时，**在同一轮里一次给全**，"
    "不要一次只调一个、等结果再调下一个。"
    "仅有真正的前后依赖（后一个的输入来自前一个的输出）时才分轮。"
)

#: 工具失败的处置纪律：失败输出就是分析素材 —— 先读懂报错、修正、再重试；
#: 绝不许因为「不知道怎么回事」就跑去调用一堆无关工具（典型恶例：shell
#: 失败后疯狂 read_image 看图「找线索」，跟图毫无关系）。
TOOL_FAILURE_DISCIPLINE = (
    "\n\n【失败处置】工具执行失败时：① 通读失败输出里的命令、退出码与报错"
    "文本，判断原因（命令不存在/依赖缺失/路径错误/服务不可达）；"
    "② 针对原因修正后重试，最多 2 次；③ 仍失败就把**原始报错**如实报告"
    "用户并给出修复建议（比如需要安装什么依赖、配置什么环境）。"
    "**禁止**：不做分析就换不相干的工具乱试，尤其禁止用 read_image / "
    "subagent_delegate 看图来「找线索」—— 图片内容与命令失败毫无关系。"
)


def identity_system_prompt(store: SettingsStore, identity=None) -> str:  # noqa: ANN001
    """身份的系统提示。``identity`` 传了就用它，否则用「当前身份」。

    通道插件（QQ 等）可以指定「由哪个 agent 接待」，那条链路拿不到"当前身份"
    这个概念 —— 网页端把当前身份切走之后，机器人不该跟着变脸。
    """
    identity = identity or store.active_identity()
    try:
        subagent_on = bool(store.agent_config().get("subagentEnabled") or False)
    except Exception:  # pragma: no cover - settings unreadable
        subagent_on = True
    return (
        identity.persona
        + current_time_block()
        + RETRIEVAL_DISCIPLINE
        + image_context_discipline(store)
        + (SUBAGENT_PLAN_DISCIPLINE if subagent_on else "")
        + COMPLETION_JUDGMENT_DISCIPLINE
        + TOOL_FAILURE_DISCIPLINE
        + PARALLEL_TOOL_DISCIPLINE
        + IMAGE_GEN_DISCIPLINE
        + active_skills_block(store)
    )


#: 「以图为准」——用户实测踩过：上一轮在聊「今日新闻」，这一轮只发了一张
#: 时尚人像，主 agent 委派识图子代理拿到描述后，**接着去 web_fetch 新浪财经
#: 首页**，把上一轮的话题当成了本轮任务。任务写在图里，就不该由对话历史决定。
IMAGE_FOCUS_DISCIPLINE = (
    "\n【以图为准】收到图片时，本轮任务的**主语是图片内容**：看懂之后要围绕"
    "图里的具体事物（人物/品牌/文字/商品/地点/作品等）作答或检索相关信息。"
    "用户本轮没有额外文字要求时，默认就是「看懂这张图并说明/检索图中的内容」，"
    "**不要延续上一轮对话的话题**（上一轮聊过新闻 ≠ 这一轮还要搜新闻）；"
    "检索词必须来自图片内容或用户原话，也不要抓新闻/财经门户首页来凑数。"
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
) + IMAGE_FOCUS_DISCIPLINE

IMAGE_SUBAGENT_DISCIPLINE = (
    "\n\n【图片处理】图片不会直接把像素放进你的上下文，你只会拿到图片的"
    "**路径与尺寸**。需要看懂图片时，第一步先规划这一步做什么、交给谁，"
    "再用 subagent_delegate 委派给识图子代理（用子代理自己的模型看图），"
    "task 里原样带上图片路径，让它调用 read_image 并回报图像内容。"
    "若委派失败或没有识图子代理，退回 read_image；两者都无法读取时如实"
    "告诉用户，**不要凭路径猜测图片内容**。"
) + IMAGE_FOCUS_DISCIPLINE

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
    "**命令/脚本成功执行且产物已生成（图片已保存、文件已写出、消息已发送）"
    "即视为该步完成**——严禁再原样重跑同一条命令来「确认结果」，"
    "执行成功的输出本身就是确认。"
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


#: Provider 缓存：provider 内部持有 httpx 连接池，每轮对话新建一个就等于
#: 每次都重新做 DNS+TCP+TLS 握手（实测到 apihub 的冷连接 ~1.4s，热连接
#: ~0.85s/请求）。这里按「协议+地址+key+模型」指纹跨轮复用同一个 provider，
#: 连接池与 keep-alive 一起复用。
_PROVIDER_CACHE: dict[tuple, Any] = {}
_PROVIDER_CACHE_LIMIT = 8


def _provider_fingerprint(ptype: str, api_key: str, base_url: str,
                          model_id: str) -> tuple:
    import hashlib

    key_hash = hashlib.sha1(api_key.encode("utf-8")).hexdigest()[:12]
    return (ptype, base_url.rstrip("/"), key_hash, model_id)


def reset_provider_cache() -> None:
    """Drop the provider cache (settings changed / tests / shutdown)."""
    _PROVIDER_CACHE.clear()


async def close_provider_cache() -> None:
    """Close every cached provider's connection pool."""
    for provider in list(_PROVIDER_CACHE.values()):
        closer = getattr(provider, "aclose", None)
        if closer is None:
            continue
        try:
            await closer()
        except Exception:  # pragma: no cover - closing must never raise
            logger.debug("provider close failed", exc_info=True)
    _PROVIDER_CACHE.clear()


def _fallback_candidates(
    store: SettingsStore, agent_cfg: dict[str, Any], active_pid: str
) -> list[FallbackCandidate]:
    """把 ``agent.fallbackModels`` 解析成有序候选（跳过无效项与主模型本身）。

    配置形如 ``[{"instance": "<厂商实例 id>", "model": "<模型 id>"}, …]`` ——
    顺序即兜底顺序：主模型挂了先试第一个，再第二个…… 用户想加几个加几个。
    """
    raw = agent_cfg.get("fallbackModels") or []
    if not isinstance(raw, list):
        return []
    instances = {str(p.get("id") or ""): p for p in store.provider_instances()}
    data = store.load()
    active_conf = (data.get("providers") or {}).get(active_pid) or {}
    active_model = str(active_conf.get("model") or "").strip()
    out: list[FallbackCandidate] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("instance") or "").strip()
        mid = str(item.get("model") or "").strip()
        if not pid or not mid or (pid, mid) in seen:
            continue
        inst = instances.get(pid)
        if inst is None:
            continue  # 厂商实例已被删掉 —— 静默跳过，别让整轮对话起不来
        if pid == active_pid and mid == active_model:
            continue  # 与主模型完全一样，占一个兜底位没意义
        seen.add((pid, mid))
        label = f"{inst.get('label') or inst.get('name') or pid} / {mid}"
        out.append(FallbackCandidate(pid, mid, label))
    return out


def _apply_fallback_chain(
    store: SettingsStore,
    provider: Any,
    agent_cfg: dict[str, Any],
    active_pid: str,
    *,
    on_fallback: Callable[[str, str, str], Any] | None = None,
):
    """配了兜底模型就把 provider 包成链，没配就原样返回。"""
    candidates = _fallback_candidates(store, agent_cfg, active_pid)
    if not candidates:
        return provider
    instances = {str(p.get("id") or ""): p for p in store.provider_instances()}

    def build(cand: FallbackCandidate):  # noqa: ANN202
        conf = instances.get(cand.instance_id)
        if conf is None:
            raise ChatSetupError(f"兜底模型 {cand.label} 的厂商实例不存在")
        # 复用 build_provider 的连接池缓存；只换 model。
        return build_provider(cand.instance_id, {**conf, "model": cand.model_id})

    logger.info(
        "LLM fallback chain enabled: %s",
        " → ".join(c.label for c in candidates),
    )
    return FallbackChainProvider(provider, candidates, build, on_switch=on_fallback)


def build_provider(provider_id: str, conf: dict[str, Any]):  # noqa: ANN201
    """Instantiate (or reuse) the LLM provider for a stored provider config.

    ``provider_id`` is the instance id; the wire protocol (and thus the
    engine) comes from ``conf["type"]``, so several OpenAI-compatible
    instances each get an OpenAIProvider with their own base URL.
    Only engines present in :data:`ENGINE_READY` can actually run.

    Providers are cached by config fingerprint so the underlying HTTP
    connection pool survives across turns (see :data:`_PROVIDER_CACHE`).
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
    model_id = (model.id if model is not None else "") or ""
    fingerprint = _provider_fingerprint(engine, api_key, base_url, model_id)
    cached = _PROVIDER_CACHE.get(fingerprint)
    if cached is not None:
        return cached
    provider = _create_provider(engine, api_key, model, base_url, ptype)
    if len(_PROVIDER_CACHE) >= _PROVIDER_CACHE_LIMIT:
        _PROVIDER_CACHE.pop(next(iter(_PROVIDER_CACHE)))
    _PROVIDER_CACHE[fingerprint] = provider
    return provider


def _create_provider(engine: str, api_key: str, model: Any, base_url: str,
                     ptype: str):  # noqa: ANN202
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
    session_id: str | None = None,
    identity_id: str | None = None,
    on_fallback: Callable[[str, str, str], Any] | None = None,
):
    """Return ``(provider, runtime, options, identity, provider_conf)`` for the
    active provider + identity, or raise :class:`ChatSetupError` with a
    user-facing message when chat is not configured yet.

    ``instance_id`` / ``model_id`` override the active config. Scheduled tasks
    need this: a task that pinned a model must keep using it no matter what the
    user later selects as active. Everything else (identity, tools, agent
    options) still comes from the current settings, so the run behaves like a
    normal chat with just the model swapped.

    ``identity_id`` overrides the **identity** (人设 + 工具集). 通道插件用它把
    机器人的对话交给指定的 agent 接待（见 ``plugins.bridge``）；网页端不传，
    照旧跟着「当前身份」走。
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

    identity = (
        store.identity(identity_id) if identity_id else None
    ) or store.active_identity()
    # Agent 对话参数(模型设置):执行步数上限 + 深度思考 + 子代理助理开关。
    agent_cfg = store.agent_config()
    # 兜底模型链：主模型限流/超时/5xx 时按用户配的顺序自动切下一个。
    provider = _apply_fallback_chain(
        store, provider, agent_cfg, pid, on_fallback=on_fallback
    )
    enabled_ids = list(identity.effective_tools())
    # skill_use 是**能力开关**而不是身份工具：老 settings.json 里存的
    # enabled_tools 早于 skill_use 出现，缺了它「可用技能」清单就成了一句
    # 空话（模型按清单调 skill_use 只会收到 Unknown tool）。这里补齐。
    if "skill_use" not in enabled_ids:
        enabled_ids.append("skill_use")
    # send 同样是**能力开关**：agent 生成图片/报告后要把产物交付给用户，
    # 硬停收尾轮也只允许调它。老 settings.json 存的 enabled_tools 里没有它时
    # 必须补齐，否则「有产物但发不出去」。
    if "send" not in enabled_ids:
        enabled_ids.append("send")
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
    # 循环模式：'react'（默认，增强护栏）| 'kt'（KT 原版四策略）。
    loop_mode = str(agent_cfg.get("loopMode") or "react").strip().lower()
    if loop_mode not in ("react", "kt"):
        loop_mode = "react"
    # 会话级循环防护：检测器的滑动窗口要跨轮累积（KT 是每会话一个实例）。
    loop_detector, repeat_guard = (
        session_guards(session_id) if session_id else (None, None)
    )
    options = AgentRuntimeOptions(
        system_prompt=identity_system_prompt(store, identity),
        max_turns=int(agent_cfg.get("maxToolSteps") or MAX_AGENT_TURNS),
        thinking_level=(
            ThinkingLevel.HIGH if agent_cfg.get("deepThinking") else ThinkingLevel.OFF
        ),
        # 图片默认不进上下文（只留路径，看图交给识图槽/子代理）
        image_context_mode=image_mode,
        loop_mode=loop_mode,
        loop_detector=loop_detector,
        repeat_guard=repeat_guard,
        # [T-tool-cards-persist-and-fold] 工具输出进上下文的上限：最近几轮保持
        # 完整，更早的折成一行（工具卡在会话里照旧完整可展开，读的是落库原文）。
        # 0/0 = 全部保留（旧行为）。
        tool_keep_recent=int(agent_cfg.get("toolKeepRecent", 6) or 0),
        tool_output_max_chars=int(agent_cfg.get("toolOutputMaxChars", 8000) or 0),
    )
    return provider, runtime, options, identity, conf
