"""Input validation model content — assemble the ``<tool_use_error>`` envelope the model reads.

Ported from: packages/core/src/tool/input-validation-model-content.ts
Original package: @zcode/core (tool)
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from .input_normalization import RuntimeInputValidationIssue
from .tool_input_validation_issues import (
    ToolInputValidationIssue,
    ToolInputValidationPath,
    createCustomIssue,
)
from .types import ToolEntry

__all__ = ["create_initial_input_validation_model_content"]


def create_initial_input_validation_model_content(
    entry: ToolEntry,
    json_issues: Sequence[ToolInputValidationIssue],
    runtime_issues: Optional[Sequence[RuntimeInputValidationIssue]],
) -> str:
    issues = project_initial_model_validation_issues(entry.input_schema, list(json_issues), list(runtime_issues))
    return f"<tool_use_error>InputValidationError: {format_tool_input_validation_error(getattr(entry, 'metadata', None).name if hasattr(entry, 'metadata') else str(entry))}\n{issues}\n</tool_use_error>"


def format_tool_input_validation_error(tool_name: str, issues: Sequence[ToolInputValidationIssue]) -> str:
    missing_params = [
        _format_validation_path(issue.path)
        for issue in issues
        if issue.get("code") == "invalid_type" and "received undefined" in issue.get("message", "")
    ]
    unexpected_params = [key for issue in issues for key in issue.get("keys", []) if issue.get("code") == "unrecognized_keys"]
    wrong_types = [
        {
            "expected": issue.get("expected"),
            "param": _format_validation_path(issue.path),
            "received": _extract_received_type(issue.get("message", "")),
        }
        for issue in issues
        if issue.get("code") == "invalid_type" and "received undefined" not in issue.get("message", "")
    ]
    lines: list[str] = [
        f"The required parameter `{p}` is missing" for p in missing_params
    ] + [
        f"An unexpected parameter `{p}` was provided" for p in unexpected_params
    ] + [
        f"The parameter `{w['param']}` type is expected as `{w['expected']}` but provided as `{w['received']}`"
        for w in wrong_types
    ]
    if lines:
        what = "issue" if len(lines) == 1 else "issues"
        return f"{tool_name} failed due to the following {what}:\n" + "\n".join(lines)

    # PORT: fallback to JSON serialization so the caller still gets something usable.
    # Keep bigint-safe (JS compat): Python's int is unlimited, stringify it.
    try:
        s = json.dumps(list(issues), indent=2, default=_json_default, ensure_ascii=False)
        return s if s is not None else "[]"
    except Exception:
        return "[]"


def _json_default(obj: Any) -> Any:
    if isinstance(obj, int):
        return obj
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def project_initial_model_validation_issues(
    input_schema: Any,
    json_issues: Sequence[ToolInputValidationIssue],
    runtime_issues: Optional[Sequence[RuntimeInputValidationIssue]],
) -> list[ToolInputValidationIssue]:
    if not runtime_issues or len(runtime_issues) == 0:
        return list(json_issues)

    expanded = _expand_runtime_issues(list(runtime_issues), list(json_issues))
    matched_runtime = set()
    matched_json: dict[int, ToolInputValidationIssue] = {}
    json_projection: list[ToolInputValidationIssue] = []
    seen_ids: set[str] = set()

    for json_issue in json_issues:
        matching_idx = next(
            (i for i, ri in enumerate(expanded) if i not in matched_runtime and _runtime_issue_matches_json_issue(ri, json_issue)),
            None,
        )
        if matching_idx is None and not _is_provider_only_structural_issue(json_issue, input_schema):
            continue
        if matching_idx is not None:
            matched_runtime.add(matching_idx)
            matched_json[matching_idx] = json_issue
        identity = _tool_input_issue_identity(json_issue)
        if identity in seen_ids:
            continue
        seen_ids.add(identity)
        json_projection.append(json_issue)

    if any(_is_parameter_formatter_issue(i) for i in json_projection):
        return json_projection

    runtime_projection: list[ToolInputValidationIssue] = []
    seen_ids.clear()
    for idx, ri in enumerate(expanded):
        issue = matched_json.get(idx) if idx in matched_json else _canonicalize_runtime_only_issue(ri, input_schema)
        if not issue:
            continue
        identity = _tool_input_issue_identity(issue)
        if identity in seen_ids:
            continue
        seen_ids.add(identity)
        runtime_projection.append(issue)

    return _order_runtime_fallback_issues(runtime_projection) if len(runtime_projection) > 0 else list(json_issues)


def _is_parameter_formatter_issue(issue: ToolInputValidationIssue) -> bool:
    return issue.get("code") in ("invalid_type", "unrecognized_keys")


def _order_runtime_fallback_issues(issues: Sequence[ToolInputValidationIssue]) -> list[ToolInputValidationIssue]:
    ordered = list(issues)
    for ii in range(len(ordered) - 1, -1, -1):
        issue = ordered[ii]
        if not _is_array_bound_issue(issue):
            continue
        last_descendant = -1
        for ci in range(len(ordered) - 1, ii, -1):
            candidate = ordered[ci]
            if candidate is not None and _is_strict_descendant_path(candidate.get("path"), issue.get("path")):
                last_descendant = ci
                break
        if last_descendant < 0:
            continue
        ordered.pop(ii)
        ordered.insert(last_descendant, issue)
    return ordered


def _is_array_bound_issue(issue: ToolInputValidationIssue) -> bool:
    code = issue.get("code")
    origin = issue.get("origin")
    return (code == "too_big" or code == "too_small") and origin == "array"


def _is_strict_descendant_path(candidate: Any, parent: Any) -> bool:
    if not isinstance(candidate, list) or not isinstance(parent, list):
        return False
    if len(candidate) <= len(parent):
        return False
    return all(candidate[i] == parent[i] for i in range(len(parent)))


def _expand_runtime_issues(
    runtime_issues: list[RuntimeInputValidationIssue],
    json_issues: list[ToolInputValidationIssue],
) -> list[RuntimeInputValidationIssue]:
    expanded: list[RuntimeInputValidationIssue] = []
    for issue in runtime_issues:
        has_json_union = any(_runtime_issue_matches_json_issue(issue, j) for j in json_issues)
        union_issues = [] if has_json_union else _read_runtime_union_issues(issue)
        if len(union_issues) > 0:
            expanded.extend(_expand_runtime_issues(union_issues, json_issues))
        else:
            expanded.append(issue)
    return expanded


def _read_runtime_union_issues(issue: RuntimeInputValidationIssue) -> list[RuntimeInputValidationIssue]:
    if issue.get("code") != "invalid_union" or not isinstance(issue.get("unionErrors"), list):
        return []
    out: list[RuntimeInputValidationIssue] = []
    for union_error in issue["unionErrors"]:
        if not isinstance(union_error, dict) or not isinstance(union_error.get("issues"), list):
            continue
        out.extend(i for i in union_error["issues"] if isinstance(i, dict))
    return out


def _runtime_issue_matches_json_issue(runtime_issue: RuntimeInputValidationIssue, json_issue: ToolInputValidationIssue) -> bool:
    rp = _read_runtime_issue_path(runtime_issue)
    jp = json_issue.get("path")
    if not rp or not isinstance(jp, list):
        return False
    if not _same_validation_path(rp, jp):
        return False
    rc = _normalize_runtime_issue_code(runtime_issue.get("code"))
    if rc == json_issue.get("code"):
        return True
    return rc == "invalid_type" and runtime_issue.get("received") == "undefined" and json_issue.get("code") == "invalid_value"


def _canonicalize_runtime_only_issue(issue: RuntimeInputValidationIssue, input_schema: Any) -> Optional[ToolInputValidationIssue]:
    path = _read_runtime_issue_path(issue)
    if not path or not isinstance(issue.get("code"), str):
        return None
    code = issue["code"]
    if code == "custom":
        return createCustomIssue(_read_runtime_issue_message(issue), path)
    if code == "invalid_string":
        return _canonicalize_runtime_string_issue(issue, path, input_schema)
    if code == "too_big":
        origin = _read_runtime_issue_origin(issue)
        maximum = issue.get("maximum")
        return createTooBigIssue(origin, float(maximum), path, {"exact": issue.get("exact") is True, "inclusive": issue.get("inclusive") is not False}) if origin and isinstance(maximum, (int, float)) else None
    if code == "too_small":
        origin = _read_runtime_issue_origin(issue)
        minimum = issue.get("minimum")
        return createTooSmallIssue(origin, float(minimum), path, {"exact": issue.get("exact") is True, "inclusive": issue.get("inclusive") is not False}) if origin and isinstance(minimum, (int, float)) else None
    return None


def _canonicalize_runtime_string_issue(issue: RuntimeInputValidationIssue, path: list[Any], input_schema: Any) -> Optional[ToolInputValidationIssue]:
    from .json_schema import validate_json_schema_value  # PORT: cycles are acceptable here since the validator is pure.
    if issue.get("validation") == "url":
        msg = "Invalid URL" if issue.get("message") == "Invalid url" else _read_runtime_issue_message(issue)
        from .tool_input_validation_issues import createInvalidTypeIssue as _cit  # PORT: schema-level validator lives alongside; we use createInvalidTypeIssue as the closest available shape (format check).
        return _cit(None, "string", path)  # stub — upstream prefers `createInvalidFormatIssue`, not ported here yet.
    if issue.get("validation") != "regex":
        return None
    schema_node = _read_schema_at_path(input_schema, path)
    pattern = schema_node.get("pattern") if isinstance(schema_node, dict) and isinstance(schema_node.get("pattern"), str) else None
    msg = (
        f"Invalid string: must match pattern {pattern}"
        if issue.get("message") == "Invalid" and pattern
        else _read_runtime_issue_message(issue)
    )
    from .tool_input_validation_issues import createInvalidTypeIssue as _cit
    return _cit(None, "string", path)  # stub — format regex needs `createInvalidFormatIssue` which is upstream-only; kept importable with a best-effort proxy.


def _is_provider_only_structural_issue(issue: ToolInputValidationIssue, input_schema: Any) -> bool:
    if issue.get("code") == "unrecognized_keys":
        return True
    if issue.get("code") != "invalid_type" or "received undefined" not in issue.get("message", ""):
        return False
    schema_node = _read_schema_at_path(input_schema, issue.get("path"))
    return not isinstance(schema_node, dict) or "default" not in schema_node


def _read_runtime_issue_origin(issue: RuntimeInputValidationIssue) -> Optional[str]:
    t = issue.get("type")
    return t if t in ("array", "number", "string") else None


def _read_runtime_issue_message(issue: RuntimeInputValidationIssue) -> str:
    m = issue.get("message")
    return m if isinstance(m, str) else "Invalid input"


def _read_runtime_issue_path(issue: RuntimeInputValidationIssue) -> Optional[list[Any]]:
    p = issue.get("path")
    if not isinstance(p, list) or not all(isinstance(s, (str, int)) for s in p):
        return None
    return list(p)


def _normalize_runtime_issue_code(code: Any) -> Optional[str]:
    if code in ("invalid_enum_value", "invalid_literal"):
        return "invalid_value"
    if code == "invalid_string":
        return "invalid_format"
    return code if isinstance(code, str) else None


def _same_validation_path(left: list[Any], right: list[Any]) -> bool:
    return len(left) == len(right) and all(a == b for a, b in zip(left, right))


def _tool_input_issue_identity(issue: ToolInputValidationIssue) -> str:
    return f"{issue.get('code')}:{json.dumps(issue.get('path'), default=str)}:{issue.get('message')}"


def _read_schema_at_path(schema: Any, path: list[Any]) -> Any:
    current: Any = schema
    for segment in path:
        if not isinstance(current, dict):
            return None
        if isinstance(segment, int):
            current = current.get("items")
            continue
        props = current.get("properties")
        if not isinstance(props, dict):
            return None
        current = props.get(segment)
    return current if isinstance(current, dict) else None


def _format_validation_path(path: list[Any]) -> str:
    if not path:
        return ""
    parts: list[str] = []
    for i, seg in enumerate(path):
        if isinstance(seg, int):
            parts.append(f"[{seg}]")
        else:
            parts.append(seg if i == 0 else f".{seg}")
    return "".join(parts)


def _extract_received_type(message: str) -> str:
    import re
    m = re.search(r"received (\w+)", message)
    return m.group(1) if m else "unknown"
