"""Executor validation — input / output schema validation entry points.

Ported from: packages/core/src/tool/executor/validation.ts
Original package: @zcode/core (tool/executor)

# PORT-STATUS: partial
# PORT: 三处外部依赖尚未移植（不在本次范围），按模块映射直连其未来落点：
#   1. ``validateJsonSchemaValue`` ← ``core/src/tool/json-schema.ts`` → ``..json_schema``
#   2. ``createInitialInputValidationModelContent`` ← ``core/src/tool/input-validation-model-content.ts``
#      → ``..input_validation_model_content``
#   3. ``RuntimeInputValidationIssue`` ← ``core/src/tool/input-normalization.ts`` → ``..input_normalization``
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ...contracts.errors import CoreErrorType, create_core_error, is_core_error
from ..input_normalization import RuntimeInputValidationIssue
from ..input_validation_model_content import create_initial_input_validation_model_content
from ..json_schema import validate_json_schema_value
from ..types import ToolEntry

__all__ = [
    "validate_output",
    "validate_input",
    "validate_initial_model_tool_input",
    "get_initial_input_validation_model_content",
]

INITIAL_INPUT_VALIDATION_MODEL_CONTENT_KEY = "initialInputValidationModelContent"


def validate_output(output: Any, entry: ToolEntry) -> None:
    """Mirrors ``validateOutput(output, entry)``."""
    runtimeSchemaValidation = _validate_runtime_schema(output, entry.runtime_output_schema)
    if runtimeSchemaValidation is True:
        return
    if isinstance(runtimeSchemaValidation, list):
        raise create_core_error(
            CoreErrorType.TOOL_EXECUTION_FAILED,
            "Tool output failed runtimeOutputSchema validation",
            {
                "context": {
                    "errors": runtimeSchemaValidation[:20],
                    "toolName": entry.metadata.name,
                },
                "recoverable": False,
            },
        )

    validation = validate_json_schema_value(output, entry.output_schema)
    if validation.valid:
        return

    raise create_core_error(
        CoreErrorType.TOOL_EXECUTION_FAILED,
        "Tool output failed outputSchema validation",
        {
            "context": {
                "errors": validation.errors[:20],
                "toolName": entry.metadata.name,
            },
            "recoverable": False,
        },
    )


def validate_input(input: Any, entry: ToolEntry) -> Optional[BaseException]:
    """Mirrors ``validateInput(input, entry)``."""
    validation = validate_json_schema_value(input, entry.input_schema)
    if validation.valid:
        return None

    return _create_input_validation_error(entry, validation.errors)


def validate_initial_model_tool_input(
    input: Any,
    entry: ToolEntry,
    runtimeValidationIssues: Optional[Sequence[RuntimeInputValidationIssue]] = None,
) -> Optional[BaseException]:
    """Mirrors ``validateInitialModelToolInput(input, entry, runtimeValidationIssues?)``."""
    validation = validate_json_schema_value(input, entry.input_schema)
    if validation.valid:
        return None

    # 模型原始参数的首次 schema 失败需要把具体问题回传给模型；Hook 或权限
    # 修改后的输入属于不同生命周期，不能复用这段 provider-visible 内容。
    modelContent = create_initial_input_validation_model_content(
        entry,
        validation.issues,
        runtimeValidationIssues,
    )
    return _create_input_validation_error(entry, validation.errors, modelContent)


def get_initial_input_validation_model_content(error: BaseException) -> Optional[str]:
    """Mirrors ``getInitialInputValidationModelContent(error)``."""
    if not is_core_error(error) or error.type != CoreErrorType.TOOL_EXECUTION_FAILED:
        return None
    context = getattr(error, "context", None)
    modelContent = context.get(INITIAL_INPUT_VALIDATION_MODEL_CONTENT_KEY) if isinstance(context, dict) else None
    return modelContent if isinstance(modelContent, str) else None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _validate_runtime_schema(output: Any, schema: Any) -> Any:
    """Mirrors ``validateRuntimeSchema(output, schema)``.

    # PORT: 返回 ``True``（通过）| ``list[str]``（失败消息）| ``None``（无 runtime schema）。
    """
    if not _is_safe_parse_schema(schema):
        return None
    parsed = schema.safe_parse(output)
    if _read_attr(parsed, "success") is True:
        return True
    error = _read_attr(parsed, "error")
    issues = _read_attr(error, "issues")
    if not isinstance(issues, list):
        return []
    return [_read_attr(issue, "message") for issue in issues]


def _is_safe_parse_schema(schema: Any) -> bool:
    """Mirrors ``isSafeParseSchema(schema)``.

    # PORT: TS 检查 ``typeof schema.safeParse === "function"``；Python 侧对应 ``safe_parse``
    方法（与 registry/handler 侧的 zod-like 校验器约定一致）。
    """
    return callable(getattr(schema, "safe_parse", None))


def _create_input_validation_error(
    entry: ToolEntry,
    errors: list[str],
    modelContent: Optional[str] = None,
) -> BaseException:
    return create_core_error(
        CoreErrorType.TOOL_EXECUTION_FAILED,
        "Tool input failed inputSchema validation",
        {
            "context": {
                "errors": errors[:20],
                "toolName": entry.metadata.name,
                **(
                    {}
                    if modelContent is None
                    else {INITIAL_INPUT_VALIDATION_MODEL_CONTENT_KEY: modelContent}
                ),
            },
            "recoverable": True,
        },
    )


def _read_attr(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
