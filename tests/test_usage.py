"""Token usage aggregation.

Ported from: src/android/app/src/test/java/com/openminis/app/ui/settings/UsageAttributionTest.kt

## Why the attribution rule is worth pinning

The original bug was not a wrong sum — the token counts were always right. It
was a wrong LABEL: usage was attributed by joining ``sessions.model_id``, a
mutable column with no history, so a session's entire past silently moved to
whatever model it currently pointed at. Nothing on screen distinguished a
re-attributed total from a real one, which is why it survived two rounds of
fixes to the same screen.

So the classification is the part that must not regress. Collapsing any two of
these states back into one restores exactly the ambiguity that hid the bug.
"""

from __future__ import annotations

import json

import pytest

from openminis.data.db.usage_record import UsageRecord
from openminis.server.usage_api import (
    Attribution,
    _read_usage,
    build_usage_stats,
    classify_attribution,
    provider_display_name,
)


def _record(
    *,
    model_id: str | None = "grok-4.5",
    display: str | None = None,
    provider: str | None = None,
    has_snapshot: bool = True,
    usage: dict | None = None,
    created_at: int = 1_760_000_000_000,
    session_id: str = "s1",
) -> UsageRecord:
    return UsageRecord(
        model_id=model_id,
        model_display_name=display,
        provider_type=provider,
        has_snapshot=has_snapshot,
        token_usage=json.dumps(usage or {"inputTokens": 100, "outputTokens": 20}),
        created_at=created_at,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# A: measured
# ---------------------------------------------------------------------------
def test_snapshot_that_resolves_in_config_is_measured():
    assert (
        classify_attribution(model_id="grok-4.5", has_snapshot=True, resolves_in_config=True)
        is Attribution.MEASURED
    )


# ---------------------------------------------------------------------------
# B: estimated (pre-migration rows)
# ---------------------------------------------------------------------------
def test_row_without_snapshot_is_estimated_even_when_id_resolves():
    """The fallback path: ``COALESCE(m.model_id, s.model_id)`` yields the
    session's CURRENT model for old rows — usable, but only as an estimate,
    because that column may have been rewritten any number of times since."""
    assert (
        classify_attribution(model_id="grok-4.5", has_snapshot=False, resolves_in_config=True)
        is Attribution.ESTIMATED
    )


def test_row_without_snapshot_whose_id_does_not_resolve_is_still_estimated():
    assert (
        classify_attribution(model_id="ghost", has_snapshot=False, resolves_in_config=False)
        is Attribution.ESTIMATED
    )


# ---------------------------------------------------------------------------
# C: orphaned rows
# ---------------------------------------------------------------------------
def test_null_model_id_is_an_unknown_session():
    """GH#168 kept these rows rather than dropping them (the tokens were really
    billed). They have no session to infer from, so they must land in their own
    bucket instead of borrowing another model's identity."""
    assert (
        classify_attribution(model_id=None, has_snapshot=False, resolves_in_config=False)
        is Attribution.UNKNOWN_SESSION
    )


# ---------------------------------------------------------------------------
# D: the case this feature exists for
# ---------------------------------------------------------------------------
def test_snapshot_whose_provider_was_deleted_is_still_measured():
    """Provider deleted after the fact: before the snapshot a CUSTOM model had
    no remaining source for its name, so it degraded to a bare id in an
    "Unknown" bucket. With a snapshot it stays a real, named row."""
    assert (
        classify_attribution(
            model_id="my-private-model", has_snapshot=True, resolves_in_config=False
        )
        is Attribution.MEASURED_REMOVED
    )


def test_orphaned_row_that_has_a_snapshot_is_still_measured():
    """Branch order matters: ``COALESCE`` returns the snapshot id even when the
    session row is gone, so the UNKNOWN_SESSION arm must be skipped. Testing
    session-existence before ``has_snapshot`` would downgrade good data."""
    assert (
        classify_attribution(model_id="grok-4.5", has_snapshot=True, resolves_in_config=True)
        is Attribution.MEASURED
    )
    assert (
        classify_attribution(model_id="grok-4.5", has_snapshot=True, resolves_in_config=False)
        is Attribution.MEASURED_REMOVED
    )


def test_the_four_states_are_mutually_exclusive():
    seen = {
        classify_attribution("m", has_snapshot=True, resolves_in_config=True),
        classify_attribution("m", has_snapshot=False, resolves_in_config=True),
        classify_attribution(None, has_snapshot=False, resolves_in_config=False),
        classify_attribution("m", has_snapshot=True, resolves_in_config=False),
    }
    assert len(seen) == 4


# ---------------------------------------------------------------------------
# provider grouping
# ---------------------------------------------------------------------------
def test_provider_raw_value_maps_to_its_display_name():
    assert provider_display_name("openAI") == "OpenAI"
    assert provider_display_name("gemini") == "Google Gemini"
    assert provider_display_name("anthropic") == "Anthropic"


def test_unknown_provider_raw_value_degrades_to_the_raw_string():
    assert provider_display_name("someFutureProvider") == "someFutureProvider"


# ---------------------------------------------------------------------------
# usage blob parsing
# ---------------------------------------------------------------------------
def test_read_usage_accepts_both_key_spellings():
    """Providers report ``cacheCreationInputTokens``; the UI-facing JSON writes
    ``cacheCreationTokens``. Both must be understood."""
    a = _read_usage(json.dumps({"inputTokens": 10, "outputTokens": 2,
                                "cacheCreationTokens": 3, "cacheReadTokens": 4}))
    b = _read_usage(json.dumps({"cacheCreationInputTokens": 3, "cacheReadInputTokens": 4,
                                "input_tokens": 10, "output_tokens": 2}))
    assert a == b == (10, 2, 3, 4)


def test_read_usage_tolerates_garbage():
    assert _read_usage("not json") == (0, 0, 0, 0)
    assert _read_usage("[]") == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------
def test_stats_sum_across_records_of_one_model():
    lookup = {"grok-4.5": ("Grok 4.5", "xAI (Grok)")}
    stats = build_usage_stats(
        [
            _record(usage={"inputTokens": 100, "outputTokens": 10}, session_id="s1"),
            _record(usage={"inputTokens": 50, "outputTokens": 5}, session_id="s2"),
        ],
        lookup,
    )
    model = stats["groups"][0]["models"][0]
    assert model["inputTokens"] == 150
    assert model["outputTokens"] == 15
    assert model["sessions"] == 2
    assert model["attribution"] == Attribution.MEASURED.value
    assert stats["grandTotal"]["totalInput"] == 150
    assert stats["grandTotal"]["formattedOutput"] == "15"


def test_estimated_rows_never_merge_into_measured_ones():
    """The core of the original bug: same model id, different trust level, must
    stay two buckets so a guess never inherits a measurement's credibility."""
    lookup = {"grok-4.5": ("Grok 4.5", "xAI (Grok)")}
    stats = build_usage_stats(
        [
            _record(has_snapshot=True, usage={"inputTokens": 100}),
            _record(has_snapshot=False, usage={"inputTokens": 7}),
        ],
        lookup,
    )
    models = stats["groups"][0]["models"]
    assert len(models) == 2
    assert {m["attribution"] for m in models} == {
        Attribution.MEASURED.value,
        Attribution.ESTIMATED.value,
    }
    # …but the grand total still counts both: the tokens were really billed.
    assert stats["grandTotal"]["totalInput"] == 107


def test_orphan_rows_land_in_the_unknown_provider_group():
    stats = build_usage_stats([_record(model_id=None, has_snapshot=False)], {})
    assert stats["groups"][0]["name"] == "Unknown"
    model = stats["groups"][0]["models"][0]
    assert model["modelId"] == "(unknown model)"
    assert model["attribution"] == Attribution.UNKNOWN_SESSION.value


def test_snapshot_strings_survive_a_deleted_provider():
    """A removed provider is the case the snapshot exists for: the stored
    display name and raw provider type are the only remaining source."""
    rec = _record(
        model_id="my-private-model",
        display="My Private Model",
        provider="openAI",
        has_snapshot=True,
    )
    stats = build_usage_stats([rec], {})
    model = stats["groups"][0]["models"][0]
    assert model["displayName"] == "My Private Model"
    assert model["provider"] == "OpenAI"
    assert model["attribution"] == Attribution.MEASURED_REMOVED.value


def test_provider_groups_follow_the_pinned_order():
    lookup = {
        "gpt-5.2": ("GPT-5.2", "OpenAI"),
        "claude-sonnet-5": ("Claude Sonnet 5", "Anthropic"),
    }
    stats = build_usage_stats(
        [
            _record(model_id="claude-sonnet-5", usage={"inputTokens": 10}),
            _record(model_id="gpt-5.2", usage={"inputTokens": 10}),
        ],
        lookup,
    )
    assert [g["name"] for g in stats["groups"]] == ["OpenAI", "Anthropic"]


def test_models_within_a_group_sort_by_input_desc():
    lookup = {"a": ("A", "OpenAI"), "b": ("B", "OpenAI")}
    stats = build_usage_stats(
        [
            _record(model_id="a", usage={"inputTokens": 1}),
            _record(model_id="b", usage={"inputTokens": 99}),
        ],
        lookup,
    )
    assert [m["modelId"] for m in stats["groups"][0]["models"]] == ["b", "a"]


def test_cache_hit_rate_needs_cache_reads():
    """No cache reads → no rate, rather than a misleading 0.0%."""
    lookup = {"a": ("A", "OpenAI")}
    plain = build_usage_stats([_record(model_id="a", usage={"inputTokens": 100})], lookup)
    assert plain["grandTotal"]["cacheHitRate"] is None

    cached = build_usage_stats(
        [_record(model_id="a", usage={"inputTokens": 50, "cacheReadTokens": 50})], lookup
    )
    assert cached["grandTotal"]["cacheHitRate"] == 50.0
    # cache reads count toward total input
    assert cached["grandTotal"]["totalInput"] == 100


def test_active_days_counts_distinct_dates():
    lookup = {"a": ("A", "OpenAI")}
    day = 86_400_000
    stats = build_usage_stats(
        [
            _record(model_id="a", created_at=1_760_000_000_000),
            _record(model_id="a", created_at=1_760_000_000_000 + 3_600_000),
            _record(model_id="a", created_at=1_760_000_000_000 + day),
        ],
        lookup,
    )
    assert stats["groups"][0]["models"][0]["activeDays"] == 2


def test_model_lookup_includes_custom_models_from_provider_instances(monkeypatch):
    """A custom model id exists only in the live config, never in the catalog.

    ``provider_instances()`` returns a LIST (the id-keyed map normalised to
    rows). Reading it as a dict — ``.values()`` on a list — raised and got
    swallowed, leaving the fallback empty so every custom model was labelled
    MEASURED_REMOVED even though it was the model actually in use.
    """
    from openminis.server import usage_api
    from openminis.settings.store import SettingsStore

    monkeypatch.setattr(
        SettingsStore,
        "provider_instances",
        lambda self: [
            {
                "id": "openAI",
                "type": "openAI",
                "model": "agnes-2.5-flash",
                "modelHints": ["agnes-2.5-pro"],
            }
        ],
    )

    lookup = usage_api.model_lookup_table()
    assert lookup["agnes-2.5-flash"] == ("agnes-2.5-flash", "OpenAI")
    assert lookup["agnes-2.5-pro"] == ("agnes-2.5-pro", "OpenAI")

    stats = build_usage_stats(
        [_record(model_id="agnes-2.5-flash", display="agnes-2.5-flash", provider="openAI")],
        lookup,
    )
    assert stats["groups"][0]["models"][0]["attribution"] == Attribution.MEASURED.value


def test_empty_records_produce_an_empty_but_valid_payload():
    stats = build_usage_stats([], {})
    assert stats["groups"] == []
    assert stats["bucketCount"] == 0
    assert stats["grandTotal"]["totalInput"] == 0
    assert stats["grandTotal"]["cacheHitRate"] is None


# ---------------------------------------------------------------------------
# persistence wiring — the snapshot columns must actually be written
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_append_turn_persists_the_attribution_snapshot(isolated_chat_db):
    """End-to-end: a turn written with a snapshot must come back out of
    ``all_usage_records`` classified as MEASURED, not as an estimate."""
    from openminis.server import chat_store

    await chat_store.ensure_db()
    session = await chat_store.create_session()
    await chat_store.append_turn(
        session.id,
        "assistant",
        "hello",
        model_label="grok-4.5",
        token_usage=json.dumps({"inputTokens": 11, "outputTokens": 3}),
        model_id="grok-4.5",
        model_display_name="Grok 4.5",
        provider_type="xAI",
        provider_instance_id="inst-1",
    )

    async with chat_store._get_db().session() as s:
        from openminis.data.db.chat_dao import ChatDao

        records = await ChatDao(s).all_usage_records()

    row = next(r for r in records if r.session_id == session.id)
    assert row.model_id == "grok-4.5"
    assert row.model_display_name == "Grok 4.5"
    assert row.provider_type == "xAI"
    assert row.has_snapshot is True
    assert json.loads(row.token_usage)["inputTokens"] == 11

    stats = build_usage_stats(records, {"grok-4.5": ("Grok 4.5", "xAI (Grok)")})
    model = next(
        m for g in stats["groups"] for m in g["models"] if m["modelId"] == "grok-4.5"
    )
    assert model["attribution"] == Attribution.MEASURED.value


@pytest.mark.asyncio
async def test_a_turn_without_usage_is_not_a_billed_row(isolated_chat_db):
    """Only rows carrying ``token_usage`` are counted — a plain user turn must
    not add a phantom zero-token model to the page."""
    from openminis.server import chat_store

    await chat_store.ensure_db()
    session = await chat_store.create_session()
    await chat_store.append_turn(session.id, "user", "hi", model_label="grok-4.5")

    async with chat_store._get_db().session() as s:
        from openminis.data.db.chat_dao import ChatDao

        records = await ChatDao(s).all_usage_records()

    assert [r for r in records if r.session_id == session.id] == []
