# -*- coding: utf-8 -*-
"""
mcp.tools.memory — 自包含 RAG 记忆系统（不依赖外部 agent 框架）

分类记忆 + 关键词索引：
    add_memory(cat, content, kws) / search_memory(query, cat=?, top_k=?)
    list_categories() / add_daily(content) / add_long_term(content, kws)

数据落在 <项目根>/memory/ 下：
    wiki_index.json        关键词索引（类别 -> 条目列表）
    <类别>/*.md            分类记忆文件
    YYYY-MM-DD.md          每日日志
    MEMORY.md              长期记忆沉淀
"""

import os
import re
import time
import json
from datetime import date
from typing import List, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MEMORY_ROOT = os.path.join(PROJECT_ROOT, "memory")
WIKI_PATH = os.path.join(MEMORY_ROOT, "wiki_index.json")
LONG_TERM_PATH = os.path.join(MEMORY_ROOT, "MEMORY.md")
CATS = ["长期记忆", "偏好", "问题", "规则", "日常"]


# ---------- 内部工具 ----------

def _dirs():
    os.makedirs(MEMORY_ROOT, exist_ok=True)
    for c in CATS:
        os.makedirs(os.path.join(MEMORY_ROOT, c), exist_ok=True)


def _idx() -> dict:
    if os.path.isfile(WIKI_PATH):
        try:
            with open(WIKI_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {c: [] for c in CATS}


def _save(idx):
    with open(WIKI_PATH, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=2, ensure_ascii=False)


def _kws(t) -> List[str]:
    """提取文本关键词：中文 2 字以上词 + 英文/数字 2 字符以上 token"""
    return list(dict.fromkeys(
        re.findall(r"[\u4e00-\u9fff]{2,}", str(t))
        + re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_.\-]{1,}", str(t))))


def _read_text(fp) -> str:
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _clean_entries(cat_entries) -> List[dict]:
    """过滤索引脏数据：历史混入过 str 路径，只保留 dict 条目"""
    if not isinstance(cat_entries, list):
        return []
    return [e for e in cat_entries if isinstance(e, dict)]


def _parse_mem_file(fp, category=""):
    """解析分类记忆 md -> {category, id, file, keywords, content}"""
    text = _read_text(fp)
    if not text:
        return None
    lines = text.splitlines()
    mid = ""
    for ln in lines[:3]:
        if ln.startswith("#"):
            mid = ln.lstrip("# ").strip()
            break
    if not mid:
        mid = os.path.splitext(os.path.basename(fp))[0]
    kws = []
    m = re.search(r"关键词[:：]\s*(.+?)\s*$", text, re.M)
    if m:
        kws = [k.strip() for k in re.split(r"[,，]", m.group(1)) if k.strip()]
        text = text[: m.start()]
    body = "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#")).strip()
    return {"category": category, "id": mid, "file": fp,
            "keywords": kws, "content": body}


# ---------- 写入 ----------

def add_memory(cat: str, content: str, kws: Optional[list] = None) -> dict:
    """写入一条分类记忆，返回 {ok, category, id, keywords}"""
    if cat not in CATS:
        return {"ok": False, "error": f"未知类别: {cat}（可用：{', '.join(CATS)}）"}
    if not content or not str(content).strip():
        return {"ok": False, "error": "content 不能为空"}
    _dirs()
    content = str(content).strip()
    if kws is None:
        kws = _kws(content)
    kws = [str(k).strip() for k in kws if str(k).strip()]
    mid = f"{cat[:2]}_{time.strftime('%H%M%S')}_{hash(content[:50]) & 0xFFFF:04x}"
    fp = os.path.join(MEMORY_ROOT, cat, f"{mid}.md")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f"# {mid}\n\n{content}\n\n关键词: {', '.join(kws)}\n")
    idx = _idx()
    idx.setdefault(cat, [])
    idx[cat] = _clean_entries(idx[cat])
    idx[cat].append({"id": mid, "file": fp, "keywords": kws,
                     "preview": content[:150]})
    _save(idx)
    return {"ok": True, "category": cat, "id": mid, "keywords": kws}


def add_daily(content: str) -> dict:
    """追加到当日日志 memory/YYYY-MM-DD.md，返回 {ok, date}"""
    if not content or not str(content).strip():
        return {"ok": False, "error": "content 不能为空"}
    _dirs()
    today = date.today().strftime("%Y-%m-%d")
    fp = os.path.join(MEMORY_ROOT, f"{today}.md")
    with open(fp, "a", encoding="utf-8") as f:
        f.write(f"\n## {today}\n\n{str(content).strip()}\n\n")
    return {"ok": True, "date": today, "file": fp}


def add_long_term(content: str, kws: Optional[list] = None) -> dict:
    """写入长期记忆（分类目录 + 索引 + 沉淀到 MEMORY.md），返回 {ok, id}"""
    r = add_memory("长期记忆", content, kws)
    if r.get("ok"):
        # 同步沉淀到 MEMORY.md（追加一段，避免重复追加同 id）
        if os.path.isfile(LONG_TERM_PATH):
            existing = _read_text(LONG_TERM_PATH)
        else:
            existing = ""
        tag = f"[{r['id']}]"
        if tag not in existing:
            with open(LONG_TERM_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n## {tag} {date.today().isoformat()}\n\n{content}\n\n")
    return r


# ---------- 查询 ----------

def list_categories() -> List[dict]:
    """返回 [{category, count}]，count 为索引 dict 条目数"""
    idx = _idx()
    out = []
    for c in CATS:
        out.append({"category": c, "count": len(_clean_entries(idx.get(c, [])))})
    return out


def search_memory(query: str, cat: Optional[str] = None,
                  top_k: int = 10) -> dict:
    """关键词检索记忆。

    score = 关键词命中(2) + keywords 命中(3) + id/file 命中(1)
    返回 {ok, count, results:[{category, id, file, keywords, content}]}
    """
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "error": "缺少 query"}
    qs = _kws(q)
    if not qs:
        return {"ok": False, "error": "query 无可检索关键词"}
    idx = _idx()
    cats = [cat] if cat else CATS
    if cat and cat not in CATS:
        return {"ok": False, "error": f"未知类别: {cat}"}

    scored = []
    for c in cats:
        for e in _clean_entries(idx.get(c, [])):
            fp = e.get("file", "")
            if not fp or not os.path.isfile(fp):
                continue
            text = _read_text(fp)
            kws = e.get("keywords") or _kws(text)
            score = 0
            for w in qs:
                wl = w.lower()
                if wl in text.lower():
                    score += 2
                if any(wl == str(k).lower() or wl in str(k).lower()
                       for k in kws):
                    score += 3
                if wl in str(e.get("id", "")).lower() or wl in fp.lower():
                    score += 1
            if score > 0:
                parsed = _parse_mem_file(fp, category=c)
                if parsed is None:
                    continue
                parsed["score"] = score
                scored.append(parsed)

    scored.sort(key=lambda x: -x.get("score", 0))
    results = [{"category": s["category"], "id": s["id"], "file": s["file"],
                "keywords": s["keywords"], "content": s["content"]}
               for s in scored[: top_k]]
    return {"ok": True, "count": len(scored), "results": results}


__all__ = ["CATS", "add_memory", "search_memory", "list_categories",
           "add_daily", "add_long_term"]
