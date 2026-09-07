"""browser_use tool.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/BrowserUseTool.kt
Original package: com.openminis.app.tools

PORT: Kotlin has a full browser subsystem under ``com.openminis.app.browser``
that does the actual screenshotting / DOM scraping — the Python port does
not include a headless browser yet, so ``execute`` returns a structured
"not yet ported" message instead of pretending to act. The schema is
complete so the LLM still gets the right tool surface and can be re-pointed
at a real driver once one lands.
"""

from __future__ import annotations

import json
from typing import Optional

from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from .tool_execution_result import ToolExecutionResult

__all__ = ["BrowserUseTool", "BROWSER_ACTIONS"]


# Aligned with the Kotlin BrowserAction enum. Kept as a tuple so the order
# matches the enum declaration in the original.
BROWSER_ACTIONS: tuple[str, ...] = (
    "navigate",
    "screenshot",
    "click",
    "type",
    "get_text",
    "get_readable",
    "scroll",
    "scroll_and_collect",
    "find_elements",
    "get_page_info",
    "get_backbone",
    "execute_js",
    "fetch",
    "hover",
    "set_user_agent",
    "set_viewport",
    "get_cookies",
    "set_cookies",
    "wait_for_dom_stable",
    "new_tab",
    "close_tab",
    "list_tabs",
)


class BrowserUseTool:
    """Kotlin: ``object BrowserUseTool`` — schema-only stub.

    The schema is registered so the LLM can call ``browser_use``; the executor
    currently rejects every action with a clear "engine not ported" message.
    Once a Python browser driver lands, swap ``execute`` for a real dispatcher
    and the rest of the agent loop is unchanged.
    """

    NAME = "browser_use"

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=BrowserUseTool.NAME,
            description=(
                "Control a web browser with up to 3 tabs. Do NOT use this tool "
                "for minis:// action URLs — those are app deep links, use "
                "Markdown links in chat instead. The browser supports both web "
                "URLs and minis:// resource URLs. Use navigate to open URLs, "
                "screenshot to see the page (returns an image), click/type to "
                "interact with elements, get_text/get_readable to extract "
                "content, scroll to navigate long pages, find_elements to "
                "discover interactive elements, get_page_info for page "
                "metadata, get_backbone to get a structural overview of the "
                "page DOM as a simplified tree, fetch to download files using "
                "the page's session, and new_tab / close_tab / list_tabs to "
                "manage tabs. NOTE: the browser engine is not yet ported to "
                "the Python runtime — calls return a clear 'engine not "
                "ported' message rather than silently no-op'ing."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this tool call does, "
                    "shown to the user (e.g. 'Open Wikipedia homepage', 'Take "
                    "screenshot of current page'). Use the same language as "
                    "the user.",
                ),
                "action": AgentToolParam(
                    "string",
                    "The browser action to perform",
                    enum_values=list(BROWSER_ACTIONS),
                ),
                "url": AgentToolParam(
                    "string",
                    "URL to navigate to (for navigate action) or resource to "
                    "download (for fetch action)",
                ),
                "selector": AgentToolParam(
                    "string",
                    "CSS selector for targeting elements (click, type, "
                    "get_text, scroll, hover, find_elements). For scroll: "
                    "specify a scrollable container to scroll; if omitted, "
                    "auto-detects the best scrollable element.",
                ),
                "text": AgentToolParam(
                    "string", "Text to type (for type action)"
                ),
                "coordinate_x": AgentToolParam(
                    "integer", "X coordinate for click (alternative to selector)"
                ),
                "coordinate_y": AgentToolParam(
                    "integer", "Y coordinate for click (alternative to selector)"
                ),
                "direction": AgentToolParam(
                    "string", "Scroll direction", enum_values=["up", "down"]
                ),
                "amount": AgentToolParam(
                    "integer", "Scroll amount in pixels (default: 500)"
                ),
                "script": AgentToolParam(
                    "string",
                    "JavaScript code to execute (for execute_js action). The "
                    "script runs inside an async function wrapper — `await` "
                    "and top-level `return` are both supported.",
                ),
                "user_agent": AgentToolParam(
                    "string",
                    "User agent profile to switch to",
                    enum_values=["desktop_chrome", "mobile_chrome"],
                ),
                "max_depth": AgentToolParam(
                    "integer", "Maximum tree depth for get_backbone (default: 5)"
                ),
                "scroll_count": AgentToolParam(
                    "integer",
                    "Number of scroll steps for scroll_and_collect (default: 10, max: 20). "
                    "Each step scrolls by 'amount' pixels and waits for new content.",
                ),
                "item_selector": AgentToolParam(
                    "string",
                    "CSS selector for individual content items in "
                    "scroll_and_collect (e.g. 'article', "
                    "'[data-testid=\"tweet\"]'). If omitted, auto-detects "
                    "repeated elements.",
                ),
                "tab_id": AgentToolParam(
                    "integer",
                    "Target tab ID (optional, defaults to most recently used "
                    "tab). Use list_tabs to see available tabs.",
                ),
                "keywords": AgentToolParam(
                    "string",
                    "Filter cookies by name (for get_cookies). A "
                    "space-separated string or array of strings.",
                ),
                "fuzzy": AgentToolParam(
                    "boolean",
                    "Whether keyword matching is fuzzy (contains-all) or "
                    "exact-any (for get_cookies, default: true).",
                ),
                "cookies": AgentToolParam(
                    "string",
                    "For set_cookies: a JSON array of cookie objects to write. "
                    "Each object: {name, value, domain?, path?, secure?, "
                    "http_only?, expires?}",
                ),
                "timeout": AgentToolParam(
                    "integer",
                    "Timeout in seconds for wait_for_dom_stable (default: 10)",
                ),
                "viewport_width": AgentToolParam(
                    "integer",
                    "Viewport width in CSS pixels for set_viewport (e.g. 1920)",
                ),
                "viewport_height": AgentToolParam(
                    "integer",
                    "Viewport height in CSS pixels for set_viewport (e.g. 1080)",
                ),
                "reset": AgentToolParam(
                    "boolean",
                    "For set_viewport: when true, clear the session-level "
                    "viewport override and fall back to the global browser "
                    "setting.",
                ),
            },
            required=["tool_title", "action"],
            property_ordering=[
                "tool_title",
                "action",
                "tab_id",
                "url",
                "selector",
                "text",
                "coordinate_x",
                "coordinate_y",
                "direction",
                "amount",
                "scroll_count",
                "item_selector",
                "script",
                "user_agent",
                "max_depth",
                "keywords",
                "fuzzy",
                "cookies",
                "timeout",
                "viewport_width",
                "viewport_height",
                "reset",
            ],
        )

    @staticmethod
    async def execute(
        args_json: str,
        session_id: str,
        _line_callback=None,
    ) -> ToolExecutionResult:
        """Stub executor — the browser subsystem is not yet ported to Python.

        Returns a SUCCESSFUL tool result with a clear "not ported" message so
        the agent loop does not enter a retry storm. Once a real driver lands
        this method should be replaced with an async dispatch table.
        """
        try:
            args = json.loads(args_json)
        except ValueError as e:
            return ToolExecutionResult(
                f"Error: invalid JSON args: {e}", True, tool_title=BrowserUseTool.NAME
            )

        action = str(args.get("action", ""))
        tool_title = str(args.get("tool_title", BrowserUseTool.NAME))
        hint = (
            f"Browser engine is not yet ported to the Python runtime. "
            f"Action '{action or '?'}' was NOT executed. "
            f"Install a headless browser driver (e.g. Playwright) and wire it "
            f"to BrowserUseTool.execute to enable this tool."
        )
        return ToolExecutionResult(hint, True, tool_title=tool_title)
