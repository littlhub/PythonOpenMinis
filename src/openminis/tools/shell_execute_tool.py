"""shell_execute tool — run commands in the session's persistent sandbox shell.

Ported from: src/android/app/src/main/java/com/openminis/app/tools/AgentTools.kt
             (shellExecuteDefinition + its execution path through
              ExecutionCoordinator / PersistentShell)
Original package: com.openminis.app.tools

Schema kept identical to the Kotlin definition (name ``shell_execute``, params
``tool_title``/``command``/``timeout``/``delay``). Execution semantics match the
Android pipeline: a shared per-session ``PersistentShell`` via
``ExecutionCoordinator.execute``, with TerminalSanitizer cleanup applied there.

# PORT: unlike the stateless file tools, shell_execute touches shared shell
# state, so this tool is stateful by necessity. ``execute`` is therefore
# ``async`` — the agent loop awaits tools uniformly.
"""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, Optional

from ..core.logging import get_logger
from ..data.model.agent_tool_definition import AgentToolDefinition, AgentToolParam
from ..sandbox.execution_coordinator import ExecutionCoordinator
from .tool_execution_result import ToolExecutionResult

logger = get_logger(__name__)

__all__ = ["ShellExecuteTool"]


class ShellExecuteTool:
    """Kotlin ``shell_execute`` tool definition + execution."""

    NAME = "shell_execute"

    def __init__(
        self,
        coordinator: Optional[ExecutionCoordinator] = None,
        get_env: Optional[Callable[[], dict[str, str]]] = None,
    ) -> None:
        """``get_env`` supplies per-call user env vars (T124a snapshot)."""
        self.coordinator = coordinator or _default_coordinator()
        self.get_env = get_env

    @staticmethod
    def definition() -> AgentToolDefinition:
        return AgentToolDefinition(
            name=ShellExecuteTool.NAME,
            description=(
                "Execute a command in an isolated Linux process (Alpine Linux via PRoot). "
                "The command runs via /bin/sh -c with stdout and stderr merged. "
                "Environment variables persist between commands in the same session "
                "(cd and export persist too)."
            ),
            parameters={
                "tool_title": AgentToolParam(
                    "string",
                    "A concise 5-10 word summary of what this command does, shown to the "
                    "user (e.g. 'List project files', 'Install Python dependencies'). "
                    "Use the same language as the user.",
                ),
                "command": AgentToolParam(
                    "string",
                    "The shell command to execute. Supports multi-line commands directly — "
                    "no special escaping needed. Keep under 1000 chars; for longer scripts, "
                    "write to a file with file_write first, then run it.",
                ),
                "timeout": AgentToolParam(
                    "integer",
                    "Timeout in seconds (default: 900). Use a larger value for long-running "
                    "commands like package installs.",
                ),
                "delay": AgentToolParam(
                    "integer",
                    "Delay in seconds before execution begins. The tool blocks the agent "
                    "flow during this wait WITHOUT occupying the shell.",
                ),
            },
            required=["tool_title", "command"],
            property_ordering=["tool_title", "command", "timeout", "delay"],
        )

    async def _one_shot_fallback(
        self, session_id: str, command: str, timeout: int,
        env_extra: Optional[dict[str, str]] = None,
    ) -> Optional[tuple[str, int]]:
        """持久 shell 失效时的一次性执行兜底（同步 subprocess，线程内跑）。

        返回 ``(output, exit_code)``；连兜底都失败（找不到 shell 等）返回
        ``None``，让上层继续走持久 shell 的失败提示。
        """
        import subprocess as _sp

        from .path_utils import workspace_root

        try:
            from ..sandbox.persistent_shell import detect_shell_spec
            from ..core.logging import get_logger

            spec = detect_shell_spec()
        except Exception:
            return None
        cwd = None
        coordinator = self.coordinator
        overrides = getattr(coordinator, "_cwd_overrides", None)
        if isinstance(overrides, dict):
            cwd = overrides.get(session_id) or str(workspace_root())
        try:

            def _run() -> tuple[str, int]:
                run_env = None
                if env_extra:
                    import os as _os

                    run_env = {**_os.environ, **env_extra}
                if spec.name == "cmd.exe":
                    argv = [spec.executable, "/d", "/s", "/c", command]
                else:
                    argv = [spec.executable, "--noprofile", "--norc", "-c", command]
                proc = _sp.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=max(float(timeout), 1.0),
                    cwd=cwd,
                    env=run_env,
                    errors="replace",
                )
                out = (proc.stdout or "") + (proc.stderr or "")
                return out.strip() or "(no output)", proc.returncode

            result = await asyncio.to_thread(_run)
            logger.warning(
                "PersistentShell[%s] unusable (exit=-1); one-shot fallback ran "
                "the command (exit=%d)", session_id, result[1],
            )
            return result
        except Exception as exc:
            logger.warning("one-shot shell fallback failed: %s", exc)
            return None

    async def execute(
        self,
        args_json: str,
        session_id: str,
        line_callback: Optional[Callable[[str], None]] = None,
    ) -> ToolExecutionResult:
        """Kotlin ``execute(argsJson, sessionId)`` — async shell dispatch."""
        try:
            args = json.loads(args_json)
        except ValueError as exc:
            return ToolExecutionResult(f"Error: invalid JSON args: {exc}", True,
                                       tool_title=ShellExecuteTool.NAME)

        command = str(args.get("command", ""))
        tool_title = str(args.get("tool_title", ShellExecuteTool.NAME))
        if not command.strip():
            return ToolExecutionResult("Error: 'command' is required", True,
                                       tool_title=tool_title)
        try:
            timeout = int(args.get("timeout", 900))
        except (TypeError, ValueError):
            timeout = 900
        try:
            delay = int(args.get("delay", 0))
        except (TypeError, ValueError):
            delay = 0
        if delay > 0:
            await asyncio.sleep(delay)

        env = self.get_env() if self.get_env is not None else _load_env_extra()
        result = await self.coordinator.execute(
            session_id,
            command,
            timeout=max(float(timeout), 1.0),
            line_callback=line_callback,
            env_vars=env,
        )

        output = result.output or "(no output)"
        if result.exit_code == -1 and "[Write error" not in output:
            # 持久 shell 没跑成（app 的事件循环拓扑下偶发秒死，exit=-1）。
            # 兜底：用一次性 ``bash -c`` 在工作线程里同步执行 —— 不依赖
            # 持久 shell 的生命周期，命令必须真的跑起来。
            one_shot = await self._one_shot_fallback(session_id, command, timeout, env)
            if one_shot is not None:
                out_text, code = one_shot
                if code != 0:
                    out_text = (
                        f"$ {command}\n{out_text}\n"
                        f"[exit code: {code}]\n"
                        "请先分析上面的报错原因（命令不存在？依赖缺失？路径不对？），"
                        "修正命令或环境后再重试；连续失败 2 次就把问题如实报告用户，"
                        "不要改用无关工具（如 read_image）来回避。"
                    )
                return ToolExecutionResult(
                    out_text, code == 0, tool_title=tool_title,
                )
        if result.exit_code == 124:
            output += "\n[command timed out]"
        if result.exit_code != 0:
            # 失败必须把「跑的是什么、退出码、输出」完整交给模型，否则它
            # 拿到一句干瘪报错没法分析原因，只会乱试别的工具。
            output = (
                f"$ {command}\n{output}\n"
                f"[exit code: {result.exit_code}]\n"
                "请先分析上面的报错原因（命令不存在？依赖缺失？路径不对？），"
                "修正命令或环境后再重试；连续失败 2 次就把问题如实报告用户，"
                "不要改用无关工具（如 read_image）来回避。"
            )
        return ToolExecutionResult(
            output, result.exit_code == 0, tool_title=tool_title,
        )


_shared: Optional[ExecutionCoordinator] = None


def _load_env_extra() -> Optional[dict[str, str]]:
    """读取「环境变量」页配置的 ``sandbox.envExtra``（JSON KV）。

    ``ShellExecuteTool()`` 默认没人传 ``get_env``，导致用户在设置页配的
    环境变量（如 ``OPENAI_API_KEY``）根本进不了 shell —— 技能脚本里
    ``$OPENAI_API_KEY`` 永远是空。这里作为默认来源；显式注入仍优先。
    """
    try:
        from ..core.prefs import get_prefs

        raw = get_prefs().get_string("sandbox.envExtra") or ""
        if not raw.strip():
            return None
        import json as _json

        data = _json.loads(raw)
        if isinstance(data, dict):
            out = {str(k): str(v) for k, v in data.items() if str(v).strip()}
            return out or None
    except Exception:  # pragma: no cover - 配置损坏不该拖垮 shell
        logger.debug("envExtra unreadable", exc_info=True)
    return None


def _default_coordinator() -> ExecutionCoordinator:
    """Module-level shared coordinator — the shell cache is app-global."""
    global _shared
    if _shared is None:
        _shared = ExecutionCoordinator()
    return _shared


def get_coordinator() -> ExecutionCoordinator:
    """Access the shared coordinator (creating it if needed).

    Callers (the Web server) use this to register per-session sandbox roots
    before a chat turn starts, so filed sessions boot inside their workspace.
    """
    return _default_coordinator()


def install_coordinator(coordinator: ExecutionCoordinator) -> None:
    """Point the default tool at an externally-owned coordinator (server/CLI)."""
    global _shared
    _shared = coordinator
