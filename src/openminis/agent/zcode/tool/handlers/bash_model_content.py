"""Bash model content — provider-visible text for a Bash tool result.

Ported from: packages/core/src/tool/handlers/bash-model-content.ts
Original package: @zcode/core (tool/handlers)

# PORT-STATUS: partial
``maybeImageContent`` 依赖 ``parseImageDataUrl``（图片契约/处理），属 ZCORE_PORTING.md §0.0
排除项，且 ``isImage`` 由同样被排除的 ``bash-image-output.ts`` 置位——Python 侧该标志恒为
``False``，故图片分支不可达。为不删减逻辑，分支保留并标注 ``# PORT:`` 说明其为死路径。
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Optional

from ..result_persistence_format import (
    PersistedOutputEnvelopeInput,
    format_persisted_output_envelope,
)
from .bash_semantics import BashProviderErrorStatusInput, is_bash_provider_error_status
from .ports import ModelMessageContent

__all__ = [
    "MODEL_RESULT_PREVIEW_CHARS",
    "ASSISTANT_BLOCKING_BUDGET_MS",
    "format_bash_model_content",
    "format_persisted_bash_model_content",
    "is_bash_output_provider_error",
]

MODEL_RESULT_PREVIEW_CHARS = 2_000
ASSISTANT_BLOCKING_BUDGET_MS = 15_000
READ_TOOL_NAME = "Read"

_LEADING_BLANK_LINES_RE = re.compile(r"^(\s*\n)+")
_TRAILING_ZERO_RE = re.compile(r"\.0$")


def format_bash_model_content(output: Any) -> ModelMessageContent:
    """Mirrors ``formatBashModelContent(output)``."""
    result = _bash_output_safe_parse(output)
    if result is None:
        return _stringify_fallback(output)

    structured = result.get("structuredContent")
    if isinstance(structured, list) and len(structured) > 0:
        return structured

    # Bash 失败以结果对象返回；模型可见的错误结果应保持为纯文本。
    if is_bash_output_provider_error(result):
        return _format_provider_error_content(result)

    image_content = _maybe_image_content(result)
    if image_content is not None:
        return image_content

    return _format_text_content_for_model(result)


def format_persisted_bash_model_content(input: dict[str, Any]) -> Optional[ModelMessageContent]:
    """Mirrors ``formatPersistedBashModelContent(input)``."""
    if _bash_output_safe_parse(input["output"]) is None:
        return None
    # serializer 已经把完整 Bash provider-visible content 写入 artifact；
    # 这里必须只预览该 content，不能重进普通 formatter 后把 stderr/provider-error 文本完整追加回模型上下文。
    return _format_bash_persisted_output_content(
        content=input["content"],
        original_bytes=input["originalBytes"],
        persisted_path=input["persistedPath"],
    )


def is_bash_output_provider_error(output: Any) -> bool:
    """Mirrors ``isBashOutputProviderError(output)``."""
    if not _is_record(output):
        return False
    return is_bash_provider_error_status(
        BashProviderErrorStatusInput(
            status=output.get("status"),
            exit_code=output.get("exitCode"),
            return_code_interpretation=output.get("returnCodeInterpretation"),
        )
    )


def _format_provider_error_content(result: dict[str, Any]) -> str:
    """Mirrors the non-exported ``formatProviderErrorContent(result)``."""
    return "\n".join(
        line
        for line in [
            f"Exit code {result.get('exitCode')}",
            _format_stdout_content_for_model(result),
            _format_stderr_for_model(result),
            _format_background_info_for_model(result),
            _trim_or_empty(result.get("staleReadFileStateHint")),
            _trim_or_empty(result.get("ghRateLimitHint")),
        ]
        if line
    )


def _format_text_content_for_model(result: dict[str, Any]) -> str:
    """Mirrors the non-exported ``formatTextContentForModel(result)``."""
    return "\n".join(
        line
        for line in [
            _format_stdout_content_for_model(result),
            _format_stderr_for_model(result),
            _format_background_info_for_model(result),
            _trim_or_empty(result.get("staleReadFileStateHint")),
            _trim_or_empty(result.get("ghRateLimitHint")),
        ]
        if line
    )


def _format_stdout_content_for_model(result: dict[str, Any]) -> str:
    """Mirrors the non-exported ``formatStdoutContentForModel(result)``."""
    processed_stdout = _format_stdout_for_model(result.get("stdout"))
    # PORT: TS ``result.persistedOutputPath ?? result.rawOutputPath``
    persisted_output_path = (
        None
        if result.get("status") == "backgrounded"
        else (
            result.get("persistedOutputPath")
            if result.get("persistedOutputPath") is not None
            else result.get("rawOutputPath")
        )
    )
    if persisted_output_path:
        processed_stdout = _format_bash_persisted_output_content(
            content=processed_stdout,
            original_bytes=_observed_output_bytes(result),
            persisted_path=persisted_output_path,
        )
    return processed_stdout


def _stringify_fallback(output: Any) -> str:
    """Mirrors the non-exported ``stringifyFallback(output)``."""
    if isinstance(output, str):
        return output
    try:
        dumped = json.dumps(output)
        return dumped if dumped is not None else str(output)
    except Exception:  # noqa: BLE001 — TS catch {} 同样吞掉序列化失败
        return str(output)


def _format_stdout_for_model(stdout: Optional[str]) -> str:
    """Mirrors the non-exported ``formatStdoutForModel(stdout)``."""
    if not stdout:
        return ""
    # PORT: TS ``.replace(/^(\s*\n)+/, "").trimEnd()`` —— 去前导空行后去尾部空白。
    return _LEADING_BLANK_LINES_RE.sub("", stdout).rstrip()


def _format_stderr_for_model(result: dict[str, Any]) -> str:
    """Mirrors the non-exported ``formatStderrForModel(result)``."""
    message = (result.get("stderr") or "").strip()
    if result.get("interrupted"):
        if message:
            message += "\n"
        message += "<error>Command was aborted before completion</error>"
    return message


def _format_background_info_for_model(result: dict[str, Any]) -> str:
    """Mirrors the non-exported ``formatBackgroundInfoForModel(result)``."""
    if not result.get("backgroundTaskId"):
        return ""
    output_text = _format_background_output_paths(result)
    if result.get("assistantAutoBackgrounded"):
        return (
            "Command exceeded the assistant-mode blocking budget "
            f"({ASSISTANT_BLOCKING_BUDGET_MS / 1000}s) and was moved to the background with ID: "
            f"{result['backgroundTaskId']}. It is still running \u2014 you will be notified when it "
            f"completes.{output_text} In assistant mode, delegate long-running work to a subagent or "
            "use run_in_background to keep this conversation responsive."
        )
    if result.get("backgroundedByUser"):
        return (
            f"Command was manually backgrounded by user with ID: {result['backgroundTaskId']}."
            f"{_strip_trailing_period(output_text)}"
        )
    read_hint = (
        f" To check interim output, use {READ_TOOL_NAME} on that file path." if output_text else ""
    )
    return (
        f"Command running in background with ID: {result['backgroundTaskId']}.{output_text} "
        f"You will be notified when it completes.{read_hint}"
    )


def _format_background_output_paths(result: dict[str, Any]) -> str:
    """Mirrors the non-exported ``formatBackgroundOutputPaths(result)``."""
    # Bash 的 stdout/stderr 已直接写入单一输出文件，继续追加 stdout/stderr legacy path
    # 会让模型看到与单一输出文件不一致的双路径信息。
    # PORT: TS 四段 ``??`` 链 —— Python 逐级判 ``is not None``。
    output_path = result.get("rawOutputPath")
    if output_path is None:
        output_path = result.get("persistedOutputPath")
    if output_path is None:
        output_path = result.get("stdoutPersistedOutputPath")
    if output_path is None:
        output_path = result.get("stderrPersistedOutputPath")
    return f" Output is being written to: {output_path}." if output_path else ""


def _strip_trailing_period(value: str) -> str:
    """Mirrors the non-exported ``stripTrailingPeriod(value)``."""
    return value[:-1] if value.endswith(".") else value


def _observed_output_bytes(result: dict[str, Any]) -> int:
    """Mirrors the non-exported ``observedOutputBytes(result)``."""
    if _is_finite(result.get("persistedOutputSize")):
        return result["persistedOutputSize"]
    if _is_finite(result.get("stdoutPersistedOutputSize")) or _is_finite(
        result.get("stderrPersistedOutputSize")
    ):
        # PORT: TS ``(a ?? 0) + (b ?? 0)`` —— 只对 null/undefined 兜底，``0`` 是合法值。
        stdout_size = result.get("stdoutPersistedOutputSize")
        stderr_size = result.get("stderrPersistedOutputSize")
        return (stdout_size if stdout_size is not None else 0) + (
            stderr_size if stderr_size is not None else 0
        )
    # PORT: TS ``(result.stdoutBytes ?? Buffer.byteLength(result.stdout, "utf8"))`` ——
    # ``stdoutBytes === 0`` 必须保留为 0，不能落到字节长度兜底。
    stdout_bytes = result.get("stdoutBytes")
    stderr_bytes = result.get("stderrBytes")
    return (
        stdout_bytes if stdout_bytes is not None else _utf8_len(result.get("stdout"))
    ) + (stderr_bytes if stderr_bytes is not None else _utf8_len(result.get("stderr")))


def _format_bash_persisted_output_content(
    content: str,
    original_bytes: int,
    persisted_path: str,
) -> str:
    """Mirrors the non-exported ``formatBashPersistedOutputContent(input)``."""
    return format_persisted_output_envelope(
        PersistedOutputEnvelopeInput(
            content=content,
            format_bytes=_format_bash_output_byte_size,
            original_bytes=original_bytes,
            persisted_path=persisted_path,
            preview_chars=MODEL_RESULT_PREVIEW_CHARS,
        )
    )


def _format_bash_output_byte_size(bytes: int) -> str:
    """Mirrors the non-exported ``formatBashOutputByteSize(bytes)``."""
    if not _is_finite(bytes) or bytes <= 0:
        return "0 bytes"
    kb = bytes / 1024
    if kb < 1:
        return f"{bytes} bytes"
    if kb < 1024:
        return f"{_trim_unit(kb)}KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{_trim_unit(mb)}MB"
    return f"{_trim_unit(mb / 1024)}GB"


def _trim_unit(value: float) -> str:
    """Mirrors the non-exported ``trimUnit(value)``.

    # PORT: TS ``value.toFixed(1).replace(/\\.0$/, "")`` —— 固定 1 位小数后去掉 ``.0``。
    """
    return _TRAILING_ZERO_RE.sub("", f"{value:.1f}")


def _maybe_image_content(result: dict[str, Any]) -> Optional[ModelMessageContent]:
    """Mirrors the non-exported ``maybeImageContent(result)``.

    # PORT-STATUS: partial —— 上游用 ``parseImageDataUrl(result.stdout, { allowWhitespace: true })``
    （图片契约，§0.0 排除项）。Python 侧没有该解析器，且 ``isImage`` 由被排除的
    ``bash-image-output.ts`` 置位（恒 ``False``），故本分支不可达；按上游「解析失败即
    ``return undefined``」的语义返回 ``None``，不伪造图片块。
    """
    if not result.get("isImage"):
        return None
    return None


def _bash_output_safe_parse(output: Any) -> Optional[dict[str, Any]]:
    """Stand-in for ``BashOutputSchema.safeParse(output)``.

    # PORT: zod 运行时校验无法直接搬运（见 handlers/ports.py 文件头说明）。此处只还原
    # ``BashOutputSchema`` 的**必需字段**约束：``stdout`` / ``stderr`` 为 string、
    # ``interrupted`` 为 boolean。不满足即视为解析失败 —— 与 ``safeParse().success === false``
    # 走 ``stringifyFallback`` 的分支一致。
    """
    if not _is_record(output):
        return None
    if not isinstance(output.get("stdout"), str):
        return None
    if not isinstance(output.get("stderr"), str):
        return None
    if not isinstance(output.get("interrupted"), bool):
        return None
    return output


def _is_record(value: Any) -> bool:
    """Mirrors the non-exported ``isRecord(value)``.

    # PORT: TS ``typeof value === "object" && value !== null && !Array.isArray(value)``。
    """
    return isinstance(value, dict)


def _utf8_len(value: Optional[str]) -> int:
    """PORT: ``Buffer.byteLength(value, "utf8")`` 的等价物。"""
    return len((value or "").encode("utf-8"))


def _trim_or_empty(value: Any) -> str:
    """PORT: TS ``result.x?.trim() ?? ""``。"""
    return value.strip() if isinstance(value, str) else ""


def _is_finite(value: Any) -> bool:
    """PORT: JS ``Number.isFinite``。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
