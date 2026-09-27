"""Dev helper: find runtime_task.* importers."""
import os
import re

root = "G:/PythonOpenMinis-main/PythonOpenMinis-main/src/openminis/agent/zcode"
pat = re.compile(
    r"from\s+\.+runtime_task\.(registry|notification|index)?\s+import\s+(?:(\()([^)]*)\))?\s*([^\n]*)",
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
            body = (m.group(3) or m.group(4) or "").strip()
            print("%s | runtime_task.%s | %s" % (rel, m.group(1) or "<pkg>", re.sub(r"#.*", "", body)[:300]))
