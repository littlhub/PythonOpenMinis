"""REST endpoints backing the Web Settings tree that the Kotlin app reaches
through its Compose SettingsScreen (storage / logs / memory / backup / about).

The Android original renders these as in-app screens backed by filesystem,
Room and SharedPreferences. This router is the desktop equivalent: thin,
read-mostly adapters over the same kernel directories. Only ``memory`` and
``backup`` mutate state, and each mutation is name-whitelisted to the file it
is allowed to touch.
"""

from __future__ import annotations

import io
import json
import os
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from ..core.context import app_context
from ..core.logging import get_logger
from ..soul import (
    DEFAULT_METADATA,
    DISPLAY_EMOJI,
    SoulFile,
    SoulMDParser,
    SoulMetadata,
    SoulStore,
    contains_injection_pattern,
)
from ..tools.memory_tools import _memory_dir  # shared single source of truth

logger = get_logger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])

#: Memory files are plain Markdown/text in the memory dir only.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_ALLOWED_SUFFIX = {".md", ".txt"}
_LOG_MAIN = "minis.log"

__all__ = ["router"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _dir_stats(root: Path) -> tuple[int, int]:
    """Total bytes and file count under ``root`` (symmetric, no follow)."""
    total = 0
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".venv", "__pycache__", "node_modules"}]
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
                count += 1
            except OSError:  # pragma: no cover - vanished mid-walk
                continue
    return total, count


def _resolve_memory(name: str) -> Path:
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="invalid_name")
    path = (_memory_dir() / name).resolve()
    mem_root = _memory_dir().resolve()
    if path.parent != mem_root or path.suffix.lower() not in _ALLOWED_SUFFIX:
        raise HTTPException(status_code=400, detail="invalid_name")
    return path


def _log_dir() -> Path:
    return app_context().cache_dir / "logs"


def _preview(text: str, limit: int = 320) -> str:
    body = " ".join(text.split())
    return body[:limit] + ("…" if len(body) > limit else "")


# ---------------------------------------------------------------------------
# appinfo — 关于 / 存储页共用的事实
# ---------------------------------------------------------------------------
@router.get("/appinfo")
async def appinfo() -> dict[str, Any]:
    ctx = app_context()
    return {
        "name": "OpenMinis",
        "version": ctx.version_name,
        "versionCode": ctx.version_code,
        "packageName": ctx.package_name,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dataDir": str(ctx.data_dir),
        "workspaceDir": str(ctx.external_files_dir),
        "memoryDir": str(_memory_dir()),
        "cacheDir": str(ctx.cache_dir),
        "databasesDir": str(ctx.databases_dir),
        "logPath": str(_log_dir() / _LOG_MAIN),
        "settingsFile": str(ctx.data_dir / "settings.json"),
    }


# ---------------------------------------------------------------------------
# logs — 日志管理
# ---------------------------------------------------------------------------
@router.get("/logs")
async def logs(lines: int = 300) -> dict[str, Any]:
    lines = max(1, min(lines, 5000))
    path = _log_dir() / _LOG_MAIN
    rotated = sorted(
        p.name for p in _log_dir().glob(f"{_LOG_MAIN}.*") if p.is_file()
    )
    if not path.exists():
        return {
            "path": str(path),
            "size": 0,
            "updatedAt": 0,
            "lines": [],
            "rotated": rotated,
            "note": "日志文件尚未生成(服务启动后写入)。",
        }
    stat = path.stat()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(e)) from None
    tail = text.splitlines()[-lines:]
    return {
        "path": str(path),
        "size": stat.st_size,
        "updatedAt": int(stat.st_mtime * 1000),
        "lines": tail,
        "rotated": rotated,
    }


@router.get("/logs/download")
async def logs_download() -> FileResponse:
    path = _log_dir() / _LOG_MAIN
    if not path.exists():
        raise HTTPException(status_code=404, detail="no log file yet")
    return FileResponse(
        path, media_type="text/plain", filename="openminis.log"
    )


# ---------------------------------------------------------------------------
# storage — 存储管理
# ---------------------------------------------------------------------------
@router.get("/storage")
async def storage() -> dict[str, Any]:
    ctx = app_context()
    data_dir = ctx.data_dir
    items: list[dict[str, Any]] = []
    total = 0
    seen: set[Path] = set()

    def add(root: Path, name: str | None = None) -> None:
        nonlocal total
        if root in seen or not root.exists():
            return
        seen.add(root)
        try:
            size, count = _dir_stats(root)
        except OSError:  # pragma: no cover
            return
        total += size
        items.append(
            {
                "name": name or root.name,
                "path": str(root),
                "bytes": size,
                "fileCount": count,
            }
        )

    # top-level scatter files (settings.json / prefs snapshots) reported as a
    # synthetic entry so "where did my settings go" is answerable
    top_files = 0
    top_bytes = 0
    if data_dir.is_dir():
        for f in data_dir.iterdir():
            if f.is_file():
                try:
                    top_bytes += f.stat().st_size
                except OSError:  # pragma: no cover
                    continue
                top_files += 1
        if top_files:
            total += top_bytes
            items.append(
                {
                    "name": "(顶层文件: settings.json 等)",
                    "path": str(data_dir),
                    "bytes": top_bytes,
                    "fileCount": top_files,
                }
            )

    add(data_dir / "workspace", "workspace(会话工作目录)")
    add(data_dir / "memory", "memory(记忆)")
    add(data_dir / "files", "files(偏好/本地文件)")
    add(data_dir / "databases", "databases(SQLite)")
    add(ctx.cache_dir, "cache(日志/缓存)")
    # keep largest first for readability
    items.sort(key=lambda it: it["bytes"], reverse=True)
    return {
        "root": str(data_dir),
        "totalBytes": total,
        "items": items,
    }


# ---------------------------------------------------------------------------
# memory — 记忆管理 (与 MemoryTools 同一目录; 前端可编辑 GLOBAL.md / 每日日志)
# ---------------------------------------------------------------------------
@router.get("/memory")
async def memory_list() -> dict[str, Any]:
    root = _memory_dir()
    files: list[dict[str, Any]] = []
    if root.is_dir():
        for p in sorted(root.glob("*.md"), key=lambda x: x.name):
            stat = p.stat()
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover
                continue
            files.append(
                {
                    "name": p.name,
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime * 1000),
                    "preview": _preview(text),
                }
            )
    return {"dir": str(root), "files": files}


@router.get("/memory/{name}")
async def memory_get(name: str) -> dict[str, Any]:
    path = _resolve_memory(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="not_found")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"name": path.name, "size": len(text.encode("utf-8")), "content": text}


@router.put("/memory/{name}")
async def memory_put(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Overwrite one memory file (``{"content": str}``). New files may be
    created by PUTting a name that does not exist yet."""
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="invalid_value: content 必须是字符串")
    path = _resolve_memory(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"name": path.name, "size": len(content.encode("utf-8")), "content": content}


@router.delete("/memory/{name}")
async def memory_delete(name: str) -> dict[str, Any]:
    path = _resolve_memory(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="not_found")
    try:
        path.unlink()
    except OSError as e:  # e.g. sandbox file-protection refuses the delete
        logger.warning("memory delete failed for %s: %s", path.name, e)
        raise HTTPException(
            status_code=500, detail=f"无法删除 {path.name}: 文件可能被占用或受保护"
        ) from None
    logger.info("memory deleted: %s", path.name)
    return {"ok": True, "name": path.name}


# ---------------------------------------------------------------------------
# soul — 人格提示词 SOUL.md (对齐 Kotlin SoulStore.kt)
# ---------------------------------------------------------------------------
def _soul_payload(file: SoulFile | None, *, exists: bool) -> dict[str, Any]:
    """Serialise a SoulFile + its body-limit check for the Settings UI."""
    if file is None:
        meta = DEFAULT_METADATA
        body = ""
    else:
        meta = file.metadata
        body = file.body
    check = SoulStore.check_body_limit(body)
    return {
        "exists": exists,
        "path": str(SoulStore.file_path()),
        "metadata": {
            "name": meta.name,
            "emoji": meta.emoji,
            "icon": meta.icon,
            "style": meta.style,
            "lang": meta.lang,
        },
        "displayEmoji": DISPLAY_EMOJI,
        "body": body,
        "limit": {
            "ok": check.ok,
            "unit": check.unit,
            "used": check.used,
            "cap": check.cap,
            "chineseCharLimit": 1600,
            "englishWordLimit": 1000,
            "cjkRatioThreshold": 0.3,
        },
        "defaultContent": SoulStore.DEFAULT_CONTENT,
    }


@router.get("/soul")
async def soul_get() -> dict[str, Any]:
    """Return the current SOUL.md parsed + body-limit info.

    On first call the file is seeded with the default content if missing.
    """
    path = SoulStore.file_path()
    SoulStore.ensure_exists()
    file = SoulStore.load()
    return _soul_payload(file, exists=path.exists())


@router.put("/soul")
async def soul_put(payload: dict[str, Any]) -> dict[str, Any]:
    """Overwrite SOUL.md. Payload::

        {
          "metadata": { name, emoji, icon, style, lang },
          "body": "Markdown body"
        }
    """
    raw = payload.get("metadata") or {}
    body = str(payload.get("body", "") or "")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="metadata 必须为对象")

    meta = SoulMetadata(
        name=str(raw.get("name") or DEFAULT_METADATA.name).strip() or DEFAULT_METADATA.name,
        emoji=str(raw.get("emoji") or ""),
        icon=str(raw.get("icon") or ""),
        style=str(raw.get("style") or ""),
        lang=str(raw.get("lang") or "auto").strip() or "auto",
    )

    # Enforce the language-aware length cap at write time so a save
    # surfaces the same red error the Settings UI counter already shows.
    check = SoulStore.check_body_limit(body)
    if not check.ok:
        raise HTTPException(
            status_code=400,
            detail=(
                f"personality body over the {check.unit} cap "
                f"({check.used}/{check.cap})"
            ),
        )
    # Reject (not silently scrub) injection attempts at write time so the
    # agent sees a clear error instead of having its lines quietly dropped.
    if contains_injection_pattern(body):
        raise HTTPException(
            status_code=400,
            detail="personality body 含有可疑的提示注入语句,已拒绝保存",
        )

    SoulStore.save(SoulFile(meta, body))
    logger.info(
        "SOUL.md saved: name=%r style.len=%d body.len=%d",
        meta.name,
        len(meta.style),
        len(body),
    )
    return _soul_payload(SoulFile(meta, body), exists=True)


@router.delete("/soul")
async def soul_delete() -> dict[str, Any]:
    removed = SoulStore.delete()
    return {"ok": True, "removed": removed}


@router.post("/soul/restore-default")
async def soul_restore() -> dict[str, Any]:
    parsed = SoulStore.restore_default()
    return _soul_payload(parsed, exists=True)


# ---------------------------------------------------------------------------
# backup — 备份与恢复 (导出 settings.json + prefs + memory 为一个 zip)
# ---------------------------------------------------------------------------
@router.get("/backup/download")
async def backup_download() -> Response:
    ctx = app_context()
    buf = io.BytesIO()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    manifest: dict[str, Any] = {
        "app": "openminis",
        "version": ctx.version_name,
        "exportedAt": datetime.now().isoformat(timespec="seconds"),
        "files": [],
    }
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(path: Path, arc: str) -> None:
            if not path.exists():
                return
            zf.write(path, arc)
            manifest["files"].append({"arc": arc, "size": path.stat().st_size})

        add(ctx.data_dir / "settings.json", "settings.json")
        add(ctx.files_dir / "minis_prefs.json", "prefs/minis_prefs.json")
        memory_root = _memory_dir()
        if memory_root.is_dir():
            for p in sorted(memory_root.glob("*.md")):
                add(p, f"memory/{p.name}")
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    data = buf.getvalue()
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="openminis-backup-{stamp}.zip"'
            )
        },
    )
