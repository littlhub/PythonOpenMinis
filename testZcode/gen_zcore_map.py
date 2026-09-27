"""Generate ZCORE_PORTING_MAP.md from module headers (debug only, not shipped).

Reads the ``Ported from: ...`` line of every module under
``src/openminis/agent/zcode`` and emits the ``源 → Python → 状态`` ledger required by
ZCORE_PORTING.md §9. Also reports in-scope TypeScript files that have no Python
counterpart yet, grouped by directory — that is the porting-completeness metric.

Usage:  python gen_zcore_map.py [--check]
  --check   only print the summary + missing list, do not write the map.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from _root import PROJECT_ROOT as ROOT  # noqa: E402

PKG = ROOT / "src"
ZC = PKG / "openminis" / "agent" / "zcode"

TS_ROOT = (
    ROOT.parent
    / "ZCode-main"
    / "apps"
    / "zcode-cli"
    / "packages"
    / "core"
    / "src"
)
TS_CONTRACTS = (
    ROOT.parent
    / "ZCode-main"
    / "apps"
    / "zcode-cli"
    / "packages"
    / "contracts"
    / "src"
)

# ZCORE_PORTING.md §0.0 —— 只移植编程部分。以下目录/文件明确排除（办公 / 非编程内核）。
EXCLUDED_DIRS = {
    "browser-client",
    "repl",
    "workflow",
    "off-peak",
    "automation",
}
EXCLUDED_FILE_PATTERNS = [
    re.compile(r"^tool/handlers/read-pdf\.ts$"),
    re.compile(r"^tool/handlers/node-repl\.ts$"),
    re.compile(r"^tool/handlers/cron\.ts$"),
    re.compile(r"^tool/handlers/off-peak\.ts$"),
    re.compile(r"^tool/handlers/escalate\.ts$"),
    re.compile(r"^tool/handlers/(create|amend|save|eval)-workflow.*\.ts$"),
    re.compile(r"^tool/handlers/(list|get|resume)-workflow.*\.ts$"),
    re.compile(r"^tool/handlers/get-workflow-run.*\.ts$"),
    re.compile(r"^tool/handlers/resolve-workflow-question\.ts$"),
    re.compile(r"^tool/handlers/list-saved-workflows\.ts$"),
    re.compile(r"^tool/handlers/list-models\.ts$"),
    re.compile(r"^tool/handlers/workflow\.ts$"),
    re.compile(r"^tool/handlers/send-message\.ts$"),
    re.compile(r"^tool/handlers/respond-to-coordinator\.ts$"),
    re.compile(r"^tool/handlers/submit-result\.ts$"),
    re.compile(r"^tool/handlers/read-session-context\.ts$"),
    re.compile(r"^tool/handlers/amend-workflow-description\.ts$"),
    re.compile(r"^tool/handlers/workflow-.*\.ts$"),
    re.compile(r"^tool/handlers/eval-workflow-snippet\.ts$"),
    re.compile(r"^tool/handlers/cron-.*\.ts$"),
    # §0.0「图像/视频产出类工具 / 媒体预算、图片处理相关 handler」：bash 结果里的图片块
    # 处理属该排除项，Python 侧在调用点做有据可依的降级（见 handlers/bash.py 的 PORT 注）。
    re.compile(r"^tool/handlers/bash-image-output\.ts$"),
    # 契约侧：办公 / 非编程内核
    re.compile(
        r"^contracts/tools/(automation|off-peak|workflow|create-workflow|amend-workflow|"
        r"saved-workflow|eval-workflow-snippet|resolve-workflow-question|"
        r"list-saved-workflows|list-workflow-runs|get-workflow-run|"
        r"resume-workflow-run|get-workflow-run-roster|read-pdf|node-repl|"
        r"send-message|respond-to-coordinator|submit-result|escalate)\.ts$"
    ),
    # 接口侧：办公 / 非编程内核端口
    re.compile(
        r"^interfaces/(automation|off-peak|pdf-document|image-processor|browser-control|"
        r"dynamic-workflow-[a-z-]+|workflow-[a-z-]+|runtime-input-presentation)\.port\.ts$"
    ),
    re.compile(r"^interfaces/index\.ts$"),
    re.compile(r"^agent/runtime-input-presentation\.ts$"),
]

PORTED_FROM_RE = re.compile(r"^Ported from:\s*(.+)$", re.MULTILINE)
PARTIAL_RE = re.compile(r"^#\s*PORT-STATUS:\s*partial", re.MULTILINE)
# ``临时落点`` 类模块（如 tool/handlers/ports.py）一个文件覆盖多个上游模块，用
# ``# PORT: 真实来源 <path>`` 逐条标注。把文件里出现的所有上游路径都计入覆盖，
# 否则台账会把这些已落地契约重复列为「未移植」。
TS_PATH_RE = re.compile(r"packages/(?:core|contracts|shared)/src/[A-Za-z0-9_./-]+\.ts")


def is_excluded(rel: str) -> bool:
    parts = rel.split("/")
    if parts[0] in EXCLUDED_DIRS:
        return True
    return any(p.match(rel) for p in EXCLUDED_FILE_PATTERNS)


def py_module_py_paths():
    for py in sorted(ZC.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        yield py


def rel_py(py: Path) -> str:
    return py.relative_to(PKG).as_posix()


def parse_header(py: Path):
    """Return ``(primary_src, partial, all_srcs)``.

    ``primary_src`` 取 ``Ported from:`` 那一行（台账主键）；``all_srcs`` 是文件里出现的
    全部上游 TS 路径（含 ``# PORT: 真实来源`` 标注），用于完成度比对。
    """
    try:
        text = py.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None, False, []
    m = PORTED_FROM_RE.search(text)
    src = m.group(1).strip() if m else None
    if src:
        # 只取第一个路径片段（有些文件头带补充说明）
        src = src.rstrip(")")
        src = re.split(r"\s*[（(]\s*", src)[0].strip()
    # 头部 docstring 里的路径 + 正文 ``# PORT: 真实来源`` 里的路径
    all_srcs = sorted(set(TS_PATH_RE.findall(text)))
    return src, bool(PARTIAL_RE.search(text)), all_srcs


def main() -> int:
    check = "--check" in sys.argv

    rows: list[tuple[str, str, str]] = []
    py_only: list[tuple[str, str]] = []
    covered: set[str] = set()
    for py in py_module_py_paths():
        src, partial, all_srcs = parse_header(py)
        covered.update(all_srcs)
        rp = rel_py(py)
        if not src:
            py_only.append((rp, "文件头无 ``Ported from:`` —— 端口占位 / Python 专有"))
            continue
        rows.append((src, rp, "partial" if partial else "done"))

    # 范围内未移植的 TS
    missing: dict[str, list[str]] = {}
    for base, base_label in ((TS_ROOT, "core"), (TS_CONTRACTS, "contracts")):
        if not base.exists():
            continue
        for ts in sorted(base.rglob("*.ts")):
            if "__tests__" in ts.parts or ts.name.endswith(".d.ts"):
                continue
            rel = ts.relative_to(base).as_posix()
            if rel.startswith(("runtime/methods/", "index.ts")):
                continue
            if is_excluded(rel):
                continue
            needle = f"packages/{base_label}/src/{rel}"
            if needle in covered:
                continue
            missing.setdefault(rel.split("/")[0], []).append(rel)

    total_missing = sum(len(v) for v in missing.values())
    print(f"ported     : {len(rows)}")
    print(f"python-only: {len(py_only)}")
    print(f"partial    : {sum(1 for _, _, s in rows if s == 'partial')}")
    print(f"missing(TS, in-scope): {total_missing}")
    for k in sorted(missing):
        print(f"  {k}: {len(missing[k])}")
        for f in missing[k]:
            print(f"      {f}")

    if check:
        return 0

    by_dir: dict[str, list[tuple[str, str, str]]] = {}
    for src, rp, st in rows:
        key = rp.split("/")[3] if rp.count("/") >= 4 else rp.split("/")[2]
        by_dir.setdefault(key, []).append((src, rp, st))

    out = [
        "# ZCode 编程内核移植台账",
        "",
        "`TypeScript 源` → `Python 文件` → `状态`",
        "",
        "状态：`done` 完成 · `partial` 部分 / 占位（文件头有 `# PORT-STATUS: partial`）",
        "",
        "本文件由 `gen_zcore_map.py` 从各模块文件头的 `Ported from:` 自动生成，"
        "请勿手工编辑表格；改完代码重跑该脚本即可。",
        "",
        "边界见 `src/openminis/agent/zcode/ZCORE_PORTING.md` §0.0："
        "**只移植编程部分（agent / tool 基础设施与编程工具 / permission / compact / "
        "context / memory / runtime），不移植办公部分（PDF、Office、cron/off-peak、"
        "动态工作流、浏览器自动化、Node REPL、图像视频产出类）。**",
        "",
        f"合计已落地模块：**{len(rows)}**（其中 partial {sum(1 for _, _, s in rows if s == 'partial')}）；"
        f"范围内未移植 TS 文件：**{total_missing}**。",
        "",
        "## 范围内未移植（待办）",
        "",
    ]
    if total_missing == 0:
        out.append("（无）")
    else:
        out.append("| TS 目录 | 未移植文件 |")
        out.append("|---|---|")
        for k in sorted(missing):
            files = "、".join(f"`{f}`" for f in missing[k])
            out.append(f"| `{k}` | {files} |")
    out.append("")

    for key in sorted(by_dir):
        out.append(f"## {key}")
        out.append("")
        out.append("| TS 源 | Python | 状态 |")
        out.append("|---|---|---|")
        for src, rp, st in sorted(by_dir[key]):
            out.append(f"| `{src}` | `{rp}` | {st} |")
        out.append("")

    if py_only:
        out.append("## Python 专有 / 端口占位（无 ``Ported from:`` 头）")
        out.append("")
        out.append("| Python | 说明 |")
        out.append("|---|---|")
        for rp, note in py_only:
            out.append(f"| `{rp}` | {note} |")
        out.append("")

    (ZC / "ZCORE_PORTING_MAP.md").write_text("\n".join(out), encoding="utf-8")
    print(f"\nwrote {ZC / 'ZCORE_PORTING_MAP.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
