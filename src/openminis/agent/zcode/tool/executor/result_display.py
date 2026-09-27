"""Tool result display — build the UI-facing display payload for a tool result.

Ported from: packages/core/src/tool/executor/result-display.ts
Original package: @zcode/core (tool/executor)

# PORT-STATUS: partial
# 本文件依赖若干**未移植**模块（按 §0.0 属 scope: non-coding 或尚未落地），直连预测落点：
#   - `./create-workflow-display.js` → `from .create_workflow_display import create_create_workflow_display`
#   - `./workflow-observation-display.js` → `from .workflow_observation_display import create_workflow_observation_display`
#   - `@zcode/shared` 的 ``parseOfficialMcpToolError`` → `from ...shared import parse_official_mcp_tool_error`
#   - `@zcode/zcode-cua/request-access-contract` → `from ...zcode_cua.request_access_contract import ...`
#   - `../diff.js` 的 ``countPatchLines`` → `from ..diff import count_patch_lines`
# 这些 import 在 py_compile 阶段不会解析；待对应模块落地后可直接连通。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ...contracts.model import (  # type: ignore
    DiffHunk,
    NodeReplCuaAppDisplay,
    ToolResultDisplayPayload,
)
from ...contracts.tools import (  # type: ignore
    CUA_TARGET_APP_DISPLAY_META_KEY,
    MCP_TOOL_DISPLAY_MAX_DESCRIPTION_CHARS,
    MCP_TOOL_DISPLAY_MAX_NAME_CHARS,
    RESPOND_TO_COORDINATOR_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
    TASK_OUTPUT_DISPLAY_MAX_OUTPUT_CHARS,
    TASK_OUTPUT_DISPLAY_MAX_STATUS_CHARS,
    TASK_OUTPUT_TOOL_NAME,
    TASK_STOP_TOOL_NAME,
    ZCODE_MCP_NODE_REPL_CUA_APP_META_KEY,
    RespondToCoordinatorOutputSchema,
    SendMessageOutputSchema,
    TaskOutputResultSchema,
    TaskStopOutputSchema,
    cuaTargetAppDisplaySchema,
    nodeReplCuaAppDisplaySchema,
)
from ...shared import parse_official_mcp_tool_error  # type: ignore
from ...zcode_cua.request_access_contract import (  # type: ignore
    CUA_REQUEST_ACCESS_STATUS_META_KEY,
    cua_request_access_status_schema,
)
from ..diff import count_patch_lines  # type: ignore
from .bash_result_display import create_bash_result_display
from .create_workflow_display import create_create_workflow_display  # type: ignore  # noqa: F401
from .display_text import bound_display_text
from .utils import is_record
from .workflow_observation_display import create_workflow_observation_display  # type: ignore  # noqa: F401

__all__ = ["create_mcp_tool_display", "create_tool_result_display"]

MAX_DISPLAY_DIFF_HUNKS = 8
MAX_DISPLAY_DIFF_LINES = 160
MAX_SEND_MESSAGE_DISPLAY_FIELD_BYTES = 4 * 1024
MAX_TASK_STOP_DISPLAY_FIELD_BYTES = 16 * 1024
MAX_NODE_REPL_DISPLAY_IMAGE_BASE64_BYTES = 200 * 1024
MAX_NODE_REPL_DISPLAY_IMAGES = 2


def create_mcp_tool_display(
    metadata: Any,
    output: Any = None,
) -> Any:
    if metadata is None:
        return None
    server_name = _bound_mcp_display_text(metadata.get("serverName", ""), MCP_TOOL_DISPLAY_MAX_NAME_CHARS)
    tool_name = _bound_mcp_display_text(metadata.get("toolName", ""), MCP_TOOL_DISPLAY_MAX_NAME_CHARS)
    if not server_name or not tool_name:
        return None
    description = (
        _bound_mcp_display_text(metadata.get("description", ""), MCP_TOOL_DISPLAY_MAX_DESCRIPTION_CHARS)
        if metadata.get("description")
        else None
    )
    unavailable = _read_official_mcp_unavailable(output) if metadata.get("official") else None
    payload: dict[str, Any] = {
        "kind": "mcp_tool",
        "serverName": server_name,
        "toolName": tool_name,
    }
    if description is not None:
        payload["description"] = description
    if unavailable is not None:
        payload["unavailable"] = unavailable
    return payload


def _read_official_mcp_unavailable(output: Any) -> Optional[dict[str, Any]]:
    if not is_record(output) or output.get("isError") is not True or not isinstance(output.get("content"), list):
        return None
    for block in output["content"]:
        if not is_record(block) or block.get("type") != "text" or not isinstance(block.get("text"), str):
            continue
        parsed = parse_official_mcp_tool_error(block["text"])
        if parsed is not None:
            return {"code": parsed.code}
    return None


def create_tool_result_display(
    tool_name: str,
    output: Any,
    options: Optional[dict[str, Any]] = None,
) -> Any:
    if tool_name == "Bash":
        return create_bash_result_display(output)

    cua_tool_name = _read_cua_tool_name(tool_name)
    if cua_tool_name:
        return _create_cua_tool_result_display(cua_tool_name, output, options.get("officialCua") is True if options else False)

    node_repl_display = _create_node_repl_display(tool_name, output)
    if node_repl_display is not None:
        return node_repl_display

    # 拆到 create-workflow-display.ts 后保持既有导出面（handlers/create-workflow.ts 仍从这里 import）。
    create_workflow = create_create_workflow_display(tool_name, output)
    if create_workflow is not None:
        return create_workflow

    workflow_observation = create_workflow_observation_display(tool_name, output)
    if workflow_observation is not None:
        return workflow_observation

    if options is not None and options.get("mcp") is not None:
        # 结果级构造：官方 MCP 的不可用标识只能从本次结果里读，因此把 output 一起传进去。
        return create_mcp_tool_display(options["mcp"], output)

    if tool_name == SEND_MESSAGE_TOOL_NAME:
        parsed = SendMessageOutputSchema.safe_parse(output) if hasattr(SendMessageOutputSchema, "safe_parse") else _safe(output)
        if not getattr(parsed, "success", False):
            return None
        # display 不经过 tool result budget，必须在进入实时事件和持久化 metadata 前单独限长。
        error = (
            None
            if parsed.data.get("error") is None
            else bound_display_text(parsed.data["error"], MAX_SEND_MESSAGE_DISPLAY_FIELD_BYTES).value
        )
        message = (
            None
            if parsed.data.get("message") is None
            else bound_display_text(parsed.data["message"], MAX_SEND_MESSAGE_DISPLAY_FIELD_BYTES).value
        )
        payload: dict[str, Any] = {
            "kind": "local_agent_message",
            "status": parsed.data.get("status"),
        }
        if error is not None:
            payload["error"] = error
        if message is not None:
            payload["message"] = message
        return payload

    if tool_name == TASK_STOP_TOOL_NAME:
        parsed = TaskStopOutputSchema.safe_parse(output) if hasattr(TaskStopOutputSchema, "safe_parse") else _safe(output)
        if not getattr(parsed, "success", False):
            return None
        command = (
            bound_display_text(parsed.data["command"], MAX_TASK_STOP_DISPLAY_FIELD_BYTES)
            if parsed.data.get("command") is not None
            else None
        )
        message = bound_display_text(_compact_task_stop_display_message(parsed.data), MAX_TASK_STOP_DISPLAY_FIELD_BYTES)
        truncated = (command.truncated if command is not None else False) or message.truncated
        payload = {
            "kind": "task_stop",
            "taskId": parsed.data.get("task_id"),
            "taskType": parsed.data.get("task_type"),
        }
        if command is not None:
            payload["command"] = command.value
        payload["message"] = message.value
        if truncated:
            payload["truncated"] = True
        return payload

    if tool_name == TASK_OUTPUT_TOOL_NAME:
        parsed = TaskOutputResultSchema.safe_parse(output) if hasattr(TaskOutputResultSchema, "safe_parse") else _safe(output)
        if not getattr(parsed, "success", False):
            return None
        task = parsed.data.get("task") or {}
        task_status = task.get("status", "").strip()[:TASK_OUTPUT_DISPLAY_MAX_STATUS_CHARS] if isinstance(task.get("status"), str) else None
        full_output = task.get("output", "").rstrip() if isinstance(task.get("output"), str) else None
        has_output = full_output is not None and len(full_output.strip()) > 0
        truncated = has_output and len(full_output) > TASK_OUTPUT_DISPLAY_MAX_OUTPUT_CHARS
        # UI display 是独立于 provider content 的有界投影；禁止把完整 TaskOutput XML
        # 或结果对象塞进实时事件和持久化 metadata。
        payload = {
            "kind": "task_output",
            "retrievalStatus": parsed.data.get("retrieval_status"),
        }
        if task_status:
            payload["taskStatus"] = task_status
        if has_output:
            payload["output"] = full_output[:TASK_OUTPUT_DISPLAY_MAX_OUTPUT_CHARS]
        if truncated:
            payload["truncated"] = True
        return payload

    if tool_name == RESPOND_TO_COORDINATOR_TOOL_NAME:
        parsed = RespondToCoordinatorOutputSchema.safe_parse(output) if hasattr(RespondToCoordinatorOutputSchema, "safe_parse") else _safe(output)
        if not getattr(parsed, "success", False):
            return None
        return {
            "kind": "respond_to_coordinator",
            "status": parsed.data.get("status"),
        }

    if not is_record(output):
        return None
    file_path = output.get("filePath")
    structured_patch = output.get("structuredPatch")
    if not isinstance(file_path, str) or not isinstance(structured_patch, list):
        return None

    hunks = [h for h in structured_patch if _is_diff_hunk(h)]
    if len(hunks) == 0:
        return None

    additions, deletions = count_patch_lines(hunks)
    bounded_patch, truncated = _bound_diff_hunks(hunks)
    return {
        "kind": "file_diff",
        "filePath": file_path,
        "additions": additions,
        "deletions": deletions,
        "structuredPatch": bounded_patch,
        "truncated": truncated,
    }


def _bound_mcp_display_text(value: str, max_chars: int) -> Optional[str]:
    trimmed = value.strip()
    if not trimmed:
        return None
    bounded = trimmed[:max_chars]
    # MCP discovery 是外部输入，直接 slice 可能在 UTF-16 surrogate pair
    # 中间截断，生成无法稳定跨事件、持久化和 replayable snapshot 的字符串。
    # 若边界落在高位 surrogate 后，丢弃这个半字符，保证 display 始终可安全序列化。
    last_code = ord(bounded[-1])
    return bounded[:-1] if 0xD800 <= last_code <= 0xDBFF else bounded


MAX_CUA_DISPLAY_FIELD_BYTES = 32 * 1024
MAX_CUA_INLINE_MEDIA_BYTES = 256 * 1024
MAX_CUA_INLINE_MEDIA_TOTAL_BYTES = 512 * 1024


def _read_cua_tool_name(tool_name: str) -> Optional[str]:
    # 兼容两种 CUA 工具命名：直接的 `mcp__computer_use__<action>` 与 plugin
    # 命名空间形式 `mcp__plugin_zcode-cua_computer-use__<action>`。
    # 归一化（小写 + `-`→`_`）后：名字包含 `computer_use`，且 action 是最后一个 `__` 之后的子串。
    normalized = tool_name.strip().lower().replace("-", "_")
    if "computer_use" not in normalized:
        return None
    last_sep = normalized.rfind("__")
    if last_sep == -1:
        return None
    action = normalized[last_sep + len("__"):]
    return action if len(action) > 0 else None


def _create_cua_tool_result_display(tool_name: str, output: Any, official_cua: bool) -> Any:
    result = output if is_record(output) else {}
    content = result.get("content") if isinstance(result.get("content"), list) else []
    text = "\n".join(
        item.get("text", "")
        for item in content
        if is_record(item) and item.get("type") == "text" and isinstance(item.get("text"), str)
    )
    structured_content = result.get("structuredContent")
    structured_record = structured_content if is_record(structured_content) else None
    error_record = structured_record.get("error") if is_record(structured_record) and is_record(structured_record.get("error")) else None
    structured_json = (
        None
        if structured_content is None
        else bound_display_text(_safe_json(structured_content), MAX_CUA_DISPLAY_FIELD_BYTES)
    )
    bounded_text = bound_display_text(text, MAX_CUA_DISPLAY_FIELD_BYTES) if text else None
    # artifact URI 只在 Agent 本地可读，直接投影会让多端 UI 收到无法渲染的媒体。
    # 在受控读取 API 建立前，display 只承载可直接渲染的内联图片。
    media: list[dict[str, str]] = []
    inline_media_bytes = 0
    media_truncated = False
    for item in content:
        if not is_record(item):
            continue
        projected_media: Optional[dict[str, str]] = None
        decoded_bytes = 0
        if (
            item.get("type") == "image"
            and isinstance(item.get("mimeType"), str)
            and isinstance(item.get("data"), str)
        ):
            decoded_bytes = _b64_len(item["data"])
            projected_media = {"mimeType": item["mimeType"], "data": item["data"]}
        if projected_media is None:
            continue
        # media 配额只约束真实媒体，前置 text block 不能吞掉截图位置。
        if len(media) >= 4:
            media_truncated = True
            break
        if decoded_bytes > MAX_CUA_INLINE_MEDIA_BYTES or inline_media_bytes + decoded_bytes > MAX_CUA_INLINE_MEDIA_TOTAL_BYTES:
            media_truncated = True
            continue
        inline_media_bytes += decoded_bytes
        media.append(projected_media)
    truncated = media_truncated or (structured_json.truncated if structured_json is not None else False) or (bounded_text.truncated if bounded_text is not None else False)
    meta = result.get("_meta") if is_record(result.get("_meta")) else None
    target_app = (
        cua_target_app_display_schema.safe_parse(meta.get(CUA_TARGET_APP_DISPLAY_META_KEY))
        if official_cua and meta is not None
        else None
    )
    permission_status = (
        cua_request_access_status_schema.safe_parse(meta.get(CUA_REQUEST_ACCESS_STATUS_META_KEY))
        if official_cua and tool_name == "request_access" and meta is not None
        else None
    )

    # MCP modelContent 会把 structuredContent 展平成文本；在展平前生成独立、有限长的
    # display，才能让实时事件和历史会话稳定区分 CUA 错误与结构化结果。
    payload: dict[str, Any] = {
        "kind": "cua",
        "schemaVersion": 1,
        "toolName": tool_name,
        "status": "failed" if result.get("isError") is True else "success",
    }
    if structured_json is not None:
        payload["structuredContent"] = structured_json.value
    if bounded_text is not None:
        payload["text"] = bounded_text.value
    if isinstance(error_record, dict) and isinstance(error_record.get("code"), str):
        payload["errorCode"] = error_record["code"]
    if isinstance(error_record, dict) and isinstance(error_record.get("suggested_action"), str):
        payload["suggestedAction"] = error_record["suggested_action"]
    if target_app is not None and getattr(target_app, "success", False):
        payload["targetApp"] = target_app.data
    if permission_status is not None and getattr(permission_status, "success", False):
        payload["permissionStatus"] = permission_status.data
    if len(media) > 0:
        payload["media"] = media
    if truncated:
        payload["truncated"] = True
    return payload


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value if value is not None else None)
    except Exception:  # noqa: BLE001
        return "null"


def _read_node_repl_cua_app(output: dict[str, Any]) -> Optional[Any]:
    meta = output.get("_meta") if is_record(output.get("_meta")) else None
    parsed = node_repl_cua_app_display_schema.safe_parse(meta.get(ZCODE_MCP_NODE_REPL_CUA_APP_META_KEY)) if meta is not None else None
    return parsed.data if parsed is not None and getattr(parsed, "success", False) else None


def _create_node_repl_display(tool_name: str, output: Any) -> Optional[Any]:
    if tool_name != "js" and tool_name != "mcp__node_repl__js":
        return None
    if not is_record(output):
        return None

    candidates = [
        * (output["images"] if isinstance(output.get("images"), list) else []),
        * (output["content"] if isinstance(output.get("content"), list) else []),
    ]
    images: list[dict[str, str]] = []
    truncated = False

    for candidate in candidates:
        if not is_record(candidate):
            continue
        mime_type = candidate.get("mimeType")
        encoded = candidate.get("base64", candidate.get("data"))
        if (
            not isinstance(mime_type, str)
            or not isinstance(encoded, str)
            or not _is_image_mime(mime_type)
        ):
            continue
        base64 = encoded[encoded.find(",") + 1:] if encoded.startswith("data:") else encoded
        if len(base64) == 0 or _b64_len(base64) > MAX_NODE_REPL_DISPLAY_IMAGE_BASE64_BYTES:
            truncated = True
            continue
        if len(images) >= MAX_NODE_REPL_DISPLAY_IMAGES:
            truncated = True
            continue
        images.append({"base64": base64, "mimeType": mime_type})

    # 纯动作 cell（点击、输入）没有截图，但仍要把 App 身份投影给工具卡的 leading icon；
    # 因此不能再以「有图」作为产出 display 的唯一条件。
    app = _read_node_repl_cua_app(output)
    if len(images) == 0 and app is None:
        return None
    payload: dict[str, Any] = {"kind": "node_repl_images"}
    if len(images) > 0:
        payload["images"] = images
    if app is not None:
        payload["app"] = app
    if truncated:
        payload["truncated"] = True
    return payload


def _compact_task_stop_display_message(output: dict[str, Any]) -> str:
    if output.get("command") is None:
        return output.get("message", "")
    # TaskStop 的标准成功文案会把 command 再拼进括号；display 已有
    # 独立 command 字段，结果行只保留停止结论。
    standard_message = f"Successfully stopped task: {output.get('task_id')} ({output.get('command')})"
    return output.get("message") if output.get("message") == standard_message else output.get("message", "")


def _is_diff_hunk(value: Any) -> bool:
    if not is_record(value):
        return False
    return (
        isinstance(value.get("oldStart"), int)
        and isinstance(value.get("oldLines"), int)
        and isinstance(value.get("newStart"), int)
        and isinstance(value.get("newLines"), int)
        and isinstance(value.get("lines"), list)
        and all(isinstance(line, str) for line in value["lines"])
    )


def _bound_diff_hunks(hunks: list[Any]) -> tuple[list[Any], bool]:
    bounded: list[Any] = []
    remaining_lines = MAX_DISPLAY_DIFF_LINES
    truncated = len(hunks) > MAX_DISPLAY_DIFF_HUNKS

    for hunk in hunks[:MAX_DISPLAY_DIFF_HUNKS]:
        if remaining_lines <= 0:
            truncated = True
            break
        lines = hunk["lines"][:remaining_lines]
        bounded.append({**hunk, "lines": lines})
        remaining_lines -= len(lines)
        if len(lines) < len(hunk["lines"]):
            truncated = True
            break

    return bounded, truncated


def _b64_len(value: str) -> int:
    # PORT: TS ``Buffer.byteLength(item.data, "base64")``。近似：base64 解码后字节数。
    try:
        import base64

        return len(base64.b64decode(value, validate=False))
    except Exception:  # noqa: BLE001
        return len(value.encode("utf-8"))


def _is_image_mime(mime_type: str) -> bool:
    import re

    return bool(re.match(r"^image/[a-z0-9.+-]+$", mime_type, re.IGNORECASE))


def _safe(value: Any) -> Any:
    # PORT: 兜底 schema（``safeParse`` 形态）。``success`` 恒真、``data`` 为原值。
    class _R:
        success = True
        data = value

    return _R()
