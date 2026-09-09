from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from main_logic.asr_client.prewire_gate import (
    ENDED_MICRO_EVENT_MAX_SAMPLES,
    OriginalAsrMapping,
    PrewireCapacityError,
    PrewireCommitStage,
    PrewireContractError,
    PrewireDecisionState,
    PrewireIdentityError,
    PrewireIntervalIdentity,
    PrewireIntervalLedger,
    PrewireIntervalSpec,
    PrewireOverlapError,
    PrewireStreamKey,
    PrewireTransitionError,
    SampleRange,
)


def _stream(generation: int = 1) -> PrewireStreamKey:
    return PrewireStreamKey("local-session", generation)


def _spec(
    segment_id: int,
    start: int,
    end: int,
    *,
    scoring_range: SampleRange | None = None,
    decision_range: SampleRange | None = None,
    commit_range: SampleRange | None = None,
    event_ended: bool = True,
    boundary_trusted: bool = True,
    independent_event: bool = True,
    stream: PrewireStreamKey | None = None,
) -> PrewireIntervalSpec:
    stream = stream or _stream()
    original = SampleRange(start, end)
    return PrewireIntervalSpec(
        identity=PrewireIntervalIdentity(
            stream,
            segment_id,
            original,
            "profile-1",
            "model-1",
            "config-1",
        ),
        scoring_range=scoring_range or original,
        decision_range=decision_range or original,
        commit_range=commit_range or original,
        event_ended=event_ended,
        boundary_trusted=boundary_trusted,
        independent_event=independent_event,
    )


def _score(
    ledger: PrewireIntervalLedger,
    spec: PrewireIntervalSpec,
    value: float = 0.8,
) -> None:
    ledger.record_score(
        spec.identity,
        score=value,
        scoring_parameters_digest="1" * 64,
    )


def _decide(
    ledger: PrewireIntervalLedger,
    spec: PrewireIntervalSpec,
    decision: PrewireDecisionState,
    *,
    used_micro_rule: bool = False,
) -> None:
    ledger.decide(
        spec.identity,
        decision,
        reason=f"test-{decision.value}",
        used_ended_micro_event_rule=used_micro_rule,
    )


def test_contract_binds_local_generations_and_keeps_ranges_distinct() -> None:
    spec = _spec(
        7,
        100,
        500,
        scoring_range=SampleRange(120, 380),
        decision_range=SampleRange(110, 480),
        commit_range=SampleRange(150, 450),
    )

    assert spec.identity.stream == PrewireStreamKey("local-session", 1)
    assert spec.identity.segment_id == 7
    assert spec.identity.profile_generation == "profile-1"
    assert spec.identity.model_generation == "model-1"
    assert spec.identity.config_generation == "config-1"
    assert spec.scoring_range == SampleRange(120, 380)
    assert spec.decision_range == SampleRange(110, 480)
    assert spec.commit_range == SampleRange(150, 450)
    with pytest.raises(FrozenInstanceError):
        spec.identity.segment_id = 8  # type: ignore[misc]


def test_contract_rejects_commit_outside_decision_or_mapping_ownership() -> None:
    with pytest.raises(PrewireContractError, match="contained"):
        _spec(
            1,
            0,
            500,
            decision_range=SampleRange(100, 400),
            commit_range=SampleRange(50, 400),
        )
    with pytest.raises(PrewireContractError, match="equal length"):
        OriginalAsrMapping(SampleRange(0, 100), SampleRange(200, 350))


def test_stream_must_open_before_local_interval_and_identity_is_exact() -> None:
    ledger = PrewireIntervalLedger()
    spec = _spec(1, 0, 100)
    with pytest.raises(PrewireIdentityError, match="opened"):
        ledger.add(spec)

    ledger.open_stream(spec.identity.stream, original_cursor=0)
    assert ledger.add(spec) == ledger.add(spec)
    stale_identity = replace(spec.identity, profile_generation="profile-2")
    assert ledger.get(stale_identity) is None
    with pytest.raises(PrewireIdentityError, match="stale or unknown"):
        ledger.record_score(
            stale_identity,
            score=0.8,
            scoring_parameters_digest="1" * 64,
        )


@pytest.mark.parametrize(
    "decision",
    (
        PrewireDecisionState.KEEP,
        PrewireDecisionState.DROP,
        PrewireDecisionState.UNCERTAIN,
    ),
)
def test_missing_score_or_parameters_cannot_become_a_scored_decision(
    decision: PrewireDecisionState,
) -> None:
    ledger = PrewireIntervalLedger()
    spec = _spec(1, 0, 100)
    ledger.open_stream(spec.identity.stream, original_cursor=0)
    ledger.add(spec)

    with pytest.raises(PrewireTransitionError, match="requires score"):
        _decide(ledger, spec, decision)
    with pytest.raises(PrewireContractError, match="recorded together"):
        ledger.record_score(
            spec.identity,
            score=0.8,
            scoring_parameters_digest=None,  # type: ignore[arg-type]
        )
    plan = ledger.plan_contiguous(spec.identity.stream)
    assert plan.record_identities == ()
    assert plan.releases == ()


def test_scoring_parameters_are_bound_by_a_strict_digest() -> None:
    ledger = PrewireIntervalLedger()
    spec = _spec(1, 0, 100)
    ledger.open_stream(spec.identity.stream, original_cursor=0)
    ledger.add(spec)

    with pytest.raises(PrewireContractError, match="lowercase SHA-256"):
        ledger.record_score(
            spec.identity,
            score=0.8,
            scoring_parameters_digest="parameters-v1",
        )


def test_open_stream_is_idempotent_after_cursors_advance() -> None:
    ledger = PrewireIntervalLedger()
    gap = _spec(1, 0, 100)
    keep = _spec(2, 100, 200)
    ledger.open_stream(gap.identity.stream, original_cursor=0, asr_cursor=10)
    ledger.add(gap)
    ledger.add(keep)
    _decide(ledger, gap, PrewireDecisionState.STALE)
    _score(ledger, keep)
    _decide(ledger, keep, PrewireDecisionState.KEEP)
    ledger.claim_enqueued(ledger.plan_contiguous(gap.identity.stream))

    ledger.open_stream(gap.identity.stream, original_cursor=0, asr_cursor=10)
    assert ledger.release_cursor(gap.identity.stream) == 200


@pytest.mark.parametrize(
    ("event_ended", "boundary_trusted", "independent_event", "sample_count"),
    (
        (False, True, True, 3_200),
        (True, False, True, 3_200),
        (True, True, False, 3_200),
        (True, True, True, 3_200),
    ),
)
def test_200ms_rule_requires_ended_trusted_independent_micro_event(
    event_ended: bool,
    boundary_trusted: bool,
    independent_event: bool,
    sample_count: int,
) -> None:
    ledger = PrewireIntervalLedger()
    spec = _spec(
        1,
        0,
        sample_count,
        event_ended=event_ended,
        boundary_trusted=boundary_trusted,
        independent_event=independent_event,
    )
    ledger.open_stream(spec.identity.stream, original_cursor=0)
    ledger.add(spec)
    _score(ledger, spec)

    with pytest.raises(PrewireTransitionError, match="200ms rule"):
        _decide(
            ledger,
            spec,
            PrewireDecisionState.KEEP,
            used_micro_rule=True,
        )


def test_200ms_rule_accepts_only_the_exact_qualified_boundary() -> None:
    ledger = PrewireIntervalLedger()
    spec = _spec(1, 0, ENDED_MICRO_EVENT_MAX_SAMPLES - 1)
    ledger.open_stream(spec.identity.stream, original_cursor=0)
    ledger.add(spec)
    _decide(ledger, spec, PrewireDecisionState.DROP, used_micro_rule=True)

    assert ledger.get(spec.identity).used_ended_micro_event_rule is True
    assert ledger.get(spec.identity).score is None


def test_only_contiguous_final_ranges_release_and_drop_advances_without_audio() -> None:
    ledger = PrewireIntervalLedger()
    first = _spec(1, 0, 100)
    second = _spec(2, 100, 200)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(second)
    ledger.add(first)
    _score(ledger, second)
    _decide(ledger, second, PrewireDecisionState.KEEP)

    blocked = ledger.plan_contiguous(first.identity.stream)
    assert blocked.record_identities == ()
    assert blocked.releases == ()
    _score(ledger, first, 0.1)
    _decide(ledger, first, PrewireDecisionState.DROP)

    plan = ledger.plan_contiguous(first.identity.stream)
    assert plan.record_identities == (first.identity, second.identity)
    assert tuple(gap.identity for gap in plan.gaps) == (first.identity,)
    assert len(plan.releases) == 1
    assert plan.releases[0].identity == second.identity
    assert plan.releases[0].original_range == SampleRange(100, 200)
    assert plan.releases[0].asr_range == SampleRange(0, 100)
    assert ledger.get(second.identity).original_to_asr is None
    assert ledger.get(second.identity).commit_stage is PrewireCommitStage.PENDING
    assert ledger.release_cursor(first.identity.stream) == 0

    ledger.claim_enqueued(plan)
    assert ledger.get(second.identity).original_to_asr == OriginalAsrMapping(
        SampleRange(100, 200),
        SampleRange(0, 100),
    )
    assert ledger.release_cursor(first.identity.stream) == 200
    assert ledger.get(second.identity).commit_stage is PrewireCommitStage.ENQUEUED
    assert ledger.plan_contiguous(first.identity.stream).record_identities == ()


def test_planning_and_enqueue_failure_leave_records_and_cursors_unchanged() -> None:
    ledger = PrewireIntervalLedger()
    gap = _spec(1, 0, 100)
    keep = _spec(2, 100, 250)
    ledger.open_stream(gap.identity.stream, original_cursor=0, asr_cursor=20)
    ledger.add(gap)
    ledger.add(keep)
    _score(ledger, gap, 0.1)
    _decide(ledger, gap, PrewireDecisionState.DROP)
    _score(ledger, keep)
    _decide(ledger, keep, PrewireDecisionState.KEEP)
    before_gap = ledger.get(gap.identity)
    before_keep = ledger.get(keep.identity)

    plan = ledger.plan_contiguous(gap.identity.stream)
    enqueue_succeeded = False
    if enqueue_succeeded:
        ledger.claim_enqueued(plan)

    assert ledger.get(gap.identity) == before_gap
    assert ledger.get(keep.identity) == before_keep
    assert ledger.release_cursor(gap.identity.stream) == 0
    assert ledger.asr_cursor(gap.identity.stream) == 20
    assert ledger.plan_contiguous(gap.identity.stream) == plan
    assert plan.original_cursor_start == 0
    assert plan.original_cursor_end == 250
    assert plan.asr_cursor_start == 20
    assert plan.asr_cursor_end == 170
    assert plan.record_identities == (gap.identity, keep.identity)


def test_pending_gap_blocks_later_keep() -> None:
    ledger = PrewireIntervalLedger()
    first = _spec(1, 0, 100)
    second = _spec(2, 100, 200)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)
    ledger.add(second)
    _score(ledger, second)
    _decide(ledger, second, PrewireDecisionState.KEEP)

    assert ledger.plan_contiguous(first.identity.stream).record_identities == ()
    assert ledger.release_cursor(first.identity.stream) == 0


@pytest.mark.parametrize(
    "gap_decision",
    (PrewireDecisionState.UNCERTAIN, PrewireDecisionState.UNAVAILABLE),
)
def test_bounded_uncertain_or_unavailable_gap_advances_without_asr_audio(
    gap_decision: PrewireDecisionState,
) -> None:
    ledger = PrewireIntervalLedger()
    first = _spec(1, 0, 100)
    second = _spec(2, 100, 200)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)
    ledger.add(second)
    if gap_decision is PrewireDecisionState.UNCERTAIN:
        _score(ledger, first)
    _decide(ledger, first, gap_decision)
    _score(ledger, second)
    _decide(ledger, second, PrewireDecisionState.KEEP)

    plan = ledger.plan_contiguous(first.identity.stream)
    assert tuple(release.identity for release in plan.releases) == (second.identity,)
    assert tuple(gap.identity for gap in plan.gaps) == (first.identity,)
    assert plan.releases[0].asr_range == SampleRange(0, 100)
    ledger.claim_enqueued(plan)
    assert ledger.get(first.identity).original_to_asr is None
    assert ledger.release_cursor(first.identity.stream) == 200
    assert ledger.asr_cursor(first.identity.stream) == 100
    with pytest.raises(PrewireTransitionError, match="consumed"):
        ledger.record_score(
            first.identity,
            score=0.8,
            scoring_parameters_digest="1" * 64,
        )


@pytest.mark.parametrize(
    "gap_decision",
    (
        PrewireDecisionState.DROP,
        PrewireDecisionState.UNCERTAIN,
        PrewireDecisionState.UNAVAILABLE,
        PrewireDecisionState.STALE,
    ),
)
def test_gap_only_plan_advances_only_original_cursor(
    gap_decision: PrewireDecisionState,
) -> None:
    ledger = PrewireIntervalLedger()
    gap = _spec(1, 0, 100)
    ledger.open_stream(gap.identity.stream, original_cursor=0, asr_cursor=30)
    ledger.add(gap)
    if gap_decision in {
        PrewireDecisionState.DROP,
        PrewireDecisionState.UNCERTAIN,
    }:
        _score(ledger, gap, 0.1)
    _decide(ledger, gap, gap_decision)

    plan = ledger.plan_contiguous(gap.identity.stream)
    assert plan.releases == ()
    assert tuple(item.identity for item in plan.gaps) == (gap.identity,)
    with pytest.raises(PrewireTransitionError, match="no enqueued audio"):
        ledger.claim_enqueued(plan)
    claimed = ledger.claim_gaps(plan)

    assert claimed == plan.gaps
    assert ledger.release_cursor(gap.identity.stream) == 100
    assert ledger.asr_cursor(gap.identity.stream) == 30
    assert ledger.get(gap.identity).commit_stage is PrewireCommitStage.PENDING
    assert ledger.get(gap.identity).original_to_asr is None


def test_empty_plan_cannot_be_claimed_as_audio_or_gaps() -> None:
    ledger = PrewireIntervalLedger()
    stream = _stream()
    ledger.open_stream(stream, original_cursor=0)
    plan = ledger.plan_contiguous(stream)

    with pytest.raises(PrewireTransitionError, match="no enqueued audio"):
        ledger.claim_enqueued(plan)
    with pytest.raises(PrewireTransitionError, match="no gaps"):
        ledger.claim_gaps(plan)


def test_unended_uncertain_or_unavailable_is_not_a_consumable_gap() -> None:
    ledger = PrewireIntervalLedger()
    first = _spec(1, 0, 100, event_ended=False)
    second = _spec(2, 100, 200)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)
    ledger.add(second)
    _decide(ledger, first, PrewireDecisionState.UNAVAILABLE)
    _score(ledger, second)
    _decide(ledger, second, PrewireDecisionState.KEEP)

    assert ledger.plan_contiguous(first.identity.stream).record_identities == ()
    assert ledger.release_cursor(first.identity.stream) == 0
    assert ledger.asr_cursor(first.identity.stream) == 0


def test_unavailable_without_score_cannot_be_reinterpreted_as_keep() -> None:
    ledger = PrewireIntervalLedger()
    spec = _spec(1, 0, 100)
    ledger.open_stream(spec.identity.stream, original_cursor=0)
    ledger.add(spec)
    _decide(ledger, spec, PrewireDecisionState.UNAVAILABLE)

    with pytest.raises(PrewireTransitionError, match="requires score"):
        _decide(ledger, spec, PrewireDecisionState.KEEP)
    plan = ledger.plan_contiguous(spec.identity.stream)
    assert plan.record_identities == (spec.identity,)
    assert plan.releases == ()


def test_overlapping_original_commit_range_is_never_registered_twice() -> None:
    ledger = PrewireIntervalLedger()
    first = _spec(1, 0, 100)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)

    with pytest.raises(PrewireOverlapError, match="cannot overlap"):
        ledger.add(_spec(2, 50, 150))


def test_consumed_range_cannot_be_registered_again_after_eviction() -> None:
    ledger = PrewireIntervalLedger(capacity=2)
    first = _spec(1, 0, 100)
    second = _spec(2, 100, 200)
    third = _spec(3, 200, 300)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)
    _decide(ledger, first, PrewireDecisionState.STALE)
    ledger.add(second)
    _score(ledger, second)
    _decide(ledger, second, PrewireDecisionState.KEEP)
    ledger.claim_enqueued(ledger.plan_contiguous(first.identity.stream))
    ledger.add(third)
    assert ledger.get(first.identity) is None

    with pytest.raises(PrewireIdentityError, match="precedes"):
        ledger.add(first)


def test_pending_has_no_mapping_and_two_keeps_allocate_contiguous_asr_ranges() -> None:
    ledger = PrewireIntervalLedger()
    first = _spec(1, 0, 100)
    second = _spec(2, 100, 250)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)
    ledger.add(second)
    assert ledger.get(first.identity).original_to_asr is None
    assert ledger.get(second.identity).original_to_asr is None
    for spec in (first, second):
        _score(ledger, spec)
        _decide(ledger, spec, PrewireDecisionState.KEEP)

    plan = ledger.plan_contiguous(first.identity.stream)
    assert tuple(release.asr_range for release in plan.releases) == (
        SampleRange(0, 100),
        SampleRange(100, 250),
    )
    assert ledger.asr_cursor(first.identity.stream) == 0
    claimed = ledger.claim_enqueued(plan)
    assert tuple(record.spec.identity for record in claimed) == (
        first.identity,
        second.identity,
    )
    assert all(record.commit_stage is PrewireCommitStage.ENQUEUED for record in claimed)
    assert ledger.asr_cursor(first.identity.stream) == 250


def test_mixed_gap_and_multiple_keeps_claim_atomically() -> None:
    ledger = PrewireIntervalLedger()
    gap = _spec(1, 0, 80)
    first = _spec(2, 80, 180)
    second = _spec(3, 180, 330)
    ledger.open_stream(gap.identity.stream, original_cursor=0, asr_cursor=40)
    for spec in (gap, first, second):
        ledger.add(spec)
    _decide(ledger, gap, PrewireDecisionState.STALE)
    for spec in (first, second):
        _score(ledger, spec)
        _decide(ledger, spec, PrewireDecisionState.KEEP)

    plan = ledger.plan_contiguous(gap.identity.stream)
    assert tuple(release.asr_range for release in plan.releases) == (
        SampleRange(40, 140),
        SampleRange(140, 290),
    )
    assert tuple(item.identity for item in plan.gaps) == (gap.identity,)
    with pytest.raises(PrewireTransitionError, match="cannot include ASR audio"):
        ledger.claim_gaps(plan)
    assert all(
        ledger.get(spec.identity).commit_stage is PrewireCommitStage.PENDING
        for spec in (gap, first, second)
    )

    claimed = ledger.claim_enqueued(plan)

    assert tuple(record.spec.identity for record in claimed) == (
        first.identity,
        second.identity,
    )
    assert all(
        ledger.get(spec.identity).commit_stage is PrewireCommitStage.ENQUEUED
        for spec in (first, second)
    )
    assert ledger.get(gap.identity).commit_stage is PrewireCommitStage.PENDING
    assert ledger.release_cursor(gap.identity.stream) == 330
    assert ledger.asr_cursor(gap.identity.stream) == 290


def test_add_after_plan_makes_plan_stale_without_partial_claim() -> None:
    ledger = PrewireIntervalLedger()
    keep = _spec(1, 0, 100)
    later = _spec(2, 200, 300)
    ledger.open_stream(keep.identity.stream, original_cursor=0)
    ledger.add(keep)
    _score(ledger, keep)
    _decide(ledger, keep, PrewireDecisionState.KEEP)
    plan = ledger.plan_contiguous(keep.identity.stream)
    ledger.add(later)

    with pytest.raises(PrewireTransitionError, match="stale"):
        ledger.claim_enqueued(plan)
    assert ledger.get(keep.identity).commit_stage is PrewireCommitStage.PENDING
    assert ledger.release_cursor(keep.identity.stream) == 0


def test_score_or_decision_after_plan_makes_plan_stale() -> None:
    ledger = PrewireIntervalLedger()
    first = _spec(1, 0, 100)
    second = _spec(2, 100, 200)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)
    ledger.add(second)
    _score(ledger, first)
    _decide(ledger, first, PrewireDecisionState.KEEP)

    before_score = ledger.plan_contiguous(first.identity.stream)
    _score(ledger, second)
    with pytest.raises(PrewireTransitionError, match="stale"):
        ledger.claim_enqueued(before_score)

    before_decision = ledger.plan_contiguous(first.identity.stream)
    _decide(ledger, second, PrewireDecisionState.KEEP)
    with pytest.raises(PrewireTransitionError, match="stale"):
        ledger.claim_enqueued(before_decision)
    assert ledger.get(first.identity).commit_stage is PrewireCommitStage.PENDING
    assert ledger.get(second.identity).commit_stage is PrewireCommitStage.PENDING


def test_cursor_change_makes_duplicate_plan_stale() -> None:
    ledger = PrewireIntervalLedger()
    keep = _spec(1, 0, 100)
    ledger.open_stream(keep.identity.stream, original_cursor=0)
    ledger.add(keep)
    _score(ledger, keep)
    _decide(ledger, keep, PrewireDecisionState.KEEP)
    first_plan = ledger.plan_contiguous(keep.identity.stream)
    duplicate_plan = ledger.plan_contiguous(keep.identity.stream)

    ledger.claim_enqueued(first_plan)
    with pytest.raises(PrewireTransitionError, match="stale"):
        ledger.claim_enqueued(duplicate_plan)
    assert ledger.release_cursor(keep.identity.stream) == 100
    assert ledger.asr_cursor(keep.identity.stream) == 100


def test_commit_progress_uses_cas_and_unknown_requires_explicit_confirmation() -> None:
    ledger = PrewireIntervalLedger()
    spec = _spec(1, 0, 100)
    ledger.open_stream(spec.identity.stream, original_cursor=0)
    ledger.add(spec)
    _score(ledger, spec)
    _decide(ledger, spec, PrewireDecisionState.KEEP)
    ledger.claim_enqueued(ledger.plan_contiguous(spec.identity.stream))

    with pytest.raises(PrewireTransitionError, match="compare-and-set"):
        ledger.advance_commit(
            spec.identity,
            expected=PrewireCommitStage.SELECTED,
            next_stage=PrewireCommitStage.ENQUEUED,
        )
    ledger.advance_commit(
        spec.identity,
        expected=PrewireCommitStage.ENQUEUED,
        next_stage=PrewireCommitStage.UNKNOWN,
    )
    with pytest.raises(PrewireTransitionError, match="not allowed"):
        ledger.advance_commit(
            spec.identity,
            expected=PrewireCommitStage.UNKNOWN,
            next_stage=PrewireCommitStage.WRITTEN,
        )
    confirmed = ledger.advance_commit(
        spec.identity,
        expected=PrewireCommitStage.UNKNOWN,
        next_stage=PrewireCommitStage.REMOTE_CONFIRMED,
    )
    assert confirmed.commit_stage is PrewireCommitStage.REMOTE_CONFIRMED


@pytest.mark.parametrize(
    "protected_stage",
    (PrewireCommitStage.ENQUEUED, PrewireCommitStage.UNKNOWN),
)
def test_capacity_never_evicts_enqueued_or_unknown_delivery(
    protected_stage: PrewireCommitStage,
) -> None:
    ledger = PrewireIntervalLedger(capacity=1)
    first = _spec(1, 0, 100)
    second = _spec(2, 100, 200)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)
    _score(ledger, first)
    _decide(ledger, first, PrewireDecisionState.KEEP)
    ledger.claim_enqueued(ledger.plan_contiguous(first.identity.stream))
    if protected_stage is PrewireCommitStage.UNKNOWN:
        ledger.advance_commit(
            first.identity,
            expected=PrewireCommitStage.ENQUEUED,
            next_stage=PrewireCommitStage.UNKNOWN,
        )

    with pytest.raises(PrewireCapacityError):
        ledger.add(second)
    assert ledger.get(first.identity).commit_stage is protected_stage


def test_capacity_can_evict_only_a_consumed_safe_record() -> None:
    ledger = PrewireIntervalLedger(capacity=2)
    first = _spec(1, 0, 100)
    second = _spec(2, 100, 200)
    third = _spec(3, 200, 300)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)
    _score(ledger, first, 0.1)
    _decide(ledger, first, PrewireDecisionState.DROP)
    ledger.add(second)
    _score(ledger, second)
    _decide(ledger, second, PrewireDecisionState.KEEP)
    ledger.claim_enqueued(ledger.plan_contiguous(first.identity.stream))
    ledger.add(third)
    assert ledger.get(first.identity) is None
    assert ledger.get(second.identity) is not None
