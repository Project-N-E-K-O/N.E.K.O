"""Full activation -> Core -> ASR capacity handoff, with no second-phrase wake."""

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.endpointing.detector_runtime import DetectorRuntime
from main_logic.asr_client.endpointing.detector import CoreDetectorEventEnvelope
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController, VoiceRouteMode
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_input.activation import ActivationState
from main_logic.voice_turn.contracts import SpeechActivityEvent
from tests.unit.test_core_independent_asr import _Runtime, _selection, CoordinatorState
from tests.unit.test_voice_activation_handoff import _Clock, _Factory


async def _until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


class _Vad:
    def load(self):
        return True

    def close(self):
        pass


class _Gate:
    def __init__(self):
        self.count = 0

    def feed(self, pcm):
        self.count += 1
        return (SpeechActivityEvent.SPEECH_STARTED,) if self.count == 3 else ()

    def reset(self):
        self.count = 0


class _Coordinator:
    state = CoordinatorState.IDLE

    def push_audio(self, pcm):
        pass

    async def on_activity_event(self, event):
        self.state = CoordinatorState.SPEECH_ACTIVE

    async def prepare_predictor(self):
        return True

    async def reset(self):
        self.state = CoordinatorState.IDLE

    async def close(self):
        self.state = CoordinatorState.CLOSED

    async def unload_predictor(self):
        pass


@asynccontextmanager
async def _cold_harness(endpointing="provider", gate=None):
    manager, clock = _Runtime(), _Clock()
    release, started = asyncio.Event(), asyncio.Event()
    deliveries, sessions = [], []

    def create_session(selection):
        session = SimpleNamespace(is_ready=False, transport_write_attempted=False)
        sessions.append(session)

        async def connect():
            started.set()
            await release.wait()
            session.is_ready = True

        async def close():
            session.is_ready = False

        async def stream(pcm, **kwargs):
            session.transport_write_attempted = True
            deliveries.append(pcm)

        session.connect = connect
        session.close = AsyncMock(side_effect=close)
        session.stream_audio = stream
        session.signal_user_activity_end = AsyncMock()
        return session

    manager._asr_route_mode = "independent"
    provider = "qwen" if endpointing == "provider" else "glm"
    manager._asr_provider = provider
    manager._asr_transport_selection = _selection(provider, endpointing)
    manager._asr_session_factory = create_session
    policy = resolve_provider_policy(provider, endpointing)
    lifecycle = VoiceInputLifecycleController(provider_policy=policy, shadow_mode=False)
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    manager._asr_lifecycle = lifecycle

    async def on_event(event):
        assert manager._asr_detector_dispatcher.submit_nowait(
            CoreDetectorEventEnvelope(
                event=event, detector_ref=detector, lifecycle_ref=lifecycle,
                session_epoch=manager._asr_session_epoch,
            )
        )

    detector = DetectorRuntime(vad=_Vad(), gate=gate or _Gate(), provider_policy=policy,
                               on_event=on_event, coordinator=_Coordinator())
    manager._asr_detector = detector
    factory = _Factory(clock)
    await manager.set_voice_session_activation_factory(factory, activation_generation="profile")
    h = SimpleNamespace(manager=manager, clock=clock, factory=factory, lifecycle=lifecycle,
                        release=release, started=started, deliveries=deliveries, sessions=sessions)
    try:
        yield h
    finally:
        # Release the test's physical-thread barrier before joining detector
        # cleanup; it is not a production resource that can stay blocked.
        if gate is not None and hasattr(gate, "release"):
            gate.release.set()
        await manager.set_voice_session_activation_factory(None, activation_generation="disabled")
        await manager._asr_runtime.abort("test_end")
        await detector.close()
        await manager._asr_audio_dispatcher.close()
        await manager._asr_detector_dispatcher.close()


async def _feed(h, marker, samples=1600):
    pcm = marker.to_bytes(2, "little") * samples
    await h.manager._route_microphone_audio(
        pcm, sample_rate_hz=16000, speech_probability=.9 if marker else 0,
        rnnoise_available=True, received_at=h.clock.value, captured_at=h.clock.value,
    )
    h.clock.value += samples / 16000
    await asyncio.sleep(0)
    return pcm


@pytest.mark.asyncio
@pytest.mark.parametrize("connection_delay", [5, 9])
@pytest.mark.parametrize("endpointing", ["provider", "manual"])
async def test_cold_first_phrase_and_silence_are_lossless(connection_delay, endpointing):
    async with _cold_harness(endpointing) as h:
        frames = []
        for marker in range(1, 76):
            frames.append(await _feed(h, marker, samples=320))
        await _until(lambda: h.started.is_set())
        await _until(lambda: h.lifecycle.pending_connect_bytes == 48000)
        # Virtual capture time: connection starts after the 1.5 s activation
        # checkpoint. Release it after 5/9 s of further captured audio. The
        # actual asynchronous ordering is controlled by events, not sleep(9).
        release_at = 75 + connection_delay * 50
        # Exactly 126 * 20 ms speech (2.52 s), then 600 * 20 ms silence (12 s).
        # No marker after the first utterance can accidentally wake the sender.
        for index in range(75, 726):
            marker = index + 1 if index < 126 else 0
            frames.append(await _feed(h, marker, samples=320))
            byte_count = len(frames) * 640
            if index + 1 <= release_at and byte_count <= 256000:
                await _until(lambda: h.lifecycle.pending_connect_bytes == byte_count)
            if connection_delay == 9 and byte_count == 256640:
                # Prove the writer actually reached admission backpressure;
                # merely observing queued upstream frames could also mean it
                # has not been scheduled yet and would miss an eviction bug.
                await _until(lambda: bool(h.lifecycle.prefix_capacity_event._waiters))
            if index + 1 == release_at:
                if connection_delay == 9:
                    assert h.lifecycle.pending_connect_bytes == 256000
                    assert h.factory.runtimes[0].pending_output_bytes > 0
                    assert h.factory.runtimes[0].state is not ActivationState.UNAVAILABLE
                assert not h.deliveries
                h.release.set()
            if index + 1 >= release_at:
                await _until(lambda: sum(map(len, h.deliveries)) == byte_count)
        expected = b"".join(frames)
        await _until(lambda: sum(map(len, h.deliveries)) == len(expected))
        assert b"".join(h.deliveries) == expected
        assert len(h.sessions) == 1
        assert h.lifecycle.metrics.buffer_overflow_count == 0
        assert h.factory.runtimes[0].pending_output_bytes == 0


@pytest.mark.asyncio
async def test_second_phrase_queued_before_ready_keeps_first_phrase_order():
    async with _cold_harness() as h:
        frames = []
        for marker in range(1, 16):
            frames.append(await _feed(h, marker))
        await _until(lambda: h.lifecycle.pending_connect_bytes == 48000)
        for marker in [0] * 5 + list(range(200, 210)):
            frames.append(await _feed(h, marker))
            await _until(lambda: h.lifecycle.pending_connect_bytes == sum(map(len, frames)))
        h.release.set()
        expected = b"".join(frames)
        await _until(lambda: sum(map(len, h.deliveries)) == len(expected))
        assert b"".join(h.deliveries) == expected
        assert len(h.sessions) == 1


@pytest.mark.asyncio
async def test_upstream_overflow_revokes_downstream_before_late_connect():
    async with _cold_harness() as h:
        for marker in range(1, 16):
            await _feed(h, marker)
        await _until(lambda: h.lifecycle.pending_connect_bytes == 48000)
        for index in range(65):
            await _feed(h, 0)
            await _until(lambda: h.lifecycle.pending_connect_bytes == (16 + index) * 3200)
        assert h.lifecycle.pending_connect_bytes == 256000
        for _ in range(81):
            await _feed(h, 0)
        assert h.factory.runtimes[0].state is ActivationState.UNAVAILABLE
        # The Core status callback must fence the ASR prefix synchronously;
        # awaiting cleanup before this assertion would mask a send race.
        assert h.lifecycle.pending_connect_bytes == 0
        h.release.set()
        await asyncio.sleep(.03)
        assert not h.deliveries
        assert h.manager._asr_route_mode == "blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["microphone_stopped", "permission_revoked"])
async def test_revoking_while_capacity_waiting_never_sends_late_prefix(reason):
    async with _cold_harness() as h:
        for marker in range(1, 16):
            await _feed(h, marker)
        await _until(lambda: h.lifecycle.pending_connect_bytes == 48000)
        for index in range(65):
            await _feed(h, 0)
            await _until(lambda: h.lifecycle.pending_connect_bytes == (16 + index) * 3200)
        await _feed(h, 0)
        assert h.factory.runtimes[0].pending_output_bytes > 0
        if reason == "permission_revoked":
            h.manager.require_voice_session_activation(activation_generation="revoked")
        else:
            await h.manager.set_voice_input_suppressed(reason, suppressed=True)
        assert h.lifecycle.pending_connect_bytes == 0
        h.release.set()
        await asyncio.sleep(.03)
        assert not h.deliveries


class _HeldGate(_Gate):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def feed(self, pcm):
        self.entered.set()
        # The harness releases this barrier before joining the detector, even
        # on failure. A wall-clock fallback would silently lift backpressure.
        self.release.wait()
        return super().feed(pcm)


@pytest.mark.asyncio
@pytest.mark.parametrize("stalled", [False, True])
async def test_manual_detector_capacity_wait_resumes_or_fails_bounded(monkeypatch, stalled):
    gate = _HeldGate()
    capacity_waiting, resume_wait = asyncio.Event(), asyncio.Event()
    try:
        async with _cold_harness("manual", gate=gate) as h:
            detector = h.manager._asr_detector
            wait_capacity = detector.wait_audio_capacity

            async def controlled_wait(pcm16, *, sample_rate_hz, deadline):
                if detector.queued_audio_ms == 1000:
                    # The first dequeue can precede entry into the physical
                    # gate; let that slot settle before observing saturation.
                    await _until(gate.entered.is_set)
                if detector.queued_audio_ms == 1000:
                    capacity_waiting.set()
                    await resume_wait.wait()
                    if stalled:
                        # Expire the real queue wait only after its full state
                        # has been observed; CI scheduling must not erase the
                        # detector before the precondition is asserted.
                        deadline = asyncio.get_running_loop().time()
                return await wait_capacity(
                    pcm16, sample_rate_hz=sample_rate_hz, deadline=deadline,
                )

            monkeypatch.setattr(detector, "wait_audio_capacity", controlled_wait)
            frames = [await _feed(h, marker, samples=320) for marker in range(1, 76)]
            await _until(lambda: gate.entered.is_set())
            activation = h.factory.runtimes[0]
            await _until(capacity_waiting.is_set)
            assert detector.queued_audio_ms == 1000
            assert activation.pending_output_bytes > 0
            resume_wait.set()
            if stalled:
                await _until(lambda: activation.state in {
                    ActivationState.UNAVAILABLE, ActivationState.CLOSED,
                })
                assert h.lifecycle.pending_connect_bytes == 0
                assert h.manager._asr_route_mode == "blocked"
                assert not h.deliveries
            else:
                gate.release.set()
                await _until(lambda: h.started.is_set())
                h.release.set()
                expected = b"".join(frames)
                await _until(lambda: sum(map(len, h.deliveries)) == len(expected))
                assert b"".join(h.deliveries) == expected
                assert activation.state is not ActivationState.UNAVAILABLE
                assert h.lifecycle.metrics.detector_overflow_count == 0
            gate.release.set()
    finally:
        resume_wait.set()
        gate.release.set()
