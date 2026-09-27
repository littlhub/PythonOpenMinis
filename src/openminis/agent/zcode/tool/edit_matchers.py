"""Edit matchers — old_string fuzzy matching for the Edit tool.

Ported from: packages/core/src/tool/edit-matchers.ts
Original package: @zcode/core (tool)

# PORT: This is the most error-prone part of the programming agent. Every match
# strategy, its fallback order, the ambiguity rule and the replacement semantics
# are copied line by line — nothing is "optimised". Regex differences between JS
# and Python are flagged inline with ``# PORT:`` notes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

__all__ = [
    "EditMatchStrategy",
    "EditMatchResult",
    "EditMatchMatched",
    "EditMatchAmbiguous",
    "EditMatchNotFound",
    "Candidate",
    "BROAD_MATCHERS",
    "BLOCK_ANCHOR_MIN_SIMILARITY",
    "find_edit_match",
    "normalize_line_endings",
    "normalize_replacement_for_match",
    "preserve_quote_style",
]

EditMatchStrategy = str  # "exact" | "quote_normalized" | "line_number_prefix_stripped" | "escape_normalized" | "unicode_escape_normalized" | "line_trimmed" | "indentation_flexible" | "block_anchor"


@dataclass(slots=True)
class EditMatchMatched:
    """``{ status: "matched"; actualString; strategy; candidateCount }``."""

    actual_string: str
    strategy: EditMatchStrategy
    candidate_count: int
    status: str = field(default="matched", init=False)


@dataclass(slots=True)
class EditMatchAmbiguous:
    """``{ status: "ambiguous"; strategy; candidateCount }``."""

    strategy: EditMatchStrategy
    candidate_count: int
    status: str = field(default="ambiguous", init=False)


@dataclass(slots=True)
class EditMatchNotFound:
    """``{ status: "not_found" }``."""

    status: str = field(default="not_found", init=False)


EditMatchResult = Union[EditMatchMatched, EditMatchAmbiguous, EditMatchNotFound]

BROAD_MATCHERS: set[EditMatchStrategy] = {
    "line_trimmed",
    "indentation_flexible",
    "block_anchor",
}
BLOCK_ANCHOR_MIN_SIMILARITY = 0.8
LEFT_SINGLE_CURLY_QUOTE = "\u2018"
RIGHT_SINGLE_CURLY_QUOTE = "\u2019"
LEFT_DOUBLE_CURLY_QUOTE = "\u201c"
RIGHT_DOUBLE_CURLY_QUOTE = "\u201d"


@dataclass(slots=True)
class Candidate:
    """Mirrors ``interface Candidate``."""

    value: str
    index: int


def find_edit_match(*, content: str, search: str, replace_all: bool) -> EditMatchResult:
    """Mirrors ``findEditMatch({ content, search, replaceAll })``.

    # PORT: TS takes a single object argument; Python uses keyword-only params.
    """
    exact = _collect_exact_candidates(content, search)
    if len(exact) > 0:
        return _to_match_result("exact", exact)

    strategies: list[EditMatchStrategy] = [
        "quote_normalized",
        "line_number_prefix_stripped",
        "escape_normalized",
        "unicode_escape_normalized",
        "line_trimmed",
        "indentation_flexible",
        "block_anchor",
    ]

    for strategy in strategies:
        if replace_all and strategy in BROAD_MATCHERS:
            continue
        candidates = _collect_candidates(strategy, content, search)
        if len(candidates) == 0:
            continue
        return _to_match_result(strategy, candidates)

    return EditMatchNotFound()


def normalize_line_endings(content: str) -> str:
    """Mirrors ``normalizeLineEndings(content)``."""
    return content.replace("\r\n", "\n")


def normalize_replacement_for_match(strategy: EditMatchStrategy, new_string: str) -> str:
    """Mirrors ``normalizeReplacementForMatch(strategy, newString)``."""
    return _unescape_visible_characters(new_string) if strategy == "escape_normalized" else new_string


def preserve_quote_style(old_string: str, actual_old_string: str, new_string: str) -> str:
    """Mirrors ``preserveQuoteStyle(oldString, actualOldString, newString)``."""
    if old_string == actual_old_string:
        return new_string

    result = new_string
    if (
        LEFT_DOUBLE_CURLY_QUOTE in actual_old_string
        or RIGHT_DOUBLE_CURLY_QUOTE in actual_old_string
    ):
        result = _apply_curly_double_quotes(result)
    if (
        LEFT_SINGLE_CURLY_QUOTE in actual_old_string
        or RIGHT_SINGLE_CURLY_QUOTE in actual_old_string
    ):
        result = _apply_curly_single_quotes(result)
    return result


def _collect_candidates(strategy: EditMatchStrategy, content: str, search: str) -> list[Candidate]:
    """Mirrors ``collectCandidates(strategy, content, search)``."""
    match strategy:
        case "exact":
            return _collect_exact_candidates(content, search)
        case "quote_normalized":
            return _collect_quote_normalized_candidates(content, search)
        case "line_number_prefix_stripped":
            return _collect_line_number_prefix_candidates(content, search)
        case "escape_normalized":
            return _collect_escape_normalized_candidates(content, search)
        case "unicode_escape_normalized":
            return _collect_unicode_escape_normalized_candidates(content, search)
        case "line_trimmed":
            return _collect_line_trimmed_candidates(content, search)
        case "indentation_flexible":
            return _collect_indentation_flexible_candidates(content, search)
        case "block_anchor":
            return _collect_block_anchor_candidates(content, search)
    # PORT: the TS switch is exhaustive over ``EditMatchStrategy`` and has no
    # ``default``; the explicit empty list keeps Python's ``match`` total.
    return []


def _to_match_result(strategy: EditMatchStrategy, candidates: list[Candidate]) -> EditMatchResult:
    """Mirrors ``toMatchResult(strategy, candidates)``."""
    # PORT: ``[...new Set(arr)]`` — order-preserving de-duplication.
    unique_values = list(dict.fromkeys(candidate.value for candidate in candidates))
    if len(unique_values) != 1:
        return EditMatchAmbiguous(strategy=strategy, candidate_count=len(candidates))

    return EditMatchMatched(
        actual_string=unique_values[0] if len(unique_values) > 0 else "",
        strategy=strategy,
        candidate_count=len(candidates),
    )


def _collect_exact_candidates(content: str, search: str) -> list[Candidate]:
    return _collect_substring_candidates(content, search)


def _collect_quote_normalized_candidates(content: str, search: str) -> list[Candidate]:
    return _collect_normalized_candidates(content, search, _normalize_quotes)


def _collect_line_number_prefix_candidates(content: str, search: str) -> list[Candidate]:
    stripped = _strip_read_line_number_prefixes(search)
    if stripped is None or stripped == search:
        return []
    return _collect_substring_candidates(content, stripped)


def _collect_escape_normalized_candidates(content: str, search: str) -> list[Candidate]:
    unescaped = _unescape_visible_characters(search)
    if unescaped == search:
        return []
    return _collect_substring_candidates(content, unescaped)


def _collect_unicode_escape_normalized_candidates(content: str, search: str) -> list[Candidate]:
    unescaped = _unescape_unicode_characters(search)
    if unescaped == search:
        return []

    # Unicode 转义回退允许 old_string 里的 \uXXXX 匹配文件中的真实字符。
    # 这里直接返回文件中的真实片段，后续 replacement 会按真实片段写入。
    return _collect_substring_candidates(content, unescaped)


def _collect_line_trimmed_candidates(content: str, search: str) -> list[Candidate]:
    content_lines = content.split("\n")
    search_lines = _trim_trailing_empty_line(search.split("\n"))
    if len(search_lines) == 0:
        return []

    candidates: list[Candidate] = []
    # PORT: JS ``for (let index = 0; index <= contentLines.length - searchLines.length; index += 1)``.
    for index in range(0, len(content_lines) - len(search_lines) + 1):
        block = content_lines[index : index + len(search_lines)]
        if not all(
            line.strip() == search_lines[offset].strip()
            for offset, line in enumerate(block)
        ):
            continue
        candidates.append(_block_candidate(content_lines, index, len(search_lines)))
    return candidates


def _collect_indentation_flexible_candidates(content: str, search: str) -> list[Candidate]:
    content_lines = content.split("\n")
    search_lines = _trim_trailing_empty_line(search.split("\n"))
    if len(search_lines) < 2:
        return []

    normalized_search = _remove_common_indent(search_lines)
    candidates: list[Candidate] = []
    for index in range(0, len(content_lines) - len(search_lines) + 1):
        block = content_lines[index : index + len(search_lines)]
        if _remove_common_indent(block) != normalized_search:
            continue
        candidates.append(_block_candidate(content_lines, index, len(search_lines)))
    return candidates


def _collect_block_anchor_candidates(content: str, search: str) -> list[Candidate]:
    content_lines = content.split("\n")
    search_lines = _trim_trailing_empty_line(search.split("\n"))
    if len(search_lines) < 3:
        return []

    first = search_lines[0].strip()
    last = search_lines[len(search_lines) - 1].strip()
    candidates: list[Candidate] = []
    for index in range(0, len(content_lines) - len(search_lines) + 1):
        block = content_lines[index : index + len(search_lines)]
        if block[0].strip() != first:
            continue
        if block[len(block) - 1].strip() != last:
            continue
        if _average_middle_similarity(block, search_lines) < BLOCK_ANCHOR_MIN_SIMILARITY:
            continue
        candidates.append(_block_candidate(content_lines, index, len(search_lines)))
    return candidates


def _collect_substring_candidates(content: str, search: str) -> list[Candidate]:
    if len(search) == 0:
        return []
    candidates: list[Candidate] = []
    position = 0
    while position <= len(content):
        index = content.find(search, position)
        if index == -1:
            break
        candidates.append(Candidate(value=search, index=index))
        position = index + max(len(search), 1)
    return candidates


def _collect_normalized_candidates(
    content: str,
    search: str,
    normalize: Callable[[str], str],
) -> list[Candidate]:
    normalized_content = normalize(content)
    normalized_search = normalize(search)
    candidates: list[Candidate] = []
    position = 0
    while position <= len(normalized_content):
        index = normalized_content.find(normalized_search, position)
        if index == -1:
            break
        candidates.append(Candidate(value=content[index : index + len(search)], index=index))
        position = index + max(len(normalized_search), 1)
    return candidates


def _strip_read_line_number_prefixes(search: str) -> Optional[str]:
    lines = search.split("\n")
    stripped: list[Optional[str]] = []
    for line in lines:
        # PORT: JS ``/^\d+: (.*)$/``. ``\d`` in JS is ASCII-only, so ``[0-9]`` is
        # used here to avoid Python's Unicode-digit ``\d``. ``$`` (JS: end of
        # input) is equivalent here because the input has no embedded newline.
        colon_match = re.match(r"^[0-9]+: (.*)$", line)
        if colon_match:
            stripped.append(colon_match.group(1) or "")
            continue
        # PORT: JS ``/^\d+\t(.*)$/`` — same ``\d`` / ``$`` notes as above.
        tab_match = re.match(r"^[0-9]+\t(.*)$", line)
        if tab_match:
            stripped.append(tab_match.group(1) or "")
            continue
        stripped.append(None)
    joined = [line for line in stripped if line is not None]
    if len(joined) == len(stripped):
        return "\n".join(joined)
    return None


def _unescape_visible_characters(search: str) -> str:
    """Mirrors ``unescapeVisibleCharacters(search)``.

    # PORT: JS ``/\\([ntr"'`\\$])/g`` with a replacement callback.
    """
    def replace(match: "re.Match[str]") -> str:
        token = match.group(1)
        if token == "n":
            return "\n"
        if token == "t":
            return "\t"
        if token == "r":
            return "\r"
        if token in ('"', "'", "`", "\\", "$"):
            return token
        return match.group(0)

    return re.sub(r"\\([ntr\"'`\\$])", replace, search)


def _unescape_unicode_characters(search: str) -> str:
    """Mirrors ``unescapeUnicodeCharacters(search)``.

    # PORT: JS ``/(\\\\)|\\u([0-9a-fA-F]{4})/g``. When the first alternative
    # matches, group 1 is defined and the whole match is returned unchanged;
    # otherwise the ``\\uXXXX`` escape is turned into a real character.
    # ``String.fromCharCode`` (UTF-16 code unit) is replaced by ``chr``.
    """
    def replace(match: "re.Match[str]") -> str:
        if match.group(1) is not None:
            return match.group(0)
        return chr(int(match.group(2), 16))

    return re.sub(r"(\\\\)|\\u([0-9a-fA-F]{4})", replace, search)


def _block_candidate(lines: list[str], start_line: int, line_count: int) -> Candidate:
    index = _offset_for_line(lines, start_line)
    return Candidate(
        value="\n".join(lines[start_line : start_line + line_count]),
        index=index,
    )


def _offset_for_line(lines: list[str], line_index: int) -> int:
    offset = 0
    for index in range(0, line_index):
        offset += len(lines[index]) + 1
    return offset


def _trim_trailing_empty_line(lines: list[str]) -> list[str]:
    if len(lines) > 0 and lines[len(lines) - 1] == "":
        return lines[: len(lines) - 1]
    return lines


def _remove_common_indent(lines: list[str]) -> str:
    non_empty = [line for line in lines if len(line.strip()) > 0]
    if len(non_empty) == 0:
        return "\n".join(lines)
    # PORT: JS ``line.match(/^[\t ]*/)?.[0].length ?? 0``; the ``*`` quantifier
    # always matches (possibly empty), so ``group(0)`` is always present.
    min_indent = min(len(re.match(r"[ \t]*", line).group(0)) for line in non_empty)
    return "\n".join(
        line if len(line.strip()) == 0 else line[min_indent:] for line in lines
    )


def _average_middle_similarity(actual: list[str], expected: list[str]) -> float:
    if len(actual) <= 2:
        return 1
    total = 0.0
    count = 0
    for index in range(1, len(actual) - 1):
        total += _line_similarity(actual[index].strip(), expected[index].strip())
        count += 1
    return 1 if count == 0 else total / count


def _line_similarity(left: str, right: str) -> float:
    if left == right:
        return 1
    max_length = max(len(left), len(right))
    if max_length == 0:
        return 1
    return 1 - _levenshtein(left, right) / max_length


def _levenshtein(left: str, right: str) -> int:
    previous = list(range(0, len(right) + 1))
    for left_index in range(1, len(left) + 1):
        current = [left_index]
        for right_index in range(1, len(right) + 1):
            cost = 0 if left[left_index - 1] == right[right_index - 1] else 1
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + cost,
                )
            )
        previous = current
    return previous[len(right)] if len(right) < len(previous) else 0


def _normalize_quotes(value: str) -> str:
    return (
        value.replace(LEFT_SINGLE_CURLY_QUOTE, "'")
        .replace(RIGHT_SINGLE_CURLY_QUOTE, "'")
        .replace(LEFT_DOUBLE_CURLY_QUOTE, '"')
        .replace(RIGHT_DOUBLE_CURLY_QUOTE, '"')
    )


def _apply_curly_double_quotes(value: str) -> str:
    # PORT: JS ``[...value].map((char, index, chars) => ...)`` — ``chars`` is the
    # original code-point array, re-created here.
    chars = list(value)
    result: list[str] = []
    for index, char in enumerate(chars):
        if char != '"':
            result.append(char)
            continue
        result.append(
            LEFT_DOUBLE_CURLY_QUOTE
            if _is_opening_quote_context(chars, index)
            else RIGHT_DOUBLE_CURLY_QUOTE
        )
    return "".join(result)


def _apply_curly_single_quotes(value: str) -> str:
    # PORT: JS ``chars[index - 1]`` / ``chars[index + 1]`` yield ``undefined``
    # out of bounds; Python would wrap around for a negative index, so the bounds
    # are guarded explicitly.
    chars = list(value)
    result: list[str] = []
    for index, char in enumerate(chars):
        if char != "'":
            result.append(char)
            continue
        previous = chars[index - 1] if index > 0 else None
        next_char = chars[index + 1] if index + 1 < len(chars) else None
        if _is_letter(previous) and _is_letter(next_char):
            result.append(RIGHT_SINGLE_CURLY_QUOTE)
            continue
        result.append(
            LEFT_SINGLE_CURLY_QUOTE
            if _is_opening_quote_context(chars, index)
            else RIGHT_SINGLE_CURLY_QUOTE
        )
    return "".join(result)


def _is_opening_quote_context(chars: list[str], index: int) -> bool:
    if index == 0:
        return True
    previous = chars[index - 1]
    return previous in (" ", "\t", "\n", "\r", "(", "[", "{", "\u2014", "\u2013")


def _is_letter(value: Optional[str]) -> bool:
    """Mirrors ``isLetter(value)``.

    # PORT: JS ``/\\p{L}/u.test(value)``. Python's stdlib ``re`` has no
    # ``\\p{...}`` support, so the Unicode general-category check (category name
    # starting with ``L``) reproduces the same predicate. Call sites pass a single
    # code point, so the "any character matches" semantics of ``.test`` is kept.
    """
    if value is None:
        return False
    return any(unicodedata.category(char).startswith("L") for char in value)
