"""Read-only filesystem API for the right-panel file browser.

Surfaces the agent's workspace (``AppContext.external_files_dir``) as a
tree + read endpoint. Single-user desktop app, so we don't bother with
multi-tenant scoping; the hard limit is "no path traversal outside the
workspace root" — anything outside gets a clean 400.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..core.context import app_context

__all__ = ["router", "_workspace_root"]


# Names / dirs that should never appear in the tree (huge / noisy / unsafe).
_DEFAULT_IGNORE = {
    ".venv",
    "node_modules",
    "__pycache__",
    ".git",
    ".pytest_cache",
    "dist",
    "build",
}


def _workspace_root() -> Path:
    return app_context().external_files_dir


def _resolve_root(workspace: str | None) -> Path:
    """Pick a file-tree root.

    ``workspace`` is a workspace id. If the workspace has a user-configured
    path that still exists on disk, we use it (so the file tree follows the
    real project the user is working on). Otherwise fall back to the agent's
    sandboxed external_files_dir.
    """
    if workspace:
        # local import to avoid a hard cycle at import time
        from . import workspaces

        user_path = workspaces.get_workspace_path(workspace)
        if user_path:
            p = Path(user_path)
            if p.is_dir():
                return p
    return _workspace_root()


def _resolve_relative(rel: str, root: Path) -> Path:
    """Resolve ``rel`` against ``root``, rejecting any path that escapes."""
    candidate = (root / rel).resolve() if rel else root.resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise HTTPException(status_code=400, detail="路径超出工作目录")
    return candidate


def _tree_node(path: Path, root: Path) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix() if path != root else ""
    try:
        stat = path.stat()
        is_dir = path.is_dir()
        size = stat.st_size if not is_dir else 0
        mtime = int(stat.st_mtime * 1000)
    except OSError as e:
        return {"name": path.name or root.name, "path": rel, "error": str(e)}

    node: dict[str, Any] = {
        "name": path.name or root.name,
        "path": rel,
        "isDir": is_dir,
        "size": size,
        "mtime": mtime,
    }
    if is_dir:
        children: list[dict[str, Any]] = []
        try:
            for child in sorted(
                path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            ):
                if child.name in _DEFAULT_IGNORE:
                    continue
                children.append(_tree_node(child, root))
        except OSError as e:
            node["error"] = str(e)
        node["children"] = children
    return node


def _safe_read_text(path: Path, max_bytes: int = 80_000) -> dict[str, Any]:
    """Read up to ``max_bytes`` of text content; detect binary files."""
    try:
        size = path.stat().st_size
    except OSError as e:
        raise HTTPException(status_code=404, detail=f"文件不存在: {e}") from None
    try:
        with path.open("rb") as f:
            raw = f.read(max_bytes + 1)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"无法读取: {e}") from None
    # binary sniff: NUL byte in the first chunk means binary
    if b"\x00" in raw[:8192]:
        raise HTTPException(status_code=415, detail="二进制文件,不支持预览")
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    return {
        "content": text,
        "truncated": truncated,
        "size": size,
        "lines": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
    }


# -----------------------------------------------------------------------
# FastAPI router
# -----------------------------------------------------------------------
router = APIRouter(prefix="/api/fs", tags=["fs"])


@router.get("/root")
async def fs_root(workspace: str | None = Query(default=None)) -> dict[str, Any]:
    root = _resolve_root(workspace)
    return {
        "root": str(root),
        "name": root.name,
        "exists": root.exists(),
        "isUserPath": bool(workspace and (root != _workspace_root())),
    }


@router.get("/tree")
async def fs_tree(
    path: str = Query(default="", description="相对工作区的子路径;空=根"),
    depth: int = Query(default=2, ge=0, le=8),
    workspace: str | None = Query(default=None),
) -> dict[str, Any]:
    root = _resolve_root(workspace)
    target = _resolve_relative(path, root)
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")

    # BFS up to `depth` to bound the response size.
    collected: dict[str, Any] = {
        "name": target.name or root.name,
        "path": path,
        "isDir": True,
        "children": [],
    }

    def walk(p: Path, rel: str, lvl: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            it = sorted(
                p.iterdir(),
                key=lambda q: (not q.is_dir(), q.name.lower()),
            )
        except OSError as e:
            return [{"name": "<error>", "path": rel, "error": str(e)}]
        for q in it:
            if q.name in _DEFAULT_IGNORE:
                continue
            child_rel = f"{rel}/{q.name}" if rel else q.name
            try:
                stat = q.stat()
            except OSError as e:
                out.append({"name": q.name, "path": child_rel, "error": str(e)})
                continue
            entry: dict[str, Any] = {
                "name": q.name,
                "path": child_rel,
                "isDir": q.is_dir(),
                "size": stat.st_size if not q.is_dir() else 0,
                "mtime": int(q.stat().st_mtime * 1000),
            }
            if q.is_dir() and lvl < depth:
                entry["children"] = walk(q, child_rel, lvl + 1)
            out.append(entry)
        return out

    collected["children"] = walk(target, path, 0)
    return collected


@router.get("/read")
async def fs_read(
    path: str = Query(..., description="相对工作区的文件路径"),
    maxBytes: int = Query(default=80_000, ge=1_000, le=400_000),
    workspace: str | None = Query(default=None),
) -> dict[str, Any]:
    root = _resolve_root(workspace)
    target = _resolve_relative(path, root)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="路径是目录,不可读")
    data = _safe_read_text(target, maxBytes)
    return {
        "path": path,
        "name": target.name,
        **data,
    }


#: 允许通过 ``/raw`` 直接回读的扩展名 —— 只放行图片。
#:
#: ``/api/upload/raw`` 只认 uploads 目录下的存储名，而 agent **生成**的图片会落在
#: ``workspace/image/``、``workspace/generated/`` 等别处，于是聊天里渲染不出来
#: （前端 onError 里直接 display:none）。这条端点是「生成的图片发不到前端」的补口。
_RAW_IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".avif",
}
_RAW_MAX_BYTES = 32 * 1024 * 1024


def _resolve_raw_target(raw: str, workspace: str | None) -> Path:
    """把 ``raw``（绝对路径或相对工作区）解析成可读文件，越界一律 400。"""
    from ..tools.path_utils import readonly_roots

    candidate = Path(raw.strip())
    if candidate.is_absolute():
        resolved = candidate.resolve()
        roots = [_resolve_root(workspace).resolve()]
        try:
            roots.extend(p.resolve() for p in readonly_roots())
        except Exception:  # pragma: no cover - 技能根读不到就不放行
            pass
        if not any(resolved == r or r in resolved.parents for r in roots):
            raise HTTPException(status_code=400, detail="路径超出可读范围")
        return resolved
    return _resolve_relative(raw, _resolve_root(workspace))


@router.get("/raw")
async def fs_raw(
    path: str = Query(..., description="工作区内的绝对路径，或相对工作区的路径"),
    workspace: str | None = Query(default=None),
) -> FileResponse:
    """按路径回读工作区内的**图片**（聊天里渲染 agent 生成的图）。"""
    target = _resolve_raw_target(path, workspace)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if target.suffix.lower() not in _RAW_IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="只支持图片预览")
    try:
        if target.stat().st_size > _RAW_MAX_BYTES:
            raise HTTPException(status_code=400, detail="图片过大")
    except OSError as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=f"无法读取: {exc}") from exc
    return FileResponse(target)