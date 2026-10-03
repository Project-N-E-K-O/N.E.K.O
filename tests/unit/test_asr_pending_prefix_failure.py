"""A held successor prefix never leaks to transport after a range failure."""

import asyncio

import pytest

from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from tests.unit.test_asr_sample_handoff import SampleInput, resampled_chunk_sizes, uploaded
from tests.unit.test_voice_admission_integration import make_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["gap", "capacity"])
async def test_pending_activation_bad_input_does_not_write_successor(failure):
    runtime, callbacks, session, vad, token = make_runtime(False)
    source = SampleInput(runtime, vad, token, resampled_chunk_sizes())
    entered, release = asyncio.Event(), asyncio.Event()
    final_task = None
    try:
        first = await source.send([0.9] * 12 + [0.1] * 10)
        await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        await source.send([0.9] * 9)

        async def pause_active(notification):
            if notification.state == "active" and not entered.is_set():
                entered.set()
                await release.wait()

        callbacks.on_lifecycle.side_effect = pause_active
        final_task = asyncio.create_task(runtime._handle_independent_asr_final(
            "first", runtime._asr_session_epoch, "qwen",
        ))
        await asyncio.wait_for(entered.wait(), 1)
        lifecycle = runtime._asr_lifecycle
        if failure == "gap":
            # A source sample is absent between held prefix and the next block.
            runtime._asr_input_sample_end += 1
            pcm = b"\x01\x00" * 490
        else:
            pcm = b"\x01\x00" * (lifecycle.prefix_capacity_bytes // 2)
        await asyncio.wait_for(runtime.submit(
            ProcessedVoiceFrame(pcm, 16000, .9, True), ingress_token=token,
        ), 1)
        release.set()
        await asyncio.wait_for(asyncio.gather(final_task, return_exceptions=True), 1)
        await runtime._asr_audio_dispatcher.wait_idle()
        callbacks.on_failure.assert_awaited_once()
        assert uploaded(session) == first
        assert callbacks.on_prepare_turn.await_count == 1
    finally:
        release.set()
        if final_task is not None and not final_task.done():
            final_task.cancel()
            await asyncio.gather(final_task, return_exceptions=True)
        await runtime.close()
