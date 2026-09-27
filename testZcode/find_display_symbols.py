"""Dev helper: list ZCode contracts/tools dir + find display symbol definitions."""
import os
import re

base = "G:/PythonOpenMinis-main/ZCode-main/apps/zcode-cli/packages/contracts/src/tools"
print("== tools dir ==")
for f in sorted(os.listdir(base)):
    print(f)

needles = [
    "SEND_MESSAGE_TOOL_NAME",
    "RESPOND_TO_COORDINATOR_TOOL_NAME",
    "TASK_OUTPUT_TOOL_NAME",
    "TASK_STOP_TOOL_NAME",
    "MCP_TOOL_DISPLAY_MAX_DESCRIPTION_CHARS",
    "MCP_TOOL_DISPLAY_MAX_NAME_CHARS",
    "TASK_OUTPUT_DISPLAY_MAX_OUTPUT_CHARS",
    "TASK_OUTPUT_DISPLAY_MAX_STATUS_CHARS",
    "CUA_TARGET_APP_DISPLAY_META_KEY",
    "ZCODE_MCP_NODE_REPL_CUA_APP_META_KEY",
    "SendMessageOutputSchema",
    "RespondToCoordinatorOutputSchema",
    "TaskOutputResultSchema",
    "TaskStopOutputSchema",
    "cuaTargetAppDisplaySchema",
    "nodeReplCuaAppDisplaySchema",
]
for root in [
    "G:/PythonOpenMinis-main/ZCode-main/apps/zcode-cli/packages/contracts/src",
    "G:/PythonOpenMinis-main/ZCode-main/apps/zcode-cli/packages/core/src",
]:
    for b, _, files in os.walk(root):
        for f in files:
            if not f.endswith(".ts"):
                continue
            p = os.path.join(b, f)
            try:
                s = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for n in needles:
                if re.search(
                    r"export\s+(?:function|const|interface|type|class)\s+" + re.escape(n) + r"\b",
                    s,
                ):
                    print(f"{n} -> {os.path.relpath(p, 'G:/PythonOpenMinis-main/ZCode-main')}")
