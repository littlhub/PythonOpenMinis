"""Memory Section Builder — persistent file-based memory instructions.

Ported from: packages/core/src/context/sections/memory.ts
Original package: @zcode/core (context/sections)
"""

from __future__ import annotations

from typing import Optional

from ..types import ContextSection
from ..utils import estimate_tokens


def build_memory_section(memory_root: Optional[str]) -> Optional[ContextSection]:
    if not memory_root:
        return None

    content = _build_memory_content(memory_root)

    return ContextSection(
        name="Memory",
        source="memory",
        injection_target="system",
        cache_hint="dynamic",
        chars=len(content),
        tokens=estimate_tokens(content),
        content=content,
        preview=content[:100],
    )


def _build_memory_content(memory_root: str) -> str:
    return "\n".join(
        [
            "# Memory",
            "",
            f"You have a persistent file-based memory at `{memory_root}/`. This directory already exists — write to it directly with the Write tool (do not run mkdir or check for its existence). Each memory is one file holding one fact, with frontmatter:",
            "",
            "```markdown",
            "---",
            "name: <short-kebab-case-slug>",
            "description: <one-line summary — used to decide relevance during recall>",
            "metadata:",
            "  type: user | feedback | project | reference",
            "---",
            "",
            "<the fact; for feedback/project, follow with **Why:** and **How to apply:** lines. Link related memories with [[their-name]].>",
            "```",
            "",
            "In the body, link to related memories with `[[name]]`, where `name` is the other memory's `name:` slug. Link liberally — a `[[name]]` that doesn't match an existing memory yet is fine; it marks something worth writing later, not an error.",
            "",
            "`user` — who the user is (role, expertise, preferences). `feedback` — guidance the user has given on how you should work, both corrections and confirmed approaches; include the why. `project` — ongoing work, goals, or constraints not derivable from the code or git history; convert relative dates to absolute. `reference` — pointers to external resources (URLs, dashboards, tickets).",
            "",
            "After writing the file, add a one-line pointer in `MEMORY.md` (`- [Title](file.md) — hook`). `MEMORY.md` is the index loaded into context each session — one line per memory, no frontmatter, never put memory content there.",
            "",
            "Before saving, check for an existing file that already covers it — update that file rather than creating a duplicate; delete memories that turn out to be wrong. Don't save what the repo already records (code structure, past fixes, git history, AGENTS.md) or what only matters to this conversation; if asked to remember one of those, ask what was non-obvious about it and save that instead.",
        ]
    )
