"""Dev helper: find all tracing importers under zcode."""
import os

root = "G:/PythonOpenMinis-main/PythonOpenMinis-main/src/openminis/agent/zcode"
needles = [
    "contracts.tracing",
    "TraceContext",
    "create_root_trace_context",
    "create_child_trace_context",
    "createTracer",
    "run_with_context",
]
for dirpath, _, files in os.walk(root):
    if "__pycache__" in dirpath:
        continue
    for f in files:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dirpath, f)
        rel = os.path.relpath(p, root)
        try:
            s = open(p, encoding="utf-8").read()
        except Exception:
            continue
        found = []
        for line_no, line in enumerate(s.splitlines(), 1):
            for n in needles:
                if n in line and not line.lstrip().startswith("#"):
                    found.append((line_no, line.rstrip()[:140]))
        if found:
            print("==", rel)
            for line_no, line in found[:12]:
                print("  L%d: %s" % (line_no, line))
