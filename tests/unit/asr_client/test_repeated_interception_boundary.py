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
        from main_logic.asr_client.endpointing.detector import ProviderCandidateFence
        self.seal_provider_candidate = AsyncMock(return_value=ProviderCandidateFence(7, 11, 0))
        self.complete_provider_candidate = AsyncMock(return_value=False)
        self.discard_provider_successor = AsyncMock(return_value=True)
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

class _DeliveryBoundaryDetector(_BoundaryDetector):
    """Controlled acoustic IO implements the current provider boundary protocol."""

    def __init__(self):
        super().__init__()
        from main_logic.asr_client.endpointing.detector import ProviderCandidateFence
        self.seal_provider_candidate = AsyncMock(return_value=ProviderCandidateFence(7, 11, 0))
        self.complete_provider_candidate = AsyncMock(return_value=False)
        self.discard_provider_successor = AsyncMock(return_value=True)


async def _delivery_boundary_fixture():
    from main_logic.voice_turn.interception_events import InterceptionDeliveryStage as Stage

    callbacks = _callbacks()
    runtime = IndependentAsrRuntime(callbacks)
    detector = _DeliveryBoundaryDetector()
    session, lifecycle, token = _install_active_candidate(runtime, detector)
    from main_logic.asr_client.provider_policy import resolve_provider_policy
    runtime._asr_provider = "soniox"
    lifecycle.provider_policy = resolve_provider_policy("soniox", "manual")
    sent = []

    async def write(pcm, **kwargs):
        spans = kwargs.get("delivery_spans", ())
        sent.append((pcm, spans))
        for span in spans:
            span.observe(Stage.TRANSPORT_WRITTEN)
            span.observe(Stage.TRANSPORT_OWNED)

    session.stream_audio = write
    session.signal_user_activity_end = AsyncMock()
    return runtime, callbacks, detector, session, lifecycle, token, sent


async def _close_delivery_boundary_fixture(runtime):
    warm = runtime._asr_warm_expiry_task
    if warm is not None:
        warm.cancel()
        await asyncio.gather(warm, return_exceptions=True)
    await _cleanup(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize("buffer_case", ["draining", "warm_idle"])
async def test_first_tagged_transition_drops_unmapped_local_pcm_and_keeps_registered_successor(buffer_case, monkeypatch):
    from main_logic.asr_client.audio_ranges import AudioSampleSpan
    from main_logic.voice_turn.audio_delivery import AudioDeliveryTag, AudioDeliverySpan
    from main_logic.voice_turn.interception_events import InterceptionDeliveryStage as Stage

    from main_logic.asr_client.input_delivery import InputAudioDeliveryLedger
    original_spans = InputAudioDeliveryLedger.spans
    mapping_errors = []

    def record_mapping_failure(ledger, ranges, samples):
        try:
            return original_spans(ledger, ranges, samples)
        except RuntimeError as exc:
            mapping_errors.append((str(exc), ranges, samples))
            raise

    # Observe the original mapping, preserving its execution and exceptions.
    monkeypatch.setattr(InputAudioDeliveryLedger, "spans", record_mapping_failure)
    runtime, callbacks, detector, session, lifecycle, token, sent = await _delivery_boundary_fixture()
    prefix = b"\x01\x00" * 1600
    unmapped = b"\x02\x00" * 400
    first, second = b"\x03\x00" * 400, b"\x04\x00" * 400
    receipts = [[], []]
    tags = [AudioDeliveryTag(400, receipts[i].append) for i in range(2)]
    try:
        assert (await runtime.submit(ProcessedVoiceFrame(prefix, 16000, 1.0, True), ingress_token=token.ingress)).status is AsrSubmitStatus.ACCEPTED
        await runtime.discontinue_input(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        sealed = runtime._asr_sealed_turn_token
        assert sealed.turn == token
        assert sent == [(prefix, ())]
        if buffer_case == "warm_idle":
            await runtime._handle_independent_asr_final("accepted prefix", runtime._asr_session_epoch, "soniox")
            await runtime.wait_transcript_idle()
            assert lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
            detector.feed.return_value = SimpleNamespace(
                endpointing_available=True, throttle_available=True,
                throttle_action=ThrottleAction.PROCESS_PCM, events=(),
            )
        # This local prefix has not yet been admitted or promised to a turn.
        detector.feed.return_value = SimpleNamespace(
            endpointing_available=True, throttle_available=True,
            throttle_action=ThrottleAction.PROCESS_PCM, events=(),
        )
        assert (await runtime.submit(ProcessedVoiceFrame(unmapped, 16000, 1.0, True), ingress_token=token.ingress)).status is AsrSubmitStatus.ACCEPTED
        if buffer_case == "draining":
            assert lifecycle._pending_turn.spans == (AudioSampleSpan(None, 400),)
        else:
            assert lifecycle._pre_roll.spans == (AudioSampleSpan(None, 400),)
        detector.feed.return_value = SimpleNamespace(
            endpointing_available=True, throttle_available=True,
            throttle_action=ThrottleAction.PROCESS_PCM,
            events=(SpeechActivityEvent.SPEECH_STARTED,),
        )
        for pcm, tag in zip((first, second), tags):
            submitted = await runtime.submit(ProcessedVoiceFrame(pcm, 16000, 1.0, True, delivery=tag), ingress_token=token.ingress)
            assert submitted.status is AsrSubmitStatus.ACCEPTED, mapping_errors
        if buffer_case == "draining":
            # Revoking local untagged PCM cannot reset the already sealed owner.
            assert runtime._asr_sealed_turn_token == sealed
            assert lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
            await runtime._handle_independent_asr_final("accepted prefix", runtime._asr_session_epoch, "soniox")
        await runtime.wait_transcript_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        assert b"".join(pcm for pcm, _ in sent) == prefix + first + second
        mapped = tuple(span for _, spans in sent for span in spans)
        assert mapped == (AudioDeliverySpan(tags[0], 0, 400), AudioDeliverySpan(tags[1], 0, 400))
        assert mapping_errors == []
        assert all(tag.settled for tag in tags)
        assert all(Stage.TRANSPORT_OWNED in items for items in receipts)
        assert callbacks.on_final.await_count == 1
        envelope = callbacks.on_final.await_args.args[0]
        assert (envelope.text, envelope.turn_token) == ("accepted prefix", token)
        callbacks.on_turn_abandoned.assert_not_awaited()
        callbacks.on_failure.assert_not_awaited()
        assert runtime._asr_session is session
        assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    finally:
        await _close_delivery_boundary_fixture(runtime)


@pytest.mark.asyncio
async def test_first_tagged_frame_does_not_reset_or_replay_already_accepted_active_turn():
    from main_logic.voice_turn.audio_delivery import AudioDeliveryTag, AudioDeliverySpan

    runtime, callbacks, _detector, session, lifecycle, token, sent = await _delivery_boundary_fixture()
    prefix, protected = b"\x05\x00" * 1600, b"\x06\x00" * 400
    tag = AudioDeliveryTag(400, lambda _stage: None)
    try:
        assert (await runtime.submit(ProcessedVoiceFrame(prefix, 16000, 1.0, True), ingress_token=token.ingress)).status is AsrSubmitStatus.ACCEPTED
        await runtime._asr_audio_dispatcher.wait_idle()
        assert sent == [(prefix, ())]
        assert (await runtime.submit(ProcessedVoiceFrame(protected, 16000, 1.0, True, delivery=tag), ingress_token=token.ingress)).status is AsrSubmitStatus.ACCEPTED
        await runtime._asr_audio_dispatcher.wait_idle()
        assert runtime._capture_turn_token(lifecycle) == token
        assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
        assert sent == [(prefix, ()), (protected, (AudioDeliverySpan(tag, 0, 400),))]
        assert tag.settled
        await runtime.discontinue_input(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        await runtime._handle_independent_asr_final("complete accepted turn", runtime._asr_session_epoch, "soniox")
        await runtime.wait_transcript_idle()
        assert callbacks.on_final.await_count == 1
        envelope = callbacks.on_final.await_args.args[0]
        assert (envelope.text, envelope.turn_token) == ("complete accepted turn", token)
        callbacks.on_turn_abandoned.assert_not_awaited()
        callbacks.on_failure.assert_not_awaited()
        assert runtime._asr_session is session
    finally:
        await _close_delivery_boundary_fixture(runtime)
@pytest.mark.asyncio
@pytest.mark.parametrize("promise", ["confirmed_pending", "protected_prefix", "active_start"])
async def test_first_tagged_frame_refuses_to_erase_promised_untagged_pcm(promise):
    from main_logic.voice_turn.audio_delivery import AudioDeliveryTag
    from main_logic.voice_turn.contracts import PreserveUnsentPrefix

    runtime, callbacks, detector, session, lifecycle, token, sent = await _delivery_boundary_fixture()
    prefix, promised, trigger = b"\x11\x00" * 1600, b"\x12\x00" * 400, b"\x13\x00" * 400
    tagged = b"\x14\x00" * 400
    receipts = []
    tag = AudioDeliveryTag(400, receipts.append)
    entered, release = asyncio.Event(), asyncio.Event()
    preparing = None
    try:
        assert (await runtime.submit(ProcessedVoiceFrame(prefix, 16000, 1.0, True), ingress_token=token.ingress)).status is AsrSubmitStatus.ACCEPTED
        await runtime.discontinue_input(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        sealed = runtime._asr_sealed_turn_token
        if promise == "confirmed_pending":
            assert (await runtime.submit(ProcessedVoiceFrame(promised, 16000, 1.0, True), ingress_token=token.ingress)).status is AsrSubmitStatus.ACCEPTED
            assert lifecycle.has_pending_turn
            preserved = lifecycle._pending_turn.peek()
        else:
            await runtime._handle_independent_asr_final("first accepted final", runtime._asr_session_epoch, "soniox")
            await runtime.wait_transcript_idle()
            detector.feed.return_value = SimpleNamespace(
                endpointing_available=True, throttle_available=True,
                throttle_action=ThrottleAction.PROCESS_PCM, events=(),
            )
            preserved_prefix = PreserveUnsentPrefix(token.ingress, "activation", 0)
            assert (await runtime.submit(
                ProcessedVoiceFrame(promised, 16000, 1.0, True), ingress_token=token.ingress,
                preserve_prefix=preserved_prefix if promise == "protected_prefix" else None,
            )).status is AsrSubmitStatus.ACCEPTED
            detector.feed.return_value = SimpleNamespace(
                endpointing_available=True, throttle_available=True,
                throttle_action=ThrottleAction.PROCESS_PCM, events=(SpeechActivityEvent.SPEECH_STARTED,),
            )
            if promise == "active_start":
                async def prepare(_turn):
                    entered.set()
                    await release.wait()
                    return True

                callbacks.on_prepare_turn.side_effect = prepare
                preparing = asyncio.create_task(runtime.submit(
                    ProcessedVoiceFrame(trigger, 16000, 1.0, True), ingress_token=token.ingress,
                ))
                await asyncio.wait_for(entered.wait(), 1)
                preserved = lifecycle.peek_active_start_audio()
                assert preserved == promised
            else:
                assert lifecycle.prefix_protected
                preserved = lifecycle._pending_connect.peek()
        before_turn = runtime._capture_turn_token(lifecycle)
        before_state = lifecycle.snapshot
        rejected = await runtime.submit(ProcessedVoiceFrame(tagged, 16000, 1.0, True, delivery=tag), ingress_token=token.ingress)
        assert rejected.status is AsrSubmitStatus.UNAVAILABLE
        assert not runtime._asr_input_delivery.enabled
        assert receipts == []
        assert lifecycle.snapshot == before_state
        assert runtime._capture_turn_token(lifecycle) == before_turn
        assert sent == [(prefix, ())]
        if promise == "confirmed_pending":
            assert runtime._asr_sealed_turn_token == sealed
            assert lifecycle._pending_turn.peek() == preserved == promised
            await runtime._handle_independent_asr_final("first accepted final", runtime._asr_session_epoch, "soniox")
        elif promise == "active_start":
            assert lifecycle.peek_active_start_audio() == preserved
            release.set()
            assert (await preparing).status is AsrSubmitStatus.ACCEPTED
        else:
            assert lifecycle.prefix_protected
            assert lifecycle._pending_connect.peek() == preserved == promised
            assert runtime._asr_protected_prefix == preserved_prefix
            assert (await runtime.submit(ProcessedVoiceFrame(trigger, 16000, 1.0, True), ingress_token=token.ingress)).status is AsrSubmitStatus.ACCEPTED
        await runtime.wait_transcript_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        assert b"".join(pcm for pcm, _ in sent) == prefix + promised + (b"" if promise == "confirmed_pending" else trigger)
        assert all(spans == () for _, spans in sent)
        await runtime.discontinue_input(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        await runtime._handle_independent_asr_final("promised successor final", runtime._asr_session_epoch, "soniox")
        await runtime.wait_transcript_idle()
        assert [call.args[0].text for call in callbacks.on_final.await_args_list] == ["first accepted final", "promised successor final"]
        assert callbacks.on_final.await_args_list[0].args[0].turn_token == token
        callbacks.on_turn_abandoned.assert_not_awaited()
        callbacks.on_failure.assert_not_awaited()
        assert runtime._asr_session is session
    finally:
        release.set()
        if preparing is not None:
            await asyncio.gather(preparing, return_exceptions=True)
        await _close_delivery_boundary_fixture(runtime)


@pytest.mark.asyncio
async def test_first_tagged_numeric_admission_prefix_fails_closed_without_mixed_upload():
    from main_logic.voice_turn.audio_delivery import AudioDeliveryTag
    from tests.unit.test_voice_admission_integration import make_runtime, send

    # The real admission gate, detector FIFO and semantic adapter assign source
    # ranges before any delivery tags. Only VAD/model/provider IO is controlled.
    runtime, callbacks, session, vad, token = make_runtime(True, smart_turn=True)
    lifecycle = runtime._asr_lifecycle
    try:
        await send(runtime, vad, token, [0.9] * 6)
        spans = lifecycle._pre_roll.spans + lifecycle._pending_connect.spans
        assert spans and all(span.start is not None for span in spans)
        callbacks.on_prepare_turn.assert_not_awaited()
        tag = AudioDeliveryTag(512, lambda _stage: None)
        vad.probability = 0.9
        await runtime.submit(ProcessedVoiceFrame(b"\x19\x00" * 512, 16000, None, delivery=tag), ingress_token=token)
        detector = runtime._asr_detector
        if detector is not None:
            await detector._semantic_adapter.wait_idle()
        await runtime._asr_detector_dispatcher.wait_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        session.stream_audio.assert_not_awaited()
        callbacks.on_prepare_turn.assert_not_awaited()
        callbacks.on_failure.assert_awaited_once()
        assert callbacks.on_failure.await_args.args[0].code == "ASR_INGRESS_BACKPRESSURE"
        assert lifecycle.admission_failure_reason == "candidate_audio_range_missing"
        assert runtime._asr_lifecycle is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("connecting_state", ["prewarming", "backoff"])
async def test_local_boundary_discards_pending_confirmation_before_new_input(connecting_state):
    from main_logic.asr_client.lifecycle import VoiceLifecycleEvent

    runtime, callbacks, detector, session, lifecycle, token, sent = await _delivery_boundary_fixture()
    prefix, old_pcm, new_pcm = (bytes([value, 0]) * 400 for value in (31, 32, 33))
    try:
        assert (await runtime.submit(ProcessedVoiceFrame(prefix, 16000, 1.0, True), ingress_token=token.ingress)).status is AsrSubmitStatus.ACCEPTED
        await runtime.discontinue_input(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        await runtime._handle_independent_asr_final("accepted prefix", runtime._asr_session_epoch, "soniox")
        await runtime.wait_transcript_idle()
        session.is_ready = False
        assert (await runtime.submit(ProcessedVoiceFrame(old_pcm, 16000, 1.0, True), ingress_token=token.ingress)).status is AsrSubmitStatus.ACCEPTED
        assert lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
        assert runtime._asr_pending_speech_confirmed
        assert runtime._asr_pending_speech_onset_at is not None
        if connecting_state == "backoff":
            lifecycle.transition(VoiceLifecycleEvent.CONNECT_FAILED)
            assert lifecycle.snapshot.state is VoiceLifecycleState.BACKOFF
        await runtime.discontinue_input(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        assert lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
        assert not runtime._asr_pending_speech_confirmed
        assert runtime._asr_pending_speech_onset_at is None
        detector.feed.return_value = SimpleNamespace(
            endpointing_available=True, throttle_available=True,
            throttle_action=ThrottleAction.PROCESS_PCM, events=(),
        )
        assert (await runtime.submit(ProcessedVoiceFrame(new_pcm, 16000, 0.0, True), ingress_token=token.ingress)).status is AsrSubmitStatus.ACCEPTED
        assert lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
        assert not runtime._asr_pending_speech_confirmed
        assert runtime._asr_pending_speech_onset_at is None
        assert sent == [(prefix, ())]
        assert callbacks.on_prepare_turn.await_count == 0
        assert callbacks.on_final.await_count == 1
        callbacks.on_failure.assert_not_awaited()
    finally:
        await _close_delivery_boundary_fixture(runtime)


@pytest.mark.asyncio
async def test_ingress_ledger_switch_rebinds_discard_without_settling_old_unknown():
    from dataclasses import replace
    from main_logic.voice_turn.audio_delivery import AudioDeliveryTag
    from main_logic.voice_turn.interception_events import InterceptionDeliveryStage as Stage

    runtime, callbacks, detector, session, lifecycle, token, sent = await _delivery_boundary_fixture()
    old_receipts, new_receipts = [], []
    old_tag = AudioDeliveryTag(400, old_receipts.append)
    new_tag = AudioDeliveryTag(400, new_receipts.append)

    async def unknown_write(pcm, **kwargs):
        for span in kwargs.get("delivery_spans", ()):
            span.observe(Stage.UNKNOWN)

    session.stream_audio = unknown_write
    try:
        first = await runtime.submit(ProcessedVoiceFrame(b"\x01\x00" * 400, 16000, 1.0, True, delivery=old_tag), ingress_token=token.ingress)
        assert first.status is AsrSubmitStatus.ACCEPTED
        await runtime._asr_audio_dispatcher.wait_idle()
        assert Stage.UNKNOWN in old_receipts
        assert not old_tag.settled
        await runtime.discontinue_input(
            ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1,
        )
        assert lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
        await runtime._handle_independent_asr_final("old uncertain capture", runtime._asr_session_epoch, "soniox")
        await runtime.wait_transcript_idle()
        assert lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
        assert Stage.UNKNOWN in old_receipts
        assert Stage.NOT_SENT not in old_receipts
        assert not old_tag.settled
        old_ledger = runtime._asr_input_delivery
        old_before = list(old_receipts)
        # The old final reaches real warm idle; only acoustic/provider IO is
        # controlled. Both discards use the public input-boundary operation.
        detector.feed = AsyncMock(return_value=SimpleNamespace(
            endpointing_available=True, throttle_available=True,
            throttle_action=ThrottleAction.PROCESS_PCM, events=(),
        ))
        successor_ingress = replace(token.ingress, connection_id="successor-connection", lease_generation=2)
        result = await runtime.submit(ProcessedVoiceFrame(b"\x02\x00" * 400, 16000, 0.0, True), ingress_token=successor_ingress)
        assert result.status is AsrSubmitStatus.ACCEPTED
        new_ledger = runtime._asr_input_delivery
        assert new_ledger is not old_ledger
        assert lifecycle.pre_roll_bytes == 800
        await runtime.discontinue_input(
            ingress_token=successor_ingress, deadline=asyncio.get_running_loop().time() + 1,
        )
        assert lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
        assert old_receipts == old_before
        assert not old_tag.settled
        assert lifecycle._audio_discard_observer.__self__ is new_ledger
        result = await runtime.submit(ProcessedVoiceFrame(b"\x03\x00" * 400, 16000, 0.0, True, delivery=new_tag), ingress_token=successor_ingress)
        assert result.status is AsrSubmitStatus.ACCEPTED
        assert lifecycle.pre_roll_bytes == 800
        await runtime.discontinue_input(
            ingress_token=successor_ingress, deadline=asyncio.get_running_loop().time() + 1,
        )
        assert new_receipts == [Stage.NOT_SENT]
        assert new_tag.settled
        assert old_receipts == old_before
        assert not old_tag.settled
        callbacks.on_failure.assert_not_awaited()
    finally:
        await _close_delivery_boundary_fixture(runtime)
