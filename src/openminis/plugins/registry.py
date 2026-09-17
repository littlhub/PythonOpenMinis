"""[T-plugins-manager] 插件运行时：把「装好的插件」变成「跑起来的东西」。

统一管理两类插件的生命周期，接口一致（界面只认这一套）：

- ``engine``（引擎内驱动，如 QQ 通道）：构造适配器 → 挂会话桥 → 起连接
- ``process``（外部程序，如 dsh-bridge）：起子进程 → 收日志 → 盯着重启
- ``manual``（只登记的包）：不允许启动，状态里说清楚

引擎启动时会自动把「上次是启用状态」的插件拉起来（``start_enabled``），
所以重启服务之后机器人不会掉线。
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import options, store
from .base import ChannelAdapter
from .bridge import DEFAULT_CHUNK_CHARS, ConversationBridge
from .drivers import has_driver, load_adapter
from .manifest import (
    RUNTIME_ENGINE,
    RUNTIME_MANUAL,
    RUNTIME_PROCESS,
    ManifestError,
)
from .process import ProcessRunner


class PluginRuntime:
    def __init__(self) -> None:
        self._adapters: dict[str, ChannelAdapter] = {}
        self._bridges: dict[str, ConversationBridge] = {}
        self._procs: dict[str, ProcessRunner] = {}
        self._starting: set[str] = set()

    # -- 启动 / 停止 ------------------------------------------------------
    async def start(self, plugin_id: str) -> dict[str, Any]:
        pid = str(plugin_id or "").strip()
        manifest = store.get(pid)
        if manifest is None:
            raise ManifestError(f"没有这个插件：{pid}")
        if pid in self._starting:
            return self.status(pid)

        lack = store.missing_required(pid)
        if lack:
            raise ManifestError("还差这些没填：" + "、".join(lack))
        if manifest.runtime == RUNTIME_MANUAL and not manifest.tools:
            # manual = 引擎不起连接。但如果它声明了 tools，仍然可以「启用」——
            # 那只是把工具挂给 agent，插件本体不需要常驻（调用时才起一次进程）。
            raise ManifestError(
                "这个插件没有可执行入口（补一份含 process.command 或 tools 的 "
                "plugin.json 才能启用）"
            )

        self._starting.add(pid)
        try:
            if manifest.runtime == RUNTIME_PROCESS:
                await self._start_process(manifest)
            elif manifest.runtime == RUNTIME_ENGINE and manifest.driver:
                await self._start_engine(manifest)
            # 纯工具插件（manual / 无 driver）：启用即生效，没有要起的连接。
            store.set_enabled(pid, True)
        finally:
            self._starting.discard(pid)
        return self.status(pid)

    async def _start_engine(self, manifest: Any) -> None:
        if not has_driver(manifest.driver):
            raise ManifestError(f"引擎里没有这个驱动：{manifest.driver}")
        existing = self._adapters.get(manifest.id)
        if existing is not None and existing.running:
            return
        config = store.read_config(manifest.id)
        adapter_cls = load_adapter(manifest.driver)
        # 注意别用 `or DEFAULT`：chunkChars=0 表示「跑完一次发」，是个有效取值，
        # 被 `or` 吃掉就变成偷偷流式分段了。
        raw_chunk = config.get("chunkChars")
        chunk_chars = (
            DEFAULT_CHUNK_CHARS
            if raw_chunk is None or raw_chunk == ""
            else int(raw_chunk)
        )
        adapter = adapter_cls(
            plugin_id=manifest.id,
            name=manifest.name,
            config=config,
            max_message_chars=int(config.get("maxMessageChars") or 2000),
        )
        bridge = ConversationBridge(
            plugin_id=manifest.id,
            adapter=adapter,
            chunk_chars=chunk_chars,
            label=manifest.name,
        )
        adapter._on_message = bridge.handle
        self._adapters[manifest.id] = adapter
        self._bridges[manifest.id] = bridge
        await adapter.start()

    async def _start_process(self, manifest: Any) -> None:
        runner = self._procs.get(manifest.id)
        if runner is not None and runner.running:
            return
        runner = runner or ProcessRunner(manifest, store.read_config(manifest.id))
        self._procs[manifest.id] = runner
        await runner.start()

    async def stop(self, plugin_id: str) -> dict[str, Any]:
        pid = str(plugin_id or "").strip()
        adapter = self._adapters.pop(pid, None)
        self._bridges.pop(pid, None)
        if adapter is not None:
            await adapter.stop()
        runner = self._procs.pop(pid, None)
        if runner is not None:
            await runner.stop()
        store.set_enabled(pid, False)
        return self.status(pid)

    async def restart(self, plugin_id: str) -> dict[str, Any]:
        pid = str(plugin_id or "").strip()
        if self.is_running(pid):
            await self.stop(pid)
            return await self.start(pid)
        return self.status(pid)

    def is_running(self, plugin_id: str) -> bool:
        pid = str(plugin_id or "").strip()
        adapter = self._adapters.get(pid)
        if adapter is not None and adapter.running:
            return True
        runner = self._procs.get(pid)
        return bool(runner is not None and runner.running)

    # -- 状态 / 日志 ------------------------------------------------------
    def status(self, plugin_id: str) -> dict[str, Any]:
        pid = str(plugin_id or "").strip()
        manifest = store.get(pid)
        out: dict[str, Any] = {
            "id": pid,
            "installed": store.is_installed(pid),
            "enabled": store.is_enabled(pid),
            "running": self.is_running(pid),
            "state": "idle",
            "error": "",
            "detail": "",
            "account": "",
            "pid": 0,
            "uptimeSec": 0,
            "tools": [],
            "toolCount": 0,
        }
        if manifest is not None:
            out.update({
                "name": manifest.name,
                "icon": manifest.icon,
                "version": manifest.version,
                "description": manifest.description,
                "category": manifest.category,
                "runtime": manifest.runtime,
                "driver": manifest.driver,
                "builtin": manifest.builtin,
                # 包内的清单比数据目录那份新 —— 界面上给一个「更新」入口，
                # 否则新加的配置项永远不会出现（装过就不会自动跟着升级）。
                "outdated": (
                    manifest.builtin and store.builtin_outdated(pid)
                ),
                "config": store.public_config(pid),
                "missing": store.missing_required(pid),
                # 界面不用管 optionsFrom —— 这里已经解析成一组真实可选值。
                "fields": options.attach_options(
                    [f.to_dict() for f in manifest.fields]
                ),
                "tools": [t.to_dict() for t in manifest.tools],
                "toolCount": len(manifest.tools),
            })
        adapter = self._adapters.get(pid)
        if adapter is not None:
            st = adapter.status()
            out.update({
                "state": st.get("state", "idle"),
                "error": st.get("error", ""),
                "detail": st.get("detail", ""),
                "account": st.get("account", ""),
                "since": st.get("since", 0),
                "maxMessageChars": st.get("maxMessageChars"),
            })
        runner = self._procs.get(pid)
        if runner is not None:
            st = runner.status()
            out.update({
                "state": st.get("state", "idle"),
                "error": st.get("error", ""),
                "pid": st.get("pid") or 0,
                "uptimeSec": st.get("uptimeSec") or 0,
                "since": st.get("startedAt", 0),
            })
        return out

    def logs(self, plugin_id: str, limit: int = 120) -> list[dict[str, Any]]:
        pid = str(plugin_id or "").strip()
        adapter = self._adapters.get(pid)
        if adapter is not None:
            return adapter.logs(limit)
        runner = self._procs.get(pid)
        if runner is not None:
            return runner.logs(limit)
        return []

    def list_status(self) -> list[dict[str, Any]]:
        return [self.status(m.id) for m in store.list_all()]

    # -- 引擎生命周期 -----------------------------------------------------
    async def start_enabled(self) -> list[str]:
        """启动时恢复：把上次启用的插件拉起来。返回启动成功的 id。"""
        started: list[str] = []
        for manifest in store.list_installed():
            if not store.is_enabled(manifest.id):
                continue
            try:
                await self.start(manifest.id)
                started.append(manifest.id)
            except Exception as exc:  # pragma: no cover - 一个插件起不来不该拖垮服务
                from ..core.logging import get_logger

                get_logger(__name__).warning(
                    "plugin %s auto-start failed: %s", manifest.id, exc
                )
        return started

    async def stop_all(self) -> None:
        for pid in list(self._adapters):
            try:
                await self.stop(pid)
            except Exception:  # pragma: no cover
                pass
        for pid in list(self._procs):
            try:
                await self.stop(pid)
            except Exception:  # pragma: no cover
                pass


#: 进程内单例（server 与测试都用它）。
runtime = PluginRuntime()


def get_runtime() -> PluginRuntime:
    return runtime


async def shutdown() -> None:
    await runtime.stop_all()


__all__ = ["PluginRuntime", "runtime", "get_runtime", "shutdown"]
