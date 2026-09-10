"""Token usage statistics.

Ported from: ``com.openminis.app.ui.settings.UsageStatsScreen``

The Android screen was pure aggregation over ``ChatDao.allUsageRecords()``; the
rules that matter are the attribution states, because the page's honesty
depends on them. Rows written before the per-message snapshot columns existed
have no reliable model — they fall back to ``sessions.model_id``, which is one
mutable column rewritten on every model switch. Those rows are still counted
(the tokens really were billed) but bucketed separately and labelled as an
estimate rather than passed off as measured fact.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Iterable

from fastapi import APIRouter

from ..core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/usage", tags=["usage"])

UNKNOWN_MODEL_KEY = "(unknown model)"
UNKNOWN_PROVIDER = "Unknown"

#: Section order in the UI; anything else sorts after these.
PROVIDER_ORDER = ["OpenAI", "Anthropic", "Google Gemini", "Google", "Antigravity", "Unknown"]


class Attribution(str, Enum):
    """How trustworthy a row's model attribution is.

    These must stay separable — lumping them together is what made the original
    bug invisible: an estimated row looked exactly like a measured one, so
    nobody could tell a session's history had been re-attributed to whichever
    model it currently pointed at.
    """

    MEASURED = "MEASURED"
    #: No snapshot: guessed from the session's current model. May be wrong.
    ESTIMATED = "ESTIMATED"
    #: Orphan row — no snapshot and no session to guess from.
    UNKNOWN_SESSION = "UNKNOWN_SESSION"
    #: Snapshot present but the model has since been removed from the config.
    MEASURED_REMOVED = "MEASURED_REMOVED"


def classify_attribution(
    model_id: str | None,
    has_snapshot: bool,
    resolves_in_config: bool,
) -> Attribution:
    """Decide whether a number is presented as fact or as a guess."""
    if model_id is None:
        return Attribution.UNKNOWN_SESSION
    if not has_snapshot:
        return Attribution.ESTIMATED
    if not resolves_in_config:
        return Attribution.MEASURED_REMOVED
    return Attribution.MEASURED


def provider_display_name(raw_value: str) -> str:
    """Map a stored provider type back to its display name.

    Snapshots store the raw type (``openAI``) rather than the label
    (``OpenAI``) so grouping stays stable — labels are localized and
    historically inconsistent. An unrecognised raw value degrades to itself
    rather than vanishing.
    """
    from ..settings.catalog import provider_label

    return provider_label(raw_value)


def model_lookup_table() -> dict[str, tuple[str, str]]:
    """``model_id -> (display_name, provider_label)`` for every known model.

    Catalogued models first, then any custom id the user has selected on a
    provider instance — those are only discoverable from the live config.
    """
    from ..settings.catalog import MODEL_GROUPS, provider_label

    lookup: dict[str, tuple[str, str]] = {}
    for ptype, models in MODEL_GROUPS.items():
        label = provider_label(ptype)
        for mid, display in models:
            lookup.setdefault(mid, (display, label))
    try:
        from ..settings.store import SettingsStore

        # ``provider_instances()`` is a list (id-keyed map normalised to rows),
        # NOT a dict — iterating it as one silently produced an empty fallback
        # and mislabelled every custom model as "removed from config".
        for inst in SettingsStore.get().provider_instances():
            ptype = str(inst.get("type") or "")
            label = provider_label(ptype) if ptype else UNKNOWN_PROVIDER
            for mid in inst.get("modelHints") or []:
                lookup.setdefault(str(mid), (str(mid), label))
            cur = str(inst.get("model") or "").strip()
            if cur:
                lookup.setdefault(cur, (cur, label))
    except Exception:  # pragma: no cover - settings unreadable
        logger.debug("usage: provider instances unavailable", exc_info=True)
    return lookup


def _read_usage(blob: str) -> tuple[int, int, int, int]:
    """``(input, output, cache_creation, cache_read)`` from a usage JSON blob.

    Both key spellings are accepted: providers report
    ``cacheCreationInputTokens`` while the UI-facing JSON writes
    ``cacheCreationTokens``.
    """
    try:
        data = json.loads(blob)
    except Exception:
        return 0, 0, 0, 0
    if not isinstance(data, dict):
        return 0, 0, 0, 0

    def _int(*keys: str) -> int:
        for k in keys:
            v = data.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str) and v.strip().lstrip("-").isdigit():
                return int(v.strip())
        return 0

    return (
        _int("inputTokens", "input_tokens", "prompt_tokens"),
        _int("outputTokens", "output_tokens", "completion_tokens"),
        _int("cacheCreationTokens", "cacheCreationInputTokens", "cache_creation_input_tokens"),
        _int("cacheReadTokens", "cacheReadInputTokens", "cache_read_input_tokens"),
    )


def _format_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        k = n / 1000.0
        return f"{int(k)}k" if k == int(k) else f"{k:.1f}k"
    return str(n)


def build_usage_stats(
    records: Iterable[Any],
    lookup: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Aggregate usage rows into per-model stats grouped by provider.

    Pure function over :class:`UsageRecord`-like rows so the attribution rules
    can be tested without a database.
    """
    lookup = lookup if lookup is not None else model_lookup_table()
    stats: dict[str, dict[str, Any]] = {}

    for rec in records:
        inp, out, cache_cr, cache_rd = _read_usage(rec.token_usage or "")
        model_id = getattr(rec, "model_id", None)
        model_key = model_id or UNKNOWN_MODEL_KEY
        resolved = lookup.get(model_key)
        attribution = classify_attribution(
            model_id=model_id,
            has_snapshot=bool(getattr(rec, "has_snapshot", False)),
            resolves_in_config=resolved is not None,
        )

        # Prefer the snapshot's own strings: for a removed provider they are
        # the ONLY remaining source of a human-readable name.
        display = (
            (getattr(rec, "model_display_name", None) or "").strip()
            or (resolved[0] if resolved else "")
            or model_key
        )
        raw_ptype = (getattr(rec, "provider_type", None) or "").strip()
        provider = (
            provider_display_name(raw_ptype)
            if raw_ptype
            else (resolved[1] if resolved else UNKNOWN_PROVIDER)
        )

        # Bucket key includes the state so an estimated row never merges into a
        # measured one and quietly inherits its credibility.
        bucket = f"{model_key}#{attribution.value}"
        row = stats.get(bucket)
        if row is None:
            row = stats[bucket] = {
                "modelId": model_key,
                "displayName": display,
                "provider": provider,
                "attribution": attribution.value,
                "inputTokens": 0,
                "outputTokens": 0,
                "cacheCreationTokens": 0,
                "cacheReadTokens": 0,
                "_days": set(),
                "_sessions": set(),
            }
        row["inputTokens"] += inp
        row["outputTokens"] += out
        row["cacheCreationTokens"] += cache_cr
        row["cacheReadTokens"] += cache_rd
        created_at = int(getattr(rec, "created_at", 0) or 0)
        if created_at:
            row["_days"].add(datetime.fromtimestamp(created_at / 1000).strftime("%Y-%m-%d"))
        sid = getattr(rec, "session_id", None)
        if sid:
            row["_sessions"].add(sid)

    models: list[dict[str, Any]] = []
    for row in stats.values():
        days = len(row.pop("_days"))
        sessions = len(row.pop("_sessions"))
        total_in = row["inputTokens"] + row["cacheReadTokens"] + row["cacheCreationTokens"]
        row["totalInput"] = total_in
        row["formattedInput"] = _format_count(total_in)
        row["formattedOutput"] = _format_count(row["outputTokens"])
        row["sessions"] = sessions
        row["activeDays"] = days
        models.append(row)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for m in models:
        grouped.setdefault(m["provider"], []).append(m)

    def _order(name: str) -> int:
        idx = PROVIDER_ORDER.index(name) if name in PROVIDER_ORDER else len(PROVIDER_ORDER)
        return idx

    groups = [
        {"name": name, "models": sorted(items, key=lambda m: -m["totalInput"])}
        for name, items in sorted(grouped.items(), key=lambda kv: _order(kv[0]))
    ]

    total_input = sum(m["totalInput"] for m in models)
    out_tokens = sum(m["outputTokens"] for m in models)
    cache_read = sum(m["cacheReadTokens"] for m in models)
    cache_cr = sum(m["cacheCreationTokens"] for m in models)
    hit_rate = (
        round((cache_read / total_input) * 100, 1) if total_input > 0 and cache_read > 0 else None
    )

    return {
        "grandTotal": {
            "totalInput": total_input,
            "outputTokens": out_tokens,
            "cacheReadTokens": cache_read,
            "cacheCreationTokens": cache_cr,
            "cacheHitRate": hit_rate,
            "formattedInput": _format_count(total_input),
            "formattedOutput": _format_count(out_tokens),
            "formattedCacheRead": _format_count(cache_read),
            "formattedCacheCreation": _format_count(cache_cr),
        },
        "groups": groups,
        # "bucket" = one (model, attribution) pair. A model seen both measured
        # and estimated deliberately counts twice.
        "bucketCount": len(models),
    }


@router.get("")
@router.get("/")
async def usage_stats() -> dict[str, Any]:
    """Aggregated token usage, grouped by provider then model."""
    from ..data.db.chat_dao import ChatDao

    try:
        from .chat_store import _get_db, ensure_db

        await ensure_db()
        async with _get_db().session() as s:
            records = await ChatDao(s).all_usage_records()
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("usage query failed")
        return {"error": f"用量统计读取失败: {e}", "groups": [], "grandTotal": {}}

    return build_usage_stats(records)
