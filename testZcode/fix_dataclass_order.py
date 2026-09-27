"""Auto-fix dataclass field-ordering violations: give trailing required fields
``Optional[...] = None`` defaults. Conservative: only modifies AnnAssign fields
that currently have no default and appear after a defaulted field in the class.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from _root import SRC_ROOT

ROOT = SRC_ROOT / "openminis" / "agent" / "zcode"
TARGETS = [
    ROOT / "runtime" / "types.py",
    ROOT / "tool" / "types.py",
]

for path in TARGETS:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    edits = []  # (lineno_1based, new_line)
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        is_dc = any(
            (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "dataclass")
            or (isinstance(d, ast.Name) and d.id == "dataclass")
            for d in cls.decorator_list
        )
        if not is_dc:
            continue
        seen_default = False
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if stmt.value is None:
                    # no default
                    if seen_default:
                        lineno = stmt.lineno
                        raw = lines[lineno - 1]
                        m = re.match(r"^(\s*)(\w+):\s*(.+?)(\s*#.*)?$", raw)
                        if not m:
                            print(f"SKIP (no match) {path}:{lineno}: {raw!r}")
                            continue
                        indent, name, ann, comment = m.groups()
                        ann = ann.strip()
                        if "Optional" in ann or ann.rstrip().endswith("| None") or "| None" in ann:
                            new_ann = ann
                        else:
                            new_ann = f"Optional[{ann}]"
                        new_line = f"{indent}{name}: {new_ann} = None{comment or ''}\n"
                        edits.append((lineno, new_line))
                else:
                    seen_default = True
    # apply edits (descending to preserve line numbers)
    for lineno, new_line in sorted(edits, key=lambda e: e[0], reverse=True):
        lines[lineno - 1] = new_line
    path.write_text("".join(lines), encoding="utf-8")
    print(f"{path}: fixed {len(edits)} fields")
