"""Coordinate command execution across persistent per-session shells.

Ported from: src/android/app/src/main/java/com/openminis/app/sandbox/ExecutionCoordinator.kt
Original package: com.openminis.app.sandbox

Semantics preserved from Kotlin:
- One ``PersistentShell`` per sessionId, created lazily under an asyncio lock.
- Full-snapshot env-var injection: the previously-injected key set is passed to
  ``apply_environment`` so vars removed since the last turn get ``unset``.
- Raw output passes through ``TerminalSanitizer.sanitize`` then
  ``truncate_if_needed``; non-zero (and non-124) exits append "(exit code: N)".
- ``PRootKernel.boot`` auto-boot is a no-op here: the shell IS the sandbox on
  the desktop port (see the ``# PORT`` note in ``persistent_shell.py``).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from openminis.core.logging import get_logger
from openminis.sandbox.persistent_shell import PersistentShell
from openminis.sandbox.terminal_sanitizer import TerminalSanitizer

__all__ = ["ExecutionCoordinator", "CommandResult"]

logger = get_logger(__name__)

# Kotlin default: 600 s.
DEFAULT_COMMAND_TIMEOUT = 600.0


@dataclass
class CommandResult:
    """Kotlin ``CommandResult`` — output, exit code and wall-clock duration."""

    output: str
    exit_code: int
    duration_ms: int


class ExecutionCoordinator:
    """Kotlin ``ExecutionCoordinator``."""

    def __init__(self, extra_env: Optional[dict[str, str]] = None) -> None:
        self._shells: dict[str, PersistentShell] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Names injected per session on the last apply_environment call, so the
        # next one can unset whatever the user has since removed.
        self._last_injected_keys: dict[str, set[str]] = {}
        # 上一次注入的**完整键值**：值没变就不必再走一次 shell 往返。
        self._last_injected_env: dict[str, dict[str, str]] = {}
        # Process-wide extra env merged into every spawned shell (read from the
        # ``sandbox.envExtra`` config field; safe to set empty).
        self._extra_env: dict[str, str] = dict(extra_env or {})
        # Per-session sandbox roots. When a session belongs to a workspace, the
        # server registers its folder's sandbox dir here so the session shell is
        # created inside that workspace instead of the global default
        # (``external_files_dir/<session_id>``).
        self._cwd_overrides: dict[str, Path] = {}

    def set_extra_env(self, env: dict[str, str]) -> None:
        """Replace the merged env applied to every newly created session shell.

        Already-running shells pick up the new vars on their next
        :meth:`execute` call via :meth:`apply_environment`.
        """
        self._extra_env = dict(env)

    def extra_env(self) -> dict[str, str]:
        return dict(self._extra_env)

    def set_session_cwd(self, session_id: str, path: Path) -> None:
        """Root the session's shell inside ``path`` (created on first boot).

        Only shells that don't exist yet honour the override — a running shell
        keeps the directory it was spawned in, mirroring how ``cd`` persists.
        """
        self._cwd_overrides[session_id] = path

    def clear_session_cwd(self, session_id: str) -> None:
        self._cwd_overrides.pop(session_id, None)

    def cwd_for(self, session_id: str) -> str:
        """会话当前的工作目录（沙箱守卫记录「调用目录」用）。"""
        override = self._cwd_overrides.get(session_id)
        if override is not None:
            return str(override)
        shell = self._shells.get(session_id)
        for attr in ("cwd", "_cwd", "workdir"):
            value = getattr(shell, attr, None)
            if value:
                return str(value)
        return ""

    def shell_for(self, session_id: str) -> Optional[PersistentShell]:
        """[diag] accessor — mirror of Kotlin's private getOrCreateShell."""
        return self._shells.get(session_id)

    async def execute(
        self,
        session_id: str,
        command: str,
        timeout: float = DEFAULT_COMMAND_TIMEOUT,
        line_callback: Optional[Callable[[str], None]] = None,
        env_vars: Optional[dict[str, str]] = None,
    ) -> CommandResult:
        """Kotlin ``execute(sessionId, command, timeout, lineCallback)``."""
        # 兜底：把沙箱写法还原成本机路径。
        #
        # 主路径已经在 ``ShellExecuteTool`` 里换过了（那里换是**为了守卫能看到真
        # 实路径**，技能库白名单才匹配得上）；这里再兜一次是因为 coordinator 也会
        # 被别的调用方直接用。已经换过的文本里不含 ``/var/minis``，等于空操作。
        try:
            from ..tools.path_utils import unscrub_sandbox_paths

            command = unscrub_sandbox_paths(command)
        except Exception:  # pragma: no cover - 路径工具不可用就不换
            logger.debug("unscrub sandbox paths failed", exc_info=True)
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            start = time.monotonic()
            shell = await self._get_or_create_shell(session_id)

            # Full-snapshot env injection (T124a semantics).
            #
            # 只在**真的变了**的时候才注入：注入本身是一次完整的 shell 往返
            # （``export …`` 要等回显标记），本机实测每次 ~400ms。而配置了
            # ``sandbox.envExtra`` 的用户，环境变量几乎每轮都不变 —— 每次都重发
            # 等于给每个工具调用白加一次往返。会话第一次执行仍会注入
            # （``_last_injected_env`` 在新建 shell 时被清掉）。
            env = dict(env_vars or {})
            previous = self._last_injected_keys.get(session_id, set())
            if (env or previous) and self._last_injected_env.get(session_id) != env:
                await shell.apply_environment(env, previous_keys=previous)
                self._last_injected_keys[session_id] = set(env)
                self._last_injected_env[session_id] = dict(env)

            raw_output, exit_code = await shell.execute_command(
                command, timeout=timeout, line_callback=line_callback
            )

            duration_ms = int((time.monotonic() - start) * 1000)
            sanitized = TerminalSanitizer.sanitize(raw_output)
            truncated = TerminalSanitizer.truncate_if_needed(sanitized)
            if exit_code not in (0, 124):
                output = f"{truncated}\n(exit code: {exit_code})"
            else:
                output = truncated
            return CommandResult(output=output, exit_code=exit_code, duration_ms=duration_ms)

    async def _get_or_create_shell(self, session_id: str) -> PersistentShell:
        """Kotlin ``getOrCreateShell`` — lazy creation, no duplicates.

        # PORT: Kotlin guards with a global mutex; Python's per-session lock in
        # ``execute`` serialises callers, and this re-checks the map so two
        # coroutines can never both spawn a shell for the same session.
        """
        existing = self._shells.get(session_id)
        if existing is not None and existing.is_alive:
            return existing
        shell = PersistentShell(
            session_id,
            cwd=self._cwd_overrides.get(session_id),
            env=self._extra_env or None,
        )
        # 新 shell 里什么都没有 —— 让下一次 execute 重新注入一遍环境变量。
        self._last_injected_env.pop(session_id, None)
        self._last_injected_keys.pop(session_id, None)
        await shell.ensure_started()
        if shell.is_alive or shell._process is not None:  # noqa: SLF001
            self._shells[session_id] = shell
        else:
            logger.error("ExecutionCoordinator[%s]: shell failed to start", session_id)
        return shell

    async def warm(self, session_id: str) -> None:
        """预热会话的 shell。

        首条命令要连 bash 启动一起等（本机实测 ~1.1s，之后每条 ~0.4s）。轮次一
        开始就在后台叫一声，用户的第一条工具命令就不必再等这段启动时间 ——
        带工具的回合动辄十几条命令，这一段是白等。

        注意：光 ``ensure_started()`` 不够（它只 spawn 不等就绪，几十毫秒就返回），
        得**真跑一条空命令**把启动开销吃在这里。``echo`` 在 bash 与 cmd 下都成立。
        """
        try:
            shell = await self._get_or_create_shell(session_id)
            if shell is not None:
                await shell.execute_command("echo", timeout=15)
        except Exception:  # pragma: no cover - 预热失败不影响正常执行
            logger.debug("shell warm-up failed for %s", session_id, exc_info=True)

    async def stop_session(self, session_id: str) -> None:
        """Kill a session's shell (e.g. session deletion)."""
        shell = self._shells.pop(session_id, None)
        self._last_injected_keys.pop(session_id, None)
        self._cwd_overrides.pop(session_id, None)
        if shell is not None:
            await shell.stop()

    async def shutdown(self) -> None:
        for shell in list(self._shells.values()):
            await shell.stop()
        self._shells.clear()
