"""Project Memory Root — resolves the per-project memory storage directory.

Ported from: packages/core/src/memory/project-root.ts
Original package: @zcode/core (memory)
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class ProjectMemoryRootInput:
    """Mirrors ``interface ProjectMemoryRootInput``."""

    cli_storage_root: str
    workspace_path: str
    workspace_identity: Optional[str] = None


def resolve_project_memory_root(input: ProjectMemoryRootInput) -> str:
    """Mirrors ``resolveProjectMemoryRoot(input)``."""
    workspace_identity = input.workspace_identity.strip() if input.workspace_identity else None
    # PORT: ``node:path`` 的 ``resolve`` → ``os.path.abspath``（规范化 + 绝对化）。
    normalized_workspace_path = os.path.abspath(input.workspace_path)
    # PORT: ``process.platform === "win32"`` → ``sys.platform == "win32"``。
    key_source = (
        workspace_identity
        if workspace_identity
        else (
            normalized_workspace_path.lower()
            if sys.platform == "win32"
            else normalized_workspace_path
        )
    )
    # PORT: ``node:crypto`` 的 ``createHash("sha256")`` → ``hashlib.sha256``。
    digest = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
    file_hash = digest[:16]
    # PORT: ``basename`` → ``os.path.basename``；``join`` → ``os.path.join``。
    slug = (
        "project"
        if workspace_identity
        else sanitize_project_slug(os.path.basename(normalized_workspace_path) or "project")
    )

    return os.path.join(
        input.cli_storage_root, "memories", "projects", f"{slug}-{file_hash}", "memory"
    )


def sanitize_project_slug(value: str) -> str:
    """Mirrors ``sanitizeProjectSlug(value)``."""
    # PORT: TS 正则 ``/[^a-z0-9._-]+/g`` 与 ``/^-+|-+$/g`` → ``re.sub``。
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.lower())
    slug = re.sub(r"^-+|-+$", "", slug)
    slug = slug[:48]
    return slug if len(slug) > 0 else "project"
