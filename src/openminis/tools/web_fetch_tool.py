"""``web_fetch`` tool — fetch a URL and return readable text.

Ported semantics from ``agent.tools.web_fetch`` (first-agent toolset): fetch an
http(s) page, strip markup and return the title + text so the model can read it
inline. Self-contained port (httpx) — no external agent framework. Kept lean:
HTML only; PDF/Office downloads are refused with a hint instead of pulling in
per-format parsers.
"""

from __future__ import annotations

import html as _html
import json
import re
from urllib.parse import urlparse

from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["WebFetchTool"]

DEFAULT_MAX_LENGTH = 20_000
MAX_LENGTH_CAP = 80_000
TIMEOUT_SECONDS = 20.0
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 OpenMinisBot/1.0"
)

_SCRIPT_RE = re.compile(r"<(script|style|noscript|template|svg)[^>]*>.*?</\1>",
                        re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html_body: str) -> str:
    """Best-effort HTML → plain text (title stripped out by the caller)."""
    body = _SCRIPT_RE.sub(" ", html_body)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.DOTALL)
    # keep line breaks between block-ish elements
    body = re.sub(r"<(br|/p|/div|/h[1-6]|/li|/tr)[^>]*>", "\n", body,
                  flags=re.IGNORECASE)
    body = _TAG_RE.sub("", body)
    body = _html.unescape(body)
    lines = [ln.strip() for ln in body.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_title(html_body: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_body,
                  re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return _html.unescape(m.group(1)).strip()


class WebFetchTool:
    """Fetch an http(s) URL and return its readable text."""

    NAME = "web_fetch"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=WebFetchTool.NAME,
            description=(
                "Fetch an http(s) URL and return its title and text content so "
                "you can read a webpage inline. Useful for documentation, README "
                "rendering or checking a page. Prefer this over curl in "
                "shell_execute for reading web pages."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, "
                    "shown to the user (e.g. 'Fetch API docs page'). Use the "
                    "same language as the user.",
                ),
                "url": AgentToolParam(
                    "string", "The http(s) URL to fetch."
                ),
                "max_length": AgentToolParam(
                    "integer",
                    "Maximum characters of text to return (default: 20000).",
                ),
            },
            required=["tool_title", "url"],
            property_ordering=["tool_title", "url", "max_length"],
        )

    @staticmethod
    async def execute(args_json: str, session_id: str) -> ToolExecutionResult:
        try:
            args = json.loads(args_json)
            tool_title = str(args.get("tool_title", WebFetchTool.NAME))
            url = str(args.get("url", "")).strip()
            max_length = max(
                1, min(int(args.get("max_length", DEFAULT_MAX_LENGTH)), MAX_LENGTH_CAP)
            )
        except (ValueError, TypeError):
            return ToolExecutionResult("Error: invalid JSON args", False,
                                       tool_title=WebFetchTool.NAME)
        if not url:
            return ToolExecutionResult("Error: 'url' is required", False,
                                       tool_title=tool_title)
        scheme = urlparse(url).scheme.lower()
        if scheme not in {"http", "https"}:
            return ToolExecutionResult(
                f"Error: only http(s) URLs are supported (got scheme '{scheme or 'none'}')",
                False, tool_title=tool_title,
            )
        try:
            import httpx
        except ImportError:  # pragma: no cover - httpx ships with the app
            return ToolExecutionResult(
                "Error: httpx 未安装，无法抓取网页", False, tool_title=tool_title
            )
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=TIMEOUT_SECONDS,
                headers={"User-Agent": UA}, limits=httpx.Limits(max_connections=4),
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "").lower()
                raw = resp.content
                text = raw.decode("utf-8", errors="replace")
                if "charset=" in ctype:
                    m = re.search(r"charset=([\w-]+)", ctype)
                    if m:
                        try:
                            text = raw.decode(m.group(1), errors="replace")
                        except LookupError:
                            pass
        except Exception as exc:  # network / HTTP errors → one readable line
            logger.debug("web_fetch failed for %s: %s", url, exc)
            return ToolExecutionResult(f"Error fetching {url}: {exc}", False,
                                       tool_title=tool_title)

        if "application/pdf" in ctype or ctype.startswith("application/octet"):
            return ToolExecutionResult(
                f"[{url} | PDF/二进制内容 — 不内联展示]",
                True, tool_title=tool_title,
            )
        if "html" in ctype or not ctype:
            title = _extract_title(text)
            content = _html_to_text(text)
            head = f"[{url}"
            if title:
                head += f" | {title[:120]}"
            head += f" | {len(content)} chars]"
            if len(content) > max_length:
                content = content[:max_length]
                head += " (truncated)"
            return ToolExecutionResult(f"{head}\n{content}", True,
                                       tool_title=tool_title)
        # plain text / json / others
        head = f"[{url} | {ctype or 'text'} | {len(text)} chars]"
        if len(text) > max_length:
            text = text[:max_length]
            head += " (truncated)"
        return ToolExecutionResult(f"{head}\n{text}", True, tool_title=tool_title)
