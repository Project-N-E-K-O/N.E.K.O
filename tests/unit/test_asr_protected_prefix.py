"""Lossless cold-ASR admission uses the existing eight-second byte budget."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.endpointing.detector import (
    DetectorSubmitResult,
    DetectorSubmitStatus,
)
from main_logic.asr_client.lifecycle import (
    AudioDisposition, VoiceInputLifecycleController, VoiceLifecycleEvent, VoiceRouteMode,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_turn.contracts import (
    AsrDeliveryStage, AsrSubmitResult, AsrSubmitStatus, PreserveUnsentPrefix,
)
from tests.unit.test_core_independent_asr import (
    _QueuedSmartTurnDetector,
    _Runtime,
    _ReadyDetector,
    _selection,
    DetectorFeedResult,
    SpeechActivityEvent,
)


def _lifecycle():
    lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "provider"), shadow_mode=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    return lifecycle


@pytest.mark.parametrize("status", list(AsrSubmitStatus))
def test_only_accepted_submission_claims_local_delivery_stage(status):
    result = AsrSubmitResult(status)
    assert result.delivery_stage is (
        AsrDeliveryStage.LOCAL_ACCEPTED if status is AsrSubmitStatus.ACCEPTED else None
    )


def test_protected_capacity_never_evicts_prefix_or_partially_accepts_frame():
    lifecycle = _lifecycle()
    lifecycle.protect_unsent_prefix()
    first = b"\x01\x00" * 160
    silence = bytes(256000 - len(first))
    assert lifecycle.accept_audio(first, sample_rate_hz=16000).disposition is AudioDisposition.BUFFER
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    lifecycle.accept_audio(silence, sample_rate_hz=16000)
    assert not lifecycle.has_prefix_capacity(2)
    rejected = lifecycle.accept_audio(b"\x02\x00", sample_rate_hz=16000)
    assert rejected.backpressure
    assert lifecycle.pending_connect_bytes == 256000
    assert lifecycle.metrics.buffer_overflow_count == 0
    lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    assert lifecycle.peek_active_start_audio() == first + silence
    assert not lifecycle.has_prefix_capacity(2)
    assert lifecycle.drain_active_start_audio() == first + silence
    assert lifecycle.has_prefix_capacity(2)


def test_unconfirmed_window_keeps_existing_rolling_semantics():
    lifecycle = _lifecycle()
    lifecycle.accept_audio(b"\x01\x00" * 16000, sample_rate_hz=16000)
    assert lifecycle.pre_roll_bytes == 22400
    assert lifecycle.metrics.buffer_overflow_count == 1


def test_prefix_promotion_overflow_preserves_uncommitted_state():
    lifecycle = _lifecycle()
    pre_roll = b"\x01\x00" * 160
    pending = bytes(lifecycle.prefix_capacity_bytes)
    # Inject the capacity failure at this ownership boundary without changing
    # production budgets or mocking the promotion implementation.
    lifecycle._pre_roll.append(pre_roll)
    lifecycle._pending_connect.append(pending)

    with pytest.raises(RuntimeError, match="^ASR_PROTECTED_PREFIX_OVERFLOW$"):
        lifecycle.protect_unsent_prefix()

    assert not lifecycle.prefix_protected
    assert lifecycle._pre_roll.peek() == pre_roll
    assert lifecycle._pending_connect.peek() == pending
    assert lifecycle.metrics.buffer_overflow_count == 0


def _cold_runtime():
    runtime = _Runtime()
    lifecycle = _lifecycle()
    runtime._asr_lifecycle = lifecycle
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "qwen"
    runtime._asr_transport_selection = _selection("qwen")
    entered, ready = asyncio.Event(), asyncio.Event()
    sessions = []

    def factory(_selection):
        session = SimpleNamespace(is_ready=False)
        async def connect():
            entered.set()
            await ready.wait()
            session.is_ready = True
        session.connect = connect
        session.close = AsyncMock()
        session.stream_audio = AsyncMock()
        session.signal_user_activity_end = AsyncMock()
        sessions.append(session)
        return session

    runtime._asr_session_factory = factory
    detector = _ReadyDetector()
    calls = 0
    async def feed(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,) if calls == 1 else (), True)
    detector.feed = AsyncMock(side_effect=feed)
    runtime._asr_detector = detector
    token = runtime._capture_ingress_token()
    prefix = PreserveUnsentPrefix(token, "first", 0)
    return runtime, lifecycle, detector, token, prefix, entered, ready, sessions


async def _close(runtime):
    await runtime._asr_runtime.abort("test_complete")
    await runtime._asr_audio_dispatcher.close()
    await runtime._asr_detector_dispatcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [True, False])
async def test_full_prefix_waits_before_detector_then_flushes_once(protected):
    runtime, lifecycle, detector, token, prefix, entered, ready, sessions = _cold_runtime()
    frames = [index.to_bytes(2, "little") * 1600 for index in range(1, 81)]
    async def submit(pcm):
        return await runtime._asr_runtime.submit(
            ProcessedVoiceFrame(pcm, 16000, 0.9, True), ingress_token=token,
            preserve_prefix=prefix if protected else None,
        )
    waiting = None
    try:
        for frame in frames:
            assert (await submit(frame)).status is AsrSubmitStatus.ACCEPTED
        await asyncio.wait_for(entered.wait(), 1)
        assert lifecycle.pending_connect_bytes == 256000
        last = b"\x51\x00" * 1600
        waiting = asyncio.create_task(submit(last))
        await asyncio.sleep(0)
        assert not waiting.done()
        assert detector.feed.await_count == 80
        ready.set()
        assert (await asyncio.wait_for(waiting, 2)).status is AsrSubmitStatus.ACCEPTED
        await runtime._asr_audio_dispatcher.wait_idle()
        assert len(sessions) == 1
        received = b"".join(call.args[0] for call in sessions[0].stream_audio.await_args_list)
        assert received == b"".join(frames) + last
        assert detector.feed.await_count == 81
        assert lifecycle.metrics.buffer_overflow_count == 0
    finally:
        if waiting and not waiting.done():
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        await _close(runtime)


@pytest.mark.asyncio
async def test_revoke_full_prefix_wakes_waiter_and_cannot_send_old_audio():
    runtime, lifecycle, detector, token, prefix, entered, ready, sessions = _cold_runtime()
    try:
        for _ in range(80):
            result = await runtime._asr_runtime.submit(
                ProcessedVoiceFrame(bytes(3200), 16000, .9, True),
                ingress_token=token, preserve_prefix=prefix,
            )
            assert result.status is AsrSubmitStatus.ACCEPTED
        await asyncio.wait_for(entered.wait(), 1)
        waiting = asyncio.create_task(runtime._asr_runtime.submit(
            ProcessedVoiceFrame(bytes(3200), 16000, .9, True),
            ingress_token=token, preserve_prefix=prefix,
        ))
        await asyncio.sleep(0)
        assert runtime._asr_runtime.invalidate_protected_prefix(prefix)
        result = await asyncio.wait_for(waiting, 1)
        assert result.status is not AsrSubmitStatus.ACCEPTED
        ready.set()
        assert lifecycle.pending_connect_bytes == 0
        assert all(not session.stream_audio.await_count for session in sessions)
    finally:
        await _close(runtime)


@pytest.mark.asyncio
async def test_oversized_protected_frame_fails_without_detector_or_partial_acceptance():
    runtime, lifecycle, detector, token, prefix, _, _, sessions = _cold_runtime()
    try:
        result = await runtime._asr_runtime.submit(
            ProcessedVoiceFrame(bytes(256002), 16000, .9, True),
            ingress_token=token, preserve_prefix=prefix,
        )
        assert result.status is AsrSubmitStatus.UNAVAILABLE
        assert detector.feed.await_count == 0
        assert lifecycle.pending_connect_bytes == 0
        assert sessions == []
    finally:
        await _close(runtime)


@pytest.mark.asyncio
async def test_protected_smart_turn_backpressure_is_not_acknowledged():
    runtime, _, _, _, _, _, ready, _ = _cold_runtime()
    policy = resolve_provider_policy("glm", "manual")
    lifecycle = VoiceInputLifecycleController(provider_policy=policy, shadow_mode=False)
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _QueuedSmartTurnDetector()
    detector.wait_audio_capacity = AsyncMock(return_value=True)
    detector.submit_audio = AsyncMock(
        return_value=DetectorSubmitResult(
            status=DetectorSubmitStatus.BACKPRESSURE,
            throttle_available=False,
            endpointing_available=True,
            identity=None,
        )
    )
    runtime._asr_provider = "glm"
    runtime._asr_transport_selection = _selection("glm", "manual")
    runtime._asr_lifecycle = lifecycle
    runtime._asr_detector = detector
    token = runtime._capture_ingress_token()
    prefix = PreserveUnsentPrefix(token, "activation", 0)
    try:
        result = await runtime._asr_runtime.submit(
            ProcessedVoiceFrame(bytes(3200), 16000, 0.9, True),
            ingress_token=token,
            preserve_prefix=prefix,
        )
        assert result.status is AsrSubmitStatus.UNAVAILABLE
        detector.wait_audio_capacity.assert_awaited_once()
        detector.submit_audio.assert_awaited_once()
        assert runtime._asr_lifecycle is None
        assert getattr(runtime._asr_runtime, "_asr_protected_prefix", None) is None
    finally:
        ready.set()
        await _close(runtime)


@pytest.mark.asyncio
async def test_prefix_promotion_overflow_returns_failure_and_retires_input():
    runtime, lifecycle, detector, token, prefix, _, ready, sessions = _cold_runtime()
    lifecycle._pre_roll.append(b"\x01\x00" * 160)
    lifecycle._pending_connect.append(bytes(lifecycle.prefix_capacity_bytes))
    try:
        result = await runtime._asr_runtime.submit(
            ProcessedVoiceFrame(bytes(320), 16000, .9, True),
            ingress_token=token, preserve_prefix=prefix,
        )
        assert result.status is AsrSubmitStatus.UNAVAILABLE
        assert getattr(runtime._asr_runtime, "_asr_protected_prefix", None) is None
        assert runtime._asr_lifecycle is None
        assert not lifecycle.prefix_protected
        assert lifecycle.pending_connect_bytes == lifecycle.pre_roll_bytes == 0
        assert detector.feed.await_count == 0
        ready.set()
        await asyncio.sleep(0)
        assert sessions == []
    finally:
        await _close(runtime)


@pytest.mark.asyncio
async def test_cancel_capacity_wait_does_not_repeat_detector_or_own_shared_connect():
    runtime, lifecycle, detector, token, prefix, entered, ready, sessions = _cold_runtime()
    try:
        for _ in range(80):
            await runtime._asr_runtime.submit(
                ProcessedVoiceFrame(bytes(3200), 16000, .9, True),
                ingress_token=token, preserve_prefix=prefix,
            )
        await entered.wait()
        waiting = asyncio.create_task(runtime._asr_runtime.submit(
            ProcessedVoiceFrame(bytes(3200), 16000, .9, True),
            ingress_token=token, preserve_prefix=prefix,
        ))
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert detector.feed.await_count == 80
        assert lifecycle.pending_connect_bytes == 256000
        assert not runtime._asr_transport_task.done()
        runtime._asr_runtime.invalidate_protected_prefix(prefix)
        ready.set()
        assert all(not session.stream_audio.await_count for session in sessions)
    finally:
        await _close(runtime)


@pytest.mark.asyncio
async def test_dispatcher_rejection_keeps_prefix_until_explicit_failure(monkeypatch):
    runtime, lifecycle, _, token, _, _, _, _ = _cold_runtime()
    try:
        lifecycle.protect_unsent_prefix()
        lifecycle.accept_audio(b"\x01\x00" * 160, sample_rate_hz=16000)
        lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
        lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
        runtime._asr_session = SimpleNamespace(is_ready=True, close=AsyncMock())
        turn = runtime._asr_runtime._capture_turn_token(lifecycle)
        await runtime._asr_detector.prepare_endpointing(turn)
        monkeypatch.setattr(runtime._asr_audio_dispatcher, "activate", lambda *args, **kwargs: False)
        assert not runtime._asr_runtime._activate_asr_audio_dispatcher(lifecycle, turn)
        assert lifecycle.peek_active_start_audio() == b"\x01\x00" * 160
        assert lifecycle.prefix_protected
    finally:
        await _close(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize("legitimate", [True, False])
async def test_detector_await_only_accepts_owned_cold_adoption(legitimate):
    runtime, _, detector, token, prefix, entered, ready, sessions = _cold_runtime()
    first, second = b"\x01\x00" * 1600, b"\x02\x00" * 1600
    waiting = None
    try:
        await runtime._asr_runtime.submit(
            ProcessedVoiceFrame(first, 16000, .9, True),
            ingress_token=token, preserve_prefix=prefix,
        )
        await entered.wait()
        feeding, release = asyncio.Event(), asyncio.Event()
        async def feed(*args, **kwargs):
            feeding.set()
            await release.wait()
            return DetectorFeedResult((), True)
        detector.feed = AsyncMock(side_effect=feed)
        waiting = asyncio.create_task(runtime._asr_runtime.submit(
            ProcessedVoiceFrame(second, 16000, .9, True),
            ingress_token=token, preserve_prefix=prefix,
        ))
        await feeding.wait()
        if legitimate:
            operation_task = runtime._asr_transport_task
            ready.set()
            await operation_task
        else:
            runtime._asr_session = SimpleNamespace(is_ready=True, close=AsyncMock())
        release.set()
        result = await waiting
        if legitimate:
            assert result.status is AsrSubmitStatus.ACCEPTED
            await runtime._asr_audio_dispatcher.wait_idle()
            received = b"".join(call.args[0] for call in sessions[0].stream_audio.await_args_list)
            assert received == first + second
        else:
            assert result.status is AsrSubmitStatus.STALE
    finally:
        if waiting and not waiting.done():
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        await _close(runtime)


@pytest.mark.asyncio
async def test_capacity_deadline_fails_batch_and_wakes_without_later_audio():
    runtime, lifecycle, _, token, prefix, entered, ready, sessions = _cold_runtime()
    try:
        for _ in range(80):
            await runtime._asr_runtime.submit(
                ProcessedVoiceFrame(bytes(3200), 16000, .9, True),
                ingress_token=token, preserve_prefix=prefix,
            )
        await entered.wait()
        runtime._asr_runtime._asr_connect_operation.deadline = time.monotonic() + .01
        result = await asyncio.wait_for(runtime._asr_runtime.submit(
            ProcessedVoiceFrame(bytes(3200), 16000, .9, True),
            ingress_token=token, preserve_prefix=prefix,
        ), 1)
        assert result.status is AsrSubmitStatus.UNAVAILABLE
        assert lifecycle.pending_connect_bytes == 0
        ready.set()
        assert all(not session.stream_audio.await_count for session in sessions)
    finally:
        await _close(runtime)


@pytest.mark.asyncio
async def test_same_batch_live_frames_do_not_reprotect_unconfirmed_idle_silence():
    runtime, lifecycle, _, token, prefix, entered, ready, _ = _cold_runtime()
    try:
        frame = ProcessedVoiceFrame(bytes(3200), 16000, .9, True)
        await runtime._asr_runtime.submit(frame, ingress_token=token, preserve_prefix=prefix)
        await entered.wait()
        task = runtime._asr_transport_task
        ready.set()
        await task
        await runtime._asr_audio_dispatcher.wait_idle()
        assert not lifecycle.prefix_protected
        lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
        lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
        for _ in range(10):
            await runtime._asr_runtime.submit(frame, ingress_token=token, preserve_prefix=prefix)
        assert not lifecycle.prefix_protected
        assert lifecycle.pre_roll_bytes == 22400
        assert lifecycle.pending_connect_bytes == 0
    finally:
        await _close(runtime)
