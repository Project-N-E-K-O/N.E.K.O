"""Delivery-level checks across more than one provider result."""

import pytest

from tests.unit.test_asr_sample_handoff import SampleInput, resampled_chunk_sizes, uploaded
from tests.unit.test_voice_admission_integration import make_runtime


@pytest.mark.asyncio
async def test_repeated_unaligned_handoffs_keep_distinct_evidence_and_exact_audio():
    runtime, callbacks, session, vad, token = make_runtime(False)
    source = SampleInput(runtime, vad, token, resampled_chunk_sizes())
    expected = b""
    boundary = 0
    try:
        for turn in range(3):
            expected += await source.send([0.9] * 9 + [0.1] * 10)
            callbacks.on_failure.assert_not_awaited()
            assert uploaded(session) == expected
            assert callbacks.on_prepare_turn.await_count == turn + 1
            evidence = runtime._asr_admission_evidence[runtime._asr_prepared_turn_token]
            assert evidence.audio_start_sample >= boundary
            boundary = source.position
            await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
            await runtime._handle_independent_asr_final(
                f"sentence {turn}", runtime._asr_session_epoch, "qwen"
            )
            await runtime.wait_transcript_idle()
            assert callbacks.on_final.await_count == turn + 1
        candidates = [call.args[0].evidence.candidate_id for call in callbacks.on_final.await_args_list]
        assert len(set(candidates)) == 3
        assert uploaded(session) == expected
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_already_uploaded_overlap_is_not_replayed_when_provider_finals_arrive():
    runtime, callbacks, session, vad, token = make_runtime(False)
    source = SampleInput(runtime, vad, token, resampled_chunk_sizes())
    try:
        audio = await source.send([0.9] * 12 + [0.1] * 10 + [0.9] * 12 + [0.1] * 10)
        assert uploaded(session) == audio
        sends_before_results = session.stream_audio.await_count
        for sentence in ("first", "second"):
            await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
            await runtime._handle_independent_asr_final(
                sentence, runtime._asr_session_epoch, "qwen"
            )
        await runtime.wait_transcript_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        callbacks.on_failure.assert_not_awaited()
        assert callbacks.on_final.await_count == 2
        assert callbacks.on_prepare_turn.await_count == 2
        assert session.stream_audio.await_count == sends_before_results
        assert uploaded(session) == audio
        first, second = [call.args[0].evidence for call in callbacks.on_final.await_args_list]
        assert first.candidate_id != second.candidate_id
        assert second.audio_start_sample >= first.audio_end_sample
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_at", [0, 1000])
async def test_actual_unsent_sample_loss_fails_before_prepare_or_upload(missing_at):
    runtime, callbacks, session, vad, token = make_runtime(False)
    source = SampleInput(runtime, vad, token, [512] * 16)
    try:
        await source.send([0.9] * 6)
        lifecycle = runtime._asr_lifecycle
        # Model a real lost PCM sample, retaining the correct positions of the
        # surviving bytes. Total byte count cannot prove range continuity.
        buffers = (lifecycle._pending_connect, lifecycle._pre_roll)
        buffer = next(item for item in buffers if item.byte_count)
        payload = buffer.peek()
        start = buffer.spans[0].start
        buffer.clear()
        if missing_at:
            buffer.append(payload[:missing_at * 2], start_sample=start)
        buffer.append(payload[(missing_at + 1) * 2:], start_sample=start + missing_at + 1)
        await source.send([0.9])
        callbacks.on_failure.assert_awaited_once()
        callbacks.on_prepare_turn.assert_not_awaited()
        callbacks.on_final.assert_not_awaited()
        session.stream_audio.assert_not_awaited()
        assert lifecycle.admission_failure_reason == (
            "candidate_audio_range_missing" if missing_at == 0
            else "candidate_audio_range_discontinuous"
        )
    finally:
        await runtime.close()
