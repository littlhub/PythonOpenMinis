"""Dev helper: full traceback for zcode import failures."""
import sys
import traceback

sys.path.insert(0, "G:/PythonOpenMinis-main/PythonOpenMinis-main/src")
try:
    import openminis.agent.zcode.contracts.ids  # noqa: F401
    print("ids OK")
except Exception:
    traceback.print_exc()
print("====")
try:
    import openminis.agent.zcode.tool.types  # noqa: F401
    print("tool.types OK")
except Exception:
    traceback.print_exc()
