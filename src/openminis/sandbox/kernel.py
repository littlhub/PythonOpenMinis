"""PRoot sandbox kernel — Linux path resolution for tool file I/O.

Ported from: src/android/app/src/main/java/com/openminis/app/sandbox/PRootKernel.kt
Original package: com.openminis.app.sandbox

# PORT: forward-reference stub. Implements the three path helpers the tools port
# imports. Real PRoot session mapping lands in the sandbox/ port; this keeps
# the guest path → host path resolution (per-session + global) working so the
# file tools import and run. Paths resolve under ``AppContext.external_files_dir``.
"""

from __future__ import annotations

import re
from pathlib import Path

from openminis.core.context import app_context

__all__ = ["PRootKernel"]

# linuxPath under a read-only mounted folder → rejected by file_write/file_edit.
_READ_ONLY_MOUNTS = ("/var/minis/mounts/readonly",)


class PRootKernel:
    """Guest→host filesystem bridge for the Linux sandbox (Alpine via PRoot)."""

    @staticmethod
    def _root() -> Path:
        return app_context().external_files_dir

    @staticmethod
    def isLinuxPathUnderReadOnlyMount(path: str) -> bool:
        """Kotlin: ``isLinuxPathUnderReadOnlyMount(path)``."""
        p = path.replace("\\", "/")
        return any(p == m or p.startswith(m + "/") for m in _READ_ONLY_MOUNTS)

    @staticmethod
    def resolveSessionHostPath(sessionId: str, path: str, context: object = None) -> Path | None:
        """Kotlin: ``resolveSessionHostPath(sessionId, path, context)``.

        Routes ``/var/minis/{workspace,attachments,offloads,browser}/...`` to
        this session's host dir; everything else falls back to the global map.
        """
        guest = path.replace("\\", "/")
        if not guest.startswith("/"):
            return None
        rel = guest.lstrip("/")
        session_dir = PRootKernel._root() / "sessions" / sessionId
        # Per-session subdirs as documented in FileWriteTool/ReadImageTool.
        for prefix in ("var/minis/workspace", "var/minis/attachments",
                       "var/minis/offloads", "var/minis/browser"):
            if rel == prefix or rel.startswith(prefix + "/"):
                return session_dir / rel
        return PRootKernel._root() / rel

    @staticmethod
    def resolveHostPath(path: str) -> Path | None:
        """Kotlin: ``resolveHostPath(path)`` — global bind-mount map fallback."""
        guest = path.replace("\\", "/")
        if not guest.startswith("/"):
            return None
        return PRootKernel._root() / guest.lstrip("/")
