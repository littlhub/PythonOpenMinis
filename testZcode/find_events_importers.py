"""Dev helper: precise contracts.* import sites under zcode."""
import os
import re

root = "G:/PythonOpenMinis-main/PythonOpenMinis-main/src/openminis/agent/zcode"
# match: from ..contracts.events import X / from ...contracts.tracing import (...)
pat = re.compile(
    r"from\s+\.+\.?contracts(?:\.(\w+))?\s+import\s+(?:(\()([^)]*)\))?\s*([^\n]*)",
    re.M,
)
for dirpath, _, files in os.walk(root):
    if "__pycache__" in dirpath:
        continue
    for f in sorted(files):
        if not f.endswith(".py"):
            continue
        p = os.path.join(dirpath, f)
        rel = os.path.relpath(p, root)
        s = open(p, encoding="utf-8").read()
        for m in pat.finditer(s):
            sub = m.group(1) or ""
            body = (m.group(3) or m.group(4) or "").strip()
            body = re.sub(r"#.*", "", body).strip()
            body = body.rstrip(",")
            print("%s | contracts.%s | %s" % (rel, sub or "<root>", body[:200]))
