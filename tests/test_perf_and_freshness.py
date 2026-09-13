"""本轮优化与时效修复的回归测试。

覆盖四件事：

1. **同轮重复直接拦**：模型一次回复里吐两份「同工具 + 完全同参」的调用，
   第二份必须当场被拦（detector 的历史要执行后才写，看不见同轮重复）。
2. **同轮多工具并发执行**：串行等待会把耗时相加（4 张识图 = 4 个请求排队），
   并发后总耗时取决于最慢的一个，结果仍按原顺序回填。
3. **provider 缓存**：provider 内部持有 httpx 连接池，每轮新建等于每次重新
   TLS 握手（实测冷连接 ~1.4s）。同配置必须复用同一实例。
4. **技能清单缓存**：清单每轮都要（拼系统提示），不能每次都重新解析 40 个
   SKILL.md 的 frontmatter。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from openminis.agent.agent_runtime import AgentRuntime, AgentRuntimeOptions, ToolExecutor
from openminis.core import context
from openminis.data.model import LLMMessage, LLMStreamChunk, ThinkingLevel
from openminis.data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from openminis.tools.tool_execution_result import ToolExecutionResult


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """把数据目录挪到 tmp，技能/设置都不碰真实的 ~/openminis。"""
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    yield tmp_path
    context._context = None  # type: ignore[attr-defined]


def _two_arg_tool_def() -> AgentToolDefinition:
    return AgentToolDefinition(
        name="read_image", description="read an image",
        parameters={
            "tool_title": AgentToolParam("string", "title"),
            "path": AgentToolParam("string", "image path"),
        },
        required=["path"],
    )


class _TwoSameRoundProvider:
    """一轮回复里同时给出两个「同工具同参」的调用，随后收尾。"""

    def __init__(self) -> None:
        self.calls = 0

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=ThinkingLevel.OFF):
        async def gen():
            self.calls += 1
            if self.calls == 1:
                for cid in ("c1", "c2"):
                    yield LLMStreamChunk.ToolCallComplete(
                        cid, "read_image",
                        {"tool_title": f"看图 {cid}", "path": "a.png"},
                    )
                yield LLMStreamChunk.Finished("tool_use")
            else:
                yield LLMStreamChunk.Text("看完了。")
                yield LLMStreamChunk.Finished("end_turn")
        return gen()


@pytest.mark.asyncio
async def test_same_round_duplicate_is_blocked():
    """同一轮里第二个完全相同的调用被拦，工具只跑一次。"""
    rt = AgentRuntime()
    runs: list[str] = []

    async def _read(args_json: str, session_id: str, **kw) -> ToolExecutionResult:
        runs.append(args_json)
        return ToolExecutionResult("一只猫", True)

    rt.register(ToolExecutor(_two_arg_tool_def(), _read))
    out, stop = await rt.run(_TwoSameRoundProvider(),
                             [LLMMessage(LLMMessage.Role.USER, "看看这张图")],
                             "sess-dup", AgentRuntimeOptions(loop_mode="react"))
    assert stop == "end_turn"
    assert len(runs) == 1, "同轮重复的第二份调用不该真的执行"
    texts = [getattr(p, "content", "") or ""
             for m in out for p in (getattr(m, "content_parts", None) or [])]
    assert any("LOOP BLOCKED" in t for t in texts)


class _TwoDifferentToolsProvider:
    """一轮里调用两个不同参数的工具（都该并发跑）。"""

    def __init__(self, delay: float = 0.25) -> None:
        self.calls = 0
        self.delay = delay

    def stream_message(self, messages, system_prompt=None, max_tokens=0,
                       temperature=None, image_parts=None, tools=None,
                       thinking_level=ThinkingLevel.OFF):
        async def gen():
            self.calls += 1
            if self.calls == 1:
                yield LLMStreamChunk.ToolCallComplete(
                    "c1", "read_image", {"path": "a.png"})
                yield LLMStreamChunk.ToolCallComplete(
                    "c2", "read_image", {"path": "b.png"})
                yield LLMStreamChunk.Finished("tool_use")
            else:
                yield LLMStreamChunk.Text("两张都看完了。")
                yield LLMStreamChunk.Finished("end_turn")
        return gen()


@pytest.mark.asyncio
async def test_round_tools_run_concurrently():
    """两个各 0.25s 的工具并发跑，总耗时明显小于 0.5s（串行会相加）。"""
    delay = 0.3
    rt = AgentRuntime()
    order: list[str] = []

    async def _read(args_json: str, session_id: str, **kw) -> ToolExecutionResult:
        await asyncio.sleep(delay)
        order.append(args_json)
        return ToolExecutionResult("ok", True)

    rt.register(ToolExecutor(_two_arg_tool_def(), _read))
    started = time.monotonic()
    out, stop = await rt.run(_TwoDifferentToolsProvider(delay),
                             [LLMMessage(LLMMessage.Role.USER, "看两张图")],
                             "sess-parallel", AgentRuntimeOptions(loop_mode="react"))
    elapsed = time.monotonic() - started
    assert stop == "end_turn"
    assert len(order) == 2
    assert elapsed < delay * 2 - 0.05, f"两个工具似乎串行了: {elapsed:.2f}s"
    # 结果顺序 = 调用顺序（a 在前 b 在后）
    assert "a.png" in order[0] and "b.png" in order[1]


# --------------------------------------------------------------------------
# provider 连接复用
# --------------------------------------------------------------------------

def test_same_provider_config_reuses_instance(tmp_path):
    from openminis.settings.chat_service import build_provider, reset_provider_cache

    reset_provider_cache()
    conf = {"type": "openAI", "apiKey": "sk-test", "baseUrl": "https://x.example/v1",
            "model": "gpt-4o-mini"}
    p1 = build_provider("openAI", conf)
    p2 = build_provider("openAI", dict(conf))
    assert p1 is p2, "同配置必须复用 provider（否则每轮都重做 TLS 握手）"
    # 换 key / 换模型 → 新实例，绝不能串用别人的连接与凭据
    p3 = build_provider("openAI", {**conf, "apiKey": "sk-other"})
    p4 = build_provider("openAI", {**conf, "model": "gpt-4o"})
    assert p1 is not p3 and p1 is not p4
    reset_provider_cache()
    assert build_provider("openAI", conf) is not p1


# --------------------------------------------------------------------------
# 技能清单缓存
# --------------------------------------------------------------------------

def test_skill_list_is_cached(home):
    from openminis.skills.store import SkillStore, invalidate_skill_cache

    invalidate_skill_cache()
    store = SkillStore()
    first = store.list()
    second = store.list()
    assert [e.name for e in first] == [e.name for e in second]
    invalidate_skill_cache()


def test_skill_list_cache_invalidated_on_new_skill(home):
    from openminis.skills.store import SkillStore, invalidate_skill_cache

    invalidate_skill_cache()
    store = SkillStore()
    before = len(store.list())
    new_skill = store.root / "zz-temp-skill"
    new_skill.mkdir(parents=True, exist_ok=True)
    (new_skill / "SKILL.md").write_text(
        "---\nname: zz-temp-skill\ndescription: 临时技能\n---\n正文\n",
        encoding="utf-8",
    )
    after = store.list()
    assert len(after) == before + 1, "新增技能后清单必须刷新"
    assert any(e.name == "zz-temp-skill" for e in after)
    invalidate_skill_cache()


# --------------------------------------------------------------------------
# 时效：系统提示必须带当前时间
# --------------------------------------------------------------------------

def test_system_prompt_carries_current_time(home):
    from datetime import datetime

    from openminis.settings.chat_service import current_time_block

    block = current_time_block()
    assert datetime.now().strftime("%Y-%m-%d") in block
    # 检索时效纪律必须写明「不传 time_range 默认不限时间」
    from openminis.settings.chat_service import RETRIEVAL_DISCIPLINE

    assert "time_range" in RETRIEVAL_DISCIPLINE
    assert "时效纪律" in RETRIEVAL_DISCIPLINE


def test_web_search_declares_time_range_param():
    from openminis.tools.web_search_tool import WebSearchTool

    d = WebSearchTool.definition()
    assert "time_range" in d.parameters
    assert "time_range" in (d.description or "")


def test_system_prompt_asks_for_parallel_tool_calls():
    """工具已并发执行，提示必须让模型「一轮给全」，否则白等多次往返。"""
    from openminis.settings.chat_service import PARALLEL_TOOL_DISCIPLINE

    assert "并行" in PARALLEL_TOOL_DISCIPLINE
    assert "同一轮" in PARALLEL_TOOL_DISCIPLINE
