import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.asr_client.lifecycle import VoiceLifecycleConfig, VoiceLifecycleEvent, VoiceLifecycleState, VoiceRouteMode
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.contracts import SpeechActivityEvent

from tests.support.core_asr_harness import (
    _QueuedSmartTurnDetector,
    _ReadyDetector,
    _install_ready_lifecycle,
    _start_and_seal_turn,
)

from tests.support.asr_fakes import (
    _Runtime,
    _selection,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def test_independent_asr_activity_probe_is_provider_neutral() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")

    assert runtime._independent_asr_user_turn_active() is False

    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._independent_asr_user_turn_active() is True

    runtime._asr_route_mode = "native"
    assert runtime._independent_asr_user_turn_active() is False


async def test_activity_probe_tracks_accepted_final_until_dispatch_completes() -> None:
    runtime = _Runtime()
    await _start_and_seal_turn(runtime, "qwen")
    sealed_token = runtime._asr_sealed_turn_token
    assert sealed_token is not None
    release_started = asyncio.Event()
    release_lease = asyncio.Event()
    dispatch_started = asyncio.Event()
    release_dispatch = asyncio.Event()

    class _BlockingLease:
        token = sealed_token.turn

        async def release(self) -> None:
            release_started.set()
            await release_lease.wait()

    async def block_dispatch(*_args, **_kwargs) -> bool:
        dispatch_started.set()
        await release_dispatch.wait()
        return True

    runtime._asr_smart_turn_lease = _BlockingLease()
    runtime.handle_input_transcript.side_effect = block_dispatch
    final_task = asyncio.create_task(
        runtime._handle_independent_asr_final(
            "短语音",
            runtime._asr_session_epoch,
            "qwen",
        )
    )

    await release_started.wait()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._independent_asr_user_turn_active() is True

    release_lease.set()
    await dispatch_started.wait()
    assert runtime._independent_asr_user_turn_active() is True

    release_dispatch.set()
    await final_task
    await runtime._wait_asr_transcript_dispatch_idle()
    assert runtime._independent_asr_user_turn_active() is False


async def test_segmented_fail_open_uses_continuous_wake_without_fake_speech() -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    asr = type("Asr", (), {"is_ready": True, "stream_audio": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "glm"
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("glm", "manual"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _QueuedSmartTurnDetector()
    runtime._asr_detector = detector

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await runtime._asr_detector_dispatcher.wait_idle()
    await runtime._asr_audio_dispatcher.wait_idle()

    detector.force_speech_started.assert_not_awaited()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    asr.stream_audio.assert_awaited_once()
    assert runtime._asr_route_mode == "independent"


async def test_warm_idle_pending_speech_does_not_reenter_draining_guard() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._route_microphone_audio(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
    )
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    assert lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert lifecycle.has_pending_turn is True

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    assert lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert lifecycle.has_pending_turn is True


async def test_initial_ready_transport_also_expires_from_local_listen() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    policy = replace(resolve_provider_policy("qwen", "manual"), warm_transport_ms=1_000)
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        config=VoiceLifecycleConfig(default_warm_transport_ms=0),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)

    runtime._schedule_transport_warm_expiry(
        runtime._asr_session_epoch,
        expected_state=VoiceLifecycleState.LOCAL_LISTEN,
    )
    # default_warm_transport_ms=0 会立刻到期，固定 sleep 赌不起；
    # 到期任务本身就是「已过期并关闭」的同步点。
    expiry = runtime._asr_warm_expiry_task
    assert expiry is not None
    await asyncio.wait_for(expiry, 5)

    assert runtime._asr_session is None
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    asr.close.assert_awaited_once_with()


async def test_prewarming_uses_idle_transport_ttl() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    policy = replace(resolve_provider_policy("openai", "provider"), warm_transport_ms=1_000)
    lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        config=VoiceLifecycleConfig(default_warm_transport_ms=0),
        shadow_mode=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle = lifecycle

    runtime._schedule_transport_warm_expiry(
        runtime._asr_session_epoch,
        expected_state=VoiceLifecycleState.PREWARMING,
    )
    expiry = runtime._asr_warm_expiry_task
    assert expiry is not None
    await asyncio.wait_for(expiry, 1)

    assert runtime._asr_session is None
    assert lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    asr.close.assert_awaited_once_with()


async def test_warm_idle_uses_provider_transport_ttl() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    policy = replace(resolve_provider_policy("openai", "provider"), warm_transport_ms=0)
    lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        config=VoiceLifecycleConfig(default_warm_transport_ms=1_000),
        shadow_mode=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_lifecycle = lifecycle

    runtime._schedule_transport_warm_expiry(
        runtime._asr_session_epoch,
        expected_state=VoiceLifecycleState.WARM_IDLE,
    )
    expiry = runtime._asr_warm_expiry_task
    assert expiry is not None
    await asyncio.wait_for(expiry, 1)

    assert runtime._asr_session is None
    assert lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    asr.close.assert_awaited_once_with()


async def test_prewarm_expiry_rechecks_identity_after_detector_reset() -> None:
    runtime = _Runtime()
    original_session = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = original_session
    runtime._asr_route_mode = "independent"
    policy = replace(resolve_provider_policy("openai", "provider"), warm_transport_ms=1_000)
    lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        config=VoiceLifecycleConfig(default_warm_transport_ms=0),
        shadow_mode=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle = lifecycle
    reset_started = asyncio.Event()
    reset_release = asyncio.Event()
    detector = _ReadyDetector()

    async def reset() -> None:
        reset_started.set()
        await reset_release.wait()

    detector.reset.side_effect = reset
    runtime._asr_detector = detector
    runtime._schedule_transport_warm_expiry(
        runtime._asr_session_epoch,
        expected_state=VoiceLifecycleState.PREWARMING,
    )
    await asyncio.wait_for(reset_started.wait(), 1)

    successor_session = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = successor_session
    reset_release.set()
    expiry = runtime._asr_warm_expiry_task
    assert expiry is not None
    await asyncio.wait_for(expiry, 1)

    assert lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
    original_session.close.assert_not_awaited()
    successor_session.close.assert_not_awaited()


async def test_optimization_disabled_buffers_until_initial_transport_is_ready() -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "glm"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("glm", "manual"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _QueuedSmartTurnDetector()
    new_asr = type("Asr", (), {})()
    new_asr.is_ready = True
    connect_started = asyncio.Event()
    connect_release = asyncio.Event()

    async def connect() -> None:
        connect_started.set()
        await connect_release.wait()

    new_asr.connect = AsyncMock(side_effect=connect)
    new_asr.stream_audio = AsyncMock()
    runtime._asr_session_factory = MagicMock(return_value=new_asr)
    runtime._asr_transport_selection = _selection("glm")
    pcm16 = b"\x04\x00" * 160

    await runtime._route_microphone_audio(
        pcm16,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await asyncio.wait_for(connect_started.wait(), 1)

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_lifecycle.pending_connect_bytes == len(pcm16)
    connect_release.set()
    await runtime._asr_detector_dispatcher.wait_idle()
    await runtime._asr_audio_dispatcher.wait_idle()

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_route_mode == "independent"
    assert runtime._omni_mic_audio_bytes == 0
    new_asr.connect.assert_awaited_once_with()
    new_asr.stream_audio.assert_awaited_once_with(
        pcm16,
        sample_rate_hz=16_000,
    )
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert all(status.get("code") != "ASR_BLOCKED_ENDPOINTING" for status in statuses)
