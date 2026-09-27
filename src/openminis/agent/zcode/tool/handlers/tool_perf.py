"""Tool performance telemetry.

Ported from: packages/core/src/tool/handlers/tool-perf.ts
Original package: @zcode/core (tool/handlers/tool-perf)
"""

from __future__ import annotations

import hashlib
import math
import time
from typing import Any, Optional

from .bash_command_parser import analyze_bash_command
from .generated.bash_command_registry import BASH_COMMAND_REGISTRY

COMMAND_HASH_LENGTH = 16
COMMAND_HASH_EDGE_CHARS = 4096
COMMAND_CLASSIFY_PREFIX_CHARS = 2048
MAX_COMMAND_IDENTITY_PARSE_CHARS = 8 * 1024


def round_non_negative_ms(ms: float) -> int:
    """Mirrors ``roundNonNegativeMs(ms)``."""
    if not math.isfinite(ms):
        return 0
    return max(0, round(ms))


def elapsed_ms_since(started_at: int) -> int:
    """Mirrors ``elapsedMsSince(startedAt)`` — ``startedAt`` is ``Date.now()`` ms.

    PORT: TS ``Date.now()`` → ``int(time.time() * 1000)``.
    """
    return round_non_negative_ms(int(time.time() * 1000) - started_at)


def command_hash(command: str) -> str:
    """Mirrors ``commandHash(command)``."""
    h = hashlib.sha256()
    h.update(str(len(command)).encode("utf-8"))
    h.update(b"\0")
    h.update(command[:COMMAND_HASH_EDGE_CHARS].encode("utf-8"))
    if len(command) > COMMAND_HASH_EDGE_CHARS:
        h.update(b"\0")
        h.update(command[-COMMAND_HASH_EDGE_CHARS:].encode("utf-8"))
    return h.hexdigest()[:COMMAND_HASH_LENGTH]


def classify_command(command: str) -> str:
    """Mirrors ``classifyCommand(command)``.

    Bugfix: Bash command 可能包含超大 heredoc/inline script，遥测分类只看有界前缀，
    避免为了埋点复制并扫描完整命令，也避免正文里的 "npm test" 误导命令分类。
    """
    import re

    normalized = command[:COMMAND_CLASSIFY_PREFIX_CHARS].lstrip().lower()
    if not normalized:
        return "empty"
    if re.search(r"\b(?:npm|pnpm|yarn|bun)\s+(?:test|run\s+test|vitest|jest)\b", normalized):
        return "test"
    if re.search(r"\b(?:npm|pnpm|yarn|bun)\s+(?:install|add|update|remove)\b", normalized):
        return "package"
    if re.search(r"\bgit\b", normalized):
        return "git"
    if re.search(r"\b(?:rg|grep|find|fd)\b", normalized):
        return "search"
    if re.search(r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:build|compile)\b", normalized):
        return "build"
    if re.search(r"\b(?:curl|wget|gh\s+api)\b", normalized):
        return "network"
    return "other"


def classify_safe_command_identity(command: str) -> dict[str, Any]:
    """Mirrors ``classifySafeCommandIdentity(command)``."""
    # 类别和 hash 已经有界，但安全命令名仍会把完整 heredoc/inline script
    # 交给 parser。遥测不能为超大命令额外制造 O(n) CPU/内存；此时宁可少一个 count。
    if len(command) > MAX_COMMAND_IDENTITY_PARSE_CHARS:
        return {"name": "other"}
    analysis = analyze_bash_command(command)
    command_count = len(analysis.commands)
    if not command.strip():
        return {"count": 0, "name": "empty"}
    if analysis.has_parse_errors or analysis.has_unsupported_syntax or analysis.has_dynamic_words:
        return {"count": command_count, "name": "other"}
    if command_count != 1:
        return {
            "count": command_count,
            "name": "compound" if command_count > 1 else "other",
        }
    executable = _basename(analysis.commands[0].name if analysis.commands else "").lower()
    return {
        "count": command_count,
        # 隐私边界：只允许公开命令表中的静态可执行文件名进入远端 Trace；
        # 自定义脚本名和无法判定的动态表达式统一降级，绝不上传原始 token。
        "name": executable if executable in BASH_COMMAND_REGISTRY else "other",
    }


def workspace_kind(context: Any) -> str:
    """Mirrors ``workspaceKind(context)`` — "local" | "remote" | "unknown"."""
    return "unknown" if getattr(context, "runtime_scope", None) == "subagent" else "local"


def file_byte_count(content: str) -> int:
    """Mirrors ``fileByteCount(content)``."""
    return len(content.encode("utf-8"))


def read_tool_execution_telemetry(output: Any) -> Optional[dict[str, Any]]:
    """Mirrors ``readToolExecutionTelemetry(output)``.

    PORT: TS 的 ``perf`` 由 ``defineProperty`` 挂在任意 output 对象上，故 dict 与实例
    两种形态都要读。本仓部分 handler 返回 dict（glob/read/…），Bash 返回 ``BashOutput``
    dataclass，统一按「先 dict 键、后属性」读取。
    """
    if isinstance(output, dict):
        perf = output.get("perf")
    else:
        perf = getattr(output, "perf", None)
    if not isinstance(perf, dict):
        return None
    return compact_tool_execution_telemetry(perf)


def compact_tool_execution_telemetry(perf: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Mirrors ``compactToolExecutionTelemetry(perf)``."""
    compact: dict[str, Any] = {}
    if perf.get("total_ms", perf.get("totalMs")) is not None:
        compact["total_ms"] = perf.get("total_ms", perf.get("totalMs"))
    if perf.get("permission_wait_ms", perf.get("permissionWaitMs")) is not None:
        compact["permission_wait_ms"] = perf.get("permission_wait_ms", perf.get("permissionWaitMs"))
    if perf.get("detail"):
        compact["detail"] = perf["detail"]
    return compact if compact else None


def merge_tool_execution_telemetry(*items: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Mirrors ``mergeToolExecutionTelemetry(...items)``."""
    merged: dict[str, Any] = {}
    for item in items:
        if item:
            merged.update(item)
    return compact_tool_execution_telemetry(merged)


def attach_tool_execution_telemetry(output: Any, perf: Optional[dict[str, Any]]) -> Any:
    """Mirrors ``attachToolExecutionTelemetry(output, perf)``.

    PORT: TS uses a non-enumerable ``defineProperty``; Python dicts get a plain
    ``perf`` key.
    """
    if not perf:
        return output
    if isinstance(output, dict):
        # 非枚举语义在 Python dict 无对应，直接写入即可。
        output["perf"] = perf
    else:
        try:
            setattr(output, "perf", perf)
        except (AttributeError, TypeError):
            pass
    return output


def _basename(value: str) -> str:
    """Mirrors ``basename(value)`` — split on ``/`` or ``\\`` and take the last."""
    return value.replace("\\", "/").split("/")[-1] if value else ""


__all__ = [
    "round_non_negative_ms",
    "elapsed_ms_since",
    "command_hash",
    "classify_command",
    "classify_safe_command_identity",
    "workspace_kind",
    "file_byte_count",
    "read_tool_execution_telemetry",
    "compact_tool_execution_telemetry",
    "merge_tool_execution_telemetry",
    "attach_tool_execution_telemetry",
]
