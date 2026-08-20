from __future__ import annotations

import json
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
    rename_character_memory_storage,
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


def test_build_repeat_signature_keeps_same_fragment_when_it_also_appears_in_prose():
    signature = build_repeat_signature(
        "run `secret_code()` then discuss secret_code in prose",
        ["secret_code"],
        language="en",
    )

    assert signature is not None
    assert signature.normalized_phrase == "secret_code"


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

    store.evict_character("Neko")
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
