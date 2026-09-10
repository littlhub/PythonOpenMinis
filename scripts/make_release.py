"""经 GitHub REST API 创建 release 并上传资产（绕过被拦的 github.com:443）。

用法: python scripts/make_release.py <tag> <zip_path> [commitish]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "littlhub/PythonOpenMinis"
API = "https://api.github.com"
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _token() -> str:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, cwd=REPO_DIR,
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    raise SystemExit("找不到 GitHub 凭据")


TOKEN = _token()
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "openminis-release",
}


def api(method, path, payload=None, binary=False):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method, headers=HEADERS,
    )
    try:
        with opener.open(req, timeout=120) as r:
            return r.read() if binary else json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"API {method} {path} -> {e.code}\n{e.read().decode()[:800]}") from None


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    tag = sys.argv[1]
    zip_path = sys.argv[2]
    commitish = sys.argv[3] if len(sys.argv) > 3 else "main"

    body = (
        "## 修复\n"
        "- **打包修复**：下载版（exe）启动即报 `ModuleNotFoundError: No module named 'aiosqlite'`、"
        "所有数据库接口 500 的问题已修复。根因是 SQLAlchemy 的 `sqlite+aiosqlite://` 驱动在运行时动态 import，"
        "PyInstaller 静态扫描抓不到，导致该模块没被打进冻结产物。现已在打包配置中加入 `aiosqlite` hidden import，"
        "并在数据库模块顶部显式 import 双保险。\n\n"
        "## 校验\n"
        "- `/api/chats/workspaces`（GET/POST）、`/api/usage`、`/api/health` 均返回 200。\n"
        "- 版本号 1.0.1（1.0.0 的打包修复版，应用功能未变）。\n\n"
        "## 下载\n"
        "- `OpenMinis-1.0.1-win64.zip`：解压后运行 `OpenMinis/OpenMinis.exe`。"
    )

    print(f"创建 release {tag} @ {commitish} ...")
    rel = api("POST", f"/repos/{REPO}/releases", {
        "tag_name": tag,
        "target_commitish": commitish,
        "name": tag,
        "body": body,
        "draft": False,
        "prerelease": False,
    })
    rid = rel["id"]
    print(f"release id={rid}")

    name = os.path.basename(zip_path)
    print(f"上传资产 {name} ({os.path.getsize(zip_path)//1024//1024} MB) ...")
    with open(zip_path, "rb") as f:
        data = f.read()
    url = f"https://uploads.github.com/repos/{REPO}/releases/{rid}/assets?name={name}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={**HEADERS, "Content-Type": "application/zip"},
    )
    try:
        with opener.open(req, timeout=300) as r:
            resp = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"上传失败 {e.code}\n{e.read().decode()[:800]}") from None
    print("资产已上传:", resp.get("browser_download_url"))


if __name__ == "__main__":
    main()
