"""Exercise owned fault recovery with the real runtime and lifecycle."""

import asyncio
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.recovery import FailureSource
from main_logic.asr_client.endpointing.detector_runtime import DetectorFeedResult
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame, VoiceInputAudioPipeline
from main_logic.voice_turn.contracts import SpeechActivityEvent
from main_logic.voice_turn.contracts import AsrSubmitStatus
from tests.unit.asr_runtime._scenarios import _start_runtime_with_callback_candidates

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def _started(monkeypatch, *, candidate_count=4):
    owner, sessions, provider_callbacks, detector = await _start_runtime_with_callback_candidates(
        monkeypatch, candidate_count=candidate_count,
    )
    runtime = owner._asr_runtime
    runtime._asr_current_ingress_token = owner._capture_ingress_token()
    detector.recovery_boundary_ready = True
    observers = {name: AsyncMock(return_value=True if name == "on_prepare_turn" else None)
                 for name in ("on_prepare_turn", "on_partial", "on_final",
                              "on_turn_abandoned", "on_failure", "on_status", "on_lifecycle")}
    runtime._callbacks = replace(runtime._callbacks, **observers)
    for session in sessions:
        session.stream_audio = AsyncMock()
    return owner, runtime, sessions, provider_callbacks, detector, observers


async def _recover(runtime, *, code="ASR_QWEN_READ_DISCONNECTED", source=FailureSource.PROVIDER):
    await runtime._handle_independent_asr_error(
        runtime._asr_session_epoch, "qwen", status_code=code, failure_source=source,
    )
    operation = runtime._asr_recovery
    assert operation is not None
    await asyncio.wait_for(asyncio.shield(operation.task), 2)
    return operation


async def test_final_timeout_retires_transport_and_recovers_once(monkeypatch):
    _, runtime, sessions, _, detector, observers = await _started(monkeypatch)
    try:
        epoch = runtime._asr_session_epoch
        await runtime._handle_independent_asr_activity(SpeechActivityEvent.SPEECH_STARTED, epoch)
        await runtime._handle_independent_asr_endpoint(epoch)
        assert runtime._asr_sealed_turn_token is not None
        await _recover(runtime, code="ASR_PROVIDER_FINAL_TIMEOUT", source=FailureSource.RUNTIME)
        assert runtime._asr_session is sessions[1]
        assert runtime._asr_session_epoch == epoch
        sessions[0].close.assert_awaited_once()
        sessions[1].connect.assert_awaited_once()
        assert runtime._asr_recovery_budget.attempts_used == 1
        detector.reset.assert_awaited()
        observers["on_failure"].assert_not_awaited()
        assert [call.args[0].code for call in observers["on_status"].await_args_list] == [
            "ASR_RECOVERY_STARTED", "ASR_TURN_INCOMPLETE", "ASR_RECOVERY_READY",
        ]
    finally:
        await runtime.close()


async def test_partial_only_failed_turn_is_abandoned_and_never_finalized(monkeypatch):
    _, runtime, sessions, _, _, observers = await _started(monkeypatch)
    try:
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED, runtime._asr_session_epoch,
        )
        prepared = runtime._asr_prepared_turn_token
        assert prepared is not None
        await runtime._send_independent_asr_preview("unfinished", runtime._asr_session_epoch)
        await _recover(runtime)
        observers["on_turn_abandoned"].assert_awaited_once_with(prepared)
        observers["on_final"].assert_not_awaited()
        sessions[1].stream_audio.assert_not_awaited()
        assert "ASR_TURN_INCOMPLETE" in [
            call.args[0].code for call in observers["on_status"].await_args_list
        ]
    finally:
        await runtime.close()


async def test_retired_callback_cannot_finalize_or_fail_replacement(monkeypatch):
    _, runtime, sessions, callbacks, _, observers = await _started(monkeypatch)
    try:
        await _recover(runtime)
        await callbacks[0]["on_input_transcript"]("late old final")
        await callbacks[0]["on_connection_error"]("ASR_QWEN_READ_DISCONNECTED: late")
        assert runtime._asr_session is sessions[1]
        assert runtime._asr_recovery is None
        assert runtime._asr_recovery_budget.attempts_used == 1
        observers["on_final"].assert_not_awaited()
        observers["on_failure"].assert_not_awaited()
    finally:
        await runtime.close()


async def test_successful_handshakes_do_not_replenish_two_attempt_budget(monkeypatch):
    _, runtime, sessions, callbacks, _, observers = await _started(monkeypatch)
    try:
        await _recover(runtime)
        await _recover(runtime)
        assert runtime._asr_session is sessions[2]
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch, "qwen", status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        operation = runtime._asr_recovery
        assert operation is not None
        await asyncio.wait_for(asyncio.gather(operation.task, return_exceptions=True), 2)
        assert runtime._asr_session is None
        assert len(callbacks) == 3
        assert runtime._asr_recovery_budget.attempts_used == 2
        observers["on_failure"].assert_awaited_once()
        assert observers["on_failure"].await_args.args[0].code == "ASR_RECOVERY_EXHAUSTED"
    finally:
        await runtime.close()


async def test_credentials_failure_never_creates_replacement(monkeypatch):
    _, runtime, sessions, callbacks, _, observers = await _started(monkeypatch)
    try:
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch, "qwen", status_code="ASR_CREDENTIALS_REJECTED",
            failure_source=FailureSource.PROVIDER,
        )
        assert runtime._asr_recovery is None
        assert len(callbacks) == 1
        assert runtime._asr_recovery_budget.attempts_used == 0
        sessions[1].connect.assert_not_awaited()
        observers["on_failure"].assert_awaited_once()
    finally:
        await runtime.close()


async def test_mute_cancels_waiting_recovery_without_reopening(monkeypatch):
    _, runtime, sessions, callbacks, detector, observers = await _started(monkeypatch)
    detector.recovery_boundary_ready = False
    try:
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch, "qwen", status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        operation = runtime._asr_recovery
        assert operation is not None
        async with asyncio.timeout(1):
            while not sessions[0].close.await_count:
                await asyncio.sleep(0)
        await runtime.suspend("hard_mute")
        await asyncio.gather(operation.task, return_exceptions=True)
        assert runtime._asr_recovery is None
        assert len(callbacks) == 1
        sessions[1].connect.assert_not_awaited()
        assert "ASR_RECOVERY_READY" not in [
            call.args[0].code for call in observers["on_status"].await_args_list
        ]
    finally:
        await runtime.close()


async def test_accepted_final_drains_before_recovery_retires_its_identity(monkeypatch):
    _, runtime, sessions, callbacks, _, observers = await _started(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def deliver(_event):
        entered.set()
        await release.wait()

    observers["on_final"].side_effect = deliver
    try:
        epoch = runtime._asr_session_epoch
        await runtime._handle_independent_asr_activity(SpeechActivityEvent.SPEECH_STARTED, epoch)
        await callbacks[0]["on_turn_endpointed"]()
        await callbacks[0]["on_input_transcript"]("accepted sentence")
        await asyncio.wait_for(entered.wait(), 1)
        await runtime._handle_independent_asr_error(
            epoch, "qwen", status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        operation = runtime._asr_recovery
        assert operation is not None
        await asyncio.sleep(0)
        sessions[1].connect.assert_not_awaited()
        assert runtime._asr_session_epoch == epoch
        release.set()
        await asyncio.wait_for(operation.task, 2)
        observers["on_final"].assert_awaited_once()
        assert observers["on_final"].await_args.args[0].text == "accepted sentence"
        observers["on_turn_abandoned"].assert_not_awaited()
        assert runtime._asr_session is sessions[1]
    finally:
        release.set()
        await runtime.close()


async def test_fresh_recovery_audio_waits_for_connect_then_preserves_order(monkeypatch):
    owner, runtime, sessions, _, detector, observers = await _started(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def connect():
        entered.set()
        await release.wait()

    sessions[1].connect.side_effect = connect
    try:
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch, "qwen", status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        operation = runtime._asr_recovery
        assert operation is not None
        await asyncio.wait_for(entered.wait(), 1)
        detector._feed_result = DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
        first, second = b"\x01\x00" * 512, b"\x02\x00" * 512
        ingress = owner._capture_ingress_token()
        await runtime.submit(ProcessedVoiceFrame(first, 16000, 1.0, False), ingress_token=ingress)
        detector._feed_result = DetectorFeedResult((), True)
        await runtime.submit(ProcessedVoiceFrame(second, 16000, 1.0, False), ingress_token=ingress)
        sessions[0].stream_audio.assert_not_awaited()
        sessions[1].stream_audio.assert_not_awaited()
        release.set()
        await asyncio.wait_for(operation.task, 2)
        async with asyncio.timeout(1):
            while not sessions[1].stream_audio.await_count:
                await asyncio.sleep(0)
        assert b"".join(call.args[0] for call in sessions[1].stream_audio.await_args_list) == first + second
        observers["on_prepare_turn"].assert_awaited_once()
    finally:
        release.set()
        await runtime.close()


@pytest.mark.parametrize("final_first", [True, False])
async def test_real_endpoint_watchdog_and_final_settle_only_one_outcome(monkeypatch, final_first):
    _, runtime, sessions, callbacks, _, observers = await _started(monkeypatch)
    runtime._asr_lifecycle.provider_policy = replace(
        runtime._asr_lifecycle.provider_policy, provider_final_timeout_ms=10,
    )
    try:
        epoch = runtime._asr_session_epoch
        await runtime._handle_independent_asr_activity(SpeechActivityEvent.SPEECH_STARTED, epoch)
        prepared = runtime._asr_prepared_turn_token
        await callbacks[0]["on_turn_endpointed"]()
        watchdog = runtime._asr_final_watchdog_task
        assert watchdog is not None
        if final_first:
            await callbacks[0]["on_input_transcript"]("completed")
            await runtime._asr_transcript_dispatcher.wait_idle()
            await asyncio.gather(watchdog, return_exceptions=True)
            assert runtime._asr_session is sessions[0]
            observers["on_final"].assert_awaited_once()
            observers["on_turn_abandoned"].assert_not_awaited()
            assert runtime._asr_recovery_budget.attempts_used == 0
        else:
            await asyncio.wait_for(watchdog, 1)
            operation = runtime._asr_recovery
            assert operation is not None
            # The old provider result arrives after the timeout acquired ownership.
            await callbacks[0]["on_input_transcript"]("too late")
            await asyncio.wait_for(operation.task, 2)
            observers["on_final"].assert_not_awaited()
            observers["on_turn_abandoned"].assert_awaited_once_with(prepared)
            assert runtime._asr_session is sessions[1]
            assert runtime._asr_recovery_budget.attempts_used == 1
    finally:
        await runtime.close()


async def test_total_recovery_deadline_includes_old_transport_close(monkeypatch):
    _, runtime, sessions, callbacks, _, observers = await _started(monkeypatch)
    close_entered, close_release = asyncio.Event(), asyncio.Event()

    async def close():
        close_entered.set()
        await close_release.wait()

    sessions[0].close.side_effect = close
    runtime._asr_recovery_budget.total_seconds = 0.03
    try:
        started = time.monotonic()
        await _recover(runtime)
        assert time.monotonic() - started < 0.5
        assert close_entered.is_set()
        assert len(callbacks) == 1
        assert runtime._asr_recovery_budget.attempts_used == 0
        observers["on_failure"].assert_awaited_once()
        assert observers["on_failure"].await_args.args[0].code == "ASR_RECOVERY_EXHAUSTED"
    finally:
        close_release.set()
        await runtime.close()


async def test_failed_replacement_connects_have_only_two_total_attempts(monkeypatch):
    _, runtime, sessions, callbacks, _, observers = await _started(monkeypatch)
    for candidate in sessions[1:]:
        candidate.last_failure_code = "ASR_QWEN_CONNECTION_FAILED"
        candidate.connect.side_effect = RuntimeError("connect failed")
    try:
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch, "qwen", status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        operation = runtime._asr_recovery
        assert operation is not None
        await asyncio.wait_for(asyncio.gather(operation.task, return_exceptions=True), 2)
        assert len(callbacks) == 3
        sessions[1].connect.assert_awaited_once()
        sessions[2].connect.assert_awaited_once()
        sessions[3].connect.assert_not_awaited()
        assert runtime._asr_recovery_budget.attempts_used == 2
        observers["on_failure"].assert_awaited_once()
    finally:
        await runtime.close()


async def test_recovery_buffer_overflow_fails_visibly_without_sending_truncated_sentence(monkeypatch):
    owner, runtime, sessions, _, detector, observers = await _started(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def connect():
        entered.set()
        await release.wait()

    sessions[1].connect.side_effect = connect
    try:
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch, "qwen", status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        operation = runtime._asr_recovery
        assert operation is not None
        await asyncio.wait_for(entered.wait(), 1)
        detector._feed_result = DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
        ingress = owner._capture_ingress_token()
        head = b"\x01\x00" * 16000
        await runtime.submit(ProcessedVoiceFrame(head, 16000, 1.0, False), ingress_token=ingress)
        detector._feed_result = DetectorFeedResult((), True)
        for _ in range(8):
            await runtime.submit(ProcessedVoiceFrame(b"\x02\x00" * 16000, 16000, 1.0, False), ingress_token=ingress)
        assert observers["on_failure"].await_count == 1
        sessions[1].stream_audio.assert_not_awaited()
        assert runtime._asr_session is None
    finally:
        release.set()
        await runtime.close()


async def test_old_sentence_tail_is_suppressed_until_existing_pause_boundary(monkeypatch):
    owner, runtime, sessions, callbacks, detector, observers = await _started(monkeypatch)
    detector.recovery_boundary_ready = False
    try:
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch, "qwen", status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        operation = runtime._asr_recovery
        assert operation is not None
        ingress = owner._capture_ingress_token()
        tail = ProcessedVoiceFrame(b"\x03\x00" * 512, 16000, 1.0, False)
        for _ in range(5):
            await runtime.submit(tail, ingress_token=ingress)
        assert len(callbacks) == 1
        sessions[0].stream_audio.assert_not_awaited()
        observers["on_prepare_turn"].assert_not_awaited()
        detector.recovery_boundary_ready = True
        await runtime.submit(ProcessedVoiceFrame(b"\x00\x00" * 512, 16000, 0.0, False), ingress_token=ingress)
        await asyncio.wait_for(operation.task, 2)
        detector._feed_result = DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
        fresh = b"\x04\x00" * 512
        await runtime.submit(ProcessedVoiceFrame(fresh, 16000, 1.0, False), ingress_token=ingress)
        async with asyncio.timeout(1):
            while not sessions[1].stream_audio.await_count:
                await asyncio.sleep(0)
        assert b"".join(call.args[0] for call in sessions[1].stream_audio.await_args_list) == fresh
    finally:
        await runtime.close()


@pytest.mark.parametrize("resource_optimization", [False, True])
async def test_long_normal_speech_and_pause_never_start_recovery(monkeypatch, resource_optimization):
    owner, runtime, sessions, callbacks, detector, observers = await _started(monkeypatch)
    runtime._voice_input_resource_optimization_enabled = resource_optimization
    try:
        ingress = owner._capture_ingress_token()
        detector._feed_result = DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
        await runtime.submit(ProcessedVoiceFrame(b"\x01\x00" * 512, 16000, 1.0, False), ingress_token=ingress)
        detector._feed_result = DetectorFeedResult((), True)
        for _ in range(40):
            await runtime.submit(ProcessedVoiceFrame(b"\x02\x00" * 16000, 16000, 1.0, False), ingress_token=ingress)
        detector._feed_result = DetectorFeedResult((SpeechActivityEvent.CANDIDATE_PAUSE,), True)
        await runtime.submit(ProcessedVoiceFrame(b"\x00\x00" * 512, 16000, 0.0, False), ingress_token=ingress)
        assert runtime._asr_recovery is None
        assert runtime._asr_recovery_budget.attempts_used == 0
        assert len(callbacks) == 1
        observers["on_failure"].assert_not_awaited()
    finally:
        await runtime.close()


async def test_first_fresh_frame_survives_completed_recovery_before_waiter_resumes(monkeypatch):
    owner, runtime, sessions, _, detector, _ = await _started(monkeypatch)
    waiter_entered, resume_waiter = asyncio.Event(), asyncio.Event()

    class DelayedResumeEvent(asyncio.Event):
        async def wait(self):
            waiter_entered.set()
            await super().wait()
            await resume_waiter.wait()
            return True

    frame_task = None
    try:
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch, "qwen", status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        operation = runtime._asr_recovery
        assert operation is not None
        operation.input_ready = DelayedResumeEvent()
        fresh = b"\x07\x00" * 512
        detector._feed_result = DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
        # Enter the fresh-frame wait before the recovery task reaches reset.
        frame_task = asyncio.create_task(runtime._observe_recovery_input(
            operation, ProcessedVoiceFrame(fresh, 16000, 1.0, False),
            owner._capture_ingress_token(),
        ))
        await asyncio.wait_for(waiter_entered.wait(), 1)
        await asyncio.wait_for(operation.task, 2)
        assert runtime._asr_recovery is None
        resume_waiter.set()
        result = await asyncio.wait_for(frame_task, 1)
        assert result.status is AsrSubmitStatus.ACCEPTED
        async with asyncio.timeout(1):
            while not sessions[1].stream_audio.await_count:
                await asyncio.sleep(0)
        assert b"".join(call.args[0] for call in sessions[1].stream_audio.await_args_list) == fresh
    finally:
        resume_waiter.set()
        if frame_task is not None:
            await asyncio.gather(frame_task, return_exceptions=True)
        await runtime.close()


async def test_recovery_deadline_preserves_accepted_delivery_then_blocks_when_idle(monkeypatch):
    owner, runtime, sessions, callbacks, _, observers = await _started(monkeypatch)
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def deliver(_event):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    observers["on_final"].side_effect = deliver
    runtime._asr_recovery_budget.total_seconds = 0.03
    try:
        epoch = runtime._asr_session_epoch
        ingress = owner._capture_ingress_token()
        await runtime._handle_independent_asr_activity(SpeechActivityEvent.SPEECH_STARTED, epoch)
        await callbacks[0]["on_turn_endpointed"]()
        await callbacks[0]["on_input_transcript"]("accepted before failure")
        await asyncio.wait_for(entered.wait(), 1)
        await runtime._handle_independent_asr_error(
            epoch, "qwen", status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        operation = runtime._asr_recovery
        assert operation is not None
        await asyncio.wait_for(operation.task, 1)
        assert operation.failed
        assert not cancelled.is_set()
        observers["on_failure"].assert_not_awaited()
        sessions[1].connect.assert_not_awaited()
        assert "ASR_RECOVERY_FAILED" in [
            call.args[0].code for call in observers["on_status"].await_args_list
        ]
        prepare_count = observers["on_prepare_turn"].await_count
        outcome = await runtime.submit(
            ProcessedVoiceFrame(b"\x01\x00" * 512, 16000, 1.0, False), ingress_token=ingress,
        )
        # FAILED is already visible; suppress late PCM without asking Core to
        # revoke the route while its accepted final still owns delivery.
        assert outcome.status is AsrSubmitStatus.ACCEPTED
        assert observers["on_prepare_turn"].await_count == prepare_count
        sessions[1].stream_audio.assert_not_awaited()
        release.set()
        async with asyncio.timeout(1):
            while not observers["on_failure"].await_count:
                await asyncio.sleep(0)
        observers["on_final"].assert_awaited_once()
        assert not cancelled.is_set()
        assert runtime._asr_session is None
        assert observers["on_failure"].await_args.args[0].code == "ASR_RECOVERY_EXHAUSTED"
    finally:
        release.set()
        await runtime.close()


@pytest.mark.parametrize("sample_rate", [16000, 48000])
async def test_smartturn_recovery_releases_old_lease_and_accepts_normalized_fresh_audio(monkeypatch, sample_rate):
    import tests.unit.asr_runtime._scenarios as scenarios
    from tests.support.asr_fakes import _selection
    from tests.support.core_asr_harness import _QueuedSmartTurnDetector

    monkeypatch.setattr(scenarios, "_selection", lambda *_args: _selection("qwen", "manual"))
    owner, runtime, sessions, _, _, observers = await _started(monkeypatch)
    detector = _QueuedSmartTurnDetector()
    detector.recovery_boundary_ready = True
    runtime._asr_detector = detector
    pipeline = VoiceInputAudioPipeline(nr_enabled=False)
    try:
        epoch = runtime._asr_session_epoch
        await runtime._handle_independent_asr_activity(SpeechActivityEvent.SPEECH_STARTED, epoch)
        old_lease = runtime._asr_smart_turn_lease
        assert old_lease is not None
        await _recover(runtime)
        assert old_lease.released
        assert runtime._asr_smart_turn_lease is None
        await runtime._handle_independent_asr_activity(SpeechActivityEvent.SPEECH_STARTED, epoch)
        assert runtime._asr_smart_turn_lease is not None
        raw = b"\x01\x00" * (sample_rate // 10)
        frame = await pipeline.process(raw, sample_rate_hz=sample_rate)
        assert frame.sample_rate_hz == 16000
        assert frame.pcm16
        result = await runtime.submit(frame, ingress_token=owner._capture_ingress_token())
        assert result.status is AsrSubmitStatus.ACCEPTED
        await runtime._asr_audio_dispatcher.wait_idle()
        assert b"".join(call.args[0] for call in sessions[1].stream_audio.await_args_list) == frame.pcm16
        assert all(call.kwargs["sample_rate_hz"] == 16000 for call in sessions[1].stream_audio.await_args_list)
        observers["on_failure"].assert_not_awaited()
    finally:
        await pipeline.close()
        await runtime.close()
