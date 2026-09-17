import asyncio
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.asr_client.lifecycle import VoiceLifecycleState
from main_logic.voice_turn.contracts import SpeechActivityEvent, VoiceTranscriptEvent

from tests.unit.asr_runtime._scenarios import (
    _start_runtime_with_callback_candidates,
)

from tests.support.asr_fakes import (
    _Runtime,
)

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
    _start_and_seal_turn,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_speech_started_interrupts_and_prepares_turn_once() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    runtime.session.handle_interruption.assert_awaited_once_with()
    runtime.handle_new_message.assert_awaited_once_with()
    assert runtime._asr_turn_prepared is True


async def test_speech_started_prepares_external_voice_turn() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.prepare_external_voice_turn = AsyncMock()

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    runtime.session.prepare_external_voice_turn.assert_awaited_once_with(
        turn_id=f"asr-{runtime._asr_session_epoch}-1"
    )
    runtime.handle_new_message.assert_awaited_once_with()


async def test_rejected_prepare_fails_closed_instead_of_sealing_turn() -> None:
    runtime = _Runtime()
    runtime._asr_session = type(
        "Asr", (), {"is_ready": True, "close": AsyncMock()}
    )()
    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime.handle_new_message.side_effect = RuntimeError("prepare rejected")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    assert runtime._asr_turn_prepared is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE

    await runtime._handle_independent_asr_endpoint(epoch)

    # A persistently rejected preparation must never seal the turn: sealing
    # is the only gate through which a provider final reaches Core, and Core
    # does not re-run the interruption/external-turn pause at dispatch time.
    assert runtime._asr_route_mode == "blocked"
    assert "ASR_CORE_TURN_REJECTED" in str(runtime.send_status.await_args_list)

    await runtime._handle_independent_asr_final("hello", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()


async def test_endpoint_reprepares_turn_after_transient_prepare_rejection() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime.handle_new_message.side_effect = [RuntimeError("transient"), None]
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    assert runtime._asr_turn_prepared is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE

    await runtime._handle_independent_asr_endpoint(epoch)

    # The retry-able recovery path: the endpoint re-runs preparation, so the
    # interruption/external-turn pause is established before the seal and the
    # provider final is injected normally.
    assert runtime._asr_turn_prepared is True
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    assert runtime.handle_new_message.await_count == 2

    await runtime._handle_independent_asr_final("hello", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once()
    runtime.session.create_response.assert_awaited_once_with("hello")


async def test_empty_final_completes_turn_without_core_injection() -> None:
    runtime = _Runtime()
    runtime.session.prepare_external_voice_turn = AsyncMock()
    runtime.session.abandon_external_voice_turn = MagicMock()
    await _start_and_seal_turn(runtime)
    turn_id = runtime.session.prepare_external_voice_turn.await_args.kwargs["turn_id"]

    await runtime._handle_independent_asr_final(
        "",
        runtime._asr_session_epoch,
        "qwen",
    )
    # Teardown racing the queued empty final may win or lose, but both paths
    # terminate the same pinned route. Repeated invalidation and a duplicate
    # provider final must not produce a second cancellation/abandonment.
    runtime._invalidate_voice_pcm_sync("duplicate_after_empty_final")
    runtime._invalidate_voice_pcm_sync("duplicate_after_empty_final")
    await runtime._handle_independent_asr_final(
        "",
        runtime._asr_session_epoch,
        "qwen",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_lifecycle.metrics.false_wake_count == 1
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    runtime.session.abandon_external_voice_turn.assert_called_once_with(turn_id)
    assert runtime._omni_mic_audio_bytes == 0


async def test_blocked_consumer_callback_does_not_block_next_turn_lifecycle() -> (
    None
):
    runtime = _Runtime()
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def block_first_final(*_args, **_kwargs) -> bool:
        callback_started.set()
        await release_callback.wait()
        return True

    runtime.handle_input_transcript.side_effect = block_first_final
    await _start_and_seal_turn(runtime, "qwen")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_final("first", epoch, "qwen")
    await asyncio.wait_for(callback_started.wait(), 1)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_turn_prepared is True
    release_callback.set()
    await runtime._wait_asr_transcript_dispatch_idle()
    runtime.session.create_response.assert_awaited_once_with("first")


async def test_final_transcript_is_dropped_when_the_route_leaves_core_mid_restore() -> None:
    # Codex P2, the other half of the case above. Pinning session_ref protects
    # only the SESSION: a game or text takeover landing inside the preview
    # restore's websocket send moves _voice_lease_owner off "core" WITHOUT
    # necessarily replacing self.session, and the transcript was still injected
    # and an ordinary Core response started after the route had left Core.
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    timed_session = runtime.session

    takeover_ran = False

    async def _game_takeover_mid_restore(*_args, **_kwargs) -> None:
        nonlocal takeover_ran
        takeover_ran = True
        runtime._voice_lease_owner = "game"

    runtime._restore_core_asr_preview_after_final = _game_takeover_mid_restore

    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(turn_token=token, provider="qwen", text="hello"),
    )

    # Pin from OUTSIDE the hook that the race was actually manufactured; without
    # this the case degrades into an ordinary final that never left Core.
    assert takeover_ran
    assert runtime._voice_lease_owner == "game"
    # Session identity never moved, so only the route check can catch this.
    assert runtime.session is timed_session
    # No Core response is started for a route that has moved on.
    timed_session.create_response.assert_not_awaited()


async def test_identical_text_in_consecutive_turns_is_delivered_twice() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch

    for _ in range(2):
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED,
            epoch,
        )
        await runtime._handle_independent_asr_endpoint(epoch)
        await runtime._handle_independent_asr_final("嗯", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["嗯", "嗯"]
    assert [
        call.args[0] for call in runtime.session.create_response.await_args_list
    ] == [
        "嗯",
        "嗯",
    ]


async def test_blocked_core_response_does_not_block_next_asr_turn() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    response_started = asyncio.Event()
    release_response = asyncio.Event()

    async def block_response(_text: str) -> None:
        response_started.set()
        await release_response.wait()

    runtime.session.create_response.side_effect = block_response
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "qwen")
    await response_started.wait()

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    release_response.set()
    await runtime._wait_asr_transcript_dispatch_idle()


async def test_accepted_final_dropped_by_generation_bump_abandons_turn(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, detector = (
        await _start_runtime_with_callback_candidates(
            monkeypatch,
            candidate_count=1,
        )
    )
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    assert lifecycle is not None
    runtime.session.abandon_external_voice_turn = MagicMock()
    component._asr_current_ingress_token = runtime._capture_ingress_token()
    epoch = component._asr_session_epoch
    on_activity = callbacks[0]["on_speech_activity"]
    on_final = callbacks[0]["on_input_transcript"]

    await on_activity(SpeechActivityEvent.SPEECH_STARTED)
    assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    sealed_turn_id = lifecycle.snapshot.turn_id
    await component._handle_independent_asr_endpoint(epoch)
    assert lifecycle.snapshot.state is VoiceLifecycleState.DRAINING

    await on_final("hello world")

    # The final was accepted, but the generation moves on before the serial
    # transcript dispatcher delivers the queued envelope.
    component._asr_audio_generation += 1
    await component.wait_transcript_idle()

    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{epoch}-{sealed_turn_id}"
    )


async def test_accepted_final_identity_loss_before_dispatch_abandons_turn() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "glm")
    runtime.session.abandon_external_voice_turn = MagicMock()
    component = runtime._asr_runtime
    epoch = component._asr_session_epoch
    await _start_and_seal_turn(runtime, "glm")
    sealed_turn_id = component._asr_lifecycle.snapshot.turn_id
    lease = component._asr_smart_turn_lease
    assert lease is not None

    async def bumping_release() -> None:
        component._asr_audio_generation += 1

    lease.release = bumping_release

    await runtime._handle_independent_asr_final("hello", epoch, "glm")
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{epoch}-{sealed_turn_id}"
    )
