from dataclasses import replace

import pytest

from main_logic.voice_identity_service.prewire_gate import (
    PrewireCommitStage,
    PrewireContractError,
    PrewireDecisionState,
    PrewireStreamKey,
    PrewireTransitionError,
    PrewireGateCapacityError,
    PrewireIdentityError,
)
from main_logic.voice_identity_service.prewire_gate.ledger import PrewireIntervalLedger
from tests.unit.voice_identity_service.prewire_gate.test_gate import (
    _gate,
    _ScoreBackend,
    _spec,
)
from tests.unit.voice_identity_service.prewire_gate.test_ledger import (
    _spec as ledger_spec,
    _score,
    _decide,
)

pytestmark = pytest.mark.runtime


def test_later_prefix_and_other_stream_updates_do_not_invalidate_delivery():
    ledger = PrewireIntervalLedger()
    first, later = ledger_spec(1, 0, 100), ledger_spec(2, 100, 200)
    stream = first.identity.stream
    ledger.open_stream(stream, original_cursor=0)
    ledger.add(first)
    _score(ledger, first)
    _decide(ledger, first, PrewireDecisionState.KEEP)
    plan = ledger.plan_contiguous(stream)
    ledger.add(later)
    _score(ledger, later)
    _decide(ledger, later, PrewireDecisionState.KEEP)
    ledger.open_stream(PrewireStreamKey("other", 1), original_cursor=0)
    ledger.claim_enqueued(plan)
    assert ledger.release_cursor(stream) == 100
    assert ledger.asr_cursor(stream) == 100
    assert ledger.plan_contiguous(stream).record_identities == (later.identity,)
    with pytest.raises(PrewireTransitionError):
        ledger.claim_enqueued(plan)


@pytest.mark.parametrize("field", ["original_cursor_end", "asr_cursor_end"])
def test_prefix_claim_rejects_forged_end_cursor(field):
    ledger = PrewireIntervalLedger()
    first = ledger_spec(1, 0, 100)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)
    _score(ledger, first)
    _decide(ledger, first, PrewireDecisionState.KEEP)
    plan = ledger.plan_contiguous(first.identity.stream)
    with pytest.raises((PrewireTransitionError, PrewireContractError)):
        ledger.claim_enqueued(replace(plan, **{field: 999}))
    assert ledger.release_cursor(first.identity.stream) == 0


@pytest.mark.asyncio
async def test_default_gate_can_confirm_delivery_beyond_record_capacity():
    gate = _gate(_ScoreBackend(), held_bytes=2_000)
    stream = PrewireStreamKey("session", 1)
    gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * (4 * 130 + 4))
    try:
        for number in range(130):
            submission = gate.submit_interval(_spec(number + 1, start=number * 4))
            plan = await gate.resolve(submission)
            gate.claim(plan)
            gate.advance_delivery(
                submission.identity,
                expected=PrewireCommitStage.ENQUEUED,
                next_stage=PrewireCommitStage.WRITTEN,
            )
            gate.advance_delivery(
                submission.identity,
                expected=PrewireCommitStage.WRITTEN,
                next_stage=PrewireCommitStage.REMOTE_CONFIRMED,
            )
    finally:
        await gate.close()


def test_change_inside_delivered_prefix_still_invalidates_plan():
    ledger = PrewireIntervalLedger()
    first = ledger_spec(1, 0, 100)
    ledger.open_stream(first.identity.stream, original_cursor=0)
    ledger.add(first)
    _score(ledger, first)
    _decide(ledger, first, PrewireDecisionState.UNCERTAIN)
    plan = ledger.plan_contiguous(first.identity.stream)
    _decide(ledger, first, PrewireDecisionState.KEEP)
    with pytest.raises(PrewireTransitionError):
        ledger.claim_gaps(plan)
    assert ledger.release_cursor(first.identity.stream) == 0


@pytest.mark.asyncio
async def test_gate_confirmation_survives_stream_retirement_and_keeps_identity_fence():
    gate = _gate(_ScoreBackend())
    stream = PrewireStreamKey("session", 1)
    gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * 8)
    submission = gate.submit_interval(_spec(1))
    gate.claim(await gate.resolve(submission))
    await gate.close()
    with pytest.raises(PrewireIdentityError):
        gate.advance_delivery(
            replace(submission.identity, profile_generation="other"),
            expected=PrewireCommitStage.ENQUEUED,
            next_stage=PrewireCommitStage.WRITTEN,
        )
    record = gate.advance_delivery(
        submission.identity,
        expected=PrewireCommitStage.ENQUEUED,
        next_stage=PrewireCommitStage.UNKNOWN,
    )
    assert record.commit_stage is PrewireCommitStage.UNKNOWN
    with pytest.raises(PrewireTransitionError):
        gate.advance_delivery(
            submission.identity,
            expected=PrewireCommitStage.ENQUEUED,
            next_stage=PrewireCommitStage.WRITTEN,
        )
    record = gate.advance_delivery(
        submission.identity,
        expected=PrewireCommitStage.UNKNOWN,
        next_stage=PrewireCommitStage.REMOTE_CONFIRMED,
    )
    assert record.commit_stage is PrewireCommitStage.REMOTE_CONFIRMED


@pytest.mark.asyncio
async def test_gate_capacity_failures_preserve_unconfirmed_records_and_streams():
    gate = _gate(_ScoreBackend(), held_bytes=2_000)
    stream = PrewireStreamKey("session", 1)
    try:
        gate.append_pcm(stream, start_sample=0, pcm16=b"\x01\x00" * (4 * 128 + 8))
        for number in range(128):
            gate.claim(
                await gate.resolve(
                    gate.submit_interval(_spec(number + 1, start=number * 4))
                )
            )
        with pytest.raises(PrewireGateCapacityError):
            gate.submit_interval(_spec(129, start=512))
        gate.invalidate_stream(stream)
        for generation in range(2, 129):
            key = PrewireStreamKey("session", generation)
            gate.open_stream(
                key, profile_generation="p", model_generation="m", config_generation="c"
            )
            gate.invalidate_stream(key)
        with pytest.raises(PrewireGateCapacityError):
            gate.open_stream(
                PrewireStreamKey("session", 129),
                profile_generation="p",
                model_generation="m",
                config_generation="c",
            )
        # Exhaustion/retirement must not erase evidence needed for later ACKs.
        record = gate.advance_delivery(
            _spec(1).identity,
            expected=PrewireCommitStage.ENQUEUED,
            next_stage=PrewireCommitStage.UNKNOWN,
        )
        assert record.commit_stage is PrewireCommitStage.UNKNOWN
    finally:
        await gate.close()
