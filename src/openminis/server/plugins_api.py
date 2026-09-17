"""[T-plugins-manager] 插件管理的 REST 接口。

界面（「插件」页与「通道」页）只走这一套：

    GET    /api/plugins                    列出所有插件 + 运行状态 + 配置（密钥脱敏）
    POST   /api/plugins/install            安装内置插件（`refresh` 可刷新已装清单）
    POST   /api/plugins/import             导入本机路径的插件包（zip / 目录）
    POST   /api/plugins/upload             上传 zip 并导入
    DELETE /api/plugins/{id}               卸载（连配置）
    PUT    /api/plugins/{id}/config        保存配置（在跑则自动重启）
    POST   /api/plugins/{id}/start|stop|restart
    GET    /api/plugins/{id}/logs          运行日志（最近 N 条）

导入是「能装现成项目」的关键：包里没有 ``plugin.json`` 也能装 —— 会按
package.json / 入口文件推断，见 ``plugins.store.infer_manifest``。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from ..core.context import app_context
from ..core.logging import get_logger
from ..plugins import store
from ..plugins.drivers import DRIVER_LABELS, driver_ids
from ..plugins.manifest import ManifestError
from ..plugins.registry import get_runtime

logger = get_logger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

#: 上传的插件包上限（zip）。
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _err(exc: Exception) -> HTTPException:
    if isinstance(exc, ManifestError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("")
async def plugins_list() -> dict[str, Any]:
    """全部插件（含未安装的内置插件）+ 运行状态。"""
    runtime = get_runtime()
    plugins = runtime.list_status()
    return {
        "plugins": plugins,
        "dir": str(store.plugins_dir()),
        "drivers": [{"id": d, "label": DRIVER_LABELS.get(d, d)} for d in driver_ids()],
    }


@router.get("/{plugin_id}")
async def plugin_detail(plugin_id: str) -> dict[str, Any]:
    status = get_runtime().status(plugin_id)
    if not status.get("name"):
        raise HTTPException(status_code=404, detail=f"没有这个插件：{plugin_id}")
    return status


@router.post("/install")
async def plugin_install(payload: dict[str, Any]) -> dict[str, Any]:
    """安装内置插件（把包内那份复制进数据目录，之后可改配置）。

    ``{"refresh": true}`` 时用包内那份覆盖已装的清单 —— 内置插件装过之后不会
    自动跟着升级，新加的配置项也就永远不出现；刷新会保留用户填的 config.json。
    """
    body = payload or {}
    plugin_id = str(body.get("id") or "").strip()
    if not plugin_id:
        raise HTTPException(status_code=400, detail="缺少 id")
    refresh = bool(body.get("refresh") or False)
    was_running = get_runtime().is_running(plugin_id)
    try:
        manifest = store.install_builtin(plugin_id, refresh=refresh)
        if refresh and was_running:
            # 清单变了（可能多了必填项），让运行时按新清单重新装配一次
            await get_runtime().restart(plugin_id)
    except Exception as exc:
        raise _err(exc) from exc
    return {"ok": True, "id": manifest.id, "plugin": get_runtime().status(manifest.id)}


@router.post("/import")
async def plugin_import(payload: dict[str, Any]) -> dict[str, Any]:
    """导入本机路径的插件包：``{"path": "C:/.../xxx.zip"}``。"""
    raw = str((payload or {}).get("path") or "").strip().strip('"')
    if not raw:
        raise HTTPException(status_code=400, detail="缺少 path")
    src = Path(raw)
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"路径不存在：{src}")
    try:
        manifest = store.import_plugin(src)
    except Exception as exc:
        raise _err(exc) from exc
    logger.info("plugin imported: %s (%s)", manifest.id, src)
    return {"ok": True, "id": manifest.id, "plugin": get_runtime().status(manifest.id)}


@router.post("/upload")
async def plugin_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传 zip 插件包并导入。"""
    name = Path(file.filename or "plugin.zip").name
    if not name.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="只接受 .zip 插件包")
    staging = app_context().data_dir / "plugins" / ".uploads"
    staging.mkdir(parents=True, exist_ok=True)
    dest = staging / f"{uuid.uuid4().hex}-{name}"
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="插件包太大了")
                out.write(chunk)
        manifest = store.import_plugin(dest)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise _err(exc) from exc
    finally:
        await file.close()
    dest.unlink(missing_ok=True)
    logger.info("plugin uploaded: %s (%d bytes)", manifest.id, size)
    return {"ok": True, "id": manifest.id, "plugin": get_runtime().status(manifest.id)}


@router.delete("/{plugin_id}")
async def plugin_remove(plugin_id: str) -> dict[str, Any]:
    runtime = get_runtime()
    try:
        if runtime.is_running(plugin_id):
            await runtime.stop(plugin_id)
    except Exception:  # pragma: no cover - 停不掉也要能卸
        logger.debug("stop before remove failed", exc_info=True)
    if not store.remove(plugin_id):
        raise HTTPException(status_code=404, detail=f"没有这个插件：{plugin_id}")
    return {"ok": True}


@router.put("/{plugin_id}/config")
async def plugin_config(plugin_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """保存配置。密钥字段留空 = 不改（界面不回传密钥）。"""
    values = (payload or {}).get("values")
    if not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="缺少 values")
    runtime = get_runtime()
    was_running = runtime.is_running(plugin_id)
    try:
        store.write_config(plugin_id, values)
    except Exception as exc:
        raise _err(exc) from exc
    if was_running:
        # 配置变了就得重连（凭证/白名单/分段长度都是起连接时读的）
        try:
            await runtime.restart(plugin_id)
        except Exception as exc:
            logger.warning("plugin restart after config failed: %s", exc)
    return {"ok": True, "plugin": runtime.status(plugin_id)}


@router.post("/{plugin_id}/{action}")
async def plugin_action(plugin_id: str, action: str) -> dict[str, Any]:
    runtime = get_runtime()
    try:
        if action == "start":
            await runtime.start(plugin_id)
        elif action == "stop":
            await runtime.stop(plugin_id)
        elif action == "restart":
            await runtime.restart(plugin_id)
        else:
            raise HTTPException(status_code=404, detail=f"不认识的动作：{action}")
    except HTTPException:
        raise
    except Exception as exc:
        raise _err(exc) from exc
    return {"ok": True, "plugin": runtime.status(plugin_id)}


@router.get("/{plugin_id}/logs")
async def plugin_logs(plugin_id: str, limit: int = Query(120, ge=1, le=500)) -> dict[str, Any]:
    return {
        "id": plugin_id,
        "logs": get_runtime().logs(plugin_id, limit),
        "now": int(time.time() * 1000),
    }
