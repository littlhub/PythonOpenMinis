"""Result serialization — budget-aware tool-output serialization to model content.

Ported from: packages/core/src/tool/executor/result-serialization.ts
Original package: @zcode/core (tool/executor)

# PORT-STATUS: partial
# ``@zcode/contracts`` 的 ``CoreErrorType`` / ``createCoreError`` / ``modelMessageContentToText`` /
# ``traceContextToLogContext`` / ``ModelMessageContent`` / ``ModelMessageContentBlock`` / ``ToolResultBudget``
# / ``TraceContext`` 未移植，保留为 ``Any``。``@zcode/zcode-cua/frame-contract`` 的
# ``OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION`` / ``containsOfficialCuaImageRefCredentialText`` 未移植，
# 保留为 ``Any``。``formatGenericPersistedOutputContent`` / ``isPersistedOutputContent`` 来自
# ``..result_persistence_format``（未移植，直连预测落点）。``ToolExecutorDeps`` 来自 ``.types``。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error  # type: ignore
from ...contracts.tracing import trace_context_to_log_context  # type: ignore
from ...zcode_cua.frame_contract import (  # type: ignore
    OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION,
    contains_official_cua_image_ref_credential_text,
)
from ..types import ToolEntry, ToolResultSerialization
from ..result_persistence_format import (  # type: ignore
    GenericPersistedOutputContentInput,
    format_generic_persisted_output_content,
    is_persisted_output_content,
)
from .hook_flow import format_hook_additional_contexts
from .result_content_projection import (
    append_hook_to_persisted_artifact_preview,
    append_hook_to_string_content,
    append_hook_without_reordering_structured_content,
    fit_content_with_suffix,
    OfficialCuaFrameContractError,
    project_hook_augmented_model_content,
    project_official_cua_structured_content,
)
from .types import ToolExecutorDeps
from .utils import is_record

__all__ = ["serialize_output", "append_hook_additional_contexts"]

_DEFAULT_RESULT_BUDGET: dict[str, Any] = {
    "maxInlineBytes": 100_000,
    "maxModelBytes": 100_000,
    "strategy": "truncate",
    "preview": {"direction": "head"},
}

OFFICIAL_CUA_INVALID_RASTER_RECOVERY_TEXT = (
    "This CUA raster is invalid and cannot be used in this request. "
    "Do not send a coordinate target; capture a new raster first."
)


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


async def serialize_output(
    deps: ToolExecutorDeps,
    output: Any,
    entry: ToolEntry,
    trace_context: Any,
    tool_call_id: str,
    signal: Any,
) -> ToolResultSerialization:
    effective_budget: dict[str, Any] = entry.result_budget if getattr(entry, "result_budget", None) is not None else _DEFAULT_RESULT_BUDGET
    model_content = _stringify_output_for_model(output, entry)
    content = _stringify_model_content_for_serialization(model_content)
    if _is_empty_model_content(model_content):
        # Bash 空输出也需要通用占位，避免模型把静默成功误读成缺失工具结果。
        empty_content = f"({entry.metadata.name} completed with no output)"
        return ToolResultSerialization(  # type: ignore[call-arg]
            content=empty_content,
            model_content=empty_content,
            original_bytes=_utf8_len(content),
            returned_bytes=_utf8_len(empty_content),
            truncated=False,
            budget_strategy=effective_budget["strategy"],
        )

    content_type = (
        entry.result_artifact_content_type
        if getattr(entry, "result_artifact_content_type", None) is not None
        else ("text/plain" if isinstance(output, str) else "application/json")
    )
    original_bytes = _utf8_len(content)
    # 部分 provider contract 按 JS 字符计数；保留现有工具的 UTF-8
    # byte budget，只让显式声明字符阈值的工具增加该持久化判据。
    exceeds_character_budget = entry.max_model_chars is not None and len(content) > entry.max_model_chars
    max_model_bytes = max(0, min(effective_budget["maxModelBytes"], effective_budget["maxInlineBytes"]))
    artifact_path = _find_artifact_path(output)
    should_persist_artifact = (
        (original_bytes > max_model_bytes or exceeds_character_budget)
        and effective_budget.get("artifact") is not None
        and effective_budget["artifact"].get("enabled") is True
        and effective_budget["strategy"] == "artifact"
    )
    artifact = (
        await _try_write_tool_artifact(deps, content, content_type, entry, effective_budget, trace_context, tool_call_id, signal)
        if should_persist_artifact
        else None
    )
    resolved_artifact_path = (artifact.get("path") if artifact is not None else None) or artifact.get("uri") if artifact is not None else None
    if resolved_artifact_path is None:
        resolved_artifact_path = artifact_path

    if (
        entry.model_content_protection == OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION
        and _has_image_block(model_content)
    ):
        try:
            protected_projection = project_official_cua_structured_content(
                model_content,
                max_model_bytes,
                effective_budget.get("preview", {}).get("direction", "head"),
            )
        except Exception as error:  # noqa: BLE001
            if not isinstance(error, OfficialCuaFrameContractError):
                raise
            # 原因：非 canonical frame 必须继续 fail closed，但低层布局 invariant 不能作为
            # tool result 暴露给模型；这里只记录稳定诊断，再交给 executor 生成可恢复错误结果。
            if deps.logger is not None:
                deps.logger.warn(
                    "Official CUA frame contract rejected during result serialization",
                    {
                        **trace_context_to_log_context(trace_context),
                        "code": error.code,
                        "event": "tool.result.cua_frame_contract_rejected",
                        "module": "core.tool.executor",
                        "status": "failed",
                        "toolCallId": tool_call_id,
                        "toolName": entry.metadata.name,
                    },
                )
            raise create_core_error(
                CoreErrorType.TOOL_EXECUTION_FAILED,
                OFFICIAL_CUA_INVALID_RASTER_RECOVERY_TEXT,
                {
                    "context": {"code": error.code, "source": "tool"},
                    "recoverable": True,
                },
            )
        if protected_projection is not None:
            projected_model_content = protected_projection["content"]
            projected_content = _stringify_model_content_for_serialization(projected_model_content)
            projected_bytes = _utf8_len(projected_content)
            # 序列化文本里 image 块只是短占位符；真实 base64 栅格（bridge 上限 200 KiB）
            # 会原样进入模型请求。returnedBytes 语义是"发给模型的字节"，必须计入
            # 图片载荷，否则 setOutputBytes / turn-tool-usage / usage-observability
            # 每次 CUA 帧系统性少计一张栅格。只保留 aggregate，避免文本/媒体分量
            # 与 returnedBytes 形成需要同步维护的第二份状态。
            structured_payload_bytes = _structured_media_bytes(projected_model_content)

            # 官方 CUA 的 image/image_ref 原子对始终原样保留；图片字节由 bridge 的独立
            # 上限保护，普通文本仍走 resultBudget，不能借一张合法 raster 绕过上下文预算。
            # 早返回与 official CUA protection authority 强制成对；配对验证失败
            #（protectedProjection 为 undefined）时不得绕过通用预算。
            return ToolResultSerialization(  # type: ignore[call-arg]
                content=projected_content,
                model_content=projected_model_content,
                original_bytes=original_bytes,
                returned_bytes=projected_bytes + structured_payload_bytes,
                truncated=protected_projection["truncated"],
                budget_strategy=effective_budget["strategy"],
                artifact_path=resolved_artifact_path,
            )

    if (original_bytes <= max_model_bytes and not exceeds_character_budget) or (
        # 带字符阈值的 provider 文本在持久化失败时必须保留原文；
        # 不能再落入通用 resultBudget 截断并注入另一套提示。
        exceeds_character_budget
        and should_persist_artifact
        and artifact is None
    ):
        return ToolResultSerialization(  # type: ignore[call-arg]
            content=content,
            model_content=model_content,
            original_bytes=original_bytes,
            returned_bytes=original_bytes,
            truncated=False,
            budget_strategy=effective_budget["strategy"],
            artifact_path=resolved_artifact_path,
        )

    if effective_budget["strategy"] == "artifact" and effective_budget.get("artifact") is not None and effective_budget["artifact"].get("enabled") is True:
        if artifact is not None and resolved_artifact_path is not None:
            persisted_output_content = _format_persisted_output_content(
                content=content,
                entry=entry,
                original_bytes=original_bytes,
                output=output,
                persisted_path=resolved_artifact_path,
            )
            persisted_content = _stringify_model_content_for_serialization(persisted_output_content)
            return ToolResultSerialization(  # type: ignore[call-arg]
                content=persisted_content,
                model_content=persisted_output_content,
                original_bytes=original_bytes,
                returned_bytes=_utf8_len(persisted_content),
                truncated=True,
                budget_strategy=effective_budget["strategy"],
                artifact_path=resolved_artifact_path,
            )

    artifact_hint = f"artifactPath={resolved_artifact_path}, " if resolved_artifact_path else ""
    suffix = f"\n\n[Tool output truncated by resultBudget: {artifact_hint}originalBytes={original_bytes}, maxModelBytes={max_model_bytes}, strategy={effective_budget['strategy']}]"
    truncated_content = fit_content_with_suffix(
        content,
        max_model_bytes,
        suffix,
        effective_budget.get("preview", {}).get("direction", "head"),
    )

    return ToolResultSerialization(  # type: ignore[call-arg]
        content=truncated_content,
        model_content=truncated_content,
        original_bytes=original_bytes,
        returned_bytes=_utf8_len(truncated_content),
        truncated=True,
        budget_strategy=effective_budget["strategy"],
        artifact_path=resolved_artifact_path,
    )


def append_hook_additional_contexts(
    serialization: ToolResultSerialization,
    additional_contexts: list[str],
    entry: ToolEntry,
) -> ToolResultSerialization:
    if len(additional_contexts) == 0:
        return serialization
    filtered_additional_contexts = (
        [context for context in additional_contexts if not contains_official_cua_image_ref_credential_text(context)]
        if entry.model_content_protection == OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION
        else list(additional_contexts)
    )
    omitted_frame_credential = len(filtered_additional_contexts) != len(additional_contexts)
    if len(filtered_additional_contexts) == 0:
        if omitted_frame_credential:
            return _replace(serialization, truncated=True)
        return serialization
    hook_context = format_hook_additional_contexts(filtered_additional_contexts)
    suffix = f"\n\n{hook_context}"
    max_model_bytes = _resolve_max_model_bytes(entry)
    if (
        entry.model_content_protection == OFFICIAL_CUA_FRAME_MODEL_CONTENT_PROTECTION
        and _has_image_block(serialization.model_content if serialization.model_content is not None else serialization.content)
    ):
        projected = append_hook_without_reordering_structured_content(
            serialization,
            hook_context,
            suffix,
            max_model_bytes,
        )
        return _replace(projected, truncated=True) if omitted_frame_credential else projected
    preview_direction = entry.result_budget.get("preview", {}).get("direction", "head") if getattr(entry, "result_budget", None) is not None else "head"
    artifact_preview = _is_persisted_artifact_preview(serialization)
    content_projection = (
        append_hook_to_persisted_artifact_preview(serialization.content, suffix, max_model_bytes)
        if artifact_preview
        else append_hook_to_string_content(serialization.content, suffix, max_model_bytes, preview_direction)
    )

    projected = _merge(
        serialization,
        {
            "content": content_projection["content"],
            "modelContent": project_hook_augmented_model_content(
                {
                    "artifactPreview": artifact_preview,
                    "contentProjection": content_projection,
                    "hookContext": hook_context,
                    "maxModelBytes": max_model_bytes,
                    "modelContent": serialization.model_content if serialization.model_content is not None else serialization.content,
                    "previewDirection": preview_direction,
                    "suffix": suffix,
                }
            ),
            "returnedBytes": _utf8_len(content_projection["content"]),
            "truncated": bool(serialization.truncated or content_projection["truncated"]),
        },
    )
    return _replace(projected, truncated=True) if omitted_frame_credential else projected


def _has_image_block(content: Any) -> bool:
    return isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "image" for block in content)


def _structured_media_bytes(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    total = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "image":
            total += _utf8_len(block.get("dataUrl", ""))
        # file-with-text 的模型可见内容是 text（dataUrl 不进请求），不计入媒体字节。
        elif block.get("type") == "file" and block.get("dataUrl") is not None and block.get("text") is None:
            total += _utf8_len(block.get("dataUrl", ""))
    return total


def _resolve_max_model_bytes(entry: ToolEntry) -> int:
    budget = entry.result_budget if getattr(entry, "result_budget", None) is not None else _DEFAULT_RESULT_BUDGET
    return max(0, min(budget["maxModelBytes"], budget["maxInlineBytes"]))


async def _try_write_tool_artifact(
    deps: ToolExecutorDeps,
    content: str,
    content_type: str,
    entry: ToolEntry,
    budget: dict[str, Any],
    trace_context: Any,
    tool_call_id: str,
    signal: Any,
) -> Optional[dict[str, Any]]:
    if deps.artifact_store is None:
        return None

    try:
        return await deps.artifact_store.write_tool_result_artifact(
            {
                "sessionId": deps.session_id,
                "turnId": trace_context.turn_id if trace_context.turn_id is not None else deps.turn_id,
                "toolCallId": tool_call_id,
                "toolName": entry.metadata.name,
                "content": content,
                "contentType": content_type,
                "retention": budget.get("artifact", {}).get("retention", "session"),
                "trace": trace_context,
            },
            {"signal": signal},
        )
    except Exception:  # noqa: BLE001
        # 与主模型交互时，artifact 写入失败不应让一次成功的工具调用变成失败。
        # 这里回退到统一的 resultBudget 截断路径，保持工具结果可见并避免原始大输出进模型。
        return None


def _format_persisted_output_content(input: dict[str, Any]) -> Any:
    projected = (
        input["entry"].format_persisted_model_content(
            {
                "content": input["content"],
                "originalBytes": input["originalBytes"],
                "output": input["output"],
                "persistedPath": input["persistedPath"],
            }
        )
        if getattr(input["entry"], "format_persisted_model_content", None) is not None
        else None
    )
    if projected is not None:
        return projected

    return format_generic_persisted_output_content(
        GenericPersistedOutputContentInput(
            content=input["content"],
            original_bytes=input["originalBytes"],
            persisted_path=input["persistedPath"],
        )
    )


def _is_empty_model_content(content: Any) -> bool:
    if isinstance(content, str):
        return content.strip() == ""
    if isinstance(content, list) and len(content) == 0:
        return True
    if isinstance(content, list):
        return all(
            (block.get("type") != "text") or (not isinstance(block.get("text"), str)) or block.get("text", "").strip() == ""
            for block in content
            if isinstance(block, dict)
        )
    return False


def _stringify_output_for_model(output: Any, entry: ToolEntry) -> Any:
    if getattr(entry, "format_model_content", None) is not None:
        return entry.format_model_content(output)
    if isinstance(output, str):
        return output
    if output is None:
        return ""
    try:
        return json.dumps(output) or ""
    except Exception:  # noqa: BLE001
        return str(output)


def _stringify_model_content_for_serialization(content: Any) -> str:
    return content if isinstance(content, str) else model_message_content_to_text(content)


def _is_persisted_artifact_preview(serialization: ToolResultSerialization) -> bool:
    return (
        serialization.budget_strategy == "artifact"
        and bool(serialization.truncated)
        and isinstance(serialization.artifact_path, str)
        and len(serialization.artifact_path) > 0
        and is_persisted_output_content(serialization.content)
    )


def _find_artifact_path(output: Any) -> Optional[str]:
    if not is_record(output):
        return None
    for key in ["persistedOutputPath", "rawOutputPath", "artifactPath", "outputPath"]:
        value = output.get(key)
        if isinstance(value, str) and len(value) > 0:
            return value
    return None


def _as_dict(serialization: ToolResultSerialization) -> dict[str, Any]:
    try:
        return vars(serialization).copy()
    except Exception:  # noqa: BLE001
        return dict(serialization)


def _replace(serialization: ToolResultSerialization, **changes: Any) -> ToolResultSerialization:
    base = _as_dict(serialization)
    base.update(changes)
    return ToolResultSerialization(**base)  # type: ignore[call-arg]


def _merge(serialization: ToolResultSerialization, changes: dict[str, Any]) -> ToolResultSerialization:
    base = _as_dict(serialization)
    base.update(changes)
    return ToolResultSerialization(**base)  # type: ignore[call-arg]
