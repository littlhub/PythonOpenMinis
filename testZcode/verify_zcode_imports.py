"""Import-health checker for openminis.agent.zcode (debug only, not shipped)."""
from __future__ import annotations

import importlib
import os
import traceback
from pathlib import Path

from _root import PROJECT_ROOT as ROOT, SRC_ROOT as PKG  # noqa: E402

import sys
sys.path.insert(0, str(PKG))

TOP = "openminis.agent.zcode"

ok, fail_syntax, fail_import = [], [], []
missing = []  # imported names that don't exist

for py in sorted((ROOT / "src" / "openminis" / "agent" / "zcode").rglob("*.py")):
    mod = ".".join([TOP, *(py.relative_to(ROOT / "src" / "openminis" / "agent" / "zcode").with_suffix("").parts)])
    try:
        importlib.import_module(mod)
        ok.append(mod)
    except SyntaxError as e:
        fail_syntax.append((mod, str(e)))
    except Exception as e:  # noqa
        fail_import.append((mod, f"{type(e).__name__}: {e}"))

print(f"OK={len(ok)}  SYNTAX_ERR={len(fail_syntax)}  IMPORT_ERR={len(fail_import)}")
if not ok and not fail_syntax and not fail_import:
    # 一个模块都没收集到 = 扫描根算错（脚本搬家/到处调用），不是「导入健康」。
    print(f"=== NO MODULES COLLECTED：扫描根没算对，本次结果无意义 ===")
    print(f"    ROOT = {ROOT}")
    raise SystemExit(1)
print("\n=== SYNTAX ERRORS ===")
for m, e in fail_syntax:
    print(f"  {m}\n    {e}")
print("\n=== IMPORT ERRORS (grouped) ===")
# group by error tail to find common missing symbols
from collections import Counter
c = Counter()
for m, e in fail_import:
    tail = e.split("cannot import name")[0] if "cannot import name" in e else (e.split("No module named")[0] if "No module named" in e else "")
    c[e] += 1
for m, e in fail_import:
    print(f"  {m}\n    {e}")
print("\n=== IMPORT ERROR TAILS (top) ===")
for e, n in c.most_common(40):
    print(f"  [{n}x] {e}")
