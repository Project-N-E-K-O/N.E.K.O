"""Sample ownership at provider turn handoff, with actual streaming resample sizes."""

import numpy as np
import pytest
import soxr

from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from tests.unit.test_voice_admission_integration import make_runtime


def resampled_chunk_sizes():
    resampler = soxr.ResampleStream(48000, 16000, 1, dtype="float32", quality="HQ")
    sizes = []
    while len(sizes) < 64:
        audio = resampler.resample_chunk(np.zeros(480, dtype=np.float32))
        if len(audio):
            sizes.append(len(audio))
    assert sizes[:3] == [490, 489, 489]
    return sizes


class SampleInput:
    """Position-coded PCM exposes missing, duplicate and reordered samples."""

    def __init__(self, runtime, vad, token, sizes):
        self.runtime, self.vad, self.token = runtime, vad, token
        self.sizes = iter(sizes)
        self.position = 0

    async def send(self, probabilities):
        chunks = []
        for probability in probabilities:
            count = next(self.sizes)
            pcm = np.arange(self.position, self.position + count, dtype="<i2").tobytes()
            self.position += count
            chunks.append(pcm)
            self.vad.probability = probability
            await self.runtime.submit(
                ProcessedVoiceFrame(pcm, 16000, None), ingress_token=self.token
            )
            detector = self.runtime._asr_detector
            if detector is not None and detector._semantic_adapter is not None:
                await detector._semantic_adapter.wait_idle()
            await self.runtime._asr_detector_dispatcher.wait_idle()
        await self.runtime._asr_audio_dispatcher.wait_idle()
        return b"".join(chunks)


def uploaded(session):
    return b"".join(call.args[0] for call in session.stream_audio.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("optimization", [False, True])
@pytest.mark.parametrize("before_final", [0, 1, 2, 8])
@pytest.mark.parametrize("chunking", ["resampled", "aligned", "residual_one", "residual_511"])
async def test_provider_handoff_preserves_each_sample_once(
    optimization, before_final, chunking
):
    runtime, callbacks, session, vad, token = make_runtime(optimization)
    sizes = resampled_chunk_sizes() if chunking == "resampled" else [512] * 64
    if chunking == "residual_one":
        sizes[21] += 1
    elif chunking == "residual_511":
        sizes[21] += 511
    source = SampleInput(runtime, vad, token, sizes)
    try:
        first = await source.send([0.9] * 12 + [0.1] * 10)
        assert uploaded(session) == first
        boundary = source.position
        await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        second_prefix = await source.send([0.9] * before_final)
        # The pending successor cannot prepare or send ahead of old final.
        assert callbacks.on_prepare_turn.await_count == 1
        assert uploaded(session) == first
        await runtime._handle_independent_asr_final(
            "first sentence", runtime._asr_session_epoch, "qwen"
        )
        await runtime.wait_transcript_idle()
        second_tail = await source.send([0.9] * (12 - before_final))
        callbacks.on_failure.assert_not_awaited()
        assert callbacks.on_prepare_turn.await_count == 2
        assert callbacks.on_final.await_count == 1
        assert uploaded(session) == first + second_prefix + second_tail
        evidence = runtime._asr_admission_evidence[runtime._asr_prepared_turn_token]
        assert evidence.audio_start_sample >= boundary
        assert evidence.voiced_audio_ms <= (evidence.audio_end_sample - boundary) / 16
    finally:
        await runtime.close()
