import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.audio import AsrAudioDispatcher
from main_logic.asr_client.audio_ranges import AudioSampleSpan, RangedAudioBuffer
from main_logic.asr_client.input_delivery import InputAudioDeliveryLedger
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController, VoiceRouteMode, VoiceLifecycleState
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.asr_client.runtime import IndependentAsrRuntime
from main_logic.voice_input.interception_events import InterceptionDeliveryStage as Stage
from main_logic.voice_turn.audio_delivery import AudioDeliverySpan, AudioDeliveryTag, slice_delivery_spans
from tests.unit.test_asr_audio_dispatcher import _turn
from tests.unit.asr_client.test_candidate_rejection_runtime import _callbacks, _install_active_candidate, _RejectionDetector


@pytest.mark.parametrize("ranges,samples,error", [
    ((AudioSampleSpan(None, 2),), 2, "SOURCE_RANGE_UNKNOWN"),
    ((AudioSampleSpan(102, 10),), 10, "SOURCE_RANGE_MISSING"),
    ((AudioSampleSpan(112, 4),), 4, "SOURCE_RANGE_MISSING"),
    ((AudioSampleSpan(102, 2), AudioSampleSpan(110, 2)), 5, "PAYLOAD_RANGE_MISMATCH"),
])
def test_invalid_payload_mapping_cannot_settle_or_replace_other_intervals(ranges, samples, error):
    reports = []
    first = AudioDeliveryTag(4, lambda stage: reports.append(("first", stage)))
    second = AudioDeliveryTag(4, lambda stage: reports.append(("second", stage)))
    ledger = InputAudioDeliveryLedger(max_tags=2)
    ledger.register(100, 104, first)
    ledger.register(110, 114, second)

    with pytest.raises(RuntimeError, match=error):
        ledger.spans(ranges, samples)

    assert reports == []
    assert not first.settled and not second.settled
    retained = ledger.spans((AudioSampleSpan(100, 4), AudioSampleSpan(110, 4)), 8)
    assert retained == (AudioDeliverySpan(first, 0, 4), AudioDeliverySpan(second, 0, 4))
    # The mapping rejection must leave both independent owners usable. An
    # actual writer may settle one without granting evidence to its neighbor.
    retained[0].observe(Stage.TRANSPORT_WRITTEN)
    retained[0].observe(Stage.TRANSPORT_OWNED)
    assert first.settled and not second.settled
    assert reports == [("first", Stage.TRANSPORT_WRITTEN), ("first", Stage.TRANSPORT_OWNED)]


@pytest.mark.parametrize("start,end,error", [
    (110, 115, "RANGE_MISMATCH"),
    (102, 106, "RANGE_OVERLAP"),
])
def test_rejected_registration_does_not_consume_capacity_or_expose_new_owner(start, end, error):
    reports = []
    first = AudioDeliveryTag(4, lambda stage: reports.append(("first", stage)))
    rejected = AudioDeliveryTag(4, lambda stage: reports.append(("rejected", stage)))
    successor = AudioDeliveryTag(4, lambda stage: reports.append(("successor", stage)))
    ledger = InputAudioDeliveryLedger(max_tags=2)
    ledger.register(100, 104, first)
    with pytest.raises(ValueError, match=error):
        ledger.register(start, end, rejected)

    assert reports == []
    assert not first.settled and not rejected.settled
    assert ledger.spans((AudioSampleSpan(100, 4),), 4) == (AudioDeliverySpan(first, 0, 4),)
    with pytest.raises(RuntimeError, match="SOURCE_RANGE_MISSING"):
        ledger.spans((AudioSampleSpan(110, 4),), 4)
    ledger.register(110, 114, successor)
    assert ledger.spans((AudioSampleSpan(110, 4),), 4) == (AudioDeliverySpan(successor, 0, 4),)
    ledger.discard(AudioSampleSpan(110, 4))
    assert reports == [("successor", Stage.NOT_SENT)]
    assert not first.settled and not rejected.settled


def test_discard_settles_only_matching_unsent_range_and_preserves_written_neighbor():
    reports = []
    written = AudioDeliveryTag(4, lambda stage: reports.append(("written", stage)))
    pending = AudioDeliveryTag(6, lambda stage: reports.append(("pending", stage)))
    untouched = AudioDeliveryTag(4, lambda stage: reports.append(("untouched", stage)))
    ledger = InputAudioDeliveryLedger()
    ledger.register(100, 104, written)
    ledger.register(110, 116, pending)
    ledger.register(120, 124, untouched)
    written.observe(0, 4, Stage.TRANSPORT_WRITTEN)
    ledger.discard(AudioSampleSpan(None, 4))
    ledger.discard(AudioSampleSpan(104, 6))  # Only the original-axis gap.
    ledger.discard(AudioSampleSpan(102, 12))  # Written tail, gap, pending prefix.
    assert reports == [("written", Stage.TRANSPORT_WRITTEN)]
    assert not written.settled and not pending.settled and not untouched.settled

    ledger.discard(AudioSampleSpan(114, 2))
    assert pending.settled
    assert reports == [("written", Stage.TRANSPORT_WRITTEN), ("pending", Stage.NOT_SENT)]
    assert not untouched.settled
    written.observe(0, 4, Stage.TRANSPORT_OWNED)
    assert written.settled
    assert reports[-1] == ("written", Stage.TRANSPORT_OWNED)
    assert ledger.spans((AudioSampleSpan(120, 4),), 4) == (AudioDeliverySpan(untouched, 0, 4),)


def test_written_is_not_settled_until_explicit_transport_ownership():
    stages = []
    tag = AudioDeliveryTag(4, stages.append)
    ledger = InputAudioDeliveryLedger(max_tags=1)
    ledger.register(10, 14, tag)
    tag.observe(0, 4, Stage.TRANSPORT_WRITTEN)
    assert not tag.settled
    with pytest.raises(RuntimeError, match="CAPACITY"):
        ledger.register(14, 18, AudioDeliveryTag(4, stages.append))
    tag.observe(0, 4, Stage.TRANSPORT_OWNED)
    assert tag.settled
    ledger.register(14, 18, AudioDeliveryTag(4, stages.append))
    assert stages == [Stage.TRANSPORT_WRITTEN, Stage.TRANSPORT_OWNED]


def test_explicit_not_sent_cannot_later_be_relabelled_written_or_owned():
    tag = AudioDeliveryTag(4, lambda _stage: None)
    tag.observe(0, 4, Stage.NOT_SENT)
    for stage in (Stage.TRANSPORT_WRITTEN, Stage.TRANSPORT_OWNED):
        with pytest.raises(ValueError, match="discarded"):
            tag.observe(0, 4, stage)


def test_unknown_does_not_allow_late_local_write_or_ownership():
    stages = []
    tag = AudioDeliveryTag(4, stages.append)
    with pytest.raises(ValueError, match="preceding write"):
        tag.observe(0, 4, Stage.TRANSPORT_OWNED)
    tag.observe(0, 4, Stage.UNKNOWN)
    tag.observe(0, 4, Stage.TRANSPORT_WRITTEN)
    tag.observe(0, 4, Stage.TRANSPORT_OWNED)
    tag.observe(0, 4, Stage.UNKNOWN)
    assert stages == [Stage.UNKNOWN]
    assert not tag.settled


def test_rejected_owner_receipt_is_not_silently_marked_settled():
    tag = AudioDeliveryTag(4, lambda _stage: False)
    with pytest.raises(RuntimeError, match="rejected"):
        tag.observe(0, 4, Stage.NOT_SENT)
    assert not tag.settled


def test_sample_mapping_retains_holes_and_slices_original_tag_coordinates():
    first, second = AudioDeliveryTag(4, lambda _stage: None), AudioDeliveryTag(6, lambda _stage: None)
    ledger = InputAudioDeliveryLedger()
    ledger.register(100, 104, first)
    ledger.register(200, 206, second)
    spans = ledger.spans((AudioSampleSpan(102, 2), AudioSampleSpan(201, 4)), 6)
    assert spans == (AudioDeliverySpan(first, 2, 4), AudioDeliverySpan(second, 1, 5))
    assert slice_delivery_spans(spans, 1, 4) == (
        AudioDeliverySpan(first, 3, 4), AudioDeliverySpan(second, 1, 4),
    )
    with pytest.raises(RuntimeError, match="MISSING"):
        ledger.spans((AudioSampleSpan(103, 98),), 98)


def test_buffer_move_transfers_ownership_and_only_trim_reports_discard():
    dropped = []
    source, target = RangedAudioBuffer(capacity_ms=100), RangedAudioBuffer(capacity_ms=100)
    source.on_discard = target.on_discard = dropped.append
    source.append(b"\x01\x00" * 8, start_sample=100)
    source.move_to(target)
    assert dropped == []
    target.trim_to_bytes(6)
    assert dropped == [AudioSampleSpan(100, 5)]
    assert target.spans == (AudioSampleSpan(105, 3),)
    target.clear()
    assert dropped[-1] == AudioSampleSpan(105, 3)


def test_candidate_retention_reports_only_rejected_prefix_not_copied_audio():
    lifecycle = VoiceInputLifecycleController(provider_policy=resolve_provider_policy("glm", "manual"), shadow_mode=False)
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    dropped = []
    lifecycle.set_audio_discard_observer(dropped.append)
    lifecycle.buffer_admission_audio(b"\x01\x00" * 12, start_sample=0, through_sample=12, confirmed=False)
    assert lifecycle.retain_admitted_candidate(start_sample=5)
    assert dropped == [AudioSampleSpan(0, 5)]
    assert lifecycle._pre_roll.spans == (AudioSampleSpan(5, 7),)


@pytest.mark.asyncio
async def test_abort_between_chunks_settles_only_never_attempted_remainder():
    reports = []
    first = AudioDeliveryTag(16000, lambda stage: reports.append(("first", stage)))
    second = AudioDeliveryTag(16000, lambda stage: reports.append(("second", stage)))
    session = SimpleNamespace(signal_user_activity_end=AsyncMock())
    dispatcher = AsrAudioDispatcher(validator=lambda _token, _session: True, on_wire_audio=AsyncMock(), on_failure=AsyncMock())
    turn = _turn()

    async def send(_pcm, **kwargs):
        assert kwargs["delivery_spans"] == (AudioDeliverySpan(first, 0, 16000),)
        dispatcher.abort(turn)

    session.stream_audio = send
    assert dispatcher.activate(turn, session, b"\x01\x00" * 32000, delivery_spans=(
        AudioDeliverySpan(first, 0, 16000), AudioDeliverySpan(second, 0, 16000),
    ))
    await dispatcher.wait_idle()
    await dispatcher.close()
    assert reports == [("first", Stage.QUEUED), ("second", Stage.NOT_SENT)]


@pytest.mark.asyncio
async def test_accepted_dispatch_only_means_queued_until_real_writer_reports():
    reports = []
    tag = AudioDeliveryTag(4, reports.append)
    session = SimpleNamespace(stream_audio=AsyncMock(), signal_user_activity_end=AsyncMock())
    dispatcher = AsrAudioDispatcher(validator=lambda _token, _session: True, on_wire_audio=AsyncMock(), on_failure=AsyncMock())
    assert dispatcher.activate(_turn(), session, b"\x01\x00" * 4, delivery_spans=(AudioDeliverySpan(tag, 0, 4),))
    await dispatcher.wait_idle()
    assert reports == [Stage.QUEUED]
    saved = session.stream_audio.await_args.kwargs["delivery_spans"]
    saved[0].observe(Stage.TRANSPORT_WRITTEN)
    saved[0].observe(Stage.TRANSPORT_OWNED)
    assert reports == [Stage.QUEUED, Stage.TRANSPORT_WRITTEN, Stage.TRANSPORT_OWNED]
    await dispatcher.close()


@pytest.mark.asyncio
async def test_real_manual_boundary_orders_pcm_then_seal_and_preserves_prefix_final():
    callbacks = _callbacks()
    runtime = IndependentAsrRuntime(callbacks)
    detector = _RejectionDetector()
    detector.endpointing_ready = lambda _token: True
    detector.release_deferred_turn = AsyncMock()
    session, lifecycle, token = _install_active_candidate(runtime, detector)
    order = []
    session.stream_audio = AsyncMock(side_effect=lambda *_args, **_kwargs: order.append("audio"))
    session.signal_user_activity_end = AsyncMock(side_effect=lambda: order.append("seal"))
    assert runtime._asr_audio_dispatcher.enqueue_audio(token, session, b"\x01\x00" * 4, sample_rate_hz=16000, sequence_no=1)
    runtime._asr_audio_sequence = 1
    try:
        await runtime.discontinue_input(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        assert order == ["audio", "seal"]
        assert lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
        assert runtime._asr_turn_prepared
        callbacks.on_turn_abandoned.assert_not_awaited()
        detector.reset.assert_awaited_once()
        # This is the real final handler and transcript dispatcher. Resetting
        # detector context at the explicit hole must not erase its sealed turn.
        await runtime._handle_independent_asr_final("accepted prefix", runtime._asr_session_epoch, "glm")
        await runtime.wait_input_settled(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        assert callbacks.on_final.await_count == 1
        envelope = callbacks.on_final.await_args.args[0]
        assert envelope.text == "accepted prefix"
        assert envelope.turn_token == token
    finally:
        watchdog = runtime._asr_final_watchdog_task
        if watchdog is not None:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
        await runtime._asr_audio_dispatcher.close()
        runtime._asr_transcript_dispatcher.invalidate_all()


@pytest.mark.asyncio
async def test_provider_vad_without_real_boundary_rejects_without_fake_silence_or_seal():
    runtime = IndependentAsrRuntime(_callbacks())
    detector = _RejectionDetector()
    session, lifecycle, token = _install_active_candidate(runtime, detector)
    lifecycle.provider_policy = resolve_provider_policy("qwen", "provider")
    session.signal_user_activity_end = AsyncMock()
    try:
        with pytest.raises(RuntimeError, match="BOUNDARY_UNSUPPORTED"):
            await runtime.discontinue_input(ingress_token=token.ingress, deadline=asyncio.get_running_loop().time() + 1)
        session.signal_user_activity_end.assert_not_awaited()
        detector.reset.assert_not_awaited()
        assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    finally:
        await runtime._asr_audio_dispatcher.close()
        runtime._asr_transcript_dispatcher.invalidate_all()
