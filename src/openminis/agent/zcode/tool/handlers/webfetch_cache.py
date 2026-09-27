"""WebFetch in-memory cache — per-URL TTL + LRU-style byte budget.

Ported from: packages/core/src/tool/handlers/webfetch-cache.ts
Original package: @zcode/core (tool/handlers)

# PORT: ``CACHE_MAX_BYTES`` / ``CACHE_TTL_MS`` 来自 ``webfetch-constants.ts``；
# 这里从本目录的 ``webfetch_constants`` 取用。缓存值是 ``CachedFetchContent``
# （见 ``webfetch.py``），外加 ``expiresAt`` 字段；对外只回吐 ``CachedFetchContent`` 形状。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .webfetch_constants import CACHE_MAX_BYTES, CACHE_TTL_MS

__all__ = [
    "get_web_fetch_cache",
    "put_web_fetch_cache",
    "clear_web_fetch_cache_for_tests",
]


@dataclass(slots=True)
class _CacheEntry:
    """``CacheEntry = CachedFetchContent & { expiresAt }``。

    # PORT: TS 里 ``CacheEntry extends CachedFetchContent``；这里把缓存值整体拍平，
    # ``expiresAt`` 为额外字段，回吐时只暴露 ``CachedFetchContent`` 部分。
    """

    artifact_path: Optional[str] = None
    artifact_uri: Optional[str] = None
    bytes: int = 0
    content: str = ""
    content_type: str = ""
    final_url: str = ""
    redirects: list[Any] = field(default_factory=list)
    size_bytes: int = 0
    status: int = 0
    status_text: str = ""
    expires_at: int = 0


# PORT: ``new Map<string, CacheEntry>()``；Python dict 保持插入顺序，可被 LRU 复用。
_fetch_cache: dict[str, _CacheEntry] = {}
_cache_bytes = 0


def clear_web_fetch_cache_for_tests() -> None:
    """Mirrors ``clearWebFetchCacheForTests()``."""
    _fetch_cache.clear()
    global _cache_bytes
    _cache_bytes = 0


def get_web_fetch_cache(key: str) -> Optional[dict[str, Any]]:
    """Mirrors ``getWebFetchCache(key)``.

    命中且在 TTL 内则移到队尾（LRU 续命）并回吐 ``CachedFetchContent``；过期则删除并扣减字节。
    """
    entry = _fetch_cache.get(key)
    if entry is None:
        return None
    if entry.expires_at <= _now_ms():
        _fetch_cache.pop(key, None)
        global _cache_bytes
        _cache_bytes -= entry.size_bytes
        return None
    # PORT: 命中续命 —— 删后重插，维持 LRU 顺序（等价 TS 的 delete+set）。
    _fetch_cache.pop(key, None)
    _fetch_cache[key] = entry
    return _entry_to_cached_content(entry)


def put_web_fetch_cache(key: str, value: dict[str, Any]) -> None:
    """Mirrors ``putWebFetchCache(key, value)``.

    单条超过 ``CACHE_MAX_BYTES`` 不入缓存；否则写入并续 TTL，随后按 TTL + 字节预算裁剪。
    """
    size_bytes = int(value.get("sizeBytes", 0) or 0)
    if size_bytes > CACHE_MAX_BYTES:
        return

    previous = _fetch_cache.get(key)
    global _cache_bytes
    if previous is not None:
        _cache_bytes -= previous.size_bytes

    entry = _cached_content_to_entry(key, value)
    _fetch_cache[key] = entry
    _cache_bytes += entry.size_bytes
    _prune_cache()


def _prune_cache() -> None:
    """Mirrors ``pruneCache()`` — 先清过期，再按字节上限淘汰最旧条目。"""
    global _cache_bytes
    now = _now_ms()
    for k in list(_fetch_cache.keys()):
        entry = _fetch_cache[k]
        if entry.expires_at > now:
            continue
        _fetch_cache.pop(k, None)
        _cache_bytes -= entry.size_bytes

    for k in list(_fetch_cache.keys()):
        if _cache_bytes <= CACHE_MAX_BYTES:
            break
        entry = _fetch_cache.pop(k, None)
        if entry is not None:
            _cache_bytes -= entry.size_bytes


def _now_ms() -> int:
    """``Date.now()`` → 毫秒时间戳。"""
    return int(time.time() * 1000)


def _entry_to_cached_content(entry: _CacheEntry) -> dict[str, Any]:
    """回吐 ``CachedFetchContent`` 形状（不含 ``expiresAt``）。"""
    return {
        "artifactPath": entry.artifact_path,
        "artifactUri": entry.artifact_uri,
        "bytes": entry.bytes,
        "content": entry.content,
        "contentType": entry.content_type,
        "finalUrl": entry.final_url,
        "redirects": entry.redirects,
        "sizeBytes": entry.size_bytes,
        "status": entry.status,
        "statusText": entry.status_text,
    }


def _cached_content_to_entry(key: str, value: dict[str, Any]) -> _CacheEntry:
    """``CachedFetchContent`` + 计算得到的 ``expiresAt`` → ``_CacheEntry``。"""
    return _CacheEntry(
        artifact_path=value.get("artifactPath"),
        artifact_uri=value.get("artifactUri"),
        bytes=int(value.get("bytes", 0) or 0),
        content=value.get("content", ""),
        content_type=value.get("contentType", ""),
        final_url=value.get("finalUrl", key),
        redirects=list(value.get("redirects", []) or []),
        size_bytes=int(value.get("sizeBytes", 0) or 0),
        status=int(value.get("status", 0) or 0),
        status_text=value.get("statusText", ""),
        expires_at=_now_ms() + CACHE_TTL_MS,
    )
