import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.asr_client.endpointing.detector_runtime import DetectorFeedResult
from main_logic.asr_client.lifecycle import VoiceRouteMode
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.contracts import SpeechActivityEvent

from tests.support.asr_fakes import (
    _Runtime,
)

from tests.support.core_asr_harness import (
    _ReadyDetector,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


def _lease_resync_statuses(runtime: _Runtime) -> list[dict]:
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    return [
        status
        for status in statuses
        if status["code"] == "VOICE_INPUT_LEASE_RESYNC_REQUIRED"
    ]


def _mic_frame() -> dict:
    return {"input_type": "audio", "sample_rate_hz": 16_000, "data": [1] * 160}


async def test_lease_resync_rearms_for_new_microphone_route_generation() -> None:
    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True

    await runtime._maybe_signal_voice_lease_resync()
    first_episode = runtime._voice_lease_resync_signal_state
    assert first_episode is not None
    assert first_episode[-1] == runtime._microphone_route_generation

    runtime._set_microphone_route("native")
    assert runtime._voice_lease_resync_signal_state is None
    await runtime._maybe_signal_voice_lease_resync()
    native_episode = runtime._voice_lease_resync_signal_state
    assert native_episode is not None

    runtime._set_microphone_route("native")
    await runtime._maybe_signal_voice_lease_resync()
    assert runtime._voice_lease_resync_signal_state == native_episode
    assert runtime.send_status.await_count == 2

    runtime._set_microphone_route("blocked")

    await runtime._maybe_signal_voice_lease_resync()
    second_episode = runtime._voice_lease_resync_signal_state
    assert second_episode is not None
    assert second_episode != first_episode
    assert second_episode[-1] == runtime._microphone_route_generation
    assert runtime.send_status.await_count == 3


async def test_external_voice_suppression_aborts_once_and_restores_pcm_gate() -> None:
    runtime = _Runtime()
    runtime._invalidate_voice_pcm_sync = MagicMock()
    runtime._abort_independent_asr = AsyncMock()
    assert runtime._voice_input_accepts_pcm() is True

    await runtime.set_voice_input_suppressed(
        "voice_identity_enrollment",
        suppressed=True,
    )
    await runtime.set_voice_input_suppressed(
        "voice_identity_enrollment",
        suppressed=True,
    )

    assert runtime._voice_input_accepts_pcm() is False
    runtime._abort_independent_asr.assert_awaited_once_with(
        "voice_identity_enrollment"
    )
    assert runtime._invalidate_voice_pcm_sync.call_count == 1

    await runtime.set_voice_input_suppressed(
        "voice_identity_enrollment",
        suppressed=False,
    )

    assert runtime._voice_input_accepts_pcm() is True
    assert runtime._invalidate_voice_pcm_sync.call_count == 2


async def test_hard_mute_during_detector_await_invalidates_inflight_pcm() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "qwen"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)

    feed_started = asyncio.Event()
    release_feed = asyncio.Event()

    class _BlockingDetector(_ReadyDetector):
        async def feed(self, _pcm16: bytes, **_kwargs) -> DetectorFeedResult:
            feed_started.set()
            await release_feed.wait()
            return DetectorFeedResult((), True)

    runtime._asr_detector = _BlockingDetector()
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    route_task = asyncio.create_task(
        runtime._route_microphone_audio(
            b"\x01\x00" * 160,
            sample_rate_hz=16_000,
        )
    )
    await asyncio.wait_for(feed_started.wait(), 1)
    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="core",
        hard_muted=True,
        focus_suppressed=False,
    )
    release_feed.set()

    assert await route_task is True
    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_audio_bytes == 0
    assert runtime._omni_mic_audio_bytes == 0


@pytest.mark.parametrize(
    "newer_transition",
    [
        "game_takeover",
        "hard_mute",
        "focus_suppress",
        "lease_generation",
        "connection_replacement",
    ],
)
async def test_game_release_resume_only_survives_pcm_gating_transitions(
    newer_transition: str,
) -> None:
    """Ownership loss during the release abort must skip resume; PCM-gating
    transitions (mute/focus/lease bump) must not, because resume has no other
    call site and skipping it leaves the runtime SUSPENDED for the session."""
    runtime = _Runtime()
    runtime._voice_lease_connection_id = "connection"
    runtime._voice_lease_generation = 1
    runtime._voice_lease_owner = "game"
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def abort(reason: str) -> None:
        if reason == "game_release":
            abort_started.set()
            await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=abort)
    runtime._asr_runtime.resume = AsyncMock()
    runtime._asr_runtime.suspend = AsyncMock()
    releasing = asyncio.create_task(
        runtime._apply_voice_lease_state(
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
            reason="game_release",
            force_abort=True,
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    if newer_transition == "connection_replacement":
        runtime._begin_voice_input_connection("replacement")
    elif newer_transition == "lease_generation":
        runtime._voice_lease_generation += 1
    elif newer_transition == "game_takeover":
        await runtime._apply_voice_lease_state(
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
            reason="game_takeover",
            force_abort=True,
        )
    elif newer_transition == "hard_mute":
        await runtime._apply_voice_lease_state(
            owner="core",
            hard_muted=True,
            focus_suppressed=False,
            reason="hard_mute",
            force_abort=True,
        )
    else:
        await runtime._apply_voice_lease_state(
            owner="core",
            hard_muted=False,
            focus_suppressed=True,
            reason="focus_suppress",
            force_abort=True,
        )
    release_abort.set()
    await asyncio.wait_for(releasing, 1)

    if newer_transition in {"game_takeover", "connection_replacement"}:
        runtime._asr_runtime.resume.assert_not_awaited()
    else:
        runtime._asr_runtime.resume.assert_awaited_once_with("game_release")


async def test_unsynchronized_pcm_signals_lease_resync_once_per_state() -> None:
    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True

    for _ in range(3):
        await runtime._enqueue_audio_stream_data(_mic_frame())

    resync = _lease_resync_statuses(runtime)
    assert len(resync) == 1
    assert resync[0]["details"]["reason"] == "lease_unsynchronized"
    assert runtime._audio_stream_queue.empty()
    assert runtime._audio_stream_worker_task is None

    assert runtime._begin_voice_input_connection("pet-window") is True
    for _ in range(2):
        await runtime._enqueue_audio_stream_data(_mic_frame())

    assert len(_lease_resync_statuses(runtime)) == 2
    assert runtime._audio_stream_queue.empty()


async def test_voice_control_status_resolves_owner_after_display_delivery() -> None:
    runtime = _Runtime()
    voice_owner = None

    async def deliver_display(_message: str) -> bool:
        nonlocal voice_owner
        voice_owner = object()
        return True

    runtime.send_status = AsyncMock(side_effect=deliver_display)
    runtime._voice_owner_socket = MagicMock(side_effect=lambda: voice_owner)
    runtime._send_to_voice_owner = AsyncMock(side_effect=lambda _payload: voice_owner)

    delivered = await runtime._send_voice_control_status("lease changed")

    assert delivered == (True, True)
    runtime._send_to_voice_owner.assert_awaited_once_with(
        {"type": "status", "message": "lease changed"}
    )


async def test_synchronized_none_owner_pcm_signals_lease_resync() -> None:
    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="none",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )

    for _ in range(2):
        await runtime._enqueue_audio_stream_data(_mic_frame())

    resync = _lease_resync_statuses(runtime)
    assert len(resync) == 1
    assert resync[0]["details"]["reason"] == "owner_none"
    assert runtime._audio_stream_queue.empty()


async def test_hard_muted_pcm_never_signals_lease_resync() -> None:
    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=True,
            focus_suppressed=False,
        )
        is True
    )

    for _ in range(2):
        await runtime._enqueue_audio_stream_data(_mic_frame())

    assert _lease_resync_statuses(runtime) == []
    assert runtime._audio_stream_queue.empty()


async def test_game_owner_pcm_never_signals_lease_resync() -> None:
    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True
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

    for _ in range(2):
        await runtime._enqueue_audio_stream_data(_mic_frame())

    assert _lease_resync_statuses(runtime) == []
    assert runtime._audio_stream_queue.empty()
