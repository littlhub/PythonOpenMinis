"""Knowledge search over the agent's on-device knowledge base.

Searches three real content sources that live on this machine:

* ``skill`` — installed skill bundles in ``<data_dir>/skills`` (each
  ``SKILL.md`` is the knowledge the agent loads to do its job);
* ``memory`` — the ``<data_dir>/memory`` Markdown files (daily logs,
  GLOBAL.md, SOUL.md);
* ``doc`` — the port's own bundled documentation (python README / PORTING*).

The endpoint is intentionally read-only and self-contained: no index, no
external service — a plain substring scan over a bounded set of Markdown files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Query

from ..core.context import app_context  # noqa: F401
from ..skills import SkillStore
from ..tools.memory_tools import _memory_dir
from .system_api import _resolve_memory

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

__all__ = ["router"]

#: Documentation files that ship inside the python/ checkout.
_DOC_FILES = {
    "README.md",
    "PORTING.md",
    "PORTING_MAP.md",
}

_KIND_ORDER = {"skill": 0, "memory": 1, "doc": 2}
_KIND_LABEL = {"skill": "技能", "memory": "记忆", "doc": "项目文档"}


@dataclass(frozen=True)
class _Doc:
    kind: str
    name: str
    title: str
    modified: int
    text: str


def _iter_docs() -> Iterator[_Doc]:
    """Yield every knowledge document, newest-ish grouping first by kind."""
    # skills — one per bundle
    for entry in SkillStore().list():
        yield _Doc(
            kind="skill",
            name=entry.name,
            title=entry.name,
            modified=0,
            text=entry.body or "",
        )
    # memory files — daily logs, wiki/<topic>.md, rules/RULES.md (recursive)
    root = _memory_dir()
    if root.is_dir():
        for p in sorted(root.rglob("*.md"), key=lambda x: x.as_posix()):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover
                continue
            rel = p.relative_to(root).as_posix()
            yield _Doc(
                kind="memory",
                name=rel,
                title=rel.removesuffix(".md"),
                modified=int(p.stat().st_mtime * 1000),
                text=text,
            )
    # bundled docs
    py_root = Path(__file__).resolve().parents[3]  # python/
    for fname in sorted(_DOC_FILES):
        p = py_root / fname
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover
            continue
        yield _Doc(
            kind="doc",
            name=fname,
            title=fname,
            modified=int(p.stat().st_mtime * 1000),
            text=text,
        )


def _highlight(lines: list[str], query: str, radius: int = 1) -> str:
    """Return the lines around the first hit (or the file head)."""
    q = query.lower()
    for i, line in enumerate(lines):
        if q and q in line.lower():
            start = max(0, i - radius)
            end = min(len(lines), i + radius + 1)
            snippet = "\n".join(l.strip()[:240] for l in lines[start:end])
            return snippet
    joined = "\n".join(l.strip() for l in lines[:6] if l.strip())
    return joined[:600]


@router.get("")
async def knowledge_search(
    q: str = Query("", description="关键词; 留空返回全部"),
    kind: str = Query("", description="skill|memory|doc; 留空返回全部"),
    limit: int = Query(60, ge=1, le=200),
) -> dict[str, Any]:
    query = q.strip().lower()
    items: list[dict[str, Any]] = []
    for doc in _iter_docs():
        if kind and doc.kind != kind:
            continue
        lines = doc.text.splitlines()
        if query and query not in doc.text.lower():
            continue
        items.append(
            {
                "id": f"{doc.kind}:{doc.name}",
                "kind": doc.kind,
                "kindLabel": _KIND_LABEL.get(doc.kind, doc.kind),
                "title": doc.title,
                "source": doc.name,
                "modified": doc.modified,
                "preview": _highlight(lines, query),
            }
        )
        if len(items) >= limit:
            break
    items.sort(key=lambda it: (_KIND_ORDER.get(it["kind"], 9), it["source"]))
    sources = {"skill": 0, "memory": 0, "doc": 0}
    for doc in _iter_docs():
        sources[doc.kind] = sources.get(doc.kind, 0) + 1
    return {
        "query": q,
        "count": len(items),
        "items": items,
        "sources": sources,
    }


@router.get("/content/{kind}/{name:path}")
async def knowledge_content(kind: str, name: str) -> dict[str, Any]:
    """Full body of one knowledge item (safe name/kind resolution)."""
    if kind not in _KIND_ORDER:
        raise HTTPException(status_code=400, detail="unknown_kind")
    if kind == "skill":
        entry = SkillStore().get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail="not_found")
        return {
            "kind": kind,
            "name": entry.name,
            "title": entry.name,
            "source": entry.name,
            "modified": 0,
            "content": entry.body,
        }
    if kind == "memory":
        path = _resolve_memory(name)  # raises HTTPException(400) on bad name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not_found")
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(_memory_dir()).as_posix()
        return {
            "kind": kind,
            "name": rel,
            "title": rel.removesuffix(".md"),
            "source": rel,
            "modified": int(path.stat().st_mtime * 1000),
            "content": text,
        }
    # doc — whitelist only
    if name not in _DOC_FILES:
        raise HTTPException(status_code=404, detail="not_found")
    p = Path(__file__).resolve().parents[3] / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail="not_found")
    return {
        "kind": kind,
        "name": name,
        "title": name,
        "source": name,
        "modified": int(p.stat().st_mtime * 1000),
        "content": p.read_text(encoding="utf-8", errors="replace"),
    }
