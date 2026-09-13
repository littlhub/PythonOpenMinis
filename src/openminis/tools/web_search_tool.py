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

#: 时间范围 → 各 provider 的原生参数。原先 bocha 把 freshness 硬编码成
#: ``noLimit``，于是「今天有什么新闻」拿到的是几个月前的高权重旧页面，
#: 模型再把旧闻当成今天的报给用户。现在由模型按需指定。
#: bocha: oneDay / oneWeek / oneMonth / oneYear / noLimit
#: serper: tbs=qdr:d|w|m|y（不加则为任意时间）
TIME_RANGE_BOCHA = {
    "any": "noLimit",
    "day": "oneDay",
    "week": "oneWeek",
    "month": "oneMonth",
    "year": "oneYear",
}
TIME_RANGE_SERPER = {
    "any": "",
    "day": "qdr:d",
    "week": "qdr:w",
    "month": "qdr:m",
    "year": "qdr:y",
}
TIME_RANGE_LABEL = {
    "any": "不限时间",
    "day": "近一天",
    "week": "近一周",
    "month": "近一个月",
    "year": "近一年",
}


def _normalize_time_range(raw: object) -> str:
    value = str(raw or "").strip().lower()
    aliases = {
        "today": "day", "24h": "day", "1d": "day", "d": "day",
        "week": "week", "7d": "week", "w": "week",
        "month": "month", "30d": "month", "m": "month",
        "year": "year", "365d": "year", "y": "year",
        "": "any", "none": "any", "nolimit": "any", "all": "any",
    }
    value = aliases.get(value, value)
    return value if value in TIME_RANGE_BOCHA else "any"


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


async def _search_serper(client, query: str, count: int, api_key: str,
                         time_range: str = "any") -> list[dict]:
    payload: dict = {"q": query, "num": count}
    tbs = TIME_RANGE_SERPER.get(time_range, "")
    if tbs:
        payload["tbs"] = tbs
    resp = await client.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json=payload,
    )
    resp.raise_for_status()
    body = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("link", ""),
         "snippet": r.get("snippet", ""), "date": r.get("date", "")}
        for r in body.get("organic") or []
    ]


async def _search_bocha(client, query: str, count: int, api_key: str,
                        time_range: str = "any") -> list[dict]:
    resp = await client.post(
        "https://api.bochaai.com/v1/web-search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "query": query,
            "count": count,
            "freshness": TIME_RANGE_BOCHA.get(time_range, "noLimit"),
        },
    )
    resp.raise_for_status()
    body = resp.json()
    pages = ((body.get("data") or {}).get("webPages") or {}).get("value") or []
    return [
        {"title": r.get("name", ""), "url": r.get("url", ""),
         "snippet": r.get("snippet", ""),
         "date": r.get("datePublished") or r.get("dateLastCrawled") or ""}
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
                "results (title, url, snippet, publish date). Requires the search "
                "provider to be configured in websearch.json — when it is not, the "
                "tool reports that instead of pretending to search. "
                "IMPORTANT: for 'today / latest / this week' questions you MUST pass "
                "time_range (day / week / month), otherwise the engine may return "
                "months-old pages."
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
                "time_range": AgentToolParam(
                    "string",
                    "Time window: day | week | month | year | any (default: any). "
                    "必须按用户问的时效选择：问「今天/今日/最新」用 day，问「本周/这几天」"
                    "用 week，问「这个月」用 month；只有查定义、常识、历史资料才用 any。",
                ),
                "provider": AgentToolParam(
                    "string",
                    f"Override the configured provider: {', '.join(SUPPORTED)}.",
                ),
            },
            required=["tool_title", "query"],
            property_ordering=["tool_title", "query", "time_range", "count", "provider"],
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
        time_range = _normalize_time_range(args.get("time_range"))

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
                    results = await _search_serper(client, query, count, api_key,
                                                   time_range)
                else:
                    results = await _search_bocha(client, query, count, api_key,
                                                  time_range)
        except Exception as exc:
            logger.debug("web_search(%s) failed: %s", provider, exc)
            return ToolExecutionResult(
                f"web_search 调用失败（{provider}）: {exc}", False,
                tool_title=tool_title,
            )
        today = _today()
        if not results:
            return ToolExecutionResult(
                f"No results for {query!r}（时间范围：{TIME_RANGE_LABEL[time_range]}，"
                f"今天 {today}）",
                True, tool_title=tool_title,
            )
        lines = []
        for i, r in enumerate(results, 1):
            title = (r.get("title") or "").strip() or r.get("url", "")
            date = (r.get("date") or "").strip()
            stamp = f"[{date[:10]}] " if date else ""
            lines.append(
                f"{i}. {stamp}{title}\n   {r.get('url', '')}\n   "
                f"{(r.get('snippet') or '').strip()}"
            )
        return ToolExecutionResult(
            f"[{provider} · {len(results)} results for {query!r} · "
            f"时间范围：{TIME_RANGE_LABEL[time_range]} · 今天是 {today}]\n"
            "（每条结果前的 [日期] 是该页面的发布/更新日期；"
            "若日期早于今天，回答时必须如实标注，不要当成今日消息。）\n"
            + "\n".join(lines),
            True, tool_title=tool_title,
        )


def _today() -> str:
    """本机当前日期（检索结果里的日期要和它对齐）。"""
    from datetime import datetime

    return f"{datetime.now().strftime('%Y-%m-%d')}（{_WEEKDAY_CN[datetime.now().weekday()]}）"


_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
