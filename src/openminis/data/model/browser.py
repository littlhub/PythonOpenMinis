"""Browser action enum, mirroring the iOS/Android browser action set.

Ported from: src/android/app/src/main/java/com/openminis/app/browser/BrowserAction.kt
Original package: com.openminis.app.browser

# PORT: forward-reference stub — only ``all_values()`` is consumed by the tool
# registries (it maps to the Kotlin ``BrowserAction.allValues`` property).
"""

from __future__ import annotations

from enum import Enum

__all__ = ["BrowserAction"]


class BrowserAction(str, Enum):
    NAVIGATE = "navigate"
    SCREENSHOT = "screenshot"
    CLICK = "click"
    TYPE = "type"
    GET_TEXT = "get_text"
    GET_READABLE = "get_readable"
    SCROLL = "scroll"
    SCROLL_AND_COLLECT = "scroll_and_collect"
    FIND_ELEMENTS = "find_elements"
    HOVER = "hover"
    GET_PAGE_INFO = "get_page_info"
    GET_BACKBONE = "get_backbone"
    EXECUTE_JS = "execute_js"
    SET_USER_AGENT = "set_user_agent"
    FETCH = "fetch"
    NEW_TAB = "new_tab"
    CLOSE_TAB = "close_tab"
    LIST_TABS = "list_tabs"
    GET_COOKIES = "get_cookies"
    SET_COOKIES = "set_cookies"
    WAIT_FOR_DOM_STABLE = "wait_for_dom_stable"
    SET_VIEWPORT = "set_viewport"

    @classmethod
    def all_values(cls) -> list[str]:
        """Kotlin: ``BrowserAction.allValues``."""
        return [a.value for a in cls]
