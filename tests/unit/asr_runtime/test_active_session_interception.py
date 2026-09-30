from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

import pytest

from main_logic.voice_input.interception import (
    ActiveSessionInterceptionBridge,
    InterceptionDecision,
    InterceptionResult,
)
from main_logic.core.asr_runtime import AsrRuntimeMixin
from main_logic.voice_input.activation import (
    ActivationGeneration,
    AudioFrame,
    OutputCommit,
)
from main_logic.core.asr_runtime import VoiceSessionActivationRouteContext
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus


class _FakeRuntime:
    def __init__(self, result: InterceptionResult) -> None:
        self.result = result
        self.calls: list[bytes] = []
        self.closed: list[str] = []

    async def process(self, pcm16: bytes, **kwargs):
        self.calls.append(pcm16)
        return self.result

    async def close(self, reason: str = "retired") -> None:
        self.closed.append(reason)


class _FakeFactory:
    def __init__(self, result: InterceptionResult) -> None:
        self.result = result
        self.runtimes: list[_FakeRuntime] = []

    def create(self, generation, *, ingress_token):
        runtime = _FakeRuntime(self.result)
        self.runtimes.append(runtime)
        return runtime

    def close(self) -> None:
        return None


class _HangingRuntime(_FakeRuntime):
    def __init__(self) -> None:
        super().__init__(InterceptionResult(InterceptionDecision.KEEP, pcm16=b"owner"))
        self.release = asyncio.Event()

    async def process(self, pcm16: bytes, **kwargs):
        await self.release.wait()
        return self.result

    async def close(self, reason: str = "retired") -> None:
        self.closed.append(reason)
        self.release.set()


class _HangingFactory(_FakeFactory):
    def __init__(self) -> None:
        super().__init__(InterceptionResult(InterceptionDecision.KEEP, pcm16=b"owner"))
        self.hanging = _HangingRuntime()

    def create(self, generation, *, ingress_token):
        if not self.runtimes:
            self.runtimes.append(self.hanging)
            return self.hanging
        return super().create(generation, ingress_token=ingress_token)


class _Runtime(AsrRuntimeMixin):
    def __init__(self) -> None:
        self._init_asr_runtime_state()
        self._voice_lease_synchronized = True
        self._voice_lease_owner = "core"
        self._voice_input_suppressed = False
        self.lanlan_name = "test"
        self.session = SimpleNamespace()


def _frame(runtime: _Runtime, generation: ActivationGeneration, pcm: bytes) -> AudioFrame:
    token = runtime._capture_ingress_token()
    return AudioFrame(
        sequence=0,
        sample_start=0,
        sample_end=len(pcm) // 2,
        captured_at=1.0,
        sample_rate=16_000,
        pcm=pcm,
        generation=generation,
        context=VoiceSessionActivationRouteContext(
            speech_probability=1.0,
            rnnoise_available=True,
            rnnoise_evidence=None,
            ingress_token=token,
            captured_at=1.0,
        ),
    )


pytestmark = pytest.mark.asyncio


async def test_bridge_releases_only_explicit_filtered_pcm_and_retires_generation():
    owner_pcm = b"owner" * 40
    factory = _FakeFactory(
        InterceptionResult(InterceptionDecision.KEEP, pcm16=owner_pcm)
    )
    bridge = ActiveSessionInterceptionBridge(factory)
    first = await bridge.process(
        b"mixed" * 40,
        sample_rate_hz=16_000,
        generation="g1",
        ingress_token="i1",
        captured_at=1.0,
    )
    assert first == InterceptionResult(InterceptionDecision.KEEP, pcm16=owner_pcm)
    second = await bridge.process(
        b"mixed-2" * 40,
        sample_rate_hz=16_000,
        generation="g2",
        ingress_token="i2",
        captured_at=2.0,
    )
    assert second.decision is InterceptionDecision.KEEP
    assert factory.runtimes[0].closed == ["generation_replaced"]


async def test_bridge_fail_closes_for_tse_or_classifier_unavailable():
    factory = _FakeFactory(
        InterceptionResult(
            InterceptionDecision.UNAVAILABLE,
            reason="tse_unavailable",
        )
    )
    bridge = ActiveSessionInterceptionBridge(factory)
    result = await bridge.process(
        b"mixed" * 40,
        sample_rate_hz=16_000,
        generation="g1",
        ingress_token="i1",
        captured_at=1.0,
    )
    assert result.decision is InterceptionDecision.UNAVAILABLE
    assert result.pcm16 == b""


@pytest.mark.parametrize("route_mode", ["native", "independent"])
async def test_common_activation_output_sends_filtered_bytes_to_receiver_only(route_mode):
    runtime = _Runtime()
    runtime._asr_route_mode = route_mode
    runtime._voice_session_activation_degraded = False
    runtime._voice_activation_handoff = None
    generation = ActivationGeneration("session", 1, 1, 1, 1, "core")
    runtime._capture_voice_session_activation_generation = lambda: generation
    runtime._voice_input_accepts_pcm = lambda: True
    if route_mode == "native":
        runtime.session.stream_audio = AsyncMock()
    else:
        runtime._asr_runtime.submit = AsyncMock(
            return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
        )
        runtime._independent_asr_provider = "fake"
        runtime._ingress_token_matches = lambda token: True
    filtered = b"owner" * 40
    assert await runtime.set_active_session_interception_factory(
        _FakeFactory(InterceptionResult(InterceptionDecision.KEEP, pcm16=filtered))
    )
    frame = _frame(runtime, generation, b"mixed" * 40)
    result = await runtime._route_voice_session_activation_output(frame, generation)
    assert result is OutputCommit.LOCAL_ACCEPTED
    if route_mode == "native":
        sent = runtime.session.stream_audio.await_args.args[0]
    else:
        sent = runtime._asr_runtime.submit.await_args.args[0].pcm16
    assert sent == filtered
    assert sent != frame.pcm


async def test_common_activation_output_drops_pending_without_raw_fallback():
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime._voice_session_activation_degraded = False
    runtime._voice_activation_handoff = None
    generation = ActivationGeneration("session", 1, 1, 1, 1, "core")
    runtime._capture_voice_session_activation_generation = lambda: generation
    runtime._route_microphone_audio_unfiltered = AsyncMock()
    assert await runtime.set_active_session_interception_factory(
        _FakeFactory(InterceptionResult(InterceptionDecision.PENDING))
    )
    frame = _frame(runtime, generation, b"short" * 40)
    result = await runtime._route_voice_session_activation_output(frame, generation)
    assert result is OutputCommit.LOCAL_ACCEPTED
    runtime._route_microphone_audio_unfiltered.assert_not_awaited()


async def test_required_policy_without_prepared_factory_is_fail_closed():
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime._voice_session_activation_degraded = False
    runtime._voice_activation_handoff = None
    generation = ActivationGeneration("session", 1, 1, 1, 1, "core")
    runtime._capture_voice_session_activation_generation = lambda: generation
    runtime._route_microphone_audio_unfiltered = AsyncMock()
    assert await runtime.set_active_session_interception_factory(
        None,
        interception_required=True,
    )
    frame = _frame(runtime, generation, b"mixed" * 40)
    result = await runtime._route_voice_session_activation_output(frame, generation)
    assert result is OutputCommit.NOT_SENT
    runtime._route_microphone_audio_unfiltered.assert_not_awaited()


async def test_requested_interception_blocks_ordinary_raw_outlet():
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    assert await runtime.set_active_session_interception_factory(
        _FakeFactory(InterceptionResult(InterceptionDecision.PENDING))
    )
    result = await runtime._route_microphone_audio_unfiltered(
        b"mixed" * 40,
        sample_rate_hz=16_000,
    )
    assert result is OutputCommit.NOT_SENT


async def test_require_interception_revokes_bridge_synchronously():
    runtime = _Runtime()
    factory = _FakeFactory(
        InterceptionResult(InterceptionDecision.KEEP, pcm16=b"owner" * 40)
    )
    assert await runtime.set_active_session_interception_factory(factory)
    revision = runtime.require_active_session_interception()
    assert revision >= 1
    assert runtime._active_session_interception_bridge is None
    assert runtime._active_session_interception_required is True


async def test_bridge_process_timeout_fail_closes_until_runtime_retires():
    factory = _HangingFactory()
    bridge = ActiveSessionInterceptionBridge(
        factory,
        process_timeout_s=0.01,
        close_timeout_s=0.05,
    )

    result = await bridge.process(
        b"mixed",
        sample_rate_hz=16_000,
        generation="g1",
        ingress_token="i1",
        captured_at=1.0,
    )

    assert result.decision is InterceptionDecision.UNAVAILABLE
    assert result.reason == "interception_process_timeout"
    # The timeout retired the owner before any replacement can be created.
    assert factory.hanging.closed == ["interception_process_timeout"]
