"""Scan the whole tree for syntax that only Python 3.12+ accepts.

Why this exists
---------------
``pyproject.toml`` declares ``requires-python = ">=3.11"`` but the other four
gates all run under whatever interpreter you happen to launch them with.  On a
3.12+ interpreter the two PEP 701 relaxations below compile fine, so an entire
subsystem can be dead on 3.11 while every gate stays green:

* a backslash inside the **expression part** of an f-string
  (``f"{d[len('\\\\?\\UNC\\'):]}"``)
* an f-string expression spanning **multiple lines in code position**
  (a newline *inside a string literal* of the expression is fine — see below)

Both were real regressions in this repo: they made
``zcode.integration.toolchain`` unimportable, which silently emptied the ZCode
tool catalogue and broke tool borrowing for the ``zcode`` subagent.

So this scanner is deliberately interpreted independently of the host Python
version: it parses with the *current* interpreter (lenient) and then inspects
the f-string expression source segments.  A flagged file is a hard error on
3.11.  ``_self_test`` re-checks the detector against known-bad / known-good
samples on every run, so the gate cannot silently rot.

Run:  python scan_py311_syntax.py      (expect ``TOTAL FINDINGS = 0``)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from _root import PROJECT_ROOT  # noqa: E402

SCAN_ROOT_DIRS = ("src", "tests")
SCAN_GLOBS = ("*.py",)
SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".venv", "build", "dist"}


def _iter_files() -> list[Path]:
    # 锚在工程根上，**不依赖 cwd**：本脚本搬过一次家（原先在工程根，现在
    # ``testZcode/``），按相对路径扫会在新位置上扫到 0 个文件，然后打印
    # 「TOTAL FINDINGS = 0」—— 一个检测器空转出来的假绿，比不跑还糟。
    found: list[Path] = []
    for root in SCAN_ROOT_DIRS:
        base = PROJECT_ROOT / root
        if not base.is_dir():
            continue
        for pattern in SCAN_GLOBS:
            for path in sorted(base.rglob(pattern)):
                if any(part in SKIP_DIR_NAMES for part in path.parts):
                    continue
                found.append(path)
    for path in sorted(PROJECT_ROOT.glob("*.py")):
        if path.is_file():
            found.append(path)
    return found


def _expression_segment(source: str, node: ast.FormattedValue) -> str:
    """Best-effort source text of a ``{...}`` expression (``ast`` 3.12+ has spans)."""
    segment = ast.get_source_segment(source, node.value)
    if segment is not None:
        return segment
    lines = source.splitlines()
    try:
        return "\n".join(lines[node.lineno - 1: node.end_lineno])
    except Exception:  # pragma: no cover - 行号缺失时放弃
        return ""


def _string_spans(node: ast.FormattedValue) -> list[tuple[int, int]]:
    """表达式内所有字符串字面量占用的 ``[起始行, 结束行)``（含跨行三引号串）。

    这些行**内部**的换行在 3.11 上合法（``f\"{'a' if x else '''\\n多行\\n'''}\"``
    在 3.10 就能编译）—— 所以判「表达式跨行」时必须把它们排除掉，否则会把正常的
    三引号字符串误报成语法错误。
    """
    spans: list[tuple[int, int]] = []
    for sub in ast.walk(node.value):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, (str, bytes)):
            start = getattr(sub, "lineno", None)
            end = getattr(sub, "end_lineno", None)
            if start is not None and end is not None and end > start:
                spans.append((start, end))
    return spans


def _structural_newline(node: ast.FormattedValue) -> bool:
    """表达式区段里是否存在**字符串字面量之外**的换行（3.11 上非法）。"""
    start = getattr(node.value, "lineno", None)
    end = getattr(node.value, "end_lineno", None)
    if start is None or end is None or end == start:
        return False
    spans = _string_spans(node)
    for line in range(start, end):
        # 第 line 行之后的那个换行落在某段字符串字面量里 = 合法。
        if any(span_start <= line < span_end for span_start, span_end in spans):
            continue
        return True
    return False


def _findings_in(source: str) -> list[tuple[int, str]]:
    """一份源码里的 PEP 701 违规点（行号 + 原因）。"""
    findings: list[tuple[int, str]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        # 当前解释器都解析不了 —— 更老的解释器只会更糟。
        return [(exc.lineno or 0, f"SyntaxError: {exc.msg}")]
    for node in ast.walk(tree):
        if not isinstance(node, ast.FormattedValue):
            continue
        expr = _expression_segment(source, node)
        if "\\" in expr:
            findings.append((node.lineno, "f-string 表达式含反斜杠（3.12+ 才合法）"))
        elif _structural_newline(node):
            findings.append((node.lineno, "f-string 表达式跨行（3.12+ 才合法）"))
    return findings


#: 已知在 3.11 上**编译不过**的写法（本仓库真实踩过的两个 + 字典跨行）。
_BAD_SAMPLES = [
    'v = f"{a +\n b}"',
    "v = f\"{d[len('\\\\?\\\\UNC\\\\'):]}\"",
    "v = f\"{fn({\n 'a': 1,\n})}\"",
]

#: 已知在 3.11 上**合法**、但很容易被误判的写法（字面量内部的换行）。
_GOOD_SAMPLES = [
    "v = f\"{'a' if _IS_WIN else '''\n多行字符串\n'''}\"",
    'v = f"{x}"',
]


def _self_test() -> list[str]:
    """确认检测器没被改瞎：该报的报、不该报的不报。"""
    problems: list[str] = []
    for sample in _BAD_SAMPLES:
        if not _findings_in(sample):
            problems.append(f"漏报: {sample!r}")
    for sample in _GOOD_SAMPLES:
        got = _findings_in(sample)
        if got:
            problems.append(f"误报: {sample!r} -> {got}")
    return problems


def main() -> int:
    problems = _self_test()
    if problems:
        print("=== SELF TEST FAILED（检测器本身有问题，下面的结果不可信） ===")
        for item in problems:
            print(f"  {item}")

    findings: list[tuple[str, int, str]] = []
    scanned = 0
    for path in _iter_files():
        source = path.read_text(encoding="utf-8")
        scanned += 1
        for lineno, reason in _findings_in(source):
            findings.append((str(path), lineno, reason))

    for path, line, reason in findings:
        print(f"{path}:{line} :: {reason}")
    print(f"scanned {scanned} files with Python {sys.version.split()[0]}")
    print(f"\nTOTAL FINDINGS = {len(findings)}")
    if scanned == 0:
        # 一个文件都没扫到 = 扫描根算错了（脚本搬家/到处调用），不是「没问题」。
        print("=== NO FILES SCANNED：扫描根没算对，本次结果无意义 ===")
        print(f"    PROJECT_ROOT = {PROJECT_ROOT}")
        return 1
    return 1 if (findings or problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
