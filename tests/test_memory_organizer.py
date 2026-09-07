"""Memory organisation (daily logs → RULES.md + wiki/<topic>.md)."""

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
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "memory" / f"{date}.md").write_text(body, encoding="utf-8")


class StubProvider:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def stream_message(self, messages, system_prompt, max_tokens, temperature,
                             tools=None, thinking_level=None):
        yield LLMStreamChunk.Text(self.reply)


REPLY = """===RULES===
- 永远用中文回复
- 输出要简洁

===WIKI===
## 项目约定
- 用 uv 管理依赖
- 测试隔离依赖 MINIS_HOME

## 用户偏好
- 喜欢 Markdown 回复
"""


def _organize(provider) -> memory_organizer.OrganizeResult:
    """organize_memories is async; drive it from sync tests."""
    return asyncio.run(memory_organizer.organize_memories(provider=provider))


def test_write_scopes_create_category_files(env):
    # daily (default) → root YYYY-MM-DD.md (UTC date, like the tool)
    MemoryTools.execute_write(json.dumps({"tool_title": "t", "content": "今天写日志"}))
    root_logs = list((env / "memory").glob("????-??-??.md"))
    assert len(root_logs) == 1

    # wiki → wiki/<topic>.md
    MemoryTools.execute_write(json.dumps({
        "tool_title": "t", "content": "项目用 uv", "scope": "wiki", "topic": "项目约定",
    }))
    # rules → rules/RULES.md
    MemoryTools.execute_write(json.dumps({
        "tool_title": "t", "content": "永远用中文", "scope": "rules",
    }))
    assert (env / "memory" / "wiki").is_dir()
    wiki = list((env / "memory" / "wiki").glob("*.md"))
    assert len(wiki) == 1
    assert "uv" in wiki[0].read_text(encoding="utf-8")
    rules_text = (env / "memory" / "rules" / "RULES.md").read_text(encoding="utf-8")
    assert rules_text.startswith("# RULES")
    assert "永远用中文" in rules_text


def test_get_all_searches_categories(env):
    MemoryTools.execute_write(json.dumps({
        "tool_title": "t", "content": "始终中文", "scope": "rules",
    }))
    MemoryTools.execute_write(json.dumps({
        "tool_title": "t", "content": "uv 是依赖管理", "scope": "wiki", "topic": "tooling",
    }))
    r = MemoryTools.execute_get(json.dumps({"scope": "all", "keywords": "中文"}))
    assert r.success and "中文" in r.output
    r2 = MemoryTools.execute_get(json.dumps({"scope": "all", "keywords": "uv"}))
    assert "uv 是依赖管理" in r2.output


def test_write_wiki_requires_topic(env):
    r = MemoryTools.execute_write(json.dumps({
        "tool_title": "t", "content": "x", "scope": "wiki",
    }))
    assert not r.success


def test_organize_builds_rules_and_wiki(env):
    _write_daily(env, "2026-09-01", "## 09:00\n\n用户喜欢 Markdown 回复\n")
    _write_daily(env, "2026-09-02", "## 10:00\n\n项目使用 uv 管理依赖\n")
    result = _organize(StubProvider(REPLY))

    assert result.applied
    assert result.logs_read >= 2
    assert result.rules_lines >= 2

    rules = (env / "memory" / "rules" / "RULES.md").read_text(encoding="utf-8")
    assert "永远用中文回复" in rules
    assert "输出要简洁" in rules

    wiki_dir = env / "memory" / "wiki"
    names = {p.name for p in wiki_dir.glob("*.md")}
    assert any("约定" in n for n in names)  # slugified 项目约定
    assert any("偏好" in n for n in names)
    combined = "\n".join(p.read_text(encoding="utf-8") for p in wiki_dir.glob("*.md"))
    assert "uv" in combined
    assert "Markdown" in combined


def test_organize_noop_without_sources(env):
    result = _organize(StubProvider(REPLY))
    assert not result.applied
    assert result.skipped


def test_organize_replaces_previous_output(env):
    _write_daily(env, "2026-09-03", "## 09:00\n\n保持简洁\n")
    _organize(StubProvider(REPLY))
    first = sorted(p.name for p in (env / "memory" / "wiki").glob("*.md"))
    # A second pass with the same logs must not duplicate wiki files.
    _organize(StubProvider(REPLY))
    second = sorted(p.name for p in (env / "memory" / "wiki").glob("*.md"))
    assert first == second
