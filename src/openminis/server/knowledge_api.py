"""Knowledge search over the agent's on-device knowledge base.

Searches four real content sources that live on this machine:

* ``skill`` — installed skill bundles in ``<data_dir>/skills`` (each
  ``SKILL.md`` is the knowledge the agent loads to do its job);
* ``memory`` — the ``<data_dir>/memory`` Markdown files（五类记忆）;
* ``knowledge`` — the ``<data_dir>/knowledge`` knowledge base, itself split
  into the five Chinese categories 概念/实体/来源/分析/模板;
* ``doc`` — the port's own bundled documentation (python README / PORTING*).

``/api/knowledge/graph`` turns the knowledge base into a node/link graph:
one node per document (coloured by its category) and one edge per
``[[wikilink]]`` that points at another document.

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
from ..tools.memory_tools import (
    KNOWLEDGE_KINDS,
    _knowledge_dir,
    _memory_dir,
    knowledge_kind_by_dir,
)
from .system_api import _resolve_memory

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

__all__ = ["router"]

#: 知识五类（中文目录）—— 与记忆五类互不相干。
_KNOWLEDGE_KINDS = {k["id"]: k for k in KNOWLEDGE_KINDS}
_KNOWLEDGE_LABELS = {k["id"]: k["label"] for k in KNOWLEDGE_KINDS}
_OTHER_LABEL = "其他"

#: ``[[双链]]`` 提取（支持 ``[[目标|别名]]``）。
_WIKILINK_RE = re.compile(r"\[\[([^\[\]\|]+?)(?:\|[^\[\]]*)?\]\]")

#: Documentation files that ship inside the python/ checkout.
_DOC_FILES = {
    "README.md",
    "PORTING.md",
    "PORTING_MAP.md",
}

_KIND_ORDER = {"skill": 0, "memory": 1, "knowledge": 2, "doc": 3}
_KIND_LABEL = {
    "skill": "技能",
    "memory": "记忆",
    "knowledge": "知识",
    "doc": "项目文档",
}


@dataclass(frozen=True)
class _Doc:
    kind: str
    name: str
    title: str
    modified: int
    text: str
    #: 知识分类 id（concepts/entities/sources/analysis/templates/other）；
    #: 非 knowledge 来源为空串。
    category: str = ""

    @property
    def category_label(self) -> str:
        if self.category:
            return _KNOWLEDGE_LABELS.get(self.category, _OTHER_LABEL)
        return _KIND_LABEL.get(self.kind, self.kind)


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
    # memory files — 五类记忆（daily/ long-term/ rules/ troubleshooting/
    # preferences/），与知识库彻底分开。
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
    # knowledge/ — 知识库（可复用资料、专题文档），**不属于记忆**。
    # 文档按 概念/实体/来源/分析/模板 五类中文目录归档，分类由所在目录决定。
    kdir = _knowledge_dir()
    if kdir.is_dir():
        for p in sorted(kdir.rglob("*.md"), key=lambda x: x.as_posix()):
            rel = p.relative_to(kdir).as_posix()
            if p.parent == kdir and rel == "index.md":
                # 索引用不着出现在列表里（它只是目录）
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover
                continue
            yield _Doc(
                kind="knowledge",
                name=rel,
                title=rel.removesuffix(".md"),
                modified=int(p.stat().st_mtime * 1000),
                text=text,
                category=knowledge_kind_by_dir(rel),
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
    kind: str = Query("", description="skill|memory|knowledge|doc; 留空返回全部"),
    category: str = Query("", description="知识分类 concepts|entities|sources|analysis|templates"),
    limit: int = Query(60, ge=1, le=200),
) -> dict[str, Any]:
    query = q.strip().lower()
    cat = category.strip().lower()
    items: list[dict[str, Any]] = []
    for doc in _iter_docs():
        if kind and doc.kind != kind:
            continue
        if cat and doc.category != cat:
            continue
        lines = doc.text.splitlines()
        if query and query not in doc.text.lower():
            continue
        entry: dict[str, Any] = {
            "id": f"{doc.kind}:{doc.name}",
            "kind": doc.kind,
            "kindLabel": _KIND_LABEL.get(doc.kind, doc.kind),
            "title": doc.title,
            "source": doc.name,
            "modified": doc.modified,
            "preview": _highlight(lines, query),
        }
        if doc.kind == "knowledge":
            entry["category"] = doc.category
            entry["categoryLabel"] = doc.category_label
        items.append(entry)
        if len(items) >= limit:
            break
    items.sort(key=lambda it: (_KIND_ORDER.get(it["kind"], 9), it["source"]))
    sources = {"skill": 0, "memory": 0, "knowledge": 0, "doc": 0}
    categories = {k["id"]: 0 for k in KNOWLEDGE_KINDS}
    for doc in _iter_docs():
        sources[doc.kind] = sources.get(doc.kind, 0) + 1
        if doc.kind == "knowledge":
            key = doc.category if doc.category in categories else "other"
            categories[key] = categories.get(key, 0) + 1
    return {
        "query": q,
        "count": len(items),
        "items": items,
        "sources": sources,
        "categories": categories,
        "knowledgeKinds": [
            {"id": k["id"], "label": k["label"], "dir": k["dir"], "desc": k["desc"]}
            for k in KNOWLEDGE_KINDS
        ],
    }


def _extract_links(text: str) -> list[str]:
    """按出现顺序取出去重后的 ``[[双链]]`` 目标名。"""
    seen: list[str] = []
    for raw in _WIKILINK_RE.findall(text):
        target = raw.strip()
        if target and target not in seen:
            seen.append(target)
    return seen


@router.get("/graph")
async def knowledge_graph() -> dict[str, Any]:
    """知识库图谱：节点 = 文档（按分类着色），连线 = 文档间 ``[[链接]]``。

    只画知识库文档；链接目标按「去掉目录与 .md 的文件名」宽松匹配（同
    Obsidian 的直觉：``[[生图配置]]`` 能指到 ``模板/生图配置.md``），
    匹配不到的目标不画边，避免出现悬空节点。
    """
    docs = [d for d in _iter_docs() if d.kind == "knowledge"]
    #: 文件 stem / 相对路径 / 带扩展名 三种写法都能命中
    index: dict[str, str] = {}
    for d in docs:
        node_id = f"knowledge:{d.name}"
        stem = d.name.rsplit("/", 1)[-1].removesuffix(".md")
        for key in {stem, d.name, d.title}:
            index.setdefault(key, node_id)

    nodes = [
        {
            "id": f"knowledge:{d.name}",
            "label": d.title.rsplit("/", 1)[-1],
            "path": d.name,
            "kind": d.kind,
            "category": d.category,
            "categoryLabel": d.category_label,
        }
        for d in docs
    ]
    links: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str]] = set()
    for d in docs:
        src = f"knowledge:{d.name}"
        for target in _extract_links(d.text):
            dst = index.get(target)
            if dst is None or dst == src:
                continue
            edge = (src, dst)
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            links.append({"source": src, "target": dst})
    return {
        "nodes": nodes,
        "links": links,
        "categories": [
            {"id": k["id"], "label": k["label"], "dir": k["dir"], "desc": k["desc"]}
            for k in KNOWLEDGE_KINDS
        ],
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
    if kind == "knowledge":
        kroot = _knowledge_dir().resolve()
        target = (kroot / name).resolve()
        if target != kroot and kroot not in target.parents:
            raise HTTPException(status_code=400, detail="bad_name")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not_found")
        cat = knowledge_kind_by_dir(name)
        return {
            "kind": kind,
            "name": name,
            "title": name.removesuffix(".md"),
            "source": name,
            "modified": int(target.stat().st_mtime * 1000),
            "content": target.read_text(encoding="utf-8", errors="replace"),
            "category": cat,
            "categoryLabel": _KNOWLEDGE_LABELS.get(cat, _OTHER_LABEL),
        }
    # doc — whitelist only
    if name not in _DOC_FILES:        raise HTTPException(status_code=404, detail="not_found")
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
