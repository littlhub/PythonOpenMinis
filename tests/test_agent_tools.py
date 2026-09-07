"""Tests for the Kotlin tools that were ported in the
'diff-driven catch-up' pass (file_write, file_edit, memory_*, browser_use,
agent_tools registry, vision_group_resolver framing).

These tests cover the behaviour the LLM-facing tool contract guarantees —
not exhaustive parity with the Android originals, just enough to keep the
agent loop safe.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest


@pytest.fixture()
def isolated_minis_home(tmp_path, monkeypatch):
    """Make sure tools that read app_context() don't touch the real data dir."""
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    from openminis.core import context

    context.set_app_context(
        context.AppContext(data_dir=tmp_path, cache_dir=tmp_path)
    )
    yield tmp_path
    context._context = None  # type: ignore[attr-defined]


def _args(**kw):
    return json.dumps(kw)


# ---------------------------------------------------------------------------
# file_write
# ---------------------------------------------------------------------------
def test_file_write_creates_file(isolated_minis_home):
    from openminis.tools.file_write_tool import FileWriteTool

    r = FileWriteTool.execute(_args(path="/hello.txt", content="hi"), "s1")
    assert r.success, r.output
    p = isolated_minis_home / "workspace" / "s1" / "hello.txt"
    assert p.exists()
    assert p.read_text(encoding="utf-8") == "hi"


def test_file_write_append_creates_dirs(isolated_minis_home):
    from openminis.tools.file_write_tool import FileWriteTool

    r = FileWriteTool.execute(
        _args(path="/nested/dir/log.txt", content="line1\n", create_dirs=True),
        "s1",
    )
    assert r.success, r.output
    r2 = FileWriteTool.execute(
        _args(path="/nested/dir/log.txt", content="line2\n", append=True),
        "s1",
    )
    assert r2.success
    p = isolated_minis_home / "workspace" / "s1" / "nested" / "dir" / "log.txt"
    assert p.read_text(encoding="utf-8") == "line1\nline2\n"


def test_file_write_rejects_escape(isolated_minis_home):
    from openminis.tools.file_write_tool import FileWriteTool

    r = FileWriteTool.execute(_args(path="../../etc/passwd", content="x"), "s1")
    assert not r.success
    assert "Cannot resolve path" in r.output


# ---------------------------------------------------------------------------
# file_edit
# ---------------------------------------------------------------------------
def test_file_edit_single_replacement(isolated_minis_home):
    from openminis.tools.file_edit_tool import FileEditTool
    from openminis.tools.file_write_tool import FileWriteTool

    FileWriteTool.execute(_args(path="/cfg.txt", content="alpha\nbeta\ngamma"), "s1")
    r = FileEditTool.execute(
        _args(path="/cfg.txt", old_string="beta", new_string="BETA"), "s1"
    )
    assert r.success, r.output
    p = isolated_minis_home / "workspace" / "s1" / "cfg.txt"
    assert "BETA" in p.read_text(encoding="utf-8")


def test_file_edit_requires_unique_when_not_replace_all(isolated_minis_home):
    from openminis.tools.file_edit_tool import FileEditTool
    from openminis.tools.file_write_tool import FileWriteTool

    FileWriteTool.execute(_args(path="/dup.txt", content="a\na\n"), "s1")
    r = FileEditTool.execute(
        _args(path="/dup.txt", old_string="a", new_string="X"), "s1"
    )
    assert not r.success
    assert "found 2 times" in r.output


def test_file_edit_replace_all(isolated_minis_home):
    from openminis.tools.file_edit_tool import FileEditTool
    from openminis.tools.file_write_tool import FileWriteTool

    FileWriteTool.execute(_args(path="/dup.txt", content="a\na\n"), "s1")
    r = FileEditTool.execute(
        _args(path="/dup.txt", old_string="a", new_string="X", replace_all=True),
        "s1",
    )
    assert r.success
    assert "2 replacement" in r.output


def test_file_edit_old_string_missing(isolated_minis_home):
    from openminis.tools.file_edit_tool import FileEditTool
    from openminis.tools.file_write_tool import FileWriteTool

    FileWriteTool.execute(_args(path="/x.txt", content="abc"), "s1")
    r = FileEditTool.execute(
        _args(path="/x.txt", old_string="zzz", new_string="X"), "s1"
    )
    assert not r.success
    assert "not found" in r.output


# ---------------------------------------------------------------------------
# memory tools
# ---------------------------------------------------------------------------
def test_memory_write_then_get(isolated_minis_home):
    from openminis.tools.memory_tools import MemoryGetTool, MemoryWriteTool

    MemoryWriteTool.execute(_args(content="## Pref\nloves Python"), "s1")
    r = MemoryGetTool.execute(_args(scope="all"), "s1")
    assert r.success
    assert "loves Python" in r.output


def test_memory_get_keyword_filter(isolated_minis_home):
    from openminis.tools.memory_tools import MemoryGetTool, MemoryWriteTool

    MemoryWriteTool.execute(
        _args(content="## Coffee\nprefers dark roast"), "s1"
    )
    MemoryWriteTool.execute(
        _args(content="## Tea\noccasionally matcha"), "s1"
    )
    r = MemoryGetTool.execute(_args(keywords="matcha", scope="all"), "s1")
    assert r.success
    assert "matcha" in r.output
    # neighbour context line should be retained
    assert "Tea" in r.output


def test_memory_write_blank_content_rejected(isolated_minis_home):
    from openminis.tools.memory_tools import MemoryWriteTool

    r = MemoryWriteTool.execute(_args(content="   "), "s1")
    assert not r.success


# ---------------------------------------------------------------------------
# browser_use stub
# ---------------------------------------------------------------------------
def test_browser_use_returns_not_ported(isolated_minis_home):
    import asyncio

    from openminis.tools.browser_use_tool import BrowserUseTool

    r = asyncio.run(
        BrowserUseTool.execute(
            _args(action="navigate", url="https://example.com"), "s1"
        )
    )
    assert r.success  # SUCCESSFUL so the agent loop doesn't retry
    assert "not yet ported" in r.output


# ---------------------------------------------------------------------------
# vision_group_resolver framing
# ---------------------------------------------------------------------------
def test_vision_framing_marks_data_untrusted():
    from openminis.tools.vision_group_resolver import VisionGroupResolver

    out = VisionGroupResolver.framed_description(
        "two tables and a bar chart", group_name="img-group"
    )
    assert "untrusted data" in out
    assert "via img-group" in out
    assert "two tables" in out
    assert out.endswith("[End of image description]")


def test_vision_failure_text_mentions_per_model_results():
    from openminis.tools.vision_group_resolver import VisionGroupResolver

    out = VisionGroupResolver.failure_text("gpt-vision: 503")
    assert "gpt-vision: 503" in out
    assert "no native vision" in out


# ---------------------------------------------------------------------------
# agent_tools registry
# ---------------------------------------------------------------------------
def test_agent_tools_default_includes_all_categories():
    from openminis.tools.agent_tools import AgentTools

    names = {d.name for d in AgentTools.make_agent_tools()}
    assert names >= {
        "shell_execute",
        "file_read",
        "file_write",
        "file_edit",
        "read_image",
        "browser_use",
        "memory_write",
        "memory_get",
    }


def test_agent_tools_drops_memory_when_disabled():
    from openminis.tools.agent_tools import AgentTools

    names = {d.name for d in AgentTools.make_agent_tools(memory_enabled=False)}
    assert "memory_write" not in names
    assert "memory_get" not in names


def test_agent_tools_drops_read_image_when_no_vision():
    from openminis.tools.agent_tools import AgentTools

    names = {
        d.name
        for d in AgentTools.make_agent_tools(
            supports_image_input=False, vision_group_configured=False
        )
    }
    assert "read_image" not in names


# ---------------------------------------------------------------------------
# catalog wiring
# ---------------------------------------------------------------------------
def test_catalog_full_set_round_trip():
    from openminis.settings.catalog import (
        TOOL_CATALOG,
        build_tool_registry,
        make_agent_tools,
    )

    ids = {t["id"] for t in TOOL_CATALOG}
    assert "file_read" in ids
    assert "memory_write" in ids
    reg = build_tool_registry(list(ids))
    assert set(reg.keys()) == ids
    def_names = {d.name for d in make_agent_tools()}
    assert def_names >= ids
