"""Memory Index Content — truncates and formats project memory index content.

Ported from: packages/core/src/memory/index-content.ts
Original package: @zcode/core (memory)
"""

from __future__ import annotations

import re

MEMORY_INDEX_LINE_LIMIT = 200
MEMORY_INDEX_CHARACTER_LIMIT = 25_000
# PORT: 原 ``/^---\s*\n[\s\S]*?---\s*\n?/u``。
LEADING_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n[\s\S]*?---\s*\n?", re.DOTALL)
# PORT: 原 ``/<!--[\s\S]*?-->/gu``。
HTML_COMMENT_PATTERN = re.compile(r"<!--[\s\S]*?-->")


def format_project_memory_index_content(content: str) -> str:
    """Mirrors ``formatProjectMemoryIndexContent(content)``."""
    without_frontmatter = LEADING_FRONTMATTER_PATTERN.sub("", content)
    return format_memory_index_content(
        strip_top_level_markdown_html_comments(without_frontmatter)
    )


def format_memory_index_content(content: str) -> str:
    """Mirrors ``formatMemoryIndexContent(content)``."""
    trimmed = content.strip()
    if not trimmed:
        return ""

    lines = trimmed.split("\n")
    line_count = len(lines)
    character_count = len(trimmed)
    line_truncated = line_count > MEMORY_INDEX_LINE_LIMIT
    character_truncated = character_count > MEMORY_INDEX_CHARACTER_LIMIT
    if not line_truncated and not character_truncated:
        return trimmed

    truncated = "\n".join(lines[:MEMORY_INDEX_LINE_LIMIT]) if line_truncated else trimmed
    if len(truncated) > MEMORY_INDEX_CHARACTER_LIMIT:
        # PORT: 原 ``lastIndexOf("\n", MEMORY_INDEX_CHARACTER_LIMIT)`` → ``rfind("\n", 0, limit)``。
        final_newline = truncated.rfind("\n", 0, MEMORY_INDEX_CHARACTER_LIMIT)
        truncated = (
            truncated[:final_newline] if final_newline > 0 else truncated[:MEMORY_INDEX_CHARACTER_LIMIT]
        )

    size_description = (
        f"{format_bytes(character_count)} (limit: {format_bytes(MEMORY_INDEX_CHARACTER_LIMIT)}) — index entries are too long"
        if character_truncated and not line_truncated
        else f"{line_count} lines (limit: {MEMORY_INDEX_LINE_LIMIT})"
        if line_truncated and not character_truncated
        else f"{line_count} lines and {format_bytes(character_count)}"
    )

    return (
        f"{truncated}\n\n> WARNING: MEMORY.md is {size_description}. "
        "Only part of it was loaded. Keep index entries to one line under ~200 chars; "
        "move detail into topic files."
    )


def format_bytes(value: int) -> str:
    """Mirrors ``formatBytes(value)``."""
    kilobytes = value / 1024
    if kilobytes < 1:
        return f"{value} bytes"
    if kilobytes < 1024:
        return f"{_trim_zero(kilobytes)}KB"
    megabytes = kilobytes / 1024
    if megabytes < 1024:
        return f"{_trim_zero(megabytes)}MB"
    return f"{_trim_zero(megabytes / 1024)}GB"


def _trim_zero(number: float) -> str:
    """PORT: 原 ``number.toFixed(1).replace(/[.]0$/, "")``。"""
    return f"{number:.1f}".replace(".0", "")


def strip_top_level_markdown_html_comments(content: str) -> str:
    """Mirrors ``stripTopLevelMarkdownHtmlComments(content)``。

    # PORT: 原 TS 用 npm ``marked`` 的 ``new Lexer({ gfm: false }).lex(content)`` 做顶层分块，
    # 仅删除顶层 HTML comment token。Python 无 ``marked`` 等价物；这里用 fence-aware 扫描近似：
    # 仅删除不在代码围栏内的顶层 ``<!-- ... -->`` 注释块（list / blockquote / paragraph /
    # code 块内的 comment 保持可见，与原语义一致）。
    """
    if "<!--" not in content:
        return content

    lines = content.split("\n")
    out: list[str] = []
    in_fence = False
    fence_marker: Optional[str] = None
    total = len(lines)
    index = 0
    while index < total:
        line = lines[index]
        fence = _fence_opening(line)
        if fence is not None:
            marker = fence
            if not in_fence:
                in_fence = True
                fence_marker = marker
                out.append(line)
                index += 1
                continue
            if marker == fence_marker:
                in_fence = False
                fence_marker = None
            out.append(line)
            index += 1
            continue
        if in_fence:
            out.append(line)
            index += 1
            continue

        stripped = line.lstrip()
        if stripped.startswith("<!--"):
            buffer = line
            closed = "-->" in line
            consumed = [line]
            j = index + 1
            while not closed and j < total:
                buffer += "\n" + lines[j]
                consumed.append(lines[j])
                if "-->" in lines[j]:
                    closed = True
                j += 1
            if closed:
                without_comments = HTML_COMMENT_PATTERN.sub("", buffer)
                # 只删除顶层 HTML comment token；其余内容保持 provider-visible。
                if len(without_comments.strip()) > 0:
                    out.append(without_comments)
                index = j
                continue
        out.append(line)
        index += 1
    return "\n".join(out)


def _fence_opening(line: str) -> Optional[str]:
    """PORT: 识别 Markdown 代码围栏起始/结束行（``` 或 ~~~），返回标记符。"""
    stripped = line.lstrip()
    if stripped.startswith("```") or stripped.startswith("~~~"):
        return stripped[:3]
    return None
