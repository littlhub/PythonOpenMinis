"""[T-plugins-manager] 外部程序插件（runtime=process）的运行器。

像 dsh-bridge 这类现成的桥接项目是**独立的 node / python 程序**，不可能塞进
Python 进程里跑。所以引擎对它们做的是「管生命周期」：按清单里的命令行启动、
收集日志、盯着退出码、需要时自动重启、顺手探一下健康地址。

安全：命令与参数**分开传**，绝不拼 shell 字符串（插件目录里的文件名可能带
空格甚至引号）；cwd 被限制在插件目录内。

找 `node` 这件事单独费了点笔墨：引擎进程往往是用户从 .bat / exe 起的，PATH 很
干净，而 Node 这类运行时经常只装在某个用户目录里（或由别的工具托管）。`which`
找不到就直接报「找不到可执行文件：node」，用户根本不知道该改哪里。所以这里会
主动扫一遍本机常见的安装位置，并允许在插件配置里显式指定搜索目录。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from .manifest import RUNTIME_PATH_KEY, ChannelManifest, ManifestError

#: 日志环形缓冲的行数（界面「查看日志」用）。
LOG_LINES = 300
#: 自动重启的最小间隔，防「起不来就疯狂重启」。
RESTART_BACKOFF_SEC = 5.0
RESTART_MAX_SEC = 60.0

#: Windows 上可执行文件的后缀。npm / yarn 是 ``.cmd``，node 是 ``.exe``，
#: 所以按名字找的时候每个后缀都要试一遍。
EXEC_SUFFIXES = ("", ".exe", ".cmd", ".bat", ".ps1")

#: 需要 ``cmd /c`` 才能起的后缀 —— CreateProcess 不认批处理脚本，
#: 不套一层 shell 会直接报「不是有效的应用程序」。
SHELL_SUFFIXES = (".cmd", ".bat")

#: 托管运行时的布局：``<home>/.workbuddy/binaries/<name>/versions/<ver>/``。
#: 这是本地运行时管理器的约定，只作为兜底候选 —— 命中不了也不影响。
_MANAGED_RUNTIMES = ("node", "python")


def _version_dirs(root: Path) -> list[Path]:
    """版本目录本身 + 它的 ``bin/``（unix 布局）；``current`` 软链优先。"""
    out: list[Path] = []
    current = root / "current"
    if current.is_dir():
        out.extend([current, current / "bin"])
    try:
        children = sorted(
            (c for c in root.iterdir() if c.is_dir() and not c.name.startswith(".")),
            key=lambda c: c.name,
            reverse=True,
        )
    except OSError:
        children = []
    for child in children:
        out.extend([child, child / "bin"])
    return out


def candidate_bin_dirs() -> list[Path]:
    """本机「可能装了可执行文件但没进 PATH」的目录。

    只在 ``which`` 失败后才用，所以宁可多列几个：多找一个目录的成本是几次
    ``is_file()``，而漏找的代价是用户对着「找不到可执行文件：node」发呆。
    """
    out: list[Path] = []
    home = Path.home()
    env = os.environ

    for name in _MANAGED_RUNTIMES:
        out.extend(_version_dirs(home / ".workbuddy" / "binaries" / name / "versions"))
    out.append(home / ".workbuddy" / "binaries" / "node" / "workspace" / "node_modules" / ".bin")

    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
        base = env.get(key)
        if not base:
            continue
        root = Path(base)
        out.extend([
            root / "nodejs",
            root / "Programs" / "nodejs",
            root / "nvm",
            root / "Volta" / "bin",
            root / "fnm_multishells",
        ])
        for version in ("Python310", "Python311", "Python312", "Python313"):
            out.append(root / "Programs" / "Python" / version)

    for key in ("NVM_HOME", "NVM_SYMLINK", "VOLTA_HOME", "PNPM_HOME", "MSYS2_HOME"):
        value = env.get(key)
        if value:
            out.append(Path(value))

    out.append(home / "scoop" / "shims")
    out.append(home / "AppData" / "Local" / "Microsoft" / "WindowsApps")
    out.append(Path("C:/ProgramData/chocolatey/bin"))
    # 去重但保序（同名的先出现的优先）
    seen: set[str] = set()
    unique: list[Path] = []
    for item in out:
        key = str(item).lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def extra_bin_dirs(config: dict[str, Any] | None) -> list[str]:
    """配置里显式指定的可执行文件搜索目录（``runtimePath``，一行一个）。"""
    raw = (config or {}).get(RUNTIME_PATH_KEY)
    if isinstance(raw, str):
        items: Iterable[Any] = raw.splitlines()
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        return []
    return [str(v).strip() for v in items if str(v or "").strip()]


def _find_in_dir(directory: str | Path, command: str) -> str | None:
    """在指定目录里按名字找可执行文件（带不带后缀都试）。"""
    base = Path(str(directory).strip().strip('"'))
    if not base.is_dir():
        return None
    for suffix in EXEC_SUFFIXES:
        candidate = base / f"{command}{suffix}"
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:  # pragma: no cover - 权限/IO
            continue
    return None


def resolve_executable(
    command: str, extra_dirs: Iterable[str | Path] = ()
) -> str | None:
    """找可执行文件。``python`` 特意指回当前解释器（虚拟环境里才对）。

    查找顺序：命令本来就是路径 → 用户指定的目录 → PATH → 本机常见安装位置。
    用户显式指定的目录排在 PATH 前面，因为他多半就是想覆盖 PATH 里那份。
    """
    cmd = str(command or "").strip().strip('"')
    if not cmd:
        return None
    if cmd in ("python", "python3", "py"):
        return sys.executable

    # 命令本身带路径（./start.sh、bin/node）—— 直接核一下在不在
    if os.sep in cmd or "/" in cmd:
        direct = Path(cmd)
        if direct.is_file():
            return str(direct)
        if direct.suffix.lower() in ("", *EXEC_SUFFIXES):
            for suffix in EXEC_SUFFIXES:
                if Path(f"{cmd}{suffix}").is_file():
                    return str(Path(f"{cmd}{suffix}"))

    for directory in extra_dirs:
        found = _find_in_dir(directory, cmd)
        if found:
            return found

    found = shutil.which(cmd)
    if found:
        return found

    for directory in candidate_bin_dirs():
        found = _find_in_dir(directory, cmd)
        if found:
            return found
    return None


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
        extra = extra_bin_dirs(self.config)
        exe = resolve_executable(command, extra)
        if exe is None:
            self.state = "error"
            self.error = (
                f"找不到可执行文件：{command}。已找过 PATH 与本机常见安装位置；"
                f"如果它装在别处，在插件的「可执行文件搜索目录」里填上它所在的目录。"
            )
            self.log(self.error, "error")
            raise ManifestError(self.error)
        if Path(exe).suffix.lower() in SHELL_SUFFIXES:
            # CreateProcess 不认 .cmd/.bat，得套一层 cmd /c（npm / yarn 就是这种）
            exe, args = os.environ.get("COMSPEC", "cmd.exe"), ["/c", exe, *args]
        base = self.manifest.path or Path.cwd()
        cwd = (base / cwd_rel).resolve() if cwd_rel not in ("", ".") else base.resolve()
        if base.resolve() not in cwd.parents and cwd != base.resolve():
            raise ManifestError(f"插件的工作目录越界了：{cwd}")
        if not cwd.is_dir():
            raise ManifestError(f"插件的工作目录不存在：{cwd}")

        self._stopping = False
        self.state = "starting"
        self.error = ""
        env = self._child_env(extra, exe)
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

        for hint in self._preflight(cwd):
            self.log(hint, "warn")
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
    def _child_env(self, extra: list[str], exe: str) -> dict[str, str]:
        """子进程环境：把可执行文件所在的目录顶到 PATH 最前面。

        为什么非加不可：``npm`` 自己会去调 ``node``。光把 npm 的绝对路径给对还不
        够 —— 子进程的 PATH 里没有 node，npm 一启动就报「找不到 node」，而且报的
        是我们完全没写过的错。同理适用于 yarn/pnpm、以及各种 ``#!/usr/bin/env``
        脚本。配置里指定的搜索目录也一并带上。
        """
        env = os.environ.copy()
        parts: list[str] = []
        for item in [*extra, str(Path(exe).parent)]:
            if item and item not in parts:
                parts.append(item)
        current = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([*parts, current]) if parts else current
        return env

    def _preflight(self, cwd: Path) -> list[str]:
        """启动前的常识性提醒 —— 只写日志，不阻断（有人就是 vendored 依赖）。"""
        hints: list[str] = []
        if (cwd / "package.json").is_file() and not (cwd / "node_modules").is_dir():
            hints.append(
                "这个插件是 Node 项目，但目录里没有 node_modules —— 依赖还没装。"
                f"先在插件目录跑一次 npm install：{cwd}"
            )
        if (cwd / "requirements.txt").is_file() and not (
            cwd / ".venv" if (cwd / ".venv").is_dir() else cwd / "site-packages"
        ).exists():
            hints.append(
                "这个插件有 requirements.txt。若启动报缺模块，先装依赖："
                f"pip install -r {cwd / 'requirements.txt'}"
            )
        return hints

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


__all__ = [
    "ProcessRunner",
    "resolve_executable",
    "candidate_bin_dirs",
    "extra_bin_dirs",
    "LOG_LINES",
]
