"""``web_search`` tool — search the web through a configurable provider.

Ported semantics from ``agent.tools.web_search`` (first-agent toolset): the
tool is provider-backed and degrades gracefully when unconfigured. This port
ships two thin adapters — **serper** (https://serper.dev) and
**bocha** (https://bochaai.com, 博查) — chosen for their tiny request shapes.

Configuration lives in ``<data_dir>/websearch.json`` (``data_dir`` is
``MINIS_HOME`` or ``~/openminis``). Schema::

    {"provider": "serper", "apiKey": "..."}

The key never appears in tool output or logs. Unconfigured calls return a clear
message telling the agent the tool is unavailable, instead of burning turns.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from ..core.context import app_context
from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["WebSearchTool"]

CONFIG_FILENAME = "websearch.json"
SUPPORTED = ("serper", "bocha")
DEFAULT_COUNT = 5
MAX_COUNT = 10
TIMEOUT_SECONDS = 15.0


def _config_path() -> Path:
    return app_context().data_dir / CONFIG_FILENAME


def _load_config() -> dict:
    path = _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


async def _search_serper(client, query: str, count: int, api_key: str) -> list[dict]:
    resp = await client.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": count},
    )
    resp.raise_for_status()
    body = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("link", ""),
         "snippet": r.get("snippet", "")}
        for r in body.get("organic") or []
    ]


async def _search_bocha(client, query: str, count: int, api_key: str) -> list[dict]:
    resp = await client.post(
        "https://api.bochaai.com/v1/web-search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"query": query, "count": count, "freshness": "noLimit"},
    )
    resp.raise_for_status()
    body = resp.json()
    pages = ((body.get("data") or {}).get("webPages") or {}).get("value") or []
    return [
        {"title": r.get("name", ""), "url": r.get("url", ""),
         "snippet": r.get("snippet", "")}
        for r in pages
    ]


class WebSearchTool:
    """Search the web via a configured provider (serper / bocha)."""

    NAME = "web_search"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=WebSearchTool.NAME,
            description=(
                "Search the web for up-to-date information and return ranked "
                "results (title, url, snippet). Requires the search provider to "
                "be configured in websearch.json — when it is not, the tool "
                "reports that instead of pretending to search."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, "
                    "shown to the user (e.g. 'Search latest fastapi release'). "
                    "Use the same language as the user.",
                ),
                "query": AgentToolParam(
                    "string", "The search query (plain keywords)."
                ),
                "count": AgentToolParam(
                    "integer", f"Number of results to return (default: {DEFAULT_COUNT}, max {MAX_COUNT})."
                ),
                "provider": AgentToolParam(
                    "string",
                    f"Override the configured provider: {', '.join(SUPPORTED)}.",
                ),
            },
            required=["tool_title", "query"],
            property_ordering=["tool_title", "query", "count", "provider"],
        )

    @staticmethod
    async def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
            tool_title = str(args.get("tool_title", WebSearchTool.NAME))
            query = str(args.get("query", "")).strip()
            count = max(1, min(int(args.get("count", DEFAULT_COUNT)), MAX_COUNT))
        except (ValueError, TypeError):
            return ToolExecutionResult("Error: invalid JSON args", False,
                                       tool_title=WebSearchTool.NAME)
        if not query:
            return ToolExecutionResult("Error: 'query' is required", False,
                                       tool_title=tool_title)

        cfg = _load_config()
        provider = str(args.get("provider") or cfg.get("provider") or "").strip().lower()
        api_key = str(cfg.get("apiKey") or "").strip()
        if not provider or not api_key:
            path = _config_path()
            return ToolExecutionResult(
                f"web_search 未启用：在 {path} 写入 "
                '{"provider": "serper 或 bocha", "apiKey": "…"} 后重试',
                False, tool_title=tool_title,
            )
        if provider not in SUPPORTED:
            return ToolExecutionResult(
                f"Error: 不支持的搜索 provider '{provider}'（支持: {', '.join(SUPPORTED)}）",
                False, tool_title=tool_title,
            )
        try:
            import httpx
        except ImportError:  # pragma: no cover
            return ToolExecutionResult(
                "Error: httpx 未安装，无法联网搜索", False, tool_title=tool_title
            )
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SECONDS,
                headers={"User-Agent": "OpenMinisBot/1.0"},
                limits=httpx.Limits(max_connections=4),
            ) as client:
                if provider == "serper":
                    results = await _search_serper(client, query, count, api_key)
                else:
                    results = await _search_bocha(client, query, count, api_key)
        except Exception as exc:
            logger.debug("web_search(%s) failed: %s", provider, exc)
            return ToolExecutionResult(
                f"web_search 调用失败（{provider}）: {exc}", False,
                tool_title=tool_title,
            )
        if not results:
            return ToolExecutionResult(f"No results for {query!r}", True,
                                       tool_title=tool_title)
        lines = []
        for i, r in enumerate(results, 1):
            title = (r.get("title") or "").strip() or r.get("url", "")
            lines.append(f"{i}. {title}\n   {r.get('url', '')}\n   {(r.get('snippet') or '').strip()}")
        return ToolExecutionResult(
            f"[{provider} · {len(results)} results for {query!r}]\n" + "\n".join(lines),
            True, tool_title=tool_title,
        )
