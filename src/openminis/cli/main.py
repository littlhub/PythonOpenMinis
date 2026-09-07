"""OpenMinis command-line entrypoint.

Ported from: the ``minis-config`` / ``minis-mcp-cli`` command surfaces that the
Android app exposes to its sandbox (see
``src/android/app/src/main/assets/default_mount/usr/local/lib/minis-mcp-cli/main.py``).

There is no single Kotlin counterpart — the Android app exposes these as
intents and as the MCP CLI inside the sandbox. This module unifies them into
one Typer app so the Python port is usable from a terminal.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from ..config.config_error import ConfigError
from ..config.config_registry import ConfigRegistry
from ..config.config_value import ConfigValue
from ..core.logging import get_logger, setup_logging

logger = get_logger(__name__)
console = Console()

app = typer.Typer(
    name="minis",
    help="OpenMinis — your private, on-device AI agent (Python port).",
    no_args_is_help=True,
    add_completion=False,
)

config_app = typer.Typer(help="Read and write app configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _registry() -> ConfigRegistry:
    try:
        return ConfigRegistry.get()
    except RuntimeError:
        return ConfigRegistry.init()


def _render_value(value: ConfigValue, as_json: bool) -> str:
    return value.json_string() if as_json else value.display_string


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
@config_app.command("topics")
def config_topics() -> None:
    """List config topics (first path segment)."""
    for topic in _registry().topics():
        console.print(topic)


@config_app.command("list")
def config_list(
    topic: str | None = typer.Argument(None, help="Restrict to one topic."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """List visible config paths with their current values."""
    registry = _registry()
    fields = registry.fields(topic) if topic else [
        registry.resolve_field(p) for p in registry.all_visible_field_paths()
    ]
    fields = [f for f in fields if f is not None]

    if as_json:
        console.print_json(
            json.dumps({f.path: _render_value(f.read(), True) for f in fields})
        )
        return

    table = Table(title=f"config ({len(fields)} fields)")
    table.add_column("path", style="cyan", no_wrap=True)
    table.add_column("value", style="green")
    table.add_column("schema", style="dim")
    table.add_column("description", overflow="fold")
    for f in fields:
        table.add_row(
            f.path,
            f.read().display_string,
            f.value_schema.help_description,
            f.description,
        )
    console.print(table)


@config_app.command("get")
def config_get(
    path: str = typer.Argument(..., help="Dot path, e.g. appearance.theme"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Read one config value."""
    field = _registry().resolve_field(path)
    if field is None:
        console.print(f"[red]unknown_path: {path}[/red]")
        raise typer.Exit(code=1)
    console.print(_render_value(field.read(), as_json))


@config_app.command("set")
def config_set(
    path: str = typer.Argument(..., help="Dot path, e.g. appearance.theme"),
    value: str = typer.Argument(..., help="JSON-encoded value, e.g. \"dark\" or 42"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Write one config value (JSON-encoded)."""
    registry = _registry()
    field = registry.resolve_field(path)
    if field is None:
        console.print(f"[red]unknown_path: {path}[/red]")
        raise typer.Exit(code=1)

    parsed = ConfigValue.decode(value)
    if parsed is None:
        console.print(f"[red]invalid_value: cannot parse {value!r} as JSON[/red]")
        raise typer.Exit(code=1)

    try:
        field.value_schema.validate(parsed)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=e.code) from None

    old = field.read()
    if not yes:
        console.print(f"[dim]{path}[/dim]")
        console.print(f"  - {old.display_string}")
        console.print(f"  + {parsed.display_string}")
        if field.risk.value != "NORMAL":
            console.print(f"[yellow]risk: {field.risk.value}[/yellow]")
        if not typer.confirm("Apply?"):
            raise typer.Abort()

    field.write(parsed)
    console.print(f"[green]ok[/green] {path} = {parsed.display_string}")


@config_app.command("help")
def config_help(topic: str = typer.Argument(..., help="Topic name.")) -> None:
    """Show schema help for a topic."""
    registry = _registry()
    fields = registry.fields(topic)
    if not fields:
        console.print(f"[red]unknown topic: {topic}[/red]")
        raise typer.Exit(code=1)
    for f in fields:
        console.print(f"[cyan]{f.path}[/cyan] — {f.value_schema.help_description}")
        console.print(f"    {f.description}")


# ---------------------------------------------------------------------------
# other entrypoints
# ---------------------------------------------------------------------------
@app.command()
def tui() -> None:
    """Launch the Textual terminal UI."""
    from ..tui.app import main as tui_main

    tui_main()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8765, help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Reload on code changes."),
) -> None:
    """Run the FastAPI server (Web UI backend)."""
    import uvicorn

    uvicorn.run("openminis.server.main:app", host=host, port=port, reload=reload)


@app.command()
def version() -> None:
    """Print the version."""
    from .. import __version__

    console.print(f"openminis {__version__}")


@app.command()
def doctor() -> None:
    """Report environment health (paths, config, dependencies)."""
    from ..core.context import app_context

    ctx = app_context()
    rows: dict[str, Any] = {
        "version": __import__("openminis").__version__,
        "data_dir": str(ctx.data_dir),
        "cache_dir": str(ctx.cache_dir),
        "files_dir": str(ctx.files_dir),
        "databases_dir": str(ctx.databases_dir),
        "workspace": str(ctx.external_files_dir),
        "assets_dir": str(ctx.assets_dir),
        "config_fields": len(_registry().all_visible_field_paths()),
    }
    for k, v in rows.items():
        console.print(f"[cyan]{k}[/cyan]: {v}")


def main() -> None:
    setup_logging()
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
