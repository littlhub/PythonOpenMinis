"""Textual terminal UI.

Ported from: the Compose navigation surface in
``src/android/app/src/main/java/com/openminis/app/ui/navigation`` and the
screens under ``ui/chat``, ``ui/sessions``, ``ui/settings``, ``ui/terminal``.

Per PORTING.md §6.3, each main screen gets a Textual ``Screen`` that consumes
the same Python ViewModel/kernel objects the FastAPI server uses — the UI is an
adapter, never the source of truth.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Static, TabbedContent, TabPane

from ..core.logging import get_logger, setup_logging

logger = get_logger(__name__)

__all__ = ["OpenMinisApp", "main"]


class OpenMinisApp(App[None]):
    """OpenMinis TUI shell.

    Mirrors the Android app's bottom-tab navigation: Chat / Sessions /
    Sandbox / Settings.
    """

    TITLE = "OpenMinis"
    SUB_TITLE = "on-device AI agent"
    CSS = """
    Screen { layout: vertical; }
    #tabs { height: 1fr; }
    .pane { padding: 1 2; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Chat", id="chat"):
                yield from self._chat_pane()
            with TabPane("Sessions", id="sessions"):
                yield from self._sessions_pane()
            with TabPane("Sandbox", id="sandbox"):
                yield from self._sandbox_pane()
            with TabPane("Settings", id="settings"):
                yield from self._settings_pane()
        yield Footer()

    # --- panes -----------------------------------------------------------
    def _chat_pane(self) -> ComposeResult:
        from .screens.chat import ChatPane

        yield ChatPane()

    def _sessions_pane(self) -> ComposeResult:
        from .screens.sessions import SessionsPane

        yield SessionsPane()

    def _sandbox_pane(self) -> ComposeResult:
        from .screens.sandbox import SandboxPane

        yield SandboxPane()

    def _settings_pane(self) -> ComposeResult:
        from .screens.settings import SettingsPane

        yield SettingsPane()

    # --- actions ---------------------------------------------------------
    def action_refresh(self) -> None:
        """Re-read every visible pane from the kernel."""
        for pane in self.query("Pane").results():  # pragma: no cover - runtime
            refresh = getattr(pane, "refresh_data", None)
            if callable(refresh):
                refresh()


def main() -> None:
    setup_logging()
    OpenMinisApp().run()


if __name__ == "__main__":  # pragma: no cover
    main()
