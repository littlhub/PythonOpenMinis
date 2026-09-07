"""Settings pane — browse and edit the config registry.

Ported from: src/android/app/src/main/java/com/openminis/app/ui/settings/**
(mirrors the Compose settings tree). Values come from the same
``ConfigRegistry`` the CLI and the FastAPI server use.
"""

from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static

from ...config.config_error import ConfigError
from ...config.config_registry import ConfigRegistry
from ...config.config_value import ConfigValue
from ...core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["SettingsPane"]


class SettingsPane(Vertical):
    """Config table + inline editor."""

    DEFAULT_CSS = """
    SettingsPane { layout: vertical; height: 1fr; }
    #settings-table { height: 1fr; }
    #settings-bar { height: auto; }
    #settings-status { height: auto; color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        self._table = DataTable(id="settings-table", cursor_type="row")
        self._editor = Input(placeholder="new value (JSON)", id="settings-value")
        self._status = Static("", id="settings-status")
        with Vertical():
            yield self._table
            with Horizontal(id="settings-bar"):
                yield self._editor
                yield Button("Apply", variant="primary", id="settings-apply")
                yield Button("Reload", id="settings-reload")
            yield self._status

    def on_mount(self) -> None:
        self._table.add_columns("path", "value", "schema", "risk")
        self.refresh_data()

    # --- events ----------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-apply":
            self._apply()
        elif event.button.id == "settings-reload":
            self.refresh_data()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = self._table.get_row_at(event.cursor_row)
        if row:
            self._editor.value = str(row[1])

    # --- behaviour -------------------------------------------------------
    def refresh_data(self) -> None:
        self._table.clear()
        registry = self._registry()
        for path in registry.all_visible_field_paths():
            field = registry.resolve_field(path)
            if field is None:
                continue
            self._table.add_row(
                path,
                field.read().display_string,
                field.value_schema.help_description,
                field.risk.value,
            )
        self._status.update(f"{self._table.row_count} fields")

    def _apply(self) -> None:
        path = self._selected_path()
        if path is None:
            self._status.update("select a row first")
            return

        field = self._registry().resolve_field(path)
        if field is None:
            self._status.update(f"unknown path: {path}")
            return

        parsed = ConfigValue.decode(self._editor.value)
        if parsed is None:
            self._status.update(f"cannot parse {self._editor.value!r} as JSON")
            return
        try:
            field.value_schema.validate(parsed)
        except ConfigError as e:
            self._status.update(str(e))
            return

        field.write(parsed)
        self._status.update(f"ok: {path} = {parsed.display_string}")
        self.refresh_data()

    def _selected_path(self) -> str | None:
        if self._table.cursor_row < 0 or self._table.row_count == 0:
            return None
        row = self._table.get_row_at(self._table.cursor_row)
        return str(row[0]) if row else None

    def _registry(self) -> ConfigRegistry:
        try:
            return ConfigRegistry.get()
        except RuntimeError:
            return ConfigRegistry.init()
