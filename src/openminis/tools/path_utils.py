"""Shared workspace-path resolution for the first-agent style tools
(``ls`` / ``search_files``).

These tools operate on the desktop workspace (``app_context().external_files_dir``
— the same root the per-session ``file_*`` tools are scoped under). A helper
lives here rather than in each module so every tool applies the identical
prefix stripping and escape guard.

Accepted path forms:

- ``""`` / ``"."``                     → the workspace root itself
- ``/var/minis/workspace/...``         → workspace-root-relative (OpenMinis sandbox notation)
- ``/var/minis/...`` or ``/workspace/...`` → alias of the workspace root
- ``~/...``                            → workspace-root-relative convenience alias
- ``relative/path``                    → workspace-root-relative

Anything that resolves outside the workspace root is refused (returns ``None``),
mirroring the escape guard in :mod:`openminis.tools.file_read_tool`.
"""

from __future__ import annotations

from pathlib import Path

from ..core.context import app_context
from ..core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["workspace_root", "resolve_workspace_path", "PREFIXES"]


#: Path prefixes that are transparently mapped onto the workspace root.
PREFIXES = ("/var/minis/workspace", "/workspace", "/var/minis")


def workspace_root() -> Path:
    """The single directory these tools read from / write under."""
    return app_context().external_files_dir


def resolve_workspace_path(path: str | None) -> Path | None:
    """Resolve ``path`` against the workspace root, or ``None`` if refused."""
    root = workspace_root().resolve()
    candidate = (path or "").strip()
    if not candidate or candidate in {".", "./"}:
        return root
    if candidate == "~":
        candidate = ""
    elif candidate.startswith("~/"):
        candidate = candidate[2:]
    for prefix in PREFIXES:
        if candidate == prefix:
            candidate = ""
            break
        if candidate.startswith(prefix + "/"):
            candidate = candidate[len(prefix) + 1 :]
            break
    candidate = candidate.replace("\\", "/").lstrip("/")

    resolved = (root / candidate).resolve() if candidate else root
    if resolved != root and root not in resolved.parents:
        logger.warning("workspace tool rejected path outside root: %s", path)
        return None
    return resolved
