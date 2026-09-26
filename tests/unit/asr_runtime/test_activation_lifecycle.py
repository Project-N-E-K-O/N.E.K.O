import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.asr_client import VoiceIdentityActivationResult
from main_logic.voice_input.activation import ActivationState
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus

from tests.support.core_asr_harness import (
    _ReadyDetector,
    _install_ready_lifecycle,
)

from tests.unit.asr_runtime._scenarios import (
    _wait_for_activation_output,
)

from tests.support.asr_fakes import (
    _CoreActivationFactory,
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_activation_prepare_failure_publishes_unavailable_and_retires_runtime() -> None:
    manager = _Runtime()

    class FailingRuntime:
        async def prepare(self):
            raise RuntimeError("scorer setup failed")

        mark_unavailable = AsyncMock()
        close = AsyncMock()

    failing = FailingRuntime()
    generation = "prepare-failure"
    manager._voice_session_activation_runtime = failing
    manager._capture_voice_session_activation_generation = lambda: generation

    await manager._prepare_voice_session_activation_runtime(failing, generation)

    failing.mark_unavailable.assert_awaited_once_with("prepare_failed")
    failing.close.assert_awaited_once()
    assert manager._voice_session_activation_runtime is None


async def test_required_activation_revoke_retires_runtime_before_async_replace() -> None:
    runtime = _Runtime()
    factory = _CoreActivationFactory()
    old_runtime = SimpleNamespace(close=AsyncMock())
    runtime._voice_session_activation_factory = factory
    runtime._voice_session_activation_runtime = old_runtime
    before_permission = runtime._voice_session_activation_permission_revision

    token = runtime.require_voice_session_activation(
        activation_generation="required-empty",
    )

    assert token == runtime._voice_session_activation_policy_revision
    assert runtime._voice_session_activation_required is True
    assert runtime._voice_session_activation_degraded is True
    assert runtime._voice_session_activation_factory is None
    assert runtime._voice_session_activation_runtime is None
    assert factory.closed is True
    assert runtime._voice_session_activation_permission_revision > before_permission
    await asyncio.sleep(0)
    old_runtime.close.assert_awaited_once()


async def test_required_activation_token_rejects_waiting_older_factory() -> None:
    runtime = _Runtime()
    old_factory = _CoreActivationFactory()
    old_token = runtime.voice_session_activation_policy_token()
    await runtime._core_voice_session_swap_lock.acquire()
    replacement = asyncio.create_task(
        runtime.set_voice_session_activation_factory(
            old_factory,
            activation_generation=old_factory.activation_generation,
            activation_required=True,
            expected_policy_revision=old_token,
        )
    )
    await asyncio.sleep(0)

    runtime.require_voice_session_activation(
        activation_generation="new-required-intent",
    )
    runtime._core_voice_session_swap_lock.release()

    assert (
        await replacement
        is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    )
    assert runtime._voice_session_activation_factory is None
    assert runtime._voice_session_activation_required is True


async def test_voice_session_activation_gates_native_then_replays_and_forwards() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.session.stream_audio = AsyncMock()
    factory = _CoreActivationFactory()
    assert (
        await runtime.set_voice_session_activation_factory(
            factory,
            activation_generation="profile",
        )
        is VoiceIdentityActivationResult.READY
    )

    pcm16 = b"\xd0\x07" * 1_600
    await runtime._route_microphone_audio(pcm16, sample_rate_hz=16_000)
    await asyncio.sleep(0)
    for _ in range(14):
        await runtime._route_microphone_audio(pcm16, sample_rate_hz=16_000)
    assert runtime.session.stream_audio.await_count == 0

    await _wait_for_activation_output(
        lambda: runtime.session.stream_audio.await_count,
        expected_count=15,
    )
    assert runtime.session.stream_audio.await_count == 15
    assert factory.scorers[0].calls == 1

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await asyncio.sleep(0)
    assert runtime.session.stream_audio.await_count == 16
    assert factory.scorers[0].calls == 1
    await runtime.set_voice_session_activation_factory(
        None,
        activation_generation="disabled",
    )


async def test_voice_session_activation_keeps_original_monotonic_capture_time() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    captured_frames = []

    class _CapturingRuntime:
        state = ActivationState.WAITING

        def __init__(self, generation) -> None:
            self.generation = generation

        async def prepare(self):
            return None

        async def feed(self, frame, *, voice_activity: bool):
            assert voice_activity is True
            captured_frames.append(frame)
            return None

        async def close(self) -> None:
            return None

    class _CapturingFactory:
        activation_generation = "profile"

        def create(self, generation, output, *, status_callback=None):
            return _CapturingRuntime(generation)

        def close(self) -> None:
            return None

    await runtime.set_voice_session_activation_factory(
        _CapturingFactory(),
        activation_generation="profile",
    )

    await runtime._route_microphone_audio(
        b"\xd0\x07" * 1_600,
        sample_rate_hz=16_000,
        received_at=29.9,
        captured_at=1_725_000_000.0,
    )

    assert len(captured_frames) == 1
    assert captured_frames[0].captured_at == 29.9
    assert captured_frames[0].context.captured_at == 1_725_000_000.0


@pytest.mark.parametrize("route_mode", ["native", "independent"])
async def test_required_activation_without_factory_blocks_both_audio_routes(
    route_mode: str,
) -> None:
    runtime = _Runtime()
    runtime.session.stream_audio = AsyncMock()
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
    )
    runtime._set_microphone_route(route_mode)

    await runtime.set_voice_session_activation_factory(
        None,
        activation_generation="required-unavailable",
        activation_required=True,
    )
    frame = b"\x01\x00" * 160
    assert await runtime._route_microphone_audio(
        frame,
        sample_rate_hz=16_000,
    )

    runtime.session.stream_audio.assert_not_awaited()
    runtime._asr_runtime.submit.assert_not_awaited()

    await runtime.set_voice_session_activation_factory(
        None,
        activation_generation="explicitly-disabled",
        activation_required=False,
    )
    assert await runtime._route_microphone_audio(
        frame,
        sample_rate_hz=16_000,
    )
    if route_mode == "native":
        runtime.session.stream_audio.assert_awaited_once_with(frame)
        runtime._asr_runtime.submit.assert_not_awaited()
    else:
        runtime.session.stream_audio.assert_not_awaited()
        runtime._asr_runtime.submit.assert_awaited_once()


async def test_voice_session_activation_route_change_retires_old_authority() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.session.stream_audio = AsyncMock()
    factory = _CoreActivationFactory()
    await runtime.set_voice_session_activation_factory(
        factory,
        activation_generation="profile",
    )

    pcm16 = b"\xd0\x07" * 1_600
    await runtime._route_microphone_audio(pcm16, sample_rate_hz=16_000)
    await asyncio.sleep(0)
    runtime._set_microphone_route("independent")
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
    )
    await runtime._route_microphone_audio(pcm16, sample_rate_hz=16_000)
    await asyncio.sleep(0)

    assert len(factory.runtimes) == 2
    assert factory.runtimes[0].generation != factory.runtimes[1].generation
    assert factory.scorers[0].closed is True
    runtime.session.stream_audio.assert_not_awaited()
    runtime._asr_runtime.submit.assert_not_awaited()
    await runtime.set_voice_session_activation_factory(
        None,
        activation_generation="disabled",
    )


async def test_session_activation_detaches_preexisting_utterance_verifier() -> None:
    runtime = _Runtime()
    legacy_factory = MagicMock()
    runtime._speaker_shadow_factory = legacy_factory
    runtime._asr_runtime.set_speaker_verifier_factory = AsyncMock(return_value=True)
    factory = _CoreActivationFactory()

    assert (
        await runtime.set_voice_session_activation_factory(
            factory,
            activation_generation="profile",
        )
        is VoiceIdentityActivationResult.READY
    )
    runtime._asr_runtime.set_speaker_verifier_factory.assert_awaited_once_with(
        None,
        activation_generation="profile",
    )
    assert runtime._speaker_shadow_factory is None
    assert runtime._voice_session_activation_factory is factory


async def test_session_activation_swap_timeout_preserves_legacy_verifier() -> None:
    runtime = _Runtime()
    legacy_factory = MagicMock()
    runtime._speaker_shadow_factory = legacy_factory
    runtime._asr_runtime.set_speaker_verifier_factory = AsyncMock(return_value=True)
    runtime._core_voice_session_swap_barrier_timeout_s = 0.01
    await runtime._core_voice_session_swap_lock.acquire()
    try:
        result = await runtime.set_voice_session_activation_factory(
            _CoreActivationFactory(),
            activation_generation="profile",
        )
    finally:
        runtime._core_voice_session_swap_lock.release()

    assert result is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    assert runtime._speaker_shadow_factory is legacy_factory
    assert runtime._voice_session_activation_factory is None
    runtime._asr_runtime.set_speaker_verifier_factory.assert_not_awaited()


async def test_session_activation_rejects_mismatched_factory_generation() -> None:
    runtime = _Runtime()
    factory = _CoreActivationFactory()

    with pytest.raises(ValueError, match="generation does not match"):
        await runtime.set_voice_session_activation_factory(
            factory,
            activation_generation="stale-profile",
        )
    assert runtime._voice_session_activation_factory is None


@pytest.mark.parametrize("accepted", [True, False])
@pytest.mark.parametrize("observer_raises", [False, True])
async def test_audio_activation_mirrors_only_dispatcher_accepted_provider_payload(
    accepted: bool,
    observer_raises: bool,
) -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    detector = component._asr_detector
    assert lifecycle is not None
    assert isinstance(detector, _ReadyDetector)
    if observer_raises:
        detector.observe_provider_audio.side_effect = RuntimeError("observer failed")
    token = component._capture_turn_token(lifecycle)
    payload = b"\x01\x00" * 320
    activate = MagicMock(return_value=accepted)
    component._asr_audio_dispatcher = SimpleNamespace(
        active_turn=None,
        activate=activate,
    )

    result = component._activate_asr_audio_dispatcher(
        lifecycle,
        token,
        buffered_pcm16=payload,
    )

    assert result is accepted
    activate.assert_called_once()
    assert activate.call_args.args[2] is payload
    if accepted:
        detector.observe_provider_audio.assert_called_once()
        assert detector.observe_provider_audio.call_args.args[0] is payload
        assert detector.observe_provider_audio.call_args.kwargs == {
            "sample_rate_hz": 16_000,
        }
    else:
        detector.observe_provider_audio.assert_not_called()


async def test_voice_session_activation_gates_independent_asr_before_submit() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
    )
    factory = _CoreActivationFactory(similarity=0.1)
    await runtime.set_voice_session_activation_factory(
        factory,
        activation_generation="profile",
    )

    pcm16 = b"\xd0\x07" * 1_600
    await runtime._route_microphone_audio(pcm16, sample_rate_hz=16_000)
    await asyncio.sleep(0)
    for _ in range(14):
        await runtime._route_microphone_audio(pcm16, sample_rate_hz=16_000)
    for _ in range(20):
        await asyncio.sleep(0)
    runtime._asr_runtime.submit.assert_not_awaited()
    assert factory.scorers[0].calls == 1
    await runtime.set_voice_session_activation_factory(
        None,
        activation_generation="disabled",
    )


async def test_voice_pcm_invalidation_retires_activation_without_another_frame() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.session.stream_audio = AsyncMock()
    factory = _CoreActivationFactory()
    await runtime.set_voice_session_activation_factory(
        factory,
        activation_generation="profile",
    )
    await runtime._route_microphone_audio(
        b"\xd0\x07" * 1_600,
        sample_rate_hz=16_000,
    )
    await asyncio.sleep(0)
    assert runtime._voice_session_activation_runtime is factory.runtimes[0]

    runtime._invalidate_voice_pcm_sync("microphone_stopped")
    cleanup = tuple(runtime._core_asr_cleanup_tasks)
    if cleanup:
        await asyncio.gather(*cleanup)

    assert runtime._voice_session_activation_runtime is None
    assert factory.scorers[0].closed is True
    assert factory.closed is False


async def test_session_activation_detach_failure_blocks_microphone_pcm() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.session.stream_audio = AsyncMock()
    runtime._speaker_shadow_factory = MagicMock()
    runtime._asr_runtime.set_speaker_verifier_factory = AsyncMock(return_value=False)

    result = await runtime.set_voice_session_activation_factory(
        _CoreActivationFactory(),
        activation_generation="profile",
    )
    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert result is VoiceIdentityActivationResult.RUNTIME_DEGRADED
    assert consumed is True
    assert runtime._speaker_shadow_factory is not None
    assert runtime._voice_session_activation_factory is None
    assert runtime._voice_session_activation_degraded is True
    runtime.session.stream_audio.assert_not_awaited()


async def test_cancelled_session_activation_detach_blocks_microphone_pcm() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.session.stream_audio = AsyncMock()
    runtime._speaker_shadow_factory = MagicMock()
    runtime._asr_runtime.set_speaker_verifier_factory = AsyncMock(
        side_effect=asyncio.CancelledError
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.set_voice_session_activation_factory(
            _CoreActivationFactory(),
            activation_generation="profile",
        )
    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert runtime._speaker_shadow_factory is not None
    assert runtime._voice_session_activation_factory is None
    assert runtime._voice_session_activation_degraded is True
    runtime.session.stream_audio.assert_not_awaited()


async def test_inflight_session_activation_detach_blocks_microphone_pcm() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.session.stream_audio = AsyncMock()
    runtime._speaker_shadow_factory = MagicMock()
    detach_entered = asyncio.Event()
    release_detach = asyncio.Event()

    async def delayed_detach(*_args, **_kwargs) -> bool:
        detach_entered.set()
        await release_detach.wait()
        return False

    runtime._asr_runtime.set_speaker_verifier_factory = AsyncMock(
        side_effect=delayed_detach
    )
    transition = asyncio.create_task(
        runtime.set_voice_session_activation_factory(
            _CoreActivationFactory(),
            activation_generation="profile",
        )
    )
    await detach_entered.wait()

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert runtime._voice_session_activation_degraded is True
    runtime.session.stream_audio.assert_not_awaited()
    release_detach.set()
    assert await transition is VoiceIdentityActivationResult.RUNTIME_DEGRADED
