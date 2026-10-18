"""Real runtime/gate/scheduler linkage with explicit acoustic protocol fixtures."""

from __future__ import annotations

import json

import numpy as np
import pytest

from main_logic.voice_identity_service.candidate_calibration import CandidateCalibrationContract
from main_logic.voice_identity_service.candidate_identity import CandidateAudio, CandidateBatch, CandidateBinding, CandidateIdentitySelector
from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory
from main_logic.voice_identity_service.prewire_gate.contracts import PrewireStreamKey
from main_logic.voice_identity_service.prewire_gate.decision import CalibratedIdentityEvidence, CalibratedIdentityOutcome
from main_logic.voice_identity_service.prewire_gate.scheduler import ControlledScoringScheduler, ScoringWindowPlan
from main_logic.voice_identity_service.tse.contracts import TseAudioChunk
from main_logic.voice_input.interception import InterceptionDecision
from main_logic.voice_input.interception_events import InterceptionOutputKind
from tests.unit.voice_identity_service.test_interception_runtime import _config, _Tse, _Classifier

pytestmark = pytest.mark.runtime


def bound():
    return CandidateBinding(PrewireStreamKey("session", 1), "profile", "model", "config", "research-separator", "research-reference", "a" * 64, "b" * 64)


class CandidateClassifier:
    def __init__(self):
        metadata = CandidateCalibrationContract("model", "research-separator", "research-reference", "pcm16-16khz-v1", "config", "a" * 64, "b" * 64, (1600,), ((1600, 0, 600),))
        self.contract = CandidateCalibrationContract.from_mapping(json.loads(json.dumps(metadata.to_mapping())))

    def require_candidate_support(self, binding, **kwargs):
        self.contract.require_candidate_support(binding, **kwargs)

    def classify(self, observation):
        outcome = CalibratedIdentityOutcome.OWNER if observation.raw_similarity > .7 else (
            CalibratedIdentityOutcome.NONOWNER if observation.raw_similarity < .3 else CalibratedIdentityOutcome.UNCERTAIN
        )
        return CalibratedIdentityEvidence(outcome, "research_candidate_fixture")


class CandidateScorer:
    def __init__(self):
        self.calls = []

    def score(self, pcm16, sample_rate_hz):
        self.calls.append(pcm16)
        value = int.from_bytes(pcm16[:2], "little", signed=True)
        return .9 if value > 0 else (.1 if value < 0 else .5)


class RawScorerMustNotRun:
    def score(self, pcm16, sample_rate_hz):
        raise AssertionError("raw mixed PCM must not decide candidate delivery")


def make_selector(backend):
    scheduler = ControlledScoringScheduler(backend, window_plan=ScoringWindowPlan((1600,)), max_outstanding_jobs=2, max_buffered_pcm_bytes=6400, deadline_seconds=1, close_timeout_seconds=.1)
    return CandidateIdentitySelector(bound(), scheduler=scheduler, classifier=CandidateClassifier(), deadline_seconds=1, max_buffered_pcm_bytes=6400)


class WindowSource:
    """Inject complete separated windows; never stitch anonymous channels."""

    def __init__(self, windows):
        self.windows = windows
        self.cleared = False
        self.discarded = []

    def batch_for(self, spec, binding):
        values = self.windows(spec.commit_range.start)
        return CandidateBatch(binding, spec, tuple(CandidateAudio(binding, str(index), spec.scoring_range, value.to_bytes(2, "little", signed=True) * spec.scoring_range.sample_count) for index, value in enumerate(values)))

    def discard_before(self, sample):
        self.discarded.append(sample)

    def clear(self):
        self.cleared = True


def runtime(windows=None, worker=None):
    backend = CandidateScorer()
    selector = make_selector(backend)
    source = None if windows is None else WindowSource(windows)
    factory = PrewireInterceptionFactory(_config(), score_backend=RawScorerMustNotRun(), classifier=_Classifier(), tse_factory=lambda _: worker or _Tse(), candidate_factory=lambda _: selector, candidate_source_factory=(lambda _: source) if source is not None else None)
    return factory.create(None, ingress_token=None), backend, source


async def feed(target, count, value=-100):
    return await target.process(value.to_bytes(2, "little", signed=True) * count, sample_rate_hz=16000, generation=None, ingress_token=None, captured_at=None)


@pytest.mark.asyncio
async def test_true_runtime_delivers_the_scored_candidate_under_channel_permutation():
    target, backend, source = runtime(lambda start: (-100, 100) if start == 0 else (200, -100))
    try:
        first = await feed(target, 1600)
        second = await feed(target, 400)
        assert first.decision is InterceptionDecision.PENDING
        assert second.decision is InterceptionDecision.KEEP
        assert second.pcm16 == b"\x64\x00" * 400 + b"\xc8\x00" * 400
        assert [event.kind for event in second.events] == [InterceptionOutputKind.AUDIO, InterceptionOutputKind.AUDIO]
        assert [(event.start_sample, event.end_sample) for event in second.events] == [(0, 400), (400, 800)]
        assert [(event.asr_start_sample, event.asr_end_sample) for event in second.events] == [(0, 400), (400, 800)]
        assert backend.calls == [b"\x9c\xff" * 1600, b"\x64\x00" * 1600, b"\xc8\x00" * 1600, b"\x9c\xff" * 1600]
        assert source.discarded == [400, 800]
        assert second.pcm16 != b"\x9c\xff" * 800
    finally:
        await target.close()
    assert source.cleared
    assert target.retirement_confirmed


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [(), (-100,), (100, 100), (100, 0)])
async def test_real_gate_releases_explicit_gaps_for_zero_guest_or_ambiguous_candidates(values):
    target, backend, _ = runtime(lambda _: values)
    try:
        result = await feed(target, 2000, value=100)
        # The capture remains open (aggregate PENDING); the exact intervals
        # are final GAP events, so later captured audio is still processed.
        assert result.decision is InterceptionDecision.PENDING
        assert result.pcm16 == b""
        assert len(result.events) == 2
        assert all(event.kind is InterceptionOutputKind.GAP for event in result.events)
        assert len(backend.calls) == len(values) * 2
        assert target._gate.held_pcm_bytes < 4000
    finally:
        await target.close()


@pytest.mark.asyncio
async def test_candidate_content_swap_blocks_guest_after_previously_confirmed_owner():
    target, _, _ = runtime(lambda start: (100,) if start < 800 else (-100,))
    try:
        accepted = await feed(target, 2000)
        guest = await feed(target, 400, value=100)
        assert accepted.pcm16 == b"\x64\x00" * 800
        assert guest.pcm16 == b""
        assert guest.events[0].kind is InterceptionOutputKind.GAP
        assert (guest.events[0].start_sample, guest.events[0].end_sample) == (800, 1200)
        assert target._owner_streak == 0
    finally:
        await target.close()


class ConstantTse(_Tse):
    def __init__(self, value):
        super().__init__()
        self.value = value

    async def push(self, pcm, *, start_sample):
        values = np.full(pcm.size, self.value, dtype=np.float32)
        return [TseAudioChunk(start_sample, start_sample + pcm.size, values)]


@pytest.mark.asyncio
@pytest.mark.parametrize("value,allowed", [(0.01, True), (-0.01, False)])
async def test_default_tfmap_adapter_scores_actual_worker_pcm_instead_of_input(value, allowed):
    target, backend, _ = runtime(worker=ConstantTse(value))
    try:
        result = await feed(target, 2000, value=100 if not allowed else -100)
        expected = (np.full(1600, value, dtype=np.float32) * 32767).astype("<i2").tobytes()
        assert backend.calls == [expected, expected]
        assert result.pcm16 == (expected[:1600] if allowed else b"")
        if allowed:
            assert result.decision is InterceptionDecision.KEEP
        else:
            assert all(event.kind is InterceptionOutputKind.GAP for event in result.events)
    finally:
        await target.close()
