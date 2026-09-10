"""OpenMinis Web 服务统一入口（配套 run.bat / stop.bat）。

用法（在 python/ 目录下）::

    python app.py                 启动后端并打开浏览器 http://127.0.0.1:8765
    python app.py --dev           额外启动 Vite 开发服务器 http://127.0.0.1:5173（热更新）
    python app.py --no-browser    不自动打开浏览器
    python app.py --port 9000     自定义后端端口
    python app.py --host 0.0.0.0   监听所有网卡（局域网访问）

说明:
    * 默认模式直接由 FastAPI 托管 ``web/dist``（构建产物），无需 Node。
    * ``--dev`` 模式会拉起 Vite（需要 Node），其 /api、/ws 代理到后端。
    * 启动时把自身 PID 写入 ``.minis-server.pid``，供 stop.bat 结束进程。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

# 允许未安装(editable)时直接运行：把 src 加入导入路径
if getattr(sys, "frozen", False):
    # 打包后 __file__ 指向 _MEIPASS 解压目录(可能是临时目录),PID 文件必须写在
    # exe 同级 —— 那是 stop.bat 与用户唯一能再次找到它的地方。
    _ROOT = Path(sys.executable).resolve().parent
else:
    _ROOT = Path(__file__).resolve().parent
    _SRC = _ROOT / "src"
    if _SRC.is_dir() and str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))

PID_FILE = _ROOT / ".minis-server.pid"
WEB_DIR = _ROOT / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="app.py",
        description="OpenMinis Web 服务入口（后端 FastAPI + Web 前端）。",
    )
    # 默认值刻意留空:先看界面里存下来的 server.host / server.port,
    # 命令行显式给出的值再覆盖它。否则「后台运行与服务」页改了也不生效。
    p.add_argument("--host", default=None, help=f"监听地址（默认读设置,初始 {DEFAULT_HOST}）")
    p.add_argument("--port", type=int, default=None, help=f"后端端口（默认读设置,初始 {DEFAULT_PORT}）")
    p.add_argument("--dev", action="store_true", help="同时启动 Vite 开发服务器(5173)并打开它")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.add_argument("--reload", action="store_true", help="开发模式自动重载后端（勿与 --dev 混淆）")
    return p.parse_args()


def _configured_bind() -> tuple[str | None, int | None]:
    """Read ``server.host`` / ``server.port`` written by the settings page.

    Anything unreadable falls back to ``(None, None)`` — a corrupt or absent
    settings file must never stop the server from booting, it just means the
    built-in defaults apply.
    """
    try:
        from openminis.config.config_registry import ConfigRegistry  # noqa: PLC0415

        registry = ConfigRegistry.init()
        host_field = registry.resolve_field("server.host")
        port_field = registry.resolve_field("server.port")
        host = str(host_field.read().value).strip() if host_field else ""
        raw_port = port_field.read().value if port_field else None
        port = int(raw_port) if raw_port is not None else None
        return (host or None, port)
    except Exception:  # pragma: no cover - 配置缺失/损坏时的兜底
        return None, None


def _resolve_bind(args: argparse.Namespace) -> tuple[str, int]:
    """CLI flag > saved setting > built-in default."""
    cfg_host, cfg_port = _configured_bind()
    host = args.host or cfg_host or DEFAULT_HOST
    port = args.port or cfg_port or DEFAULT_PORT
    return host, port


def _write_pid() -> None:
    # 注意:必须以换行结尾,否则 Windows cmd 的 `set /p` 读不到内容(stop.bat 依赖它)
    PID_FILE.write_text(f"{subprocess.os.getpid()}\n", encoding="ascii")


def _remove_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _open_browser_later(url: str) -> None:
    """等服务真正起来后再打开浏览器；失败静默（不阻塞主流程）。"""

    def _wait_and_open() -> None:
        probe = f"{url}/api/health"
        for _ in range(60):  # 最多等 ~6s
            try:
                with urllib.request.urlopen(probe, timeout=0.5):
                    break
            except Exception:
                time.sleep(0.1)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_wait_and_open, daemon=True).start()


def _start_vite() -> subprocess.Popen[bytes] | None:
    """后台拉起 Vite dev server（node web/node_modules/vite/bin/vite.js）。"""
    if getattr(sys, "frozen", False):
        print("[错误] --dev 只在源码目录下可用（打包后的 exe 不含 Node 前端）。", file=sys.stderr)
        print("       请直接启动 exe 使用已构建的界面。", file=sys.stderr)
        sys.exit(2)
    node = shutil.which("node")
    vite_js = WEB_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    if node is None or not vite_js.exists():
        print(f"[错误] --dev 需要 Node 与 web/node_modules（未找到 {vite_js}）。", file=sys.stderr)
        print("       请先执行:  cd web && npm install    然后重试。", file=sys.stderr)
        sys.exit(2)
    print(f"[dev] 启动 Vite -> http://127.0.0.1:5173")
    return subprocess.Popen([node, str(vite_js)], cwd=str(WEB_DIR))


def main() -> None:
    args = _parse_args()
    host, port = _resolve_bind(args)

    # 后端 app（复用 server 模块里已装配路由/静态托管的 FastAPI 实例）
    from openminis.server.main import app as fastapi_app  # noqa: PLC0415

    vite_proc: subprocess.Popen[bytes] | None = None
    if args.dev:
        vite_proc = _start_vite()

    _write_pid()
    try:
        # 0.0.0.0 表示「监听所有网卡」,它本身不是可连地址 —— 浏览器要开回环。
        browser_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        backend_url = f"http://{browser_host}:{port}"
        frontend_url = "http://127.0.0.1:5173" if args.dev else backend_url
        print(f"[OpenMinis] 后端     http://{host}:{port}")
        if host not in ("127.0.0.1", "localhost", "::1", ""):
            print(f"[OpenMinis] 局域网   http://<本机IP>:{port}  （同一网络的设备可访问）")
        print(f"[OpenMinis] 前端     {frontend_url}")
        print(f"[OpenMinis] PID 文件 {PID_FILE}（stop.bat 据此停止）")
        if not args.no_browser:
            _open_browser_later(frontend_url)

        import uvicorn

        uvicorn.run(
            fastapi_app,
            host=host,
            port=port,
            reload=args.reload,
            log_level="info",
        )
    except OSError as e:
        # WinError 10048 = 端口已被占用。这是双击启动时最常见的一种失败,
        # 值得直接告诉用户去哪儿改,而不是丢一段 uvicorn 堆栈。
        print(f"[错误] 无法绑定 {host}:{port} —— {e}", file=sys.stderr)
        print("       端口可能已被占用（例如已有一个 OpenMinis 在运行）。", file=sys.stderr)
        print("       可先跑 stop.bat,或换端口:python app.py --port 8899", file=sys.stderr)
        sys.exit(1)
    finally:
        if vite_proc is not None and vite_proc.poll() is None:
            vite_proc.terminate()
            try:
                vite_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                vite_proc.kill()
        _remove_pid()


if __name__ == "__main__":
    main()
