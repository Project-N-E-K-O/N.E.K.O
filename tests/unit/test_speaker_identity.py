"""Pure-function layer of the two-layer speaker identity (``memory/identity.py``).

Zero IO here on purpose: everything stateful belongs to ``test_trust_store.py``.
"""
from __future__ import annotations

import math
import random

import pytest

from memory.identity import (
    account_platform,
    activity_count_cap,
    apply_signal_event,
    derive_conversation_id,
    derive_entity_id,
    effective_trust,
    is_conversation_id,
    is_entity_id,
    merge_order_key,
    normalize_account_id,
    normalize_account_record,
    normalize_channel,
    normalize_legacy_profile,
    record_activity,
)
from memory.speaker_trust import stable_speaker_id


def _fresh(account_id: str = "qq:1") -> dict:
    return normalize_account_record(account_id)


# ── account_id is stable_speaker_id, byte for byte ──────────────────────────

def test_normalize_account_id_is_exactly_stable_speaker_id():
    """Any drift here silently invalidates three SHA256 ledgers at once."""
    for candidate in (
        "qq:123456", "QQ:AbC", " qq:1 ", "bilibili:9", "qq:a:b", "qq:a@b",
        "", None, "noseparator", "qq:", ":123", "qq:with space",
        "qq:" + "x" * 200, "qq:​1",
    ):
        assert normalize_account_id(candidate) == stable_speaker_id(candidate)


def test_account_id_case_is_never_folded():
    """``qq:ABC`` and ``qq:abc`` are two accounts and two entities.

    Folding case here while fact rows keep both spellings would make every
    speaker_id equality comparison in the repo miss.
    """
    upper = normalize_account_id("qq:ABC")
    lower = normalize_account_id("qq:abc")
    assert upper == "qq:ABC" and lower == "qq:abc"
    assert derive_entity_id(upper) != derive_entity_id(lower)


def test_platform_segment_is_lowercased_but_actor_is_not():
    assert normalize_account_id("QQ:AbC") == "qq:AbC"
    assert account_platform("qq:AbC") == "qq"


# ── namespace disjointness is provable, not conventional ────────────────────

def test_entity_and_account_namespaces_are_bidirectionally_disjoint():
    rng = random.Random(20260805)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:@-"
    for _ in range(400):
        actor = "".join(
            rng.choice(alphabet) for _ in range(rng.randint(1, 40))
        )
        raw = f"qq:{actor}"
        account_id = normalize_account_id(raw)
        if account_id is not None:
            # An account id always contains a colon, so it can never match the
            # entity pattern.
            assert not is_entity_id(account_id)
            assert not is_conversation_id(account_id)
            entity_id = derive_entity_id(account_id)
            # ...and an entity id never contains one, so stable_speaker_id
            # bails out at its "no separator" branch.
            assert is_entity_id(entity_id)
            assert normalize_account_id(entity_id) is None
            conversation_id = derive_conversation_id(account_id)
            assert is_conversation_id(conversation_id)
            assert normalize_account_id(conversation_id) is None
            assert not is_entity_id(conversation_id)


def test_entity_id_is_deterministic_and_generation_separated():
    first = derive_entity_id("qq:1", 0)
    assert first == derive_entity_id("qq:1", 0)
    assert first != derive_entity_id("qq:1", 1)
    assert first != derive_entity_id("qq:2", 0)


# ── channel is anchored (Python ``re`` side of the two-layer regex trap) ────

@pytest.mark.parametrize("value", [
    "q#q", "中文", "a" * 17, "", "  ", None, "open open", "open;drop",
    "open\nnapcat", "op\ten",
])
def test_normalize_channel_rejects_everything_unanchored_would_accept(value):
    """An unanchored ``re.search`` would accept every one of these."""
    assert normalize_channel(value) is None


def test_normalize_channel_lowercases_and_accepts_the_two_real_values():
    assert normalize_channel("napcat") == "onebot"
    assert normalize_channel("OPEN") == "open"
    assert normalize_channel(" Open ") == "open"


@pytest.mark.parametrize("value", ["open\n", "\nopen", " open "])
def test_internal_normalizer_strips_surrounding_whitespace(value):
    """Deliberate asymmetry with the wire layer, and it is fail-safe.

    ``memory/identity.py`` runs on values this process produced (a ``ClassVar``
    on the connection object), so trimming whitespace is harmless tolerance.
    The wire model's ``\\A[a-z0-9_]{1,16}\\z`` does NOT trim — an untrusted
    ``speaker_channel`` carrying a newline is a 422 there. Strict at the trust
    boundary, tolerant inside it; never the other way round.
    """
    assert normalize_channel(value) == "open"


def test_wire_channel_pattern_rejects_what_the_internal_one_trims():
    """The two layers must NOT share a pattern string.

    ``Field(pattern=...)`` compiles with the Rust regex crate, which does not
    recognise ``\\Z`` and raises ``SchemaError`` at model-definition time, so
    the wire side must use ``\\A...\\z`` (lowercase) while ``memory/identity.py``
    on Python ``re`` uses ``\\A...\\Z``. Copying one string into the other
    layer is the way this bug comes back.
    """
    import pydantic

    from app.memory_server.routes import ScopedHistorySegment

    for bad in ("open\n", "\nopen", "OPEN", "中文", "a" * 17, "q#q"):
        with pytest.raises(pydantic.ValidationError):
            ScopedHistorySegment(
                input_history="[]",
                subject={"subject_kind": "participant", "subject_id": "qq:1"},
                speaker_label="x",
                speaker_channel=bad,
            )
    assert ScopedHistorySegment(
        input_history="[]",
        subject={"subject_kind": "participant", "subject_id": "qq:1"},
        speaker_label="x",
        speaker_channel="onebot",
    ).speaker_channel == "onebot"


def test_wire_activity_event_id_pattern_is_anchored():
    """The pre-existing unanchored pattern accepted literally everything.

    ``pattern=r"[A-Za-z0-9_.:-]+"`` is UNANCHORED SEARCH under pydantic v2, so
    a raw participant identity carrying a space (any character name with one)
    and values containing newlines both passed — i.e. the documented guard was
    empty. The rejected inputs below are the evidence.
    """
    import pydantic

    from app.memory_server.routes import ActivityEvent

    for bad in (
        "participant:猫娘 A:12:34:56", "x\nyyyyyyy", "good.id\n", "has space",
        "short",
    ):
        with pytest.raises(pydantic.ValidationError):
            ActivityEvent(id=bad)
    assert ActivityEvent(id="activity_abcdef0123").count == 1


# ── the scoring formula: both clamps outside their sums, applied once ───────

def test_activity_cap_reaches_the_declared_max_bonus():
    """I-T-3: any constant drift must fail loud rather than silently re-cap."""
    from config import (
        SPEAKER_TRUST_ACTIVITY_MAX_BONUS,
        SPEAKER_TRUST_ACTIVITY_WEIGHT,
    )

    cap = activity_count_cap()
    assert cap == math.ceil(
        SPEAKER_TRUST_ACTIVITY_MAX_BONUS / SPEAKER_TRUST_ACTIVITY_WEIGHT
    )
    assert cap * SPEAKER_TRUST_ACTIVITY_WEIGHT >= SPEAKER_TRUST_ACTIVITY_MAX_BONUS


def test_effective_trust_supremum_is_independent_of_partition_size():
    """I-T-2 / I-S1-1: sup = base + 0.30 + 0.02 no matter how many accounts."""
    from config import (
        SPEAKER_TRUST_ACTIVITY_MAX_BONUS,
        SPEAKER_TRUST_ADJUSTMENT_LIMIT,
    )

    cap = activity_count_cap()
    for accounts in range(1, 9):
        raw_adjustment = 0.6 * accounts
        raw_activity = cap * accounts
        assert effective_trust(0.3, raw_adjustment, raw_activity) == pytest.approx(
            min(
                1.0,
                0.3
                + SPEAKER_TRUST_ADJUSTMENT_LIMIT
                + SPEAKER_TRUST_ACTIVITY_MAX_BONUS,
            )
        )


def test_activity_bonus_cannot_be_multiplied_by_account_count():
    """Deleting the outer ``min`` would give 0.02*N; at N=8 that is > margin."""
    from config import (
        SPEAKER_TRUST_ACTIVITY_MAX_BONUS,
        SPEAKER_TRUST_ARBITRATION_MARGIN,
    )

    cap = activity_count_cap()
    bonus = effective_trust(0.0, 0.0, cap * 8) - effective_trust(0.0, 0.0, 0)
    assert bonus == pytest.approx(SPEAKER_TRUST_ACTIVITY_MAX_BONUS)
    assert bonus < SPEAKER_TRUST_ARBITRATION_MARGIN


def test_adjustment_clamp_sits_outside_the_sum_so_signs_do_not_cancel():
    """Clamp-then-sum would report 0.00 where sum-then-clamp reports +0.10."""
    assert effective_trust(0.5, 0.50 - 0.40, 0) == pytest.approx(0.60)
    # And the clamp still binds on the total.
    assert effective_trust(0.5, 5.0, 0) == pytest.approx(0.80)
    assert effective_trust(0.5, -5.0, 0) == pytest.approx(0.20)


def test_effective_trust_repartition_invariance():
    """I-T-1 over the pure formula: only the SUMS may matter.

    Partitions use per-component activity in ``[0, cap]`` because the write side
    clamps each account to cap — a partition with an over-cap component cannot
    be constructed by any real write path, so asserting on one would be fake.
    """
    rng = random.Random(4242)
    cap = activity_count_cap()
    for _ in range(200):
        parts = rng.randint(1, 8)
        adjustments = [rng.uniform(-0.9, 0.9) for _ in range(parts)]
        activities = [rng.randint(0, cap) for _ in range(parts)]
        baseline = effective_trust(0.5, sum(adjustments), sum(activities))
        shuffled = list(adjustments)
        rng.shuffle(shuffled)
        regrouped = list(activities)
        rng.shuffle(regrouped)
        assert effective_trust(
            0.5, sum(shuffled), sum(regrouped),
        ) == pytest.approx(baseline)


# ── ledger primitives ───────────────────────────────────────────────────────

def test_record_activity_is_idempotent_by_event_id():
    record = _fresh()
    assert record_activity(record, 3, "activity_aaaa") is True
    assert record_activity(record, 3, "activity_aaaa") is False
    assert record["message_count"] == 3


def test_record_activity_noop_uses_the_ENTITY_sum_not_this_account():
    """A three-account entity at 7 messages each is saturated (21 >= cap 20).

    Judging saturation per account would rewrite the whole pool JSON on every
    flush for exactly the multi-account entities this feature exists for.
    """
    cap = activity_count_cap()
    record = _fresh()
    record["message_count"] = 7
    assert record_activity(
        record, 1, "activity_bbbb", entity_message_count=cap + 1,
    ) is False
    # No event id is recorded either — that is the documented under-count.
    assert record["processed_activity_events"] == []
    assert record_activity(
        record, 1, "activity_bbbb", entity_message_count=7,
    ) is True


def test_apply_signal_event_is_idempotent_and_lossless():
    from config import (
        SPEAKER_TRUST_CONFIRMATION_DELTA,
        SPEAKER_TRUST_CORRECTION_DELTA,
    )

    record = _fresh()
    event = {"event_id": "e1", "kind": "correction"}
    assert apply_signal_event(record, event) is True
    assert apply_signal_event(record, event) is False
    assert record["adjustment"] == pytest.approx(-SPEAKER_TRUST_CORRECTION_DELTA)
    assert apply_signal_event(
        record, {"event_id": "e2", "kind": "confirmation"},
    ) is True
    assert record["adjustment"] == pytest.approx(
        -SPEAKER_TRUST_CORRECTION_DELTA + SPEAKER_TRUST_CONFIRMATION_DELTA
    )


def test_adjustment_is_never_clamped_on_the_write_side():
    """Clamping per write is non-commutative near the cap."""
    from config import SPEAKER_TRUST_ADJUSTMENT_LIMIT

    record = _fresh()
    for index in range(20):
        apply_signal_event(
            record, {"event_id": f"e{index}", "kind": "correction"},
        )
    assert record["adjustment"] < -SPEAKER_TRUST_ADJUSTMENT_LIMIT
    # The clamp only appears at read time.
    assert effective_trust(0.5, record["adjustment"], 0) == pytest.approx(0.20)


def test_signal_ring_is_never_truncated_while_activity_ring_is():
    from config import SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT

    raw = {
        "processed_signal_events": [f"s{i}" for i in range(5000)],
        "processed_activity_events": [f"a{i}" for i in range(5000)],
    }
    record = normalize_account_record("qq:1", raw)
    assert record["processed_signal_events"] == raw["processed_signal_events"]
    assert len(record["processed_activity_events"]) == (
        SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT
    )
    # Truncation keeps the newest, and the order is preserved (never sorted).
    assert record["processed_activity_events"][-1] == "a4999"


def test_activity_history_limit_exceeds_one_batch_of_messages():
    """A limit below the max batch size would evict ids from the same batch."""
    from config import (
        SCOPED_HISTORY_BATCH_MAX_MESSAGES,
        SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT,
    )

    assert (
        SPEAKER_TRUST_ACTIVITY_EVENT_HISTORY_LIMIT
        > SCOPED_HISTORY_BATCH_MAX_MESSAGES
    )


def test_normalize_account_record_survives_hostile_values():
    record = normalize_account_record("qq:1", {
        "adjustment": float("nan"),
        "message_count": -5,
        "generation": "x",
        "processed_signal_events": "not a list",
        "channels_seen": {"OPEN": {"count": "x"}, "bad chan": {}},
    })
    assert record["adjustment"] == 0.0
    assert record["message_count"] == 0
    assert record["generation"] == 0
    assert record["processed_signal_events"] == []
    assert set(record["channels_seen"]) == {"open"}


def test_normalize_legacy_profile_is_per_field_tolerant():
    """A dirty profile must degrade field-by-field, never reject the request."""
    profile = normalize_legacy_profile({
        "adjustment": "not a number",
        "message_count": None,
        "processed_signal_events": ["a", "a", "", "b"],
    })
    assert profile["adjustment"] == 0.0
    assert profile["message_count"] == 0
    assert profile["processed_signal_events"] == ["a", "b"]


def test_merge_order_key_is_a_total_order_on_created_at_then_id():
    older = {"created_at": "2026-01-01", "entity_id": "ent_b"}
    newer = {"created_at": "2026-02-01", "entity_id": "ent_a"}
    assert merge_order_key(older) < merge_order_key(newer)
    tie_low = {"created_at": "2026-01-01", "entity_id": "ent_a"}
    assert merge_order_key(tie_low) < merge_order_key(older)
