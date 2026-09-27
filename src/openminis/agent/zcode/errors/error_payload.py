"""Error payload projection — summarize a Python exception chain for the UI.

Ported from: packages/core/src/errors/error-payload.ts
Original package: @zcode/core (errors)

# PORT: upstream walks the TS error chain (``cause`` / ``lastError`` /
# ``error``) plus the ``context`` bag; Python mirrors the same traversal on
# ``__cause__`` / ``__context__`` / ``.cause`` / ``.context``. The
# ``ExecutionErrorPayloadProjection`` interface is an **open record**
# (missing keys are omitted), so it is returned as a plain ``dict`` — the
# consumer (``tool/executor/errors.py``) already reads keys defensively.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional, Union

__all__ = [
    "ERROR_PAYLOAD_ROLE_PRIMARY",
    "ERROR_PAYLOAD_ROLE_WRAPPER",
    "ErrorPayloadRole",
    "with_error_payload_role",
    "select_execution_error_message",
    "project_execution_error_payload",
]

ERROR_PAYLOAD_ROLE_PRIMARY = "primary"
ERROR_PAYLOAD_ROLE_WRAPPER = "wrapper"

#: PORT: upstream ``type ErrorPayloadRole = "primary" | "wrapper"``.
ErrorPayloadRole = str


def with_error_payload_role(
    context: Optional[dict[str, Any]], role: ErrorPayloadRole
) -> dict[str, Any]:
    out: dict[str, Any] = dict(context or {})
    out["errorPayloadRole"] = role
    return out


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------

def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _read_string(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if len(trimmed) > 0 else None


def _read_original_text(value: Any) -> Optional[str]:
    if not isinstance(value, str) or len(value.strip()) == 0:
        return None
    return value


def _sanitize_text(message: Optional[str]) -> Optional[str]:
    if message is None:
        return None
    compact = re.sub(r"\s+", " ", message).strip()
    if not compact:
        return None
    return compact if len(compact) <= 500 else compact[:497] + "..."


def _sanitize_attribution_text(value: Any) -> Optional[str]:
    text = _read_string(value)
    if not text:
        return None
    return text if len(text) <= 160 else text[:160]


def _read_code(value: Any) -> Optional[str]:
    as_string = _read_string(value)
    if as_string:
        return as_string
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value != 0:
        return str(value)
    return None


def _read_error_payload_role(value: Any) -> Optional[str]:
    if value == ERROR_PAYLOAD_ROLE_PRIMARY or value == ERROR_PAYLOAD_ROLE_WRAPPER:
        return value
    return None


def _error_frame(
    code: Optional[str] = None,
    context: Optional[dict[str, Any]] = None,
    context_code: Optional[str] = None,
    detail: Optional[str] = None,
    is_wrapper: bool = False,
    message: Optional[str] = None,
    original_message: Optional[str] = None,
    retryable: Optional[bool] = None,
    role: Optional[str] = None,
) -> dict[str, Any]:
    frame: dict[str, Any] = {"isWrapper": is_wrapper}
    if code:
        frame["code"] = code
    if context:
        frame["context"] = context
    if context_code:
        frame["contextCode"] = context_code
    if detail:
        frame["detail"] = detail
    if message:
        frame["message"] = message
    if original_message:
        frame["originalMessage"] = original_message
    if retryable is not None:
        frame["retryable"] = retryable
    if role:
        frame["role"] = role
    return frame


def _to_error_payload_frame(entry: Any) -> dict[str, Any]:
    # PORT: upstream reads a *record* entry; in Python the chain walk also
    # accepts Exception objects (exception chain), so normalise to a dict.
    record: Optional[dict[str, Any]] = None
    if isinstance(entry, dict):
        record = entry
    elif isinstance(entry, BaseException):
        record = {
            "message": str(entry),
            "context": getattr(entry, "context", None),
            "retryable": getattr(entry, "retryable", None),
            "providerCode": getattr(entry, "code", None),
            "detail": getattr(entry, "detail", None),
            "errorPayloadRole": getattr(entry, "error_payload_role", None),
        }
    else:
        return _error_frame(is_wrapper=False)
    if record is None:
        return _error_frame(is_wrapper=False)

    context = record.get("context") if _is_record(record.get("context")) else None
    original_message = _read_original_text(record.get("message"))
    message = _sanitize_text(original_message)
    role = _read_error_payload_role(record.get("errorPayloadRole")) or (
        _read_error_payload_role(context.get("errorPayloadRole")) if context else None
    )
    code = (
        _read_code(record.get("providerCode"))
        or (_read_code(context.get("providerCode")) if context else None)
        or _read_code(record.get("code"))
    )
    context_code = _read_code(context.get("code")) if context else None
    detail = (
        _sanitize_text(_read_string(record.get("detail")))
        or _sanitize_text(_read_string(record.get("errorDetails")))
        or _sanitize_text(_read_string(record.get("details")))
    )
    is_wrapper = role == ERROR_PAYLOAD_ROLE_WRAPPER
    raw_retryable = record.get("retryable")
    retryable = raw_retryable if isinstance(raw_retryable, bool) else None

    return _error_frame(
        code=code,
        context=context,
        context_code=context_code,
        detail=detail,
        is_wrapper=is_wrapper,
        message=message,
        original_message=original_message,
        retryable=retryable,
        role=role,
    )


def _collect_error_payload_frames(error: Any) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: Any = error

    # PORT: upstream caps the chain at 12 frames and follows
    # ``cause / lastError / error``; Python follows ``__cause__`` (explicit)
    # then ``__context__`` (implicit) then the ``cause`` / ``lastError`` /
    # ``error`` attributes (contract-shaped exceptions).
    for _depth in range(12):
        entry = current
        if isinstance(entry, BaseException):
            key = id(entry)
        elif isinstance(entry, dict):
            key = id(entry)
        else:
            break
        if key in seen:
            break
        seen.add(key)
        frames.append(_to_error_payload_frame(entry))

        if isinstance(entry, BaseException):
            if entry.__cause__ is not None:
                current = entry.__cause__
            elif entry.__context__ is not None:
                current = entry.__context__
            else:
                current = (
                    getattr(entry, "cause", None)
                    or getattr(entry, "lastError", None)
                    or getattr(entry, "error", None)
                )
        else:
            current = (
                entry.get("cause")
                or entry.get("lastError")
                or entry.get("error")
            )
        if current is None or current is entry:
            break
    return frames


def _select_primary_error_frame(
    frames: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    for frame in frames:
        if frame.get("message") and not frame.get("isWrapper"):
            return frame
    for frame in frames:
        if frame.get("message"):
            return frame
    return None


def _select_error_code(
    primary_frame: Optional[dict[str, Any]],
    frames: list[dict[str, Any]],
) -> Optional[str]:
    if primary_frame is not None:
        candidates: list[Optional[str]] = (
            [primary_frame.get("contextCode"), primary_frame.get("code")]
            if primary_frame.get("isWrapper")
            else [primary_frame.get("code"), primary_frame.get("contextCode")]
        )
    else:
        candidates = []
    candidates.extend(
        frame.get("code") for frame in frames if not frame.get("isWrapper")
    )
    candidates.extend(frame.get("contextCode") for frame in frames)
    candidates.extend(frame.get("code") for frame in frames)
    for code in candidates:
        if code:
            return code
    return None


def _format_context_part(label: str, value: Any) -> Optional[str]:
    if isinstance(value, str) and len(value.strip()) > 0:
        return f"{label}={value.strip()}"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return f"{label}={value}"
    if isinstance(value, bool):
        return f"{label}={'true' if value else 'false'}"
    return None


def _format_context_detail(
    context: Optional[dict[str, Any]],
) -> Optional[str]:
    if not context:
        return None

    detail_parts = [
        part
        for part in [
            _format_context_part("provider", context.get("providerId") or context.get("provider")),
            _format_context_part("provider_code", context.get("providerCode")),
            _format_context_part("model", context.get("modelId") or context.get("model")),
            _format_context_part("request", context.get("requestId")),
            _format_context_part("code", context.get("code")),
            _format_context_part("reason", context.get("reason")),
            _format_context_part("status", context.get("statusCode") or context.get("status")),
            _format_context_part("retryable", context.get("retryable")),
        ]
        if part
    ]

    return " ".join(detail_parts) if len(detail_parts) > 0 else None


def _unique_strings(values: list[Optional[str]]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _build_error_detail(frames: list[dict[str, Any]], message: str) -> Optional[str]:
    candidates: list[Optional[str]] = []
    for frame in frames:
        candidates.extend(
            [
                frame.get("message"),
                frame.get("detail"),
                _format_context_detail(frame.get("context")),
            ]
        )
    detail_lines = _unique_strings(candidates)
    detail_lines = [line for line in detail_lines if line is not None and line != message]
    return "\n".join(detail_lines) if len(detail_lines) > 0 else None


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------

_MODEL_API_ERROR_PHASES = frozenset(
    [
        "prepare",
        "configuration",
        "connect",
        "response",
        "stream",
        "parse",
        "validation",
        "unhandled",
    ]
)
_MODEL_FAILURE_EXCEPTION_KINDS = frozenset(
    [
        "api_call",
        "generic",
        "protocol",
        "provider_business",
        "transport",
        "type_error",
        "validation",
    ]
)


def _select_context_text(
    contexts: list[dict[str, Any]], keys: list[str]
) -> Optional[str]:
    for context in contexts:
        for key in keys:
            value = _sanitize_attribution_text(context.get(key))
            if value:
                return value
    return None


def _select_context_code(
    contexts: list[dict[str, Any]], keys: list[str]
) -> Optional[str]:
    for context in contexts:
        for key in keys:
            value = _read_code(context.get(key))
            if value:
                return _sanitize_attribution_text(value)
    return None


def _select_context_bool(
    contexts: list[dict[str, Any]], key: str
) -> Optional[bool]:
    for context in contexts:
        value = context.get(key)
        if isinstance(value, bool):
            return value
    return None


def _select_frame_retryable(frames: list[dict[str, Any]]) -> Optional[bool]:
    for frame in frames:
        if not frame.get("isWrapper") and frame.get("retryable") is not None:
            return frame["retryable"]
    return None


def _select_status_code(contexts: list[dict[str, Any]]) -> Optional[int]:
    for context in contexts:
        value = context.get("statusCode")
        if value is None:
            value = context.get("status")
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 100 <= value <= 599
        ):
            return value
    return None


def _select_error_phase(
    contexts: list[dict[str, Any]],
) -> Optional[str]:
    value = _select_context_text(contexts, ["errorPhase"])
    return value if value in _MODEL_API_ERROR_PHASES else None


def _select_exception_kind(
    contexts: list[dict[str, Any]],
) -> Optional[str]:
    value = _select_context_text(contexts, ["exceptionKind"])
    return value if value in _MODEL_FAILURE_EXCEPTION_KINDS else None


def _select_transport(
    contexts: list[dict[str, Any]],
) -> Optional[str]:
    value = _select_context_text(contexts, ["transport"])
    if value in ("http", "sse", "websocket"):
        return value
    return None


def _select_source(
    contexts: list[dict[str, Any]],
) -> Optional[str]:
    value = _select_context_text(contexts, ["source"])
    if value in ("provider", "runtime", "tool", "network"):
        return value
    return None


def _project_error_attribution(
    frames: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    contexts = [
        frame["context"]
        for frame in frames
        if isinstance(frame.get("context"), dict)
    ]
    reason = _select_context_text(contexts, ["reason"])
    error_phase = _select_error_phase(contexts)
    exception_kind = _select_exception_kind(contexts)
    provider_id = _select_context_text(contexts, ["providerId", "provider"])
    model_id = _select_context_text(contexts, ["modelId", "model"])
    provider_kind = _select_context_text(contexts, ["providerKind"])
    transport = _select_transport(contexts)
    status_code = _select_status_code(contexts)
    provider_error_code = (
        _select_context_code(contexts, ["providerCode"])
        or next(
            (
                frame.get("code")
                for frame in frames
                if isinstance(frame.get("code"), str)
                and re.fullmatch(r"\d+", frame.get("code"))
            ),
            None,
        )
    )
    retryable = (
        _select_context_bool(contexts, "retryable")
        or _select_frame_retryable(frames)
    )
    source = _select_source(contexts)

    attribution: dict[str, Any] = {}
    if source:
        attribution["source"] = source
    if reason:
        attribution["reason"] = reason
    if error_phase:
        attribution["errorPhase"] = error_phase
    if exception_kind:
        attribution["exceptionKind"] = exception_kind
    if provider_id:
        attribution["providerId"] = provider_id
    if model_id:
        attribution["modelId"] = model_id
    if provider_kind:
        attribution["providerKind"] = provider_kind
    if transport:
        attribution["transport"] = transport
    if status_code is not None:
        attribution["statusCode"] = status_code
    if provider_error_code:
        attribution["providerErrorCode"] = provider_error_code
    if retryable is not None:
        attribution["retryable"] = retryable

    return attribution if len(attribution) > 0 else None


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------

def select_execution_error_message(
    error: Any, fallback_message: str = "Turn execution failed"
) -> str:
    frames = _collect_error_payload_frames(error)
    primary_frame = _select_primary_error_frame(frames)
    return (
        primary_frame.get("originalMessage")
        or _read_original_text(fallback_message)
        or fallback_message
    )


def project_execution_error_payload(
    error: Any, fallback_message: str = "Turn execution failed"
) -> dict[str, Any]:
    """Mirror of ``projectExecutionErrorPayload``.

    # PORT: upstream returns an open object (keys omitted when absent); Python
    # returns a plain dict — the consumer (``tool/executor/errors.py``) reads
    # ``message`` / ``code`` / ``detail`` defensively.
    """
    frames = _collect_error_payload_frames(error)
    primary_frame = _select_primary_error_frame(frames)
    message = (
        primary_frame.get("message")
        or _sanitize_text(fallback_message)
        or fallback_message
    )
    code = _select_error_code(primary_frame, frames)
    detail = _build_error_detail(frames, message)
    # 保留最深层非 wrapper frame 的原始 message/detail，供 UI telemetry 定位具体失败原因；
    # 不改变既有 detail 的拼接结果或错误处理行为。
    underlying_frame: Optional[dict[str, Any]] = None
    for frame in reversed(frames):
        if not frame.get("isWrapper"):
            underlying_frame = frame
            break
    if underlying_frame is None and frames:
        underlying_frame = frames[-1]
    attribution = _project_error_attribution(frames)

    result: dict[str, Any] = {"message": message}
    if attribution:
        result["attribution"] = attribution
    if code:
        result["code"] = code
    if detail:
        result["detail"] = detail
    if underlying_frame and underlying_frame.get("message"):
        result["underlyingErrorMessage"] = underlying_frame["message"]
    if underlying_frame and underlying_frame.get("detail"):
        result["underlyingErrorDetail"] = underlying_frame["detail"]
    return result
