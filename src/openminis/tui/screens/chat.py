"""Chat pane.

Ported from: src/android/app/src/main/java/com/openminis/app/ui/chat/ChatScreen.kt
and ``ChatViewModel.kt``.

Per PORTING.md §6.1 the ViewModel half belongs in
``openminis.ui.chat.chat_view_model``; this file is only the Textual view
(§6.3). The agent kernel is not ported yet, so the pane renders locally and
streams through the same frame contract the kernel will use.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, RichLog

from ...core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["ChatPane"]

_ROLE_STYLE = {
    "user": "[bold cyan]you[/bold cyan]",
    "assistant": "[bold green]minis[/bold green]",
    "system": "[dim]system[/dim]",
}


class ChatPane(Vertical):
    """Streaming transcript + composer."""

    DEFAULT_CSS = """
    ChatPane { layout: vertical; height: 1fr; }
    #chat-log { height: 1fr; border: round $accent; }
    #chat-bar { height: auto; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._messages: list[dict[str, str]] = []

    def compose(self) -> ComposeResult:
        self._log = RichLog(id="chat-log", wrap=True, markup=True)
        self._input = Input(placeholder="Ask Minis…  (/help for commands)", id="chat-input")
        with Vertical():
            yield self._log
            with Horizontal(id="chat-bar"):
                yield self._input
                yield Button("Send", variant="primary", id="chat-send")

    def on_mount(self) -> None:
        self._log.write("[dim]OpenMinis — provider/agent kernel pending port.[/dim]")

    # --- events ----------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "chat-send":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    # --- behaviour -------------------------------------------------------
    def _submit(self) -> None:
        text = self._input.value.strip()
        if not text:
            return
        self._input.value = ""

        if text.startswith("/"):
            self._command(text[1:])
            return

        self._append("user", text)
        # PORT: replace with a call into openminis.agent once ported — the
        # assistant reply should stream provider deltas here.
        self._append("assistant", "(agent kernel not ported yet) " + self._echo(text))

    def _echo(self, text: str) -> str:
        return f"received {len(text)} chars"

    def _command(self, name: str) -> None:
        if name in ("help", "?"):
            self._log.write("[dim]/help  show this message[/dim]")
            self._log.write("[dim]/clear empty the transcript[/dim]")
            self._log.write("[dim]/config  list config topics[/dim]")
        elif name == "clear":
            self._log.clear()
            self._messages.clear()
        elif name == "config":
            from ...config.config_registry import ConfigRegistry

            try:
                registry = ConfigRegistry.get()
            except RuntimeError:
                registry = ConfigRegistry.init()
            self._log.write("[dim]topics: " + ", ".join(registry.topics()) + "[/dim]")
        else:
            self._log.write(f"[red]unknown command: /{name}[/red]")

    def _append(self, role: str, text: str) -> None:
        self._messages.append({"role": role, "content": text})
        self._log.write(f"{_ROLE_STYLE.get(role, role)}: {text}")

    def refresh_data(self) -> None:
        self._log.write("[dim]— refreshed —[/dim]")
