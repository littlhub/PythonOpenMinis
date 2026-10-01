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

import asyncio
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
from .repeat_guard import RepeatGuard
from .tool_loop_detector import LoopCheckResult, LoopLevel, ToolLoopDetector


def _session_workspace_dir(session_id: str) -> str:
    """会话工作区（``zcode`` 循环模式的根目录，与 ``file_*`` 工具同一套映射）。"""
    import os

    try:
        from ..core.context import app_context

        root = app_context().external_files_dir / str(session_id)
        root.mkdir(parents=True, exist_ok=True)
        return str(root)
    except Exception:  # pragma: no cover - 无 AppContext（CLI / 测试）时退回 cwd
        return os.getcwd()


def _guard_outbound(messages: list[LLMMessage], session_id: str) -> None:
    """出站敏感信息拦截：发给 LLM 前把明文凭据换成占位符/部分显示。

    覆盖「密码 / 密钥 / 令牌 / 私钥」中英文写法；命中会记一条沙箱拦截事件
    （沙箱页可见、可放行）。守卫本身出问题不能挡住对话。
    """
    try:
        from ..sandbox.guard import sanitize_message_parts

        sanitize_message_parts(messages, session_id=session_id)
    except Exception:  # pragma: no cover - 脱敏失败不该中断本轮
        logger.debug("outbound secret scan failed", exc_info=True)


def _prepare_outbound(
    messages: list[LLMMessage], session_id: str, opts: "AgentRuntimeOptions"
) -> None:
    """每次把 messages 交给 provider 之前的统一处理。

    两件事，顺序有讲究：

    1. **脱敏**（``_guard_outbound``）：凭据不能发出去。
    2. **工具输出收敛**（``fold_tool_outputs``）：只让最近几轮的输出保持完整，
       更早的折成一行。放在这里 —— 也就是「每一次真实请求之前」—— 而不是
       ``run()`` 开头，是因为一轮里可能连着好几次请求（工具轮、空转收尾轮、
       硬停总结轮），每一趟上下文都在变大。

    折叠只改 ``ToolResult.content``，块本身保留：``tool_use`` 少了配对的
    ``tool_result`` 会被 provider 判为非法请求。
    """
    _guard_outbound(messages, session_id)
    if not (opts.tool_keep_recent or opts.tool_output_max_chars):
        return
    try:
        from .tool_context import fold_tool_outputs

        fold_tool_outputs(
            messages,
            keep_recent=opts.tool_keep_recent,
            max_chars=opts.tool_output_max_chars,
        )
    except Exception:  # pragma: no cover - 收敛失败不该中断本轮
        logger.debug("tool output fold failed", exc_info=True)

logger = get_logger("agent.runtime")

__all__ = ["AgentRuntime", "AgentChunkSink", "AgentRuntimeOptions", "MAX_AGENT_TURNS"]


def _round_dedup_key(params: dict[str, Any]) -> str:
    """同轮去重用的参数指纹：忽略 ``tool_title``（纯展示字段）。"""
    payload = {k: v for k, v in (params or {}).items() if k != "tool_title"}
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - unusual arg shapes
        return repr(sorted(payload.items(), key=lambda kv: kv[0]))


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
    #: 图片如何进上下文。
    #: ``"path"``（默认）—— 图片**字节不进上下文**，工具结果里只留路径与元数据；
    #: 真正"看图"交给识图槽（返回文本描述）或子代理，上下文只承担文本开销。
    #: ``"inline"`` —— 把工具产出的图片字节作为 image_parts 附到下一次请求，
    #: 多模态主模型能直接看图，代价是 base64 长期吃上下文（原项目行为）。
    image_context_mode: str = "path"
    #: Kotlin's per-tool-category timeout defaults (seconds).
    tool_timeout: float = 900.0

    # A ``ToolLoopDetector`` shared across the whole session — pass one in to
    # keep the sliding window across runs. When None a fresh one is created.
    loop_detector: Optional[ToolLoopDetector] = None
    #: Project-specific companion guards (retrieval-family runaway /
    #: effect-tool repeat). Same lifetime rules as ``loop_detector``: one per
    #: session, injected by ``chat_service.session_guards``.
    repeat_guard: Optional[RepeatGuard] = None
    #: Agent 循环模式（设置 → Agent 对话参数 → 循环模式，聊天页也可切换）：
    #: ``"react"``（默认）—— 增强版：额外启用补充护栏（同参重复立刻拦、检索
    #:   家族空转、effect 一张就停）+ 空转自动收尾 + 连续全拦截硬停；
    #: ``"kt"``  —— 原版：只跑 KT ToolLoopDetector 的四条策略（10/20/30），
    #:   不做额外拦截与自动收尾，循环由模型自己停或 max_turns 用尽结束；
    #: ``"zcode"`` —— **整个循环换成 ZCode 编程内核**（专职编程）：它自带
    #:   loop / 工具链 / 系统提示 / 技能根，主智能体这轮的 registry、护栏与
    #:   技能库都不参与。见 :meth:`AgentRuntime._run_zcode_mode`。
    loop_mode: str = "react"
    #: [T-tool-cards-persist-and-fold] 工具输出进上下文的上限。
    #: ``tool_keep_recent`` —— 最近 N 条工具输出保持完整，更早的折成一行
    #:   （0 = 不折叠，全部保留；原来的行为）。
    #: ``tool_output_max_chars`` —— 单条输出的字符上限（0 = 不截断）。
    #: 工具卡本身在会话里照旧完整可展开（那读的是落库原文），这里管的只是
    #: 「喂给模型的那一份」。设置页 → 对话参数 → 工具输出进上下文。
    tool_keep_recent: int = 6
    tool_output_max_chars: int = 8000


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

    # ── zcode 循环模式 ─────────────────────────────────────────────────────
    async def _run_zcode_mode(
        self,
        provider: Any,
        messages: list[LLMMessage],
        session_id: str,
        opts: AgentRuntimeOptions,
    ) -> tuple[list[LLMMessage], Optional[str]]:
        """把整轮交给 ZCode 编程内核（``loopMode = "zcode"``）。

        这与 ``react`` / ``kt`` 的区别不是「换一套工具」，而是**整个循环换掉**：
        ZCode 自带 loop、工具链、系统提示与技能根，主智能体的 registry、循环
        护栏、技能库都不参与本轮。所以它是一个**专职编程**的会话模式 —— 选了
        它就按编程 agent 的方式干活，而不是主智能体换个工具清单。

        文本与工具结果仍走同一个 ``chunk_sink`` 出去，界面上的流式气泡和工具卡
        与 ReAct 模式没有区别；本轮结果的正文会作为一条 assistant 消息追加进
        ``messages``，这样后续轮次（以及切回 ReAct 时）看得到它说过什么。

        已知边界（不是 bug，是「换了个 agent」的必然结果）：

        * 只取最后一条 user 消息的**文本**作为任务 —— 历史轮次与图片附件不进
          ZCode 的上下文（它自带会话与提示词装配，不吃主智能体的消息数组）。
        * ``session_user_env``（会话级环境变量）不参与：ZCode 的 Bash 走自己的
          ``ExecutionPort``，环境来自工具请求里的 overlay。
        * 主智能体的循环护栏（``loop_detector`` / ``repeat_guard``）与工具输出
          折叠都不生效 —— 那两条都是 ReAct 循环的机制。
        """
        from .zcode.integration.agent import (
            ZCODE_DEFAULT_MAX_TURNS,
            ZcodeRunOptions,
            run_zcode_agent,
        )

        # ReAct 那边是「每次请求前」过一遍出站脱敏；这里换成整轮交给 ZCode，
        # 那个时机不存在了，所以显式补一次 —— 凭据不能因为换了循环就漏出去。
        _guard_outbound(messages, session_id)

        task = ""
        for message in reversed(messages):
            if getattr(message, "role", None) == LLMMessage.Role.USER:
                task = str(getattr(message, "content", "") or "")
                break
        if not task.strip():
            return messages, "no_input"

        workdir = _session_workspace_dir(session_id)
        options = ZcodeRunOptions(
            working_directory=workdir,
            workspace_root=workdir,
            # 轮次预算沿用宿主的 ``maxToolSteps``（与原实现一致，见 git 历史）。
            # 上游 ZCode 的 ``maxTurns`` 本来就是**宿主注入**的（
            # ``runtimeConfig.maxTurns``，见 script-workflow-child-runtime.ts），
            # 所以「由宿主决定轮次」才是与源码对齐的做法 —— 实测一个改飞机的
            # 任务真会用到 20 轮，写死 12 会把合法工作截断。
            max_turns=max(1, int(opts.max_turns or ZCODE_DEFAULT_MAX_TURNS)),
            # 输出预算**必须**沿用宿主的：原先这行漏了，ZCode 就退回自己的默认值
            # （8192，宿主是 16384）。写 20KB+ 文件时那一半预算会在中途被
            # ``finish_reason="length"`` 截断 —— 工具调用到不了终点、参数退化成
            # 空串，落库成 ``input: {}``，工具再抛 ``len(None)``，模型只能原地
            # 重试到轮次耗尽（用户报的「ZCode 又卡了」）。
            max_tokens=int(opts.max_tokens or 16_384),
            chunk_sink=self.chunk_sink,
            # 借来的宿主工具按**宿主会话**解析工作区，不是 ZCode 的内部会话 id。
            host_session_id=session_id,
            # 专职编程：只用它自己的工具链与技能根，不向主智能体借 ——
            # 借了就不再是「ZCode 模式」，而是普通的 ReAct 换了个循环。
            borrow_host_tools=False,
            borrow_host_skills=False,
        )
        result = await run_zcode_agent(
            provider, task, session_id=f"{session_id}:zcode", options=options
        )
        # 「本轮结束」不等于「活干完了」：模型若顺手写了 ``... &``（内核的 Bash 明确
        # 拒绝 ``run_in_background``，所以这些后台作业一定是没被正式授权的），命令
        # 立刻返回、进程却继续跑。它们不在任何 pending 里，``stop()`` 也碰不到，
        # 于是留在机器上烧 CPU（用户报的「ZCode 工作完没清理没用的 pid」）。
        # 一轮收口时按**宿主会话 id 前缀**匹配收掉（ZCode 侧真实 shell 键是
        # ``<宿主 id>:zcode``，子代理还会再挂后缀）。
        try:
            from ..tools.shell_execute_tool import get_coordinator

            await get_coordinator().reap_background_jobs(session_id)
        except Exception:  # pragma: no cover - 收尾绝不拖垮本轮结果
            logger.debug("zcode: reap background jobs failed", exc_info=True)
        local_only = False
        if result.error:
            text = f"(ZCode 运行失败：{result.error})"
            local_only = True
        elif (result.text or "").strip():
            text = result.text
        else:
            text = f"(ZCode 未产出文字，stop_reason={result.stop_reason})"
            local_only = True
        if local_only:
            # 这条文案是**本地拼的**，从没经过 chunk_sink —— 模型产的文字走的是流式
            # delta 帧，而它没有。不推这一下，用户在界面上看到的就是「助手」标签
            # 下面一片空白。落库那条路见下方 content_parts。
            await self._emit(LLMStreamChunk.Text(text=text))
        # 正文必须放进 ``content_parts``（``content`` 留空）—— 这不是风格问题。
        # 服务端落库读的是 ``messages[-1].content_parts`` 里的 ``Text``
        # （见 ``server/main.py`` 的 ``final_text``）：正文塞进 ``content`` 会让它
        # 恒为空串，加上 zcode 模式下 ``runs`` 也拿不到 ReAct 的工具记录，于是
        # ``if final_text.strip() or runs`` 为假 —— **整轮不写库**，前端刷新/切会话/
        # 重启后端之后 ZCode 的总结就没了（用户报的现象）。重建上下文
        # （``load_runtime_history``）同样只读 text part，所以多轮对话里它也会
        # 「忘记」自己上一轮说了什么。
        # 与 ReAct 分支（``content_parts=[Text(text_joined)]``）保持同一形态。
        messages.append(
            LLMMessage(LLMMessage.Role.ASSISTANT, "", content_parts=[Text(text)])
        )
        return messages, result.stop_reason or "end_turn"

    async def _execute_tool(
        self,
        tu: ToolUse,
        args_json: str,
        session_id: str,
        session_user_env: Optional[dict[str, str]],
    ) -> ToolExecutionResult:
        """Run one tool call, converting a crash into a failed result.

        被 ``asyncio.gather`` 并发调用：任何异常都必须在这里被吃掉，否则
        一个工具炸了会连带整轮结果一起丢。
        """
        executor = self.tools.get(tu.name)
        if executor is None:  # pragma: no cover - gate already filtered this
            return ToolExecutionResult(f"Unknown tool: {tu.name}", False,
                                       tool_title=tu.name)
        started = time.monotonic()
        # 记下「现在跑的是哪一次外层工具调用」—— 子代理（subagent_delegate）
        # 要靠它把自己挂到对应的工具卡上，前端才能把子代理气泡归到正确的位置。
        from .subagent_events import push_tool_use, reset_tool_use

        tool_use_token = push_tool_use(tu.id, tu.name)
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
        finally:
            reset_tool_use(tool_use_token)
        # 每个工具的耗时单独打点：整轮慢时能立刻分清是哪个工具（网络/模型）
        # 慢，而不是模型慢。
        logger.info("tool_ms name=%s ms=%s ok=%s",
                    tu.name, int((time.monotonic() - started) * 1000),
                    result.success)
        return result

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
        mode = (opts.loop_mode or "react").strip().lower()
        if mode == "zcode":
            # 整个循环换掉 —— 交给 ZCode 编程内核，本轮不跑 ReAct。
            return await self._run_zcode_mode(provider, messages, session_id, opts)
        # 循环模式：'kt' = 只用 KT 原版四策略检测器；'react'（默认）额外启用
        # 本项目补充护栏与空转自动收尾。
        react_mode = mode != "kt"
        detector = opts.loop_detector or ToolLoopDetector()
        # kt 模式只跳过**启发式拦截**（同参重复 / 家族兜底），产物级规则照样生效：
        # 生图「一张就停」与空转收尾都是产品行为，不是循环启发式。用户反馈开着
        # kt 时连出 12 张图、且一直不总结收尾。
        repeat_guard = opts.repeat_guard or RepeatGuard()
        if repeat_guard is not None:
            # 按轮计数的护栏在这里归零（「一张就停」），检测器的跨轮历史不动。
            repeat_guard.begin_turn()
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
        #: Rounds in which a **生图预算** block happened (cumulative, never reset).
        #: 这条必须独立于 ``blocked_rounds``：预算用完后模型若夹着别的工具一起调
        #: （实测 shell_execute 被拦 + skill_use 成功交替），原来的
        #: 「整轮全被拦」判据永远为假 → blocked_rounds 归零 → 「预算用完还在跑」
        #: 一直转到 max_turns。预算用完后的每次生图尝试都是纯空转，累计到 2 就收尾。
        image_blocked_rounds = 0
        #: Consecutive rounds that ran tools but wrote nothing to the user.
        #: The loop's ONLY natural exit is the model declining to call a tool;
        #: when it falls into an act-only rhythm (very common after a research
        #: step: "fetch → fetch → fetch…") that exit is never reached and the
        #: turn dies on the terse max_turns marker with no answer. After
        #: ``wrap_up_after`` such rounds we force a text-only wrap-up so the
        #: turn actually closes with a summary instead of spinning.
        stalled_rounds = 0
        # 空转收尾是**产品行为**（必须有总结），kt 模式下也保留 —— 用户反馈
        # kt 模式下「跑了十几轮还是没有总结」。
        wrap_up_after = (
            opts.wrap_up_rounds if opts.wrap_up_rounds is not None
            else max(6, min(12, opts.max_turns))
        )
        #: 上一轮工具返回的图片（read_image / 截图…）。工具输出里的图片字节
        #: 原先被 loop 直接丢弃，模型永远看不到图 —— 这里收集起来，在**下一次**
        #: 请求时作为 image_parts 附到末尾 user 消息上。有原生视觉的多模态模型
        #: 直接看图；没有视觉的模型由 provider 自动换成文本占位（不报错）。
        #: 只在紧接着的那一次请求里带一帧，避免把 base64 常驻进历史反复计费。
        pending_image_parts: list[LLMMessage.ImagePart] = []

        for turn in range(opts.max_turns):
            turn_start = len(messages)
            round_text: list[str] = []
            round_tool_uses: list[ToolUse] = []
            seen_finished = False
            turn_stop_reason: Optional[str] = None

            # ── 1. stream the turn ─────────────────────────────────────────
            llm_started = time.monotonic()
            try:
                _prepare_outbound(messages, session_id, opts)
                stream = provider.stream_message(
                    messages,
                    opts.system_prompt,
                    opts.max_tokens,
                    opts.temperature,
                    image_parts=pending_image_parts or None,
                    tools=tool_defs,
                    thinking_level=opts.thinking_level,
                )
                # 这一帧已交给本次请求（provider 在迭代时才组装请求体，所以
                # 是重新绑定而非原地清空，旧列表仍被引用）。
                pending_image_parts = []
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
                # 每轮的模型耗时单独打点：工具慢/模型慢能一眼分清
                # （上下文越大这一项越大，接不上时先看这里）。
                logger.info("agent turn=%s llm_ms=%s tools=%s text_chars=%s",
                            turn, int((time.monotonic() - llm_started) * 1000),
                            len(round_tool_uses), len("".join(round_text)))
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
            #: 本轮是否出现过「生图预算已用完」的拦截（product-level，与循环启发式不同）。
            image_blocked_any = False
            #: 同一轮内「同工具 + 完全同参」的重复调用：detector 的历史要等
            #: 执行后才写入，看不见同轮重复（模型一次吐两份一样的调用很常见），
            #: 这里用集合补上（用户要求「第一次重复就停」）。
            seen_in_round: set[tuple] = set()
            #: 通过门禁、待执行的调用。并发跑，结果按原顺序回填。
            runnable: list[tuple[ToolUse, str]] = []
            for tu in round_tool_uses:
                args_json = json.dumps(tu.input, ensure_ascii=False)
                # Loop-detector gate BEFORE execution. CRITICAL → surface the
                # message as a tool error and do NOT run the tool. Both the
                # ported KT detector and the project-specific companion guard
                # get a veto (see repeat_guard.py).
                gate = detector.check(tu.name, tu.input)
                if not gate.is_blocking and repeat_guard is not None:
                    # kt 模式只保留产物级规则（生图配额）；启发式拦截留给 react。
                    extra = (
                        repeat_guard.check(tu.name, tu.input)
                        if react_mode
                        else repeat_guard.check_image_budget(tu.name, tu.input)
                    )
                    if extra.is_blocking:
                        gate = extra
                dedup_key = (tu.name, _round_dedup_key(tu.input))
                if not gate.is_blocking and dedup_key in seen_in_round:
                    gate = LoopCheckResult(
                        LoopLevel.CRITICAL,
                        f"[LOOP BLOCKED] CRITICAL: 同一轮回复里 {tu.name} 用完全"
                        "相同的参数被调用了两次，本次调用已被拦截。重复调用只会"
                        "拿到重复结果，请直接引用已有结果继续或总结收尾。",
                    )
                seen_in_round.add(dedup_key)
                if gate.is_blocking:
                    blocked_any = True
                    if (gate.warning_key or "") == "imagebudget":
                        image_blocked_any = True
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

                if self.tools.get(tu.name) is None:
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
                    if repeat_guard is not None:
                        repeat_guard.record(tu.name, tu.input, None,
                                            error_message=msg, tool_call_id=tu.id)
                    tool_result_parts.append(ToolResult(
                        id=tu.id, name=tu.name, content=msg, is_error=True,
                    ))
                    await self._emit(LLMStreamChunk.ToolResult(
                        id=tu.id, name=tu.name, content=msg, is_error=True,
                    ))
                    continue

                runnable.append((tu, args_json))

            # ── 4b. run this round's tools (concurrently) ──────────────────
            # 一次回复里规划多个动作时，串行等每个工具跑完会把耗时直接相加
            # （4 张识图 = 4 个视觉请求排队）。这里并发执行、结果按原顺序回填，
            # 门禁与同轮去重已在上面按顺序判定过。
            round_results: list[ToolExecutionResult] = []
            if runnable:
                tools_started = time.monotonic()
                round_results = list(await asyncio.gather(*(
                    self._execute_tool(tu, args_json, session_id, session_user_env)
                    for tu, args_json in runnable
                )))
                if len(runnable) > 1:
                    logger.info(
                        "agent round=%s tools=%s ran concurrently in %s ms",
                        turn, len(runnable),
                        int((time.monotonic() - tools_started) * 1000),
                    )

            for (tu, args_json), result in zip(runnable, round_results):
                executed_any = True

                # Loop-detector record AFTER execution (may attach a warning).
                rec = detector.record(tu.name, tu.input, result.output,
                                      error_message=None if result.success
                                      else result.output,
                                      tool_call_id=tu.id)
                extra_rec = (
                    repeat_guard.record(tu.name, tu.input, result.output,
                                        error_message=None if result.success
                                        else result.output,
                                        tool_call_id=tu.id)
                    if repeat_guard is not None else None
                )
                out_text = result.output
                for warning in (rec, extra_rec):
                    if (warning is not None and warning.level == LoopLevel.WARNING
                            and warning.message):
                        out_text = f"{out_text}\n\n{warning.message}"

                # One-line run-trace entry so server logs keep an auditable
                # "which tool ran when" trail (name, success, output size).
                logger.info(
                    "tool_call session=%s name=%s ok=%s chars=%s",
                    session_id, tu.name, result.success, len(out_text or ""),
                )

                if not result.success and not result.output.startswith("[command timed out"):
                    out_text = out_text or "(failed with no output)"

                # 图片进上下文的方式由 ``image_context_mode`` 决定:
                #   path(默认) —— 只留路径/元数据,图片字节既不进历史也不进请求,
                #                 "看图"交给识图槽/子代理,上下文只承担文本;
                #   inline      —— 附到下一轮请求,prompt 多模态主模型直接读图。
                _inline_images = (opts.image_context_mode or "path").lower() == "inline"
                tool_result_parts.append(ToolResult(
                    id=tu.id, name=tu.name, content=out_text,
                    is_error=not result.success,
                    image_data=result.image_data if _inline_images else None,
                    image_mime_type=result.image_mime_type if _inline_images else None,
                    image_linux_path=result.image_linux_path if _inline_images else None,
                ))
                # Notify the UI chunk sink that this tool call has finished.
                await self._emit(LLMStreamChunk.ToolResult(
                    id=tu.id, name=tu.name, content=out_text,
                    is_error=not result.success,
                ))
                if _inline_images and result.success and result.image_data:
                    pending_image_parts.append(LLMMessage.ImagePart(
                        data=result.image_data,
                        mime_type=result.image_mime_type or "image/png",
                        linux_path=result.image_linux_path,
                    ))

            # ── 5. append the tool-result turn and continue ────────────────
            if tool_result_parts:
                messages.append(LLMMessage(
                    LLMMessage.Role.USER,
                    "",
                    content_parts=tool_result_parts,
                ))
                # 生图预算被拦：**独立累计、不归零**（见上面 image_blocked_rounds）。
                if image_blocked_any:
                    image_blocked_rounds += 1
                if (blocked_any and not executed_any) or image_blocked_any:
                    # 两种模式都要硬停：kt 下生图配额也会产生 blocked，
                    # 少了这段模型会「拦截→重试→再拦截」一路空转到 max_turns，
                    # 最后连总结都没有（用户反馈的「还是没有总结」）。
                    blocked_rounds += 1
                    if blocked_rounds >= 2 or image_blocked_rounds >= 2:
                        # Hard stop: the model kept calling the same tool even
                        # after a CRITICAL block. Two finishing passes:
                        # ① send-only round — deliver an already-generated
                        #    artefact (image/file) to the user if not yet sent;
                        # ② no-tools round — text summary so the turn closes
                        #    with an answer instead of a bare stop marker.
                        messages.append(LLMMessage(
                            LLMMessage.Role.USER,
                            "[loop protection] Repeated calls to the same tool are no longer allowed. "
                            "Do NOT repeat any generation/execution command. If the task "
                            "artefact (image/file) has been generated but not yet sent, "
                            "first call the send tool to deliver it; otherwise reply "
                            "directly with a text summary of what was accomplished.",
                        ))
                        summary: Optional[str] = None
                        finish_defs = self.tool_definitions(include={"send"}) or None
                        for pass_tools in (finish_defs, None):
                            wrap_text: list[str] = []
                            wrap_calls: list[ToolUse] = []
                            _prepare_outbound(messages, session_id, opts)
                            stream = provider.stream_message(
                                messages,
                                opts.system_prompt,
                                opts.max_tokens,
                                opts.temperature,
                                tools=pass_tools,
                                thinking_level=opts.thinking_level,
                            )
                            async for chunk in stream:
                                await self._emit(chunk)
                                if isinstance(chunk, LLMStreamChunk.Text):
                                    wrap_text.append(chunk.text)
                                elif isinstance(chunk, LLMStreamChunk.ToolCallComplete):
                                    wrap_calls.append(ToolUse(
                                        id=chunk.id or f"toolu_{time.time_ns()}",
                                        name=chunk.name,
                                        input=chunk.args or {},
                                    ))
                            wrap_joined = "".join(wrap_text).strip()
                            assistant_parts: list = []
                            if wrap_joined:
                                assistant_parts.append(Text(wrap_joined))
                            assistant_parts.extend(wrap_calls)
                            if not assistant_parts:
                                break
                            messages.append(LLMMessage(
                                LLMMessage.Role.ASSISTANT,
                                "",
                                content_parts=assistant_parts,
                            ))
                            if not wrap_calls:
                                summary = wrap_joined
                                break
                            # 产物投递：只执行 send，其余一律拒掉
                            finish_results: list[ToolResult] = []
                            for tu in wrap_calls:
                                if tu.name == "send" and tu.name in self.tools:
                                    try:
                                        result = await self.tools[tu.name].executor(
                                            json.dumps(tu.input, ensure_ascii=False),
                                            session_id,
                                            **({"env": session_user_env}
                                               if session_user_env else {}),
                                        )
                                    except Exception as exc:
                                        result = ToolExecutionResult(
                                            f"[tool error: {type(exc).__name__}] {exc}",
                                            False, tool_title=tu.name,
                                        )
                                    finish_results.append(ToolResult(
                                        id=tu.id, name=tu.name,
                                        content=result.output,
                                        is_error=not result.success,
                                    ))
                                else:
                                    finish_results.append(ToolResult(
                                        id=tu.id, name=tu.name,
                                        content="[loop protection] 收尾阶段只允许 send。",
                                        is_error=True,
                                    ))
                                await self._emit(LLMStreamChunk.ToolResult(
                                    id=tu.id, name=tu.name,
                                    content=finish_results[-1].content,
                                    is_error=finish_results[-1].is_error,
                                ))
                            messages.append(LLMMessage(
                                LLMMessage.Role.USER,
                                "",
                                content_parts=finish_results,
                            ))
                        if summary is None:
                            summary = (
                                "任务已收尾：重复的工具调用被拦截，"
                                "以上是已完成的产出。"
                            )
                        messages.append(LLMMessage(
                            LLMMessage.Role.ASSISTANT, summary,
                        ))
                        logger.warning(
                            "agent hard-stopped: blocked_rounds=%s "
                            "image_blocked_rounds=%s",
                            blocked_rounds, image_blocked_rounds,
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
                    _prepare_outbound(messages, session_id, opts)
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
        # 用户要求：最后一轮不要再甩一句生硬的 [stopped: reached the maximum
        # number of tool rounds …] 报错 —— 要让模型**用自己的话总结**（做完了
        # 什么、产物在哪、还差什么）。这里补一次「无工具」收尾轮；只有它彻底
        # 失败或空回时才退回原来那句停止标记。
        messages.append(LLMMessage(
            LLMMessage.Role.USER,
            f"[auto wrap-up] 本轮工具调用已达到上限（{opts.max_turns} 轮），"
            "**不要再调用任何工具**。请立即用文本把已经完成的工作与产物整理成"
            "总结回复给用户：生成的图片单独成行写 `![简短说明](图片绝对路径)`，"
            "写好的文件给出路径；如果任务还没做完，就直接说明还差哪一步、"
            "需要用户补充什么。",
        ))
        summary: Optional[str] = None
        try:
            _prepare_outbound(messages, session_id, opts)
            stream = provider.stream_message(
                messages,
                opts.system_prompt,
                opts.max_tokens,
                opts.temperature,
                tools=None,
                thinking_level=opts.thinking_level,
            )
            closing: list[str] = []
            async for chunk in stream:
                await self._emit(chunk)
                if isinstance(chunk, LLMStreamChunk.Text):
                    closing.append(chunk.text)
            summary = "".join(closing).strip() or None
        except Exception as exc:  # a failed wrap-up must not lose the turn
            logger.warning("max_turns wrap-up failed: %s: %s",
                           type(exc).__name__, exc)
        if summary is None:
            # 收尾轮彻底空回（连一句话都没有）才退回原来的停止标记。
            messages.append(LLMMessage(
                LLMMessage.Role.ASSISTANT,
                "",
                content_parts=[Text(
                    "\n\n[stopped: reached the maximum number of tool rounds "
                    f"({opts.max_turns}). Try narrowing the task or asking a "
                    "new question.]"
                )],
            ))
        else:
            messages.append(LLMMessage(LLMMessage.Role.ASSISTANT, summary))
        return messages, "max_turns"
