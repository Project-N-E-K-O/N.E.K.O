from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import memory.anti_repeat_effects as anti_repeat_effects
from memory.anti_repeat_effects import (
    AntiRepeatDecision,
    AntiRepeatEffectStore,
    RepeatSignature,
    build_repeat_signature,
)
from utils.character_memory import (
    delete_character_memory_storage,
    evict_character_runtime_caches,
    rename_character_memory_storage,
    retire_character_runtime_caches,
)


def _store(tmp_path) -> AntiRepeatEffectStore:
    store = AntiRepeatEffectStore()
    config_manager = MagicMock()
    config_manager.memory_dir = str(tmp_path)
    store._config_manager = config_manager
    return store


def test_build_repeat_signature_prefers_safe_detector_evidence():
    signature = build_repeat_signature(
        "我又想说我会一直陪着你的，请放心。",
        ["我会一直陪着你", "https://private.example/path"],
        language="zh-CN",
    )

    assert signature == RepeatSignature(
        phrase="我会一直陪着你",
        normalized_phrase="我会一直陪着你",
        language="zh-CN",
    )


@pytest.mark.parametrize(
    "fragment",
    [
        "https://example.test/private",
        "intranet.example/private",
        "10.0.0.1/private",
        "localhost:8080/private",
        "`secret_code()`",
        "{{PRIVATE_VALUE}}",
        "<secret_key>",
    ],
)
def test_build_repeat_signature_rejects_protected_fragments(fragment):
    assert (
        build_repeat_signature(
            f"draft {fragment}",
            [fragment],
            language="en",
        )
        is None
    )


@pytest.mark.parametrize(
    ("draft", "tokenized_fragment"),
    [
        ("visit https://secret.example/private now", "//secret"),
        ("visit intranet.example/private now", "example/private"),
        ("visit intranet.example/private now", "intranet"),
        ("run `secret_code()` now", "`secret_code"),
        ("do not expose <secret_key> now", "secret_key"),
        ("```python\nsecret_key = 1", "secret_key"),
        ("~~~python\nsecret_key = 1\n~~~", "secret_key"),
        ("intro\n\n    secret_key = value\noutro", "secret_key"),
    ],
)
def test_build_repeat_signature_rejects_fragments_tokenized_from_protected_spans(
    draft,
    tokenized_fragment,
):
    assert (
        build_repeat_signature(
            draft,
            [tokenized_fragment],
            language="en",
        )
        is None
    )


def test_a_fragment_in_prose_does_not_survive_its_own_code_spelling():
    """The draft holds the term twice, in code and in prose. It signs neither.

    This used to assert the prose copy still signed, which needed the span
    layer to know exactly where the code span ended. An opener now discards
    the whole draft, so the prose copy goes with it -- the accepted cost.
    """
    assert build_repeat_signature(
        "run `secret_code()` then discuss secret_code in prose",
        ["secret_code"],
        language="en",
    ) is None

    # The dual: without the code spelling the same prose still signs, so this
    # cannot pass by never signing anything.
    signature = build_repeat_signature(
        "we should discuss secret phrase in prose, and discuss it again",
        ["secret phrase"],
        language="en",
    )
    assert signature is not None
    assert signature.normalized_phrase == "secret phrase"


@pytest.mark.parametrize(
    ("language", "draft"),
    [
        ("en", "quiet lantern"),
        ("zh-CN", "真的好想你"),
    ],
)
def test_build_repeat_signature_never_retains_a_complete_short_draft(
    language,
    draft,
):
    assert (
        build_repeat_signature(
            draft,
            [draft],
            language=language,
            fallback_fragment=draft,
        )
        is None
    )


def test_build_repeat_signature_skips_full_fallback_but_keeps_shorter_evidence():
    signature = build_repeat_signature(
        "quiet lantern again",
        ["quiet lantern"],
        language="en",
        fallback_fragment="quiet lantern again",
    )

    assert signature == RepeatSignature(
        phrase="quiet lantern",
        normalized_phrase="quiet lantern",
        language="en",
    )


def test_decision_is_counted_once_even_with_multiple_reasons(tmp_path):
    store = _store(tmp_path)
    signature = RepeatSignature("quiet lantern", "quiet lantern", "en")
    store.record_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("bm25", "unanswered_repeat"),
            action="regenerate",
            outcome="blocked_after_regen_bm25",
            signature=signature,
            score_before=12.0,
            score_after=4.0,
        ),
        now=1_700_000_000.0,
    )

    result = store.query_effects("Neko", 30, now=1_700_000_000.0)
    assert result["totals"]["detected"] == 1
    assert result["totals"]["regen_triggered"] == 1
    assert result["totals"]["blocked_delivery"] == 1
    assert result["reason_counts"] == {
        "bm25": 1,
        "literal_similarity": 0,
        "unanswered_repeat": 1,
    }
    assert result["bm25"] == {
        "pair_count": 1,
        "average_before": 12.0,
        "average_after": 4.0,
        "reduction_ratio": 0.6667,
    }
    assert result["patterns"][0]["blocked_count"] == 1


def test_bm25_summary_preserves_increased_repetition_ratio(tmp_path):
    store = _store(tmp_path)
    store.record_decision(
        "Neko",
        AntiRepeatDecision(
            source="regular_prompt",
            reasons=("bm25",),
            action="regenerate",
            outcome="blocked_after_regen_bm25",
            score_before=9.0,
            score_after=15.0,
        ),
        now=1_700_000_000.0,
    )

    result = store.query_effects("Neko", 30, now=1_700_000_000.0)

    assert result["bm25"] == {
        "pair_count": 1,
        "average_before": 9.0,
        "average_after": 15.0,
        "reduction_ratio": -0.6667,
    }


def test_unattributed_decision_keeps_aggregate_without_text(tmp_path):
    store = _store(tmp_path)
    store.record_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("literal_similarity",),
            action="block",
            outcome="blocked_initial",
        ),
        now=1_700_000_000.0,
    )

    result = store.query_effects("Neko", 7, now=1_700_000_000.0)
    assert result["totals"]["unattributed"] == 1
    assert result["patterns"] == []


def test_query_missing_store_does_not_create_character_directory(tmp_path):
    store = _store(tmp_path)
    result = store.query_effects("Missing", 30, now=1_700_000_000.0)

    assert result["source_available"] is False
    assert not (tmp_path / "Missing").exists()


def test_query_sanitizes_invalid_persisted_effect_values(tmp_path):
    effect_dir = tmp_path / "Neko"
    effect_dir.mkdir()
    (effect_dir / "anti_repeat_effects.json").write_text(
        json.dumps(
            {
                "version": 1,
                "schema_version": "anti-repeat-effects/v1",
                "started_at": "inf",
                "daily_buckets": {
                    "2023-11-14": {
                        "counters": {"detected": 1},
                        "bm25": {
                            "before_sum": "nan",
                            "after_sum": "inf",
                            "pair_count": 1,
                        },
                        "patterns": {
                            "pattern": {
                                "phrase": "quiet lantern",
                                "normalized_phrase": "quiet lantern",
                                "language": "en",
                                "detected_count": 1,
                                "last_seen_at": "-inf",
                            }
                        },
                    }
                },
                "response_buckets": {
                    "response": {
                        "created_at": 10**400,
                        "delivered_at": "nan",
                        "bucket": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = _store(tmp_path).query_effects(
        "Neko",
        30,
        now=1_700_000_000.0,
    )

    assert result["started_at"] == 1_700_000_000.0
    assert result["bm25"] == {
        "pair_count": 1,
        "average_before": 0.0,
        "average_after": 0.0,
        "reduction_ratio": 0.0,
    }
    assert result["patterns"][0]["last_seen_at"] == 0.0
    json.dumps(result, allow_nan=False)


def test_query_derives_normalized_phrase_from_sanitized_phrase(tmp_path):
    effect_dir = tmp_path / "Neko"
    effect_dir.mkdir()
    (effect_dir / "anti_repeat_effects.json").write_text(
        json.dumps(
            {
                "version": 1,
                "schema_version": "anti-repeat-effects/v1",
                "started_at": 1_700_000_000.0,
                "daily_buckets": {
                    "2023-11-14": {
                        "patterns": {
                            "tampered": {
                                "phrase": "quiet lantern",
                                "normalized_phrase": "https://private.example/path",
                                "language": "en",
                                "detected_count": 1,
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = _store(tmp_path).query_effects(
        "Neko",
        30,
        now=1_700_000_000.0,
    )

    assert result["patterns"] == [
        {
            "phrase": "quiet lantern",
            "normalized_phrase": "quiet lantern",
            "language": "en",
            "reasons": {},
            "detected_count": 1,
            "regen_triggered_count": 0,
            "regen_guard_passed_count": 0,
            "blocked_count": 0,
            "last_seen_at": 0.0,
        }
    ]


def test_query_sanitizes_overflowing_counter_without_resetting_other_history(tmp_path):
    effect_dir = tmp_path / "Neko"
    effect_dir.mkdir()
    (effect_dir / "anti_repeat_effects.json").write_text(
        '{"version":1,"schema_version":"anti-repeat-effects/v1",'
        '"started_at":1700000000,"daily_buckets":{"2023-11-14":{'
        '"counters":{"detected":1e400,"regen_triggered":2}}}}',
        encoding="utf-8",
    )

    result = _store(tmp_path).query_effects("Neko", 30, now=1_700_000_000.0)

    assert result["totals"]["detected"] == 0
    assert result["totals"]["regen_triggered"] == 2


def test_storage_contains_fragment_but_not_rejected_draft(tmp_path):
    store = _store(tmp_path)
    rejected_draft = "PRIVATE full rejected draft around quiet lantern and more context"
    signature = build_repeat_signature(
        rejected_draft,
        ["quiet lantern"],
        language="en",
    )
    store.record_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="regenerate",
            outcome="regen_guard_passed",
            signature=signature,
        ),
        now=1_700_000_000.0,
    )

    payload = (tmp_path / "Neko" / "anti_repeat_effects.json").read_text(
        encoding="utf-8"
    )
    assert "quiet lantern" in payload
    assert rejected_draft not in payload
    assert "PRIVATE full rejected draft" not in payload
    assert json.loads(payload)["schema_version"] == "anti-repeat-effects/v1"


def test_clear_effects_propagates_write_failure_and_restores_cached_history(
    tmp_path,
    monkeypatch,
):
    store = _store(tmp_path)
    store.record_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        ),
        now=1_700_000_000.0,
    )
    monkeypatch.setattr(
        anti_repeat_effects,
        "atomic_write_json",
        MagicMock(side_effect=OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        store.clear_effects("Neko")

    result = store.query_effects("Neko", 30, now=1_700_000_000.0)
    assert result["totals"]["detected"] == 1


def test_effect_write_fence_runs_before_sidecar_mutation(tmp_path, monkeypatch):
    store = _store(tmp_path)
    write = MagicMock()
    monkeypatch.setattr(anti_repeat_effects, "atomic_write_json", write)

    @contextmanager
    def reject_write(*args, **kwargs):
        raise RuntimeError("maintenance")
        yield

    from utils import cloudsave_runtime

    monkeypatch.setattr(cloudsave_runtime, "cloudsave_writable_transaction", reject_write)

    with pytest.raises(RuntimeError, match="maintenance"):
        store._flush_snapshot(
            "Neko",
            {"version": 1},
            1,
            raise_on_error=True,
        )

    write.assert_not_called()
    assert not (tmp_path / "Neko").exists()


def test_query_rejects_unsupported_period(tmp_path):
    with pytest.raises(ValueError, match="effect days"):
        _store(tmp_path).query_effects("Neko", 14)


def _record_delivered_response(store, response_id, timestamp):
    store.record_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="regenerate",
            outcome="regen_guard_passed",
            response_id=response_id,
        ),
        now=timestamp,
    )
    staged = store.stage_response_delivered("Neko", response_id, now=timestamp)
    store._flush_snapshot(*staged)


def test_response_query_availability_requires_a_link_in_requested_slice(tmp_path):
    store = _store(tmp_path)
    timestamp = 1_700_000_000.0
    _record_delivered_response(store, "outside-slice", timestamp)

    result = store.query_effects_for_responses(
        "Neko",
        ["requested-response"],
        25,
        now=timestamp,
    )

    assert result["source_available"] is False
    assert result["linked_message_count"] == 0
    assert result["totals"]["detected"] == 0


def test_response_query_prunes_expired_records_and_persists_snapshot(tmp_path):
    store = _store(tmp_path)
    timestamp = 1_700_000_000.0
    _record_delivered_response(store, "expired-response", timestamp)

    result = store.query_effects_for_responses(
        "Neko",
        ["expired-response"],
        100,
        now=timestamp + 121 * 24 * 60 * 60,
    )

    payload = json.loads(
        (tmp_path / "Neko" / "anti_repeat_effects.json").read_text(encoding="utf-8")
    )
    assert result["source_available"] is False
    assert result["linked_message_count"] == 0
    assert payload["response_buckets"] == {}


def test_query_prunes_future_dated_effect_records_and_persists_snapshot(tmp_path):
    store = _store(tmp_path)
    timestamp = 1_700_000_000.0
    future_timestamp = timestamp + 365 * 24 * 60 * 60
    current_day = anti_repeat_effects._utc_day(timestamp)
    future_day = anti_repeat_effects._utc_day(future_timestamp)
    current_response_key = anti_repeat_effects._response_key("current-response")
    future_response_key = anti_repeat_effects._response_key("future-response")
    effect_dir = tmp_path / "Neko"
    effect_dir.mkdir()
    (effect_dir / "anti_repeat_effects.json").write_text(
        json.dumps(
            {
                "version": 1,
                "daily_buckets": {
                    current_day: {"counters": {"detected": 1}},
                    future_day: {"counters": {"detected": 99}},
                },
                "response_buckets": {
                    current_response_key: {
                        "created_at": timestamp,
                        "delivered_at": timestamp,
                        "bucket": {"counters": {"detected": 1}},
                    },
                    future_response_key: {
                        "created_at": future_timestamp,
                        "delivered_at": future_timestamp,
                        "bucket": {"counters": {"detected": 99}},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = store.query_effects("Neko", 30, now=timestamp)
    persisted = json.loads(
        (effect_dir / "anti_repeat_effects.json").read_text(encoding="utf-8")
    )

    assert result["totals"]["detected"] == 1
    assert set(persisted["daily_buckets"]) == {current_day}
    assert set(persisted["response_buckets"]) == {current_response_key}


def test_response_cap_evicts_undelivered_before_delivered(tmp_path, monkeypatch):
    monkeypatch.setattr(anti_repeat_effects, "MAX_RESPONSE_BUCKETS", 2)
    store = _store(tmp_path)
    _record_delivered_response(store, "delivered", 1_700_000_000.0)

    for offset, response_id in enumerate(("blocked-old", "blocked-new"), start=1):
        store.record_decision(
            "Neko",
            AntiRepeatDecision(
                source="proactive",
                reasons=("bm25",),
                action="block",
                outcome="blocked_initial",
                response_id=response_id,
            ),
            now=1_700_000_000.0 + offset,
        )

    result = store.query_effects_for_responses(
        "Neko",
        ["delivered"],
        100,
        now=1_700_000_003.0,
    )

    assert result["linked_message_count"] == 1
    assert len(store._cache["Neko"]["response_buckets"]) == 2


def test_response_load_cap_preserves_delivered_bucket(monkeypatch):
    monkeypatch.setattr(anti_repeat_effects, "MAX_RESPONSE_BUCKETS", 2)
    bucket = {"counters": {"detected": 1}}
    payload = {
        "version": 1,
        "response_buckets": {
            "delivered": {
                "created_at": 1.0,
                "delivered_at": 1.0,
                "bucket": bucket,
            },
            "blocked-old": {
                "created_at": 2.0,
                "delivered_at": 0.0,
                "bucket": bucket,
            },
            "blocked-new": {
                "created_at": 3.0,
                "delivered_at": 0.0,
                "bucket": bucket,
            },
        },
    }

    normalized = AntiRepeatEffectStore._normalize_payload(payload, now=3.0)

    assert set(normalized["response_buckets"]) == {"delivered", "blocked-new"}


def test_evict_fences_old_snapshot_before_reusing_character_name(tmp_path):
    store = _store(tmp_path)
    old_snapshot = store.stage_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        ),
        now=1_700_000_000.0,
    )

    store.retire_character("Neko")
    store._flush_snapshot(*old_snapshot)
    assert not (tmp_path / "Neko").exists()

    store.record_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("literal_similarity",),
            action="regenerate",
            outcome="regen_guard_passed",
        ),
        now=1_700_000_001.0,
    )
    result = store.query_effects("Neko", 30, now=1_700_000_001.0)
    assert result["totals"]["detected"] == 1
    assert result["reason_counts"]["bm25"] == 0


def test_character_storage_rename_and_delete_evict_effect_cache(
    tmp_path, monkeypatch
):
    config_manager = SimpleNamespace(
        memory_dir=str(tmp_path),
        project_memory_dir=None,
    )
    store = _store(tmp_path)
    monkeypatch.setattr(anti_repeat_effects, "_GLOBAL_STORE", store)
    store.record_decision(
        "Old",
        AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        ),
        now=1_700_000_000.0,
    )
    store.query_effects("New", 30, now=1_700_000_000.0)

    rename_character_memory_storage(config_manager, "Old", "New")
    assert "Old" not in store._cache
    assert "New" not in store._cache
    assert (tmp_path / "New" / "anti_repeat_effects.json").exists()

    store.query_effects("New", 30, now=1_700_000_000.0)
    assert "New" in store._cache
    delete_character_memory_storage(config_manager, "New")
    assert "New" not in store._cache
    assert not (tmp_path / "New").exists()


def _decision(outcome: str = "blocked_initial", **kwargs) -> AntiRepeatDecision:
    return AntiRepeatDecision(
        source="proactive",
        reasons=("bm25",),
        action="block",
        outcome=outcome,
        **kwargs,
    )


def test_capacity_eviction_keeps_the_bucket_the_current_turn_just_created(tmp_path):
    """A full, all-delivered store must not swallow the in-flight response bucket.

    Capacity eviction prefers to keep DELIVERED buckets, so a freshly created one
    (``delivered_at == 0``) sorts ahead of every delivered bucket. Once the store
    reaches steady state — delivery converts buckets, blocked ones are evicted
    first — each turn deleted the very bucket it had just created, and
    ``stage_response_delivered`` could never find it again.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0

    for index in range(anti_repeat_effects.MAX_RESPONSE_BUCKETS):
        response_id = f"old-turn-{index}"
        store.record_decision("Neko", _decision(response_id=response_id), now=now)
        store._flush_snapshot(
            *store.stage_response_delivered("Neko", response_id, now=now)
        )

    payload = store._cache["Neko"]
    assert len(payload["response_buckets"]) == anti_repeat_effects.MAX_RESPONSE_BUCKETS
    assert all(
        bucket["delivered_at"] > 0 for bucket in payload["response_buckets"].values()
    )

    store.record_decision("Neko", _decision(response_id="fresh-turn"), now=now + 1)
    assert store.stage_response_delivered("Neko", "fresh-turn", now=now + 2) is not None

    linked = store.query_effects_for_responses(
        "Neko", ["fresh-turn"], 100, now=now + 3
    )
    assert linked["source_available"] is True
    assert linked["linked_message_count"] == 1
    assert linked["totals"]["detected"] == 1


def test_delivery_mark_survives_a_publication_timestamp_in_the_past(tmp_path):
    """``mark_anti_repeat_response_delivered`` passes the publication instant.

    That instant is already in the past when the mark runs, so pruning with it
    must not treat the bucket being marked as future-dated.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0

    store.record_decision("Neko", _decision(response_id="turn"), now=now + 10)
    staged = store.stage_response_delivered("Neko", "turn", now=now)

    assert staged is not None
    linked = store.query_effects_for_responses("Neko", ["turn"], 100, now=now + 20)
    assert linked["linked_message_count"] == 1


def test_staged_snapshot_shares_no_mutable_state_with_the_live_cache(tmp_path):
    """The snapshot is serialized on a worker thread that does not hold the
    per-name lock, so sharing any sub-dict with the cache would let
    ``atomic_write_json`` iterate a dict another turn is writing to."""
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    signature = RepeatSignature("quiet lantern", "quiet lantern", "en")
    store.record_decision(
        "Neko", _decision(signature=signature, response_id="turn"), now=now
    )

    _name, snapshot, _seq = store.stage_decision(
        "Neko", _decision(signature=signature, response_id="turn"), now=now
    )
    live = store._cache["Neko"]
    day = anti_repeat_effects._utc_day(now)

    assert snapshot is not live
    assert snapshot["daily_buckets"] is not live["daily_buckets"]
    assert snapshot["daily_buckets"][day] is not live["daily_buckets"][day]
    assert (
        snapshot["daily_buckets"][day]["counters"]
        is not live["daily_buckets"][day]["counters"]
    )
    live_patterns = live["daily_buckets"][day]["patterns"]
    pattern_id = next(iter(live_patterns))
    assert snapshot["daily_buckets"][day]["patterns"] is not live_patterns
    assert (
        snapshot["daily_buckets"][day]["patterns"][pattern_id]["reasons"]
        is not live_patterns[pattern_id]["reasons"]
    )
    response_key = next(iter(live["response_buckets"]))
    assert (
        snapshot["response_buckets"][response_key]["bucket"]
        is not live["response_buckets"][response_key]["bucket"]
    )

    expected = json.loads(json.dumps(live, ensure_ascii=False))
    assert snapshot == expected

    # Mutating the cache the way a later turn would must not reach the snapshot.
    store.record_decision("Neko", _decision(response_id="turn"), now=now)
    assert snapshot["daily_buckets"][day]["counters"]["detected"] == 2


def test_decision_after_eviction_does_not_recreate_a_removed_character_dir(tmp_path):
    """``retire_character`` fences snapshots staged before it, not after it.

    Without the retirement guard, a decision recorded while delete/rename was
    still in flight went through ``ensure_character_dir`` and made the directory
    the caller had just removed reappear.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    store.record_decision("Neko", _decision(), now=now)
    assert (tmp_path / "Neko" / "anti_repeat_effects.json").exists()

    import shutil

    store.retire_character("Neko")
    shutil.rmtree(tmp_path / "Neko")

    store.record_decision("Neko", _decision(), now=now + 1)

    assert not (tmp_path / "Neko").exists()


def test_a_retired_name_writes_again_only_once_a_directory_exists(tmp_path):
    """Retirement outlives the directory, but it never blocks a live one.

    Exercises the real delete-then-recreate order: retire, remove the tree, then
    let another writer create the directory again. The store must refuse while
    the directory is gone and resume once it exists — without ever creating it
    itself, and without un-retiring the name. Directory existence cannot be
    treated as proof the identity is live, because
    ``delete_character_memory_storage`` retires BEFORE it removes the tree, so a
    flush landing in that window would otherwise disarm the guard permanently.
    Only ``evict_character`` -- the explicit live-identity event -- lifts it.
    """
    import shutil

    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0

    store.retire_character("Neko")

    # Still inside the delete window: the doomed directory is present. Writing
    # here is harmless (rmtree wins), but retirement must NOT be lifted.
    store.record_decision("Neko", _decision(), now=now)
    assert "Neko" in store._retired

    shutil.rmtree(tmp_path / "Neko")
    store.record_decision("Neko", _decision(), now=now + 1)
    assert not (tmp_path / "Neko").exists()

    # A sibling writer (or an explicit re-creation) brings the directory back.
    (tmp_path / "Neko").mkdir()
    store.record_decision("Neko", _decision(), now=now + 2)

    assert (tmp_path / "Neko" / "anti_repeat_effects.json").exists()
    assert "Neko" in store._retired


def test_queries_do_not_hand_live_cache_buckets_to_the_summarizer(tmp_path):
    """Summarizing happens after the per-name lock is released.

    Handing out the live bucket dicts lets a concurrent decision add pattern
    keys while `_summarize_effect_buckets` iterates them — a "dictionary changed
    size during iteration" away, and torn counters short of that. The staging
    path already refuses to share sub-dicts for exactly this reason.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    signature = RepeatSignature("quiet lantern", "quiet lantern", "en")
    store.record_decision(
        "Neko", _decision(signature=signature, response_id="turn"), now=now
    )
    store._flush_snapshot(*store.stage_response_delivered("Neko", "turn", now=now))

    live_day = store._cache["Neko"]["daily_buckets"]
    live_responses = store._cache["Neko"]["response_buckets"]
    handed_out = []
    original = anti_repeat_effects._summarize_effect_buckets

    def _spy(buckets):
        buckets = list(buckets)
        handed_out.extend(buckets)
        return original(buckets)

    anti_repeat_effects._summarize_effect_buckets = _spy
    try:
        day_result = store.query_effects("Neko", 30, now=now)
        response_result = store.query_effects_for_responses(
            "Neko", ["turn"], 100, now=now
        )
    finally:
        anti_repeat_effects._summarize_effect_buckets = original

    assert handed_out, "the summarizer should have received buckets"
    live_objects = list(live_day.values()) + [
        entry["bucket"] for entry in live_responses.values()
    ]
    for bucket in handed_out:
        assert not any(bucket is live for live in live_objects)

    # A later decision must not retroactively change an already-returned result.
    store.record_decision("Neko", _decision(response_id="turn"), now=now)
    assert day_result["totals"]["detected"] == 1
    assert response_result["totals"]["detected"] == 1


def test_availability_reflects_in_period_buckets_not_file_existence(tmp_path):
    """`clear_effects` leaves an empty payload on disk.

    Reporting availability from file existence made the panel skip its "no
    records for this period" state and render a row of zeros right after the
    user cleared the statistics. A nonempty sidecar whose buckets all fall
    outside the requested window had the same problem.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    store.record_decision("Neko", _decision(), now=now)

    assert store.query_effects("Neko", 30, now=now)["source_available"] is True

    store.clear_effects("Neko")
    assert (tmp_path / "Neko" / "anti_repeat_effects.json").exists()
    assert store.query_effects("Neko", 30, now=now)["source_available"] is False

    # Buckets outside the requested window are equally unavailable.
    store.record_decision("Neko", _decision(), now=now)
    much_later = now + 60 * 24 * 60 * 60
    assert store.query_effects("Neko", 7, now=much_later)["source_available"] is False


def test_clear_effects_does_not_hold_the_character_lock_across_the_fence(tmp_path):
    """Lock ORDER, not just correctness: fence first, character lock second.

    A cloud import holds the cloud-apply fence for its whole duration and takes
    the character lock inside it (to evict caches). If a reset takes the
    character lock and then reaches for the fence, the two deadlock. This pins
    the order by proving the character lock is free while the flush is inside
    the transaction.
    """
    import threading

    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    store.record_decision("Neko", _decision(), now=1_700_000_000.0)

    observed: list[bool] = []
    original = anti_repeat_effects.AntiRepeatEffectStore._flush_snapshot

    def _flush_and_probe(self, *args, **kwargs):
        # Stands in for the point where the real flush enters the cloud-save
        # transaction: another thread must be able to take the character lock.
        lock = self._get_lock("Neko")
        acquired = lock.acquire(timeout=1.0)
        observed.append(acquired)
        if acquired:
            lock.release()
        return original(self, *args, **kwargs)

    anti_repeat_effects.AntiRepeatEffectStore._flush_snapshot = _flush_and_probe
    try:
        done = threading.Event()
        errors: list[BaseException] = []

        def run_clear():
            try:
                store.clear_effects("Neko")
            except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
                errors.append(exc)
            done.set()

        worker = threading.Thread(target=run_clear)
        worker.start()
        worker.join(5)
    finally:
        anti_repeat_effects.AntiRepeatEffectStore._flush_snapshot = original

    assert done.is_set()
    assert errors == []
    assert observed == [True], "the character lock was still held during the flush"


def test_evicting_a_live_identity_does_not_retire_it(tmp_path):
    """A cloud-save import replaces the files of a LIVE character.

    Retiring it would deny it the lazy directory creation every sibling memory
    writer gets, so an imported profile that ships no managed memory files
    would never persist its aggregates while the character is in active use.
    """
    store = _store(tmp_path)
    now = 1_700_000_000.0

    store.evict_character("Neko")

    assert "Neko" not in store._retired
    store.record_decision("Neko", _decision(), now=now)
    assert (tmp_path / "Neko" / "anti_repeat_effects.json").exists()


def test_evicting_a_retired_name_brings_it_back(tmp_path):
    """Re-creating an identity is the explicit event that lifts retirement.

    A rename target and a cloud-save import both name a live character. Nothing
    else lifts it -- directory existence in particular does not, because the
    delete path retires while the doomed tree is still on disk.
    """
    import shutil

    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0

    store.retire_character("Neko")
    shutil.rmtree(tmp_path / "Neko")
    store.record_decision("Neko", _decision(), now=now)
    assert not (tmp_path / "Neko").exists()

    store.evict_character("Neko")

    assert "Neko" not in store._retired
    store.record_decision("Neko", _decision(), now=now + 1)
    assert (tmp_path / "Neko" / "anti_repeat_effects.json").exists()


def test_a_failed_reset_loses_nothing(tmp_path):
    """A reset that reports failure must lose NEITHER generation.

    Publishing the empty payload before the flush made a concurrent decision
    load it, mutate it in place and stage a newer snapshot built on the cut --
    so the racer made the reset durable even though the reset failed, taking
    the pre-reset aggregates and ``started_at`` with it while the endpoint
    reported failure. No rollback can undo that, because by then the racer has
    already written the cleared payload out. Keeping the pre-reset payload in
    the cache until the cut is durable inverts it: the concurrent decision
    builds on the OLD data, so both survive.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    # Distinct reasons: the pre-reset state and the concurrent decision land
    # on the SAME day bucket with the same counters, so only the reason tells
    # a preserved decision from a restored `previous`.
    store.record_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        ),
        now=now,
    )

    original_flush = store._flush_snapshot

    def _flush_then_fail(*args, **kwargs):
        # Runs after clear_effects released the character lock, exactly where a
        # proactive decision can land. Restore the real flush first so the
        # concurrent decision persists normally instead of recursing.
        store._flush_snapshot = original_flush
        store.record_decision(
            "Neko",
            AntiRepeatDecision(
                source="proactive",
                reasons=("literal_similarity",),
                action="block",
                outcome="blocked_initial",
            ),
            now=now + 1,
        )
        raise OSError("disk full")

    store._flush_snapshot = _flush_then_fail
    try:
        with pytest.raises(OSError):
            store.clear_effects("Neko")
    finally:
        store._flush_snapshot = original_flush

    day = anti_repeat_effects._utc_day(now + 1)
    reasons = store._cache["Neko"]["daily_buckets"][day]["reason_counts"]
    assert reasons.get("literal_similarity") == 1, (
        "the concurrent decision was lost with the failed reset"
    )
    assert reasons.get("bm25") == 1, (
        "the failed reset destroyed the pre-reset aggregates anyway"
    )
    assert store._cache["Neko"]["started_at"] == now, (
        "the failed reset still moved the statistics-since date"
    )

    # The file is the authority, and it must not carry the cut either.
    persisted = json.loads(
        (tmp_path / "Neko" / "anti_repeat_effects.json").read_text(encoding="utf-8")
    )
    assert persisted["daily_buckets"][day]["reason_counts"]["bm25"] == 1
    assert persisted["started_at"] == now


def test_runtime_cache_entry_points_split_live_from_removed(tmp_path):
    """The two entry points differ only in retirement, and must keep differing.

    A delete or a rename SOURCE is going away and retires. A cloud-save import
    or a rename TARGET is live and only invalidates. Collapsing them in either
    direction breaks one of the two: retiring a live name stops it persisting,
    and not retiring a removed one lets an in-flight decision recreate the
    directory that was just deleted.
    """
    previous = anti_repeat_effects._GLOBAL_STORE
    store = _store(tmp_path)
    anti_repeat_effects._GLOBAL_STORE = store
    try:
        store._cache["Neko"] = anti_repeat_effects._default_payload(1_700_000_000.0)
        retire_character_runtime_caches("Neko")
        assert "Neko" not in store._cache
        assert "Neko" in store._retired

        store._cache["Neko"] = anti_repeat_effects._default_payload(1_700_000_000.0)
        evict_character_runtime_caches("Neko")
        assert "Neko" not in store._cache
        assert "Neko" not in store._retired
    finally:
        anti_repeat_effects._GLOBAL_STORE = previous


_WRAPPED_TEMPLATE_DRAFTS = [
    ("jinja", "sure thing {{" + chr(10) + "secret helper phrase" + chr(10) + "}} enjoy"),
    ("shell", "sure thing ${" + chr(10) + "secret helper phrase" + chr(10) + "} enjoy"),
    ("erb", "sure thing <%" + chr(10) + "secret helper phrase" + chr(10) + "%> enjoy"),
    # Opener and closer on their own lines with a two-line body.
    ("jinja block", "ok {{" + chr(10) + "alpha" + chr(10) + "secret helper phrase" + chr(10) + "}} done"),
    ("shell block", "ok ${" + chr(10) + "alpha" + chr(10) + "secret helper phrase" + chr(10) + "} done"),
    # The sidecar consults its OWN pattern, so the statement and comment
    # forms have to be added here as well as in the miner. Patching only the
    # miner leaves this leak open while every miner-side test goes green.
    ("jinja statement", "sure {%" + chr(10) + "secret helper phrase" + chr(10) + "%} enjoy"),
    ("jinja comment", "sure {#" + chr(10) + "secret helper phrase" + chr(10) + "#} enjoy"),
    # Braces inside the body, on the sidecar side too.
    ("jinja dict", 'sure {{ {"k": "secret helper phrase"} }} enjoy'),
    ("jinja statement dict", 'sure {% set c = {"k": "secret helper phrase"} %} enjoy'),
    ("jinja comment dict", 'sure {# {"k": "secret helper phrase"} #} enjoy'),
]


@pytest.mark.parametrize(
    "label, draft",
    _WRAPPED_TEMPLATE_DRAFTS,
    ids=[row[0] for row in _WRAPPED_TEMPLATE_DRAFTS],
)
def test_a_wrapped_template_body_never_reaches_the_sidecar(label, draft):
    """The single-line form of the same content already returned None.

    `_PROTECTED_RE` rejected newlines inside every template alternative, so a
    body that merely wrapped stayed searchable and detector evidence taken from
    inside it could be persisted -- the leak was triggered purely by a newline
    between the delimiters.
    """
    assert build_repeat_signature(
        draft, ["secret", "helper"], language="en"
    ) is None, label


def test_a_stray_template_opener_costs_the_whole_draft():
    """Distance from the closer no longer buys anything, because nothing looks.

    The line budget this once pinned existed so a stray "${" could not protect
    to end of text. There is no span to bound now: the opener alone discards
    the draft however far away its closer sits.
    """
    draft = (
        "那个 ${" + chr(10)
        + "A呢" + chr(10)
        + "我们一起去吃饭吧" + chr(10)
        + "B呢" + chr(10)
        + "最后那个括号 }"
    )

    assert build_repeat_signature(draft, ["我们一起去吃饭吧"], language="zh-CN") is None

    # The dual: the same speech without the delimiter still signs.
    clean = draft.replace("那个 ${", "那个").replace("最后那个括号 }", "最后那个括号")
    signature = build_repeat_signature(clean, ["我们一起去吃饭吧"], language="zh-CN")
    assert signature is not None
    assert signature.phrase == "我们一起去吃饭吧"


def test_a_failed_reset_does_not_resurrect_an_evicted_cache(tmp_path):
    """A cloud import evicting mid-reset must stay evicted.

    ``_evict_unlocked`` fences the sequence at ``max(staged, written)``, which
    equals the sequence the reset staged -- so a rollback guarded on "did the
    sequence advance" read "nothing newer" and wrote the pre-reset payload back
    into the cache the import had just dropped. The next decision then flushed
    that resurrected payload over the freshly imported file.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    store.record_decision("Neko", _decision(), now=now)

    original_flush = store._flush_snapshot

    def _evict_then_fail(*_args, **_kwargs):
        store._flush_snapshot = original_flush
        # What import_local_cloudsave_snapshot does under the cloud-apply
        # fence, right before it replaces memory/Neko/.
        store.evict_character("Neko")
        raise OSError("disk full")

    store._flush_snapshot = _evict_then_fail
    try:
        with pytest.raises(OSError):
            store.clear_effects("Neko")
    finally:
        store._flush_snapshot = original_flush

    assert "Neko" not in store._cache, (
        "the failed reset resurrected a cache entry the import had evicted"
    )


def test_a_reset_outrun_by_a_writer_cuts_again(tmp_path):
    """Publishing a cut the racer already overwrote would report a false success.

    A decision that stages AFTER the reset flushed writes the pre-reset payload
    plus its own delta at a higher sequence, so it lands after ours and the cut
    is not durable. Publishing regardless would leave the cache empty while the
    file still held the data -- the reset reporting success having cleared
    nothing.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    store.record_decision("Neko", _decision(), now=now)

    original_flush = store._flush_snapshot
    races = []

    def _flush_then_race(*args, **kwargs):
        original_flush(*args, **kwargs)
        if not races:
            races.append(True)
            # Stages seq+1 from the PRE-RESET payload and writes it out, so
            # the cut we just flushed is already gone from disk.
            store.record_decision("Neko", _decision(), now=now + 1)

    store._flush_snapshot = _flush_then_race
    try:
        store.clear_effects("Neko")
    finally:
        store._flush_snapshot = original_flush

    assert races, "the race never happened; the test proves nothing"
    assert store._cache["Neko"]["daily_buckets"] == {}
    persisted = json.loads(
        (tmp_path / "Neko" / "anti_repeat_effects.json").read_text(encoding="utf-8")
    )
    assert persisted["daily_buckets"] == {}, (
        "the reset reported success while the file still held the old data"
    )


def test_a_reset_abandons_when_the_identity_is_replaced(tmp_path):
    """An import landing mid-flush must not have its file cleared afterwards.

    Eviction fences the write sequence at exactly the value the reset staged,
    so the flush is silently skipped AND "did the sequence advance" reads no.
    Publishing on that basis put an empty payload into the cache the import had
    just dropped, and the next decision flushed it over the imported file --
    while the endpoint reported the reset had succeeded. Re-cutting would be
    worse still: it would clear data the reset never asked about.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    day = anti_repeat_effects._utc_day(now)
    store.record_decision("Neko", _decision(), now=now)
    persisted_path = tmp_path / "Neko" / "anti_repeat_effects.json"

    original_flush = store._flush_snapshot

    def _import_then_flush(*args, **kwargs):
        store._flush_snapshot = original_flush
        # What import_local_cloudsave_snapshot does under the cloud-apply
        # fence: drop the cache, then replace memory/Neko/ wholesale.
        store.evict_character("Neko")
        imported = json.loads(persisted_path.read_text(encoding="utf-8"))
        imported["daily_buckets"][day]["reason_counts"]["bm25"] = 42
        persisted_path.write_text(
            json.dumps(imported, ensure_ascii=False), encoding="utf-8"
        )
        return original_flush(*args, **kwargs)

    store._flush_snapshot = _import_then_flush
    try:
        with pytest.raises(RuntimeError):
            store.clear_effects("Neko")
    finally:
        store._flush_snapshot = original_flush

    assert "Neko" not in store._cache, (
        "the reset republished a cut into the cache the import had evicted"
    )
    # A different reason, so the imported count stays exactly 42 and the
    # assertion cannot be satisfied by an increment that merely looks intact.
    store.record_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("literal_similarity",),
            action="block",
            outcome="blocked_initial",
        ),
        now=now + 1,
    )
    imported = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert imported["daily_buckets"][day]["reason_counts"]["bm25"] == 42, (
        "the imported file was clobbered by the abandoned reset"
    )


def test_a_reset_that_gives_up_leaves_the_file_matching_memory(tmp_path):
    """Every attempt wrote the cut out before losing the race.

    The writer that outran us normally restores the file, but if its own flush
    fails or is cancelled the cut stays on disk while this reports failure --
    and the data comes back erased after a restart, contradicting the promise
    that a failed reset loses nothing.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    day = anti_repeat_effects._utc_day(now)
    store.record_decision("Neko", _decision(), now=now)
    persisted_path = tmp_path / "Neko" / "anti_repeat_effects.json"

    original_flush = store._flush_snapshot
    attempts = []

    def _outrun(*args, **kwargs):
        original_flush(*args, **kwargs)
        attempts.append(True)
        if len(attempts) <= anti_repeat_effects._CLEAR_ATTEMPTS:
            # A writer stages a newer snapshot but its own flush never lands.
            with store._get_lock("Neko"):
                store._staged_seq["Neko"] = store._staged_seq.get("Neko", 0) + 1

    store._flush_snapshot = _outrun
    try:
        with pytest.raises(RuntimeError):
            store.clear_effects("Neko")
    finally:
        store._flush_snapshot = original_flush

    # Three cuts plus the restore flush that puts the live payload back --
    # counting it is what proves the restore actually reached the file layer.
    assert len(attempts) == anti_repeat_effects._CLEAR_ATTEMPTS + 1
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted["daily_buckets"][day]["reason_counts"]["bm25"] == 1, (
        "the abandoned reset left its cut on disk"
    )
    assert persisted["started_at"] == now


def test_a_failed_restore_surfaces_instead_of_being_swallowed(tmp_path, monkeypatch):
    """The restore is the last thing standing between the cut and the file.

    Flushing it with the default raise_on_error swallowed an atomic-write
    failure, so the reset raised its race message while the file kept the cut --
    the same loss the restore exists to prevent, one level down.

    The real ``_flush_snapshot`` has to run for this to mean anything: stubbing
    it out entirely would raise from the stub and never consult the flag at
    all. So only the write underneath it is made to fail.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    store.record_decision("Neko", _decision(), now=now)

    writes = []
    real_write = anti_repeat_effects.atomic_write_json

    def _fail_the_restore_write(*args, **kwargs):
        writes.append(True)
        if len(writes) > anti_repeat_effects._CLEAR_ATTEMPTS:
            raise OSError("restore write failed")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(
        anti_repeat_effects, "atomic_write_json", _fail_the_restore_write
    )

    original_flush = store._flush_snapshot

    def _outrun(*args, **kwargs):
        original_flush(*args, **kwargs)
        with store._get_lock("Neko"):
            store._staged_seq["Neko"] = store._staged_seq.get("Neko", 0) + 1

    store._flush_snapshot = _outrun
    try:
        with pytest.raises(OSError):
            store.clear_effects("Neko")
    finally:
        store._flush_snapshot = original_flush

    assert len(writes) == anti_repeat_effects._CLEAR_ATTEMPTS + 1, (
        "the restore never reached the write layer"
    )


def test_a_transient_read_failure_is_not_cached_as_empty(tmp_path, monkeypatch):
    """An unreadable-but-present sidecar must not become "no aggregates".

    A bare ``except Exception`` turned a sharing violation into an empty
    payload, which ``_load_unlocked`` then CACHED -- it only assigns on a
    return -- so later reads served zeros and the next decision flushed that
    emptiness over real history. Asserted through ``query_effects``, the call
    site, not the helper: a test that only drives ``_read_payload_from_disk``
    stays green if the caching above it is what regresses.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    for _ in range(5):
        store.record_decision("Neko", _decision(), now=now)
    persisted_path = tmp_path / "Neko" / "anti_repeat_effects.json"
    before = persisted_path.read_text(encoding="utf-8")

    # A second store: cold cache, so the read actually happens.
    reader = _store(tmp_path)
    real_open = anti_repeat_effects.open if hasattr(
        anti_repeat_effects, "open"
    ) else open

    def _sharing_violation(*args, **kwargs):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr("builtins.open", _sharing_violation)
    try:
        with pytest.raises(OSError):
            reader.query_effects("Neko", 30)
    finally:
        monkeypatch.setattr("builtins.open", real_open)

    assert "Neko" not in reader._cache, (
        "an unreadable sidecar was cached as empty"
    )
    # And the real history is still intact once the file is readable again.
    reader.record_decision("Neko", _decision(), now=now + 1)
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    day = anti_repeat_effects._utc_day(now)
    assert persisted["daily_buckets"][day]["counters"]["detected"] == 6, (
        "the poisoned cache was flushed over real history; before=%s" % before[:80]
    )
    assert persisted["started_at"] == now


def test_reviving_a_retired_name_drops_the_deleted_identity_cache(tmp_path):
    """A decision recorded between retire and rmtree repopulates the cache.

    Lifting retirement while that entry is live would flush the DELETED
    character's aggregates -- and its ``started_at`` -- under the reused name.
    A live name still must NOT be evicted: that fences away a snapshot staged
    and not yet flushed, which is why revive is cache-preserving in general.
    """
    import shutil

    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    for _ in range(5):
        store.record_decision("Neko", _decision(), now=now)

    store.retire_character("Neko")
    # The in-flight turn that lands between the retire and the rmtree.
    store.record_decision("Neko", _decision(), now=now + 1)
    assert "Neko" in store._cache
    shutil.rmtree(tmp_path / "Neko")

    # The name is reused: a cloud download or snapshot import of the same name.
    store.revive_character("Neko")

    assert "Neko" not in store._cache, (
        "the deleted identity's aggregates survived into the reused name"
    )
    (tmp_path / "Neko").mkdir()
    store.record_decision("Neko", _decision(), now=now + 2)
    persisted = json.loads(
        (tmp_path / "Neko" / "anti_repeat_effects.json").read_text(encoding="utf-8")
    )
    totals = [
        bucket["counters"]["detected"]
        for bucket in persisted["daily_buckets"].values()
    ]
    assert sum(totals) == 1, (
        "the reused name inherited the deleted character's history: %s" % persisted
    )


def test_reviving_a_live_name_keeps_its_cache_and_fence(tmp_path):
    """The cache-preserving half must survive the retired-name fix.

    A cloud import of a name that was never retired must not evict: the apply
    does not rewrite this sidecar, and fencing would discard a staged write.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    store.record_decision("Neko", _decision(), now=now)
    staged = store.stage_decision("Neko", _decision(), now=now + 1)

    store.revive_character("Neko")

    assert "Neko" in store._cache, "revive evicted a live identity"
    store._flush_snapshot(*staged)
    persisted = json.loads(
        (tmp_path / "Neko" / "anti_repeat_effects.json").read_text(encoding="utf-8")
    )
    day = anti_repeat_effects._utc_day(now)
    assert persisted["daily_buckets"][day]["counters"]["detected"] == 2, (
        "revive fenced away the staged write"
    )


def test_a_stat_denied_path_is_not_mistaken_for_an_absent_file(
    tmp_path, monkeypatch
):
    """`os.path.exists` answers False on a permission-denied stat.

    That probe used to sit above the open, so a path whose STAT is denied took
    the absent-file branch even though the file was readable -- its empty
    payload was cached and then flushed over real history, the very loss the
    OSError split was added to prevent, reached by a different door.

    The discriminator has to be a denied STAT with a working OPEN. Making
    `open` raise instead proves nothing: with the probe restored the file still
    exists, so the probe passes and the error propagates either way.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    for _ in range(5):
        store.record_decision("Neko", _decision(), now=now)

    reader = _store(tmp_path)
    monkeypatch.setattr(
        anti_repeat_effects.os.path, "exists", lambda _path: False
    )

    reader.query_effects("Neko", 30)

    # `started_at` is the discriminator. `source_available` is windowed by day
    # and the daily buckets are pruned by retention, so both read the same
    # either way for backdated decisions -- but a default payload stamps
    # `started_at` with the current time, and the real file carries the
    # original. It is also the field the on-disk repro showed being clobbered.
    assert reader._cache["Neko"]["started_at"] == now, (
        "a stat-denied but readable sidecar was loaded as a fresh empty payload"
    )
    # The flush-over-history half is covered by
    # test_a_transient_read_failure_is_not_cached_as_empty. Asserting it here
    # too would be wrong: query_effects prunes by real time, so these backdated
    # buckets are legitimately dropped from the cache first.


def test_a_missing_sidecar_still_reads_as_empty(tmp_path):
    """The absent case must keep working without the exists() probe.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()

    effects = store.query_effects("Neko", 30)

    assert effects["source_available"] is False
    assert "Neko" in store._cache


_SIGNATURE_CONTAINER_DRAFTS = [
    ("relative link target", "we always say [endpoint](/api/secret_helper) ok"),
    (
        "wrapped html comment",
        "we always say <!--" + chr(10) + "secret_helper" + chr(10) + "--> ok",
    ),
    (
        "list-nested indented code",
        "sure" + chr(10) + "-     secret_helper = 1" + chr(10) + "done",
    ),
]


@pytest.mark.parametrize(
    "label, draft",
    _SIGNATURE_CONTAINER_DRAFTS,
    ids=[row[0] for row in _SIGNATURE_CONTAINER_DRAFTS],
)
def test_container_bodies_never_reach_the_sidecar(label, draft):
    """These three shapes were reaching the PERSISTED signature, not just reports.

    `_without_protected_text` shares `_runtime_protected_spans` with the miner,
    so a container the miner does not know about is one the sidecar will accept
    evidence from. Asserted here as well as in the miner tests because the two
    paths have drifted before -- the template alternatives were fixed in the
    miner while the signature copy kept leaking.
    """
    assert build_repeat_signature(
        draft, ["secret_helper"], language="en"
    ) is None, label


def test_a_backward_clock_step_does_not_delete_recorded_effects(tmp_path):
    """A record dated ahead of the clock means the CLOCK moved, not corruption.

    A manual correction, a VM restore or a stepped time sync all leave every
    existing record "newer than now". ``_prune`` runs on every query and every
    recorded decision and its result is flushed, so deleting on that reading
    permanently destroyed the message-linked aggregates -- and a correction
    across midnight took a whole day of daily buckets with them.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0
    recorded_day = anti_repeat_effects._utc_day(now)

    store.record_decision("Neko", _decision(response_id="turn"), now=now)
    assert store.stage_response_delivered("Neko", "turn", now=now) is not None

    # Far enough back to cross a UTC midnight, still inside the skew window.
    stepped_back = now - 23 * 60 * 60
    linked = store.query_effects_for_responses(
        "Neko", ["turn"], 100, now=stepped_back
    )

    assert linked["linked_message_count"] == 1
    assert recorded_day in store._cache["Neko"]["daily_buckets"]


def test_records_beyond_the_skew_window_are_still_dropped(tmp_path):
    """The tolerance is bounded on purpose.

    Past the window a timestamp cannot be explained by clock skew, and keeping
    it would let the record outlive the retention period entirely.
    """
    store = _store(tmp_path)
    (tmp_path / "Neko").mkdir()
    now = 1_700_000_000.0

    store.record_decision("Neko", _decision(response_id="turn"), now=now)
    assert store.stage_response_delivered("Neko", "turn", now=now) is not None

    beyond = now - (anti_repeat_effects.FUTURE_SKEW_SECONDS + 60 * 60)
    linked = store.query_effects_for_responses("Neko", ["turn"], 100, now=beyond)

    assert linked["linked_message_count"] == 0


_DECORATED_WHOLE_DRAFT_CASES = [
    ("plain", "quiet lantern"),
    ("emoji tail", "quiet lantern \U0001F60A"),
    ("emoji both ends", "\U0001F60Aquiet lantern\U0001F60A"),
    ("ascii elongation", "quiet lantern~~~"),
    ("fullwidth elongation", "quiet lantern\uff5e\uff5e"),
    ("sentence punctuation", "quiet lantern!!!"),
    ("protected url tail", "quiet lantern https://a.com/x"),
    ("code tail", "quiet lantern `x = 1`"),
]


@pytest.mark.parametrize(
    "label, draft",
    _DECORATED_WHOLE_DRAFT_CASES,
    ids=[row[0] for row in _DECORATED_WHOLE_DRAFT_CASES],
)
def test_a_decorated_whole_draft_is_still_the_whole_draft(label, draft):
    """The signature may never be the entire rejected reply.

    A plain equality test against the draft was defeated by whatever the reply
    happened to end with -- an emoji, an elongation mark, a protected URL --
    so the sidecar could retain essentially the complete text. Trailing "!!!"
    was rejected only because ``_EDGE_PUNCTUATION`` lists it, which is exactly
    why a fixed literal set is the wrong tool.
    """
    assert build_repeat_signature(draft, ["quiet lantern"], language="en") is None, label


def test_a_genuine_partial_still_builds_a_signature():
    """The dual: narrowing the boundary must not silence real evidence."""
    assert build_repeat_signature(
        "quiet lantern and more prose", ["quiet lantern"], language="en"
    ) == RepeatSignature("quiet lantern", "quiet lantern", "en")
    assert build_repeat_signature(
        "我们一起去吃饭吧真的好开心", ["我们一起去吃饭吧"], language="zh-CN"
    ) == RepeatSignature("我们一起去吃饭吧", "我们一起去吃饭吧", "zh-CN")


def _retirement_probe(tmp_path, monkeypatch, *, order):
    """Drive a delete through the module helpers in a given ordering."""
    import time

    from memory import anti_repeat_effects

    (tmp_path / "Ghost").mkdir()
    config_manager = MagicMock()
    config_manager.memory_dir = str(tmp_path)
    config_manager.app_docs_dir = str(tmp_path)
    config_manager.load_root_state.return_value = {"mode": "normal"}

    monkeypatch.setattr(anti_repeat_effects, "_GLOBAL_STORE", None)
    monkeypatch.setattr(anti_repeat_effects, "_PENDING_RETIREMENTS", set())

    if order == "retire_before_store":
        anti_repeat_effects.retire_cached_anti_repeat_effects("Ghost")
        shutil.rmtree(tmp_path / "Ghost")
    elif order == "retire_then_revive":
        anti_repeat_effects.retire_cached_anti_repeat_effects("Ghost")
        anti_repeat_effects.revive_cached_anti_repeat_effects("Ghost")
        # The directory goes too, so the assertion has teeth: a LIVE name is
        # allowed to recreate it, a retired one is not. Without the removal a
        # stuck retirement is invisible, because the surviving directory is
        # writable either way.
        shutil.rmtree(tmp_path / "Ghost")

    # Build through the REAL factory. Seeding the store by hand here would
    # bypass the very wiring under test: with the seeding line removed from
    # get_anti_repeat_effect_store, a hand-seeded fixture stayed green.
    monkeypatch.setattr(
        anti_repeat_effects, "get_config_manager", lambda: config_manager
    )
    store = anti_repeat_effects.get_anti_repeat_effect_store()

    anti_repeat_effects.record_anti_repeat_decision(
        "Ghost",
        anti_repeat_effects.AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        ),
    )
    deadline = time.monotonic() + 15.0
    while store._detached_flushes:
        # Failing loudly matters: returning the directory state on timeout
        # would let a slow flush decide the verdict, and both of these
        # assertions read "directory absent" as the fixed behaviour.
        assert time.monotonic() < deadline, "the detached flush never drained"
        time.sleep(0.02)
    return (tmp_path / "Ghost").exists()


def test_retirement_recorded_before_the_store_exists_still_blocks_resurrection(
    tmp_path, monkeypatch
):
    """Delete and rename retire the identity BEFORE removing the tree.

    The store is built lazily on the first detector hit, so a generation
    already in flight could construct one with an empty retirement set, whose
    first flush calls ``ensure_character_dir`` and puts the deleted directory
    straight back -- the exact resurrection ``_write_file_path`` exists to
    prevent. Measured before the fix: the directory and its sidecar both came
    back.
    """
    assert _retirement_probe(tmp_path, monkeypatch, order="retire_before_store") is False


def test_a_revived_name_does_not_stay_pending(tmp_path, monkeypatch):
    """The dual: revival lifts retirement, so it must clear the pending record.

    A revived name is LIVE, and a live name may create its own directory --
    an imported profile that ships no managed memory files has none yet. If
    the pending record survived the revival, its aggregates would silently
    stop persisting while the character is in active use.
    """
    assert _retirement_probe(tmp_path, monkeypatch, order="retire_then_revive") is True


def test_a_live_name_is_unaffected(tmp_path, monkeypatch):
    assert _retirement_probe(tmp_path, monkeypatch, order="none") is True


class _WindowedPendingSet:
    """A pending set that opens a window AFTER it has been copied.

    Not a ``set`` subclass on purpose: ``set.update`` takes an internal fast
    path for set-like arguments and would never call ``__iter__``, so the
    window would never open.
    """

    def __init__(self, copied, retirer_done):
        self._names: set[str] = set()
        self._copied = copied
        self._retirer_done = retirer_done

    def add(self, name):
        self._names.add(name)

    def discard(self, name):
        self._names.discard(name)

    def __contains__(self, name):
        return name in self._names

    def __iter__(self):
        snapshot = iter(tuple(self._names))
        self._copied.set()
        # Under one shared lock the retiring thread cannot get here, so this
        # times out and the builder publishes -- correct. Under two locks it
        # completes, and the builder then publishes a store seeded from the
        # snapshot taken above, without the retirement.
        self._retirer_done.wait(1.0)
        return snapshot


def test_pending_retirement_transfer_is_atomic_with_singleton_publication(
    tmp_path, monkeypatch
):
    """The builder must not publish an instance seeded from a stale set.

    The dangerous window is between COPYING the pending set and assigning the
    global. A concurrent retire that slips into it reads ``None``, returns
    early, and leaves its update only in the set already copied -- so the
    published store carries no retirement and the deleted directory comes
    back. Both sides take the SAME lock, which is what closes it.

    Pinning this needed the window in the right place: an earlier version
    paused before the copy instead, which is the benign ordering, and it
    stayed green with the two-lock version restored.
    """
    import threading

    from memory import anti_repeat_effects

    config_manager = MagicMock()
    config_manager.memory_dir = str(tmp_path)
    config_manager.app_docs_dir = str(tmp_path)
    config_manager.load_root_state.return_value = {"mode": "normal"}

    copied = threading.Event()
    retirer_done = threading.Event()
    monkeypatch.setattr(anti_repeat_effects, "_GLOBAL_STORE", None)
    monkeypatch.setattr(
        anti_repeat_effects,
        "_PENDING_RETIREMENTS",
        _WindowedPendingSet(copied, retirer_done),
    )
    monkeypatch.setattr(
        anti_repeat_effects, "get_config_manager", lambda: config_manager
    )

    built: list = []

    def _build() -> None:
        built.append(anti_repeat_effects.get_anti_repeat_effect_store())

    def _retire() -> None:
        anti_repeat_effects.retire_cached_anti_repeat_effects("Ghost")
        retirer_done.set()

    builder = threading.Thread(target=_build)
    builder.start()
    assert copied.wait(10.0), "the builder never reached the pending-set copy"
    retirer = threading.Thread(target=_retire)
    retirer.start()
    builder.join(20.0)
    retirer.join(20.0)
    assert not builder.is_alive() and not retirer.is_alive()

    store = built[0]
    assert "Ghost" in store._retired, (
        "the retirement was lost between copying the pending set and publishing"
    )


def test_a_fragment_carrying_a_protected_shape_is_refused():
    """_PROTECTED_RE's only remaining reader, and nothing was holding it.

    Before the sidecar delegated span masking to the miner, the parity rows
    exercised this pattern because ``_without_protected_text`` built its span set
    from it. Now they compare the miner against itself, and making
    ``_PROTECTED_RE`` a never-matching pattern left the whole suite green --
    while it is still the only gate deciding whether an evidence FRAGMENT may be
    persisted for 120 days.

    One row per alternative, so a single alternative going missing is caught
    rather than averaged away.
    """
    ticks = chr(96) * 3
    rows = {
        "fenced": ticks + chr(10) + "token = 'abc'" + chr(10) + ticks,
        "inline code": chr(96) + "token = 'abc'" + chr(96),
        "handlebars": "{{ token }}",
        "jinja statement": "{% set token = 'abc' %}",
        "jinja comment": "{# token #}",
        "shell interpolation": "${token}",
        "erb": "<% token %>",
        "tag": "<div class=secret>",
        "placeholder": "[SECRET_TOKEN]",
    }
    for label, fragment in rows.items():
        assert anti_repeat_effects._safe_fragment(fragment) == "", label

    # The dual, so this cannot pass by refusing everything: ordinary speech and
    # a bare word survive the same check.
    assert anti_repeat_effects._safe_fragment("\u6211\u4eec\u4e00\u8d77\u53bb\u516c\u56ed\u6563\u6b65\u5427") != ""
    assert anti_repeat_effects._safe_fragment("token") != ""


_MASKING_PARITY_CASES = [
    ("emoticon pair", "I love it <3 you are so cute >_< really"),
    ("comparisons", "记住哦 3 < 5，我们一起去吃饭吧，10 > 7 呢"),
    ("arrows", "心情 <- 超好，我们一起去吃饭吧 ->"),
    ("real inline tag", "a <b>SECRET_TOKEN</b> b"),
    ("real code container", "see <code>SECRET_TOKEN</code> ok"),
    ("unterminated code container", "use <code> then SECRET_TOKEN"),
    ("attributed tag", "看看 <div class=x> 好不好"),
    # A container OPENER inside a runtime span, with its closer outside it.
    # The miner filters such a match out; this side did not, so the match
    # ran from inside the code to the closer and blanked the speech between
    # them. Every row above has the whole match either inside or outside a
    # runtime span, which is why they agreed while these did not.
    ("url then template opener",
     "看看 https://example.com/a${x 我们一起去吃饭吧 } 好不好"),
    ("comment holding a template opener",
     "<!-- {{ --> 我们一起去吃饭吧 }} 好不好"),
    ("raw-text container holding a template opener",
     "<code>{{</code> 我们一起去吃饭吧 }} 好不好"),
    # Adding the miner's _starts_inside filter here and keeping a separate
    # sweep is NOT the same fix, and this row is where they part. Once one
    # of the two alternatives _PROTECTED_RE carries and _TEMPLATE_RE does
    # not (the inline-code form) has fired, finditer resumes at a different
    # offset than the miner's sweep, and the two go on to find different
    # matches -- here, whether the stray </code> is masked at all.
    ("inline code shifts the sweep off the miner's offsets",
     "`看 {{`\n</code> 我们一起去吃饭吧 }} 好不好"),
    ("indented code holding a template opener",
     "hi\n\n    tpl {{\n我们一起去吃饭吧 }} ok"),
]


@pytest.mark.parametrize(
    "label, text",
    _MASKING_PARITY_CASES,
    ids=[row[0] for row in _MASKING_PARITY_CASES],
)
def test_the_miner_and_the_sidecar_mask_the_same_text(label, text):
    """The two masking paths are supposed to agree, and drifted apart once.

    The miner's ``<...>`` alternative was tightened to require a tag shape and
    this one was not, so the sidecar went on pairing the "<" of "<3" with the
    ">" of ">_<". It masked the speech between them and dropped a signature the
    miner reported happily -- on one of the commonest shapes in this project's
    speech.

    Asserting only "the sidecar no longer masks emoticons" would be rescued by
    deleting the alternative outright, which would stop masking real tags. This
    compares the two paths instead, so they can only pass together. Whitespace
    is normalised because the sidecar substitutes a blank where the miner drops
    the span; the surviving text is what has to match.
    """
    import re

    from utils.natural_expression_candidates import _protected_spans

    spans = _protected_spans(text)
    mined = "".join(
        char for index, char in enumerate(text)
        if not any(start <= index < end for start, end in spans)
    )
    masked = anti_repeat_effects._without_protected_text(text)
    normalise = lambda value: re.sub(r"\s+", " ", value).strip()  # noqa: E731

    assert normalise(mined) == normalise(masked), label


def test_a_container_opener_anywhere_costs_the_signature():
    """A stray opener beside real speech now drops the whole draft.

    This once pinned the opposite: the sidecar and the miner had drifted, and
    evidence sitting after a stray opener landed unattributed. Both sides ask
    one detector now, so there is no drift left to catch -- what is asserted
    instead is the decision itself, which is what the panel counts.
    """
    drafts = (
        "看看 https://example.com/a${x 我们一起去吃饭吧 } 好不好",
        "<!-- {{ --> 我们一起去吃饭吧 }} 好不好",
        "<code>{{</code> 我们一起去吃饭吧 }} 好不好",
    )
    for draft in drafts:
        assert build_repeat_signature(
            draft, ["我们一起去吃饭吧"], language="zh",
        ) is None, draft

    # The dual, and the half that still has something to fail: the same
    # catchphrase with no opener anywhere still signs.
    signature = build_repeat_signature(
        "看看那边的猫咪 我们一起去吃饭吧 好不好", ["我们一起去吃饭吧"], language="zh",
    )
    assert signature is not None
    assert signature.phrase == "我们一起去吃饭吧"

    # The dual, and the direction that would matter if it broke: a payload
    # genuinely inside a container is still refused.
    assert build_repeat_signature(
        "run ```\nSECRET_TOKEN=abc\n``` now",
        ["SECRET_TOKEN=abc"],
        language="en",
    ) is None
    assert build_repeat_signature(
        "see {{ SECRET_TOKEN }} ok", ["SECRET_TOKEN"], language="en",
    ) is None


def test_the_dropped_conversational_signature_comes_back():
    """The user-visible half of the same drift."""
    assert build_repeat_signature(
        "I love it <3 you are so cute >_< really",
        ["you are so cute"],
        language="en",
    ) == RepeatSignature("you are so cute", "you are so cute", "en")


_SIDECAR_STORE_MODULES = (
    ("memory.anti_repeat", "AntiRepeatCorpus", "anti_repeat_corpus.json"),
    ("memory.anti_repeat_effects", "AntiRepeatEffectStore", "anti_repeat_effects.json"),
    (
        "memory.startup_greeting_history",
        "StartupGreetingHistory",
        "startup_greetings.json",
    ),
)


@pytest.mark.parametrize(
    "module_name, class_name, filename",
    _SIDECAR_STORE_MODULES,
    ids=[row[0].rsplit(".", 1)[-1] for row in _SIDECAR_STORE_MODULES],
)
def test_a_retired_name_never_writes_outside_its_own_directory(
    module_name, class_name, filename, tmp_path
):
    """All three sidecar stores must refuse the same paths, not just one.

    These three carry the same write path three times over, and this PR has
    now fixed the same defect in one copy and left the others four separate
    times. Parametrising over the modules is what makes the next divergence
    fail here rather than in review: "." resolves to the memory ROOT, which
    always exists, so the is-a-directory check alone let a retired identity
    drop its sidecar straight into memory/.
    """
    import importlib
    import os

    module = importlib.import_module(module_name)
    config_manager = MagicMock()
    config_manager.memory_dir = str(tmp_path)
    config_manager.app_docs_dir = str(tmp_path)

    store = getattr(module, class_name).__new__(getattr(module, class_name))
    store._config_manager = config_manager

    # BOTH branches: the live path calls ensure_character_dir, so it is the
    # one that would actually create the stray directory.
    unsafe = (".", "..", "a/b", "a" + chr(92) + "b", "./x", "../y")
    for retired in (set(), set(unsafe)):
        store._retired = set(retired)
        state = "retired" if retired else "live"
        for name in unsafe:
            try:
                target = store._write_file_path(name)
            except OSError:
                target = "the OS refused the name, but the store still offered it"
            assert target is None, (
                f"{module_name} ({state}) would have written {name!r} to {target}"
            )
    store._retired = set()

    # The dual: a retired name whose own directory exists still writes there,
    # which is what keeps a rescued rename from losing its aggregates.
    (tmp_path / "Live").mkdir()
    store._retired = {"Live"}
    live_target = store._write_file_path("Live")
    assert live_target is not None
    assert os.path.dirname(live_target) == str(tmp_path / "Live")
    assert os.path.basename(live_target) == filename


def test_the_separator_rule_holds_under_posix_path_semantics(monkeypatch):
    """The name check must not depend on which separator the platform has.

    On Windows a backslash IS a separator, so normalisation alone already
    refuses a name carrying one and the explicit check never fires. On
    POSIX the same character is an ordinary filename character: the name
    arrives as a legal DIRECT child of the memory root, every structural
    check passes it, and the store creates a directory literally named
    with it. The same profile would then resolve to a nested path once it
    moved to Windows.

    So this runs the REAL helper with the path flavour swapped, rather
    than reimplementing the rule against posixpath -- a copy would stay
    green while the shipped helper regressed. Only the module-level "os"
    name is replaced, so nothing outside the helper is affected.
    """
    import posixpath

    import memory as memory_pkg

    class _PosixOs:
        path = posixpath

    monkeypatch.setattr(memory_pkg, "os", _PosixOs)

    root = "/tmp/mem"
    refused = (
        ".",
        "..",
        "a/b",
        "a" + chr(92) + "b",
        "./x",
        "../y",
    )
    for name in refused:
        assert not memory_pkg._is_within_memory_root(
            root, name, posixpath.join(root, name)
        ), f"POSIX would have accepted {name!r} as a character directory"

    # The dual, so the rule cannot pass by refusing everything.
    assert memory_pkg._is_within_memory_root(
        root, "Neko", posixpath.join(root, "Neko")
    )


def test_a_symlinked_character_directory_is_refused(monkeypatch):
    """Containment has to survive a link, which abspath cannot see.

    abspath is pure string arithmetic: it normalises "." and ".." and
    stops there. A memory/<name> that is a symlink to a directory
    somewhere else therefore still reads as a direct child, and the
    sidecar is written THROUGH the link -- outside the tree the rule
    exists to keep it inside. Only realpath resolves it.

    The link is injected rather than created, because making a real one
    on Windows needs a privilege CI does not grant, and a skipped test
    would leave the fix unverified on the machine it was written on.
    The helper under test is the real one; only path resolution is
    substituted.
    """
    import posixpath

    import memory as memory_pkg

    root = "/tmp/mem"
    linked = posixpath.join(root, "Linked")

    class _Path:
        """posixpath, except that "Linked" points out of the tree."""

        def __getattr__(self, item):
            return getattr(posixpath, item)

        @staticmethod
        def realpath(path):
            if path == linked:
                return "/elsewhere/Linked"
            return posixpath.normpath(path)

        @staticmethod
        def abspath(path):
            # Present so that reverting the fix to abspath still runs --
            # and fails, which is the point.
            return posixpath.normpath(path)

    class _Os:
        path = _Path()

    monkeypatch.setattr(memory_pkg, "os", _Os)

    assert not memory_pkg._is_within_memory_root(root, "Linked", linked), (
        "a symlinked character directory was accepted as a direct child"
    )
    # The dual: an ordinary directory under the same root still passes.
    assert memory_pkg._is_within_memory_root(
        root, "Neko", posixpath.join(root, "Neko")
    )


@pytest.mark.parametrize(
    "module_name, class_name, filename",
    _SIDECAR_STORE_MODULES,
    ids=[row[0].rsplit(".", 1)[-1] for row in _SIDECAR_STORE_MODULES],
)
def test_the_fence_target_names_the_file_actually_written(
    module_name, class_name, filename, tmp_path
):
    """The cloud-save fence target must name the real file.

    It was a separate literal from the write path, and two of the three stores
    had already drifted -- `anti_repeat.json` for a store that writes
    `anti_repeat_corpus.json`, `startup_greeting_history.json` for one that
    writes `startup_greetings.json`. The target is diagnostics only, so the
    cost was a MaintenanceModeError, and a client payload, naming a file that
    does not exist.
    """
    import importlib
    import os
    from unittest.mock import patch

    from utils.cloudsave_runtime import MaintenanceModeError

    module = importlib.import_module(module_name)
    assert module._SIDECAR_FILENAME == filename

    config_manager = MagicMock()
    config_manager.memory_dir = str(tmp_path)
    config_manager.app_docs_dir = str(tmp_path)
    # Built through the real constructor: _flush_snapshot needs the lock and
    # sequence state that __new__ would skip.
    with patch.object(module, "get_config_manager", lambda: config_manager):
        store = getattr(module, class_name)()
    store._config_manager = config_manager

    written = store._write_file_path("Neko")
    assert os.path.basename(written) == filename

    captured = {}

    def _capture(_config_manager, *, operation="write", target=""):
        captured["target"] = target
        raise MaintenanceModeError("maintenance_readonly", operation=operation, target=target)

    with patch("utils.cloudsave_runtime.cloudsave_writable_transaction", _capture):
        try:
            store._flush_snapshot("Neko", {}, 1)
        except Exception:
            pass

    assert captured.get("target") == f"memory/Neko/{filename}", (
        f"{module_name} announced {captured.get('target')!r} but writes {filename!r}"
    )


def test_eviction_is_what_keeps_a_reused_name_from_inheriting_old_data(
    tmp_path,
):
    """The carry-over is held shut by the callers, so pin their mechanism.

    A record made between the retirement and the rmtree reloads the deleted
    identity into the cache. If the directory is then recreated for a
    different character of the same name, the next write carries that data
    in. Nothing in the store stops it -- every route that recreates the
    directory lifts retirement first, and lifting drops the cache.

    So this asserts on the mechanism those routes share,
    ``evict_character_runtime_caches``, with the no-eviction control right
    next to it. Without the control the test would pass for the wrong
    reason -- a scenario that never contaminates in the first place looks
    identical to one the eviction cleaned.

    On FILE CONTENTS, not on the file existing: the guards at
    ``test_a_retired_name_never_writes_outside_its_own_directory`` and
    ``test_a_retired_name_writes_again_only_once_a_directory_exists`` assert
    existence, which is why this slipped past them.
    """
    import json
    import shutil

    import memory.anti_repeat_effects as effects_module
    from utils.character_memory import evict_character_runtime_caches

    name = "Reused"
    decision = effects_module.AntiRepeatDecision(
        source="proactive",
        reasons=("bm25",),
        action="block",
        outcome="blocked_initial",
    )

    def detected_in_file(root):
        path = root / name / "anti_repeat_effects.json"
        if not path.exists():
            return 0
        payload = json.loads(path.read_text(encoding="utf-8"))
        return sum(
            bucket["counters"]["detected"]
            for bucket in payload["daily_buckets"].values()
        )

    def run(root, *, evict):
        root.mkdir()
        (root / name).mkdir()
        config_manager = MagicMock()
        config_manager.memory_dir = str(root)
        config_manager.app_docs_dir = str(root)
        store = effects_module.AntiRepeatEffectStore()
        store._config_manager = config_manager

        previous = effects_module._GLOBAL_STORE
        effects_module._GLOBAL_STORE = store
        try:
            for tick in range(3):
                store.record_decision(
                    name, decision, now=1_700_000_000.0 + tick * 60
                )
            accumulated = detected_in_file(root)

            # Delete retires BEFORE it removes the tree, and a turn can
            # land in that window.
            store.retire_character(name)
            store.record_decision(name, decision, now=1_700_000_500.0)
            shutil.rmtree(root / name)

            if evict:
                evict_character_runtime_caches(name)

            # A different character, same name, directory back.
            (root / name).mkdir()
            store.record_decision(name, decision, now=1_700_100_000.0)
            return accumulated, detected_in_file(root)
        finally:
            effects_module._GLOBAL_STORE = previous

    # Control: without the lift, the old identity comes across.
    accumulated, carried = run(tmp_path / "control", evict=False)
    assert accumulated == 3
    assert carried > 1, (
        "the control did not contaminate, so this test cannot show that "
        "eviction is what prevents it"
    )

    # What every reuse route actually does.
    accumulated, after = run(tmp_path / "evicted", evict=True)
    assert accumulated == 3
    assert after == 1, (
        "a reused name inherited the deleted identity's data: %d entries"
        % after
    )


def test_a_reset_fails_loudly_when_the_sidecar_refuses_the_write(tmp_path):
    """Telling the user their statistics were cleared has to mean they were.

    ``_flush_snapshot`` RETURNS when ``_write_file_path`` refuses -- it does
    not raise -- so ``raise_on_error=True`` did not cover the refusal. The
    reset then passed both of its own checks: the generation is unchanged,
    because whatever retired the name bumped it BEFORE the reset captured
    it, and the staged sequence matches, because the refusal fenced it. The
    route reported success while the file still held every statistic, and a
    rolled-back delete or rename would put them back.

    Both refusal shapes are covered. The second only became reachable with
    the rename write fence, so the change that widened this is the one that
    has to pin it.
    """
    import json
    import shutil

    import memory.anti_repeat_effects as effects_module
    from utils.character_memory import (
        fence_character_runtime_writes,
        unfence_character_runtime_writes,
    )

    name = "Refused"
    decision = effects_module.AntiRepeatDecision(
        source="proactive",
        reasons=("bm25",),
        action="block",
        outcome="blocked_initial",
    )

    def build(root):
        root.mkdir(parents=True, exist_ok=True)
        config_manager = MagicMock()
        config_manager.memory_dir = str(root)
        config_manager.app_docs_dir = str(root)
        store = effects_module.AntiRepeatEffectStore()
        store._config_manager = config_manager
        store.record_decision(name, decision, now=1_700_000_000.0)
        return store

    def detected(root):
        path = root / name / "anti_repeat_effects.json"
        if not path.exists():
            return 0
        payload = json.loads(path.read_text(encoding="utf-8"))
        return sum(
            bucket["counters"]["detected"]
            for bucket in payload["daily_buckets"].values()
        )

    # CONTROL: an ordinary reset still succeeds and really does cut the file,
    # so the raises below are about the refusal and not about reset being
    # broken outright.
    ok_root = tmp_path / "ok"
    ok_store = build(ok_root)
    assert detected(ok_root) == 1
    ok_store.clear_effects(name)
    assert detected(ok_root) == 0

    # A: retired, and the directory already removed -- delete retires first
    # and only then rmtree's the tree.
    gone_root = tmp_path / "gone"
    gone_store = build(gone_root)
    gone_store.retire_character(name)
    shutil.rmtree(gone_root / name)
    with pytest.raises(RuntimeError):
        gone_store.clear_effects(name)

    # B: write-fenced by a rename in progress. The directory is still there,
    # so only the fence refuses.
    fenced_root = tmp_path / "fenced"
    fenced_store = build(fenced_root)
    assert detected(fenced_root) == 1
    fence_character_runtime_writes(name)
    try:
        with pytest.raises(RuntimeError):
            fenced_store.clear_effects(name)
    finally:
        unfence_character_runtime_writes(name)

    assert detected(fenced_root) == 1, (
        "the reset was refused, so the statistics must still be on disk -- "
        "reporting success here is what told the user otherwise"
    )


def test_a_newer_schema_version_is_never_overwritten(tmp_path):
    """An older build must not destroy a sidecar a newer one wrote.

    The version check treated anything but the known version exactly like an
    empty store, so ``_load_unlocked`` cached that emptiness and the next
    decision flushed a payload holding only itself over the whole file.
    Measured before the fix: 1837 bytes became 728, every prior day bucket
    gone, ``started_at`` reset, and not one log line. There is no backup
    anywhere in this store, so it was irreversible.

    A version we cannot read is the UNREADABLE case this file already
    distinguishes, not the corrupt one: the file is intact and
    authoritative. Raising skips the cache install and the staging that
    follows it, so the newer file simply stops being recorded into.
    """
    import json

    import memory.anti_repeat_effects as effects_module

    name = "Newer"
    root = tmp_path / "memory"
    (root / name).mkdir(parents=True)
    path = root / name / "anti_repeat_effects.json"

    from_the_future = {
        "version": effects_module._SCHEMA_VERSION + 1,
        "started_at": 1_600_000_000.0,
        "daily_buckets": {
            "2023-11-13": {"counters": {"detected": 7}},
        },
        "something_this_build_has_never_heard_of": True,
    }
    original = json.dumps(from_the_future, ensure_ascii=False, indent=2)
    path.write_text(original, encoding="utf-8")

    config_manager = MagicMock()
    config_manager.memory_dir = str(root)
    config_manager.app_docs_dir = str(root)
    store = effects_module.AntiRepeatEffectStore()
    store._config_manager = config_manager

    decision = effects_module.AntiRepeatDecision(
        source="proactive",
        reasons=("bm25",),
        action="block",
        outcome="blocked_initial",
    )
    # The STORE raises: that is what skips the cache install and the staging
    # that follows it in the same critical section. The production entry
    # point is what absorbs it -- asserted below, because "callers swallow"
    # is a claim about record_anti_repeat_decision and not about this method.
    with pytest.raises(ValueError):
        store.record_decision(name, decision, now=1_700_000_000.0)

    assert path.read_text(encoding="utf-8") == original, (
        "an older build rewrote a newer sidecar, and nothing anywhere kept "
        "a copy of what it replaced"
    )
    assert name not in store._cache, (
        "the unreadable payload was cached, so the next write would flush "
        "it over the file"
    )

    # And the production path absorbs it, so a downgrade degrades to "stops
    # recording" rather than an error reaching the reply pipeline.
    previous = effects_module._GLOBAL_STORE
    effects_module._GLOBAL_STORE = store
    try:
        effects_module.record_anti_repeat_decision(name, decision)
    finally:
        effects_module._GLOBAL_STORE = previous
    assert path.read_text(encoding="utf-8") == original

    # The dual: a file this build DOES understand still records normally,
    # so the check cannot pass by refusing everything.
    ours = root / "Ours"
    ours.mkdir()
    store.record_decision("Ours", decision, now=1_700_000_000.0)
    payload = json.loads(
        (ours / "anti_repeat_effects.json").read_text(encoding="utf-8")
    )
    assert payload["version"] == effects_module._SCHEMA_VERSION
    assert sum(
        bucket["counters"]["detected"]
        for bucket in payload["daily_buckets"].values()
    ) == 1


def test_the_draft_is_masked_before_it_is_normalized():
    """NFKC re-arms two delimiters the miner disables on purpose.

    It maps U+FF40 into a backtick and U+FF5E into a tilde. The miner treats
    neither as a delimiter deliberately: the fullwidth backtick is a kaomoji
    face part (recorded firing on 49.8% of 20k code-free replies) and a
    fullwidth tilde run is a divider rather than a fence, because an unclosed
    fence protects to end of text.

    Normalizing before masking put both back on the runtime path -- and these
    are not exotic shapes, they are how this project's characters punctuate.
    Delegating the span set to the miner fixed WHICH text is protected; this is
    about which text the miner is asked about.
    """
    phrase = "我们一起去公园散步吧"
    kaomoji = "（｀・ω・´）"

    decorated = kaomoji + phrase + "，你说好不好呀" + kaomoji
    assert build_repeat_signature(decorated, [phrase], language="zh") is not None, (
        "a fullwidth-backtick kaomoji masked the speech between two of them"
    )

    divider = (
        "今天好开心呀" + chr(10) + "～～～" + chr(10)
        + phrase + "，明天也要一起哦"
    )
    assert build_repeat_signature(divider, [phrase], language="zh") is not None, (
        "a fullwidth tilde divider opened a fence and silenced the rest"
    )

    # The ASCII spellings always worked, which is what showed the normalization
    # step rather than the phrase was deciding.
    assert build_repeat_signature(
        "(・ω・)" + phrase + "，好不好", [phrase], language="zh"
    ) is not None

    # The duals, so this cannot pass by never masking anything: a reply
    # carrying a fence does not sign, whether or not that fence is closed.
    ticks = chr(96) * 3
    secret = "API_KEY = 'sk-live-x'"
    assert build_repeat_signature(
        ticks + chr(10) + secret + chr(10) + ticks, [secret], language="en"
    ) is None, "a fenced secret became a persisted signature"
    # Speech after a CLOSED fence goes too. Locating that closer is exactly
    # the work the detector gave up, and the reply carries a fence either way.
    assert build_repeat_signature(
        ticks + chr(10) + "code" + chr(10) + ticks + chr(10) + chr(10)
        + "今天天气真好呀，" + phrase + "，你说好不好呢",
        [phrase], language="zh",
    ) is None, "a fenced reply must not sign, closed or not"


def test_a_fenced_write_drops_its_staged_record_from_the_cache(tmp_path):
    """A write refused by the RENAME fence must invalidate, or two mix.

    Fencing the sequence alone left the refused record in ``_cache``. A writer
    that stages after the target cache is reactivated but before the rename
    fence comes off lands in the refusal branch; once the fence lifted, the
    next legitimate decision snapshotted that cache and persisted the previous
    owner's record under the renamed-TO identity.
    """
    from utils.character_memory import (
        fence_character_runtime_writes,
        unfence_character_runtime_writes,
    )

    store = _store(tmp_path)
    staged = store.stage_decision(
        "Previous",
        AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        ),
        now=1_700_000_000.0,
    )

    fence_character_runtime_writes("Previous")
    try:
        store._flush_snapshot(*staged)
    finally:
        unfence_character_runtime_writes("Previous")

    assert "Previous" not in store._cache, (
        "the refused record stayed staged and would be republished"
    )


def test_retirement_refusal_keeps_accumulating_in_memory(tmp_path):
    """The dual, and the reason the invalidation is narrowed to the fence.

    Retirement refuses the WRITE, but the records are still this character's
    own -- the panel answers for a retired name from the cache. Popping on
    every refusal, not just the lifecycle fence, emptied that.
    """
    store = _store(tmp_path)
    staged = store.stage_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        ),
        now=1_700_000_000.0,
    )
    store.retire_character("Neko")
    store._flush_snapshot(*staged)

    store.record_decision(
        "Neko",
        AntiRepeatDecision(
            source="proactive",
            reasons=("literal_similarity",),
            action="regenerate",
            outcome="regen_guard_passed",
        ),
        now=1_700_000_001.0,
    )
    assert store.query_effects("Neko", 30, now=1_700_000_001.0)["totals"][
        "detected"
    ] == 1
