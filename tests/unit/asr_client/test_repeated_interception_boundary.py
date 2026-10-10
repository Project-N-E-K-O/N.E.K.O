"""Real submit/final/FIFO boundaries; only acoustic/provider IO is controlled."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.endpointing.throttle_policy import ThrottleAction
from main_logic.asr_client.lifecycle import VoiceLifecycleState
from main_logic.asr_client.runtime import AsrSubmitStatus, IndependentAsrRuntime
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_turn.contracts import SpeechActivityEvent
from tests.unit.asr_client.test_candidate_rejection_runtime import (
    _callbacks,
    _install_active_candidate,
    _RejectionDetector,
)


class _BoundaryDetector(_RejectionDetector):
    def __init__(self):
        super().__init__()
        self.endpointing_ready = lambda _token: True
        self.release_deferred_turn = AsyncMock()
        self.feed = AsyncMock(return_value=SimpleNamespace(
            endpointing_available=True,
            throttle_available=True,
            throttle_action=ThrottleAction.PROCESS_PCM,
            events=(SpeechActivityEvent.SPEECH_STARTED,),
        ))

    async def prepare_endpointing(self, token):
        return SimpleNamespace(token=token, release=AsyncMock())


async def _scenario():
    callbacks = _callbacks()
    runtime = IndependentAsrRuntime(callbacks)
    detector = _BoundaryDetector()
    session, lifecycle, token = _install_active_candidate(runtime, detector)
    order = []
    session.stream_audio = AsyncMock(side_effect=lambda pcm, **_kw: order.append(("pcm", pcm)))
    session.signal_user_activity_end = AsyncMock(side_effect=lambda: order.append(("seal", None)))
    prefix, successor = b"\x01\x00" * 1600, b"\x02\x00" * 1600
    submitted = await runtime.submit(ProcessedVoiceFrame(prefix, 16000, 1.0, True), ingress_token=token.ingress)
    assert submitted.status is AsrSubmitStatus.ACCEPTED
    await runtime.discontinue_input(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
    submitted = await runtime.submit(ProcessedVoiceFrame(successor, 16000, 1.0, True), ingress_token=token.ingress)
    assert submitted.status is AsrSubmitStatus.ACCEPTED
    assert lifecycle.has_pending_turn and lifecycle.pending_turn_bytes == len(successor)
    assert order == [("pcm", prefix), ("seal", None)]
    return runtime, callbacks, detector, session, lifecycle, token, order, prefix, successor


async def _cleanup(runtime, *tasks):
    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
    await asyncio.gather(*(task for task in tasks if task is not None), return_exceptions=True)
    watchdog = runtime._asr_final_watchdog_task
    if watchdog is not None:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
    await runtime._asr_audio_dispatcher.close()
    runtime._asr_transcript_dispatcher.invalidate_all()
    lease = runtime._asr_smart_turn_lease
    if lease is not None:
        await lease.release()


async def _assert_waiting(task):
    # Shield preserves the real boundary task and its original absolute budget.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=0.02)
    assert not task.done()


@pytest.mark.asyncio
async def test_second_gap_waits_for_old_final_and_prepared_successor_without_splicing_pcm():
    runtime, callbacks, detector, session, lifecycle, token, order, prefix, successor = await _scenario()
    entered, release = asyncio.Event(), asyncio.Event()

    async def prepare(_token):
        entered.set()
        await release.wait()
        return True

    callbacks.on_prepare_turn.side_effect = prepare
    boundary = asyncio.create_task(runtime.discontinue_input(
        ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1,
    ))
    final = None
    try:
        await _assert_waiting(boundary)
        final = asyncio.create_task(runtime._handle_independent_asr_final(
            "prefix final", runtime._asr_session_epoch, "glm",
        ))
        await asyncio.wait_for(entered.wait(), 1)
        # ACTIVE is reached before its preparation await completes. It does not
        # authorize a seal until the successor owns the actual audio writer.
        assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
        await _assert_waiting(boundary)
        assert order == [("pcm", prefix), ("seal", None)]
        release.set()
        await final
        await boundary
        assert order == [("pcm", prefix), ("seal", None), ("pcm", successor), ("seal", None)]
        successor_token = runtime._capture_turn_token(lifecycle)
        assert successor_token != token
        assert successor_token.ingress == token.ingress
        assert lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
        await runtime._handle_independent_asr_final("successor final", runtime._asr_session_epoch, "glm")
        await runtime.wait_input_settled(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        envelopes = [item.args[0] for item in callbacks.on_final.await_args_list]
        assert [(item.text, item.turn_token) for item in envelopes] == [
            ("prefix final", token), ("successor final", successor_token),
        ]
        callbacks.on_turn_abandoned.assert_not_awaited()
        assert detector.reset.await_count == 2
        assert session.signal_user_activity_end.await_count == 2
    finally:
        release.set()
        await _cleanup(runtime, boundary, final)


@pytest.mark.asyncio
async def test_successor_preparation_does_not_refresh_boundary_absolute_deadline():
    runtime, callbacks, _detector, session, lifecycle, token, _order, _prefix, _successor = await _scenario()
    entered, release = asyncio.Event(), asyncio.Event()

    async def prepare(_token):
        entered.set()
        await release.wait()
        return True

    callbacks.on_prepare_turn.side_effect = prepare
    deadline = asyncio.get_running_loop().time() + 0.08
    boundary = asyncio.create_task(runtime.discontinue_input(ingress_token=token.ingress, deadline=deadline))
    final = asyncio.create_task(runtime._handle_independent_asr_final("prefix final", runtime._asr_session_epoch, "glm"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(boundary), 0.5)
        assert boundary.done()  # The boundary's deadline, not the test timeout.
        assert session.signal_user_activity_end.await_count == 1
        release.set()
        await final
        await runtime.wait_transcript_idle()
        assert callbacks.on_final.await_args.args[0].text == "prefix final"
        assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
        assert session.signal_user_activity_end.await_count == 1
    finally:
        release.set()
        await _cleanup(runtime, boundary, final)


@pytest.mark.asyncio
async def test_cancelled_second_gap_preserves_pending_audio_and_prior_final():
    runtime, callbacks, _detector, session, lifecycle, token, order, prefix, successor = await _scenario()
    boundary = asyncio.create_task(runtime.discontinue_input(
        ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1,
    ))
    try:
        await _assert_waiting(boundary)
        boundary.cancel()
        with pytest.raises(asyncio.CancelledError):
            await boundary
        assert lifecycle.has_pending_turn and lifecycle.pending_turn_bytes == len(successor)
        assert session.signal_user_activity_end.await_count == 1
        await runtime._handle_independent_asr_final("prefix final", runtime._asr_session_epoch, "glm")
        await runtime.wait_transcript_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        assert callbacks.on_final.await_args.args[0].text == "prefix final"
        assert order == [("pcm", prefix), ("seal", None), ("pcm", successor)]
        assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
        await runtime.discontinue_input(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        assert session.signal_user_activity_end.await_count == 2
    finally:
        await _cleanup(runtime, boundary)


@pytest.mark.asyncio
async def test_waiting_boundary_and_late_final_cannot_seal_replacement_session():
    runtime, callbacks, _detector, old_session, _lifecycle, token, _order, _prefix, _successor = await _scenario()
    old_epoch = runtime._asr_session_epoch
    boundary = asyncio.create_task(runtime.discontinue_input(
        ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1,
    ))
    try:
        await _assert_waiting(boundary)
        # Use the runtime's real epoch invalidation and dispatcher retirement;
        # the replacement fixture opens its own lifecycle/turn reservation.
        runtime._advance_asr_session_epoch()
        runtime._asr_audio_dispatcher.abort(token)
        runtime._asr_transcript_dispatcher.invalidate_all()
        runtime._asr_sealed_turn_token = None
        runtime._asr_pending_activation_turn = None
        replacement, new_lifecycle, new_token = _install_active_candidate(runtime, _BoundaryDetector())
        replacement.stream_audio = AsyncMock()
        replacement.signal_user_activity_end = AsyncMock()
        with pytest.raises(RuntimeError, match="BOUNDARY_STALE"):
            await boundary
        await runtime._handle_independent_asr_final("late old final", old_epoch, "glm")
        await runtime.wait_transcript_idle()
        replacement.signal_user_activity_end.assert_not_awaited()
        replacement.stream_audio.assert_not_awaited()
        assert new_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
        assert runtime._asr_audio_dispatcher.active_turn == new_token
        assert old_session.signal_user_activity_end.await_count == 1
        callbacks.on_final.assert_not_awaited()
    finally:
        await _cleanup(runtime, boundary)


@pytest.mark.asyncio
async def test_second_gap_entering_during_active_successor_preparation_waits_for_writer():
    runtime, callbacks, _detector, session, lifecycle, token, order, prefix, successor = await _scenario()
    entered, release = asyncio.Event(), asyncio.Event()

    async def prepare(_token):
        entered.set()
        await release.wait()
        return True

    callbacks.on_prepare_turn.side_effect = prepare
    final = asyncio.create_task(runtime._handle_independent_asr_final(
        "prefix final", runtime._asr_session_epoch, "glm",
    ))
    boundary = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
        assert runtime._asr_pending_activation_turn is not None
        # This boundary starts after the old final has changed the lifecycle;
        # checking only the DRAINING entry state misses this real interleaving.
        boundary = asyncio.create_task(runtime.discontinue_input(
            ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1,
        ))
        await _assert_waiting(boundary)
        assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
        assert order == [("pcm", prefix), ("seal", None)]
        release.set()
        await final
        await boundary
        await runtime.wait_transcript_idle()
        assert order == [("pcm", prefix), ("seal", None), ("pcm", successor), ("seal", None)]
        assert lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
        assert callbacks.on_final.await_args.args[0].text == "prefix final"
        assert callbacks.on_final.await_count == 1
        callbacks.on_turn_abandoned.assert_not_awaited()
        assert session.signal_user_activity_end.await_count == 2
    finally:
        release.set()
        await _cleanup(runtime, boundary, final)
