from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory
from main_logic.voice_identity_service.prewire_gate.contracts import PrewireDecisionState
from main_logic.voice_identity_service.prewire_gate.decision import CalibratedIdentityEvidence, CalibratedIdentityOutcome
from main_logic.voice_identity_service.prewire_gate.ledger import PrewireIntervalLedger, PrewireTransitionError
from main_logic.voice_identity_service.prewire_gate.scheduler import ScorerCapabilities
from main_logic.voice_input.interception import InterceptionDecision
from main_logic.voice_input.interception_events import InterceptionOutputKind
from tests.unit.voice_identity_service.test_interception_runtime import _config, _Scorer, _Tse, _Classifier
from tests.unit.voice_identity_service.prewire_gate.test_ledger import _spec, _stream

pytestmark = pytest.mark.unit_fast


def test_unsupported_model_length_rejected_before_allocation():
    capabilities = ScorerCapabilities(16000, 24000, (24000,), "model")
    config = replace(_config(), window_samples=14400, scorer_capabilities=capabilities)
    with pytest.raises(ValueError, match="declared model support"):
        PrewireInterceptionFactory(config, score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda _: _Tse()).create(None, ingress_token=None)


def test_owner_confirmation_capacity_is_checked_before_model_start():
    config = replace(_config(), max_held_pcm_bytes=3200)
    with pytest.raises(ValueError, match="owner confirmation"):
        PrewireInterceptionFactory(config, score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda _: _Tse()).create(None, ingress_token=None)


def test_explicit_uncertain_gap_is_immutable_without_fake_endpoint():
    ledger = PrewireIntervalLedger()
    ledger.open_stream(_stream(), original_cursor=0)
    spec = _spec(1, 0, 1600, event_ended=False, boundary_trusted=False, independent_event=False)
    ledger.add(spec)
    ledger.record_score(spec.identity, score=0.4, scoring_parameters_digest="a" * 64)
    ledger.decide(spec.identity, PrewireDecisionState.UNCERTAIN, reason="uncertain")
    assert not ledger.plan_contiguous(_stream()).gaps
    record = ledger.finalize_uncertain(spec.identity)
    assert not record.spec.event_ended
    assert record.decision is PrewireDecisionState.UNCERTAIN
    plan = ledger.plan_contiguous(_stream())
    assert len(plan.gaps) == 1 and not plan.releases
    with pytest.raises(PrewireTransitionError, match="immutable"):
        ledger.record_score(spec.identity, score=0.9, scoring_parameters_digest="a" * 64)
    with pytest.raises(PrewireTransitionError, match="immutable"):
        ledger.decide(spec.identity, PrewireDecisionState.KEEP, reason="late_owner")
    ledger.claim_gaps(plan)
    assert ledger.release_cursor(_stream()) == 1600
    assert ledger.asr_cursor(_stream()) == 0


class _FirstUncertain:
    def __init__(self):
        self.calls = 0

    def classify(self, observation):
        self.calls += 1
        return CalibratedIdentityEvidence(
            CalibratedIdentityOutcome.UNCERTAIN if self.calls == 1 else CalibratedIdentityOutcome.OWNER,
            "research_first_uncertain",
        )


async def _feed(runtime, count):
    return await runtime.process(b"\x02\x00" * count, sample_rate_hz=16000, generation=None, ingress_token=None, captured_at=None)


@pytest.mark.asyncio
async def test_completed_uncertain_gap_does_not_block_later_owner():
    runtime = PrewireInterceptionFactory(_config(), score_backend=_Scorer(), classifier=_FirstUncertain(), tse_factory=lambda _: _Tse()).create(None, ingress_token=None)
    try:
        first = await _feed(runtime, 1600)
        assert first.pcm16 == b""
        assert [event.kind for event in first.events] == [InterceptionOutputKind.GAP]
        assert await _feed(runtime, 400)
        third = await _feed(runtime, 400)
        assert third.decision is InterceptionDecision.KEEP and third.pcm16
        assert all(event.start_sample >= first.events[0].end_sample for event in third.events)
        assert runtime._gate.held_pcm_bytes < 4800
    finally:
        await runtime.close()


class _FailFirst(_Scorer):
    def score(self, pcm16, sample_rate_hz):
        raise RuntimeError("research_backend_failure")


@pytest.mark.asyncio
async def test_first_scoring_failure_is_not_hidden_until_capacity():
    runtime = PrewireInterceptionFactory(_config(), score_backend=_FailFirst(), classifier=_Classifier(), tse_factory=lambda _: _Tse()).create(None, ingress_token=None)
    result = await _feed(runtime, 1600)
    assert result.decision is InterceptionDecision.UNAVAILABLE
    assert result.reason == "scoring_failed:backend_exception"
    assert result.pcm16 == b"" and not result.events
    assert runtime._gate.held_pcm_bytes == 0
    assert runtime.retirement_confirmed
    assert (await _feed(runtime, 400)).decision is InterceptionDecision.STALE


@pytest.mark.asyncio
async def test_finish_settles_real_plan_when_continuous_gap_finalization_is_disabled(monkeypatch):
    # Counterfactual: retain the former held-uncertain policy to exercise the
    # independent Plan -> claim -> End repair through the actual gate.
    runtime = PrewireInterceptionFactory(_config(), score_backend=_Scorer(), classifier=_FirstUncertain(), tse_factory=lambda _: _Tse()).create(None, ingress_token=None)
    monkeypatch.setattr(runtime._gate, "finalize_uncertain", lambda submission: None)
    try:
        assert (await _feed(runtime, 1600)).decision is InterceptionDecision.PENDING
        await _feed(runtime, 400)
        result = await runtime.finish()
        assert result.decision is InterceptionDecision.DROP
        assert result.events and result.events[-1].kind is InterceptionOutputKind.END
        assert any(event.kind is InterceptionOutputKind.GAP for event in result.events)
        assert not result.pcm16
        assert runtime.retirement_confirmed
    finally:
        await runtime.close()


class _BlockedFlush(_Tse):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.released = asyncio.Event()

    async def flush(self):
        self.entered.set()
        await self.released.wait()
        return []


@pytest.mark.asyncio
async def test_finish_deadline_includes_flush_and_fences_late_output():
    worker = _BlockedFlush()
    runtime = PrewireInterceptionFactory(replace(_config(), finish_timeout_seconds=0.03), score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda _: worker).create(None, ingress_token=None)
    try:
        await _feed(runtime, 2000)
        result = await asyncio.wait_for(runtime.finish(), timeout=0.3)
        assert worker.entered.is_set()
        assert result.decision is InterceptionDecision.UNAVAILABLE
        assert result.reason == "finish_deadline_expired"
        worker.released.set()
        assert (await _feed(runtime, 400)).decision is InterceptionDecision.STALE
    finally:
        worker.released.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_finish_deadline_includes_lock_wait():
    runtime = PrewireInterceptionFactory(replace(_config(), finish_timeout_seconds=0.03), score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda _: _Tse()).create(None, ingress_token=None)
    await runtime._lock.acquire()
    try:
        result = await asyncio.wait_for(runtime.finish(), timeout=0.3)
        assert result.decision is InterceptionDecision.UNAVAILABLE
        assert result.reason == "finish_deadline_expired"
        assert runtime._closed
    finally:
        runtime._lock.release()
        await runtime.close()


@pytest.mark.asyncio
async def test_finish_cancellation_retires_and_preserves_no_new_audio():
    worker = _BlockedFlush()
    runtime = PrewireInterceptionFactory(_config(), score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda _: worker).create(None, ingress_token=None)
    try:
        await _feed(runtime, 2000)
        task = asyncio.create_task(runtime.finish())
        await worker.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime._closed
        assert (await _feed(runtime, 400)).decision is InterceptionDecision.STALE
    finally:
        worker.released.set()
        await runtime.close()


class _BlockedPush(_Tse):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def push(self, pcm, *, start_sample):
        self.entered.set()
        await self.release.wait()
        return await super().push(pcm, start_sample=start_sample)


@pytest.mark.asyncio
async def test_close_fences_a_model_wait_without_waiting_for_process_lock():
    worker = _BlockedPush()
    runtime = PrewireInterceptionFactory(_config(), score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda _: worker).create(None, ingress_token=None)
    task = asyncio.create_task(_feed(runtime, 2000))
    try:
        await worker.entered.wait()
        await asyncio.wait_for(runtime.close(), timeout=.3)
        assert runtime._closed
        worker.release.set()
        result = await task
        assert result.pcm16 == b"" and not result.events
        assert result.decision is InterceptionDecision.UNAVAILABLE
    finally:
        worker.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()
