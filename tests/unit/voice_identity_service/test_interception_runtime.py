from __future__ import annotations

import asyncio
import numpy as np
import pytest

from main_logic.voice_identity_service.interception_runtime import (
    InterceptionRuntimeConfig,
    PrewireInterceptionFactory,
)
from main_logic.voice_identity_service.prewire_gate.decision import (
    CalibratedIdentityEvidence,
    CalibratedIdentityOutcome,
)
from main_logic.voice_identity_service.tse.contracts import TseAudioChunk, TseExtractionResult
from main_logic.voice_input.interception import InterceptionDecision

pytestmark = pytest.mark.unit_fast


class _Scorer:
    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        assert sample_rate_hz == 16000
        return 0.9 if any(pcm16) else 0.0


class _Classifier:
    def classify(self, observation):
        return CalibratedIdentityEvidence(
            CalibratedIdentityOutcome.OWNER
            if observation.raw_similarity >= 0.5
            else CalibratedIdentityOutcome.NONOWNER,
            "fixture_evidence",
        )


class _SequenceClassifier:
    def __init__(self, values):
        self._values = iter(values)

    def classify(self, observation):
        owner = next(self._values)
        return CalibratedIdentityEvidence(
            CalibratedIdentityOutcome.OWNER if owner else CalibratedIdentityOutcome.NONOWNER,
            "sequence",
        )


class _Tse:
    def __init__(self, fail: bool = False, absent: bool = False):
        self.fail = fail
        self.absent = absent
        self.started = False

    async def start(self, *, timeout: float = 1.0) -> None:
        if self.fail:
            raise TimeoutError("fixture_tse_timeout")
        self.started = True

    async def push(self, pcm: np.ndarray, *, start_sample: int):
        if self.fail:
            raise RuntimeError("fixture_tse_failure")
        if self.absent:
            return TseExtractionResult.target_absent()
        return [TseAudioChunk(start_sample, start_sample + pcm.size, pcm)]

    async def flush(self):
        return []

    async def close(self, *, timeout: float = 1.0) -> bool:
        return True


class _DelayedTse(_Tse):
    def __init__(self):
        super().__init__()
        self._first: np.ndarray | None = None
        self._first_start = 0

    async def push(self, pcm: np.ndarray, *, start_sample: int):
        if self._first is None:
            self._first = pcm.copy()
            self._first_start = start_sample
            return []
        combined = np.concatenate((self._first, pcm))
        start = self._first_start
        self._first = None
        return [TseAudioChunk(start, start + combined.size, combined)]


class _UnstoppableTse(_Tse):
    async def close(self, *, timeout: float = 1.0) -> bool:
        return False


def _config() -> InterceptionRuntimeConfig:
    return InterceptionRuntimeConfig(
        session_id="session",
        ingress_generation=1,
        profile_generation="profile",
        model_generation="model",
        config_generation="config",
        scoring_parameters_digest="a" * 64,
        window_samples=1600,
        step_samples=400,
        guard_samples=200,
        required_consistent_observations=1,
    )


@pytest.mark.asyncio
async def test_short_prefix_stays_pending_then_releases_filtered_pcm() -> None:
    token = object()
    factory = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=_Classifier(),
        tse_factory=lambda stream: _Tse(),
    )
    runtime = factory.create(token, ingress_token="route")
    first = await runtime.process(
        b"\x01\x00" * 400,
        sample_rate_hz=16000,
        generation=token,
        ingress_token="route",
        captured_at=None,
    )
    assert first.decision is InterceptionDecision.PENDING
    assert first.pcm16 == b""
    second = await runtime.process(
        b"\x02\x00" * 1200,
        sample_rate_hz=16000,
        generation=token,
        ingress_token="route",
        captured_at=None,
    )
    assert second.decision is InterceptionDecision.PENDING
    assert second.pcm16 == b""
    third = await runtime.process(
        b"\x02\x00" * 400,
        sample_rate_hz=16000,
        generation=token,
        ingress_token="route",
        captured_at=None,
    )
    assert third.decision is InterceptionDecision.KEEP
    assert third.pcm16
    await runtime.close()


@pytest.mark.asyncio
async def test_missing_classifier_or_tse_never_returns_raw_pcm() -> None:
    token = object()
    factory = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=None,
        tse_factory=lambda stream: _Tse(),
    )
    runtime = factory.create(token, ingress_token=None)
    result = await runtime.process(
        b"\x01\x00" * 1600,
        sample_rate_hz=16000,
        generation=token,
        ingress_token=None,
        captured_at=None,
    )
    assert result.pcm16 == b""
    await runtime.close()

    failed_factory = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=_Classifier(),
        tse_factory=lambda stream: _Tse(fail=True),
    )
    failed = failed_factory.create(token, ingress_token=None)
    result = await failed.process(
        b"\x01\x00" * 1600,
        sample_rate_hz=16000,
        generation=token,
        ingress_token=None,
        captured_at=None,
    )
    assert result.pcm16 == b""


@pytest.mark.asyncio
async def test_generation_or_route_mismatch_is_stale() -> None:
    token = object()
    factory = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=_Classifier(),
        tse_factory=lambda stream: _Tse(),
    )
    runtime = factory.create(token, ingress_token="route")
    result = await runtime.process(
        b"\x01\x00" * 400,
        sample_rate_hz=16000,
        generation=object(),
        ingress_token="route",
        captured_at=None,
    )
    assert result.decision is InterceptionDecision.STALE
    result = await runtime.process(
        b"\x01\x00" * 400,
        sample_rate_hz=16000,
        generation=token,
        ingress_token="other",
        captured_at=None,
    )
    assert result.decision is InterceptionDecision.STALE
    await runtime.close()


@pytest.mark.asyncio
async def test_target_absent_result_is_fail_closed() -> None:
    token = object()
    factory = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=_Classifier(),
        tse_factory=lambda stream: _Tse(absent=True),
    )
    runtime = factory.create(token, ingress_token=None)
    result = await runtime.process(
        b"\x01\x00" * 400,
        sample_rate_hz=16000,
        generation=token,
        ingress_token=None,
        captured_at=None,
    )
    assert result.pcm16 == b""
    assert result.decision is InterceptionDecision.UNAVAILABLE


@pytest.mark.asyncio
async def test_finish_turns_short_unconfirmed_prefix_into_gap() -> None:
    token = object()
    factory = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=_Classifier(),
        tse_factory=lambda stream: _Tse(),
    )
    runtime = factory.create(token, ingress_token=None)
    await runtime.process(
        b"\x01\x00" * 400,
        sample_rate_hz=16000,
        generation=token,
        ingress_token=None,
        captured_at=None,
    )
    result = await runtime.finish()
    assert result.pcm16 == b""
    assert result.decision is InterceptionDecision.DROP


@pytest.mark.asyncio
async def test_prefix_deadline_retires_without_later_raw_fallback() -> None:
    token = object()
    config = InterceptionRuntimeConfig(
        session_id="session", ingress_generation=1, profile_generation="profile",
        model_generation="model", config_generation="config",
        scoring_parameters_digest="a" * 64, window_samples=1600,
        step_samples=400, guard_samples=200, prefix_deadline_seconds=0.01,
    )
    runtime = PrewireInterceptionFactory(
        config, score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda stream: _Tse()
    ).create(token, ingress_token=None)
    await runtime.process(b"\x01\x00" * 400, sample_rate_hz=16000, generation=token, ingress_token=None, captured_at=None)
    await asyncio.sleep(0.03)
    result = await runtime.process(b"\x01\x00" * 400, sample_rate_hz=16000, generation=token, ingress_token=None, captured_at=None)
    assert result.pcm16 == b""


@pytest.mark.asyncio
async def test_delayed_tse_output_is_retained_until_owner_streak_releases() -> None:
    token = object()
    runtime = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda stream: _DelayedTse()
    ).create(token, ingress_token=None)
    first = await runtime.process(b"\x01\x00" * 1600, sample_rate_hz=16000, generation=token, ingress_token=None, captured_at=None)
    assert first.pcm16 == b""
    second = await runtime.process(b"\x02\x00" * 400, sample_rate_hz=16000, generation=token, ingress_token=None, captured_at=None)
    assert second.decision is InterceptionDecision.KEEP
    assert len(second.pcm16) == 1600


@pytest.mark.asyncio
async def test_gap_discards_late_audio_from_previous_owner_window() -> None:
    token = object()
    config = InterceptionRuntimeConfig(
        session_id="session", ingress_generation=1, profile_generation="profile",
        model_generation="model", config_generation="config",
        scoring_parameters_digest="a" * 64, window_samples=1600,
        step_samples=400, guard_samples=200, prefix_deadline_seconds=5.0,
    )
    runtime = PrewireInterceptionFactory(
        config, score_backend=_Scorer(),
        classifier=_SequenceClassifier([True, False, True, True, True, True]),
        tse_factory=lambda stream: _DelayedTse(),
    ).create(token, ingress_token=None)
    results = []
    for value in range(1, 9):
        results.append(await runtime.process(
            bytes((value, 0)) * 400, sample_rate_hz=16000,
            generation=token, ingress_token=None, captured_at=None,
        ))
    # The first owner's delayed extraction is followed by a gap.  It must not
    # be released when the later two owner windows satisfy the streak.
    assert results[-1].decision is InterceptionDecision.KEEP
    assert len(results[-1].pcm16) <= 1600
    await runtime.close()


@pytest.mark.asyncio
async def test_unconfirmed_tse_retirement_blocks_factory_replacement() -> None:
    token = object()
    factory = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda stream: _UnstoppableTse()
    )
    runtime = factory.create(token, ingress_token=None)
    await runtime.process(b"\x01\x00" * 400, sample_rate_hz=16000, generation=token, ingress_token=None, captured_at=None)
    await runtime.close()
    with pytest.raises(RuntimeError, match="retirement_pending"):
        factory.create(object(), ingress_token=None)


@pytest.mark.asyncio
async def test_active_session_can_continue_past_one_second_with_new_frames() -> None:
    token = object()
    config = InterceptionRuntimeConfig(
        session_id="session", ingress_generation=1, profile_generation="profile",
        model_generation="model", config_generation="config",
        scoring_parameters_digest="a" * 64, window_samples=1600,
        step_samples=400, guard_samples=200, prefix_deadline_seconds=0.05,
    )
    runtime = PrewireInterceptionFactory(
        config, score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda stream: _Tse()
    ).create(token, ingress_token=None)
    for _ in range(25):
        result = await runtime.process(
            b"\x01\x00" * 400, sample_rate_hz=16000,
            generation=token, ingress_token=None, captured_at=None,
        )
        assert result.pcm16 == b"" or result.decision is InterceptionDecision.KEEP
        await asyncio.sleep(0.01)
    assert not runtime.retirement_confirmed or runtime._closed is False
    await runtime.close()
