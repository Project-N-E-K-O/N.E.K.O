"""Barrier regressions for local output authority and physical retirement."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import threading

import pytest

from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory
from main_logic.voice_identity_service.prewire_gate.decision import (
    CalibratedIdentityEvidence, CalibratedIdentityOutcome, PrewireQualitySummary,
)
from main_logic.voice_identity_service.prewire_gate.scheduler import ScorerCapabilities
from main_logic.voice_identity_service.tse.contracts import TseAudioChunk
from main_logic.voice_input.interception import InterceptionDecision
from tests.unit.voice_identity_service.test_interception_runtime import _config, _Scorer, _Classifier, _Tse

pytestmark = [pytest.mark.unit_fast, pytest.mark.asyncio]


def _factory(worker=None, *, scorer=None, classifier=None, quality=None, **changes):
    return PrewireInterceptionFactory(
        replace(_config(), **changes), score_backend=scorer or _Scorer(),
        classifier=classifier or _Classifier(), tse_factory=lambda _: worker or _Tse(),
        quality_analyzer=quality,
    )


async def _feed(runtime, samples=2000, value=2):
    return await runtime.process(bytes((value, 0)) * samples, sample_rate_hz=16000,
                                 generation=None, ingress_token=None, captured_at=None)


class _BarrierTse(_Tse):
    def __init__(self, phase):
        super().__init__()
        self.phase = phase
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.buffered = []

    async def _barrier(self, phase):
        if self.phase == phase:
            self.entered.set()
            await self.release.wait()

    async def start(self, *, timeout=1.0):
        await self._barrier("start")
        await super().start(timeout=timeout)

    async def push(self, pcm, *, start_sample):
        await self._barrier("push")
        chunks = await super().push(pcm, start_sample=start_sample)
        if self.phase in {"flush", "close"}:
            self.buffered.extend(chunks)
            return []
        return chunks

    async def flush(self):
        await self._barrier("flush")
        chunks, self.buffered = self.buffered, []
        return chunks

    async def close(self, *, timeout=1.0):
        await self._barrier("close")
        return True


@pytest.mark.parametrize("phase", ["start", "push", "flush", "close"])
async def test_revoke_at_extractor_await_never_returns_old_pcm(phase):
    worker = _BarrierTse(phase)
    factory = _factory(worker, scoring_close_timeout_seconds=.02)
    runtime = factory.create(None, ingress_token=None)
    task = None
    try:
        if phase in {"flush", "close"}:
            assert not (await _feed(runtime)).pcm16
            task = asyncio.create_task(runtime.finish())
        else:
            task = asyncio.create_task(_feed(runtime))
        await asyncio.wait_for(worker.entered.wait(), 1)
        await asyncio.wait_for(runtime.close("profile_revoked"), .3)
        assert runtime._output_revoked
        assert not runtime.retirement_confirmed
        with pytest.raises(RuntimeError, match="retirement_pending"):
            factory.create(None, ingress_token=None)
        worker.release.set()
        result = await asyncio.wait_for(task, 1)
        assert not result.pcm16
        assert result.decision is InterceptionDecision.UNAVAILABLE
        await runtime.close()
        assert runtime.retired
        successor = factory.create(None, ingress_token=None)
        await successor.close()
    finally:
        worker.release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


async def test_quality_native_thread_is_owned_after_cancel_and_close():
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    class Quality:
        def analyze(self, pcm16, sample_rate_hz):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            return PrewireQualitySummary()

    factory = _factory(quality=Quality(), scoring_close_timeout_seconds=.02)
    runtime = factory.create(None, ingress_token=None)
    task = asyncio.create_task(_feed(runtime))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await runtime.close()
        assert not runtime.retirement_confirmed
        with pytest.raises(RuntimeError, match="retirement_pending"):
            factory.create(None, ingress_token=None)
        release.set()
        await asyncio.gather(*tuple(runtime._operations))
        await runtime.close()
        assert runtime.retired
    finally:
        release.set()
        await asyncio.gather(task, *tuple(runtime._operations), return_exceptions=True)
        await runtime.close()


async def test_async_quality_revoke_is_checked_before_score_submission():
    entered, release = asyncio.Event(), asyncio.Event()

    class Quality:
        async def analyze_async(self, pcm16, sample_rate_hz):
            entered.set()
            await release.wait()
            return PrewireQualitySummary()

    class Scorer(_Scorer):
        calls = 0
        def score(self, pcm16, sample_rate_hz):
            self.calls += 1
            return super().score(pcm16, sample_rate_hz)

    scorer = Scorer()
    runtime = _factory(scorer=scorer, quality=Quality()).create(None, ingress_token=None)
    task = asyncio.create_task(_feed(runtime))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await runtime.close()
        release.set()
        result = await task
        assert not result.pcm16
        assert scorer.calls == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


async def test_score_failure_retires_instead_of_accumulating_pending_audio():
    class Scorer:
        calls = 0
        def score(self, pcm16, sample_rate_hz):
            self.calls += 1
            raise RuntimeError("backend_failure")

    scorer = Scorer()
    runtime = _factory(scorer=scorer).create(None, ingress_token=None)
    try:
        result = await _feed(runtime)
        assert result.decision is InterceptionDecision.UNAVAILABLE
        assert "scoring_failed" in result.reason
        assert not result.pcm16
        assert (await _feed(runtime)).decision is InterceptionDecision.STALE
        assert scorer.calls == 1
        assert not runtime._pending_audio
    finally:
        await runtime.close()


async def test_completed_uncertain_does_not_block_later_owner_audio():
    class Classifier:
        calls = 0
        def classify(self, observation):
            self.calls += 1
            return CalibratedIdentityEvidence(
                CalibratedIdentityOutcome.UNCERTAIN if self.calls == 1 else CalibratedIdentityOutcome.OWNER,
                "controlled_uncertainty",
            )

    runtime = _factory(classifier=Classifier()).create(None, ingress_token=None)
    try:
        assert not (await _feed(runtime, 1600)).pcm16
        assert not (await _feed(runtime, 400)).pcm16
        result = await _feed(runtime, 400)
        assert result.decision is InterceptionDecision.KEEP
        assert len(result.pcm16) == 1600
        assert runtime._settled_sample == 1200
    finally:
        await runtime.close()


async def test_normal_finish_preserves_confirmed_delayed_extraction_once():
    worker = _BarrierTse("flush")
    runtime = _factory(worker).create(None, ingress_token=None)
    task = None
    try:
        assert not (await _feed(runtime)).pcm16
        task = asyncio.create_task(runtime.finish())
        await worker.entered.wait()
        worker.release.set()
        result = await task
        assert result.decision is InterceptionDecision.KEEP
        assert len(result.pcm16) == 1600
        assert not (await runtime.finish()).pcm16
    finally:
        worker.release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


async def test_finish_consumes_real_unresolved_gate_plan_before_end(monkeypatch):
    class Classifier:
        def classify(self, observation):
            return CalibratedIdentityEvidence(CalibratedIdentityOutcome.UNCERTAIN, "uncertain")

    runtime = _factory(classifier=Classifier()).create(None, ingress_token=None)
    # Leave the continuous interval pending to exercise finish_stream's actual
    # plan return, independently from the normal early uncertainty settlement.
    monkeypatch.setattr(runtime._gate, "finalize_uncertain", lambda submission: None)
    plans = []
    claim = runtime._gate.claim

    def record_claim(plan):
        plans.append(plan)
        claim(plan)

    monkeypatch.setattr(runtime._gate, "claim", record_claim)
    try:
        await _feed(runtime, 1600)
        result = await runtime.finish()
        assert result.decision is InterceptionDecision.DROP
        assert not result.pcm16
        assert plans
        assert all(event.kind == "gap" for plan in plans for event in plan.events)
        assert runtime._settled_sample == 1600
    finally:
        await runtime.close()


async def test_cancel_close_waiter_keeps_cleanup_owned_and_blocks_replacement():
    worker = _BarrierTse("close")
    factory = _factory(worker, scoring_close_timeout_seconds=.02)
    runtime = factory.create(None, ingress_token=None)
    task = None
    try:
        await _feed(runtime, 400)
        task = asyncio.create_task(runtime.close())
        await worker.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime._output_revoked
        assert not runtime._tse_close_task.cancelled()
        assert not runtime.retired
        with pytest.raises(RuntimeError, match="retirement_pending"):
            factory.create(None, ingress_token=None)
        worker.release.set()
        await runtime._tse_close_task
        await runtime._component_retirement_task
        assert runtime.retired
    finally:
        worker.release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


async def test_finish_rechecks_revocation_at_public_return(monkeypatch):
    worker = _BarrierTse("flush")
    worker.release.set()
    factory = _factory(worker)
    runtime = factory.create(None, ingress_token=None)
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime._finish_locked

    async def delay_after_internal_finish(deadline):
        result = await original(deadline)
        assert result.pcm16
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(runtime, "_finish_locked", delay_after_internal_finish)
    task = None
    try:
        await _feed(runtime)
        task = asyncio.create_task(runtime.finish())
        await entered.wait()
        assert not runtime.retired
        with pytest.raises(RuntimeError, match="retirement_pending"):
            factory.create(None, ingress_token=None)
        await runtime.close("profile_revoked")
        release.set()
        result = await task
        assert result.decision is InterceptionDecision.UNAVAILABLE
        assert not result.pcm16
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


async def test_concurrent_finish_never_returns_the_same_pcm_twice():
    worker = _BarrierTse("flush")
    runtime = _factory(worker).create(None, ingress_token=None)
    tasks = []
    try:
        await _feed(runtime)
        tasks = [asyncio.create_task(runtime.finish()) for _ in range(2)]
        await worker.entered.wait()
        worker.release.set()
        results = await asyncio.gather(*tasks)
        assert sum(len(result.pcm16) for result in results) == 1600
    finally:
        worker.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.close()


async def test_finish_total_deadline_includes_lock_and_retains_cleanup_owner():
    runtime = _factory(finish_timeout_seconds=.01).create(None, ingress_token=None)
    await runtime._lock.acquire()
    try:
        result = await asyncio.wait_for(runtime.finish(), .3)
        assert result.decision is InterceptionDecision.UNAVAILABLE
        assert result.reason == "finish_deadline_expired"
        assert runtime._output_revoked
        assert runtime._component_retirement_task is not None
    finally:
        runtime._lock.release()
        await runtime.close()


async def test_new_input_does_not_renew_an_old_extraction_debt():
    worker = _BarrierTse("flush")
    runtime = _factory(worker).create(None, ingress_token=None)
    try:
        await _feed(runtime)
        original_deadline = runtime._capture_deadline
        await _feed(runtime, 400)
        assert runtime._capture_deadline == original_deadline
        assert runtime._pending_audio
        # Move only the owned deadline to now to exercise the actual timer path.
        runtime._arm_deadline(asyncio.get_running_loop().time())
        await asyncio.wait_for(runtime._deadline_task, 1)
        assert runtime._output_revoked
        assert not (await _feed(runtime, 400)).pcm16
    finally:
        worker.release.set()
        await runtime.close()


@pytest.mark.parametrize("field", ["prefix_deadline_seconds", "scoring_deadline_seconds", "scoring_close_timeout_seconds", "finish_timeout_seconds"])
@pytest.mark.parametrize("value", [0, float("nan"), float("inf"), True])
async def test_invalid_budget_rejected_before_model_allocation(field, value):
    with pytest.raises(ValueError, match=field):
        _factory(**{field: value})


async def test_support_and_capacity_validation_happen_before_allocation():
    for changes in ({"capture_support_timeout_seconds": .1}, {"max_held_pcm_bytes": 3999},
                    {"max_buffered_pcm_bytes": 3999}, {"extraction_max_buffered_pcm_bytes": 3999},
                    {"extraction_max_pending_events": 1}):
        with pytest.raises(ValueError):
            _factory(**changes)
    scorer = _Scorer()
    scorer.capabilities = ScorerCapabilities(16000, 24000, (24000,), "model")
    with pytest.raises(ValueError):
        _factory(scorer=scorer)


async def test_sampling_support_is_explicit_and_separate_from_processing_budget():
    runtime = _factory().create(None, ingress_token=None)
    try:
        assert runtime._capture_support_seconds == .125
        assert runtime._config.prefix_deadline_seconds == 1
        await _feed(runtime, 400)
        origin = runtime._ingress_anchors[0][1]
        assert runtime._capture_deadline == origin + .125 + 1
    finally:
        await runtime.close()


async def test_factory_authority_readiness_does_not_allocate_or_depend_on_slot():
    factory = _factory()
    assert factory.is_available
    assert not factory._runtimes
    runtime = factory.create(None, ingress_token=None)
    assert factory.is_available
    factory.close()
    assert not factory.is_available
    await runtime.close()


@pytest.mark.parametrize("changes", [
    {"required_consistent_observations": True}, {"session_id": ""}, {"ingress_generation": True},
    {"profile_generation": ""}, {"model_generation": ""}, {"config_generation": ""},
    {"scoring_parameters_digest": "G" * 64}, {"window_samples": True},
    {"step_samples": 1600, "guard_samples": 200},
])
async def test_invalid_static_contract_rejected_before_factory_reports_available(changes):
    with pytest.raises(ValueError):
        _factory(**changes)


async def test_sync_score_still_running_blocks_runtime_factory_handover():
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    class Scorer:
        def score(self, pcm16, sample_rate_hz):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            return .9

    factory = _factory(scorer=Scorer(), scoring_close_timeout_seconds=.02)
    runtime = factory.create(None, ingress_token=None)
    task = asyncio.create_task(_feed(runtime))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(runtime.close(), .3)
        assert not runtime.retirement_confirmed
        with pytest.raises(RuntimeError, match="retirement_pending"):
            factory.create(None, ingress_token=None)
        release.set()
        await asyncio.gather(*tuple(runtime._scheduler._physical_tasks), return_exceptions=True)
        assert not (await task).pcm16
        await runtime.close()
        assert runtime.retired
        successor = factory.create(None, ingress_token=None)
        await successor.close()
    finally:
        release.set()
        await asyncio.gather(task, *tuple(runtime._scheduler._physical_tasks), return_exceptions=True)
        await runtime.close()


@pytest.mark.parametrize("deadline", ["capture", "finish"])
async def test_expired_operation_is_rejected_before_constructing_model_work(deadline):
    runtime = _factory().create(None, ingress_token=None)
    calls = []
    def construct_work():
        calls.append(True)
        return _Tse().start()
    if deadline == "capture":
        runtime._capture_deadline = 0
    else:
        runtime._finish_deadline = 0
    try:
        with pytest.raises(RuntimeError, match="deadline_expired"):
            await runtime._await_operation(construct_work)
        assert not calls
        assert not runtime._operations
    finally:
        await runtime.close()


async def test_quality_explicit_physical_owner_blocks_handover_after_normal_return():
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    class Quality:
        task = None
        @property
        def retirement_confirmed(self):
            return self.task is None or self.task.done()
        def background(self):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
        async def analyze_async(self, pcm16, sample_rate_hz):
            if self.task is None:
                self.task = asyncio.create_task(asyncio.to_thread(self.background))
            return PrewireQualitySummary()

    quality = Quality()
    factory = _factory(quality=quality)
    runtime = factory.create(None, ingress_token=None)
    try:
        assert (await _feed(runtime)).decision is InterceptionDecision.KEEP
        await asyncio.wait_for(entered.wait(), 1)
        await runtime.close()
        assert not runtime.retirement_confirmed
        with pytest.raises(RuntimeError, match="retirement_pending"):
            factory.create(None, ingress_token=None)
        release.set()
        await quality.task
        assert runtime.retired
        successor = factory.create(None, ingress_token=None)
        await successor.close()
    finally:
        release.set()
        if quality.task is not None:
            await quality.task
        await runtime.close()


@pytest.mark.parametrize("evidence", [None, 1, "unconfirmed", RuntimeError("owner_unavailable"), AttributeError("owner_unavailable")])
async def test_quality_owner_missing_affirmative_evidence_keeps_slot(evidence):
    class Quality:
        state = evidence
        @property
        def retirement_confirmed(self):
            if isinstance(self.state, Exception):
                raise self.state
            return self.state
        async def analyze_async(self, pcm16, sample_rate_hz):
            return PrewireQualitySummary()
    quality = Quality()
    factory = _factory(quality=quality)
    runtime = factory.create(None, ingress_token=None)
    try:
        await _feed(runtime)
        await runtime.close()
        assert not runtime.retired
        with pytest.raises(RuntimeError, match="retirement_pending"):
            factory.create(None, ingress_token=None)
        quality.state = True
        assert runtime.retired
    finally:
        quality.state = True
        await runtime.close()


async def test_dynamic_quality_owner_false_keeps_slot_until_true():
    class Quality:
        state = False
        def __getattr__(self, name):
            if name == "retirement_confirmed":
                return self.state
            raise AttributeError(name)
        async def analyze_async(self, pcm16, sample_rate_hz):
            return PrewireQualitySummary()
    quality = Quality()
    factory = _factory(quality=quality)
    runtime = factory.create(None, ingress_token=None)
    try:
        await _feed(runtime)
        await runtime.close()
        assert not runtime.retired
        with pytest.raises(RuntimeError, match="retirement_pending"):
            factory.create(None, ingress_token=None)
        quality.state = True
        assert runtime.retired
    finally:
        quality.state = True
        await runtime.close()
