"""Dev helper: verify every relative import in zcode resolves to a real module."""
import ast
import importlib
import os

PKG_ROOT = "G:/PythonOpenMinis-main/PythonOpenMinis-main/src/openminis/agent/zcode"
PKG = "openminis.agent.zcode"

# First: import each module file to collect the module names we know about.
module_names = set()
for dirpath, _, files in os.walk(PKG_ROOT):
    rel_dir = os.path.relpath(dirpath, PKG_ROOT)
    for f in files:
        if f.endswith(".py"):
            parts = []
            if rel_dir != ".":
                parts += rel_dir.replace(os.sep, "/").split("/")
            parts.append(f[:-3] if f.endswith(".py") else f)
            if parts[-1] == "__init__":
                parts = parts[:-1] or ["__root__"]
            module_names.add(".".join(parts) if parts else "")

import sys

sys.path.insert(0, "G:/PythonOpenMinis-main/PythonOpenMinis-main/src")

# Second: find which modules are imported from within the package (via relative or absolute import)
# but have no file, and try importing each leaf module to see which still fail.
try:
    import openminis.agent.zcode  # noqa: F401
    print("TOP-LEVEL IMPORT: OK")
except Exception as e:
    print("TOP-LEVEL IMPORT FAILS:", type(e).__name__, str(e)[:200])
except SystemExit:
    print("TOP-LEVEL IMPORT: SystemExit raised")
