"""Tool input validation issues — factory functions for the JSON-schema / runtime-schema error envelope.

Ported from: packages/core/src/tool/tool-input-validation-issues.ts
Original package: @zcode/contracts (tools, via core/src/tool)

# PORT: upstream uses ``zod`` issue shapes heavily; Python keeps the same
# record keys so the consumer (``input-validation-model-content.ts`` /
# ``json-schema.ts``) can read them without a rewrite. All values are plain
# dicts (``dict[str, Any]``) rather than typed interfaces — the schema is
# open / record-like upstream too.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

__all__ = [
    "ToolInputValidationIssue",
    "ToolInputValidationPath",
    "createInvalidTypeIssue",
    "createInvalidUnionIssue",
    "createInvalidValueIssue",
    "createTooBigIssue",
    "createTooSmallIssue",
    "createUnrecognizedKeysIssue",
    "createCustomIssue",
]

#: Mirrors the upstream union type of zod issue records (open record in practice).
ToolInputValidationIssue = dict[str, Any]
ToolInputValidationPath = list[object]  # segment: str | int


def createInvalidTypeIssue(value: Any, expected: str, path: ToolInputValidationPath) -> ToolInputValidationIssue:
    return {
        "code": "invalid_type",
        "expected": expected,
        "path": path,
        "message": f"Expected {expected}, received {type(value).__name__}" if value is not None else "Expected an object, received undefined",
    }


def createInvalidUnionIssue(candidates: Sequence[Sequence[ToolInputValidationIssue]], path: ToolInputValidationPath) -> ToolInputValidationIssue:
    return {
        "code": "invalid_union",
        "path": path,
        "unionErrors": [
            [{"code": i["code"], "message": i.get("message"), "path": i.get("path")} for i in group if isinstance(i, dict)]
            for group in candidates
        ],
    }


def createInvalidValueIssue(expected: Sequence[Any], path: ToolInputValidationPath) -> ToolInputValidationIssue:
    return {
        "code": "invalid_value",
        "path": path,
        "expected": expected,
        "message": f"Invalid value",
    }


def createTooBigIssue(origin: str, maximum: float, path: ToolInputValidationPath, extra: Optional[dict[str, Any]] = None) -> ToolInputValidationIssue:
    out: dict[str, Any] = {
        "code": "too_big",
        "origin": origin,
        "maximum": maximum,
        "path": path,
        "message": f"must be at most {maximum}",
    }
    if extra:
        out.update(extra)
    return out


def createTooSmallIssue(origin: str, minimum: float, path: ToolInputValidationPath, extra: Optional[dict[str, Any]] = None) -> ToolInputValidationIssue:
    out: dict[str, Any] = {
        "code": "too_small",
        "origin": origin,
        "minimum": minimum,
        "path": path,
        "message": f"must be at least {minimum}",
    }
    if extra:
        out.update(extra)
    return out


def createUnrecognizedKeysIssue(keys: Sequence[str], path: ToolInputValidationPath) -> ToolInputValidationIssue:
    return {
        "code": "unrecognized_keys",
        "keys": list(keys),
        "path": path,
    }


def createCustomIssue(message: str, path: ToolInputValidationPath) -> ToolInputValidationIssue:
    return {
        "code": "custom",
        "path": path,
        "message": message,
    }
