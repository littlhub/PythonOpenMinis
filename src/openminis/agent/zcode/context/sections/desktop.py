"""ZCode Desktop Context section builder.

Ported from: packages/core/src/context/sections/desktop.ts
Original package: @zcode/core (context/sections)

# PORT: 原 desktop.ts 只拼装一段静态渲染/交互协议文本，并不调用 Electron API
# （没有 require("electron") / ipcRenderer 之类），因此可以整段 1:1 翻译，无需端口占位。
"""

from __future__ import annotations

from ..types import ContextSection, ContextSource
from ..utils import estimate_tokens


def build_desktop_context_section() -> ContextSection:
    return _create_desktop_section(
        "ZCode Desktop Context",
        "desktop_context",
        "\n".join(
            [
                "# ZCode Desktop Context",
                "",
                "### Files & URLs",
                "- Return local web URLs as Markdown links (e.g., [label](http://127.0.0.1:8080)).",
                "- File should be an absolute path or include the workspace folder segment so it can be resolved relative to the workspace.",
                "- Unless otherwise specified, return local file references as Markdown links (e.g., [name.md](/absolute/path/to/name.md)).",
                "",
                "### Inline Code Comments",
                "- Use the ::code-comment{...} directive when you need to attach feedback directly to specific code lines.",
                "- Emit one directive per inline comment; emit none when there are no actionable inline comments.",
                "- Required attributes: title (short label), body (one-paragraph explanation), file (path to the file).",
                "- Optional attributes: start, end (1-based line numbers), priority (0-3).",
                "- file should be an absolute path or include the workspace folder segment so it can be resolved relative to the workspace.",
                "- Keep line ranges tight; end defaults to start.",
                '- Example: ::code-comment{title="[P2] Off-by-one" body="Loop iterates past the end when length is 0." file="/path/to/foo.ts" start=10 end=11 priority=2}',
            ]
        ),
    )


def _create_desktop_section(name: str, source: ContextSource, content: str) -> ContextSection:
    return ContextSection(
        name=name,
        source=source,
        injection_target="system",
        cache_hint="stable",
        chars=len(content),
        tokens=estimate_tokens(content),
        content=content,
        preview=content[:100],
    )
