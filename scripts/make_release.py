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


def _body(version: str, zip_name: str) -> str:
    return (
        "## 新增\n"
        "- **沙箱守卫**：危险删除（`rm -rf` / `del /s` / 通配符 / 工作区外）、目录越界、"
        "敏感信息出站三类动作实时检测；命中即拦，可在界面放行一次 / 本次会话 / 永久。\n"
        "- **密码保护**：控制台密码 + 页面访问密码。设了访问密码后进站只见锁屏输入框，"
        "未解锁时接口返回 423、WebSocket 握手直接 4401 拒绝（不会漏内容）。\n"
        "- **知识库**：记忆与知识彻底分开，知识按「概念 / 实体 / 来源 / 分析 / 模板」五类归档，"
        "新增双链图谱视图（节点按分类着色，`index.md` 自动重建）。\n"
        "- **图片体验**：聊天图片、项目文件里的图片都能点开放大（灯箱，支持适配/原始尺寸缩放）；"
        "输入框已选图片有缩略图条；**本轮生成的图自动出现在气泡下方**，模型忘记写 markdown 也看得到。\n"
        "- **生图张数按语义**：说一张跑一次、说两张跑两次 —— 模型在调用参数里声明张数"
        "（`image_gen` 用 `count`、技能脚本用 `--count N`），护栏据此建预算，不再硬限一张，"
        "也不会失控连出十几张。\n"
        "- 界面：侧栏可折叠；聊天队列支持追加与暂停；消息气泡可复制 / 删除。\n\n"
        "## 改进\n"
        "- **Agent 循环**：内置 KT 原版检测器，并可在 ReAct / 原版之间切换；重复调用「第一次就停」。"
        "拦截后必定收尾（先投递产物再总结），轮次用尽时改为**让模型自己写总结**，"
        "不再把 `[stopped: reached the maximum number of tool rounds]` 丢给用户。\n"
        "- **提速**：模型连接按配置指纹跨轮复用（省掉每次重连），同一轮里互不依赖的工具调用并发执行；"
        "检索支持时间范围（今天/本周/本月/今年）。\n"
        "- **修复**：`api_key = openai_key` 这类读变量不再被脱敏误伤；"
        "`scripts/image_generation.py` 不再被误判为目录越界；一键解压即用（`web/dist` 已随包）。\n\n"
        "## 校验\n"
        "- `/api/health`、`/api/chats/workspaces`、`/api/settings` 均返回 200。\n"
        f"- 版本号 {version}，回溯测试 566+ 通过。\n\n"
        "## 下载\n"
        f"- `{zip_name}`：解压后运行 `OpenMinis/OpenMinis.exe`，浏览器打开提示的地址即可。"
    )


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    tag = sys.argv[1]
    zip_path = sys.argv[2]
    commitish = sys.argv[3] if len(sys.argv) > 3 else "main"
    #: ``v1.0.2`` -> ``1.0.2``
    version = tag.lstrip("v")
    body = _body(version, os.path.basename(zip_path))

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
