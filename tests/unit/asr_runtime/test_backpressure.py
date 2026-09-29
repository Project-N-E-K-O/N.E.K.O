import asyncio
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.asr_client.lifecycle import VoiceLifecycleState, VoiceRouteMode
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.contracts import SpeechActivityEvent

from tests.support.core_asr_harness import (
    _ReadyDetector,
    _install_active_smart_turn,
    _install_ready_lifecycle,
)

from tests.support.asr_fakes import (
    _Runtime,
)

from tests.unit.asr_runtime._scenarios import (
    _start_runtime_with_callback_candidates,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def test_asr_backpressure_reports_specific_blocking_status() -> None:
    runtime = _Runtime()
    blocking_status_sent = asyncio.Event()

    async def record_status(message: str) -> None:
        if "ASR_STREAM_BACKPRESSURE" in message:
            blocking_status_sent.set()

    runtime.send_status.side_effect = record_status
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock(
        side_effect=RuntimeError("ASR_STREAM_BACKPRESSURE: queue full")
    )
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime, "qwen")

    await runtime._route_microphone_audio(
        b"\x00\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()
    await asyncio.wait_for(blocking_status_sent.wait(), 1)

    assert "ASR_STREAM_BACKPRESSURE" in runtime.send_status.await_args.args[0]
    assert runtime._asr_route_mode == "blocked"


async def test_backpressured_status_send_does_not_block_pipeline_transitions() -> None:
    """The frontend socket is unbounded; the transition lock must not wait on it.

    ``_fail_closed_voice_route`` writes the failure notice to the voice owner,
    and a throttled or backpressured client can stall that write for as long
    as it likes. Session restart, independent-ASR close and the
    noise-reduction toggle all need the same pipeline transition lock, so
    holding it across the notify phase parked every recovery operation behind
    one unrelated client write.
    """

    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    assert runtime._begin_voice_input_connection("socket-a") is True
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_synchronized = True
    runtime._asr_runtime.abort = AsyncMock()
    status_started = asyncio.Event()
    release_status = asyncio.Event()

    async def backpressured_status(_payload) -> None:
        status_started.set()
        await release_status.wait()

    runtime.send_status = AsyncMock(side_effect=backpressured_status)
    failure = asyncio.create_task(
        runtime._fail_voice_input_pipeline(
            ingress_token=runtime._capture_ingress_token(),
            session_ref=runtime.session,
            audio_epoch=runtime._audio_stream_epoch,
            pipeline_ref=runtime._voice_input_audio_pipeline,
        )
    )
    await asyncio.wait_for(status_started.wait(), 1)

    source_pipeline = runtime._voice_input_audio_pipeline
    # The client is still absorbing the notice. A toggle must not queue behind
    # it: this is the whole point of shrinking the lock.
    assert await asyncio.wait_for(
        runtime.apply_voice_input_noise_reduction(False),
        1,
    ) is True
    assert runtime._voice_input_audio_pipeline is not source_pipeline

    release_status.set()
    await asyncio.wait_for(failure, 1)

    # ...and the failure still finishes fail-closed once the client catches up.
    assert runtime._asr_route_mode == "blocked"
    assert runtime._voice_lease_connection_id == ""
    assert runtime._voice_lease_owner == "none"


async def test_idle_backpressure_trailing_activity_is_dropped_cleanly(
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
    token = runtime._capture_ingress_token()
    component._asr_current_ingress_token = token
    on_activity = callbacks[0]["on_speech_activity"]
    assert callable(on_activity)

    await component._handle_audio_ingress_backpressure(token)

    # The idle branch bumps the audio generation but keeps the session
    # adopted so genuinely new speech keeps working.
    assert lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert component._asr_session is sessions[0]
    assert not component._ingress_token_matches(token)

    # Trailing session-side speech events with the stale generation must be
    # dropped cleanly: without the identity gate the first event corrupts the
    # lifecycle toward ACTIVE and the second raises an uncaught
    # ASR_INGRESS_TOKEN_REQUIRED into the provider adapter.
    await on_activity(SpeechActivityEvent.SPEECH_STARTED)
    await on_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert component._asr_turn_prepared is False
    runtime.session.handle_interruption.assert_not_awaited()
    runtime.handle_new_message.assert_not_awaited()
    assert all(
        "ASR_INDEPENDENT_FAILED" not in call.args[0]
        for call in runtime.send_status.await_args_list
    )


async def test_idle_backpressure_new_speech_still_wakes_adopted_session(
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
    stale_token = runtime._capture_ingress_token()
    component._asr_current_ingress_token = stale_token
    on_activity = callbacks[0]["on_speech_activity"]

    await component._handle_audio_ingress_backpressure(stale_token)

    # New speech re-arms the current ingress token through submit() before
    # the provider observes it; the adopted session must then wake normally.
    component._asr_current_ingress_token = runtime._capture_ingress_token()
    await on_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert component._asr_turn_prepared is True
    runtime.handle_new_message.assert_awaited_once()


async def test_provider_overflow_lock_then_final_preserves_accepted_final() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    discard_started = asyncio.Event()
    discard_release = asyncio.Event()

    async def discard_provider_successor(_fence) -> bool:
        discard_started.set()
        await discard_release.wait()
        return True

    detector.discard_provider_successor.side_effect = discard_provider_successor
    detector.complete_provider_candidate.return_value = False
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    ingress_token = runtime._asr_runtime._asr_current_ingress_token
    assert ingress_token is not None

    overflow_task = asyncio.create_task(
        runtime._handle_audio_ingress_backpressure(
            ingress_token,
            observed_state=VoiceLifecycleState.DRAINING,
        )
    )
    await asyncio.wait_for(discard_started.wait(), 1)
    final_task = asyncio.create_task(
        runtime._handle_independent_asr_final("first", epoch, "openai")
    )
    await asyncio.sleep(0)
    assert final_task.done() is False
    discard_release.set()
    await asyncio.gather(overflow_task, final_task)
    await runtime._wait_asr_transcript_dispatch_idle()

    detector.discard_provider_successor.assert_awaited_once()
    detector.complete_provider_candidate.assert_awaited_once()
    runtime.handle_input_transcript.assert_awaited_once_with(
        "first",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "openai"},
        source_game_route_identity=None,
    )
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_accepted_final_keys


@pytest.mark.parametrize("replacement", ["epoch", "lifecycle", "detector"])
async def test_provider_overflow_waiting_on_final_lock_is_identity_fenced(
    replacement: str,
) -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    ingress_token = runtime._asr_runtime._asr_current_ingress_token
    assert ingress_token is not None

    await runtime._asr_final_lock.acquire()
    overflow_task = asyncio.create_task(
        runtime._handle_audio_ingress_backpressure(
            ingress_token,
            observed_state=VoiceLifecycleState.DRAINING,
        )
    )
    await asyncio.sleep(0)
    if replacement == "epoch":
        runtime._asr_session_epoch += 1
    elif replacement == "lifecycle":
        replacement_lifecycle = VoiceInputLifecycleController(
            provider_policy=resolve_provider_policy("openai", "provider"),
            shadow_mode=False,
        )
        replacement_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
        runtime._asr_lifecycle = replacement_lifecycle
    else:
        runtime._asr_detector = _ReadyDetector()
    runtime._asr_final_lock.release()
    await overflow_task

    detector.discard_provider_successor.assert_not_awaited()
    assert "ASR_INGRESS_BACKPRESSURE" not in str(runtime.send_status.await_args_list)
    watchdog = runtime._asr_final_watchdog_task
    if watchdog is not None:
        watchdog.cancel()


async def _complete_turn(runtime: _Runtime, epoch: int, text: str) -> None:
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final(text, epoch, "qwen")


async def _block_first_delivery_behind_queued_final_and_active_turn(
    runtime: _Runtime,
) -> asyncio.Event:
    """Leave turn 1 dispatching, turn 2 queued and turn 3 ACTIVE."""

    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    delivery_started = asyncio.Event()
    release_delivery = asyncio.Event()

    async def blocking_input(text: str, **_kwargs) -> bool:
        if text == "first":
            delivery_started.set()
            await release_delivery.wait()
        return True

    runtime.handle_input_transcript.side_effect = blocking_input
    epoch = runtime._asr_session_epoch
    await _complete_turn(runtime, epoch, "first")
    await asyncio.wait_for(delivery_started.wait(), 1)
    await _complete_turn(runtime, epoch, "second")
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_turn_prepared is True
    return release_delivery


async def test_core_backpressure_delivers_accepted_finals_exactly_once() -> None:
    runtime = _Runtime()
    release_delivery = (
        await _block_first_delivery_behind_queued_final_and_active_turn(runtime)
    )
    epoch = runtime._asr_session_epoch

    await runtime._abort_independent_asr("ingress_backpressure")
    release_delivery.set()
    await runtime._wait_asr_transcript_dispatch_idle()
    await runtime._voice_input_registry.wait_idle()

    # Only the interrupted third turn loses its audio; both finals the runtime
    # had already accepted still reach the model, in order, exactly once.
    assert runtime.session.create_response.await_args_list == [
        call("first"),
        call("second"),
    ]
    assert call(f"asr-{epoch}-3") in (
        runtime.session.abandon_external_voice_turn.call_args_list
    )
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN


async def test_runtime_backpressure_delivers_accepted_finals_exactly_once() -> None:
    runtime = _Runtime()
    release_delivery = (
        await _block_first_delivery_behind_queued_final_and_active_turn(runtime)
    )
    ingress_token = runtime._asr_runtime._asr_current_ingress_token
    assert ingress_token is not None

    await runtime._handle_audio_ingress_backpressure(ingress_token)
    release_delivery.set()
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime.session.create_response.await_args_list == [
        call("first"),
        call("second"),
    ]
    assert "ASR_INGRESS_BACKPRESSURE" in str(runtime.send_status.await_args_list)


async def test_non_backpressure_abort_still_retires_accepted_finals() -> None:
    runtime = _Runtime()
    release_delivery = (
        await _block_first_delivery_behind_queued_final_and_active_turn(runtime)
    )

    await runtime._abort_independent_asr("microphone_stopped")
    release_delivery.set()
    await runtime._wait_asr_transcript_dispatch_idle()
    await runtime._voice_input_registry.wait_idle()

    runtime.session.create_response.assert_not_awaited()
    assert runtime._asr_runtime.pending_transcript_turn_tokens() == frozenset()


async def _accept_final_behind_blocked_lease_release(
    runtime: _Runtime,
) -> tuple[asyncio.Task[None], asyncio.Event]:
    """Leave turn 1's final accepted while its SmartTurn lease still releases."""

    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    sealed = runtime._asr_runtime._asr_sealed_turn_token
    assert sealed is not None
    release_started = asyncio.Event()
    finish_release = asyncio.Event()

    class _BlockingLease:
        token = sealed.turn

        async def release(self) -> None:
            release_started.set()
            await finish_release.wait()

    runtime._asr_runtime._asr_smart_turn_lease = _BlockingLease()
    final_task = asyncio.create_task(
        runtime._handle_independent_asr_final("first", epoch, "qwen")
    )
    await asyncio.wait_for(release_started.wait(), 1)
    assert sealed.turn in runtime._asr_runtime.pending_transcript_turn_tokens()
    return final_task, finish_release


async def test_backpressure_during_lease_release_keeps_accepted_final() -> None:
    runtime = _Runtime()
    final_task, finish_release = (
        await _accept_final_behind_blocked_lease_release(runtime)
    )

    await runtime._abort_independent_asr("ingress_backpressure")
    finish_release.set()
    await asyncio.wait_for(final_task, 1)
    await runtime._wait_asr_transcript_dispatch_idle()
    await runtime._voice_input_registry.wait_idle()

    # The final was accepted before the abort, so the user still gets an
    # answer even though the lease release outlived the old identity.
    assert runtime.session.create_response.await_args_list == [call("first")]
    assert runtime._asr_runtime.pending_transcript_turn_tokens() == frozenset()


async def test_non_backpressure_abort_during_lease_release_abandons_final() -> None:
    runtime = _Runtime()
    final_task, finish_release = (
        await _accept_final_behind_blocked_lease_release(runtime)
    )
    epoch = runtime._asr_session_epoch

    await runtime._abort_independent_asr("microphone_stopped")
    finish_release.set()
    await asyncio.wait_for(final_task, 1)
    await runtime._wait_asr_transcript_dispatch_idle()
    await runtime._voice_input_registry.wait_idle()

    runtime.session.create_response.assert_not_awaited()
    assert call(f"asr-{epoch}-1") in (
        runtime.session.abandon_external_voice_turn.call_args_list
    )
    assert runtime._asr_runtime.pending_transcript_turn_tokens() == frozenset()
