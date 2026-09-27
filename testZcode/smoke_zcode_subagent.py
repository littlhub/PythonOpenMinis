"""ZCode 子代理侧冒烟：配置可改 + 独立运行链路。

用假 store / 假 provider，验证：
- 内置 zcode 现在**可以保存配置覆盖**（技能/工具/模型），且不可删除；
- 默认工具集是 ZCode 自己的工具链；
- ``run_subagent("zcode")`` 走的是独立链路（内核工具 + 内核 prompt），
  而不是 ``AgentRuntime`` + 主智能体工具。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from _root import ensure_src_on_path  # noqa: E402

ensure_src_on_path()

from openminis.agent import subagents as SA  # noqa: E402
from openminis.data.model import LLMStreamChunk, LLMMessage  # noqa: E402

PASSED = 0
FAILED = 0

#: ZCode 自带的工具链（**只增不减**：配置里勾什么都删不掉这些）。
ZCODE_OWN_TOOLS = [
    "zcode_read",
    "zcode_write",
    "zcode_edit",
    "zcode_bash",
    "zcode_glob",
    "zcode_grep",
    "zcode_web_fetch",
    "zcode_todo_read",
    "zcode_todo_write",
    "zcode_skill",
]


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label} {detail}")


class FakeStore:
    """最小 SettingsStore 替身（只在内存里）。"""

    def __init__(self, data: dict) -> None:
        self._data = json.loads(json.dumps(data))

    def load(self) -> dict:
        return self._data

    def save(self, data: dict) -> None:
        self._data = data

    def provider_instances(self) -> list[dict]:
        return list(self._data.get("providers", {}).values())


def store() -> FakeStore:
    return FakeStore(
        {
            "activeProviderId": "openAI",
            "providers": {
                "openAI": {
                    "id": "openAI",
                    "type": "openAI",
                    "apiKey": "sk-test",
                    "model": "gpt-5.2",
                }
            },
            "subagents": {},
        }
    )


def test_config() -> None:
    print("=== 内置 zcode 配置 ===")
    st = store()
    listing = SA.list_subagents(st)
    zcode = listing[0]
    check("zcode 排第一", zcode["id"] == "zcode")
    check("标记为内置", zcode.get("builtin") is True)
    check("标记可配置", zcode.get("configurable") is True)
    check(
        "默认工具集 = ZCode 工具链",
        all(str(t).startswith("zcode_") for t in zcode["tools"]) and len(zcode["tools"]) == 10,
        str(zcode["tools"]),
    )
    check("默认跟随激活实例", zcode["providerId"] == "openAI")

    # 保存覆盖：借一个宿主工具 + 挂一个借来的技能。工具与技能都是**只增不减**：
    # 它自带的那套永远在（界面上也删不掉），覆盖层里只留额外借来的那一份。
    saved = SA.upsert_subagent(
        st,
        {"tools": ["zcode_read", "zcode_edit", "zcode_bash", "file_read"],
         "skills": ["my-zcode-skill"], "maxRounds": 5},
        sid="zcode",
    )
    check(
        "自带工具删不掉（只增不减）",
        all(t in saved["tools"] for t in ZCODE_OWN_TOOLS),
        str(saved["tools"]),
    )
    check("借来的工具单独记账", saved["borrowedTools"] == ["file_read"], str(saved["borrowedTools"]))
    check("技能已挂上", saved["skills"] == ["my-zcode-skill"], str(saved["skills"]))
    check("borrowedSkills 与 skills 一致", saved["borrowedSkills"] == ["my-zcode-skill"])
    check("maxRounds 覆盖生效", saved["maxRounds"] == 5)
    check("覆盖层落库", "zcode" in st.load()["subagents"])

    reloaded = SA.get_subagent(st, "zcode")
    check("读回走合成（不是原始行）", reloaded["skills"] == ["my-zcode-skill"])
    check("读回仍标记内置", reloaded.get("builtin") is True)
    check("未改字段回落默认（name）", reloaded["name"] == "ZCode 编程助手")

    # 未知工具必须被拒（包含型校验对 ZCode 工具放行、对胡编的名字仍然拒绝）
    try:
        SA.upsert_subagent(st, {"tools": ["nope_not_a_tool"]}, sid="zcode")
        check("未知工具被拒", False, "no error raised")
    except SA.SubagentError as exc:
        check("未知工具被拒", "未知工具" in str(exc), str(exc))

    # 应用侧工具也能被 ZCode 子代理勾上（并集＝自带 ∪ 借来的）
    mixed = SA.upsert_subagent(st, {"tools": ["zcode_read", "file_read"]}, sid="zcode")
    check("两套工具可并存", "file_read" in mixed["tools"] and "zcode_read" in mixed["tools"],
          str(mixed["tools"]))
    check("借来的只有一个", mixed["borrowedTools"] == ["file_read"], str(mixed["borrowedTools"]))
    check("自带工具一个不少", all(t in mixed["tools"] for t in ZCODE_OWN_TOOLS), str(mixed["tools"]))

    # loop / prompt 不可改
    try:
        SA.upsert_subagent(st, {"loop": "custom"}, sid="zcode")
        check("loop 不可改", False, "no error raised")
    except SA.SubagentError as exc:
        check("loop 不可改", "不支持修改" in str(exc), str(exc))

    # 不可删除
    try:
        SA.delete_subagent(st, "zcode")
        check("不可删除", False, "no error raised")
    except SA.SubagentError as exc:
        check("不可删除", "不可删除" in str(exc), str(exc))

    # 覆盖层不污染表里的其它子代理
    SA.upsert_subagent(st, {"tools": ["zcode_read"], "skills": []}, sid="zcode")
    check("覆盖层是独立键", set(st.load()["subagents"]) == {"zcode"})
    check(
        "清空勾选 = 不再借任何技能（不是『没改』）",
        SA.get_subagent(st, "zcode")["skills"] == [],
    )
    check(
        "清空勾选也删不掉自带工具",
        set(SA.get_subagent(st, "zcode")["tools"]) == set(ZCODE_OWN_TOOLS),
        str(SA.get_subagent(st, "zcode")["tools"]),
    )


class FakeProvider:
    """两轮模型：第一轮调 ``zcode_read``，第二轮收口成文本。"""

    def __init__(self) -> None:
        self.rounds = 0
        self.seen_tools: list[str] = []
        self.seen_system: str = ""

    async def stream_message(self, messages, system_prompt, max_tokens, temperature=None,
                             image_parts=None, tools=None, thinking_level=None):
        self.rounds += 1
        self.seen_system = system_prompt or ""
        self.seen_tools = [t.name for t in (tools or [])]
        if self.rounds == 1:
            yield LLMStreamChunk.ToolCallComplete(
                id="call-1", name="zcode_read", args={"file_path": self._target}
            )
            yield LLMStreamChunk.Finished(stop_reason="tool_use")
            return
        yield LLMStreamChunk.Text(text="读到了文件，任务完成。")
        yield LLMStreamChunk.Finished(stop_reason="end_turn")

    def aclose(self) -> None:  # pragma: no cover
        return None


def test_independent_run() -> None:
    print("=== 独立运行链路 ===")
    from openminis.agent.zcode.integration.agent import ZcodeRunOptions, run_zcode_agent

    root = Path(tempfile.mkdtemp(prefix="zcode-agent-"))
    target = root / "hello.py"
    target.write_text("print('hi')\n", encoding="utf-8")

    provider = FakeProvider()
    provider._target = str(target)

    result = asyncio.run(
        run_zcode_agent(
            provider,
            "看看 hello.py",
            session_id="sess-zcode",
            options=ZcodeRunOptions(working_directory=str(root), workspace_root=str(root)),
        )
    )
    check("运行成功", result.ok, repr(result.text))
    check("拿到最终文本", "任务完成" in result.text, repr(result.text))
    check("工具已执行", len(result.tool_calls) == 1 and result.tool_calls[0]["ok"], str(result.tool_calls))
    check("工具输出含文件内容", "print" in result.tool_calls[0]["output"], repr(result.tool_calls[0]["output"][:80]))
    check("用的是 ZCode 工具名", result.tool_calls[0]["name"] == "zcode_read")
    check("暴露的工具全是 zcode_*", all(n.startswith("zcode_") for n in provider.seen_tools), str(provider.seen_tools))
    check("系统提示由内核拼装（含工具链段）", "ZCode toolchain" in provider.seen_system)
    check("系统提示不含主智能体人设", "OpenMinis" not in provider.seen_system)

    print("=== run_subagent 路由到独立链路 ===")
    st = store()
    fake = FakeProvider()
    fake._target = str(target)
    # 只在测试里替掉 provider 构造：正式的 provider 层依赖 httpx（应用运行环境自带），
    # 本冒烟脚本用最小解释器跑，不引入它。
    import types as _types

    stub = _types.ModuleType("openminis.settings.chat_service")
    stub.build_provider = lambda pid, conf: fake  # type: ignore[attr-defined]
    saved_module = sys.modules.get("openminis.settings.chat_service")
    sys.modules["openminis.settings.chat_service"] = stub
    try:
        out = asyncio.run(SA.run_subagent(st, "zcode", "看看 hello.py", "sess-1"))
    finally:
        if saved_module is None:
            sys.modules.pop("openminis.settings.chat_service", None)
        else:
            sys.modules["openminis.settings.chat_service"] = saved_module
    check("委派 zcode 走独立链路", "任务完成" in out, repr(out))
    check("未走 AgentRuntime（模型被调 2 轮）", fake.rounds == 2, str(fake.rounds))


def test_loop_mode_zcode() -> None:
    """``loopMode = "zcode"``：整轮交给编程内核，而不是换一套工具跑 ReAct。"""
    print("=== 循环模式 zcode ===")
    from openminis.agent.agent_runtime import AgentRuntime, AgentRuntimeOptions
    from openminis.core.context import AppContext, reset_app_context, set_app_context

    class ChattyProvider:
        """一开口就收口的模型：ZCode 内核跑一轮就该结束。"""

        def __init__(self) -> None:
            self.rounds = 0
            self.seen_system = ""

        async def stream_message(self, messages, system_prompt, max_tokens,
                                 temperature=None, image_parts=None, tools=None,
                                 thinking_level=None):
            self.rounds += 1
            self.seen_system = system_prompt or ""
            yield LLMStreamChunk.Text(text="快排已经写好了。")
            yield LLMStreamChunk.Finished(stop_reason="end_turn")

        def aclose(self) -> None:  # pragma: no cover
            return None

    tmp = Path(tempfile.mkdtemp(prefix="smoke-loop-zcode-"))
    set_app_context(AppContext(data_dir=tmp))
    try:
        seen: list = []

        async def sink(chunk) -> None:
            seen.append(chunk)

        runtime = AgentRuntime(tools={}, chunk_sink=sink)
        provider = ChattyProvider()
        messages = [LLMMessage(LLMMessage.Role.USER, "写个快排")]
        out, stop = asyncio.run(
            runtime.run(
                provider,
                messages,
                "sess-zcode",
                AgentRuntimeOptions(loop_mode="zcode", max_turns=4),
            )
        )

        check("返回的就是传进来的 messages", out is messages)
        check(
            "追加了一条 assistant 消息",
            len(messages) == 2 and messages[-1].role == LLMMessage.Role.ASSISTANT,
            f"len={len(messages)}",
        )
        # 正文必须在 ``content_parts`` 里（``content`` 留空）—— 服务端落库就是按这个
        # 形态取的（``server/main.py`` 的 ``final_text``）。原先写成
        # ``LLMMessage(role, text)``（正文进 ``content``）会让那一步恒得空串，加上
        # zcode 模式下拿不到 ReAct 的工具记录（``runs`` 空），``if final_text.strip()
        # or runs`` 为假 → **整轮不写库** → 前端刷新 / 切会话 / 重启后端之后 ZCode
        # 的总结就没了。重建上下文也只读 text part，所以多轮里它还会「忘记」自己
        # 上一轮说过什么。
        from openminis.data.model.agent_content_part import Text as _Text

        def _persisted(m) -> str:
            """复刻 ``server/main.py`` 落库前对 ``messages[-1]`` 的正文提取。"""
            return "".join(
                p.text
                for p in (getattr(m, "content_parts", None) or [])
                if isinstance(p, _Text)
            )

        check(
            "正文落在 content_parts（与 ReAct 同形态）",
            _persisted(messages[-1]) == "快排已经写好了。",
            repr(_persisted(messages[-1])),
        )
        check(
            "服务端提取得到非空正文（否则整轮不写库）",
            bool(_persisted(messages[-1]).strip()),
            repr(_persisted(messages[-1])),
        )
        check(
            "content 字段留空（形态与 ReAct 一致）",
            not messages[-1].content,
            repr(messages[-1].content),
        )
        check("stop_reason 透传给调用方", stop == "end_turn", str(stop))
        check(
            "文本仍经同一个 chunk_sink 流式流出",
            any(isinstance(c, LLMStreamChunk.Text) for c in seen),
            str(seen)[:80],
        )
        check(
            "用的是 ZCode 内核自己的系统提示",
            "ZCode" in provider.seen_system and "OpenMinis" not in provider.seen_system,
            provider.seen_system[:80],
        )
        check("内核自己收口，只跑了 1 轮", provider.rounds == 1, str(provider.rounds))
    finally:
        reset_app_context()


def test_zcode_summary_persists() -> None:
    """``loopMode = "zcode"`` 的总结必须**真的进库** —— 刷新/切会话/重启后还在。

    用户现场：「有总结，但刷新后没了」。原因不在渲染，在存储：原先
    ``_run_zcode_mode`` 用 ``LLMMessage(role, text)`` 把正文放进 ``content``，
    而 ``content_parts`` 是空的；服务端落库读的是 ``content_parts`` 里的 ``Text``
    （``server/main.py`` 的 ``final_text``）→ 恒为空串，加上 zcode 模式下 ``runs``
    也拿不到 ReAct 的工具记录 → ``if final_text.strip() or runs`` 为假 → **整轮
    不写库**。实时看得到（走流式 delta 帧），一刷新就没了。

    这里走**真实 SQLite**（``set_database_path`` 指到 tmp），把服务端的落库逻辑
    复刻一遍，再用 ``load_messages`` / ``load_runtime_history`` 读回来 ——
    等价于「刷新页面后重新拉历史」。
    """
    print("=== ZCode 总结持久化 ===")
    import asyncio as _asyncio
    import tempfile as _tempfile

    from openminis.agent.agent_runtime import AgentRuntime, AgentRuntimeOptions
    from openminis.core.context import AppContext, reset_app_context, set_app_context
    from openminis.data.model.agent_content_part import Text as _Text

    try:
        from openminis.server import chat_store
    except Exception as exc:  # pragma: no cover - 取决于解释器里装没装项目依赖
        # 这条**必须**喊出来：静默返回会让「没验证」看起来和「验证通过」一样。
        # 项目依赖（sqlalchemy / aiosqlite）只装在开发解释器里；managed runtime
        # 是裸的。用装了依赖的那个跑（本机是系统 Python 3.10）。
        print(f"  - 跳过：需要项目依赖才能开真实库（{type(exc).__name__}: {exc}）")
        print("    用装了项目依赖的解释器跑：Python310")
        return

    tmp = Path(_tempfile.mkdtemp(prefix="smoke-zcode-persist-"))
    settled = "总结：已经写好 summary-note.txt，放在工作区根目录。"

    class TwoRoundProvider:
        """第一轮「说话 + 调工具」，第二轮给总结 —— 用户现场就是这种形态。"""

        def __init__(self) -> None:
            self.rounds = 0

        async def stream_message(self, messages, system_prompt, max_tokens,
                                 temperature=None, image_parts=None, tools=None,
                                 thinking_level=None):
            self.rounds += 1
            if self.rounds == 1:
                yield LLMStreamChunk.Text(text="我先看一眼目录。")
                yield LLMStreamChunk.ToolUseStart(id="c1", name="zcode_write")
                yield LLMStreamChunk.ToolCallComplete(
                    id="c1", name="zcode_write",
                    args={"file_path": "summary-note.txt", "content": "hi\n"},
                )
                yield LLMStreamChunk.Finished(stop_reason="tool_use")
                return
            yield LLMStreamChunk.Text(text=settled)
            yield LLMStreamChunk.Finished(stop_reason="end_turn")

        def aclose(self) -> None:  # pragma: no cover
            return None

    set_app_context(AppContext(data_dir=tmp / "persist-data"))
    chat_store.set_database_path(str(tmp / "chat.sqlite"))
    try:
        streamed: list = []

        async def sink(chunk) -> None:
            streamed.append(chunk)

        async def scenario() -> tuple[str, str]:
            info = await chat_store.create_session()
            runtime = AgentRuntime(tools={}, chunk_sink=sink)
            messages = [LLMMessage(LLMMessage.Role.USER, "写个 note")]
            out, _stop = await runtime.run(
                TwoRoundProvider(),
                messages,
                info.id,
                AgentRuntimeOptions(loop_mode="zcode", max_turns=4),
            )
            # ── 复刻 server/main.py 的落库步骤（这段在原处是内联的）──────────
            tail = out[-1]
            final_text = "".join(
                p.text for p in (getattr(tail, "content_parts", None) or [])
                if isinstance(p, _Text)
            ).strip()
            if final_text:
                await chat_store.append_turn(info.id, "assistant", final_text)
            return info.id, final_text

        sid, final_text = _asyncio.run(scenario())

        check("服务端提取得到总结正文（非空才落库）", bool(final_text), repr(final_text))
        check(
            "落库的是**最终总结**，不是中途那句话",
            final_text == settled,
            repr(final_text),
        )

        rows = _asyncio.run(chat_store.load_messages(sid))
        assistant_rows = [r for r in rows if r.role == "assistant"]
        check("库里有一条 assistant 行", len(assistant_rows) == 1, f"rows={[r.role for r in rows]}")
        check(
            "刷新后读得到总结（load_messages）",
            bool(assistant_rows) and settled in assistant_rows[0].text,
            repr(assistant_rows[0].text) if assistant_rows else "<none>",
        )

        rebuilt = _asyncio.run(chat_store.load_runtime_history(sid))
        rebuilt_text = " ".join(
            "".join(p.text for p in (getattr(m, "content_parts", None) or [])
                    if isinstance(p, _Text)) or str(getattr(m, "content", "") or "")
            for m in rebuilt
        )
        check(
            "重建上下文也拿得到（多轮里它不会「失忆」）",
            settled in rebuilt_text,
            rebuilt_text[:120],
        )
    finally:
        chat_store.set_database_path(None)
        reset_app_context()


def test_zcode_summary_turn() -> None:
    """工具跑完、模型却没留一句面向用户的话 → 必须补一轮收尾（逼出总结）。

    ReAct 那边有「空转自动收尾」护栏兜这个，ZCode 是**整个循环换掉**、没有那套
    —— 于是界面上的「助手」下面是空的，只剩一排工具卡。这里让模型每个回合都只顾
    调工具（轮次用尽而自然收口），确认收尾轮把工具清空、逼出了正文。
    """
    print("=== ZCode 收尾轮 ===")
    import asyncio as _asyncio
    import tempfile as _tempfile

    from openminis.agent.agent_runtime import AgentRuntime, AgentRuntimeOptions
    from openminis.core.context import AppContext, reset_app_context, set_app_context
    from openminis.data.model.agent_content_part import Text as _Text

    tmp = Path(_tempfile.mkdtemp(prefix="smoke-zcode-summary-"))

    class ToolOnlyProvider:
        """每个回合只顾调工具、从不说人话；工具被拿走时才开口。"""

        def __init__(self) -> None:
            self.rounds = 0
            self.tool_counts: list[int] = []

        async def stream_message(self, messages, system_prompt, max_tokens,
                                 temperature=None, image_parts=None, tools=None,
                                 thinking_level=None):
            self.rounds += 1
            self.tool_counts.append(len(tools or []))
            if not tools:
                # 收尾轮：工具列表被清空 → 它无处可调，只能产出正文。
                yield LLMStreamChunk.Text(text="总结：已经把 a1.txt / a2.txt 写好了。")
                yield LLMStreamChunk.Finished(stop_reason="end_turn")
                return
            yield LLMStreamChunk.ToolUseStart(id=f"c{self.rounds}", name="zcode_write")
            yield LLMStreamChunk.ToolCallComplete(
                id=f"c{self.rounds}", name="zcode_write",
                args={"file_path": f"a{self.rounds}.txt", "content": "x\n"},
            )
            yield LLMStreamChunk.Finished(stop_reason="tool_use")

        def aclose(self) -> None:  # pragma: no cover
            return None

    set_app_context(AppContext(data_dir=tmp / "summary-data"))
    try:
        provider = ToolOnlyProvider()
        messages = [LLMMessage(LLMMessage.Role.USER, "写两个文件")]
        out, stop = _asyncio.run(
            AgentRuntime(tools={}, chunk_sink=None).run(
                provider,
                messages,
                "sess-summary",
                AgentRuntimeOptions(loop_mode="zcode", max_turns=2),
            )
        )
        tail = out[-1]
        text = "".join(
            p.text for p in (getattr(tail, "content_parts", None) or [])
            if isinstance(p, _Text)
        )
        check("轮次用尽也补出了总结", "已经把 a1.txt" in text, repr(text))
        check(
            "收尾轮把工具清空了（否则它只会继续调）",
            provider.tool_counts[-1] == 0,
            str(provider.tool_counts),
        )
        check("只补一轮（2 轮工具 + 1 轮收尾）", provider.rounds == 3, str(provider.rounds))
        check("stop_reason 收成 end_turn", stop == "end_turn", str(stop))
    finally:
        reset_app_context()


test_config()
test_independent_run()
test_loop_mode_zcode()
test_zcode_summary_turn()
test_zcode_summary_persists()

print()
print(f"PASSED = {PASSED}   FAILED = {FAILED}")
if FAILED:
    raise SystemExit(1)
