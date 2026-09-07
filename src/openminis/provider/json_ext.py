"""Safe JSON helpers mirroring Android JSONObject quirks.

Ported from: src/android/app/src/main/java/com/openminis/app/provider/JsonExt.kt
Original package: com.openminis.app.provider
"""

from __future__ import annotations

from typing import Any

__all__ = ["safe_opt_string"]


def safe_opt_string(obj: dict, key: str, fallback: str = "") -> str:
    """Safe optString that handles the Android JSONObject quirk where
    optString("key", "") returns the literal string "null" when the key
    exists but its value is JSON null.

    Returns ``fallback`` in both cases:
    - key does not exist
    - key exists but value is None (JSON null)
    """
    if key not in obj:
        return fallback
    val = obj[key]
    if val is None:
        return fallback
    return str(val)
