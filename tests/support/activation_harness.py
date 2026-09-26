import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.asr_client.endpointing.detector_runtime import DetectorRuntime
from main_logic.asr_client.endpointing.detector import CoreDetectorEventEnvelope
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController, VoiceRouteMode
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_input.activation import ActivationState, VoiceActivationController
from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntime
from main_logic.voice_turn.contracts import SpeechActivityEvent, AsrSubmitResult, AsrSubmitStatus
from tests.support.asr_fakes import _Runtime, _selection, CoordinatorState, _CoreActivationScorer
from main_logic.voice_turn.contracts import VoiceTurnToken


class _Vad:
    """Synchronous VAD stub accepted by DetectorRuntime test harnesses."""

    def load(self) -> bool:
        return True

    def close(self) -> None:
        return None


class _Gate:
    def __init__(self) -> None:
        self.count = 0

    def feed(self, pcm):
        self.count += 1
        return (SpeechActivityEvent.SPEECH_STARTED,) if self.count == 3 else ()

    def reset(self) -> None:
        self.count = 0


class _Coordinator:
    state = CoordinatorState.IDLE

    def push_audio(self, pcm) -> None:
        return None

    async def on_activity_event(self, event) -> None:
        self.state = CoordinatorState.SPEECH_ACTIVE

    async def prepare_predictor(self) -> bool:
        return True

    async def reset(self) -> None:
        self.state = CoordinatorState.IDLE

    async def close(self) -> None:
        self.state = CoordinatorState.CLOSED

    async def unload_predictor(self) -> None:
        return None


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
class _Clock:
    value = 100.0

    def __call__(self) -> float:
        return self.value

class _Factory:
    activation_generation = "profile"

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.runtimes: list[VoiceSessionActivationRuntime] = []
        self.scorers: list[_CoreActivationScorer] = []

    def create(self, generation, output, *, status_callback=None):
        scorer = _CoreActivationScorer()
        scorer.profile_generation = self.activation_generation
        runtime = VoiceSessionActivationRuntime(
            generation,
            scorer,
            output,
            controller=VoiceActivationController(clock=self.clock),
            status_callback=status_callback,
        )
        self.scorers.append(scorer)
        self.runtimes.append(runtime)
        return runtime

    def close(self) -> None:
        pass
async def _until(predicate) -> None:
    async with asyncio.timeout(2.0):
        while not predicate():
            await asyncio.sleep(0)

class _Session:
    def __init__(self, name: str, deliveries: list[tuple[str, bytes]]) -> None:
        self.name = name
        self.can_handoff_voice_input = MagicMock(return_value=True)
        self.stream_audio = AsyncMock(side_effect=self._stream)
        self._deliveries = deliveries

    async def _stream(self, pcm: bytes) -> None:
        self._deliveries.append((self.name, pcm))


@dataclass
class _Harness:
    manager: _Runtime
    clock: _Clock
    factory: _Factory
    route: str
    deliveries: list[tuple[str, bytes]] = field(default_factory=list)

    @property
    def activation(self) -> VoiceSessionActivationRuntime:
        return self.factory.runtimes[0]

    @property
    def pcm(self) -> list[bytes]:
        return [pcm for _, pcm in self.deliveries]

    def session(self, name: str) -> _Session:
        return _Session(name, self.deliveries)

    async def submit(self, frame, **_kwargs) -> AsrSubmitResult:
        self.deliveries.append(("independent", frame.pcm16))
        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

    async def feed(self, marker: int, *, voice: bool = True) -> bytes:
        pcm = marker.to_bytes(2, "little", signed=True) * 1_600
        await self.manager._route_microphone_audio(
            pcm,
            sample_rate_hz=16_000,
            speech_probability=0.9 if voice else 0.0,
            received_at=self.clock.value,
            captured_at=self.clock.value,
        )
        await asyncio.sleep(0)
        return pcm

    async def promote(self, ticket, target: _Session) -> None:
        assert self.manager._voice_activation_handoff_is_current(ticket)
        assert self.manager._mark_voice_activation_handoff_irreversible(ticket)
        self.manager.session = target
        await self.manager._reconcile_independent_asr_after_core_change()
        assert self.manager._voice_activation_handoff_is_current(
            ticket, allow_promoted=True
        )
        assert await self.manager._commit_voice_activation_handoff(ticket)

@asynccontextmanager
async def _harness(route: str, *, active: bool = True):
    manager = _Runtime()
    clock = _Clock()
    factory = _Factory(clock)
    harness = _Harness(manager, clock, factory, route)
    manager.is_active = True
    manager.core_api_type = "qwen"
    manager._independent_asr_route_key = "qwen"
    manager.session = harness.session("source")
    manager._set_microphone_route(route)
    manager._asr_runtime.submit = AsyncMock(side_effect=harness.submit)
    await manager.set_voice_session_activation_factory(
        factory, activation_generation="profile"
    )
    try:
        for index in range(15 if active else 1):
            clock.value = 100.0 + index / 10
            await harness.feed(2_000 + index)
        expected_state = ActivationState.ACTIVE if active else ActivationState.WAITING
        await _until(lambda: harness.activation.state is expected_state)
        if active:
            await _until(lambda: len(harness.deliveries) == 15)
        yield harness
    finally:
        await manager.set_voice_session_activation_factory(
            None, activation_generation="test-finished"
        )
        pending = tuple(manager._core_asr_cleanup_tasks)
        if pending:
            async with asyncio.timeout(2.0):
                await asyncio.gather(*pending, return_exceptions=True)
