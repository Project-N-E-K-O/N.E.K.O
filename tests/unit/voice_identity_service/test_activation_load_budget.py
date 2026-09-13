"""Cold model loading and hot scoring have independent bounded budgets."""

import asyncio
from dataclasses import dataclass
from threading import Event
import time

import pytest

from main_logic.voice_identity_service import activation_scoring
from main_logic.voice_identity_service.activation_scoring import (
    ActivationScoreIdentity, ActivationScoreStatus, CampPlusActivationScorer,
)
from tests.unit.voice_identity_service.test_activation_scoring import _profile


class _SlowLoadBackend:
    def __init__(self, *, block_score=False):
        self.block_score = block_score

    def load(self):
        time.sleep(2.2)
        return True

    def score(self, pcm16, sample_rate_hz):
        if self.block_score:
            time.sleep(60)
        return .75

    def close(self):
        pass


@dataclass
class _SlowLoadFactory:
    block_score: bool = False

    def __call__(self):
        return _SlowLoadBackend(block_score=self.block_score)

    def close(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ready", "timeout", "cancel"])
async def test_slow_process_start_uses_load_budget_and_retires_late_host(monkeypatch, outcome):
    entered, release = Event(), Event()
    hosts = []
    original_start = activation_scoring._BackendProcessHost.create_started

    def held_start(**kwargs):
        entered.set()
        release.wait()
        host = original_start(**kwargs)
        hosts.append(host)
        return host

    monkeypatch.setattr(activation_scoring._BackendProcessHost, "create_started", held_start)
    monkeypatch.setattr(
        activation_scoring, "CampPlusBackendFactory",
        lambda *args, **kwargs: _SlowLoadFactory(),
    )
    profile = _profile()
    scorer = CampPlusActivationScorer(
        profile, scorer_generation=1, timeout_seconds=.05,
        load_timeout_seconds=.1 if outcome == "timeout" else 15,
    )
    task = asyncio.create_task(scorer.prepare())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        if outcome == "ready":
            await asyncio.sleep(.15)
            assert not task.done()
            release.set()
            assert await task is ActivationScoreStatus.READY
        elif outcome == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert scorer.closed
        else:
            assert await task is ActivationScoreStatus.TIMED_OUT
            assert scorer.closed
    finally:
        release.set()
        await scorer.close()
        profile.close()
        async with asyncio.timeout(15):
            while not hosts or hosts[0].alive:
                await asyncio.sleep(.01)
    assert not hosts[0].alive


@pytest.mark.asyncio
@pytest.mark.parametrize("block_score", [False, True])
async def test_real_process_cold_load_can_exceed_hot_score_budget(monkeypatch, block_score):
    monkeypatch.setattr(
        activation_scoring, "CampPlusBackendFactory",
        lambda *args, **kwargs: _SlowLoadFactory(block_score),
    )
    profile = _profile()
    scorer = CampPlusActivationScorer(
        profile, scorer_generation=1, timeout_seconds=.2,
    )
    host = None
    try:
        assert await scorer.prepare() is ActivationScoreStatus.READY
        host = scorer._host
        result = await scorer.score(
            ActivationScoreIdentity(profile.generation, 1, 1),
            bytes(3200), sample_rate_hz=16000,
        )
        assert result.status is (
            ActivationScoreStatus.TIMED_OUT if block_score else ActivationScoreStatus.READY
        )
        if block_score:
            assert scorer.closed
        else:
            assert result.similarity == .75
    finally:
        await scorer.close()
        profile.close()
    assert host is not None and not host.alive


@pytest.mark.asyncio
async def test_injected_load_also_uses_loading_budget():
    profile = _profile()
    scorer = CampPlusActivationScorer(
        profile, scorer_generation=1, timeout_seconds=.2,
        backend_factory=_SlowLoadBackend,
    )
    try:
        assert await scorer.prepare() is ActivationScoreStatus.READY
        assert not scorer.closed
    finally:
        await scorer.close()
        profile.close()


@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf")])
def test_loading_budget_must_be_finite_and_positive(budget):
    profile = _profile()
    try:
        with pytest.raises(ValueError, match="load_timeout_seconds"):
            CampPlusActivationScorer(
                profile, scorer_generation=1, load_timeout_seconds=budget,
            )
    finally:
        profile.close()


@pytest.mark.asyncio
async def test_real_process_load_timeout_retires_host(monkeypatch):
    monkeypatch.setattr(
        activation_scoring, "CampPlusBackendFactory",
        lambda *args, **kwargs: _SlowLoadFactory(),
    )
    profile = _profile()
    scorer = CampPlusActivationScorer(profile, scorer_generation=1)
    host = None
    try:
        # Start the real host separately so this exercises the load command's
        # deadline independently of Windows spawn time.
        status, host = await scorer._ensure_process_host()
        assert status is ActivationScoreStatus.READY
        scorer._load_timeout_seconds = .05
        assert await scorer.prepare() is ActivationScoreStatus.TIMED_OUT
        assert scorer.closed
        assert await scorer.prepare() is ActivationScoreStatus.CLOSED
    finally:
        await asyncio.wait_for(scorer.close(), 2)
        profile.close()
    assert host is not None and not host.alive
