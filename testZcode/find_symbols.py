"""Dev helper: locate upstream definitions of missing symbols."""
import os
import re

root = "G:/PythonOpenMinis-main/ZCode-main"
needles = [
    "countPatchLines",
    "parseOfficialMcpToolError",
    "CUA_REQUEST_ACCESS_STATUS_META_KEY",
    "createCreateWorkflowDisplay",
    "createWorkflowObservationDisplay",
    "NodeReplCuaAppDisplay",
    "ToolResultDisplayPayload",
    "DiffHunk",
    "SEND_MESSAGE_TOOL_NAME",
    "TASK_OUTPUT_TOOL_NAME",
    "CUA_TARGET_APP_DISPLAY_META_KEY",
    "MCP_TOOL_DISPLAY_MAX",
    "TaskOutputResultSchema",
    "SendMessageOutputSchema",
    "RespondToCoordinatorOutputSchema",
    "TaskStopOutputSchema",
    "HookEventName",
]
hits: dict[str, list[str]] = {}
count_files = 0
for base, _, files in os.walk(root):
    if "node_modules" in base:
        continue
    for f in files:
        if not f.endswith(".ts"):
            continue
        p = os.path.join(base, f)
        try:
            s = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        count_files += 1
        for n in needles:
            # definition patterns: export function X / export const X / export interface X / export type X / export class X
            if re.search(
                r"export\s+(?:function|const|interface|type|class|default)\s+" + re.escape(n) + r"\b",
                s,
            ):
                hits.setdefault(n, []).append(p)
print("scanned ts files:", count_files)
for n in needles:
    print(n, "->", hits.get(n, "(none)"))
