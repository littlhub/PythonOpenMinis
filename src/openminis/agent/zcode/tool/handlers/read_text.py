"""Read text helper — read a text range and format it for the model.

Ported from: packages/core/src/tool/handlers/read-text.ts
Original package: @zcode/core (tool/handlers)
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from .ports import READ_DEFAULT_MAX_LINES, READ_MAX_FILE_SIZE_BYTES, READ_MAX_OUTPUT_TOKENS


def _format_read_tool_result_warning(body: str) -> str:
    return f"<system-reminder>{body}</system-reminder>"


EMPTY_FILE_REMINDER = _format_read_tool_result_warning(
    "Warning: the file exists but the contents are empty."
)
READ_TOKEN_BUDGET_PARTIAL_TARGET = int(READ_MAX_OUTPUT_TOKENS * 0.85)


async def read_text_file_for_model(input: dict[str, Any]) -> dict[str, Any]:
    """Mirrors ``readTextFileForModel({...})``."""
    abort_signal = input.get("abort_signal")
    allow_partial_fallback = input.get("allow_partial_fallback")
    file_path = input["file_path"]
    file_system_port = input["file_system_port"]
    limit = input.get("limit")
    on_read: Optional[Callable[[Any], None]] = input.get("on_read")
    offset = input.get("offset")
    trace = input.get("trace")

    limit_provided = limit is not None
    read = await file_system_port.read_text_file_range(
        {
            "path": file_path,
            "offsetLine": _to_range_offset_line(offset),
            "limitLines": limit,
            "maxBytes": None if limit_provided else READ_MAX_FILE_SIZE_BYTES,
            "trace": trace,
        },
        {"signal": abort_signal},
    )
    if on_read is not None:
        on_read(read)

    return _read_text_range_result_to_output(
        {
            "allow_partial_fallback": (
                allow_partial_fallback
                if allow_partial_fallback is not None
                else _is_initial_whole_file_read(offset, limit)
            ),
            "file_path": file_path,
            "offset": offset,
            "read": read,
        }
    )


def format_read_text_output(output: dict[str, Any]) -> str:
    """Mirrors ``formatReadTextOutput(output)``."""
    partial_view_prefix = (
        f"{_format_read_tool_result_warning(output['partialViewNotice'])}\n\n"
        if output.get("partialViewNotice")
        else ""
    )

    if not output.get("content"):
        warning = (
            EMPTY_FILE_REMINDER
            if output.get("totalLines", 0) == 0
            else _format_read_tool_result_warning(
                "Warning: the file exists but is shorter than the provided offset "
                f"({output['startLine']}). The file has {output.get('totalLines', 0)} lines."
            )
        )
        return f"{partial_view_prefix}{warning}"

    # 成功文本结果的模型可见契约只包含条件提醒与带行号正文；
    # 历史安全提醒不属于当前 tool result 路径。
    # PORT: f-string 的表达式部分不能跨行（Python < 3.12 直接 SyntaxError），
    # 故先算好再拼。
    numbered = add_read_line_numbers(
        {
            "content": output["content"],
            "start_line": output["startLine"],
        }
    )
    return f"{partial_view_prefix}{numbered}"


def add_read_line_numbers(input: dict[str, Any]) -> str:
    """Mirrors ``addReadLineNumbers({content, startLine})``."""
    import re

    content = input["content"]
    start_line = input["start_line"]
    return "\n".join(
        f"{index + start_line}\t{line}"
        for index, line in enumerate(re.split(r"\r?\n", content))
    )


def _to_range_offset_line(offset: Optional[int]) -> int:
    if offset is None or offset <= 1:
        return 0
    return offset - 1


def _is_initial_whole_file_read(offset: Optional[int], limit: Optional[int]) -> bool:
    return (offset is None or offset <= 1) and limit is None


def _read_text_range_result_to_output(input: dict[str, Any]) -> dict[str, Any]:
    from ...context.utils import estimate_tokens

    allow_partial_fallback = input["allow_partial_fallback"]
    file_path = input["file_path"]
    offset = input["offset"]
    read = input["read"]

    token_count = estimate_tokens(read.content)
    if token_count > READ_MAX_OUTPUT_TOKENS:
        if not allow_partial_fallback:
            _throw_read_output_token_budget_error(token_count, file_path)
        fallback = _create_token_cap_partial_view(read, token_count)
        if fallback is not None:
            return {"filePath": file_path, "type": "text", **fallback}
        _throw_read_output_token_budget_error(token_count, file_path)

    return _normalize_read_text_output(
        {
            "type": "text",
            "filePath": file_path,
            "content": read.content,
            "numLines": read.lineCount if hasattr(read, "lineCount") else read.line_count,
            "startLine": 0 if offset == 0 else read.startLine if hasattr(read, "startLine") else read.start_line,
            "totalLines": read.totalLines if hasattr(read, "totalLines") else read.total_lines,
            "sizeBytes": read.sizeBytes if hasattr(read, "sizeBytes") else read.size_bytes,
            "bytesRead": read.bytesRead if hasattr(read, "bytesRead") else read.bytes_read,
            "truncated": read.truncated,
        }
    )


def _normalize_read_text_output(output: dict[str, Any]) -> dict[str, Any]:
    if (
        len(output["content"]) == 0
        and output["numLines"] == 0
        and output["startLine"] == 1
        and output["totalLines"] == 0
    ):
        return {**output, "numLines": 1, "totalLines": 1}
    return output


def _create_token_cap_partial_view(read: Any, token_count: int) -> Optional[dict[str, Any]]:
    import re

    content = read.content
    start_line = read.startLine if hasattr(read, "startLine") else read.start_line
    total_lines = read.totalLines if hasattr(read, "totalLines") else read.total_lines
    size_bytes = read.sizeBytes if hasattr(read, "sizeBytes") else read.size_bytes
    bytes_read = read.bytesRead if hasattr(read, "bytesRead") else read.bytes_read

    lines = re.split(r"\r?\n", content)
    if len(lines) == 0:
        return None

    line_count = _find_largest_prefix_within_token_budget(lines)
    if line_count > 0:
        prefix = "\n".join(lines[:line_count])
        end_line = start_line + line_count - 1
        next_offset = end_line + 1
        return {
            "content": prefix,
            "numLines": line_count,
            "startLine": start_line,
            "totalLines": total_lines,
            "sizeBytes": size_bytes,
            "bytesRead": bytes_read,
            "truncated": True,
            "truncatedByTokenCap": True,
            "partialViewNotice": " ".join(
                [
                    f"The file is too large to display in full ({token_count} estimated tokens, limit {READ_MAX_OUTPUT_TOKENS}).",
                    f"Showing a partial view of lines {start_line}-{end_line} of {total_lines}.",
                    f"Use Read with offset {next_offset} and limit {READ_DEFAULT_MAX_LINES} to continue, or use a search tool to find a specific section.",
                ]
            ),
        }

    char_count = _find_largest_prefix_chars_within_token_budget(content)
    if char_count <= 0:
        return None
    return {
        "content": content[:char_count],
        "numLines": 1,
        "startLine": start_line,
        "totalLines": total_lines,
        "sizeBytes": size_bytes,
        "bytesRead": bytes_read,
        "truncated": True,
        "truncatedByTokenCap": True,
        "partialViewNotice": " ".join(
            [
                f"The file is too large to display in full ({token_count} estimated tokens, limit {READ_MAX_OUTPUT_TOKENS}).",
                "Showing a partial view of the first line because the first line alone exceeds the token budget.",
                "Use Read with a smaller range or use a search tool to find a specific section.",
            ]
        ),
    }


def _find_largest_prefix_within_token_budget(lines: list[str]) -> int:
    from ...context.utils import estimate_tokens

    low = 0
    high = len(lines)
    while low < high:
        mid = -(-(low + high) // 2)  # Math.ceil((low+high)/2)
        if estimate_tokens("\n".join(lines[:mid])) <= READ_TOKEN_BUDGET_PARTIAL_TARGET:
            low = mid
        else:
            high = mid - 1
    return low


def _find_largest_prefix_chars_within_token_budget(content: str) -> int:
    from ...context.utils import estimate_tokens

    low = 0
    high = len(content)
    while low < high:
        mid = -(-(low + high) // 2)
        if estimate_tokens(content[:mid]) <= READ_TOKEN_BUDGET_PARTIAL_TARGET:
            low = mid
        else:
            high = mid - 1
    return low


def _throw_read_output_token_budget_error(token_count: int, file_path: str) -> None:
    raise create_core_error(
        CoreErrorType.TOOL_EXECUTION_FAILED,
        f"File content ({token_count} tokens) exceeds maximum allowed tokens ({READ_MAX_OUTPUT_TOKENS}). "
        "Use offset and limit parameters to read specific portions of the file, or search for specific "
        "content instead of reading the whole file.",
        {
            "context": {
                "code": "read_output_too_many_tokens",
                "filePath": file_path,
                "maxTokens": READ_MAX_OUTPUT_TOKENS,
                "tokenCount": token_count,
            },
            "recoverable": True,
        },
    )


__all__ = [
    "read_text_file_for_model",
    "format_read_text_output",
    "add_read_line_numbers",
]
