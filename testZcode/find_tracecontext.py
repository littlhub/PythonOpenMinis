"""Dev helper: locate TraceContext definition in ZCode sources."""
import os

root = "G:/PythonOpenMinis-main/ZCode-main/apps/zcode-cli/packages"
for base, _, files in os.walk(root):
    if "node_modules" in base:
        continue
    for f in files:
        if not f.endswith(".ts"):
            continue
        p = os.path.join(base, f)
        try:
            s = open(p, encoding="utf-8").read()
        except Exception:
            continue
        if "interface TraceContext" in s or "type TraceContext" in s:
            print(p)
            for i, line in enumerate(s.splitlines()):
                if "interface TraceContext" in line or "type TraceContext" in line:
                    print("  L%d: %s" % (i + 1, line[:160]))
