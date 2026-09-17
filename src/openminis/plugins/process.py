"""[T-plugins-manager] 外部程序插件（runtime=process）的运行器。

像 dsh-bridge 这类现成的桥接项目是**独立的 node / python 程序**，不可能塞进
Python 进程里跑。所以引擎对它们做的是「管生命周期」：按清单里的命令行启动、
收集日志、盯着退出码、需要时自动重启、顺手探一下健康地址。

安全：命令与参数**分开传**，绝不拼 shell 字符串（插件目录里的文件名可能带
空格甚至引号）；cwd 被限制在插件目录内。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

from .manifest import ChannelManifest, ManifestError

#: 日志环形缓冲的行数（界面「查看日志」用）。
LOG_LINES = 300
#: 自动重启的最小间隔，防「起不来就疯狂重启」。
RESTART_BACKOFF_SEC = 5.0
RESTART_MAX_SEC = 60.0


class ProcessRunner:
    """一个外部程序插件的运行实例。"""

    def __init__(self, manifest: ChannelManifest, config: dict[str, Any] | None = None) -> None:
        self.manifest = manifest
        self.config = dict(config or {})
        self.state = "idle"
        self.error = ""
        self.pid: int | None = None
        self.started_at = 0.0
        self._proc: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task[Any] | None = None
        self._watch: asyncio.Task[Any] | None = None
        self._stopping = False
        self._logs: deque[dict[str, Any]] = deque(maxlen=LOG_LINES)

    # -- 日志 -------------------------------------------------------------
    def log(self, text: str, level: str = "info") -> None:
        self._logs.append({"ts": int(time.time() * 1000), "level": level, "text": str(text)})

    def logs(self, limit: int = 120) -> list[dict[str, Any]]:
        items = list(self._logs)
        return items[-max(1, int(limit)):]

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # -- 启停 -------------------------------------------------------------
    async def start(self) -> None:
        if self.running:
            return
        command, args, cwd_rel = self.manifest.command_line()
        exe = resolve_executable(command)
        if exe is None:
            self.state = "error"
            self.error = f"找不到可执行文件：{command}"
            self.log(self.error, "error")
            raise ManifestError(self.error)
        base = self.manifest.path or Path.cwd()
        cwd = (base / cwd_rel).resolve() if cwd_rel not in ("", ".") else base.resolve()
        if base.resolve() not in cwd.parents and cwd != base.resolve():
            raise ManifestError(f"插件的工作目录越界了：{cwd}")
        if not cwd.is_dir():
            raise ManifestError(f"插件的工作目录不存在：{cwd}")

        self._stopping = False
        self.state = "starting"
        self.error = ""
        env = os.environ.copy()
        for key, value in (self.manifest.process.get("env") or {}).items():
            if value is not None:
                env[str(key)] = str(value)
        # 配置项也注入成环境变量（大写下划线），外部程序可以直接读
        for key, value in self.config.items():
            if key.startswith("_") or value in (None, ""):
                continue
            name = f"OM_{key.upper()}"
            if isinstance(value, list):
                env[name] = ",".join(str(v) for v in value)
            elif not isinstance(value, (dict,)):
                env[name] = str(value)
        for key in ("port", "PORT"):
            if self.manifest.process.get(key):
                env.setdefault("PORT", str(self.manifest.process[key]))

        self.log(f"启动：{exe} {' '.join(args)}（cwd={cwd}）")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                exe,
                *args,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            self.state = "error"
            self.error = f"启动失败：{exc}"
            self.log(self.error, "error")
            raise ManifestError(self.error) from exc

        self.pid = self._proc.pid
        self.started_at = time.time()
        self.state = "running"
        self._pump = asyncio.create_task(self._pump_output(self._proc))
        self._watch = asyncio.create_task(self._watch_exit(self._proc))
        if self.manifest.process.get("healthUrl"):
            asyncio.create_task(self._probe_health(str(self.manifest.process["healthUrl"])))

    async def stop(self) -> None:
        self._stopping = True
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            self.state = "idle"
            self.pid = None
            return
        self.state = "stopping"
        self.log("正在停止…")
        try:
            proc.terminate()
        except ProcessLookupError:  # pragma: no cover - 已经没了
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=8)
        except asyncio.TimeoutError:
            self.log("进程没响应，强制结束", "warn")
            try:
                proc.kill()
            except ProcessLookupError:  # pragma: no cover
                pass
            await proc.wait()
        for task in (self._pump, self._watch):
            if task is not None and not task.done():
                task.cancel()
        self._pump = self._watch = None
        self.pid = None
        self.state = "idle"
        self.log("已停止")

    # -- 内部 -------------------------------------------------------------
    async def _pump_output(self, proc: asyncio.subprocess.Process) -> None:
        stream = proc.stdout
        if stream is None:
            return
        while True:
            try:
                raw = await stream.readline()
            except (asyncio.CancelledError, ValueError):
                return
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self.log(line, "out")

    async def _watch_exit(self, proc: asyncio.subprocess.Process) -> None:
        code = await proc.wait()
        if self._stopping:
            return
        self.state = "error"
        self.error = f"进程退出（code={code}）"
        self.log(self.error, "error")
        if not self.manifest.process.get("autoRestart", True):
            return
        delay = RESTART_BACKOFF_SEC
        while not self._stopping and delay <= RESTART_MAX_SEC:
            await asyncio.sleep(delay)
            if self._stopping:
                return
            self.log(f"尝试自动重启（上次退出 {delay:.0f}s 前）", "warn")
            try:
                await self.start()
                return
            except (ManifestError, OSError):
                delay = min(delay * 2, RESTART_MAX_SEC)

    async def _probe_health(self, url: str) -> None:
        """探一次健康地址 —— 只用于日志提示，不阻塞启动。"""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
            self.log(f"健康检查 {url} → HTTP {resp.status_code}")
        except Exception as exc:  # pragma: no cover - 探活失败很正常
            self.log(f"健康检查失败（{url}）：{exc}", "warn")

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "running": self.running,
            "pid": self.pid,
            "error": self.error,
            "startedAt": int(self.started_at * 1000) if self.started_at else 0,
            "uptimeSec": int(time.time() - self.started_at) if self.running and self.started_at else 0,
        }


def resolve_executable(command: str) -> str | None:
    """找可执行文件。``python`` 特意指回当前解释器（虚拟环境里才对）。"""
    cmd = str(command or "").strip()
    if not cmd:
        return None
    if cmd in ("python", "python3", "py"):
        return sys.executable
    found = shutil.which(cmd)
    if found:
        return found
    # Windows 上 npm/yarn 这类是 .cmd，which 一般能补；补不到再试一次
    for suffix in (".cmd", ".exe", ".bat"):
        found = shutil.which(cmd + suffix)
        if found:
            return found
    return None


__all__ = ["ProcessRunner", "resolve_executable", "LOG_LINES"]
