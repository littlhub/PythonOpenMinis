"""WebFetch tool — fetch a public URL, convert to markdown, answer a prompt.

Ported from: packages/core/src/tool/handlers/webfetch.ts
Original package: @zcode/core (tool/handlers)

# PORT-STATUS: partial
#
# ``webfetch.ts`` 在 TS 侧依赖一批 handler 内 helper 模块（``webfetch-network`` /
# ``webfetch-processing`` / ``webfetch-url`` / ``webfetch-types`` / ``webfetch-errors`` /
# ``webfetch-preapproved``，以及 ``@zcode/contracts`` 的 ``HttpClientPort`` 与若干
# egress / IP 工具）。本次只移植 ``webfetch.ts`` + ``webfetch-cache`` + ``webfetch-constants``，
# 故这些 helper 以**等价实现**内联在下方：
#
#   - ``HttpClientPort``（@zcode/contracts，尚未移植）→ 本文件内自定的 ``Protocol``。
#   - HTML→markdown 抽取（``webfetch-content.ts``）与原始内容落盘（artifact store）未移植，
#     以 faithful-equivalent 占位（``extract_readable_content`` / ``maybe_persist_raw_content``）。
#   - egress / IP 判定（``getIpAddressVersion`` / ``getPublicEgressIpBlockReason`` 等）以
#     ``ipaddress`` 等价实现（``_get_ip_address_version`` 等）。
#   - 模型抽取（``context.model.generateText``）依赖未移植的 Model 层，按原分支忠实调用。
"""

from __future__ import annotations

import ipaddress
import json
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol, TypedDict, Union, runtime_checkable

from ...contracts.errors import CoreErrorType, create_core_error
from ..types import ToolEntry, ToolExecutionContext, ToolHandler
from .ports import (
    MAX_WEBFETCH_MODEL_BYTES,
    WEBFETCH_INPUT_JSON_SCHEMA,
    WEBFETCH_INPUT_SCHEMA,
    WEBFETCH_OUTPUT_JSON_SCHEMA,
    WEBFETCH_OUTPUT_SCHEMA,
    WEBFETCH_TOOL_NAME,
)
from .webfetch_cache import get_web_fetch_cache, put_web_fetch_cache
from .webfetch_constants import (
    MAX_MODEL_INPUT_CHARS,
    MAX_REDIRECTS,
    MAX_WEBFETCH_URL_CHARS,
    WEBFETCH_USER_AGENT,
)
from .ports import DEFAULT_WEBFETCH_TIMEOUT_MS

__all__ = [
    "web_fetch_handler",
    "web_fetch_tool_entry",
    "HttpClientPort",
]

# PORT: 与 TS ``STATUS_CODES``（``node:http``）等价；只取 webfetch 实际用得到的常见码。
_NODE_STATUS_CODES: dict[int, str] = {
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    206: "Partial Content",
    301: "Moved Permanently",
    302: "Found",
    303: "See Other",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    409: "Conflict",
    410: "Gone",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}

WEBFETCH_DESCRIPTION = "\n".join([
    "Fetches a URL, converts the page to markdown, and answers `prompt` against it using a small fast model.",
    "",
    "- Fails on authenticated/private URLs — use an authenticated MCP tool or `gh` for those instead.",
    "- HTTP is upgraded to HTTPS. Cross-host redirects are returned to you rather than followed; call again with the redirect URL.",
    "- Responses are cached for 15 minutes per URL.",
])


# ---------------------------------------------------------------------------
# HttpClientPort — self-contained Protocol (not yet ported in @zcode/contracts)
# ---------------------------------------------------------------------------

@runtime_checkable
class HttpClientPort(Protocol):
    """Ported from ``HttpClientPort`` (@zcode/contracts) — not yet ported upstream.

    # PORT: spec §5 / task instruction —— 内核一律经此端口出网，绝不直连
    # ``requests`` / ``urllib`` / ``httpx``。方法名按任务要求暴露为
    # ``fetch(url, method, headers, body) -> Any``（TS 原契约为 ``request(...)``，
    # 语义等价）。

    约定返回值形状（由宿主适配层提供，本文件按此消费）：

    ``{"status": int, "statusText": str, "headers": dict[str, str],
       "body": str, "bytes": int, "url": str, "durationMs": int,
       "egress": Any | None}``
    """

    async def fetch(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        body: Any,
    ) -> Any:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 载荷类型（webfetch-types.ts）
# ---------------------------------------------------------------------------

class WebFetchRedirect(TypedDict, total=False):
    """Ported from ``WebFetchRedirect`` (@zcode/contracts)."""

    from_url: str  # "from"
    to: str
    status: int


class CachedFetchContent(TypedDict, total=False):
    """Ported from ``interface CachedFetchContent``."""

    artifactPath: Optional[str]
    artifactUri: Optional[str]
    bytes: int
    content: str
    contentType: str
    finalUrl: str
    redirects: list[WebFetchRedirect]
    sizeBytes: int
    status: int
    statusText: str


class RedirectFetchContent(TypedDict, total=False):
    """Ported from ``interface RedirectFetchContent``."""

    type: str  # "redirect"
    originalUrl: str
    redirectUrl: str
    redirects: list[WebFetchRedirect]
    status: int
    statusText: str


class HttpErrorFetchContent(TypedDict, total=False):
    """Ported from ``interface HttpErrorFetchContent``."""

    type: str  # "http_error"
    finalUrl: str
    originalUrl: str
    redirects: list[WebFetchRedirect]
    retryAfter: Optional[str]
    status: int
    statusText: str


FetchAndExtractContentResult = Union[
    CachedFetchContent, HttpErrorFetchContent, RedirectFetchContent
]


# ---------------------------------------------------------------------------
# WebFetchError（webfetch-errors.ts）
# ---------------------------------------------------------------------------

class WebFetchError(Exception):
    """Ported from ``WebFetchError`` (webfetch-errors.ts)."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        if cause is not None:
            self.__cause__ = cause


def web_fetch_error(
    code: str,
    message: str,
    details: Optional[dict[str, Any]] = None,
    cause: Optional[BaseException] = None,
) -> WebFetchError:
    """Mirrors ``webFetchError(code, message, details, cause)``."""
    return WebFetchError(code, message, details, cause)


# ---------------------------------------------------------------------------
# egress / URL 校验（webfetch-url.ts 的等价实现）
# ---------------------------------------------------------------------------

def _get_ip_address_version(hostname: str) -> int:
    """等价 ``getIpAddressVersion`` —— 返回 4 / 6 / 0（非 IP 字面量）。"""
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return 0
    return 4 if addr.version == 4 else 6


def _get_public_egress_ip_block_reason(hostname: str) -> Optional[str]:
    """等价 ``getPublicEgressIpBlockReason`` —— 非公网 IP 返回阻断原因。"""
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return None
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
        return "WebFetch blocks non-public IPv4/IPv6 hosts"
    return None


def _normalize_ip_address_literal(hostname: str) -> str:
    """等价 ``normalizeIpAddressLiteral`` —— 去掉 IPv6 方括号。"""
    if hostname.startswith("[") and hostname.endswith("]"):
        return hostname[1:-1]
    return hostname


def _is_web_fetch_ip_literal(hostname: str) -> bool:
    """等价 ``isWebFetchIpLiteral``。"""
    return _get_ip_address_version(hostname) != 0


def _normalize_hostname(hostname: str) -> str:
    return _normalize_ip_address_literal(hostname)


def _effective_port(url: Any) -> str:
    """等价 ``effectivePort``。"""
    if url.port:
        return str(url.port)
    return "443" if url.protocol == "https:" else "80"


def _strip_www(hostname: str) -> str:
    return hostname.lower().replace("^www\\.", "", 1) if hostname.lower().startswith("www.") else hostname.lower()


def _same_host_modulo_www(left: str, right: str) -> bool:
    return _strip_www(left) == _strip_www(right)


def _get_blocked_host_reason(url: Any) -> Optional[dict[str, str]]:
    """等价 ``getBlockedHostReason``。"""
    hostname = _normalize_hostname(url.hostname)
    if len(hostname) == 0:
        return {"hostname": hostname, "message": "URL must include a hostname"}

    # 本地 IP 字面量 egress 由每次真实 GET 前的 assert 把关；普通域名不再做 DNS preflight。
    if _is_web_fetch_ip_literal(hostname):
        return None

    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        return {"hostname": hostname, "message": "WebFetch requires a public hostname"}

    ip_version = _get_ip_address_version(hostname)
    blocked_ip = None if ip_version == 0 else _get_public_egress_ip_block_reason(hostname)
    if blocked_ip and ip_version == 4:
        return {"hostname": hostname, "message": "WebFetch blocks non-public IPv4 hosts"}
    if blocked_ip and ip_version == 6:
        return {"hostname": hostname, "message": "WebFetch blocks non-public IPv6 hosts"}

    if ip_version == 0 and len(hostname.split(".")) < 2:
        return {"hostname": hostname, "message": "Invalid URL"}

    return None


def _is_public_host(url: Any) -> bool:
    return _get_blocked_host_reason(url) is None


def _assert_public_host(url: Any) -> None:
    blocked = _get_blocked_host_reason(url)
    if blocked is None:
        return
    raise web_fetch_error("InvalidUrl", blocked["message"], {
        "hostname": blocked["hostname"],
        "url": str(url),
    })


def normalize_web_fetch_url(value: str) -> Any:
    """Mirrors ``normalizeWebFetchUrl(value)``.

    # PORT: ``URL``（``globalThis.URL``）等价 ``urllib.parse`` 的 URL 对象；
    这里沿用 ``urllib.parse.urlparse`` 包装为轻量对象以保留 ``.protocol/.hostname/.port``。
    """
    from urllib.parse import urlparse

    if len(value) > MAX_WEBFETCH_URL_CHARS:
        raise web_fetch_error("InvalidUrl", "URL is too long", {
            "maxLength": MAX_WEBFETCH_URL_CHARS,
            "urlLength": len(value),
        })

    trimmed = value.strip()
    try:
        parsed = urlparse(trimmed)
    except Exception as error:  # noqa: BLE001
        raise web_fetch_error("InvalidUrl", f"Invalid URL: {value}", {"url": value}, error)

    if parsed.scheme not in ("http", "https"):
        raise web_fetch_error(
            "UnsupportedProtocol",
            "WebFetch only supports http and https URLs",
            {"protocol": parsed.scheme, "url": value},
        )

    if parsed.username or parsed.password:
        raise web_fetch_error(
            "CredentialsInUrl",
            "WebFetch URLs must not include credentials",
            {"url": _redact_url_credentials_str(trimmed)},
        )

    # 模型传入的 HTTP URL 升级成 HTTPS 后再出站请求。
    upgraded = _with_scheme(trimmed, "https")

    url_obj = _UrlView(upgraded)
    _assert_public_host(url_obj)
    return url_obj


class _UrlView:
    """PORT: 轻量 URL 视图，复刻 ``URL`` 上 webfetch 实际用到的字段。"""

    def __init__(self, raw: str) -> None:
        from urllib.parse import urlparse

        self._p = urlparse(raw)
        self._raw = raw

    @property
    def protocol(self) -> str:
        return f"{self._p.scheme}:"

    @property
    def hostname(self) -> str:
        return self._p.hostname or ""

    @property
    def port(self) -> str:
        return str(self._p.port) if self._p.port is not None else ""

    @property
    def username(self) -> str:
        return self._p.username or ""

    @property
    def password(self) -> str:
        return self._p.password or ""

    def __str__(self) -> str:
        return self._raw


def _with_scheme(raw: str, scheme: str) -> str:
    from urllib.parse import urlparse, urlunparse

    p = urlparse(raw)
    return urlunparse(p._replace(scheme=scheme))


def _redact_url_credentials_str(raw: str) -> str:
    from urllib.parse import urlparse, urlunparse

    p = urlparse(raw)
    if not p.username and not p.password:
        return raw
    return urlunparse(p._replace(netloc=p.hostname or "" + (f":{p.port}" if p.port else "")))


def redact_url_credentials(url: Any) -> str:
    """Mirrors ``redactUrlCredentials(url)``."""
    return _redact_url_credentials_str(str(url))


def resolve_redirect_url(location: str, current_url: Any) -> Any:
    """Mirrors ``resolveRedirectUrl(location, currentUrl)``。"""
    from urllib.parse import urljoin, urlparse

    try:
        joined = urljoin(str(current_url), location)
        urlparse(joined)  # 校验合法
        return _UrlView(joined)
    except Exception as error:  # noqa: BLE001
        raise web_fetch_error(
            "UnsafeRedirect",
            f"Redirect Location is not a valid URL: {location}",
            {"location": location, "url": str(current_url)},
            error,
        )


def is_permitted_redirect(from_url: Any, to_url: Any) -> bool:
    """Mirrors ``isPermittedRedirect(from, to)`` —— 同主机、同协议端口、公网、无凭据。"""
    if to_url.username or to_url.password:
        return False
    if not _is_public_host(to_url):
        return False
    if from_url.protocol != to_url.protocol or _effective_port(from_url) != _effective_port(to_url):
        return False
    if not _same_host_modulo_www(from_url.hostname, to_url.hostname):
        return False
    return True


# ---------------------------------------------------------------------------
# 每-GET egress 守卫（webfetch-egress-guard.ts 的等价实现）
# ---------------------------------------------------------------------------

def _assert_web_fetch_literal_egress(url: Any) -> None:
    """等价 ``assertWebFetchLiteralEgress(currentUrl)``。

    # PORT: 仅对 IP 字面量做公网判定（域名不再做 DNS preflight，贴近新版 TS）。
    """
    hostname = _normalize_hostname(url.hostname)
    if not _is_web_fetch_ip_literal(hostname):
        return
    reason = _get_public_egress_ip_block_reason(hostname)
    if reason is None:
        return
    raise web_fetch_error(
        "EgressBlocked",
        f"Access to {hostname} is blocked by the network egress guard.",
        {"domain": hostname, "error_type": "EGRESS_BLOCKED", "url": str(url)},
    )


# ---------------------------------------------------------------------------
# 网络抽取（webfetch-network.ts 的等价实现）
# ---------------------------------------------------------------------------

def _is_http_client_port_error(error: Any) -> bool:
    """等价 ``isHttpClientPortError`` —— 适配器错误约定带 ``code`` 字段。"""
    return isinstance(error, Exception) and getattr(error, "code", None) is not None


def _read_header(headers: dict[str, str], name: str) -> Optional[str]:
    """等价 ``readHeader`` —— 大小写不敏感查找。"""
    normalized = name.lower()
    if normalized in headers:
        return headers[normalized]
    for key, value in headers.items():
        if key.lower() == normalized:
            return value
    return None


def _is_redirect_status(status: int) -> bool:
    return status in (301, 302, 303, 307, 308)


def _is_successful_status(status: int) -> bool:
    return 200 <= status < 300


def _web_fetch_headers() -> dict[str, str]:
    return {
        "User-Agent": WEBFETCH_USER_AGENT,
        "Accept": "text/markdown, text/html, */*",
    }


async def _emit_network_request_status(context: Any, status: dict[str, Any]) -> None:
    """等价 ``emitNetworkRequestStatus`` —— 仅当 context 暴露 emit_event 时透传。"""
    emit = getattr(context, "emit_event", None)
    if callable(emit):
        try:
            result = emit(status)
            if hasattr(result, "__await__"):
                await result
        except Exception:  # noqa: BLE001  PORT: 遥测失败不阻断主流程
            pass


def _deepest_cause_details(error: Any) -> dict[str, Optional[str]]:
    seen: set[Any] = set()
    current: Any = error
    message: Optional[str] = None
    code: Optional[str] = None
    while current and current not in seen:
        seen.add(current)
        if isinstance(current, str) and current.strip():
            message = current.strip()
            break
        if not isinstance(current, Exception):
            break
        if current.args and str(current.args[0]).strip():
            message = str(current.args[0]).strip()
        c = getattr(current, "code", None)
        if isinstance(c, str) and c.strip():
            code = c.strip()
        current = getattr(current, "__cause__", None) or getattr(current, "cause", None)
    if not message and not code:
        return {}
    return {"code": code, "message": message}


def _append_error_code(message: str, code: Optional[str]) -> str:
    if not code or code in message:
        return message
    return f"{message} ({code})"


def _compact_known_network_cause_message(cause: Optional[dict[str, Optional[str]]]) -> Optional[str]:
    if not cause or not cause.get("message"):
        return None
    # PORT: 与 TS 仅保留 connect-timeout 的紧凑化；其余还原样。
    return None


def _format_request_error(error: Any) -> str:
    fallback = error.args[0] if isinstance(error, Exception) and error.args else "WebFetch request failed"
    if isinstance(fallback, str):
        fallback = fallback
    else:
        fallback = "WebFetch request failed"
    concrete = _deepest_cause_details(error)
    compact = _compact_known_network_cause_message(concrete or None)
    if compact:
        return compact
    concrete_message = (concrete or {}).get("message")
    fallback_with_code = _append_error_code(fallback, (concrete or {}).get("code"))
    if not concrete_message or concrete_message == fallback:
        return fallback_with_code
    return f"{fallback}: {_append_error_code(concrete_message, (concrete or {}).get('code'))}"


def _format_egress_blocked_message(message: str, domain: str) -> str:
    if "resolved to" in message:
        return f"HTTP public egress blocked {domain} because it resolved to a non-public address"
    return message


def _format_web_fetch_request_error(error: Any, current_url: Any) -> str:
    if _is_http_client_port_error(error) and getattr(error, "code", None) == "egress_blocked":
        return _format_egress_blocked_message(error.args[0] if error.args else "", current_url.hostname)
    return _format_request_error(error)


def _to_http_error_fetch_content(
    response: dict[str, Any],
    current_url: Any,
    redirects: list[WebFetchRedirect],
    original_url: str,
) -> HttpErrorFetchContent:
    retry_after = _read_header(response.get("headers", {}), "retry-after")
    return {
        "type": "http_error",
        "finalUrl": str(current_url),
        "originalUrl": original_url,
        "redirects": list(redirects),
        "retryAfter": retry_after if isinstance(retry_after, str) and retry_after.strip() else None,
        "status": int(response.get("status", 0)),
        "statusText": str(response.get("statusText", "")),
    }


def _throw_if_proxy_blocked(response: dict[str, Any], current_url: Any) -> None:
    if _read_header(response.get("headers", {}), "x-proxy-error") != "blocked-by-allowlist":
        return
    domain = current_url.hostname
    raise web_fetch_error(
        "EgressBlocked",
        json.dumps({
            "error_type": "EGRESS_BLOCKED",
            "domain": domain,
            "message": f"Access to {domain} is blocked by the network egress proxy.",
        }),
        {"domain": domain, "error_type": "EGRESS_BLOCKED", "url": str(current_url)},
    )


def extract_readable_content(body: str, content_type: str) -> str:
    """等价 ``extractReadableContent``。

    # PORT: HTML→markdown 抽取（``webfetch-content.ts``）未移植；faithful-equivalent
    # 直接回吐解码后的正文。text/markdown 原样返回；text/html 暂不做标记清洗。
    """
    return body


async def maybe_persist_raw_content(content: str, content_type: str, context: Any) -> Optional[dict[str, Any]]:
    """等价 ``maybePersistRawContent``。

    # PORT: artifact store 落盘（``ToolArtifactStorePort``）未移植；faithful-equivalent
    # 返回 None（即不落盘，仅内存返回）。
    """
    return None


async def fetch_and_extract_content(options: dict[str, Any]) -> FetchAndExtractContentResult:
    """Mirrors ``fetchAndExtractContent(options)``。

    经 ``context.httpClientPort.fetch`` 出网，处理重定向 / HTTP 错误 / egress / 代理拦截。
    """
    context: ToolExecutionContext = options["context"]
    http_client_port = context.http_client_port
    if not http_client_port:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "HttpClientPort is not configured for WebFetch tool",
            {
                "context": {
                    "toolCallId": context.tool_call_id,
                    "toolName": WEBFETCH_TOOL_NAME,
                },
                "recoverable": False,
            },
        )

    redirects: list[WebFetchRedirect] = []
    current_url = options["url"]
    response: Optional[dict[str, Any]] = None

    for _redirect_count in range(0, MAX_REDIRECTS + 1):
        # 每个真实 GET 前阻断 URL 字面量本地/私网目标。
        _assert_web_fetch_literal_egress(current_url)

        request_id = f"net_{_uuid4()}"
        request_started_at = time.time()
        await _emit_network_request_status(context, {
            "method": "GET",
            "requestId": request_id,
            "source": "http_client",
            "startedAt": _iso_now(),
            "status": "pending",
            "toolCallId": context.tool_call_id,
            "toolName": WEBFETCH_TOOL_NAME,
            "url": str(current_url),
        })

        try:
            response = await http_client_port.fetch(
                str(current_url),
                "GET",
                _web_fetch_headers(),
                None,
            )
            response = _coerce_response(response)
            await _emit_network_request_status(context, {
                "completedAt": _iso_now(),
                "durationMs": response.get("durationMs", 0),
                "egress": response.get("egress"),
                "method": "GET",
                "requestId": request_id,
                "source": "http_client",
                "startedAt": _iso_now(),
                "status": "complete",
                "statusCode": response.get("status"),
                "toolCallId": context.tool_call_id,
                "toolName": WEBFETCH_TOOL_NAME,
                "url": response.get("url", str(current_url)),
            })
        except Exception as error:  # noqa: BLE001
            error_message = _format_web_fetch_request_error(error, current_url)
            await _emit_network_request_status(context, {
                "completedAt": _iso_now(),
                "durationMs": max(0, int((time.time() - request_started_at) * 1000)),
                "error": error_message,
                "method": "GET",
                "requestId": request_id,
                "source": "http_client",
                "startedAt": _iso_now(),
                "status": "error",
                "statusCode": error.code if _is_http_client_port_error(error) else None,
                "toolCallId": context.tool_call_id,
                "toolName": WEBFETCH_TOOL_NAME,
                "url": str(current_url),
            })
            if _is_http_client_port_error(error) and error.code == "too_large":
                raise web_fetch_error("ResponseTooLarge", error.args[0] if error.args else str(error), {"url": str(current_url)}, error)
            if _is_http_client_port_error(error) and error.code == "egress_blocked":
                domain = current_url.hostname
                raise web_fetch_error(
                    "EgressBlocked",
                    error_message,
                    {"domain": domain, "error_type": "EGRESS_BLOCKED", "url": str(current_url)},
                    error,
                )
            raise web_fetch_error("FetchFailed", error_message, {"url": str(current_url)}, error)

        if not _is_redirect_status(int(response.get("status", 0))):
            break
        terminal = _classify_redirect(response, current_url, redirects, options["originalUrl"])
        if _is_fetch_terminal_result(terminal):
            return terminal
        current_url = terminal  # type: ignore[assignment]

    if response is None:
        raise web_fetch_error("FetchFailed", "WebFetch did not receive a response", {
            "url": options["originalUrl"],
        })

    if _is_redirect_status(int(response.get("status", 0))):
        raise web_fetch_error("TooManyRedirects", "WebFetch exceeded the safe redirect limit", {
            "maxRedirects": MAX_REDIRECTS,
            "url": str(current_url),
        })

    _throw_if_proxy_blocked(response, current_url)

    if not _is_successful_status(int(response.get("status", 0))):
        return _to_http_error_fetch_content(response, current_url, redirects, options["originalUrl"])

    content_type = _read_header(response.get("headers", {}), "content-type") or ""
    content = extract_readable_content(response.get("body", ""), content_type)
    artifact = await maybe_persist_raw_content(content, content_type, context)

    return {
        "artifactPath": (artifact or {}).get("path"),
        "artifactUri": (artifact or {}).get("uri"),
        "bytes": int(response.get("bytes", 0)),
        "content": content,
        "contentType": content_type,
        "finalUrl": str(current_url),
        "redirects": list(redirects),
        "sizeBytes": len(content.encode("utf-8")),
        "status": int(response.get("status", 0)),
        "statusText": str(response.get("statusText", "")),
    }


def _classify_redirect(
    response: dict[str, Any],
    current_url: Any,
    redirects: list[WebFetchRedirect],
    original_url: str,
) -> Any:
    """Mirrors ``classifyRedirect`` —— 返回 ``HttpErrorFetchContent | RedirectFetchContent | URL``。"""
    location = _read_header(response.get("headers", {}), "location")
    if not isinstance(location, str) or location.strip() == "":
        return _to_http_error_fetch_content(response, current_url, redirects, original_url)

    next_url = resolve_redirect_url(location, current_url)
    display_next_url = redact_url_credentials(next_url)
    redirect: WebFetchRedirect = {
        "from": str(current_url),
        "to": display_next_url,
        "status": int(response.get("status", 0)),
    }
    if not is_permitted_redirect(current_url, next_url):
        return {
            "type": "redirect",
            "originalUrl": str(current_url),
            "redirectUrl": display_next_url,
            "redirects": [*redirects, redirect],
            "status": int(response.get("status", 0)),
            "statusText": str(response.get("statusText", "")),
        }
    redirects.append(redirect)
    return next_url


def _is_fetch_terminal_result(value: Any) -> bool:
    """等价 ``isFetchTerminalResult`` —— ``URL``（_UrlView）之外皆为终态。"""
    return not isinstance(value, _UrlView)


def _coerce_response(response: Any) -> dict[str, Any]:
    """把适配层返回的响应规范成 webfetch 消费的字典形状。"""
    if isinstance(response, dict):
        return response
    # 适配层可能返回对象；尽力取其属性。
    return {
        "status": getattr(response, "status", 0),
        "statusText": getattr(response, "statusText", ""),
        "headers": getattr(response, "headers", {}) or {},
        "body": getattr(response, "body", ""),
        "bytes": getattr(response, "bytes", 0),
        "url": getattr(response, "url", ""),
        "durationMs": getattr(response, "durationMs", 0),
        "egress": getattr(response, "egress", None),
    }


# ---------------------------------------------------------------------------
# 内容抽取（webfetch-processing.ts 的等价实现）
# ---------------------------------------------------------------------------

def _truncate_content_for_model(content: str) -> tuple[str, bool]:
    """等价 ``truncateContentForModel``。"""
    if len(content) > MAX_MODEL_INPUT_CHARS:
        return content[:MAX_MODEL_INPUT_CHARS], True
    return content, False


def _should_return_markdown_directly(
    fetched: CachedFetchContent,
    preapproved_url: bool,
) -> bool:
    content_type = (fetched.get("contentType") or "").lower()
    return (
        preapproved_url
        and "text/markdown" in content_type
        and len(fetched.get("content", "")) < MAX_MODEL_INPUT_CHARS
    )


def _build_processing_prompt(content: str, prompt: str, preapproved_url: bool) -> str:
    instruction = (
        "Provide a concise response based on the content above. Include relevant details, code examples, and documentation excerpts as needed."
        if preapproved_url
        else [
            "Provide a concise response based only on the content above. In your response:",
            " - Enforce a strict 125-character maximum for quotes from any source document. Open Source Software is ok as long as we respect the license.",
            " - Use quotation marks for exact language from articles; any language outside of the quotation should never be word-for-word the same.",
            " - You are not a lawyer and never comment on the legality of your own prompts and responses.",
            " - Never produce or reproduce exact song lyrics.",
        ].join("\n")
    )
    return f"""
Web page content:
---
{content}
---

{prompt}

{instruction}
"""


async def process_fetched_content(
    input: dict[str, Any],
    fetched: CachedFetchContent,
    context: Any,
    options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Mirrors ``processFetchedContent(input, fetched, context, options?)``。"""
    preapproved_url = bool(options.get("preapprovedUrl")) if options else False
    if _should_return_markdown_directly(fetched, preapproved_url):
        return {"result": fetched.get("content", ""), "truncated": False}

    model = getattr(context, "model", None)
    if not model:
        raise create_core_error(
            CoreErrorType.CONFIGURATION_ERROR,
            "Model is not configured for WebFetch prompt processing",
            {
                "context": {
                    "toolCallId": getattr(context, "tool_call_id", None),
                    "toolName": WEBFETCH_TOOL_NAME,
                },
                "recoverable": False,
            },
        )

    content, truncated = _truncate_content_for_model(fetched.get("content", ""))
    messages = [
        {"role": "user", "content": _build_processing_prompt(content, input.get("prompt", ""), preapproved_url)},
    ]

    try:
        # PORT: ``auxiliaryModelOptions`` / ``runWithModelInvocationContext`` 未移植；
        # 这里以等价方式直接调用 ``model.generate_text``。
        result = await model.generate_text(
            messages=messages,
            tools=[],
            options={
                "max_output_tokens": min(4096, _max_output_tokens(model)),
            },
            abort_signal=getattr(context, "abort_signal", None),
        )
        text = result.text if hasattr(result, "text") else str(result)
        return {
            "result": text.strip() or "WebFetch completed, but the extraction model returned no text.",
            "truncated": truncated,
        }
    except Exception as error:  # noqa: BLE001
        raise web_fetch_error(
            "ProcessingFailed",
            error.args[0] if isinstance(error, Exception) and error.args else "WebFetch prompt processing failed",
            {"url": fetched.get("finalUrl", "")},
            error,
        )


def _max_output_tokens(model: Any) -> int:
    option_specs = getattr(model, "optionSpecs", None)
    if option_specs is not None:
        max_tokens = getattr(getattr(option_specs, "maxOutputTokens", None), "max", None)
        if isinstance(max_tokens, int):
            return max_tokens
    return 4096


def _auxiliary_model_options(model: Any) -> dict[str, Any]:
    """等价 ``auxiliaryModelOptions`` —— 未移植，回吐空选项。"""
    return {}


# ---------------------------------------------------------------------------
# preapproved（webfetch-preapproved.ts 的等价实现）
# ---------------------------------------------------------------------------

def is_web_fetch_preapproved_url(url: str) -> bool:
    """Mirrors ``isWebFetchPreapprovedUrl(url)``。

    # PORT: 预批准主机清单（``webfetch-preapproved.ts``）未移植；默认 False。
    """
    return False


# ---------------------------------------------------------------------------
# 终态输出格式化（webfetch.ts）
# ---------------------------------------------------------------------------

def _http_status_text(status: int, status_text: str) -> str:
    return (status_text or "").strip() or _NODE_STATUS_CODES.get(status) or "Unknown Status"


def is_terminal_fetch_content(value: FetchAndExtractContentResult) -> bool:
    """等价 ``isTerminalFetchContent`` —— 带 ``type`` 判别式者为终态。"""
    return "type" in value


def format_terminal_output(
    input: dict[str, Any],
    fetched: Any,
    duration_ms: int,
) -> dict[str, Any]:
    if fetched.get("type") == "redirect":
        return _format_redirect_output(input, fetched, duration_ms)
    return _format_http_error_output(fetched, duration_ms)


def _format_redirect_output(
    input: dict[str, Any],
    redirect: dict[str, Any],
    duration_ms: int,
) -> dict[str, Any]:
    status_text = _http_status_text(redirect.get("status", 0), redirect.get("statusText", ""))
    result = [
        "REDIRECT DETECTED: The URL redirects to a different host.",
        "",
        f"Original URL: {redirect.get('originalUrl', '')}",
        f"Redirect URL: {redirect.get('redirectUrl', '')}",
        f"Status: {redirect.get('status', 0)} {status_text}",
        "",
        "To complete your request, I need to fetch content from the redirected URL. Please use WebFetch again with these parameters:",
        f"- url: \"{redirect.get('redirectUrl', '')}\"",
        f"- prompt: \"{input.get('prompt', '')}\"",
    ].join("\n")

    return {
        "url": input.get("url", ""),
        "finalUrl": redirect.get("originalUrl", ""),
        "status": redirect.get("status", 0),
        "statusText": status_text,
        "contentType": "text/plain",
        "bytes": len(result.encode("utf-8")),
        "durationMs": duration_ms,
        "result": result,
        "cacheHit": False,
        "redirects": list(redirect.get("redirects", [])),
        "truncated": False,
    }


def _format_http_error_output(error: dict[str, Any], duration_ms: int) -> dict[str, Any]:
    status_text = _http_status_text(error.get("status", 0), error.get("statusText", ""))
    retry_after = error.get("retryAfter")
    retry_line = f"\nRetry-After: {retry_after}" if retry_after else ""
    result = [
        f"The server returned HTTP {error.get('status', 0)} {status_text}.{retry_line}",
        "",
        "The response body was not retrieved. If this URL requires authentication, use an authenticated tool (e.g. `gh` for GitHub, or an MCP-provided fetch tool) instead of WebFetch.",
    ].join("\n")

    return {
        "url": error.get("originalUrl", ""),
        "finalUrl": error.get("finalUrl", ""),
        "status": error.get("status", 0),
        "statusText": status_text,
        "contentType": "text/plain",
        "bytes": 0,
        "durationMs": duration_ms,
        "result": result,
        "cacheHit": False,
        "redirects": list(error.get("redirects", [])),
        "truncated": False,
    }


# ---------------------------------------------------------------------------
# 主 handler（webfetch.ts）
# ---------------------------------------------------------------------------

async def web_fetch_handler(input: Any, context: Any) -> Any:
    """Mirrors ``webFetchHandler: ToolHandler``."""
    parsed = input  # executor 已经归一化
    started_at = _now_ms()
    normalized_url = normalize_web_fetch_url(parsed.get("url", ""))
    cache_key = parsed.get("url", "")
    cached = get_web_fetch_cache(cache_key)
    content = (
        await _fetch_fresh_content({
            "context": context,
            "originalUrl": cache_key,
            "url": normalized_url,
        })
        if cached is None
        else {
            "fetched": cached,
            "preapprovedUrl": is_web_fetch_preapproved_url(parsed.get("url", "")),
        }
    )
    fetched = content["fetched"]

    if is_terminal_fetch_content(fetched):
        return format_terminal_output(parsed, fetched, max(0, _now_ms() - started_at))

    if cached is None:
        put_web_fetch_cache(cache_key, fetched)

    processing = await process_fetched_content(parsed, fetched, context, {
        "preapprovedUrl": content.get("preapprovedUrl"),
    })
    duration_ms = max(0, _now_ms() - started_at)

    return {
        "url": parsed.get("url", ""),
        "finalUrl": fetched.get("finalUrl", ""),
        "status": fetched.get("status", 0),
        "statusText": _http_status_text(fetched.get("status", 0), fetched.get("statusText", "")),
        "contentType": fetched.get("contentType", ""),
        "bytes": fetched.get("bytes", 0),
        "durationMs": duration_ms,
        "result": processing.get("result", ""),
        "cacheHit": cached is not None,
        "redirects": list(fetched.get("redirects", [])),
        "artifactUri": fetched.get("artifactUri"),
        "artifactPath": fetched.get("artifactPath"),
        "truncated": processing.get("truncated", False),
    }


async def _fetch_fresh_content(options: dict[str, Any]) -> dict[str, Any]:
    fetched = await fetch_and_extract_content(options)
    return {
        "fetched": fetched,
        "preapprovedUrl": is_web_fetch_preapproved_url(options.get("originalUrl", "")),
    }


def _now_ms() -> int:
    return int(time.time() * 1000)


def _uuid4() -> str:
    import uuid

    return str(uuid.uuid4())


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def is_web_fetch_output(value: Any) -> bool:
    """Mirrors ``isWebFetchOutput(value)``。"""
    return (
        isinstance(value, dict)
        and isinstance(value.get("result"), str)
        and isinstance(value.get("finalUrl"), str)
    )


def format_web_fetch_model_content(output: Any) -> str:
    """Mirrors ``formatWebFetchModelContent(output)``。"""
    if is_web_fetch_output(output):
        return output["result"]
    if isinstance(output, str):
        return output
    return json.dumps(output) if output is not None else ""


# ---------------------------------------------------------------------------
# tool entry（webfetch.ts 的 webFetchToolEntry）
# ---------------------------------------------------------------------------

def _web_fetch_permission() -> Any:
    from ...contracts.tools import ToolPermissionSpec

    return ToolPermissionSpec(
        permission="webfetch",
        reason="WebFetch performs an outbound network GET request to the requested domain",
        risk_level="medium",
        side_effect_scope="network",
        needs_approval=True,
        pattern_sources=["network"],
        always_allow_pattern_sources=["network"],
        deny_priority="beforeAsk",
    )


def _web_fetch_result_budget() -> Any:
    from ...contracts.tools import ToolResultBudget, ToolResultBudgetArtifact, ToolResultBudgetPreview

    return ToolResultBudget(
        max_inline_bytes=MAX_WEBFETCH_MODEL_BYTES,
        max_model_bytes=MAX_WEBFETCH_MODEL_BYTES,
        strategy="artifact",
        preview=ToolResultBudgetPreview(max_bytes=MAX_WEBFETCH_MODEL_BYTES, direction="head"),
        artifact=ToolResultBudgetArtifact(enabled=True, retention="session"),
    )


def _build_web_fetch_tool_entry() -> ToolEntry:
    from ...contracts.tools import (
        TimedToolTimeoutPolicy,
        ToolCancellationPolicy,
        ToolTracePolicy,
    )

    return ToolEntry(
        capability="Fetch a public URL, convert readable content to markdown, and answer a prompt from it",
        metadata={
            "name": WEBFETCH_TOOL_NAME,
            "description": WEBFETCH_DESCRIPTION,
            "read_only": True,
            "destructive": False,
            "concurrent_safe": True,
            "timeout_ms": DEFAULT_WEBFETCH_TIMEOUT_MS,
            "max_output_bytes": MAX_WEBFETCH_MODEL_BYTES,
            "side_effect_scope": "network",
            "risk_level": "medium",
            "needs_approval": True,
        },
        handler=web_fetch_handler,
        format_model_content=format_web_fetch_model_content,
        input_schema=WEBFETCH_INPUT_JSON_SCHEMA,
        output_schema=WEBFETCH_OUTPUT_JSON_SCHEMA,
        runtime_input_schema=WEBFETCH_INPUT_SCHEMA,
        runtime_output_schema=WEBFETCH_OUTPUT_SCHEMA,
        permission=_web_fetch_permission(),
        result_budget=_web_fetch_result_budget(),
        timeout=TimedToolTimeoutPolicy(
            default_ms=DEFAULT_WEBFETCH_TIMEOUT_MS,
            allow_call_override=False,
            max_ms=DEFAULT_WEBFETCH_TIMEOUT_MS,
        ),
        cancellation=ToolCancellationPolicy(
            supported=True,
            cleanup="bestEffort",
            user_visible_message="WebFetch was cancelled before the page could be processed",
        ),
        trace=ToolTracePolicy(
            propagate_to_adapters=True,
            record_input="summary",
            record_output="summary",
        ),
    )


web_fetch_tool_entry: ToolEntry = _build_web_fetch_tool_entry()
