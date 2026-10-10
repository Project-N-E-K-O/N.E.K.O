"""Real runtime/gate/scheduler delivery with explicitly research separators."""
from __future__ import annotations

from dataclasses import replace
import asyncio
import threading

import numpy as np
import pytest

from main_logic.voice_identity_service.candidate_identity import CandidateIdentitySelector
from main_logic.voice_identity_service.prewire_gate.candidate_contracts import CandidateAudio, CandidateBatch, CandidateBinding
from main_logic.voice_identity_service.prewire_gate.decision import CalibratedIdentityEvidence, CalibratedIdentityOutcome
from main_logic.voice_identity_service.prewire_gate.decision import PrewireQualitySummary
from main_logic.voice_identity_service.prewire_gate.scheduler import ControlledScoringScheduler, ScoringWindowPlan
from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory, InterceptionRuntimeError
from main_logic.voice_identity_service.tse.contracts import TseAudioChunk
from main_logic.voice_input.interception import InterceptionDecision
from main_logic.voice_input.interception_events import InterceptionOutputKind
from tests.unit.voice_identity_service.test_interception_runtime import _config, _Classifier, _Tse

pytestmark = pytest.mark.unit_fast


class ResearchCandidateClassifier:
    def __init__(self, binding):
        self.binding = binding

    def require_candidate_support(self, binding, *, sample_counts, decision_sample_ranges):
        assert binding == self.binding
        assert all(count == 1600 for count in sample_counts)
        assert not decision_sample_ranges or decision_sample_ranges == ((1600, 0, 600),)

    def classify(self, observation):
        state = CalibratedIdentityOutcome.OWNER if observation.raw_similarity > 0.7 else CalibratedIdentityOutcome.NONOWNER
        return CalibratedIdentityEvidence(state, "research_candidate_only")


class CandidateScorer:
    def __init__(self):
        self.calls = []

    def score(self, pcm16, sample_rate_hz):
        self.calls.append(pcm16)
        value = int.from_bytes(pcm16[:2], "little", signed=True)
        return 0.9 if value > 0 else 0.1


class ForbiddenRawScorer:
    def score(self, pcm16, sample_rate_hz):
        raise AssertionError("raw mixed PCM must never be rescored in candidate mode")


def candidate_factory(backend, bindings):
    def create(stream):
        binding = CandidateBinding(stream, "profile", "model", "config", "research-separator", "enrolled-reference", "a" * 64, "b" * 64)
        bindings.append(binding)
        scheduler = ControlledScoringScheduler(backend, window_plan=ScoringWindowPlan((1600,)), max_outstanding_jobs=2, max_buffered_pcm_bytes=6400, deadline_seconds=1, close_timeout_seconds=.02)
        return CandidateIdentitySelector(binding, scheduler=scheduler, classifier=ResearchCandidateClassifier(binding), deadline_seconds=1, max_buffered_pcm_bytes=6400)
    return create


class HalfAmplitudeTse(_Tse):
    async def push(self, pcm, *, start_sample):
        return [TseAudioChunk(start_sample, start_sample + pcm.size, pcm * .5)]


async def feed(runtime, count=400):
    return await runtime.process(b"\x00\x20" * count, sample_rate_hz=16000, generation="owner", ingress_token=None, captured_at=None)


@pytest.mark.asyncio
async def test_actual_single_candidate_is_scored_and_delivered_without_raw_score():
    scorer = CandidateScorer()
    bindings = []
    factory = PrewireInterceptionFactory(_config(), score_backend=ForbiddenRawScorer(), classifier=_Classifier(), tse_factory=lambda _: HalfAmplitudeTse(), candidate_factory=candidate_factory(scorer, bindings))
    runtime = factory.create("owner", ingress_token=None)
    try:
        first = await feed(runtime, 1600)
        assert first.decision is InterceptionDecision.PENDING
        second = await feed(runtime)
        assert second.decision is InterceptionDecision.KEEP
        candidate_pcm = np.full(1600, 8192 / 32768 * .5, dtype=np.float32)
        candidate_pcm = (candidate_pcm * 32767).astype("<i2").tobytes()
        assert scorer.calls == [candidate_pcm, candidate_pcm]
        assert second.pcm16 == candidate_pcm[:800] * 2
        assert all(event.kind is InterceptionOutputKind.AUDIO for event in second.events)
        assert runtime._candidate_source.buffered_pcm_bytes == 2400
        finished = await runtime.finish()
        assert finished.events[-1].kind is InterceptionOutputKind.END
        assert len(scorer.calls) == 2  # Unsupported short tails never score raw.
        assert runtime.retirement_confirmed
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_quality_analysis_receives_the_same_real_candidate_as_identity_score():
    scorer = CandidateScorer()
    bindings = []

    class Quality:
        def __init__(self):
            self.calls = []

        def analyze(self, pcm16, sample_rate_hz):
            self.calls.append(pcm16)
            return PrewireQualitySummary(speech_samples=len(pcm16) // 2, continuous=True)

    quality = Quality()
    runtime = PrewireInterceptionFactory(_config(), score_backend=ForbiddenRawScorer(), classifier=_Classifier(), tse_factory=lambda _: HalfAmplitudeTse(), candidate_factory=candidate_factory(scorer, bindings), quality_analyzer=quality).create("owner", ingress_token=None)
    try:
        await feed(runtime, 2000)
        assert quality.calls == scorer.calls
        assert len(quality.calls) == 2
        assert all(pcm != b"\x00\x20" * 1600 for pcm in quality.calls)
    finally:
        await runtime.close()


class WindowSeparatorSource:
    """Complete-window separator fixture; anonymous channel order may change."""
    def __init__(self, binding, windows):
        self.binding = binding
        self.windows = iter(windows)
        self.closed = False

    def batch_for(self, spec, binding):
        assert not self.closed and binding == self.binding
        values = next(self.windows)
        return CandidateBatch(binding, spec, tuple(
            CandidateAudio(binding, str(index), spec.scoring_range, value.to_bytes(2, "little", signed=True) * spec.scoring_range.sample_count)
            for index, value in enumerate(values)
        ))

    def discard_before(self, sample):
        pass

    def clear(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("windows,kept", [
    (((100, -100), (-100, 200)), True),
    (((), (100, -100)), False),
    (((100, 200), (-100, 100)), False),
    (((-100, -200), (100, -100)), False),
])
async def test_double_candidates_are_reidentified_and_gap_resets_owner_streak(windows, kept):
    backend = CandidateScorer()
    bindings = []
    factory = PrewireInterceptionFactory(_config(), score_backend=ForbiddenRawScorer(), classifier=_Classifier(), tse_factory=lambda _: _Tse(), candidate_factory=candidate_factory(backend, bindings), candidate_source_factory=lambda _: WindowSeparatorSource(bindings[-1], windows))
    runtime = factory.create("owner", ingress_token=None)
    try:
        first = await feed(runtime, 1600)
        second = await feed(runtime)
        if kept:
            assert second.pcm16 == b"\x64\x00" * 400 + b"\xc8\x00" * 400
            assert len(backend.calls) == 4
        else:
            assert first.pcm16 == second.pcm16 == b""
            assert any(event.kind is InterceptionOutputKind.GAP for event in first.events)
        assert runtime._gate.held_pcm_bytes == 2400
    finally:
        await runtime.close()


class BlockedNativeScorer(CandidateScorer):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def score(self, pcm16, sample_rate_hz):
        self.started.set()
        self.release.wait(2)
        return .9


@pytest.mark.asyncio
async def test_native_candidate_score_must_physically_exit_before_factory_replacement():
    backend = BlockedNativeScorer()
    bindings = []
    factory = PrewireInterceptionFactory(replace(_config(), scoring_deadline_seconds=.03, scoring_close_timeout_seconds=.03), score_backend=ForbiddenRawScorer(), classifier=_Classifier(), tse_factory=lambda _: _Tse(), candidate_factory=candidate_factory(backend, bindings))
    runtime = factory.create("owner", ingress_token=None)
    try:
        result = await feed(runtime, 1600)
        assert backend.started.is_set()
        assert result.decision is InterceptionDecision.UNAVAILABLE
        assert not runtime.retirement_confirmed
        with pytest.raises(InterceptionRuntimeError, match="previous_runtime_retirement_pending"):
            factory.create("owner", ingress_token=None)
        backend.release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(runtime._candidate_selector._scheduler._physical_tasks)), timeout=1)
        await runtime.close()
        assert runtime.retirement_confirmed
        replacement = factory.create("owner", ingress_token=None)
        assert replacement._stream != runtime._stream
        await replacement.close()
    finally:
        backend.release.set()
        await runtime.close()
