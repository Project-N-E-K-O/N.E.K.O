"""Evaluation must exercise production scheduling and local activation, not IPC alone."""

import pytest
import json
from types import MappingProxyType

from main_logic.voice_input.activation import ActivationGeneration, AudioFrame, WakeWordBatchResult, WakeWordDetection
from scripts.wake_word.evaluate_wake_runtime import _MeasuredDetector, measure_case


class Detector:
    inference_timeout_seconds = 2.0
    runtime_info = MappingProxyType({"test_detector": True})

    def __init__(self, hit_sequence=None):
        self.hit_sequence = hit_sequence
        self.frames = []
        self.closed = False

    async def prepare(self):
        pass

    async def feed_batch(self, frames, epoch):
        for count, frame in enumerate(frames, 1):
            self.frames.append(frame)
            if frame.sequence == self.hit_sequence:
                return WakeWordBatchResult(count, WakeWordDetection(
                    "test", frame.generation, epoch, frame.sample_start, frame.sample_end))
        return WakeWordBatchResult(len(frames), None)

    async def feed(self, frame, epoch):
        return (await self.feed_batch((frame,), epoch)).detection

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_runtime_evaluator_preserves_pcm_and_drains_production_tail_timer():
    pcm = b"".join(bytes([index]) * 320 for index in range(7))
    batch, single = Detector(), Detector()
    batched = await measure_case(pcm, batch, batching=True, paced=False)
    baseline = await measure_case(pcm, single, batching=False, paced=False)
    assert b"".join(frame.pcm for frame in batch.frames) == pcm
    assert b"".join(frame.pcm for frame in single.frames) == pcm
    assert batched["processed_frames"] == baseline["processed_frames"] == 7
    assert batched["ipc_calls"] < baseline["ipc_calls"] == 7
    assert batched["state_before_close"] == "waiting"
    assert batched["accepted_activations"] == []
    assert batched["total_cpu_seconds"] is None  # Fake worker must not invent CPU evidence.
    assert batch.closed and single.closed
    json.dumps(batched)


@pytest.mark.asyncio
async def test_runtime_evaluator_distinguishes_model_hit_and_local_acceptance():
    detector = Detector(hit_sequence=1)
    report = await measure_case(bytes(1280), detector, batching=True, paced=False)
    assert len(report["model_hits"]) == len(report["accepted_activations"]) == 1
    assert report["model_hits"][0]["processed_end_sample"] == 320
    assert report["state_before_close"] == "active"
    assert report["local_output_ranges"] == [(0, 160), (160, 320), (320, 480), (480, 640)]
    assert report["local_output_pcm_sha256"] == report["pcm_sha256"]
    assert detector.closed


@pytest.mark.asyncio
async def test_runtime_evaluator_accepts_irregular_original_frame_boundaries():
    detector = Detector()
    report = await measure_case(bytes(3000), detector, frame_samples=(489, 490, 160), paced=False)
    assert [f.sample_end - f.sample_start for f in detector.frames] == [489, 490, 160, 361]
    assert report["processed_frames"] == 4
    assert b"".join(f.pcm for f in detector.frames) == bytes(3000)


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", [(), (0,), (True,), (16001,)])
async def test_runtime_evaluator_rejects_invalid_packet_sizes(pattern):
    with pytest.raises(ValueError, match="Frame samples"):
        await measure_case(bytes(320), Detector(), frame_samples=pattern)


@pytest.mark.asyncio
async def test_runtime_evaluator_reports_detector_failure_as_failure():
    class FailingDetector(Detector):
        async def feed_batch(self, frames, epoch):
            raise RuntimeError("explicit test failure")

    detector = FailingDetector()
    report = await measure_case(bytes(1280), detector, paced=False)
    assert report["state_before_close"] == "unavailable"
    assert report["failure_reasons"] == ["wake_word_runtime_failed"]
    assert not report["accepted_activations"]
    assert report["ipc_calls"] == 1
    assert report["completed_ipc_calls"] == 0
    assert detector.closed


@pytest.mark.asyncio
async def test_optional_rss_observation_failure_does_not_change_detection():
    import psutil

    class DisappearedProcess:
        def memory_info(self):
            raise psutil.NoSuchProcess(123)

    measured = _MeasuredDetector(Detector(hit_sequence=0))
    measured.child = DisappearedProcess()
    frame = AudioFrame(0, 0, 160, 0.0, 16000, bytes(320),
        ActivationGeneration("test", 1, 1, 1, 1, "fixture"))
    result = await measured.feed_batch((frame,), 1)
    assert result.detection is not None
    assert measured.metric_errors == ["worker_rss_sample_unavailable"]
    assert measured.hits[0]["epoch"] == 1
