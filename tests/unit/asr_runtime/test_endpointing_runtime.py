import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.asr_client.runtime import AsrStartStatus
from main_logic.asr_client.endpointing.detector_runtime import DetectorFeedResult
from main_logic.asr_client.lifecycle import VoiceLifecycleEvent, VoiceLifecycleState, VoiceTurnToken, VoiceRouteMode
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_turn.contracts import AsrSubmitStatus, SpeechActivityEvent, VoiceIngressToken

from tests.support.core_asr_harness import (
    _QueuedSmartTurnDetector,
    _ReadyDetector,
    _TestSmartTurnLease,
    _install_active_smart_turn,
    _install_ready_lifecycle,
    _install_replacement_runtime_generation,
)

from tests.support.asr_fakes import (
    _Runtime,
    _selection,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


class _FailedSmartTurnDetector(_ReadyDetector):
    async def prepare_endpointing(self, token):
        self._token = None
        return None

    def endpointing_ready(self, token) -> bool:
        return False


@pytest.mark.parametrize(
    "provider",
    ["dummy", "glm", "gemini"],
)
async def test_smart_turn_unavailable_blocks_segmented_provider_before_wire_audio(
    provider: str,
) -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(
            provider,
            "manual",
        ),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _FailedSmartTurnDetector()

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_route_mode == "blocked"
    assert runtime._omni_mic_audio_bytes == 0


@pytest.mark.parametrize("provider", ["qwen", "grok", "soniox"])
async def test_provider_endpoint_does_not_wait_for_smart_turn(
    provider: str,
) -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "provider"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _FailedSmartTurnDetector(
        DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
    )
    pcm16 = b"\x01\x00" * 160

    assert await runtime._route_microphone_audio(
        pcm16,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once_with(pcm16, sample_rate_hz=16_000)
    assert runtime._asr_route_mode == "independent"
    assert runtime._omni_mic_audio_bytes == 0


@pytest.mark.parametrize("provider", ["qwen", "openai"])
async def test_optimization_disabled_provider_route_never_prepares_smart_turn(
    provider: str,
) -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    asr = type("Asr", (), {"is_ready": True, "stream_audio": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "provider"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _ReadyDetector()
    detector.prepare_endpointing = AsyncMock()
    runtime._asr_detector = detector

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once()
    detector.prepare_endpointing.assert_not_awaited()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_smart_turn_lease is None
    assert runtime._omni_mic_audio_bytes == 0


async def test_stale_pending_activation_discards_confirmed_candidate() -> None:
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
    assert lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    assert lifecycle.has_pending_turn is True

    await runtime._activate_pending_independent_turn(epoch)

    assert lifecycle.pending_turn_bytes == 0
    assert lifecycle.has_pending_turn is False
    assert runtime._asr_pending_detector_candidate is None


@pytest.mark.parametrize("provider", ["glm", "gemini"])
async def test_smart_turn_fail_open_buffers_until_deep_sleep_transport_reconnects(
    provider: str,
) -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = provider
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.WARM_EXPIRED)
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
    runtime._asr_transport_selection = _selection(provider)
    pcm16 = b"\x03\x00" * 160

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


async def test_old_smart_turn_release_cannot_clear_replacement_lease() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "glm")
    lifecycle = runtime._asr_lifecycle
    detector = runtime._asr_detector
    assert lifecycle is not None
    assert detector is not None
    release_started = asyncio.Event()
    release_old_lease = asyncio.Event()

    class BlockingLease:
        token = object()

        async def release(self) -> None:
            release_started.set()
            await release_old_lease.wait()

    old_lease = BlockingLease()
    runtime._asr_smart_turn_lease = old_lease
    prepare_task = asyncio.create_task(
        runtime._asr_runtime._ensure_smart_turn_ready(
            lifecycle,
            runtime._asr_session_epoch,
        )
    )
    await asyncio.wait_for(release_started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "glm"
    )
    new_lease = _TestSmartTurnLease(
        runtime._asr_runtime._capture_turn_token(new_lifecycle)
    )
    runtime._asr_smart_turn_lease = new_lease
    release_old_lease.set()

    assert await asyncio.wait_for(prepare_task, 1) is False
    assert runtime._asr_smart_turn_lease is new_lease
    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert new_lease.released is False


async def test_concurrent_smart_turn_readiness_callers_share_installed_lease() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "glm")
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    prepare_started = asyncio.Event()
    release_prepare = asyncio.Event()

    class _Lease:
        def __init__(self, token, detector) -> None:
            self.token = token
            self._detector = detector
            self.released = False

        async def release(self) -> None:
            self.released = True
            self._detector.token = None

    class _BlockingDetector:
        def __init__(self) -> None:
            self.token = None
            self.prepare_calls = 0

        async def prepare_endpointing(self, token):
            self.prepare_calls += 1
            prepare_started.set()
            await release_prepare.wait()
            self.token = token
            return _Lease(token, self)

        def endpointing_ready(self, token) -> bool:
            return self.token == token

    detector = _BlockingDetector()
    runtime._asr_detector = detector
    component = runtime._asr_runtime
    epoch = component._asr_session_epoch
    first = asyncio.create_task(
        component._ensure_smart_turn_ready(lifecycle, epoch)
    )
    await asyncio.wait_for(prepare_started.wait(), 1)
    second_started = asyncio.Event()

    async def ensure_from_speech_caller() -> bool:
        second_started.set()
        return await component._ensure_smart_turn_ready(lifecycle, epoch)

    second = asyncio.create_task(ensure_from_speech_caller())
    await asyncio.wait_for(second_started.wait(), 1)
    release_prepare.set()

    assert await asyncio.wait_for(first, 1) is True
    assert await asyncio.wait_for(second, 1) is True
    assert detector.prepare_calls == 1
    lease = component._asr_smart_turn_lease
    assert lease is not None
    assert lease.released is False
    assert detector.endpointing_ready(lease.token) is True


@pytest.mark.parametrize("provider", ["qwen", "soniox"])
async def test_manual_streaming_provider_waits_for_smart_turn(
    provider: str,
) -> None:
    runtime = _Runtime()
    lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "manual"),
        shadow_mode=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _FailedSmartTurnDetector()
    turn_token = VoiceTurnToken(
        VoiceIngressToken(1, "socket", 1, 1, 1),
        turn_id=1,
    )

    assert runtime._asr_endpointing_ready(lifecycle, detector, turn_token) is False


async def test_enforced_lifecycle_suppresses_local_silence_upload() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = type(
        "Detector",
        (),
        {"feed": AsyncMock(return_value=DetectorFeedResult((), True))},
    )()

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    assert consumed is True
    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_lifecycle.pre_roll_bytes == 320


async def test_turn_endpoint_seals_immediately_before_provider_final() -> None:
    runtime = _Runtime()
    runtime._asr_session = type("Asr", (), {"is_ready": True})()
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )

    await runtime._handle_independent_asr_endpoint(epoch)

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING


async def test_optimization_disabled_streaming_uploads_without_smart_turn() -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    asr = type("Asr", (), {"is_ready": True, "stream_audio": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "provider"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_smart_turn_lease is None
    assert runtime._asr_detector._token is None
    assert runtime._omni_mic_audio_bytes == 0


async def test_detector_failure_fails_open_to_same_independent_asr() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime)
    runtime._asr_detector.feed = AsyncMock(return_value=DetectorFeedResult((), False))

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once_with(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_route_mode == "independent"


async def test_stale_detector_feed_exception_cannot_fail_new_generation() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    started = asyncio.Event()
    release = asyncio.Event()

    class _BlockingDetector(_ReadyDetector):
        async def feed(self, _pcm16: bytes, **_kwargs):
            started.set()
            await release.wait()
            raise RuntimeError("old detector failed")

    runtime._asr_detector = _BlockingDetector()
    ingress = runtime._capture_ingress_token()
    runtime._asr_runtime._asr_current_ingress_token = ingress
    submit = asyncio.create_task(
        runtime._asr_runtime.submit(
            ProcessedVoiceFrame(b"\x01\x00" * 160, 16_000, 0.8, True),
            ingress_token=ingress,
        )
    )
    await asyncio.wait_for(started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    release.set()
    result = await asyncio.wait_for(submit, 1)

    assert result.status is AsrSubmitStatus.STALE
    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    new_session.close.assert_not_awaited()
    runtime.send_status.assert_not_awaited()


async def test_current_detector_feed_exception_fails_closed_once() -> None:
    runtime = _Runtime()
    runtime._asr_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    _install_ready_lifecycle(runtime, "qwen")
    runtime._asr_detector.feed = AsyncMock(
        side_effect=RuntimeError("current detector failed")
    )
    ingress = runtime._capture_ingress_token()
    runtime._asr_runtime._asr_current_ingress_token = ingress

    result = await runtime._asr_runtime.submit(
        ProcessedVoiceFrame(b"\x01\x00" * 160, 16_000, 0.8, True),
        ingress_token=ingress,
    )
    await asyncio.sleep(0)

    assert result.status is AsrSubmitStatus.UNAVAILABLE
    codes = [
        json.loads(call.args[0])["code"] for call in runtime.send_status.await_args_list
    ]
    assert codes.count("ASR_INDEPENDENT_STREAM_FAILED") == 1
    assert runtime._asr_session is None
    assert runtime._asr_lifecycle is None
    assert runtime._asr_detector is None


async def test_failed_detector_construction_closes_created_speaker_shadow(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    selection = _selection("qwen", "provider")
    session = SimpleNamespace(
        is_ready=True,
        connect=AsyncMock(),
        close=AsyncMock(),
    )
    shadow = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        lambda _core_type: selection,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        lambda _core_type, **_kwargs: session,
    )
    monkeypatch.setattr(
        runtime_module,
        "DetectorRuntime",
        MagicMock(side_effect=RuntimeError("detector construction failed")),
    )

    result = await runtime._asr_runtime.start(
        route_key="qwen",
        resource_optimization_enabled=True,
        speaker_shadow_factory=lambda: shadow,
    )

    assert result.status in {AsrStartStatus.FAILED, AsrStartStatus.UNAVAILABLE}
    shadow.close.assert_awaited_once_with()


async def test_provider_fence_failure_does_not_accept_final() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    detector.complete_provider_candidate.return_value = None
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)

    await runtime._handle_independent_asr_final(
        "must-not-publish",
        epoch,
        "openai",
    )

    statuses = [json.loads(call.args[0]) for call in runtime.send_status.await_args_list]
    codes = [payload["code"] for payload in statuses]
    assert codes.count("ASR_ENDPOINTING_FAILED") == 1
    assert runtime._asr_accepted_final_keys == {}
    runtime.handle_input_transcript.assert_not_awaited()
    assert runtime._asr_route_mode == "blocked"


async def test_stale_provider_endpoint_releases_local_final_reservation() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    component = runtime._asr_runtime
    component._runtime_identity_matches = MagicMock(return_value=False)
    epoch = component._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )

    await runtime._handle_independent_asr_endpoint(epoch)

    assert component._asr_reserved_final_key is None
    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE


async def test_provider_successor_discard_failure_fails_closed_once() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    detector.discard_provider_successor.side_effect = RuntimeError("private failure")
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    ingress_token = runtime._asr_runtime._asr_current_ingress_token
    assert ingress_token is not None

    await runtime._handle_audio_ingress_backpressure(
        ingress_token,
        observed_state=VoiceLifecycleState.DRAINING,
    )

    statuses = [json.loads(call.args[0]) for call in runtime.send_status.await_args_list]
    codes = [payload["code"] for payload in statuses]
    assert codes.count("ASR_ENDPOINTING_FAILED") == 1
    assert codes.count("ASR_INGRESS_BACKPRESSURE") == 0
    assert runtime._asr_session_epoch == epoch + 1
    assert runtime._asr_route_mode == "blocked"
    assert "private failure" not in str(runtime.send_status.await_args_list)
