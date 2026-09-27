"""Dev helper: locate serializeWorkflowArtifact anywhere in ZCode-main."""
import os

root = "G:/PythonOpenMinis-main/ZCode-main"
hits = []
for base, _, files in os.walk(root):
    if "node_modules" in base:
        continue
    for f in files:
        if f.endswith((".ts", ".js", ".mjs")):
            p = os.path.join(base, f)
            try:
                s = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            if "serializeWorkflowArtifact" in s:
                hits.append(p)
print(len(hits))
for p in hits[:12]:
    print(p)
