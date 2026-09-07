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
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            start = time.monotonic()
            shell = await self._get_or_create_shell(session_id)

            # Full-snapshot env injection (T124a semantics).
            env = dict(env_vars or {})
            previous = self._last_injected_keys.get(session_id, set())
            if env or previous:
                await shell.apply_environment(env, previous_keys=previous)
                self._last_injected_keys[session_id] = set(env)

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
        await shell.ensure_started()
        if shell.is_alive or shell._process is not None:  # noqa: SLF001
            self._shells[session_id] = shell
        else:
            logger.error("ExecutionCoordinator[%s]: shell failed to start", session_id)
        return shell

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
