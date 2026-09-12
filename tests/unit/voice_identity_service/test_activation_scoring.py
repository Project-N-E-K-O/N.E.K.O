from __future__ import annotations

import asyncio
import multiprocessing
from threading import Event
import time
from typing import Any

import numpy as np
import pytest

from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
)
from main_logic.asr_client.speaker_shadow.campplus import CAMPPLUS_EMBEDDING_DIM
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service import activation_scoring
from main_logic.voice_identity_service.activation_scoring import (
    ActivationScoreIdentity,
    ActivationScoreResult,
    ActivationScoreStatus,
    CampPlusActivationScorer,
)


def _profile(generation: str = "profile-1") -> SpeakerProfile:
    identity = SpeakerModelIdentity(
        CAMPPLUS_MODEL_ID,
        CAMPPLUS_MODEL_REVISION,
        CAMPPLUS_EMBEDDING_DIM,
    )
    reference = SpeakerReference(identity, np.ones(CAMPPLUS_EMBEDDING_DIM))
    try:
        return SpeakerProfile(generation, reference)
    finally:
        reference.close()


class _Backend:
    def __init__(self, *, score: float = 0.75, load: bool = True) -> None:
        self.score_value = score
        self.load_value = load
        self.closed = False
        self.score_calls = 0

    def load(self) -> bool:
        return self.load_value

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        assert pcm16
        assert sample_rate_hz == 16_000
        self.score_calls += 1
        return self.score_value

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_score_returns_identity_without_asr_fields() -> None:
    profile = _profile()
    backend = _Backend()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=3,
        backend_factory=lambda: backend,
    )
    identity = ActivationScoreIdentity("profile-1", 3, 7)
    try:
        assert scorer.profile_generation == "profile-1"
        assert scorer.scorer_generation == 3
        assert await scorer.prepare() is ActivationScoreStatus.READY
        assert await scorer.prepare() is ActivationScoreStatus.READY
        result = await scorer.score(
            identity, b"\x01\x00" * 24_000, sample_rate_hz=16_000
        )
        assert result.identity == identity
        assert result.status is ActivationScoreStatus.READY
        assert result.similarity == pytest.approx(0.75)
        assert backend.score_calls == 1
    finally:
        await scorer.close()
        profile.close()


@pytest.mark.asyncio
async def test_stale_identity_is_rejected_before_model_work() -> None:
    profile = _profile()
    backend = _Backend()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=3,
        backend_factory=lambda: backend,
    )
    try:
        result = await scorer.score(
            ActivationScoreIdentity("profile-1", 2, 7),
            b"\x01\x00" * 24_000,
            sample_rate_hz=16_000,
        )
        assert result.status is ActivationScoreStatus.CLOSED
        assert backend.score_calls == 0
    finally:
        await scorer.close()
        profile.close()


@pytest.mark.asyncio
async def test_non_finite_backend_result_is_not_ready() -> None:
    profile = _profile()
    backend = _Backend(score=float("nan"))
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        backend_factory=lambda: backend,
    )
    try:
        result = await scorer.score(
            ActivationScoreIdentity("profile-1", 1, 1),
            b"\x01\x00" * 24_000,
            sample_rate_hz=16_000,
        )
        assert result.status is ActivationScoreStatus.FAILED
        assert result.similarity is None
    finally:
        await scorer.close()
        profile.close()


class _BlockingBackend(_Backend):
    def __init__(self) -> None:
        super().__init__()
        self.release = Event()

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        del pcm16, sample_rate_hz
        self.release.wait(timeout=1.0)
        return self.score_value

    def close(self) -> None:
        super().close()
        self.release.set()


class _IgnoringCloseBackend(_BlockingBackend):
    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        self.score_calls += 1
        return super().score(pcm16, sample_rate_hz)

    def close(self) -> None:
        self.closed = True


class _BlockingCloseBackend(_Backend):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = Event()
        self.release_close = Event()

    def close(self) -> None:
        self.close_started.set()
        self.release_close.wait(timeout=1.0)
        super().close()


class _SpawnBackend:
    def __init__(
        self,
        score_started: Any,
        *,
        block_score: bool = False,
        block_close: bool = False,
    ) -> None:
        self._score_started = score_started
        self._block_score = block_score
        self._block_close = block_close

    def load(self) -> bool:
        return True

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        assert pcm16
        assert sample_rate_hz == 16_000
        self._score_started.set()
        if self._block_score:
            time.sleep(60)
        return 0.75

    def close(self) -> None:
        if self._block_close:
            time.sleep(60)


class _SpawnBackendFactory:
    def __init__(
        self,
        score_started: Any,
        *,
        block_score: bool = False,
        block_close: bool = False,
    ) -> None:
        self._score_started = score_started
        self._block_score = block_score
        self._block_close = block_close

    def __call__(self) -> _SpawnBackend:
        return _SpawnBackend(
            self._score_started,
            block_score=self._block_score,
            block_close=self._block_close,
        )

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_timeout_closes_backend_and_joins_worker() -> None:
    profile = _profile()
    backend = _BlockingBackend()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        timeout_seconds=0.01,
        backend_factory=lambda: backend,
    )
    try:
        result = await scorer.score(
            ActivationScoreIdentity("profile-1", 1, 1),
            b"\x01\x00" * 24_000,
            sample_rate_hz=16_000,
        )
        assert result.status is ActivationScoreStatus.TIMED_OUT
        assert backend.closed is True
        assert scorer._active_task is None
    finally:
        await scorer.close()
        profile.close()


@pytest.mark.asyncio
async def test_injected_backend_ignoring_close_times_out_bounded_and_is_terminal() -> (
    None
):
    profile = _profile()
    backend = _IgnoringCloseBackend()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        timeout_seconds=0.01,
        shutdown_timeout_seconds=0.02,
        backend_factory=lambda: backend,
    )
    identity = ActivationScoreIdentity("profile-1", 1, 1)
    started_at = asyncio.get_running_loop().time()
    try:
        result = await scorer.score(
            identity,
            b"\x01\x00" * 24_000,
            sample_rate_hz=16_000,
        )
        assert result.status is ActivationScoreStatus.TIMED_OUT
        assert asyncio.get_running_loop().time() - started_at < 0.2
        assert scorer.closed is True
        assert backend.closed is True

        score_calls_before_successor = backend.score_calls
        successor = await scorer.score(
            ActivationScoreIdentity("profile-1", 1, 2),
            b"\x01\x00" * 24_000,
            sample_rate_hz=16_000,
        )
        assert successor.status is ActivationScoreStatus.CLOSED
        assert backend.score_calls == score_calls_before_successor
        await asyncio.wait_for(scorer.close(), timeout=0.2)
    finally:
        backend.release.set()
        await asyncio.sleep(0.02)
        await scorer.close()
        profile.close()


@pytest.mark.asyncio
async def test_cancelled_score_releases_backend_before_propagating() -> None:
    profile = _profile()
    backend = _BlockingBackend()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        backend_factory=lambda: backend,
    )
    task = asyncio.create_task(
        scorer.score(
            ActivationScoreIdentity("profile-1", 1, 1),
            b"\x01\x00" * 24_000,
            sample_rate_hz=16_000,
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.closed is True
    assert scorer._active_task is None
    await scorer.close()
    profile.close()


@pytest.mark.asyncio
async def test_cancelled_close_waits_for_real_scorer_cleanup_before_propagating() -> None:
    profile = _profile()
    backend = _BlockingCloseBackend()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        backend_factory=lambda: backend,
    )
    closing = asyncio.create_task(scorer.close())
    assert await asyncio.to_thread(backend.close_started.wait, 1.0)

    closing.cancel()
    backend.release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(closing, timeout=1.0)

    assert scorer.closed is True
    assert backend.closed is True
    assert scorer._close_task is not None
    assert scorer._close_task.done()
    await scorer.close()
    profile.close()


@pytest.mark.asyncio
async def test_default_backend_scores_in_real_spawn_process(monkeypatch) -> None:
    context = multiprocessing.get_context("spawn")
    score_started = context.Event()
    factory = _SpawnBackendFactory(score_started)
    monkeypatch.setattr(
        activation_scoring,
        "CampPlusBackendFactory",
        lambda *_args, **_kwargs: factory,
    )
    profile = _profile()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        timeout_seconds=5.0,
    )
    try:
        assert await scorer.prepare() is ActivationScoreStatus.READY
        host = scorer._host
        assert host is not None
        assert host.alive is True
        result = await scorer.score(
            ActivationScoreIdentity("profile-1", 1, 1),
            b"\x01\x00" * 24_000,
            sample_rate_hz=16_000,
        )
        assert result.status is ActivationScoreStatus.READY
        assert result.similarity == pytest.approx(0.75)
        assert score_started.is_set()
    finally:
        await scorer.close()
        profile.close()
    assert host.alive is False


@pytest.mark.asyncio
async def test_process_backend_ignoring_close_is_force_terminated(monkeypatch) -> None:
    context = multiprocessing.get_context("spawn")
    factory = _SpawnBackendFactory(context.Event(), block_close=True)
    monkeypatch.setattr(
        activation_scoring,
        "CampPlusBackendFactory",
        lambda *_args, **_kwargs: factory,
    )
    profile = _profile()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        timeout_seconds=5.0,
        shutdown_timeout_seconds=0.2,
    )
    assert await scorer.prepare() is ActivationScoreStatus.READY
    host = scorer._host
    assert host is not None
    started_at = asyncio.get_running_loop().time()
    await scorer.close()
    elapsed = asyncio.get_running_loop().time() - started_at
    profile.close()
    assert elapsed < 1.0
    assert host.alive is False


@pytest.mark.asyncio
async def test_process_backend_ignoring_cancellation_is_force_terminated(
    monkeypatch,
) -> None:
    context = multiprocessing.get_context("spawn")
    score_started = context.Event()
    factory = _SpawnBackendFactory(score_started, block_score=True)
    monkeypatch.setattr(
        activation_scoring,
        "CampPlusBackendFactory",
        lambda *_args, **_kwargs: factory,
    )
    profile = _profile()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        timeout_seconds=5.0,
        shutdown_timeout_seconds=0.2,
    )
    assert await scorer.prepare() is ActivationScoreStatus.READY
    host = scorer._host
    assert host is not None
    task = asyncio.create_task(
        scorer.score(
            ActivationScoreIdentity("profile-1", 1, 1),
            b"\x01\x00" * 24_000,
            sample_rate_hz=16_000,
        )
    )
    assert await asyncio.to_thread(score_started.wait, 2.0)
    started_at = asyncio.get_running_loop().time()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    elapsed = asyncio.get_running_loop().time() - started_at
    try:
        assert elapsed < 1.0
        assert scorer.closed is True
        assert host.alive is False
        successor = await scorer.score(
            ActivationScoreIdentity("profile-1", 1, 2),
            b"\x01\x00" * 24_000,
            sample_rate_hz=16_000,
        )
        assert successor.status is ActivationScoreStatus.CLOSED
    finally:
        await scorer.close()
        profile.close()


@pytest.mark.asyncio
async def test_close_cancels_blocked_process_score_before_closing_host(
    monkeypatch,
) -> None:
    context = multiprocessing.get_context("spawn")
    score_started = context.Event()
    factory = _SpawnBackendFactory(score_started, block_score=True)
    monkeypatch.setattr(
        activation_scoring,
        "CampPlusBackendFactory",
        lambda *_args, **_kwargs: factory,
    )
    profile = _profile()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        timeout_seconds=5.0,
        shutdown_timeout_seconds=0.2,
    )
    assert await scorer.prepare() is ActivationScoreStatus.READY
    host = scorer._host
    assert host is not None
    score_task = asyncio.create_task(
        scorer.score(
            ActivationScoreIdentity("profile-1", 1, 1),
            b"\x01\x00" * 24_000,
            sample_rate_hz=16_000,
        )
    )
    assert await asyncio.to_thread(score_started.wait, 2.0)
    started_at = asyncio.get_running_loop().time()
    await scorer.close()
    elapsed = asyncio.get_running_loop().time() - started_at
    try:
        result = await asyncio.wait_for(score_task, timeout=1.0)
        assert result.status is ActivationScoreStatus.CLOSED
        assert elapsed < 1.0
        assert host.alive is False
        assert scorer._active_task is None
    finally:
        await scorer.close()
        profile.close()


@pytest.mark.asyncio
async def test_prepare_reports_unavailable_and_close_is_terminal() -> None:
    profile = _profile()
    backend = _Backend(load=False)
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        backend_factory=lambda: backend,
    )
    assert await scorer.prepare() is ActivationScoreStatus.MODEL_UNAVAILABLE
    await scorer.close()
    assert scorer.closed is True
    assert await scorer.prepare() is ActivationScoreStatus.CLOSED
    result = await scorer.score(
        ActivationScoreIdentity("profile-1", 1, 1),
        b"\x01\x00" * 24_000,
        sample_rate_hz=16_000,
    )
    assert result.status is ActivationScoreStatus.CLOSED
    profile.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pcm16", "sample_rate_hz"),
    [(b"", 16_000), (b"\x00", 16_000), (b"\x00\x00", 48_000)],
)
async def test_invalid_audio_never_reaches_backend(
    pcm16: bytes,
    sample_rate_hz: int,
) -> None:
    profile = _profile()
    backend = _Backend()
    scorer = CampPlusActivationScorer(
        profile,
        scorer_generation=1,
        backend_factory=lambda: backend,
    )
    try:
        result = await scorer.score(
            ActivationScoreIdentity("profile-1", 1, 1),
            pcm16,
            sample_rate_hz=sample_rate_hz,
        )
        assert result.status is ActivationScoreStatus.INVALID_AUDIO
        assert backend.score_calls == 0
    finally:
        await scorer.close()
        profile.close()


def test_score_contract_rejects_invalid_identity_and_similarity() -> None:
    with pytest.raises(ValueError):
        ActivationScoreIdentity("", 1, 1)
    with pytest.raises(ValueError):
        ActivationScoreIdentity("profile", 0, 1)
    with pytest.raises(ValueError):
        ActivationScoreIdentity("profile", 1, 0)
    identity = ActivationScoreIdentity("profile", 1, 1)
    with pytest.raises(ValueError):
        ActivationScoreResult(identity, ActivationScoreStatus.READY, float("nan"))
    with pytest.raises(ValueError):
        ActivationScoreResult(identity, ActivationScoreStatus.FAILED, 0.5)


def test_scorer_constructor_rejects_incompatible_profile() -> None:
    identity = SpeakerModelIdentity("other", "v1", CAMPPLUS_EMBEDDING_DIM)
    reference = SpeakerReference(identity, np.ones(CAMPPLUS_EMBEDDING_DIM))
    profile = SpeakerProfile("profile", reference)
    reference.close()
    try:
        with pytest.raises(ValueError, match="does not match CAM"):
            CampPlusActivationScorer(profile, scorer_generation=1)
    finally:
        profile.close()
