"""Dev helper: inspect TraceContext / trace factory call sites."""
import os
import re

root = "G:/PythonOpenMinis-main/PythonOpenMinis-main/src/openminis/agent/zcode"
pats = [
    r"create_root_trace_context\((?:[^()]|\([^()]*\))*\)",
    r"create_child_trace_context\((?:[^()]|\([^()]*\))*\)",
    r"TraceContext\(",
    r"trace_context\.\w+",
    r"TraceContext\(\w+.*?\)",
]
for dirpath, _, files in os.walk(root):
    if "__pycache__" in dirpath:
        continue
    for f in files:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dirpath, f)
        rel = os.path.relpath(p, root)
        s = open(p, encoding="utf-8").read()
        seen: set[str] = set()
        for m in re.finditer(r"create_root_trace_context\(|create_child_trace_context\(|TraceContext\(|trace_context\.\w+", s):
            line_start = s.rfind("\n", 0, m.start()) + 1
            line_end = s.find("\n", m.end())
            line = s[line_start:line_end if line_end != -1 else len(s)]
            key = (rel, line.strip())
            if key in seen:
                continue
            seen.add(key)
            print("%s | %s" % (rel, line.strip()[:150]))
