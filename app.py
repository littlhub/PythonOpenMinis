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
    p.add_argument("--host", default=DEFAULT_HOST, help=f"监听地址（默认 {DEFAULT_HOST}）")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"后端端口（默认 {DEFAULT_PORT}）")
    p.add_argument("--dev", action="store_true", help="同时启动 Vite 开发服务器(5173)并打开它")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.add_argument("--reload", action="store_true", help="开发模式自动重载后端（勿与 --dev 混淆）")
    return p.parse_args()


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

    # 后端 app（复用 server 模块里已装配路由/静态托管的 FastAPI 实例）
    from openminis.server.main import app as fastapi_app  # noqa: PLC0415

    vite_proc: subprocess.Popen[bytes] | None = None
    if args.dev:
        vite_proc = _start_vite()

    _write_pid()
    try:
        backend_url = f"http://{args.host}:{args.port}"
        frontend_url = "http://127.0.0.1:5173" if args.dev else backend_url
        print(f"[OpenMinis] 后端     {backend_url}")
        print(f"[OpenMinis] 前端     {frontend_url}")
        print(f"[OpenMinis] PID 文件 {PID_FILE}（stop.bat 据此停止）")
        if not args.no_browser:
            _open_browser_later(frontend_url)

        import uvicorn

        uvicorn.run(
            fastapi_app,
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
        )
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
