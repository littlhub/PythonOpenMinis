"""Tests for the first-agent toolset ports: ls / search_files / web_fetch /
web_search, plus their registration in the tool catalog + registry.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openminis.settings.catalog import TOOL_CATALOG, VALID_TOOLS, build_tool_registry
from openminis.tools.ls_tool import LsTool
from openminis.tools.search_files_tool import SearchFilesTool
from openminis.tools.web_fetch_tool import WebFetchTool
from openminis.tools.web_search_tool import WebSearchTool


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIS_HOME", str(tmp_path))
    from openminis.core import context

    context.set_app_context(
        context.AppContext(data_dir=tmp_path, cache_dir=tmp_path)
    )
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    yield ws
    context._context = None  # type: ignore[attr-defined]


def _args(**kw):
    return json.dumps(kw)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------
def test_ls_lists_root_and_relative(home):
    (home / "README.md").write_text("hi", encoding="utf-8")
    (home / "src").mkdir()
    (home / "src" / "main.py").write_text("print(1)", encoding="utf-8")

    r = _run(LsTool.execute(_args(tool_title="t"), "s"))
    assert r.success, r.output
    assert "README.md" in r.output and "src/" in r.output

    r = _run(LsTool.execute(_args(tool_title="t", path="src"), "s"))
    assert r.success and "main.py" in r.output


def test_ls_rejects_escape_and_missing(home):
    r = _run(LsTool.execute(_args(tool_title="t", path=".."), "s"))
    assert not r.success
    r = _run(LsTool.execute(_args(tool_title="t", path="/var/minis/workspace/../../x"), "s"))
    assert not r.success
    r = _run(LsTool.execute(_args(tool_title="t", path="nope"), "s"))
    assert not r.success


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------
def test_search_content_and_count(home):
    (home / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (home / "b.txt").write_text("nothing here", encoding="utf-8")

    r = _run(SearchFilesTool.execute(_args(tool_title="t", pattern="return 1"), "s"))
    assert r.success
    assert "a.py:2" in r.output and "b.txt" not in r.output

    r = _run(SearchFilesTool.execute(
        _args(tool_title="t", pattern="return", output_mode="count"), "s"))
    assert "a.py: 1" in r.output

    r = _run(SearchFilesTool.execute(
        _args(tool_title="t", pattern="nothing", file_glob="*.py"), "s"))
    assert "No matches" in r.output


def test_search_by_name_and_ignore_flags(home):
    (home / "report_2026.md").write_text("x", encoding="utf-8")
    (home / "notes").mkdir()
    (home / "notes" / ".env").write_text("SECRET=1", encoding="utf-8")

    r = _run(SearchFilesTool.execute(_args(tool_title="t", pattern="*report*", target="files"), "s"))
    assert r.success and "report_2026.md" in r.output

    # secret-looking file skipped by default, included with no_ignore
    r = _run(SearchFilesTool.execute(_args(tool_title="t", pattern="SECRET"), "s"))
    assert "No matches" in r.output and "疑似密钥" in r.output
    r = _run(SearchFilesTool.execute(
        _args(tool_title="t", pattern="SECRET", no_ignore=True), "s"))
    assert r.success and "SECRET=1" in r.output


def test_search_invalid_regex_and_escape(home):
    r = _run(SearchFilesTool.execute(_args(tool_title="t", pattern="(["), "s"))
    assert not r.success
    r = _run(SearchFilesTool.execute(_args(tool_title="t", pattern="x", path="../../x"), "s"))
    assert not r.success


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------
def test_web_fetch_requires_url_and_scheme():
    r = _run(WebFetchTool.execute(_args(tool_title="t"), "s"))
    assert not r.success
    for bad in ("file:///etc/passwd", "ftp://x", "javascript:alert(1)"):
        r = _run(WebFetchTool.execute(_args(tool_title="t", url=bad), "s"))
        assert not r.success, bad


class _HtmlHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html><head><title>Test Page</title></head><body><h1>Hello</h1><p>world &amp; more</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # pragma: no cover
        pass


def test_web_fetch_returns_title_and_text():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HtmlHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        r = _run(WebFetchTool.execute(
            _args(tool_title="t", url=f"http://127.0.0.1:{port}/page"), "s"))
        assert r.success, r.output
        assert "Test Page" in r.output
        assert "Hello" in r.output and "world & more" in r.output
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------
def test_web_search_unconfigured_and_bad_provider(home):
    r = _run(WebSearchTool.execute(_args(tool_title="t", query="fastapi"), "s"))
    assert not r.success and "web_search 未启用" in r.output

    (home.parent / "websearch.json").write_text(
        json.dumps({"provider": "nope", "apiKey": "k"}), encoding="utf-8")
    r = _run(WebSearchTool.execute(_args(tool_title="t", query="fastapi"), "s"))
    assert not r.success and "不支持" in r.output


def test_web_search_formats_results(home, monkeypatch):
    import openminis.tools.web_search_tool as mod

    (home.parent / "websearch.json").write_text(
        json.dumps({"provider": "serper", "apiKey": "k"}), encoding="utf-8")

    async def fake_search(client, query, count, api_key):  # noqa: ANN001
        return [{"title": "FastAPI docs", "url": "https://fastapi.example",
                 "snippet": "Modern web framework"}]

    monkeypatch.setattr(mod, "_search_serper", fake_search)
    r = _run(WebSearchTool.execute(_args(tool_title="t", query="fastapi"), "s"))
    assert r.success
    assert "FastAPI docs" in r.output and "fastapi.example" in r.output


# ---------------------------------------------------------------------------
# registration: catalog + registry + default identities
# ---------------------------------------------------------------------------
def test_new_tools_registered(home):
    for tid in ("ls", "search_files", "web_fetch", "web_search"):
        assert tid in VALID_TOOLS
        assert any(t["id"] == tid for t in TOOL_CATALOG)

    reg = build_tool_registry(["ls", "search_files", "web_fetch", "web_search",
                               "shell_execute", "file_read"])
    for tid in ("ls", "search_files", "web_fetch", "web_search"):
        assert tid in reg and reg[tid].definition.name == tid

    from openminis.tools.agent_tools import AgentTools

    names = {d.name for d in AgentTools.make_agent_tools()}
    assert {"ls", "search_files", "web_fetch", "web_search"} <= names

    from openminis.settings.catalog import BUILTIN_IDENTITIES

    by_id = {i.id: i for i in BUILTIN_IDENTITIES}
    for iid in ("assistant", "coder"):
        eff = by_id[iid].effective_tools()
        assert {"ls", "search_files", "web_fetch", "web_search"} <= set(eff)
