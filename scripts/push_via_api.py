"""把本地提交经 GitHub Git Data API 推上去。

为什么需要它:本机到 github.com:443 的 CONNECT 隧道被代理挡掉(502),
直连也不通,而 api.github.com 是通的。git 的 smart-http 传输必须打到
github.com,但 Git Data API 走 api.github.com —— 于是可以用 API 逐个
重建 blob/tree/commit,最后把 ref 指过去。

前提是对象哈希可复现:tree 的 sha 只由条目(名/模式/blob sha)决定,
commit 的 sha 由 tree+parent+author+committer+message 决定。只要这些
字段与本地逐字节一致,API 造出来的 commit sha 就等于本地的 —— 推送后
本地与远端历史不会分叉。脚本最后会断言这一点。

用法:  python scripts/push_via_api.py <远端基准 sha> [<本地 tip>]
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = "littlhub/PythonOpenMinis"
BRANCH = "main"
API = "https://api.github.com"
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _token() -> str:
    """从 git 凭据助手取 token —— 不落盘、不写日志。"""
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        cwd=REPO_DIR,
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line[len("password=") :]
    raise SystemExit("找不到 GitHub 凭据")


TOKEN = _token()


def git(*args: str, binary: bool = False):
    r = subprocess.run(
        ["git", *args], capture_output=True, cwd=REPO_DIR, check=True
    )
    return r.stdout if binary else r.stdout.decode("utf-8").strip()


def api(method: str, path: str, payload: dict | None = None) -> dict:
    """打 api.github.com —— 显式绕过代理(直连是通的,走代理反而可能被拦)。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "openminis-push",
        },
    )
    try:
        with opener.open(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit(f"API {method} {path} -> {e.code}\n{body[:800]}") from None


def _ident(raw: bytes) -> dict:
    """解析 ``Name <email> <unix-ts> <±hhmm>`` 成 GitHub 要的 ISO 身份。

    必须从原始对象读,不能走 ``git log --format`` —— 后者输出的正文末尾
    换行与对象里的不一致,重建出来的 commit sha 会变(实测差一个 \\n)。
    """
    lt = raw.rindex(b"<")
    gt = raw.index(b">", lt)
    ts_b, tz_b = raw[gt + 1 :].split()
    tz = tz_b.decode()
    sign = -1 if tz[0] == "-" else 1
    offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
    dt = datetime.fromtimestamp(int(ts_b), timezone(offset))
    return {
        "name": raw[:lt].rstrip().decode("utf-8"),
        "email": raw[lt + 1 : gt].decode("utf-8"),
        "date": dt.isoformat(),
    }


def commit_meta(sha: str) -> dict:
    """读一个提交的原始对象,原样拆出 tree/parent/作者/时间/正文。

    正文按字节保留:GitHub 的 commit sha 是按序列化后的内容算的,少一个
    末尾换行就对不上。
    """
    raw = git("cat-file", "commit", sha, binary=True)
    head, _, message = raw.partition(b"\n\n")
    tree = ""
    parents: list[str] = []
    author: dict = {}
    committer: dict = {}
    for line in head.split(b"\n"):
        if line.startswith(b"tree "):
            tree = line[5:].decode()
        elif line.startswith(b"parent "):
            parents.append(line[7:].decode())
        elif line.startswith(b"author "):
            author = _ident(line[7:])
        elif line.startswith(b"committer "):
            committer = _ident(line[10:])
    return {
        "tree": tree,
        "parents": parents,
        "author": author,
        "committer": committer,
        "message": message.decode("utf-8"),
    }


def changed_paths(parent: str, sha: str) -> list[tuple[str, str, str | None]]:
    """列出 parent..sha 的变更: (状态, 新路径, 旧路径)。"""
    raw = git(
        "diff-tree",
        "-r",
        "--no-commit-id",
        "--name-status",
        "-z",
        parent,
        sha,
        binary=True,
    )
    fields = [f for f in raw.split(b"\x00") if f]
    out: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(fields):
        status = fields[i].decode()
        if status.startswith("R") or status.startswith("C"):
            old, new = fields[i + 1].decode("utf-8"), fields[i + 2].decode("utf-8")
            out.append((status, new, old))
            i += 3
        else:
            path = fields[i + 1].decode("utf-8")
            out.append((status, path, None))
            i += 2
    return out


def blob_sha_for(commit: str, path: str) -> str:
    """把工作树里该路径的内容作为 blob 上传,拿回 sha。"""
    content = git("cat-file", "blob", f"{commit}:{path}", binary=True)
    res = api(
        "POST",
        f"/repos/{REPO}/git/blobs",
        {"content": base64.b64encode(content).decode(), "encoding": "base64"},
    )
    return res["sha"]


def mode_for(commit: str, path: str) -> str:
    line = git("ls-tree", commit, "--", path)
    return line.split()[0] if line else "100644"


def push_commit(sha: str, base_tree: str | None) -> tuple[str, str]:
    """重建一个提交,返回 ``(新 tree sha, 远端 commit sha)``。

    返回 tree 是因为下一个提交要拿它当 ``base_tree`` —— 用 commit sha 是错的,
    tree API 只认 tree。
    """
    meta = commit_meta(sha)
    parent = meta["parents"][0] if meta["parents"] else None
    items: list[dict] = []

    if base_tree is None:
        # 远端还没有历史:整棵树都得传。这里不做,留给调用方报错。
        raise SystemExit("远端为空仓库,请手动初始化后再用本脚本")

    for status, path, old in changed_paths(parent, sha):
        if status.startswith("D"):
            items.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        elif status.startswith("R") or status.startswith("C"):
            assert old is not None
            items.append(
                {"path": old, "mode": "100644", "type": "blob", "sha": None}
            )
            items.append(
                {
                    "path": path,
                    "mode": mode_for(sha, path),
                    "type": "blob",
                    "sha": blob_sha_for(sha, path),
                }
            )
        else:
            items.append(
                {
                    "path": path,
                    "mode": mode_for(sha, path),
                    "type": "blob",
                    "sha": blob_sha_for(sha, path),
                }
            )

    tree = api(
        "POST",
        f"/repos/{REPO}/git/trees",
        {"base_tree": base_tree, "tree": items},
    )
    assert tree["sha"] == meta["tree"], (
        f"tree 不一致: 远端 {tree['sha']} vs 本地 {meta['tree']} "
        "(API 与本地对同一内容的哈希算法/排序不同,推送会分叉)"
    )

    commit = api(
        "POST",
        f"/repos/{REPO}/git/commits",
        {
            "message": meta["message"],
            "tree": tree["sha"],
            "parents": meta["parents"],
            "author": meta["author"],
            "committer": meta["committer"],
        },
    )
    if commit["sha"] != sha:
        print(f"  警告: commit sha 不一致 {commit['sha'][:8]} != {sha[:8]}")
    return tree["sha"], commit["sha"]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    remote_base = sys.argv[1]
    tip = sys.argv[2] if len(sys.argv) > 2 else git("rev-parse", "HEAD")

    # GitHub 的 ``/git/commits/{sha}`` 只认完整 SHA —— 传缩写会直接 404,
    # 看起来像是「远端没有这个提交」。本地 rev-parse 就能补全,顺手做掉。
    remote_base = git("rev-parse", remote_base)

    # 远端基准必须是本地已有对象,且两侧 tree 一致 —— 否则下面的增量是错的。
    remote_commit = api("GET", f"/repos/{REPO}/git/commits/{remote_base}")
    local_tree = git("rev-parse", f"{remote_base}^{{tree}}")
    if remote_commit["tree"]["sha"] != local_tree:
        raise SystemExit(
            f"基准不一致: 远端 {remote_commit['tree']['sha'][:8]} vs 本地 {local_tree[:8]}"
        )
    print(f"基准 {remote_base[:8]} 校验通过 (tree {local_tree[:8]})")

    todo = git("rev-list", "--reverse", f"{remote_base}..{tip}").split()
    if not todo:
        print("没有需要推送的提交")
        return

    base_tree = local_tree
    tip_sha = tip
    for sha in todo:
        subject = git("log", "-1", "--format=%s", sha)
        print(f"推送 {sha[:8]} {subject}")
        base_tree, tip_sha = push_commit(sha, base_tree)

    api(
        "PATCH",
        f"/repos/{REPO}/git/refs/heads/{BRANCH}",
        {"sha": tip_sha, "force": False},
    )
    same = tip_sha == tip
    print(f"\n完成: {BRANCH} -> {tip_sha[:8]}{'' if same else f'  (本地 {tip[:8]}，历史分叉!)'}")
    print(f"https://github.com/{REPO}")
    if not same:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
