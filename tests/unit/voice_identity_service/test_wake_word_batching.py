"""Deterministic collector tests: advance logical time, never race wall sleeps."""
import asyncio
from dataclasses import replace

import pytest

from main_logic.voice_input.activation import ActivationState, AudioFrame, WakeWordBatchResult, WakeWordDetection
from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntimeConfig
from tests.unit.voice_identity_service.test_wake_word_runtime import Detector, GENERATION, runtime, settle


def small(sequence, size=160, start=None):
    start = sequence * size if start is None else start
    return AudioFrame(sequence, start, start + size, start / 16000, 16000,
                      bytes([sequence % 251]) * (size * 2), GENERATION)


class BatchDetector(Detector):
    def __init__(self):
        super().__init__()
        self.batches = []
        self.handler = None

    async def feed_batch(self, frames, epoch):
        self.batches.append(tuple(frames))
        if self.handler:
            return await self.handler(frames, epoch)
        return WakeWordBatchResult(len(frames), None)


@pytest.mark.asyncio
async def test_four_frames_one_batch_exact_original_pcm():
    detector = BatchDetector()
    instance, _, sent, _ = runtime(detector)
    await instance.prepare()
    for i in range(4):
        await instance.feed(small(i), voice_activity=False)
    await settle()
    assert detector.batches == [tuple(small(i) for i in range(4))]
    assert instance._wake_queue_bytes == instance._wake_inflight_bytes == 0
    assert sent == []
    await instance.close()


@pytest.mark.asyncio
async def test_tail_wait_is_anchored_and_close_wakes_waiter():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector)
    await instance.prepare()
    clock = [asyncio.get_running_loop().time()]
    instance._wake_now = lambda: clock[0]
    entered = asyncio.Event()
    waits = []

    async def controlled_wait(timeout):
        waits.append(timeout)
        entered.set()
        await instance._wake_changed.wait()

    instance._wait_wake_changed = controlled_wait
    await instance.feed(small(0), voice_activity=False)
    await entered.wait()
    clock[0] += .010
    entered.clear()
    await instance.feed(small(1), voice_activity=False)
    await entered.wait()
    assert waits[0] == pytest.approx(.030)
    assert waits[-1] == pytest.approx(.020)
    clock[0] += .0201
    instance._wake_changed.set()
    await settle()
    assert detector.batches == [(small(0), small(1))]
    entered.clear()
    await instance.feed(small(2), voice_activity=False)
    await entered.wait()
    await instance.close()
    assert len(detector.batches) == 1
    assert instance._wake_task is None


@pytest.mark.asyncio
async def test_large_frame_flushes_prefix_without_splitting():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector)
    await instance.prepare()
    frames = [small(0), small(1, 512, 160), small(2, 489, 672), small(3, 490, 1161)]
    for frame in frames:
        await instance.feed(frame, voice_activity=False)
    await settle()
    # 489+490 cannot fit. The last short batch expires via an explicit clock advance.
    instance._wake_now = lambda: asyncio.get_running_loop().time() + .1
    instance._wake_changed.set()
    await settle()
    assert detector.batches == [(frame,) for frame in frames]
    await instance.close()


@pytest.mark.asyncio
async def test_rejected_hit_tail_runs_before_new_audio_once():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector)
    await instance.prepare()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def handler(frames, epoch):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            # Valid detector identity but unavailable controller evidence -> rejected.
            return WakeWordBatchResult(2, WakeWordDetection("keyword", GENERATION, epoch, 0, 1))
        return WakeWordBatchResult(len(frames), None)

    detector.handler = handler
    # Reject through the existing controller contract without editing the result.
    instance._controller.apply_wake_word = lambda *args, **kwargs: instance._controller.tick(1.5)
    for i in range(4):
        await instance.feed(small(i), voice_activity=False)
    await entered.wait()
    for i in range(4, 8):
        await instance.feed(small(i), voice_activity=False)
    assert instance._wake_inflight_bytes == 1280
    assert instance._wake_queue_bytes == 1280
    release.set()
    await settle()
    assert detector.batches == [tuple(small(i) for i in range(4)),
                                (small(2), small(3)), tuple(small(i) for i in range(4, 8))]
    assert instance._wake_inflight_bytes == instance._wake_queue_bytes == 0
    await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [WakeWordBatchResult(0, None), WakeWordBatchResult(1, None)])
async def test_invalid_ack_fails_closed_without_retry(invalid):
    detector = BatchDetector()
    async def handler(frames, epoch):
        return invalid
    detector.handler = handler
    instance, _, sent, _ = runtime(detector)
    await instance.prepare()
    for i in range(4):
        await instance.feed(small(i), voice_activity=False)
    await settle()
    assert instance.state is ActivationState.UNAVAILABLE
    assert len(detector.batches) == 1
    assert detector.closed
    assert sent == []
    await instance.close()


@pytest.mark.asyncio
async def test_disabled_policy_uses_batch_protocol_singletons_without_wait():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector, config=VoiceSessionActivationRuntimeConfig(wake_batching_enabled=False))
    await instance.prepare()
    for i in range(4):
        await instance.feed(small(i), voice_activity=False)
    await settle()
    assert detector.batches == [(small(i),) for i in range(4)]
    await instance.close()


@pytest.mark.asyncio
async def test_preparation_backlog_preserves_original_age():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector)
    clock = [asyncio.get_running_loop().time()]
    instance._wake_now = lambda: clock[0]
    await instance.feed(small(0), voice_activity=False)
    clock[0] += .1
    await instance.prepare()
    await settle()
    assert detector.batches == [(small(0),)]
    await instance.close()


@pytest.mark.asyncio
async def test_tail_keeps_original_processing_deadline():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector)
    await instance.prepare()
    clock = [asyncio.get_running_loop().time()]
    instance._wake_now = lambda: clock[0]
    async def handler(frames, epoch):
        clock[0] += 2.1
        return WakeWordBatchResult(1, WakeWordDetection("keyword", GENERATION, epoch, 0, 1))
    detector.handler = handler
    instance._controller.apply_wake_word = lambda *args, **kwargs: instance._controller.tick(1.5)
    for i in range(4):
        await instance.feed(small(i), voice_activity=False)
    await settle()
    assert instance.state is ActivationState.UNAVAILABLE
    assert len(detector.batches) == 1
    assert detector.closed
    await instance.close()


@pytest.mark.asyncio
async def test_batch_selection_never_crosses_identity_or_sample_gap():
    from main_logic.voice_identity_service.activation_runtime import _WakeQueuedFrame
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector)
    await instance.prepare()
    first = small(0)
    epoch = instance._controller.standby_epoch
    for following in (small(1, start=161),
                      replace(small(1), generation=replace(GENERATION, route=999))):
        instance._wake_queue.clear()
        instance._wake_queue.extend((_WakeQueuedFrame(first, epoch, instance._wake_now()),
                                     _WakeQueuedFrame(following, epoch, instance._wake_now())))
        assert instance._select_wake_batch_locked() == (1, 0.0, "boundary")
    instance._wake_queue.clear()
    await instance.close()


@pytest.mark.asyncio
async def test_tiny_frames_are_bounded_by_frame_count():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector)
    await instance.prepare()
    for i in range(16):
        await instance.feed(small(i, 1), voice_activity=False)
    await settle()
    assert detector.batches == [tuple(small(i, 1) for i in range(16))]
    await instance.close()


@pytest.mark.asyncio
async def test_inflight_batch_capacity_is_not_freed_early():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector, config=VoiceSessionActivationRuntimeConfig(wake_queue_bytes=1280))
    await instance.prepare()
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(frames, epoch):
        entered.set()
        await release.wait()
        return WakeWordBatchResult(len(frames), None)
    detector.handler = handler
    for i in range(4):
        await instance.feed(small(i), voice_activity=False)
    await entered.wait()
    await instance.feed(small(4), voice_activity=False)
    assert instance.state is ActivationState.UNAVAILABLE
    release.set()
    await settle()
    assert len(detector.batches) == 1
    assert instance._wake_task is None
    await instance.close()


@pytest.mark.asyncio
async def test_accepted_mid_batch_hit_keeps_original_replay_once():
    detector = BatchDetector()
    instance, _, sent, statuses = runtime(detector, clock=lambda: .04)
    await instance.prepare()
    async def handler(frames, epoch):
        return WakeWordBatchResult(2, WakeWordDetection("keyword", GENERATION, epoch, 0, 320))
    detector.handler = handler
    for i in range(4):
        await instance.feed(small(i), voice_activity=False)
    await settle()
    assert instance.state is ActivationState.ACTIVE
    assert len(detector.batches) == 1
    assert sent == [small(i) for i in range(4)]
    assert sum(item.reason == "wake_word_detected" for item in statuses) == 1
    assert instance._wake_inflight_bytes == instance._wake_queue_bytes == 0
    await instance.close()


@pytest.mark.asyncio
async def test_old_epoch_failure_does_not_fail_successor():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector)
    await instance.prepare()
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(frames, epoch):
        entered.set()
        await release.wait()
        raise RuntimeError("old worker call failed")
    detector.handler = handler
    for i in range(4):
        await instance.feed(small(i), voice_activity=False)
    await entered.wait()
    # Standby epoch is the authority fence even if a worker finishes later.
    instance._controller._standby_epoch += 1
    release.set()
    await settle()
    assert instance.state is ActivationState.WAITING
    assert not detector.closed
    await instance.close()


@pytest.mark.asyncio
async def test_session_close_cancelling_automatic_retirement_settles_accounting():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector)
    await instance.prepare()
    retiring = asyncio.Event()
    close_calls = 0
    async def close():
        nonlocal close_calls
        close_calls += 1
        if close_calls == 1:
            retiring.set()
            await asyncio.Event().wait()
        detector.closed = True
    async def handler(frames, epoch):
        raise RuntimeError("detector failed")
    detector.close = close
    detector.handler = handler
    for i in range(4):
        await instance.feed(small(i), voice_activity=False)
    await retiring.wait()
    await instance.close()
    assert detector.closed
    assert close_calls == 2
    assert instance._wake_inflight_bytes == instance._wake_queue_bytes == 0
    assert instance._wake_task is None
    assert instance.state is ActivationState.CLOSED


@pytest.mark.asyncio
@pytest.mark.parametrize("score_status,similarity,expected", [
    ("ready", .9, ActivationState.ACTIVE),
    ("ready", .1, ActivationState.ACTIVE),
    ("failed", None, ActivationState.UNAVAILABLE),
])
async def test_speaker_result_during_batch_inference_keeps_existing_priority(score_status, similarity, expected):
    from main_logic.voice_identity_service.activation_scoring import ActivationScoreStatus
    detector = BatchDetector()
    instance, scorer, sent, statuses = runtime(detector, clock=lambda: .04,
        config=VoiceSessionActivationRuntimeConfig(first_checkpoint_seconds=.02, second_checkpoint_seconds=.03))
    await instance.prepare()
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(frames, epoch):
        entered.set()
        await release.wait()
        return WakeWordBatchResult(2, WakeWordDetection("keyword", GENERATION, epoch, 0, 320))
    detector.handler = handler
    for i in range(4):
        await instance.feed(small(i), voice_activity=True)
    await entered.wait()
    assert scorer.calls
    await scorer.results.put((ActivationScoreStatus(score_status), similarity))
    await settle()
    release.set()
    await settle()
    assert instance.state is expected
    assert len({frame.sequence for frame in sent}) == len(sent)
    assert sum(item.reason == "wake_word_detected" for item in statuses) <= 1
    if expected is ActivationState.UNAVAILABLE:
        assert sent == []
    await instance.close()


@pytest.mark.asyncio
async def test_epoch_change_wakes_waiter_and_discards_old_prefix():
    detector = BatchDetector()
    instance, _, _, _ = runtime(detector)
    await instance.prepare()
    entered = asyncio.Event()
    async def wait(timeout):
        entered.set()
        await instance._wake_changed.wait()
    instance._wait_wake_changed = wait
    await instance.feed(small(0), voice_activity=False)
    await entered.wait()
    instance._controller._standby_epoch += 1
    await instance.tick()
    await settle()
    assert detector.batches == []
    assert instance._wake_queue_bytes == 0
    assert instance._wake_task is None
    await instance.close()
