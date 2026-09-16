"""记忆五分类 + 记忆与知识分开（memory/ 五类，knowledge/ 独立）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from openminis.core import context
from openminis.data.model import LLMStreamChunk
from openminis.server import memory_organizer
from openminis.tools.memory_tools import MemoryTools


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    context.set_app_context(context.AppContext(data_dir=tmp_path, cache_dir=tmp_path))
    yield tmp_path
    context._context = None


def _write_daily(tmp_path, date: str, body: str) -> None:
    d = tmp_path / "memory" / "daily"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{date}.md").write_text(body, encoding="utf-8")


class StubProvider:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def stream_message(self, messages, system_prompt, max_tokens, temperature,
                             tools=None, thinking_level=None):
        yield LLMStreamChunk.Text(self.reply)


REPLY = """===LONG_TERM===
- 项目用 uv 管理依赖
- 测试隔离依赖 MINIS_HOME

===RULES===
- 永远用中文回复
- 输出要简洁

===TROUBLESHOOTING===
- 现象：GBK 控制台打印 emoji 崩 → 根因：编码不是 utf-8 → 解法：入口 reconfigure utf-8

===PREFERENCES===
- 喜欢 Markdown 回复
"""


def _organize(provider) -> memory_organizer.OrganizeResult:
    """organize_memories is async; drive it from sync tests."""
    return asyncio.run(memory_organizer.organize_memories(provider=provider))


# ---------------------------------------------------------------------------
# 五类落点
# ---------------------------------------------------------------------------
def test_five_kinds_write_to_their_own_files(env):
    """记忆只分五类，各有固定落点；知识（wiki）写到独立的 knowledge/。"""
    w = lambda **kw: MemoryTools.execute_write(json.dumps({"tool_title": "t", **kw}))

    w(content="今天的流水账")
    w(content="项目用 uv 管理依赖", scope="long_term")
    w(content="永远用中文", scope="rules")
    w(content="报错：GBK → 解法：reconfigure", scope="troubleshooting")
    w(content="喜欢 Markdown 回复", scope="preferences")
    w(content="uv 的使用说明", scope="wiki", topic="tooling")

    mem = env / "memory"
    assert len(list((mem / "daily").glob("????-??-??.md"))) == 1
    assert "uv 管理依赖" in (mem / "long-term" / "LONG_TERM.md").read_text("utf-8")
    assert (mem / "rules" / "RULES.md").read_text("utf-8").startswith("# RULES")
    assert "GBK" in (mem / "troubleshooting" / "TROUBLESHOOTING.md").read_text("utf-8")
    assert "Markdown" in (mem / "preferences" / "USER.md").read_text("utf-8")

    # 知识不落在记忆目录里
    assert not (mem / "wiki").exists()
    knowledge = list((env / "knowledge").glob("*.md"))
    assert len(knowledge) == 1 and "uv 的使用说明" in knowledge[0].read_text("utf-8")


def test_kind_alias_and_default(env):
    """scope 别名归一化；未知/缺省 → daily。"""
    MemoryTools.execute_write(json.dumps({"tool_title": "t", "content": "规则一", "scope": "规则"}))
    MemoryTools.execute_write(json.dumps({"tool_title": "t", "content": "偏好一", "scope": "preference"}))
    MemoryTools.execute_write(json.dumps({"tool_title": "t", "content": "随手记"}))
    mem = env / "memory"
    assert "规则一" in (mem / "rules" / "RULES.md").read_text("utf-8")
    assert "偏好一" in (mem / "preferences" / "USER.md").read_text("utf-8")
    assert len(list((mem / "daily").glob("*.md"))) == 1


def test_get_can_scope_to_one_kind_or_knowledge(env):
    MemoryTools.execute_write(json.dumps({"tool_title": "t", "content": "始终中文", "scope": "rules"}))
    MemoryTools.execute_write(json.dumps({"tool_title": "t", "content": "uv 是依赖管理", "scope": "wiki", "topic": "tooling"}))

    all_hit = MemoryTools.execute_get(json.dumps({"scope": "all", "keywords": "中文"}))
    assert all_hit.success and "中文" in all_hit.output

    # 知识不在「记忆」里：按记忆范围搜不到，只有 knowledge 范围能搜到
    mem_only = MemoryTools.execute_get(json.dumps({"scope": "rules", "keywords": "uv"}))
    assert "uv 是依赖管理" not in mem_only.output
    kn = MemoryTools.execute_get(json.dumps({"scope": "knowledge", "keywords": "uv"}))
    assert "uv 是依赖管理" in kn.output


def test_write_wiki_requires_topic(env):
    r = MemoryTools.execute_write(json.dumps({
        "tool_title": "t", "content": "x", "scope": "wiki",
    }))
    assert not r.success


# ---------------------------------------------------------------------------
# 整理：日志 → 四类长期记忆
# ---------------------------------------------------------------------------
def test_organize_distils_into_four_kinds(env):
    _write_daily(env, "2026-09-01", "## 09:00\n\n用户喜欢 Markdown 回复\n")
    _write_daily(env, "2026-09-02", "## 10:00\n\n项目使用 uv 管理依赖\n")
    result = _organize(StubProvider(REPLY))

    assert result.applied
    assert result.logs_read >= 2
    assert result.rules_lines >= 2
    assert set(result.kinds) == {"long_term", "rules", "troubleshooting", "preferences"}

    mem = env / "memory"
    assert "永远用中文回复" in (mem / "rules" / "RULES.md").read_text("utf-8")
    assert "uv" in (mem / "long-term" / "LONG_TERM.md").read_text("utf-8")
    assert "reconfigure" in (mem / "troubleshooting" / "TROUBLESHOOTING.md").read_text("utf-8")
    assert "Markdown" in (mem / "preferences" / "USER.md").read_text("utf-8")
    # 整理器不再产出知识文件（knowledge/ 只会有五类空目录 + 自动索引）
    assert not list((env / "knowledge").rglob("*.md")) or {
        p.name for p in (env / "knowledge").rglob("*.md")
    } == {"index.md"}


def test_organize_noop_without_sources(env):
    result = _organize(StubProvider(REPLY))
    assert not result.applied
    assert result.skipped


def test_organize_is_repeatable(env):
    _write_daily(env, "2026-09-03", "## 09:00\n\n保持简洁\n")
    _organize(StubProvider(REPLY))
    lt = env / "memory" / "long-term" / "LONG_TERM.md"
    first = lt.read_text("utf-8")
    _organize(StubProvider(REPLY))
    second = lt.read_text("utf-8")
    # 覆盖式重建：同样的输入不会把条目越堆越多
    assert first.count("- 项目用 uv 管理依赖") == second.count("- 项目用 uv 管理依赖") == 1


# ---------------------------------------------------------------------------
# 旧布局迁移
# ---------------------------------------------------------------------------
def test_legacy_layout_is_migrated(env):
    """旧布局（根目录日志 / GLOBAL.md / wiki）就地归位，内容不丢。"""
    mem = env / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "2026-08-01.md").write_text("旧日志内容\n", encoding="utf-8")
    (mem / "GLOBAL.md").write_text("旧全局记忆\n", encoding="utf-8")
    (mem / "wiki").mkdir()
    (mem / "wiki" / "旧主题.md").write_text("旧知识内容\n", encoding="utf-8")

    memory_organizer.ensure_memory_layout()

    assert "旧日志内容" in (mem / "daily" / "2026-08-01.md").read_text("utf-8")
    assert "旧全局记忆" in (mem / "long-term" / "LONG_TERM.md").read_text("utf-8")
    assert "旧知识内容" in (env / "knowledge" / "旧主题.md").read_text("utf-8")
    assert not (mem / "wiki").exists()
    assert not (mem / "GLOBAL.md").exists()


# ---------------------------------------------------------------------------
# 自动整理：提问数触发 + 时间兜底
# ---------------------------------------------------------------------------
def test_note_user_message_counts_and_resets(env):
    """每条提问计数 +1；整理盖戳后计数清零。"""
    _write_daily(env, "2026-09-01", "## 09:00\n\n保持简洁\n")
    assert memory_organizer.note_user_message() == 1
    assert memory_organizer.note_user_message() == 2
    assert memory_organizer.message_organize_due(3) is False
    assert memory_organizer.note_user_message() == 3
    assert memory_organizer.message_organize_due(3) is True
    # 到点整理一次：计数归零，四类文件写出
    result = _organize(StubProvider(REPLY))
    assert result.applied
    memory_organizer.mark_organized()
    assert memory_organizer.message_organize_due(3) is False


def test_message_organize_disabled_when_zero(env):
    """memoryOrganizeEvery = 0 表示关闭，永远不会到点。"""
    for _ in range(50):
        memory_organizer.note_user_message()
    assert memory_organizer.message_organize_due(0) is False


def test_auto_organize_due_time_throttle(env, monkeypatch):
    """时间兜底：没日志不跑；有日志且没盖戳跑一次；20h 内且无新日志不重复跑。"""
    import os
    import time as _time

    _write_daily(env, "2026-09-01", "## 09:00\n\n保持简洁\n")
    assert memory_organizer.auto_organize_due() is True  # 从没整理过

    memory_organizer.mark_organized()
    assert memory_organizer.auto_organize_due() is False  # 20h 内 + 无新日志

    # 时间兜底是硬节流：日志再新，冷却（20h）没过也不跑（提问数触发不受此限）
    future = _time.time() + 25 * 3600
    log = env / "memory" / "daily" / "2026-09-01.md"
    os.utime(log, (future, future))
    assert memory_organizer.auto_organize_due() is False

    # 冷却过了 + 日志比上次整理新 → 该跑
    old = _time.time() - 25 * 3600
    stamp = env / "memory" / ".last-organize"
    os.utime(stamp, (old, old))
    assert memory_organizer.auto_organize_due() is True

    # 没有任何每日日志 → 永远不跑
    empty = env / "empty-home"
    empty.mkdir()
    (empty / "memory" / "daily").mkdir(parents=True)
    assert memory_organizer.auto_organize_due(empty) is False


def test_run_message_organize_failure_keeps_counter(env, monkeypatch):
    """模型失败（如未配置）时计数保留，下一条提问还会重试。"""
    _write_daily(env, "2026-09-01", "## 09:00\n\n保持简洁\n")
    for _ in range(3):
        memory_organizer.note_user_message()

    async def _boom(provider=None):
        raise RuntimeError("no provider")

    monkeypatch.setattr(memory_organizer, "organize_memories", _boom)
    result = asyncio.run(memory_organizer.run_message_organize_if_due(3))
    assert result is None
    assert memory_organizer.message_organize_due(3) is True  # 计数还在
