"""Fetch the model list of a provider from its Base URL.

Two conventions are probed, in order:
* OpenAI-compatible  — ``GET {base}/models`` (official ``…/v1`` base, most
  gateways such as One API / LiteLLM / vLLM, OpenRouter, …).
* Anthropic native  — ``GET {base}/v1/models`` with ``x-api-key`` +
  ``anthropic-version`` headers.

Whichever endpoint answers 200 first wins; its ``data[].id`` entries become
the remote model candidates shown in the Web settings UI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

__all__ = [
    "RemoteModelsResult",
    "ModelsFetchError",
    "fetch_remote_models",
    "default_base_url",
]

PROBE_TIMEOUT_S = 12.0

#: Default Base URLs used when the user leaves the field empty.
DEFAULT_BASE_URLS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openAI": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "openRouter": "https://openrouter.ai/api/v1",
    "xAI": "https://api.x.ai/v1",
}

_ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class RemoteModelsResult:
    models: list[str]
    source: str  # final URL that answered 200


class ModelsFetchError(Exception):
    """User-facing error while probing a Base URL."""


def default_base_url(provider_type: str) -> str:
    return DEFAULT_BASE_URLS.get(provider_type, "")


def _candidate_urls(provider_type: str, base_url: str) -> list[str]:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ModelsFetchError("请先填写 Base URL(官方地址或代理网关地址)。")
    out: list[str] = []

    def add(url: str) -> None:
        if url not in out:
            out.append(url)

    if base.endswith("/v1"):
        # official OpenAI / gateways that embed the version in the path
        add(f"{base}/models")
        host = base[: -3].rstrip("/")
        add(f"{host}/models")
    else:
        add(f"{base}/models")
        add(f"{base}/v1/models")
    if provider_type == "anthropic":
        # native Anthropic shape, prefer /v1/models when not already covered
        add(f"{base}/v1/models")
        add(f"{base}/models")
    return out


def _is_loopback(url: str) -> bool:
    """Loopback/private addresses are reached directly (no system proxy) —
    that is where self-hosted gateways live; public API hosts should honour
    the user's proxy environment instead."""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "::1"):
        return True
    return host.startswith("127.") or host == "0.0.0.0"


def _auth_rounds(provider_type: str, api_key: str) -> list[dict[str, str]]:
    """Header sets to try: native first, bearer fallback for anthropic."""
    if provider_type == "anthropic":
        return [
            {"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION},
            {"Authorization": f"Bearer {api_key}"},
        ]
    return [{"Authorization": f"Bearer {api_key}"}]


def _parse_models(text: str) -> list[str]:
    """Extract model ids from common list shapes:
    ``{"data":[{"id": …}]}`` (OpenAI/Anthropic), ``{"models":[…] }`` or a
    bare array of strings/objects."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:  # pragma: no cover - defensive
        raise ModelsFetchError(f"响应不是有效 JSON: {e}") from None
    entries: list[Any] = []
    if isinstance(payload, dict):
        for key in ("data", "models", "items"):
            arr = payload.get(key)
            if isinstance(arr, list):
                entries = arr
                break
    elif isinstance(payload, list):
        entries = payload
    ids: list[str] = []
    for item in entries:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
        elif isinstance(item, str) and item.strip():
            ids.append(item.strip())
    # de-duplicate preserving order
    return list(dict.fromkeys(ids))


async def fetch_remote_models(
    provider_type: str, base_url: str, api_key: str
) -> RemoteModelsResult:
    """Probe candidate model-list endpoints; return the first successful one.

    Raises :class:`ModelsFetchError` with a readable summary when every probe
    fails (network error, auth, unsupported endpoint…).
    """
    urls = _candidate_urls(provider_type, base_url)
    rounds = _auth_rounds(provider_type, api_key)
    errors: list[str] = []
    try:
        # loopback gateways: connect directly; public hosts: honour proxy env
        async with httpx.AsyncClient(
            timeout=PROBE_TIMEOUT_S,
            follow_redirects=True,
            trust_env=not _is_loopback(urls[0]),
        ) as client:
            for url in urls:
                for headers in rounds:
                    try:
                        resp = await client.get(url, headers=headers)
                    except httpx.HTTPError as e:
                        errors.append(f"{url} → 网络错误({type(e).__name__})")
                        continue
                    if resp.status_code == 200:
                        try:
                            models = _parse_models(resp.text)
                        except ModelsFetchError as e:
                            errors.append(f"{url} → {e}")
                            continue
                        if models:
                            return RemoteModelsResult(models=models, source=url)
                        errors.append(f"{url} → 返回 200 但未找到模型列表")
                        continue
                    if resp.status_code in (401, 403):
                        errors.append(f"{url} → {resp.status_code} 认证失败(Key 无效或无权限)")
                    elif resp.status_code == 404:
                        errors.append(f"{url} → 404(该地址不支持模型列表接口)")
                    else:
                        errors.append(f"{url} → HTTP {resp.status_code}")
    except httpx.HTTPError as e:  # pragma: no cover - defensive
        raise ModelsFetchError(f"请求失败: {e}") from None
    detail = "\n".join(errors[:8])
    raise ModelsFetchError(
        "无法从该地址获取模型列表:\n" + detail
        if detail
        else "无法从该地址获取模型列表。"
    )
