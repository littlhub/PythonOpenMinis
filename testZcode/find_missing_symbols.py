"""Dev helper: locate remaining contract symbols in ZCode sources."""
import os

root = "G:/PythonOpenMinis-main/ZCode-main/apps/zcode-cli/packages/contracts/src"
needles = ["BashOutputSchema", "isAutomationCreateLimitError"]
for base, _, files in os.walk(root):
    for f in files:
        if not f.endswith(".ts"):
            continue
        p = os.path.join(base, f)
        try:
            s = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for n in needles:
            if n in s:
                for i, line in enumerate(s.splitlines(), 1):
                    if n in line and ("export" in line or "const" in line or "function" in line or "interface" in line):
                        print("%s L%d: %s" % (os.path.relpath(p, root), i, line.strip()[:150]))
