"""Result content projection — hook-augmented model-content projection helpers.

Ported from: packages/core/src/tool/executor/result-content-projection.ts
Original package: @zcode/core (tool/executor)

# PORT-STATUS: partial
# ``@zcode/contracts`` 的 ``modelMessageContentToText`` / ``ModelMessageContent`` /
# ``ModelMessageContentBlock`` 及 ``@zcode/zcode-cua/frame-contract`` 的
# ``findOfficialCuaFrameContentPair`` 未移植，保留为 ``Any``。``ToolResultSerialization`` 来自
# ``..types``。``OFFICIAL_CUA_FRAME_TRUNCATION_MARKER`` / ``OFFICIAL_CUA_FRAME_CONTRACT_ERROR_CODE``
# 为文件内常量，逐字保留。
"""

from __future__ import annotations

from typing import Any, Optional

from ...contracts.model import ModelMessageContent, ModelMessageContentBlock, model_message_content_to_text  # type: ignore
from ...zcode_cua.frame_contract import find_official_cua_frame_content_pair  # type: ignore
from ..types import ToolResultSerialization

__all__ = [
    "OfficialCuaFrameContractError",
    "project_official_cua_structured_content",
    "append_hook_without_reordering_structured_content",
    "append_hook_to_string_content",
    "append_hook_to_persisted_artifact_preview",
    "project_hook_augmented_model_content",
    "fit_content_with_suffix",
]


OFFICIAL_CUA_TEXT_TRUNCATION_MARKER = (
    "[Official CUA text truncated by resultBudget. Re-observe with detail=compact or a narrower window before relying on omitted state.]"
)

OFFICIAL_CUA_FRAME_CONTRACT_ERROR_CODE = "official_cua_frame_pair_not_leading"


class OfficialCuaFrameContractError(Exception):
    code = OFFICIAL_CUA_FRAME_CONTRACT_ERROR_CODE

    def __init__(self) -> None:
        super().__init__("Official CUA frame pair must be the first two result blocks")
        self.name = "OfficialCuaFrameContractError"


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


def project_official_cua_structured_content(
    content: Any,
    max_model_bytes: int,
    direction: str,  # "head" | "tail"
) -> Optional[dict[str, Any]]:
    pair = find_official_cua_frame_content_pair(content)
    if pair is None:
        return None
    # 官方 producer 的 canonical pair 必须已经位于结果首部。consumer 只校验、
    # 预算并保序，不再猜测或提升非 canonical 布局。这里直接失败，避免后续通用
    # 小结果直通路径重新把带前缀的 frame authority 交给模型。
    if pair.image_index != 0 or pair.image_ref_index != 1:
        raise OfficialCuaFrameContractError()

    pair_content: list[Any] = [pair.image, pair.image_ref]
    suffix_blocks = [
        block
        for block in content[2:]
        if isinstance(block, dict) and block.get("type") == "text" and len(block.get("text", "")) > 0
    ]
    has_other_structured_blocks = any(
        index != pair.image_ref_index and index != pair.image_index and block.get("type") != "text"
        for index, block in enumerate(content)
    )
    complete_content: list[Any] = [*pair_content, *suffix_blocks]
    if not has_other_structured_blocks and _utf8_len(_stringify(content=complete_content)) <= max_model_bytes:
        return {"content": complete_content, "truncated": False}

    suffix_text = _join_text_blocks(suffix_blocks)
    pair_bytes = _utf8_len(_stringify(content=pair_content))
    remaining_after_pair = max(0, max_model_bytes - pair_bytes)
    projected_marker = _fit_string_to_bytes(
        OFFICIAL_CUA_TEXT_TRUNCATION_MARKER,
        max(0, remaining_after_pair - 2),
        "head",
    )
    marker_cost = _utf8_len(projected_marker) + 2 if len(projected_marker) > 0 else 0
    suffix_budget = max(0, remaining_after_pair - marker_cost - 2) if len(projected_marker) > 0 else 0
    projected_suffix = _fit_string_to_bytes(suffix_text, suffix_budget, direction)

    projected: list[Any] = [
        pair.image,
        pair.image_ref,
        *([{"type": "text", "text": projected_suffix}] if projected_suffix else []),
        *([{"type": "text", "text": projected_marker}] if projected_marker else []),
    ]
    return {
        "content": projected,
        "truncated": True,
    }


def append_hook_without_reordering_structured_content(
    serialization: ToolResultSerialization,
    hook_context: str,
    suffix: str,
    max_model_bytes: int,
) -> ToolResultSerialization:
    model_content = serialization.model_content if serialization.model_content is not None else serialization.content
    if not isinstance(model_content, list):
        return serialization

    current_bytes = _utf8_len(serialization.content)
    remaining_bytes = max(0, max_model_bytes - current_bytes)
    separator = "\n\n"
    separator_bytes = _utf8_len(separator)
    fitted_hook_context = (
        _fit_string_to_bytes(hook_context, remaining_bytes - separator_bytes, "head")
        if remaining_bytes > separator_bytes
        else ""
    )
    fitted_suffix = f"{separator}{fitted_hook_context}" if len(fitted_hook_context) > 0 else ""
    hook_truncated = _utf8_len(suffix) > remaining_bytes
    content = f"{serialization.content}{fitted_suffix}"

    return ToolResultSerialization(  # type: ignore[call-arg]
        **{
            **_as_dict(serialization),
            "content": content,
            # 只能在原始块之后追加 hook。这样 raster 仍然是首块、image_ref 仍紧随其后，
            # 即使 hook 超出预算也不会把文本搬到图片前或用字符串替换图片。
            "modelContent": (
                [*model_content, {"type": "text", "text": fitted_hook_context}]
                if len(fitted_hook_context) > 0
                else model_content
            ),
            # returnedBytes 已包含受保护 raster；追加 hook 只增加实际新增的文本 bytes，
            # 避免重新拆分文本/媒体并维护第二份计量状态。
            "returnedBytes": serialization.returned_bytes + _utf8_len(fitted_suffix),
            "truncated": bool(serialization.truncated or hook_truncated),
        }
    )


def append_hook_to_string_content(
    content: str,
    suffix: str,
    max_bytes: int,
    preview_direction: str,  # "head" | "tail"
) -> dict[str, Any]:
    augmented_content = f"{content}{suffix}"
    if _utf8_len(augmented_content) <= max_bytes:
        return {"content": augmented_content, "truncated": False}
    return {
        "content": _fit_content_with_suffix(content, max_bytes, suffix, preview_direction),
        "truncated": True,
    }


def append_hook_to_persisted_artifact_preview(
    content: str,
    suffix: str,
    max_hook_bytes: int,
) -> dict[str, Any]:
    fitted_suffix = _fit_string_to_bytes(suffix, max_hook_bytes, "head")
    return {
        "content": f"{content}{fitted_suffix}",
        "truncated": _utf8_len(suffix) > max_hook_bytes,
    }


def project_hook_augmented_model_content(input: dict[str, Any]) -> Any:
    if input["artifactPreview"]:
        return input["contentProjection"]["content"]
    if not input["contentProjection"]["truncated"]:
        return _append_hook_context_to_model_content(input["modelContent"], input["hookContext"])
    if not _has_preserved_structured_blocks(input["modelContent"]):
        return input["contentProjection"]["content"]

    text_content = _budgeted_text_from_structured_content(input["modelContent"])
    budgeted_text = _fit_content_with_suffix(
        text_content,
        input["maxModelBytes"],
        input["suffix"],
        input["previewDirection"],
    )
    return [
        *[block for block in input["modelContent"] if _should_preserve_structured_block(block)],
        *([{"type": "text", "text": budgeted_text}] if len(budgeted_text) > 0 else []),
    ]


def fit_content_with_suffix(
    content: str,
    max_bytes: int,
    suffix: str,
    direction: str,  # "head" | "tail"
) -> str:
    if max_bytes <= 0:
        return ""
    suffix_content = _fit_string_to_bytes(suffix, max_bytes, "head")
    remaining_bytes = max_bytes - _utf8_len(suffix_content)
    if remaining_bytes <= 0:
        return suffix_content
    return f"{_fit_string_to_bytes(content, remaining_bytes, direction)}{suffix_content}"


def _join_text_blocks(content: Any) -> str:
    return "\n\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and len(block.get("text", "")) > 0
    )


def _stringify(content: Any) -> str:
    return content if isinstance(content, str) else model_message_content_to_text(content)


def _append_hook_context_to_model_content(content: Any, hook_context: str) -> Any:
    if isinstance(content, str):
        return f"{content}\n\n{hook_context}"
    return [*content, {"type": "text", "text": hook_context}]


def _has_preserved_structured_blocks(content: Any) -> bool:
    return isinstance(content, list) and any(_should_preserve_structured_block(block) for block in content)


def _should_preserve_structured_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    if block.get("type") == "text":
        return False
    if block.get("type") == "file" and isinstance(block.get("text"), str) and len(block.get("text", "")) > 0:
        return False
    return True


def _budgeted_text_from_structured_content(content: Any) -> str:
    return "\n\n".join(
        (block.get("text", "") if block.get("type") == "text" else (block.get("text", "") if block.get("type") == "file" and isinstance(block.get("text"), str) else ""))
        for block in content
        if isinstance(block, dict)
    )


def _fit_string_to_bytes(value: str, max_bytes: int, direction: str) -> str:
    if max_bytes <= 0:
        return ""
    if _utf8_len(value) <= max_bytes:
        return value

    chars = list(value)
    low = 0
    high = len(chars)
    while low < high:
        mid = (low + high + 1) // 2
        candidate = "".join(chars[len(chars) - mid :]) if direction == "tail" else "".join(chars[:mid])
        if _utf8_len(candidate) <= max_bytes:
            low = mid
        else:
            high = mid - 1
    return "".join(chars[len(chars) - low :]) if direction == "tail" else "".join(chars[:low])


def _as_dict(serialization: ToolResultSerialization) -> dict[str, Any]:
    # PORT: ``ToolResultSerialization`` 是 dataclass；展开为 dict 以便合并覆盖字段。
    try:
        return vars(serialization).copy()
    except Exception:  # noqa: BLE001
        return dict(serialization)
