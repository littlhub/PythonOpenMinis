"""Bash output assembly — ExecutionResult → provider-visible BashOutput.

Ported from: packages/core/src/tool/handlers/bash-output.ts
Original package: @zcode/core (tool/handlers)

# PORT-STATUS: partial
``prepareBashImageOutput``（`bash-image-output.ts`）属 ZCORE_PORTING.md §0.0 明确排除的
「图像/视频产出类工具、图片处理相关 handler」，故未移植；本文件在调用点做**有据可依的降级**：
视为「stdout 不是可识别的图片 data URL」，返回 ``None``。这与上游在 ``parseImageDataUrl``
判空时的返回一致（见下方 ``_prepare_bash_image_output`` 的 ``# PORT:`` 注释）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ...contracts.execution import ExecutionResult
from .bash_cwd_policy import append_bash_cwd_stderr_suffix
from .bash_gh_rate_limit import get_gh_rate_limit_hint
from .bash_semantics import (
    BashProviderErrorStatusInput,
    interpret_bash_return_code,
    is_bash_provider_error_status,
    is_silent_bash_command,
)
from .ports import BashInput, BashOutput, ToolExecutionTelemetry, ToolCommandStatus
from .tool_perf import (
    attach_tool_execution_telemetry,
    classify_command,
    classify_safe_command_identity,
    command_hash,
    compact_tool_execution_telemetry,
    round_non_negative_ms,
)

__all__ = [
    "BashProgressTiming",
    "ToBashOutputOptions",
    "to_bash_output",
    "create_bash_background_performance_telemetry",
    "create_empty_bash_performance_telemetry",
]


@dataclass(slots=True)
class BashProgressTiming:
    """Mirrors ``interface BashProgressTiming``."""

    first_output_ms: Optional[int] = None


@dataclass(slots=True)
class ToBashOutputOptions:
    """Mirrors the inline ``options`` shape of ``toBashOutput``."""

    progress_timing: Optional[BashProgressTiming] = None
    stderr_suffix: Optional[str] = None


async def to_bash_output(
    result: ExecutionResult,
    input: BashInput,
    context: Any,
    options: Optional[ToBashOutputOptions] = None,
) -> BashOutput:
    """Mirrors ``toBashOutput(result, input, context, options = {})``."""
    if options is None:
        options = ToBashOutputOptions()

    stdout_persisted_output_path = result.stdout.artifact_path
    stderr_persisted_output_path = result.stderr.artifact_path
    persisted_output_path = (
        stdout_persisted_output_path
        if stdout_persisted_output_path is not None
        else stderr_persisted_output_path
    )
    stdout_persisted_output_size = result.stdout.artifact_bytes
    stderr_persisted_output_size = result.stderr.artifact_bytes
    # artifactBytes 是 64MiB cap 后的实际文件大小，不是模型可见文案中的
    # 原始观测大小。Bash persisted-output 必须使用截断前的 stream bytes。
    persisted_output_size = (
        (result.stdout.bytes if stdout_persisted_output_path else 0)
        + (result.stderr.bytes if stderr_persisted_output_path else 0)
        if persisted_output_path
        else None
    )
    stderr = append_bash_cwd_stderr_suffix(
        result.stderr.text or (result.error.message if result.error is not None else "") or "",
        options.stderr_suffix,
    )
    return_code_interpretation = interpret_bash_return_code(input.command, result)
    provider_error = is_bash_provider_error_status(
        BashProviderErrorStatusInput(
            exit_code=result.exit_code,
            return_code_interpretation=return_code_interpretation,
            status=result.status,
        )
    )
    provider_stdout = result.stdout.text
    image_output = (
        None
        if provider_error
        else await _prepare_bash_image_output(
            _BashImageSource(
                artifact_path=stdout_persisted_output_path,
                artifact_size=stdout_persisted_output_size,
                inline=provider_stdout,
            ),
            context,
        )
    )
    # PORT: TS ``imageOutput?.stdout ?? providerStdout`` —— 注意 ``??`` 只对 null/undefined
    # 生效，空串 ``""`` 也要保留（因此不能写成 ``a or b``）。
    stdout = (
        image_output.stdout
        if image_output is not None and image_output.stdout is not None
        else provider_stdout
    )

    gh_rate_limit_hint = (
        None if provider_error else get_gh_rate_limit_hint(input.command, result.stdout.text)
    )
    output: dict[str, Any] = {
        "stdout": stdout,
        "stderr": stderr,
        "interrupted": result.timed_out or result.cancelled,
        "isImage": image_output is not None,
        "noOutputExpected": is_silent_bash_command(input.command),
        "status": result.status,
        "exitCode": result.exit_code,
        "signal": result.signal,
        "timedOut": result.timed_out,
        "cancelled": result.cancelled,
        "stdoutTruncated": result.stdout.truncated,
        "stderrTruncated": result.stderr.truncated,
        "stdoutBytes": result.stdout.bytes,
        "stderrBytes": result.stderr.bytes,
        "rawOutputPath": persisted_output_path,
        "dangerouslyDisableSandbox": input.dangerously_disable_sandbox,
        "returnCodeInterpretation": return_code_interpretation,
        "persistedOutputPath": persisted_output_path,
        "stdoutPersistedOutputPath": stdout_persisted_output_path,
        "stderrPersistedOutputPath": stderr_persisted_output_path,
        "persistedOutputSize": persisted_output_size,
        "stdoutPersistedOutputSize": stdout_persisted_output_size,
        "stderrPersistedOutputSize": stderr_persisted_output_size,
    }
    # PORT: TS ``...(ghRateLimitHint ? { ghRateLimitHint } : {})`` —— 条件展开。
    if gh_rate_limit_hint:
        output["ghRateLimitHint"] = gh_rate_limit_hint

    return attach_tool_execution_telemetry(
        output,
        create_bash_performance_telemetry(input, result, options.progress_timing),
    )


@dataclass(slots=True)
class _BashImageSource:
    """Mirrors the inline ``BashImageSource`` shape (``bash-image-output.ts``)."""

    inline: str
    artifact_path: Optional[str] = None
    artifact_size: Optional[int] = None


@dataclass(slots=True)
class _BashImageOutput:
    """Mirrors ``{ stdout: string }`` returned by ``prepareBashImageOutput``."""

    stdout: str


async def _prepare_bash_image_output(
    stdout: _BashImageSource,
    context: Any,
) -> Optional[_BashImageOutput]:
    """Degraded stand-in for ``prepareBashImageOutput(stdout, context)``.

    # PORT-STATUS: partial —— ``bash-image-output.ts`` 按 ZCORE_PORTING.md §0.0 排除
    # （图像/图片处理类）。降级语义：始终按「stdout 不是可识别的图片 data URL」处理，
    # 返回 ``None``。上层据此把 ``isImage`` 置 False 并保留原始文本 stdout。
    #
    # PORT: 与上游行为的一致性依据 —— ``prepareBashImageOutput`` 在
    # ``parseImageDataUrl`` 返回假值时就是 ``return undefined``；且注释明确该能力是
    # 「best-effort」，解码失败时回退文本，「避免把坏图片块发给 provider」。
    """
    return None


def create_bash_performance_telemetry(
    input: BashInput,
    result: ExecutionResult,
    progress_timing: Optional[BashProgressTiming],
) -> Optional[ToolExecutionTelemetry]:
    """Mirrors the non-exported ``createBashPerformanceTelemetry(...)``."""
    output_bytes = result.stdout.bytes + result.stderr.bytes
    first_output_ms = progress_timing.first_output_ms if progress_timing is not None else None
    return compact_tool_execution_telemetry(
        {
            "detail": {
                "kind": "command",
                "command": {
                    "runMs": round_non_negative_ms(result.duration_ms),
                    "firstOutputMs": first_output_ms,
                    # PORT: TS ``firstOutputMs ?? roundNonNegativeMs(durationMs)``
                    "noOutputMs": (
                        first_output_ms
                        if first_output_ms is not None
                        else round_non_negative_ms(result.duration_ms)
                    ),
                    "exitCode": result.exit_code,
                    "timedOut": result.timed_out,
                    "outputBytes": output_bytes,
                    "category": classify_command(input.command),
                    # PORT: TS ``...classifySafeCommandIdentity(command)`` 展开
                    **classify_safe_command_identity(input.command),
                    "status": resolve_bash_command_status(result),
                    "hash": command_hash(input.command),
                },
            }
        }
    )


def create_bash_background_performance_telemetry(
    input: BashInput,
) -> Optional[ToolExecutionTelemetry]:
    """Mirrors ``createBashBackgroundPerformanceTelemetry(input)``."""
    return compact_tool_execution_telemetry(
        {
            "detail": {
                "kind": "command",
                "command": {
                    "category": classify_command(input.command),
                    **classify_safe_command_identity(input.command),
                    "status": "backgrounded",
                    "hash": command_hash(input.command),
                },
            }
        }
    )


def create_empty_bash_performance_telemetry(
    input: BashInput,
) -> Optional[ToolExecutionTelemetry]:
    """Mirrors ``createEmptyBashPerformanceTelemetry(input)``."""
    return compact_tool_execution_telemetry(
        {
            "detail": {
                "kind": "command",
                "command": {
                    "category": "empty",
                    "count": 0,
                    "name": "empty",
                    "status": "completed",
                    "hash": command_hash(input.command),
                },
            }
        }
    )


def resolve_bash_command_status(result: ExecutionResult) -> ToolCommandStatus:
    """Mirrors the non-exported ``resolveBashCommandStatus(result)``."""
    if result.timed_out:
        return "timed_out"
    if result.cancelled:
        return "cancelled"
    if result.status == "completed" and result.exit_code is not None and result.exit_code != 0:
        return "failed"
    return result.status
