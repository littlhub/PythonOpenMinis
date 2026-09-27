"""Minimal JSON Schema validator for tool contract boundaries.

Ported from: packages/core/src/tool/json-schema.ts
Original package: @zcode/core (tool)

# PORT: upstream ``z`` is a zod schema; Python validates a subset of JSON Schema
# directly (``type`` / ``enum`` / ``const`` / ``properties`` / ``items`` /
# ``minItems`` / ``maxItems`` / ``minLength`` / ``maxLength`` /
# ``minimum`` / ``maximum`` / ``additionalProperties`` / ``oneOf`` /
# ``required``). The ``validateJsonSchemaValue`` return shape mirrors the
# upstream ``JsonSchemaValidationResult`` record so the call sites stay valid.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .tool_input_validation_issues import (
    ToolInputValidationIssue,
    ToolInputValidationPath,
    createInvalidTypeIssue,
    createInvalidUnionIssue,
    createInvalidValueIssue,
    createTooBigIssue,
    createTooSmallIssue,
    createUnrecognizedKeysIssue,
)

__all__ = [
    "validate_json_schema_value",
    "ToolInputValidationIssue",
    "ToolInputValidationPath",
]


class JsonSchemaValidationResult:
    """Mirrors ``interface JsonSchemaValidationResult``."""

    def __init__(
        self,
        valid: bool,
        errors: list[str],
        issues: list[ToolInputValidationIssue],
    ) -> None:
        self.valid = valid
        self.errors = errors
        self.issues = issues


class _ValidationCollector:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.issues: list[ToolInputValidationIssue] = []


def validate_json_schema_value(
    value: Any,
    schema: Any,
) -> JsonSchemaValidationResult:
    """Mirrors ``validateJsonSchemaValue(value, schema)``."""
    if not isinstance(schema, dict) or len(schema) == 0:
        return JsonSchemaValidationResult(True, [], [])

    collector = _ValidationCollector()
    _validate_node(value, schema, "$", [], collector)
    return JsonSchemaValidationResult(
        valid=len(collector.errors) == 0,
        errors=list(collector.errors),
        issues=list(collector.issues),
    )


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _validate_node(
    value: Any,
    schema: dict[str, Any],
    display_path: str,
    issue_path: ToolInputValidationPath,
    collector: _ValidationCollector,
) -> None:
    one_of = _as_schema_array(schema.get("oneOf"))
    if one_of is not None:
        candidates = []
        for candidate in one_of:
            c = _ValidationCollector()
            _validate_node(value, candidate, "$", [], c)
            candidates.append(c)
        matches = [c for c in candidates if len(c.errors) == 0]
        if len(matches) != 1:
            collector.errors.append(
                f"{display_path} must match exactly one oneOf schema, matched {len(matches)}"
            )
            collector.issues.append(
                createInvalidUnionIssue(
                    [[i for i in c.issues] for c in candidates],
                    issue_path,
                )
            )
        return

    value_constraint_failed = False
    if "const" in schema:
        if not _is_equal(value, schema["const"]):
            collector.errors.append(f"{display_path} must be {json.dumps(schema['const'])}")
            collector.issues.append(createInvalidValueIssue([schema["const"]], issue_path))
            value_constraint_failed = True

    enum_values = schema.get("enum")
    if isinstance(enum_values, list):
        if not any(_is_equal(item, value) for item in enum_values):
            collector.errors.append(
                f"{display_path} must be one of {', '.join(json.dumps(item) for item in enum_values)}"
            )
            collector.issues.append(createInvalidValueIssue(enum_values, issue_path))
            value_constraint_failed = True

    type_spec = schema.get("type")
    if type_spec is not None and not _matches_type(value, type_spec):
        collector.errors.append(f"{display_path} must be {_format_schema_type(type_spec)}")
        if not value_constraint_failed:
            if isinstance(type_spec, list):
                collector.issues.append(
                    createInvalidUnionIssue(
                        [[createInvalidTypeIssue(value, str(t), [])] for t in type_spec],
                        issue_path,
                    )
                )
            else:
                collector.issues.append(createInvalidTypeIssue(value, str(type_spec), issue_path))
        return

    if isinstance(value, str):
        _validate_string(value, schema, display_path, issue_path, collector)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, display_path, issue_path, collector)
    if isinstance(value, list):
        _validate_array(value, schema, display_path, issue_path, collector)
    if isinstance(value, dict):
        _validate_object(value, schema, display_path, issue_path, collector)


def _validate_object(
    value: dict[str, Any],
    schema: dict[str, Any],
    display_path: str,
    issue_path: ToolInputValidationPath,
    collector: _ValidationCollector,
) -> None:
    required = [item for item in schema.get("required", []) if isinstance(item, str)]
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required_set = set(required)

    for key in required:
        if key not in value or value[key] is None:
            collector.errors.append(f"{display_path}.{key} is required")

    for key, property_schema in properties.items():
        if not isinstance(property_schema, dict):
            continue
        if key not in value or value[key] is None:
            if key in required_set:
                collector.issues.extend(
                    _create_missing_property_issues(
                        property_schema, [*issue_path, key]
                    )
                )
            continue
        _validate_node(value[key], property_schema, f"{display_path}.{key}", [*issue_path, key], collector)

    for key in required:
        if key in properties:
            continue
        if key not in value or value[key] is None:
            collector.issues.append(createInvalidTypeIssue(None, "unknown", [*issue_path, key]))

    if schema.get("additionalProperties") is False:
        allowed = set(properties.keys())
        unexpected: list[str] = [k for k in value if k not in allowed]
        if unexpected:
            collector.errors.extend(f"{display_path}.{k} is not allowed" for k in unexpected)
            collector.issues.append(createUnrecognizedKeysIssue(unexpected, issue_path))


def _validate_array(
    value: list[Any],
    schema: dict[str, Any],
    display_path: str,
    issue_path: ToolInputValidationPath,
    collector: _ValidationCollector,
) -> None:
    minimum_issue: Optional[dict[str, Any]] = None
    if isinstance(schema.get("minItems"), (int, float)) and len(value) < schema["minItems"]:
        collector.errors.append(f"{display_path} must contain at least {schema['minItems']} items")
        minimum_issue = createTooSmallIssue("array", schema["minItems"], issue_path)

    maximum_issue: Optional[dict[str, Any]] = None
    if isinstance(schema.get("maxItems"), (int, float)) and len(value) > schema["maxItems"]:
        collector.errors.append(f"{display_path} must contain at most {schema['maxItems']} items")
        maximum_issue = createTooBigIssue("array", schema["maxItems"], issue_path)

    items_schema = schema.get("items")
    if isinstance(items_schema, dict):
        for index, item in enumerate(value):
            _validate_node(item, items_schema, f"{display_path}[{index}]", [*issue_path, index], collector)

    if minimum_issue:
        collector.issues.append(minimum_issue)
    if maximum_issue:
        collector.issues.append(maximum_issue)


def _validate_string(
    value: str,
    schema: dict[str, Any],
    display_path: str,
    issue_path: ToolInputValidationPath,
    collector: _ValidationCollector,
) -> None:
    min_len = schema.get("minLength")
    if isinstance(min_len, (int, float)) and len(value) < min_len:
        collector.errors.append(f"{display_path} must be at least {min_len} characters")
        collector.issues.append(createTooSmallIssue("string", float(min_len), issue_path))
    max_len = schema.get("maxLength")
    if isinstance(max_len, (int, float)) and len(value) > max_len:
        collector.errors.append(f"{display_path} must be at most {max_len} characters")
        collector.issues.append(createTooBigIssue("string", float(max_len), issue_path))


def _validate_number(
    value: float,
    schema: dict[str, Any],
    display_path: str,
    issue_path: ToolInputValidationPath,
    collector: _ValidationCollector,
) -> None:
    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and value < minimum:
        collector.errors.append(f"{display_path} must be >= {minimum}")
        collector.issues.append(createTooSmallIssue("number", float(minimum), issue_path))
    maximum = schema.get("maximum")
    if isinstance(maximum, (int, float)) and value > maximum:
        collector.errors.append(f"{display_path} must be <= {maximum}")
        collector.issues.append(createTooBigIssue("number", float(maximum), issue_path))


def _create_missing_property_issues(
    property_schema: Optional[dict[str, Any]],
    path: ToolInputValidationPath,
) -> list[ToolInputValidationIssue]:
    if property_schema:
        c = _ValidationCollector()
        _validate_node(None, property_schema, "$", path, c)
        if c.issues:
            return c.issues
    return [createInvalidTypeIssue(None, _infer_schema_type(property_schema), path)]


def _infer_schema_type(schema: Optional[dict[str, Any]]) -> str:
    if not schema:
        return "unknown"
    if isinstance(schema.get("type"), str):
        return schema["type"]
    if isinstance(schema.get("properties"), dict) or isinstance(schema.get("required"), list):
        return "object"
    if isinstance(schema.get("items"), dict):
        return "array"
    return "unknown"


def _matches_type(value: Any, type_spec: Any) -> bool:
    if isinstance(type_spec, list):
        return any(_matches_type(value, t) for t in type_spec)
    if type_spec == "array":
        return isinstance(value, list)
    if type_spec == "boolean":
        return isinstance(value, bool)
    if type_spec == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_spec == "null":
        return value is None
    if type_spec == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_spec == "object":
        return isinstance(value, dict)
    if type_spec == "string":
        return isinstance(value, str)
    return True


def _as_schema_array(value: Any) -> Optional[list[dict[str, Any]]]:
    if not isinstance(value, list):
        return None
    schemas = [s for s in value if isinstance(s, dict)]
    return schemas if len(schemas) == len(value) else None


def _format_schema_type(type_spec: Any) -> str:
    if isinstance(type_spec, list):
        return " or ".join(str(t) for t in type_spec)
    return str(type_spec)


def _is_equal(a: Any, b: Any) -> bool:
    # Mirrors ``Object.is`` as closely as practical for tool contract shapes.
    if a is b:
        return True
    if isinstance(a, float) and isinstance(b, float):
        if a != b:
            return True
        # Both NaN
        return a != a and b != b
    return a == b
