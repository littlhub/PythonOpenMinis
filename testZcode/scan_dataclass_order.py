"""Scan zcode package for dataclass field-ordering violations (non-default after default)."""
from __future__ import annotations

import ast
from pathlib import Path

from _root import SRC_ROOT  # noqa: E402

ROOT = SRC_ROOT / "openminis" / "agent" / "zcode"

VIOLATING_DEFAULT_TOKENS = ("=", "field", "Field")

def has_default(node: ast.AnnAssign) -> bool:
    if node.value is None:
        return False
    # default present if `= value`
    return True

problems = []
for py in sorted(ROOT.rglob("*.py")):
    src = py.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        is_dataclass = any(
            isinstance(d, ast.Call) and getattr(d.func, "id", "") == "dataclass"
            for d in cls.decorator_list
        ) or any(
            isinstance(d, ast.Name) and d.id == "dataclass" for d in cls.decorator_list
        )
        if not is_dataclass:
            continue
        seen_default = False
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                # skip ClassVar / non-field? keep simple
                if has_default(stmt):
                    seen_default = True
                else:
                    if seen_default:
                        problems.append((str(py), cls.name, stmt.target.id))

for p, c, f in problems:
    print(f"{p} :: {c} :: field '{f}' (non-default after default)")
print(f"\nTOTAL: {len(problems)} violations")
if not ROOT.is_dir():
    # 扫描根不存在 = 搬家后路径算错，不是「零违规」。
    print(f"=== SCAN ROOT MISSING：{ROOT} 不存在，本次结果无意义 ===")
    raise SystemExit(1)
