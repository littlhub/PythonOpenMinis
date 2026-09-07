"""Session list pane.

Ported from: src/android/app/src/main/java/com/openminis/app/ui/sessions/SessionListScreen.kt
and ``SessionListViewModel.kt``.

PORT-STATUS: partial — the list renders from an in-memory placeholder until
``openminis.data`` (Room → SQLAlchemy) is ported. The column set and sorting
match the original screen.
"""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ...core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["SessionsPane"]


class SessionsPane(Vertical):
    """Session table (title, model, updated, message count)."""

    DEFAULT_CSS = """
    SessionsPane { layout: vertical; height: 1fr; }
    #sessions-table { height: 1fr; }
    #sessions-status { height: auto; color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        self._table = DataTable(id="sessions-table", cursor_type="row")
        self._status = Static("", id="sessions-status")
        with Vertical():
            yield self._table
            yield self._status

    def on_mount(self) -> None:
        self._table.add_columns("title", "model", "updated", "messages")
        self.refresh_data()

    def refresh_data(self) -> None:
        self._table.clear()
        # PORT: replace with a query against openminis.data.repository.ChatRepository
        # once the data layer lands. Shape mirrors ChatSessionEntity.
        for session in _placeholder_sessions():
            self._table.add_row(
                session["title"],
                session["model"],
                _format_ts(session["updated_at_ms"]),
                str(session["message_count"]),
            )
        self._status.update(
            f"{self._table.row_count} sessions (data layer not ported — sample rows)"
        )


def _placeholder_sessions() -> list[dict[str, object]]:
    now_ms = int(datetime.now().timestamp() * 1000)
    return [
        {
            "title": "Weekly reading notes",
            "model": "claude-sonnet-4-5",
            "updated_at_ms": now_ms - 3_600_000,
            "message_count": 12,
        },
        {
            "title": "Alpine sandbox setup",
            "model": "gpt-5",
            "updated_at_ms": now_ms - 86_400_000,
            "message_count": 34,
        },
    ]


def _format_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
