"""Terminal sandbox pane.

Ported from: src/android/app/src/main/java/com/openminis/app/ui/terminal/**
(and its ViewModel). The Android version drives a real PTY into the Alpine
sandbox; this pane talks to the same kernel surface so behaviour stays
consistent once ``openminis.sandbox`` is ported. Until then it runs commands
through the local shell with a hard timeout — see the ``# PORT:`` notes.
"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, RichLog

from ...core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["SandboxPane"]


class SandboxPane(Vertical):
    """Command entry + scrolling output."""

    DEFAULT_CSS = """
    SandboxPane { layout: vertical; height: 1fr; }
    #sandbox-output { height: 1fr; border: round $accent; }
    #sandbox-bar { height: auto; }
    """

    def compose(self) -> ComposeResult:
        self._log = RichLog(id="sandbox-output", wrap=True, markup=False)
        self._input = Input(placeholder="$ enter a command", id="sandbox-input")
        with Vertical():
            yield self._log
            with Horizontal(id="sandbox-bar"):
                yield self._input
                yield Button("Run", variant="primary", id="sandbox-run")
                yield Button("Clear", id="sandbox-clear")

    def on_mount(self) -> None:
        self._log.write("OpenMinis sandbox — type a command and press Enter.")

    # --- events ----------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sandbox-run":
            self._submit()
        elif event.button.id == "sandbox-clear":
            self._log.clear()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    # --- behaviour -------------------------------------------------------
    def _submit(self) -> None:
        command = self._input.value.strip()
        if not command:
            return
        self._input.value = ""
        self._log.write(f"[dim]$ {command}[/dim]")
        self.run_worker(self._run(command), exclusive=True)

    async def _run(self, command: str) -> None:
        # PORT: once openminis.sandbox.ShellExecutor is ported this should call
        # into it (and honour sandbox.shellTimeoutSeconds) instead of spawning
        # a local shell.
        timeout = self._timeout_seconds()
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as e:
            self._log.write(f"[red]spawn failed: {e}[/red]")
            return

        assert proc.stdout is not None
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                if not line:
                    break
                self._log.write(line.decode(errors="replace").rstrip())
        except TimeoutError:
            proc.kill()
            self._log.write(f"[red]timeout after {timeout}s — killed[/red]")
            return
        code = await proc.wait()
        self._log.write(f"[dim]exit {code}[/dim]")

    def _timeout_seconds(self) -> float:
        """Read ``sandbox.shellTimeoutSeconds`` from the config registry."""
        try:
            from ...config.config_registry import ConfigRegistry

            field = ConfigRegistry.get().resolve_field("sandbox.shellTimeoutSeconds")
            if field is not None:
                value = field.read()
                seconds = getattr(value, "value", 120)
                return float(seconds)
        except Exception:
            logger.debug("timeout config unavailable, defaulting", exc_info=True)
        return 120.0

    def refresh_data(self) -> None:
        """Called by the app-level refresh action."""
        self._log.write("[dim]refreshed[/dim]")
