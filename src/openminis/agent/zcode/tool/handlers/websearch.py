"""WebSearch tool — provider-native web search driven by the model layer.

Ported from: packages/core/src/tool/handlers/websearch.ts
Original package: @zcode/core (tool/handlers)

# PORT-STATUS: partial
#
# ``websearch.ts`` 依赖整条未移植的 Model 层与 provider-native web search 契约：
#   - ``context.model.streamText`` / ``model.properties.supportsNativeWebSearch`` /
#     ``model.optionSpecs``（@zcode/contracts model，未移植）
#   - ``auxiliaryModelOptions``、``runWithModelInvocationContext``、
#     ``toWebSearchProviderNativeArgs``、``WEBSEARCH_PROVIDER_NATIVE_SPEC``、
#     ``WEBSEARCH_TOOL_CONTRACT``、``buildWebSearchOutput``、``webSearchTraceFromContext``
#     （分别来自 ``model/auxiliary-model-options`` / ``websearch-results`` /
#     ``websearch-support`` / @zcode/contracts，均未移植）
#
# 本文件按 1:1 还原 handler 结构与所有分支/判别，但对上述未移植能力的调用以
# **等价实现 / fail-closed 占位** 复刻（见各 ``# PORT:`` 注释）。运行时能否真正出网
# 取决于宿主注入的 ``context.model`` 是否实现 provider-native web search。
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from ...contracts.errors import CoreErrorType, create_core_error
from ..types import ToolEntry, ToolHandler
from .ports import (
    WEBSEARCH_INPUT_JSON_SCHEMA,
    WEBSEARCH_INPUT_SCHEMA,
    WEBSEARCH_OUTPUT_JSON_SCHEMA,
    WEBSEARCH_OUTPUT_SCHEMA,
    WEBSEARCH_TOOL_NAME,
)

__all__ = [
    "web_search_handler",
    "web_search_tool_entry",
]

PROVIDER_WEBSEARCH_TOOL_NAME = "web_search"
DEFAULT_TIMEOUT_MS = 60_000
DEFAULT_MAX_USES = 8

WEBSEARCH_MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# PORT: ``WEBSEARCH_PROVIDER_NATIVE_SPEC``（@zcode/contracts）未移植；以等价常量复刻
# provider-native web search 的基础描述（具体 args 由 ``to_web_search_provider_native_args`` 组装）。
WEBSEARCH_PROVIDER_NATIVE_SPEC: dict[str, Any] = {
    "logical_name": PROVIDER_WEBSEARCH_TOOL_NAME,
    "provider_tool_name": PROVIDER_WEBSEARCH_TOOL_NAME,
    "fallback": "disabled",
    "kind": "provider_native",
}


def build_web_search_provider_description(now: Optional[Any] = None) -> str:
    """Mirrors ``buildWebSearchProviderDescription(now)``。"""
    if now is None:
        now = time.localtime()
    current_month = f"{WEBSEARCH_MONTH_NAMES[now.tm_mon - 1]} {now.tm_year}"
    return "\n".join([
        "Search the web. Returns result blocks with titles and URLs. US-only.",
        "",
        f"- The current month is {current_month} — use this when searching for recent information.",
        "- `allowed_domains` / `blocked_domains` filter results.",
        '- After answering from results, end with a "Sources:" list of the URLs you used as markdown links.',
    ])


# PORT: WEBSEARCH_DESCRIPTION 用 getter 在 TS 侧每次读取时重新生成，避免长驻进程跨月后过期。
# Python 的 ``ToolMetadata.description`` 是普通字段，这里在工厂构造时生成一次（等价语义，仅月份刷新粒度不同）。
WEBSEARCH_DESCRIPTION = build_web_search_provider_description()


async def web_search_handler(input: Any, context: Any) -> Any:
    """Mirrors ``webSearchHandler: ToolHandler<WebSearchInput, WebSearchOutput>``。"""
    started_at = _now_ms()
    model = getattr(context, "model", None)

    if not model:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "Model is required for WebSearch",
            {
                "context": {
                    "toolCallId": getattr(context, "tool_call_id", None),
                    "toolName": WEBSEARCH_TOOL_NAME,
                },
                "recoverable": False,
            },
        )

    model_props = getattr(model, "properties", None)
    supports_native = bool(getattr(model_props, "supportsNativeWebSearch", False)) if model_props else False
    if not supports_native:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "Current model does not support native WebSearch",
            {
                "context": {
                    "toolCallId": getattr(context, "tool_call_id", None),
                    "toolName": WEBSEARCH_TOOL_NAME,
                },
                "recoverable": True,
            },
        )

    request: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": "You are an assistant for performing a web search tool use."},
            {"role": "user", "content": f"Perform a web search for the query: {input.get('query', '')}"},
        ],
        "tools": [create_provider_native_web_search_contract(input)],
        # BigModel 的 Anthropic 兼容端点会拒绝 named forced web_search tool_choice（1210）。
        # 这里保持自动选择，依靠单工具请求和 prompt 触发 provider-native 搜索。
        "options": {
            **_auxiliary_model_options(model),
            "max_output_tokens": min(4096, _max_output_tokens(model)),
        },
        "abort_signal": getattr(context, "abort_signal", None),
    }

    # BigModel Anthropic 兼容端点的非流式 JSON 会把 provider 内部 web_search 结果返回为
    # assistant-side 裸 tool_result，AI SDK 会在 schema 校验阶段抛 Invalid JSON response。
    # 走流式可复用现有 SSE compat。
    result = await _collect_web_search_stream_result({
        "events": _run_with_model_invocation_context(
            {
                "metadata": {
                    "traceId": getattr(context, "trace_id", None),
                    "sessionId": getattr(context, "session_id", None),
                    "turnId": getattr(context, "turn_id", None),
                    "toolCallId": getattr(context, "tool_call_id", None),
                    "toolName": WEBSEARCH_TOOL_NAME,
                    "querySource": "web_search_tool",
                },
                "modelRequestSessionType": "other",
                "modelCall": {"operation": "web_search"},
                # statusSink 不在这里设：执行器交出的 context.model 已带默认会话事件出口。
                "traceContext": _web_search_trace_from_context(context),
            },
            lambda: model.stream_text(request),
        ),
    })

    return _build_web_search_output(input, result, started_at)


def create_provider_native_web_search_contract(input: dict[str, Any]) -> dict[str, Any]:
    """Mirrors ``createProviderNativeWebSearchContract(input)``。"""
    return {
        "name": PROVIDER_WEBSEARCH_TOOL_NAME,
        "capability": "web_search",
        "description": "Provider-native web search used internally by the WebSearch tool",
        "executionMode": "providerNative",
        "providerNative": {
            **WEBSEARCH_PROVIDER_NATIVE_SPEC,
            "args": _to_web_search_provider_native_args(input, {"maxUses": DEFAULT_MAX_USES}),
        },
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        "outputSchema": {"type": "object"},
    }


def _to_web_search_provider_native_args(
    input: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    """等价 ``toWebSearchProviderNativeArgs(input, options)``。"""
    return {
        "query": input.get("query", ""),
        "allowed_domains": input.get("allowed_domains"),
        "blocked_domains": input.get("blocked_domains"),
        "max_uses": options.get("maxUses", DEFAULT_MAX_USES),
    }


async def _collect_web_search_stream_result(input: dict[str, Any]) -> dict[str, Any]:
    """Mirrors ``collectWebSearchStreamResult(input)``。"""
    text = ""
    finish_reason = "unknown"
    provider_metadata: Optional[dict[str, Any]] = None
    usage: dict[str, Any] = {}
    tool_calls: list[Any] = []

    events = input["events"]
    async for event in events:
        event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
        if event_type == "text_delta":
            delta = event.get("text") if isinstance(event, dict) else getattr(event, "text", "")
            text += delta
        elif event_type == "tool_call":
            tool_call = event.get("toolCall") if isinstance(event, dict) else getattr(event, "toolCall", None)
            tool_calls.append(tool_call)
        elif event_type == "finish":
            finish_reason = event.get("finishReason", "unknown") if isinstance(event, dict) else getattr(event, "finishReason", "unknown")
            usage = event.get("usage", {}) if isinstance(event, dict) else getattr(event, "usage", {})
            provider_metadata = event.get("providerMetadata") if isinstance(event, dict) else getattr(event, "providerMetadata", None)
        elif event_type == "error":
            error = event.get("error") if isinstance(event, dict) else getattr(event, "error", None)
            raise _normalize_stream_error(error)

    return {
        "finishReason": finish_reason,
        "providerMetadata": provider_metadata,
        "text": text,
        "toolCalls": tool_calls if tool_calls else None,
        "usage": usage,
    }


def _normalize_stream_error(error: Any) -> Any:
    """Mirrors ``normalizeStreamError(error)``。"""
    if isinstance(error, Exception):
        return error
    return create_core_error(
        CoreErrorType.MODEL_ERROR,
        "WebSearch stream failed",
        {"context": {"error": error}, "recoverable": True},
    )


def _run_with_model_invocation_context(metadata: dict[str, Any], thunk: Any) -> Any:
    """等价 ``runWithModelInvocationContext(metadata, thunk)``。

    # PORT: 真实的调用上下文包装（注入 trace / 会话事件出口）未移植；这里直接执行 thunk，
    # 语义上等价于「在该上下文里执行 model 调用」。
    """
    return thunk()


def _web_search_trace_from_context(context: Any) -> Any:
    """等价 ``webSearchTraceFromContext(context)``。

    # PORT: 未移植；回吐由 context 透出的 trace_context（缺省 None）。
    """
    return getattr(context, "trace_context", None)


def _auxiliary_model_options(model: Any) -> dict[str, Any]:
    """等价 ``auxiliaryModelOptions(model)`` —— 未移植，回吐空选项。"""
    return {}


def _max_output_tokens(model: Any) -> int:
    option_specs = getattr(model, "optionSpecs", None)
    if option_specs is not None:
        max_tokens = getattr(getattr(option_specs, "maxOutputTokens", None), "max", None)
        if isinstance(max_tokens, int):
            return max_tokens
    return 4096


# ---------------------------------------------------------------------------
# 结果组装（websearch-results.ts 的等价实现）
# ---------------------------------------------------------------------------

def _build_web_search_output(input: dict[str, Any], result: dict[str, Any], started_at: int) -> dict[str, Any]:
    """等价 ``buildWebSearchOutput(input, result, startedAt)``。

    # PORT: TS 从 provider metadata 的 web_search 块抽取结构化结果；未移植，这里
    # 在 provider_metadata 含 ``web_search.results`` 时如实采用，否则退回空列表。
    """
    results: list[dict[str, Any]] = []
    provider_metadata = result.get("providerMetadata") or {}
    web_search_meta = provider_metadata.get("web_search") if isinstance(provider_metadata, dict) else None
    if isinstance(web_search_meta, dict) and isinstance(web_search_meta.get("results"), list):
        results = list(web_search_meta["results"])

    return {
        "query": input.get("query", ""),
        "results": results,
        "durationMs": max(0, _now_ms() - started_at),
    }


def format_web_search_model_content(output: Any) -> str:
    """等价 ``formatWebSearchModelContent(output)``。"""
    if not isinstance(output, dict):
        return json.dumps(output) if output is not None else ""

    results = output.get("results") or []
    lines: list[str] = []
    sources: list[str] = []
    for item in results:
        title = item.get("title") or item.get("url") or "(untitled)"
        url = item.get("url") or ""
        snippet = item.get("snippet") or ""
        lines.append(f"- [{title}]({url})")
        if snippet:
            lines.append(f"  {snippet}")
        if url:
            sources.append(f"- [{url}]({url})")

    body = "\n".join(lines) if lines else "No web search results returned."
    sources_block = "\n\nSources:\n" + "\n".join(sources) if sources else ""
    return body + sources_block


# ---------------------------------------------------------------------------
# tool entry（websearch.ts 的 webSearchToolEntry）
# ---------------------------------------------------------------------------

def _build_web_search_tool_entry() -> ToolEntry:
    from ...contracts.tools import (
        ToolCancellationPolicy,
        ToolResultBudget,
        ToolResultBudgetArtifact,
        ToolResultBudgetPreview,
        ToolTracePolicy,
    )

    return ToolEntry(
        capability="Search the web using the model provider's native web search capability",
        metadata={
            "name": WEBSEARCH_TOOL_NAME,
            # 写死月份会导致模型可见的 WebSearch 描述过期；工厂构造时重新生成（见 WEBSEARCH_DESCRIPTION）。
            "description": WEBSEARCH_DESCRIPTION,
            "read_only": True,
            "destructive": False,
            "concurrent_safe": True,
            "timeout_ms": DEFAULT_TIMEOUT_MS,
            "max_output_bytes": 20_000,
            "side_effect_scope": "network",
            "risk_level": "low",
            "needs_approval": False,
        },
        handler=web_search_handler,
        format_model_content=format_web_search_model_content,
        input_schema=WEBSEARCH_INPUT_JSON_SCHEMA,
        output_schema=WEBSEARCH_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=WEBSEARCH_INPUT_SCHEMA,
        runtime_output_schema=WEBSEARCH_OUTPUT_SCHEMA,
        permission=_web_search_permission(),
        result_budget=ToolResultBudget(
            max_inline_bytes=20_000,
            max_model_bytes=20_000,
            strategy="artifact",
            preview=ToolResultBudgetPreview(max_bytes=20_000, direction="head"),
            artifact=ToolResultBudgetArtifact(enabled=True, retention="session"),
        ),
        timeout=_web_search_timeout(),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="bestEffort",
            user_visible_message="WebSearch was cancelled before results were returned",
        ),
        trace=ToolTracePolicy(
            propagate_to_adapters=True,
            record_input="summary",
            record_output="summary",
        ),
    )


def _web_search_permission() -> Any:
    from ...contracts.tools import ToolPermissionSpec

    return ToolPermissionSpec(
        permission="websearch",
        reason="WebSearch performs an outbound network search request via the model provider",
        risk_level="low",
        side_effect_scope="network",
        needs_approval=False,
        pattern_sources=["network"],
        always_allow_pattern_sources=["network"],
        deny_priority="beforeAsk",
    )


def _web_search_timeout() -> Any:
    from ...contracts.tools import TimedToolTimeoutPolicy

    return TimedToolTimeoutPolicy(
        default_ms=DEFAULT_TIMEOUT_MS,
        allow_call_override=False,
        max_ms=DEFAULT_TIMEOUT_MS,
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


web_search_tool_entry: ToolEntry = _build_web_search_tool_entry()
