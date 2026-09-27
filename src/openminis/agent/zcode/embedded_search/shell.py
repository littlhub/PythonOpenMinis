"""Embedded search Bash prelude — when to inject ``find()`` / ``grep()`` aliases.

Ported from: packages/core/src/embedded-search/shell.ts
Original package: @zcode/core (embedded-search)
"""

from __future__ import annotations

from typing import Any, Optional

from ..contracts.execution import ExecutionShellSelection

__all__ = [
    "ENABLE_EMBEDDED_SEARCH_BASH_PRELUDE",
    "should_inject_embedded_search_bash_prelude",
    "supports_embedded_search_shell_selection",
]

# provider-visible embedded branch 已默认开启；执行层仅在明确支持 POSIX
# shell function 的 session shell 中注入 find()/grep() alias。
ENABLE_EMBEDDED_SEARCH_BASH_PRELUDE = True


def should_inject_embedded_search_bash_prelude() -> bool:
    """Mirrors ``shouldInjectEmbeddedSearchBashPrelude()``."""
    return ENABLE_EMBEDDED_SEARCH_BASH_PRELUDE


def supports_embedded_search_shell_selection(
    selection: Optional[ExecutionShellSelection],
) -> bool:
    """Mirrors ``supportsEmbeddedSearchShellSelection(selection)``.

    PORT: TS ``selection?.dialect === "posix" || selection?.dialect === "git-bash"``。
    ``ExecutionShellSelection`` 在本包是 dataclass，但宿主注入的选择对象可能是裸
    dict/对象字面量，故按字段名读取（``dialect`` 在两种形态下同名）。
    """
    if selection is None:
        return False
    dialect = _read_dialect(selection)
    return dialect == "posix" or dialect == "git-bash"


def _read_dialect(selection: Any) -> Optional[str]:
    if isinstance(selection, dict):
        value = selection.get("dialect")
    else:
        value = getattr(selection, "dialect", None)
    return value if isinstance(value, str) else None
