"""Wake evidence uses real activation/Core routing; only inference and I/O are fake."""

from __future__ import annotations

import asyncio

import pytest

from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntime
from main_logic.voice_input.activation import (
    ActivationState,
    VoiceActivationController,
    WakeWordDetection,
)
from tests.unit import test_voice_activation_handoff as handoff
from tests.unit import test_voice_activation_cold_prefix as cold
from tests.unit.test_core_independent_asr import _CoreActivationScorer


pytestmark = pytest.mark.asyncio


class _Detector:
    def __init__(self):
        self.trigger = None
        self.start_sample = 0
        self.closed = False

    async def prepare(self):
        pass

    async def feed(self, frame, epoch):
        if frame.sequence != self.trigger:
            return None
        self.trigger = None
        return WakeWordDetection(
            keyword="test-wake",
            generation=frame.generation,
            epoch=epoch,
            sample_start=self.start_sample,
            sample_end=frame.sample_end,
        )

    async def close(self):
        self.closed = True


class _WakeFactory(handoff._Factory):
    def __init__(self, clock):
        super().__init__(clock)
        self.detectors = []

    def create(self, generation, output, *, status_callback=None):
        scorer = _CoreActivationScorer(similarity=0.0)
        detector = _Detector()
        runtime = VoiceSessionActivationRuntime(
            generation, scorer, output,
            controller=VoiceActivationController(clock=self.clock),
            status_callback=status_callback,
            wake_detector=detector,
        )
        self.scorers.append(scorer)
        self.runtimes.append(runtime)
        self.detectors.append(detector)
        return runtime


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_short_wake_replays_once_and_creates_new_batch_each_idle_round(monkeypatch, route):
    monkeypatch.setattr(handoff, "_Factory", _WakeFactory)
    async with handoff._harness(route, active=False) as h:
        detector = h.factory.detectors[0]
        # Initial harness frame was buffered while the runtime prepared.
        expected = [(2000).to_bytes(2, "little", signed=True) * 1600]
        prefixes = []
        for round_index in range(2):
            if round_index:
                h.clock.value += 31.0
                await h.activation.tick(now=h.clock.value)
                assert h.activation.state is ActivationState.WAITING
            first_sequence = h.manager._voice_session_activation_sequence
            detector.start_sample = h.manager._voice_session_activation_sample_cursor
            detector.trigger = first_sequence + 4
            for offset in range(5):
                h.clock.value += 0.1
                # Coarse VAD deliberately rejects every wake frame.
                expected.append(await h.feed(3000 + round_index * 10 + offset, voice=False))
            await handoff._until(lambda: h.activation.state is ActivationState.ACTIVE)
            await handoff._until(lambda: h.pcm == expected)
            assert h.factory.scorers[0].calls == 0
            assert h.manager._voice_activation_delivery_batch == round_index + 1
            if route == "independent":
                prefixes.append(h.manager._voice_activation_delivery_prefix[2])
            h.clock.value += 0.1
            expected.append(await h.feed(4000 + round_index))
            await handoff._until(lambda: h.pcm == expected)
        if prefixes:
            assert prefixes[0].batch_id != prefixes[1].batch_id
    assert detector.closed


@pytest.mark.parametrize("endpointing", ["provider", "manual"])
async def test_short_wake_preserves_following_audio_during_cold_connection(monkeypatch, endpointing):
    monkeypatch.setattr(cold, "_Factory", _WakeFactory)
    async with cold._cold_harness(endpointing) as h:
        expected = [await cold._feed(h, 3000)]
        await cold._until(lambda: h.factory.runtimes[0].state is ActivationState.WAITING)
        detector = h.factory.detectors[0]
        detector.start_sample = h.manager._voice_session_activation_sample_cursor
        detector.trigger = h.manager._voice_session_activation_sequence + 4
        for marker in range(3001, 3006):
            expected.append(await cold._feed(h, marker))
        await cold._until(h.started.is_set)
        # Continue the same phrase while physical provider connection is delayed.
        for marker in range(3006, 3010):
            expected.append(await cold._feed(h, marker))
        expected.append(await cold._feed(h, 0))
        assert h.deliveries == []
        assert h.factory.scorers[0].calls == 0
        h.release.set()
        await cold._until(lambda: sum(map(len, h.deliveries)) == sum(map(len, expected)))
        assert b"".join(h.deliveries) == b"".join(expected)
        await asyncio.sleep(0)
        assert len(h.sessions) == 1


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_nonkeyword_short_audio_still_stays_local(monkeypatch, route):
    monkeypatch.setattr(handoff, "_Factory", _WakeFactory)
    async with handoff._harness(route, active=False) as h:
        for marker in range(3001, 3006):
            h.clock.value += 0.1
            await h.feed(marker)
        for _ in range(6):
            h.clock.value += 0.1
            await h.feed(0, voice=False)
        assert h.activation.state is ActivationState.WAITING
        assert h.pcm == []
        assert h.factory.scorers[0].calls == 0
