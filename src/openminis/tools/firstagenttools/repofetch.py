#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repofetch.py - 技能外部仓库依赖（imports）拉取器

让技能可以「引用 GitHub 或其他现有仓库的代码」：把声明的文件下载到
技能目录 deps/，技能脚本运行前自动把 deps/ 注入 PYTHONPATH，
脚本内即可直接 import 复用。

SKILL.md frontmatter 声明：
    ---
    name: my-skill
    imports:
      - url: https://raw.githubusercontent.com/org/repo/main/mod.py
      - url: https://raw.githubusercontent.com/org/repo/main/lib/client.py
        as: herb_client.py          # 可选：本地文件名（默认取 URL 末段）
    ---

skill.json 同样支持 "imports": [...] 字段。

幂等：目标文件已存在且非空则跳过（不重复下载）；删除 deps/ 即强制重拉。
注意：外部代码可能包含不可信逻辑，仅用于你自己确认过的仓库。
"""

import os
import re
import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (SkillMCP repofetch)"}


def _read_frontmatter(md_path):
    """读 SKILL.md frontmatter -> dict（失败返回 None）"""
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return None
    m = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not m:
        return None
    try:
        import yaml
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}
    return meta if isinstance(meta, dict) else {}


def parse_imports(skill_dir):
    """解析 SKILL.md / skill.json 里的 imports 声明 -> [{"url", "as"}...]"""
    items = []
    md = os.path.join(skill_dir, "SKILL.md")
    if os.path.isfile(md):
        meta = _read_frontmatter(md)
        if meta and isinstance(meta.get("imports"), list):
            items.extend(meta["imports"])
    sj = os.path.join(skill_dir, "skill.json")
    if os.path.isfile(sj):
        try:
            with open(sj, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("imports"), list):
                items.extend(data["imports"])
        except Exception:
            pass
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            continue
        name = str(it.get("as") or it.get("name")
                   or url.rsplit("/", 1)[-1]).strip()
        # 只允许纯文件名（防路径穿越）
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        if not base or base in (".", ".."):
            base = "dep.py"
        out.append({"url": url, "as": base})
    return out


def _fetch(url, dest, timeout=20):
    """下载单个文件到 dest；已存在且非空则跳过。返回 (ok, msg)"""
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return True, "cached"
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if not data:
            return False, "empty response"
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return True, "ok"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def ensure_imports(skill_dir):
    """拉取技能声明的外部依赖到 <skill_dir>/deps/，返回 deps 路径列表（可为空）"""
    deps = os.path.join(skill_dir, "deps")
    items = parse_imports(skill_dir)
    if not items:
        return []
    os.makedirs(deps, exist_ok=True)
    ready = []
    for it in items:
        dest = os.path.join(deps, it["as"])
        ok, msg = _fetch(it["url"], dest)
        if ok:
            ready.append(dest)
        else:
            print(f"⚠️ [外部依赖] 拉取失败 {it['url']} -> {msg}")
    return ready


# ---------- 命令行：单独测试某个技能目录 ----------
def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="技能外部依赖拉取器")
    parser.add_argument("skill_dir", help="技能目录（含 SKILL.md / skill.json）")
    args = parser.parse_args(argv)
    ready = ensure_imports(args.skill_dir)
    if ready:
        print(f"✅ 已就绪 {len(ready)} 个外部依赖：")
        for p in ready:
            print(f"   {p}")
    else:
        print("（无外部依赖或拉取失败）")


if __name__ == "__main__":
    main()
