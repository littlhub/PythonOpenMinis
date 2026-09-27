"""ZCode 独立 agent runner —— 自己的 loop / prompt / 工具链 / 技能。

与主智能体的关系
----------------
ZCode 是**外部 agent**：它的 tool、mcp、skill、loop、prompt 与主智能体都不一样，
并且**可以独立运行** —— 不经过 ``AgentRuntime``，不与主智能体共享工具注册表、
技能库或系统提示。

本模块把移植内核的零件接成一条真实可跑的循环：

- **loop**：这里自己实现「模型轮次 → 工具执行 → 结果回填」的循环（内核的
  ``ZCodeAgent.ask`` 仍是桩，只做状态机演示，不真调模型）。
- **prompt**：由内核自己的 context sections 拼装（CLI 前缀 + 身份守则 + 当前日期 +
  技能清单），不是主智能体的人设。
- **tools**：``integration.toolchain`` 登记的 ``zcode_*`` 工具，execute 走内核
  handler，输入输出**不经过**应用工具层；同时可以**借用**主智能体的工具
  （``resolve_tool_spec`` —— 借来的窗口沿用宿主原名，执行落回宿主注册表）。
- **skills**：``integration.skills`` 的 ZCode 技能根；打开借用时把宿主技能库也
  当作最低优先级的根（双向互借）。

可独立运行的判据就是本函数不依赖任何主智能体状态：给它 provider + 会话信息 +
一个宿主运行时，它就能跑完一轮任务。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional

from ....core.logging import get_logger
from .toolchain import (
    DEFAULT_ZCODE_TOOL_IDS,
    ZcodeHostRuntime,
    ZcodeToolSpec,
    build_agent_tool_definition,
    build_zcode_runtime,
    resolve_tool_spec,
    use_host_session,
    zcode_tool_spec,
)

__all__ = [
    "ZCODE_DEFAULT_MAX_TURNS",
    "ZcodeRunOptions",
    "ZcodeRunResult",
    "build_zcode_system_prompt",
    "run_zcode_agent",
    "run_zcode_external_agent",
    "format_zcode_tool_output",
]

#: 与主智能体的 ``maxToolSteps`` 语义不同：ZCode 自己的循环预算（内核默认 12 轮）。
ZCODE_DEFAULT_MAX_TURNS = 12
_MAX_TOOL_OUTPUT_CHARS = 60_000

logger = get_logger(__name__)


@dataclass(slots=True)
class ZcodeRunOptions:
    """一次 ZCode 运行的参数。"""

    working_directory: str
    workspace_root: Optional[str] = None
    system_prompt: Optional[str] = None
    tool_ids: Optional[list[str]] = None
    max_turns: int = ZCODE_DEFAULT_MAX_TURNS
    max_tokens: int = 8192
    #: 会话级 todo 存储（跨工具调用复用；同一会话多次运行应传同一个实例）。
    session_store: Any = None
    #: 过程事件回调（前端把子代理气泡画出来用）；None 表示静默运行。
    chunk_sink: Optional[Callable[[Any], Any]] = None
    abort_signal: Any = None
    #: 附加在系统提示后的片段（子代理配置里的 persona 追加区）。
    extra_instructions: str = ""
    #: **宿主**会话 id（借来的宿主工具据此解析工作区）。空则回落到 ``session_id``。
    host_session_id: Optional[str] = None
    #: 是否允许向主智能体**借**工具（``resolve_tool_spec`` 会为宿主工具建桥）。
    borrow_host_tools: bool = True
    #: 是否把主智能体的技能库也纳入技能发现（借技能）。
    borrow_host_skills: bool = True
    #: 借来的宿主技能**只取这几个名字**（``None`` = 整个宿主技能库）。
    borrowed_skill_names: Optional[list[str]] = None


@dataclass(slots=True)
class ZcodeRunResult:
    """一次 ZCode 运行的结果。"""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    turns: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


# ---------------------------------------------------------------------------
# System prompt —— ZCode 自己的
# ---------------------------------------------------------------------------
#: section builder 函数 → 定义它的**子模块名**。
#:
#: 不能用 ``getattr(context.sections, name)`` 取 —— 那个包的 ``__init__`` 刻意保持
#: **空 barrel**（子模块之间互相依赖，包级再导出会成导入环，见该文件注释）。所以包
#: 命名空间里根本没有这些名字，``getattr`` 一律 ``AttributeError``。
#:
#: 历史故障：原实现正是 ``getattr(package, name)`` + ``except: return ""``，于是
#: **每一段都静默降级成空串** —— 系统提示里只剩工具链清单，「你是谁」「工作目录在
#: 哪」全丢。模型于是按训练数据里最常见的 Linux 容器惯例自己编了个 ``/home/user``
#: 当工作目录，写文件的落点跑到了盘根（``C:\home\user\...``）。
_SECTION_MODULES: dict[str, str] = {
    "build_cli_prefix_section": "cli_prefix",
    "build_identity_section": "identity",
    "build_env_info_section": "env_info",
    "build_current_date_section": "current_date",
    "build_desktop_context_section": "desktop",
}


def _section_text(builder_name: str, *args: Any, **kwargs: Any) -> str:
    """取得某个内核 context section 的正文；失败即降级为空串。

    section 的构造失败**不该**上升为运行失败（提示词段落是增量价值，缺一段不该让
    整个 agent 起不来），所以仍然降级 —— 但**必须留痕**：``AttributeError`` 这类
    「装配错了」和「这段没内容」在调用方看来都是空串，没有日志就永远查不出来。
    """
    from importlib import import_module

    module_name = _SECTION_MODULES.get(builder_name)
    if module_name is None:
        logger.warning("zcode prompt: unknown section builder %r", builder_name)
        return ""
    try:
        module = import_module(f"..context.sections.{module_name}", __package__)
        builder = getattr(module, builder_name)
        section = builder(*args, **kwargs)
        # 部分 builder 返回 ``Optional[ContextSection]``（如 current_date / memory）：
        # 传 ``None`` 表示「这段这次不需要」，是正常路径而非错误。
        return str(getattr(section, "content", "") or "")
    except Exception:
        logger.warning(
            "zcode prompt: section %s failed to build", builder_name, exc_info=True
        )
        return ""


def _sandbox_cwd(working_directory: str) -> str:
    """工作目录 → 沙箱写法（模型眼里不该出现 ``C:\\Users\\…`` 这类机器路径）。"""
    text = str(working_directory or "")
    if not text:
        return text
    try:
        from ....tools.path_utils import to_sandbox_path

        return to_sandbox_path(text)
    except Exception:  # pragma: no cover - 路径工具缺席时退回原样
        return text


def _env_snapshot(working_directory: str) -> Any:
    """采集宿主环境快照喂给 ``build_env_info_section``。

    内核刻意不做 IO（``process.cwd()`` / ``process.env`` 都不碰），快照必须由宿主
    采集后注入 —— 这正是这一段此前一直缺席的原因。**它在提示里的作用不可替代**：
    模型只有知道真实工作目录，才不会去编 ``/home/user``。
    """
    from ..context.types import EnvInfo

    try:
        import platform as _platform

        os_version = _platform.release()
    except Exception:  # pragma: no cover
        os_version = ""
    return EnvInfo(
        cwd=_sandbox_cwd(working_directory),
        platform=sys.platform,
        # ZCode 的 Bash 工具走宿主的持久 shell（Git Bash），不是 ``COMSPEC`` 那个
        # cmd.exe —— 写明真实解释器，模型才不会按 cmd 语法拼命令。
        shell=os.environ.get("SHELL") or "bash",
        os_version=os_version,
        # 宿主不跑 node；该字段仅为结构对齐，渲染 ``_build_env_info_content`` 时并不使用。
        node_version="",
    )


#: 宿主路径约定 —— **内核没有这一段，是适配层补的**。
#:
#: 内核的 ``path_policy`` 是 Android 语义（``/var/minis/...`` 就是真实 Linux 绝对
#: 路径），而这里跑在 Windows 上、工作区挂在用户目录下。不把这条讲清楚，模型就会
#: 用容器世界观写路径：``/home/user`` / ``/root`` 会被当作盘根绝对路径落到
#: ``C:\home\user\...``（无权限）或 ``G:\home\user\...``（用户找不到）。
_HOST_PATH_GUIDANCE = "\n".join(
    [
        "# Path conventions (host adapter)",
        "The environment above is a desktop app on a real machine, **not** a Linux container.",
        "The working directory shown there is the sandbox notation of a real local folder.",
        "Do **not** invent container-style roots such as `/home/user`, `/root` or `/app` for "
        "files or commands — on this platform they resolve to the drive root and the write "
        "will fail.",
        "Use, in order of preference: (1) a relative path, resolved against the working "
        "directory; (2) the `/var/minis/workspace/...` sandbox notation. Files you create "
        "should live in the workspace so the user can actually find them.",
    ]
)


def _system_sections(working_directory: str) -> list[str]:
    """按顺序拼内核的 system 段落（+ 宿主路径约定）。空段自动跳过。"""
    blocks = [
        _section_text("build_cli_prefix_section"),
        _section_text("build_identity_section"),
        _section_text("build_env_info_section", _env_snapshot(working_directory)),
        _section_text("build_current_date_section", date.today().isoformat()),
        _HOST_PATH_GUIDANCE,
    ]
    return [block.strip() for block in blocks if block and block.strip()]


def _skills_block(
    working_directory: str,
    *,
    borrow_host_skills: bool = True,
    borrowed_skill_names: Optional[list[str]] = None,
) -> str:
    """技能清单段落：技能根里发现的技能，渲染成模型可读列表。

    与主智能体的技能清单**分开**（各自来源、各自作用域），这正是「skill 不一样」
    的落点：ZCode 只看到 ZCode 自己的技能 —— 外加（打开借用时）从宿主借来的那些，
    并如实标出来源，模型才会挑对。``borrowed_skill_names`` 让「借来的」这一半收窄
    到用户勾选的那几个（配置界面上的「借技能」就是往这里加名字）。
    """
    try:
        from .skills import discover_zcode_skills

        outcome = discover_zcode_skills(
            working_directory,
            borrow_host_skills=borrow_host_skills,
            host_skill_filter=(
                None if borrowed_skill_names is None else set(borrowed_skill_names)
            ),
        )
    except Exception:  # pragma: no cover
        return ""
    if not outcome.skills:
        return ""
    lines = [
        "## Available skills",
        "",
        "These skills come from this agent's own skill roots, plus any skills borrowed "
        "from the host agent (marked below). Load one with the `Skill` tool when it "
        "matches the task.",
        "",
    ]
    for skill in outcome.skills:
        detail = skill.when_to_use or skill.description or ""
        borrowed = " · borrowed from host" if skill.source == "bundled" else ""
        lines.append(f"- `{skill.name}` ({skill.scope}/{skill.source}{borrowed}): {detail}")
    return "\n".join(lines)


def build_zcode_system_prompt(
    *,
    working_directory: str,
    tool_names: Optional[list[str]] = None,
    extra_instructions: str = "",
    borrow_host_skills: bool = True,
    host_tool_names: Optional[list[str]] = None,
    borrowed_skill_names: Optional[list[str]] = None,
) -> str:
    """拼装 ZCode 的系统提示（内核 sections + 技能清单 + 工具链段）。"""
    parts: list[str] = _system_sections(working_directory)
    skills = _skills_block(
        working_directory,
        borrow_host_skills=borrow_host_skills,
        borrowed_skill_names=borrowed_skill_names,
    )
    if skills:
        parts.append(skills)
    if tool_names:
        parts.append(
            "## ZCode toolchain\n\n"
            "You are running on ZCode's own toolchain. These tools are **not** the host "
            "agent's tools and have different names and semantics:\n\n"
            + "\n".join(f"- `{name}`" for name in tool_names)
        )
    if host_tool_names:
        parts.append(
            "## Borrowed host tools\n\n"
            "These tools are **borrowed from the host agent** (same names and semantics as "
            "the host's own tools). Use them when they fit better than your own:\n\n"
            + "\n".join(f"- `{name}`" for name in host_tool_names)
        )
    if extra_instructions.strip():
        parts.append(extra_instructions.strip())
    if not parts:  # pragma: no cover - 全段降级时的最小可用提示
        parts.append(
            "You are a programming agent. Use the provided tools to inspect and "
            "modify the workspace, then report what you did."
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 工具结果 → 模型可读文本
# ---------------------------------------------------------------------------
def format_zcode_tool_output(output: Any) -> str:
    """把内核 handler 的返回值转成模型可读文本。

    内核 handler 的返回形态不统一（多为 str，Bash 是 camelCase dict，Todo* 是
    结构化对象）。这里按「模型要能读懂」的原则统一：

    - ``str`` → 原样
    - ``None`` → 空串（工具成功但无输出）
    - 含 ``stdout``/``stderr`` 的 dict → 按 shell 结果渲染（含退出码/状态）
    - 其它 dict/list → JSON（``ensure_ascii=False``，中文不转义）
    """
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        has_streams = "stdout" in output or "stderr" in output
        if has_streams:
            stdout = output.get("stdout") or ""
            stderr = output.get("stderr") or ""
            status = output.get("status") or ""
            exit_code = output.get("exitCode", output.get("exit_code"))
            lines: list[str] = []
            if stdout:
                lines.append(str(stdout))
            if stderr:
                lines.append(f"[stderr]\n{stderr}")
            tail: list[str] = []
            if status and status not in ("completed",):
                tail.append(f"status: {status}")
            if exit_code not in (None, 0):
                tail.append(f"exit code: {exit_code}")
            if output.get("noOutputExpected"):
                tail.append("(no output expected)")
            if tail:
                lines.append("(" + ", ".join(tail) + ")")
            return "\n".join(lines).strip() or "(no output)"
        return json.dumps(output, ensure_ascii=False, indent=1, default=str)
    if isinstance(output, (list, tuple)):
        return json.dumps(list(output), ensure_ascii=False, indent=1, default=str)
    return str(output)


def _clip(text: str, limit: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…(truncated, {len(text) - limit} more chars)"


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
async def run_zcode_agent(
    provider: Any,
    task: str,
    *,
    session_id: str,
    options: ZcodeRunOptions,
    turn_id: Optional[str] = None,
) -> ZcodeRunResult:
    """跑一轮 ZCode 任务直到模型不再调用工具。

    这是「独立运行」的入口：只依赖 ``provider``（模型）+ ``options``（会话与工作
    目录），不需要主智能体的 registry / 技能库 / 系统提示。
    """
    from ....data.model import LLMStreamChunk, LLMMessage

    tool_ids = list(options.tool_ids) if options.tool_ids else list(DEFAULT_ZCODE_TOOL_IDS)
    specs: list[ZcodeToolSpec] = []
    for tool_id in tool_ids:
        # 先看 ZCode 自带，再向宿主借 —— 「工具互借」的解析点。
        spec = resolve_tool_spec(tool_id) if options.borrow_host_tools else zcode_tool_spec(tool_id)
        if spec is not None:
            specs.append(spec)
    if not specs:
        return ZcodeRunResult(
            text="",
            error=(
                "ZCode 没有任何可用工具：核心工具链未注册，且配置里也没有可借用的宿主工具"
                "（检查 integration.toolchain）。"
            ),
        )

    # 端口按「选中工具的 needs 并集」拼装 —— 不用的端口不建，避免无谓的 shell 启动。
    need: list[str] = []
    for spec in specs:
        for key in spec.needs:
            if key not in need:
                need.append(key)
    runtime: ZcodeHostRuntime = build_zcode_runtime(
        session_id=session_id,
        working_directory=options.working_directory,
        workspace_root=options.workspace_root,
        need=tuple(need),
        session_store=options.session_store,
        borrow_host_skills=options.borrow_host_skills,
        borrowed_skill_names=options.borrowed_skill_names,
    )
    runtime.abort_signal = options.abort_signal

    definitions = [build_agent_tool_definition(spec) for spec in specs]
    by_id = {spec.id: spec for spec in specs}
    own_tools = [spec.id for spec in specs if spec.origin != "host"]
    borrowed_tools = [spec.id for spec in specs if spec.origin == "host"]
    system_prompt = options.system_prompt or build_zcode_system_prompt(
        working_directory=runtime.working_directory,
        tool_names=own_tools,
        extra_instructions=options.extra_instructions,
        borrow_host_skills=options.borrow_host_skills,
        host_tool_names=borrowed_tools,
        borrowed_skill_names=options.borrowed_skill_names,
    )

    messages: list[LLMMessage] = [LLMMessage(LLMMessage.Role.USER, task)]
    collected: list[dict[str, Any]] = []
    final_text = ""
    stop_reason = ""
    turns = 0

    # 借来的宿主工具要用**宿主会话**解析工作区（外部 agent 的内部会话 id 宿主不认识）。
    host_session = str(options.host_session_id or session_id or "")
    with use_host_session(host_session):
        turns, final_text, stop_reason = await _run_turns(
            provider=provider,
            by_id=by_id,
            definitions=definitions,
            runtime=runtime,
            system_prompt=system_prompt,
            messages=messages,
            collected=collected,
            options=options,
            turn_id=turn_id,
        )

    return ZcodeRunResult(
        text=final_text.strip(),
        tool_calls=collected,
        stop_reason=stop_reason,
        turns=turns,
    )


async def _run_turns(
    *,
    provider: Any,
    by_id: dict[str, ZcodeToolSpec],
    definitions: list[Any],
    runtime: ZcodeHostRuntime,
    system_prompt: str,
    messages: list[Any],
    collected: list[dict[str, Any]],
    options: ZcodeRunOptions,
    turn_id: Optional[str],
) -> tuple[int, str, str]:
    """模型轮次循环：发模型 → 收工具调用 → 执行 → 结果回填。

    抽出来只是为了把「宿主会话作用域」包在外层（见 ``use_host_session``）；
    语义与内联版本完全一致。
    """
    from ....data.model import LLMStreamChunk, LLMMessage
    from ....data.model.agent_content_part import Text as TextPart
    from ....data.model.agent_content_part import ToolResult as ToolResultPart
    from ....data.model.agent_content_part import ToolUse as ToolUsePart

    final_text = ""
    stop_reason = ""
    turns = 0

    for turn in range(1, max(1, int(options.max_turns)) + 1):
        turns = turn
        text_chunks: list[str] = []
        pending: list[tuple[str, str, dict[str, Any]]] = []
        round_stop = ""
        stream = provider.stream_message(
            messages, system_prompt, options.max_tokens, None, None, definitions
        )
        async for chunk in stream:
            # **先把事件原样转出去，再做本地记账** —— 与 ReAct 循环保持一致
            # （那边是「provider 吐什么就 emit 什么」：``AgentRuntime.run`` 里
            # ``await self._emit(chunk)`` 在类型分发之前）。
            #
            # 宿主靠 ``LLMStreamChunk.ToolCallComplete`` 画工具卡：服务端 sink 收到
            # 它就发 ``toolStart`` 帧，``ToolResult`` 再发 ``toolEnd`` 补结果。原先
            # 这里只转 ``Text``，工具调用事件被就地吞掉 —— 前端于是**永远看不到
            # 「工具调用」这一步**，只在最后凭空冒出一张没有名字/参数的孤儿结果卡。
            if options.chunk_sink is not None:
                await _maybe_await(options.chunk_sink, chunk)
            if isinstance(chunk, LLMStreamChunk.Text):
                text_chunks.append(chunk.text)
            elif isinstance(chunk, LLMStreamChunk.ToolCallComplete):
                pending.append((chunk.id, chunk.name, chunk.args or {}))
            elif isinstance(chunk, LLMStreamChunk.Finished):
                round_stop = str(chunk.stop_reason or "")

        turn_text = "".join(text_chunks)
        if turn_text.strip():
            final_text = turn_text

        assistant_parts: list[Any] = []
        if turn_text:
            assistant_parts.append(TextPart(text=turn_text))
        for call_id, name, args in pending:
            assistant_parts.append(ToolUsePart(id=call_id, name=name, input=args))
        if assistant_parts:
            messages.append(
                LLMMessage(LLMMessage.Role.ASSISTANT, turn_text, content_parts=assistant_parts)
            )

        if not pending:
            stop_reason = round_stop or "end_turn"
            break

        # 工具执行：同一轮的多个调用并发跑（内核 scheduler 的策略；这里的并发
        # 上限交给 asyncio，工具自身没有共享可变状态）。
        results = await asyncio.gather(
            *(
                _execute_tool_call(
                    spec=by_id.get(name),
                    call_id=call_id,
                    name=name,
                    args=args,
                    runtime=runtime,
                    turn_id=turn_id,
                    chunk_sink=options.chunk_sink,
                )
                for call_id, name, args in pending
            )
        )

        result_parts: list[Any] = []
        for (call_id, name, args), (output_text, is_error) in zip(pending, results):
            collected.append(
                {
                    "id": call_id,
                    "name": name,
                    "input": args,
                    "ok": not is_error,
                    "output": output_text,
                }
            )
            result_parts.append(
                ToolResultPart(id=call_id, name=name, content=output_text, is_error=is_error)
            )
        messages.append(LLMMessage(LLMMessage.Role.USER, "", content_parts=result_parts))
        stop_reason = round_stop or "tool_use"
    else:
        stop_reason = stop_reason or "max_turns"

    # ── 收尾轮：工具跑了，模型却一句面向用户的话都没留 ──────────────────────
    #
    # 两种成因都会走到这儿：① 轮次预算用尽（``for...else``），模型还在埋头调工具；
    # ② 模型最后一轮只调工具、不写正文，循环因 ``not pending`` 自然收口。
    #
    # ReAct 那边有「空转自动收尾」护栏兜这一类，而 ZCode 是**整个循环换掉**、
    # 没有那套机制 —— 于是界面上的「助手」下面是空的，只剩一排工具卡（用户报的
    # 「这里缺少总结」）。
    #
    # 补**一次**收尾轮：把工具列表清空，逼它写字（无处可调就只能产出正文）。
    # 只补一次、不递归 —— 它要是仍然不说话，由调用方落兜底文案。
    if not final_text.strip() and collected:
        messages.append(
            LLMMessage(
                LLMMessage.Role.USER,
                "工具已经执行完毕。请直接用简洁的中文向用户总结：你做了什么、"
                "结果如何、产出文件在哪里。不要再调用任何工具。",
            )
        )
        summary_chunks: list[str] = []
        try:
            summary_stream = provider.stream_message(
                messages, system_prompt, options.max_tokens, None, None, []
            )
            async for chunk in summary_stream:
                # 同样先转发 —— 这段收尾正文要能实时流到前端气泡里，否则用户
                # 要等落库后重新拉历史才看得见「总结」。
                if options.chunk_sink is not None:
                    await _maybe_await(options.chunk_sink, chunk)
                if isinstance(chunk, LLMStreamChunk.Text):
                    summary_chunks.append(chunk.text)
        except Exception:
            logger.warning("zcode: summary turn failed", exc_info=True)
        summary_text = "".join(summary_chunks).strip()
        if summary_text:
            final_text = summary_text
            stop_reason = "end_turn"
            messages.append(LLMMessage(LLMMessage.Role.ASSISTANT, "", content_parts=[TextPart(summary_text)]))

    return turns, final_text, stop_reason


async def _execute_tool_call(
    *,
    spec: Optional[ZcodeToolSpec],
    call_id: str,
    name: str,
    args: dict[str, Any],
    runtime: ZcodeHostRuntime,
    turn_id: Optional[str],
    chunk_sink: Optional[Callable[[Any], Any]],
) -> tuple[str, bool]:
    """执行一个工具调用；异常转成 ``is_error`` 结果而不是终止整轮。

    主智能体的循环是「工具炸了不能带走整轮」，这里保持同一约定 —— 模型看到错误
    文本后可以自己改正；抛出去则整轮白跑。
    """
    from ....data.model import LLMStreamChunk

    if spec is None:
        return f"[tool error] Unknown ZCode tool: {name}", True

    missing = [key for key in spec.needs if runtime.port(key) is None]
    if missing:
        return (
            f"[tool error] {name} 缺少宿主端口：{', '.join(missing)}",
            True,
        )

    context = runtime.make_context(tool_call_id=call_id, turn_id=turn_id)
    try:
        handler = getattr(spec.entry, "handler", None)
        if handler is None:
            return f"[tool error] {name} 没有 handler（内核未注册实现）", True
        output = await handler(dict(args or {}), context)
        text = _clip(format_zcode_tool_output(output))
        if chunk_sink is not None:
            await _maybe_await(
                chunk_sink,
                LLMStreamChunk.ToolResult(id=call_id, name=name, content=text, is_error=False),
            )
        return text, False
    except asyncio.CancelledError:  # pragma: no cover - 上层取消
        raise
    except Exception as error:  # noqa: BLE001
        message = _describe_tool_error(error)
        if chunk_sink is not None:
            await _maybe_await(
                chunk_sink,
                LLMStreamChunk.ToolResult(
                    id=call_id, name=name, content=message, is_error=True
                ),
            )
        return message, True


def _describe_tool_error(error: BaseException) -> str:
    """把内核业务错误翻译成模型能据以改正的一句话。

    内核的工具失败大多是**预期内的业务失败**（文件不存在、old_string 不匹配、
    配置缺失），带 ``code`` 字段；把这些当作异常堆栈丢给模型是没用的。
    """
    code = getattr(error, "code", None)
    label = type(error).__name__
    detail = str(error)
    if code:
        return f"[tool error: {code}] {detail}"
    return f"[tool error: {label}] {detail}"


async def _maybe_await(value: Any, arg: Any) -> None:
    """回调可能是同步或异步（内核与宿主两侧都出现过两种写法）。"""
    if value is None:
        return
    result = value(arg)
    if asyncio.iscoroutine(result):
        await result


# ---------------------------------------------------------------------------
# 外部 agent 协议的统一入口
# ---------------------------------------------------------------------------
async def run_zcode_external_agent(
    task: str,
    *,
    session_id: str,
    workspace: str,
    config: Optional[dict[str, Any]] = None,
    provider: Any = None,
) -> str:
    """ZCode 作为**外部 agent 描述符**被调用时的入口。

    签名与 ``openminis.agent.external_agents.runner`` 约定的统一契约一致：任何
    transport=internal 的描述符都实现这一条即可被调用并回传文本。ZCode 的内置
    描述符 ``external_agents/descriptors/zcode.yaml`` 指向本函数。

    ``config`` 是子代理配置：``tools`` 里既有 ``zcode_*``（走内核）也有宿主工具 id
    （走借用桥），两套并存；``skills`` 里除了它自带的，还可以有用户勾中的宿主技能。
    应用侧工具**不会**变成 ZCode 自己的工具 —— 它们以「借来的」身份出现（提示词里
    单独一段、执行落回宿主注册表与沙箱）。
    """
    config = dict(config or {})
    if provider is None:
        return "(ZCode 需要一个模型：请为它绑定模型服务，或确认全局默认模型可用)"

    # 工具互借：配置里勾了什么就用什么 —— ``zcode_*`` 走内核，宿主工具 id 走借用桥，
    # **两套并存**。一个都没勾时退回国核默认工具链。
    declared = [str(t) for t in (config.get("tools") or [])]
    tool_ids: Optional[list[str]] = declared or None

    borrow_tools = bool(config.get("borrowTools", True))
    borrow_skills = bool(config.get("borrowSkills", True))

    # 技能互借：``skills`` 减去它自带的（``ownSkills``）就是「用户要借的那几个」。
    # 一个都没勾 = 借整个宿主技能库（保持默认行为，见 PROTOCOL.md「互借」一节）。
    all_skills = [str(s) for s in (config.get("skills") or [])]
    own_skills = {str(s) for s in (config.get("ownSkills") or [])}
    borrowed_skills = [s for s in all_skills if s not in own_skills]
    borrowed_skill_names: Optional[list[str]] = borrowed_skills or None

    try:
        max_turns = max(1, min(int(config.get("maxRounds") or ZCODE_DEFAULT_MAX_TURNS), 12))
    except (TypeError, ValueError):
        max_turns = ZCODE_DEFAULT_MAX_TURNS

    options = ZcodeRunOptions(
        working_directory=workspace,
        workspace_root=workspace,
        tool_ids=tool_ids,
        max_turns=max_turns,
        extra_instructions=str(config.get("persona") or ""),
        # 借来的宿主工具要落在**宿主会话**的工作区上，不是 ZCode 的内部会话 id。
        host_session_id=session_id,
        borrow_host_tools=borrow_tools,
        borrow_host_skills=borrow_skills,
        borrowed_skill_names=borrowed_skill_names,
    )
    result = await run_zcode_agent(
        provider, task, session_id=f"{session_id}:zcode", options=options
    )
    if result.error:
        return f"(ZCode 运行失败：{result.error})"
    return result.text or f"(ZCode 未产出文字，stop_reason={result.stop_reason})"
