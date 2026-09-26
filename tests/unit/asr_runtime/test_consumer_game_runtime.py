import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, call
import pytest
from main_logic.asr_client.endpointing.detector_runtime import DetectorFeedResult
from main_logic.asr_client.lifecycle import VoiceLifecycleState, VoiceTurnToken, VoiceRouteMode
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.activity_evidence import RnnoiseEvidence
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_turn.contracts import AsrFailureEvent, AsrSubmitResult, AsrSubmitStatus, SpeechActivityEvent, VoiceTranscriptEvent
from main_logic.asr_client.endpointing.detector import CoreDetectorEventEnvelope, DetectorCandidateKey, DetectorIngressIdentity, DetectorRuntimeEvent

from tests.support.core_asr_harness import (
    _ReadyDetector,
    _install_ready_lifecycle,
    _start_and_seal_turn,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_external_voice_suppression_resets_native_audio_turn() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime._invalidate_voice_pcm_sync = MagicMock()
    runtime._abort_independent_asr = AsyncMock()
    runtime.session.clear_audio_buffer = AsyncMock()

    await runtime.set_voice_input_suppressed(
        "voice_identity_enrollment",
        suppressed=True,
    )

    runtime.session.clear_audio_buffer.assert_awaited_once_with()
    runtime._abort_independent_asr.assert_not_awaited()


async def test_game_takeover_clears_provider_audio_and_suspends_lifecycle() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = type("Detector", (), {"reset": AsyncMock()})()
    runtime._asr_detector = detector

    await runtime._suspend_independent_voice_input_for_game()

    asr.close.assert_awaited_once_with()
    detector.reset.assert_awaited_once_with()
    assert runtime._asr_lifecycle.snapshot.state.value == "suspended"

    await runtime._resume_independent_voice_input_after_game()
    assert runtime._asr_lifecycle.snapshot.state.value == "local_listen"


async def test_game_takeover_wins_even_if_provider_clear_fails() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock(side_effect=RuntimeError("provider abort failed"))
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)

    await runtime._suspend_independent_voice_input_for_game()

    assert runtime._asr_lifecycle.snapshot.state.value == "suspended"


async def test_game_consumer_reuses_smart_turn_asr_without_core(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    route_transcript = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.get_active_game_route_identity",
        lambda _name: ("game", "session-a"),
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_accepts_pcm() is True

    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")
    await runtime._handle_independent_asr_final("play", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    route_transcript.assert_awaited_once_with(
        "Test",
        "play",
        request_id=f"asr-{epoch}-1",
        game_type="game",
        session_id="session-a",
    )
    runtime.handle_new_message.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            2,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )


async def test_game_takeover_pre_abort_window_rejects_stale_core_turn(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    route_transcript = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.get_active_game_route_identity",
        lambda _name: ("game", "session-a"),
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )
    runtime.session.abandon_external_voice_turn = MagicMock()
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    _install_ready_lifecycle(runtime, "openai")
    runtime._asr_session.close = AsyncMock()
    runtime._asr_session.signal_user_activity_end = AsyncMock()

    preview_clear_started = asyncio.Event()
    release_preview_clear = asyncio.Event()

    async def block_preview_clear(payload: dict[str, object]) -> None:
        if (
            payload.get("type") == "user_transcript_preview"
            and payload.get("text") == ""
        ):
            preview_clear_started.set()
            await release_preview_clear.wait()

    runtime.websocket = SimpleNamespace(
        send_json=AsyncMock(side_effect=block_preview_clear),
    )
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "openai")
    sealed = runtime._asr_runtime._asr_sealed_turn_token
    assert sealed is not None
    stale_ingress = sealed.turn.ingress

    await runtime._handle_independent_asr_final("", epoch, "openai")
    await asyncio.wait_for(preview_clear_started.wait(), 1)

    takeover = asyncio.create_task(
        runtime._handle_voice_input_control("game_takeover", 2)
    )
    endpoint: asyncio.Task[None] | None = None
    try:
        for _ in range(100):
            if runtime._voice_lease_owner == "game":
                break
            await asyncio.sleep(0)
        assert runtime._voice_lease_owner == "game"
        assert runtime._voice_lease_generation == 2
        assert takeover.done() is False

        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED,
            epoch,
        )
        prepared_token = runtime._asr_runtime._asr_partial_turn_token
        endpoint = asyncio.create_task(
            runtime._handle_independent_asr_endpoint(epoch)
        )
        for _ in range(100):
            if endpoint.done():
                break
            await asyncio.sleep(0)
        await runtime._handle_independent_asr_final(
            "stale core audio",
            epoch,
            "openai",
        )
        await runtime._asr_runtime.wait_transcript_idle()

        assert stale_ingress.lease_generation == 1
        assert prepared_token is None
        route_transcript.assert_not_awaited()
    finally:
        release_preview_clear.set()
        pending = [takeover]
        if endpoint is not None:
            pending.append(endpoint)
        results = await asyncio.wait_for(asyncio.gather(*pending), 1)
        assert results[0] is True
        await runtime._voice_input_registry.wait_idle()

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{epoch}-1"
    )


async def test_game_takeover_during_core_prepare_drops_stale_message() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    prepare_started = asyncio.Event()
    release_prepare = asyncio.Event()

    async def block_prepare(*, turn_id: str) -> None:
        prepare_started.set()
        await release_prepare.wait()

    runtime.session.prepare_external_voice_turn = AsyncMock(side_effect=block_prepare)
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime._asr_runtime.suspend = AsyncMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    prepare_task = asyncio.create_task(runtime._prepare_core_voice_turn(token))
    await asyncio.wait_for(prepare_started.wait(), 1)

    await runtime._suspend_independent_voice_input_for_game()
    release_prepare.set()

    assert await asyncio.wait_for(prepare_task, 1) is False
    runtime.handle_new_message.assert_not_awaited()
    external_turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    assert runtime.session.abandon_external_voice_turn.call_args_list == [
        call(external_turn_id),
    ]


async def test_game_takeover_suppresses_stale_detector_dispatcher_failure() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    runtime._asr_session = asr
    _install_ready_lifecycle(runtime, "qwen")
    detector = _ReadyDetector()
    detector.detector_epoch = 1
    runtime._asr_detector = detector
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    ingress_token = runtime._capture_ingress_token(lifecycle)
    envelope = CoreDetectorEventEnvelope(
        event=DetectorRuntimeEvent(
            ingress=DetectorIngressIdentity(
                ingress_token=ingress_token,
                detector_epoch=detector.detector_epoch,
                sequence_no=1,
            ),
            candidate=DetectorCandidateKey(detector.detector_epoch, 1),
            kind="control_lane_failed",
        ),
        detector_ref=detector,
        lifecycle_ref=lifecycle,
        session_epoch=runtime._asr_session_epoch,
    )

    assert await runtime._handle_voice_input_control(
        "game_takeover",
        1,
    )
    runtime.send_status.reset_mock()
    await runtime._handle_asr_detector_dispatcher_failure(
        envelope,
        RuntimeError("old detector callback failed after game takeover"),
    )

    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_lifecycle is lifecycle
    assert lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED
    runtime.send_status.assert_not_awaited()


async def test_game_takeover_during_transcript_drops_stale_core_final() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "glm")
    transcript_started = asyncio.Event()
    release_transcript = asyncio.Event()

    async def block_transcript(*_args, **_kwargs) -> bool:
        transcript_started.set()
        await release_transcript.wait()
        return True

    runtime.handle_input_transcript.side_effect = block_transcript
    runtime.session.submit_external_voice_turn = AsyncMock()
    runtime._asr_runtime.suspend = AsyncMock()
    turn_token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    event = VoiceTranscriptEvent(
        turn_token=turn_token,
        provider="glm",
        text="belongs to Core",
    )
    dispatch_task = asyncio.create_task(runtime._dispatch_core_asr_transcript(event))
    await asyncio.wait_for(transcript_started.wait(), 1)

    await runtime._suspend_independent_voice_input_for_game()
    release_transcript.set()
    await asyncio.wait_for(dispatch_task, 1)

    runtime.session.submit_external_voice_turn.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()


async def test_game_consumer_accepts_real_pcm_through_pipeline(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = False
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.get_active_game_route_identity",
        lambda _name: ("game", "session-a"),
    )
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "qwen"
    route_audio = AsyncMock(return_value=True)
    runtime._route_microphone_audio = route_audio
    evidence = RnnoiseEvidence(True, 3, 0.9, 0.6, 0.2, 0.55)
    processed = ProcessedVoiceFrame(
        pcm16=b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=0.8,
        rnnoise_available=True,
        rnnoise_evidence=evidence,
    )
    runtime._voice_input_audio_pipeline.process = AsyncMock(return_value=processed)
    token = runtime._capture_ingress_token()

    await runtime._process_microphone_stream_data(
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 160,
        },
        ingress_token=token,
        captured_at=1234.5,
    )

    runtime._voice_input_audio_pipeline.process.assert_awaited_once()
    route_audio.assert_awaited_once_with(
        processed.pcm16,
        sample_rate_hz=processed.sample_rate_hz,
        speech_probability=processed.speech_probability,
        rnnoise_available=processed.rnnoise_available,
        rnnoise_evidence=evidence,
        ingress_token=token,
        received_at=ANY,
        captured_at=1234.5,
    )


async def test_game_consumer_submit_preserves_owner_identity(monkeypatch) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "qwen"
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
    )
    token = runtime._capture_ingress_token()
    evidence = RnnoiseEvidence(True, 3, 0.9, 0.6, 0.2, 0.55)
    processed = ProcessedVoiceFrame(
        pcm16=b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=0.8,
        rnnoise_available=True,
        rnnoise_evidence=evidence,
    )

    await runtime._route_microphone_audio(
        processed.pcm16,
        sample_rate_hz=processed.sample_rate_hz,
        speech_probability=processed.speech_probability,
        rnnoise_available=processed.rnnoise_available,
        rnnoise_evidence=evidence,
        ingress_token=token,
    )

    runtime._asr_runtime.submit.assert_awaited_once_with(
        processed,
        ingress_token=token,
    )


async def test_game_consumer_failure_never_falls_back_to_core(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    route_transcript = AsyncMock(side_effect=RuntimeError("consumer failed"))
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.get_active_game_route_identity",
        lambda _name: ("game", "session-a"),
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )
    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="game",
        hard_muted=False,
        focus_suppressed=False,
    )
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")

    await runtime._handle_independent_asr_final("play", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    route_transcript.assert_awaited_once_with(
        "Test",
        "play",
        request_id=f"asr-{epoch}-1",
        game_type="game",
        session_id="session-a",
    )
    runtime.handle_new_message.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0


async def test_game_owner_without_consumer_remains_fail_closed() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )

    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED
    assert runtime._omni_mic_audio_bytes == 0


async def test_hard_mute_is_backend_authoritative_and_rejects_stale_lease_events() -> (
    None
):
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = type("Detector", (), {})()
    detector.reset = AsyncMock()
    detector.feed = AsyncMock(return_value=DetectorFeedResult((), True))
    runtime._asr_detector = detector
    runtime._clear_audio_stream_queue = MagicMock()
    runtime.hot_swap_audio_cache = [b"old-pcm"]
    old_token = runtime._capture_ingress_token(runtime._asr_lifecycle)

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            12,
            owner="core",
            hard_muted=True,
            focus_suppressed=False,
        )
        is True
    )
    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    asr.close.assert_awaited_once_with()
    runtime._clear_audio_stream_queue.assert_called_once_with("lease_sync")
    assert runtime.hot_swap_audio_cache == []
    assert runtime._ingress_token_matches(old_token) is False
    detector.reset.assert_awaited_once_with()
    detector.feed.assert_not_awaited()
    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_lifecycle.pre_roll_bytes == 0

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            11,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is False
    )
    assert runtime._voice_input_suppressed is True
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            13,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_suppressed is False


async def test_hard_mute_suppresses_stale_audio_dispatcher_failure() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    runtime._asr_session = asr
    _install_ready_lifecycle(runtime, "qwen")
    runtime._asr_detector = _ReadyDetector()
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    turn_token = VoiceTurnToken(
        ingress=runtime._capture_ingress_token(lifecycle),
        turn_id=lifecycle.snapshot.turn_id,
    )

    assert await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="core",
        hard_muted=True,
        focus_suppressed=False,
    )
    runtime.send_status.reset_mock()
    await runtime._handle_asr_audio_dispatcher_failure(
        turn_token,
        RuntimeError("old provider write failed after hard mute"),
    )

    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_lifecycle is lifecycle
    runtime.send_status.assert_not_awaited()


async def test_runtime_failure_leaves_the_game_lease_alone() -> None:
    # The galgame route holds the mic through its built-in consumer route and tears
    # down via GAME_ROUTE_ENDED; re-basing the identity must not start
    # collaterally revoking it.
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._voice_lease_connection_id = "socket-a"
    runtime._voice_lease_owner = "game"

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="current-provider",
            session_epoch=runtime._asr_session_epoch,
        )
    )

    assert runtime._voice_lease_connection_id == "socket-a"


async def test_current_game_release_still_aborts_and_resumes_once() -> None:
    runtime = _Runtime()
    runtime._voice_lease_connection_id = "connection"
    runtime._voice_lease_generation = 1
    runtime._voice_lease_owner = "game"
    runtime._asr_runtime.abort = AsyncMock()
    runtime._asr_runtime.resume = AsyncMock()

    await runtime._apply_voice_lease_state(
        owner="core",
        hard_muted=False,
        focus_suppressed=False,
        reason="game_release",
        force_abort=True,
    )

    runtime._asr_runtime.abort.assert_awaited_once_with("game_release")
    runtime._asr_runtime.resume.assert_awaited_once_with("game_release")
