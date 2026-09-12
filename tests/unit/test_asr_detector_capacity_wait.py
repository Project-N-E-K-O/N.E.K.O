"""Backpressure waits for admission space without waiting for evaluation idle."""

import asyncio
from types import SimpleNamespace

import pytest

from main_logic.asr_client.endpointing.detector import DetectorDurationQueue
from main_logic.asr_client.endpointing.detector_runtime import DetectorRuntime


@pytest.mark.asyncio
@pytest.mark.parametrize("dequeue", ["get", "get_nowait"])
async def test_capacity_released_on_dequeue_before_processing_finishes(dequeue):
    queue = DetectorDurationQueue(capacity_us=10000, max_frames=1)
    queue.put_audio_nowait("first", duration_us=10000)
    waiter = asyncio.create_task(
        queue.wait_audio_capacity(10000, asyncio.get_running_loop().time() + 1)
    )
    await asyncio.sleep(0)
    assert not waiter.done()
    item = await queue.get() if dequeue == "get" else queue.get_nowait()
    assert item == "first"
    assert await asyncio.wait_for(waiter, 0.2)
    assert queue._unfinished_tasks == 1  # inference still in flight
    queue.task_done()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["discard", "invalidate", "cancel", "timeout"])
async def test_capacity_wait_is_bounded_and_old_waiter_does_not_reenter(action):
    queue = DetectorDurationQueue(capacity_us=10000, max_frames=1)
    queue.put_audio_nowait("first", duration_us=10000)
    waiter = asyncio.create_task(
        queue.wait_audio_capacity(10000, asyncio.get_running_loop().time() + 0.05)
    )
    await asyncio.sleep(0)
    if action == "discard":
        queue.discard_audio()
    elif action == "invalidate":
        queue.invalidate_capacity_waiters()
    elif action == "cancel":
        waiter.cancel()
    if action == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await waiter
    else:
        assert not await asyncio.wait_for(waiter, 0.2)
    assert queue.audio_frames == (0 if action == "discard" else 1)
    assert not await queue.wait_audio_capacity(
        10001, asyncio.get_running_loop().time() + 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["epoch", "adapter", "closed", "none"])
async def test_runtime_capacity_bridge_rechecks_detector_ownership(change):
    queue = DetectorDurationQueue(capacity_us=10000, max_frames=1)
    queue.put_audio_nowait("first", duration_us=10000)
    adapter = SimpleNamespace(_queue=queue, failed=False)
    runtime = object.__new__(DetectorRuntime)
    runtime._semantic_adapter = adapter
    runtime._detector_epoch = 2
    runtime._closed = False
    waiter = asyncio.create_task(
        runtime.wait_audio_capacity(
            bytes(320),
            sample_rate_hz=16000,
            deadline=asyncio.get_running_loop().time() + 1,
        )
    )
    await asyncio.sleep(0)
    if change == "epoch":
        runtime._detector_epoch += 1
    elif change == "adapter":
        runtime._semantic_adapter = SimpleNamespace(
            _queue=DetectorDurationQueue(), failed=False
        )
    elif change == "closed":
        runtime._closed = True
    queue.get_nowait()
    assert await asyncio.wait_for(waiter, 0.2) is (change == "none")
