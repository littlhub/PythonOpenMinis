#!/usr/bin/env python
"""把知识库从平铺结构迁移成五类中文分类目录。

一次性脚本（幂等，可重复执行）：

    python scripts/migrate_knowledge_categories.py [--data-dir <数据目录>] [--dry-run]

它做四件事：

1. 备份整个 ``knowledge/`` 到 ``knowledge_backup_<时间戳>/``；
2. 合并重复文档、去掉追加日志里反复堆叠的时间戳段落；
3. 把每篇文档移进 ``概念/ 实体/ 来源/ 分析/ 模板/`` 之一；
4. 在相关文档之间补 ``[[双链]]``，并重建 ``index.md``。

知识分类的权威定义在 ``openminis.tools.memory_tools.KNOWLEDGE_KINDS``，
本脚本只负责把**已有**文档摆对位置。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 允许直接 `python scripts/xxx.py`（把 src/ 加进 sys.path）
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# 归类表：文档名（不带 .md）→ 知识分类 id
# ---------------------------------------------------------------------------
CATEGORY: dict[str, str] = {
    # 概念：可复用的方法、风格、规范
    "古风武侠插画风格": "concepts",
    "奇幻唯美插画风格": "concepts",
    "图像分析规范": "concepts",
    "文学创作经验": "concepts",
    # 实体：具体的作品 / 工具 / 项目
    "月夜剑姬插画": "entities",
    "校园爱情故事-银杏路上的约定": "entities",
    "agnes-image-generation": "entities",
    # 来源：外部参考素材
    "剑客侠女插画风格参考": "sources",
    # 分析：需求、结论、复盘记录
    "图片生成需求": "analysis",
    # 模板：配置、流程、可复用骨架
    "生图配置": "templates",
    "知识库整理": "templates",
}

# ---------------------------------------------------------------------------
# 合并：这些旧文档的内容已被合并进目标文档，迁移后删除旧文件
# ---------------------------------------------------------------------------
MERGE_INTO: dict[str, str] = {
    "校园爱情故事创作": "文学创作经验",
    "创作项目记录": "校园爱情故事-银杏路上的约定",
    "图像生图配置": "生图配置",
    "图像生成配置": "生图配置",
    "知识库整理计划": "知识库整理",
}

# ---------------------------------------------------------------------------
# 直接改写的最终正文（去掉重复块 + 合并后的版本）
# ---------------------------------------------------------------------------
REWRITE: dict[str, str] = {
    "文学创作经验": """# 文学创作经验

- 校园爱情题材成功案例: 《银杏路上的约定》5章+尾声结构
- 用户偏好完整叙事结构(章节+尾声)
- 注重人物设定和情感描写
- 核心意象运用(如银杏大道作为爱情见证)
- 双向成长主题受认可

## 校园爱情故事创作要点
- 代表作品: [[校园爱情故事-银杏路上的约定]]（5章+尾声结构）
- 主角: 林夏(女主)、沈辞(男主)；场景: A大校园
- 核心意象: 银杏大道(爱情见证)、奶茶、橡皮
- 主题: 清新自然校园氛围、细腻情感描写、双向成长、半年分离→坚定重逢

## 相关
- [[校园爱情故事-银杏路上的约定]]
""",
    "校园爱情故事-银杏路上的约定": """# 校园爱情故事-银杏路上的约定

## 基本信息
- **类型**：校园甜宠爱情
- **主角**：林夏（女主）、沈辞（男主）
- **场景**：A大校园

## 故事大纲
| 章节 | 内容 |
|------|------|
| 第一章 | 初遇：开学季，林夏拖着行李箱撞见沈辞 |
| 第二章 | 重逢：《高等数学》课上再次相遇，图书馆成为第三教室 |
| 第三章 | 心动：银杏大道夜谈，沈辞承诺"一起变得更好" |
| 第四章 | 波折：林夏获英国交换机会，沈辞怕拖累选择留下 |
| 第五章 | 重逢：半年后林夏回国，沈辞仍在原地等待 |
| 尾声 | 毕业合照，牵手走向未来 |

## 核心意象
- 银杏大道（象征爱情的见证）
- 奶茶、橡皮等日常细节
- 半年分离→坚定重逢的情感张力

## 项目档案
- 结构：5章+尾声（初遇→重逢→心动→波折→重逢→尾声）
- 风格：清新自然校园氛围、细腻情感描写、双向成长主题

## 相关
- [[文学创作经验]]
""",
    "生图配置": """# 生图配置

- API Base: https://apihub.agnes-ai.cn/v1
- 生图模型: agnes-image-2.1-flash
- 识图模型: agnes-2.0-flash（限流时暂停重试）
- 输出路径: /tmp/storage/sda1/图片/Agnes/{年月日}_{时间戳}.png
- 脚本: C:\\Users\\loo\\openminis\\skills\\agnes-image\\scripts\\image_generation_v4.py
- 常见问题: agnes-2.0-flash 识图偶发 RateLimited 错误，需冷却后重试

## 相关
- [[agnes-image-generation]]
- [[图片生成需求]]
""",
    "知识库整理": """# 知识库整理

## 整理流程
1. 收集原始信息
2. 分类整理（概念 / 实体 / 来源 / 分析 / 模板）
3. 建立索引与 `[[双链]]`
4. 定期更新维护

## 注意事项
- 保持内容的准确性和时效性
- 建立清晰的分类结构：`knowledge/<分类>/<主题>.md`
- 方便后续检索和使用

## 现行落盘约定
- 记忆走 `memory/`（五类），知识走 `knowledge/`（五类），两者**分开**
- 一个主题一个文件；重复内容合并，不留副本
- 文档之间用 `[[主题]]` 双链，知识页图谱就是由双链生成的
""",
}

# ---------------------------------------------------------------------------
# 去重：文档里反复堆叠的时间戳段落，只保留标题 + 最后一块
# ---------------------------------------------------------------------------
DEDUPE: set[str] = {
    "月夜剑姬插画",
    "剑客侠女插画风格参考",
    "图片生成需求",
}

# ---------------------------------------------------------------------------
# 双链：迁移完在这些文档尾部补「相关」段（图谱的连线来源）
# ---------------------------------------------------------------------------
LINKS: dict[str, list[str]] = {
    "月夜剑姬插画": ["古风武侠插画风格", "剑客侠女插画风格参考", "奇幻唯美插画风格"],
    "剑客侠女插画风格参考": ["月夜剑姬插画", "古风武侠插画风格", "图像分析规范"],
    "图片生成需求": ["图像分析规范", "生图配置", "古风武侠插画风格"],
    "agnes-image-generation": ["生图配置"],
    "图像分析规范": ["图片生成需求"],
}

_TS_BLOCK = re.compile(r"^##\s+\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\s*$")
_H1 = re.compile(r"^#\s+(.*)$")


def keep_latest_block(text: str) -> str:
    """保留标题 + 最后一个时间戳段落（追加日志里的重复块只留最新的）。"""
    lines = text.splitlines()
    stamps = [i for i, line in enumerate(lines) if _TS_BLOCK.match(line.strip())]
    if not stamps:
        return text.strip() + "\n"
    head = lines[: stamps[0]]
    title = ""
    for line in head:
        m = _H1.match(line.strip())
        if m:
            title = m.group(1).strip()
            break
    block = lines[stamps[-1] :]
    # 段落自带重复的一级标题时去掉
    while block and not block[0].strip():
        block.pop(0)
    if block:
        m = _H1.match(block[0].strip())
        if m:
            block.pop(0)
            while block and not block[0].strip():
                block.pop(0)
    parts = []
    if title:
        parts.append(f"# {title}")
    parts.append("\n".join(block).strip())
    return "\n\n".join(p for p in parts if p) + "\n"


def strip_related(text: str) -> str:
    """去掉已有的「相关」段，避免重复执行时叠加。"""
    idx = text.find("\n## 相关")
    if idx == -1:
        return text.rstrip() + "\n"
    return text[:idx].rstrip() + "\n"


def add_links(text: str, targets: list[str]) -> str:
    body = strip_related(text)
    if not targets:
        return body
    links = "\n".join(f"- [[{t}]]" for t in targets)
    return f"{body.rstrip()}\n\n## 相关\n{links}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("MINIS_HOME") or str(Path.home() / "openminis"),
        help="OpenMinis 数据目录（含 knowledge/），默认 %(default)s",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不动盘")
    args = parser.parse_args()

    os.environ["MINIS_HOME"] = args.data_dir
    data_dir = Path(args.data_dir).expanduser()
    root = data_dir / "knowledge"
    if not root.is_dir():
        print(f"[!] 没有找到知识库目录：{root}")
        return 1

    from openminis.core.context import AppContext, set_app_context
    from openminis.tools.memory_tools import KNOWLEDGE_KINDS, rebuild_knowledge_index

    set_app_context(AppContext(data_dir=data_dir))

    flat = {p.stem: p for p in root.glob("*.md") if p.stem != "index"}
    if not flat:
        print("[=] knowledge/ 根目录已经没有平铺文档，无需迁移。")
        return 0

    actions: list[str] = []

    # 1) 备份
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = data_dir / f"knowledge_backup_{stamp}"
    if not args.dry_run:
        shutil.copytree(root, backup)
    print(f"[+] 备份 → {backup}")

    # 2) 合并 / 去重 / 归类
    kind_dir = {k["id"]: k["dir"] for k in KNOWLEDGE_KINDS}
    for stem, src in sorted(flat.items()):
        if stem in MERGE_INTO:
            actions.append(f"合并 {stem}.md → {MERGE_INTO[stem]}.md")
            if not args.dry_run:
                src.unlink(missing_ok=True)
            continue
        target_stem = stem
        text = src.read_text(encoding="utf-8", errors="replace")
        if target_stem in REWRITE:
            text = REWRITE[target_stem]
            actions.append(f"改写 {target_stem}.md（去重 + 合并副本）")
        elif target_stem in DEDUPE:
            text = keep_latest_block(text)
            actions.append(f"去重 {target_stem}.md（只留最新段落）")
        if target_stem in LINKS:
            text = add_links(text, LINKS[target_stem])
            actions.append(f"补双链 {target_stem}.md → {LINKS[target_stem]}")

        kind = CATEGORY.get(target_stem, "concepts")
        dest_dir = root / kind_dir[kind]
        dest = dest_dir / f"{target_stem}.md"
        actions.append(f"归档 {target_stem}.md → {kind_dir[kind]}/")
        if not args.dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            if src != dest:
                src.unlink(missing_ok=True)

    for a in actions:
        print(f"    - {a}")

    # 3) 索引
    if args.dry_run:
        print("[=] dry-run：未改动磁盘。")
        return 0
    index = rebuild_knowledge_index()
    print(f"[+] 重建索引 → {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
