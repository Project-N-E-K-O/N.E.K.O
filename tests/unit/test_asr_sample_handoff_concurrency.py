"""Only model/provider I/O is fake; delivery and handoff use the real runtime."""

import asyncio

import pytest

from tests.unit.test_asr_sample_handoff import SampleInput, resampled_chunk_sizes, uploaded
from tests.unit.test_voice_admission_integration import make_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("final_during_detection", [False, True])
async def test_endpoint_during_detector_await_does_not_lose_accepted_audio(
    final_during_detection, monkeypatch
):
    runtime, callbacks, session, vad, token = make_runtime(False)
    source = SampleInput(runtime, vad, token, resampled_chunk_sizes())
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    try:
        first = await source.send([0.9] * 12 + [0.1] * 10)
        real_feed = runtime._asr_detector.feed

        async def blocked_feed(*args, **kwargs):
            result = await real_feed(*args, **kwargs)
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(runtime._asr_detector, "feed", blocked_feed)
        task = asyncio.create_task(source.send([0.9]))
        await asyncio.wait_for(entered.wait(), 1)
        await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        if final_during_detection:
            await runtime._handle_independent_asr_final(
                "first", runtime._asr_session_epoch, "qwen"
            )
            await runtime.wait_transcript_idle()
        release.set()
        crossing = await asyncio.wait_for(task, 1)
        monkeypatch.setattr(runtime._asr_detector, "feed", real_feed)
        if not final_during_detection:
            await runtime._handle_independent_asr_final(
                "first", runtime._asr_session_epoch, "qwen"
            )
            await runtime.wait_transcript_idle()
        following = await source.send([0.9] * 12)
        callbacks.on_failure.assert_not_awaited()
        assert uploaded(session) == first + crossing + following
        assert callbacks.on_final.await_count == 1
        assert callbacks.on_prepare_turn.await_count == 2
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("already_admitted", [False, True])
async def test_closed_session_cannot_upload_a_pending_detector_result(
    monkeypatch, already_admitted
):
    runtime, callbacks, session, vad, token = make_runtime(False)
    source = SampleInput(runtime, vad, token, resampled_chunk_sizes())
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    try:
        await source.send([0.9] * (12 if already_admitted else 7))
        real_feed = runtime._asr_detector.feed

        async def blocked_feed(*args, **kwargs):
            result = await real_feed(*args, **kwargs)
            entered.set()
            await release.wait()
            return result

        monkeypatch.setattr(runtime._asr_detector, "feed", blocked_feed)
        task = asyncio.create_task(source.send([0.9]))
        await asyncio.wait_for(entered.wait(), 1)
        # An active turn can commit this frame before awaiting detection.
        # Closing forbids later writes; it does not undo valid earlier writes.
        await runtime._asr_audio_dispatcher.wait_idle()
        before_close = uploaded(session)
        old_epoch = runtime._asr_session_epoch
        await runtime.close()
        release.set()
        await asyncio.wait_for(task, 1)
        await runtime._handle_independent_asr_final("late", old_epoch, "qwen")
        assert uploaded(session) == before_close
        assert callbacks.on_prepare_turn.await_count == int(already_admitted)
        if not already_admitted:
            assert before_close == b""
        callbacks.on_final.assert_not_awaited()
        callbacks.on_failure.assert_not_awaited()
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("smart_turn", [False, True])
async def test_short_pause_preserves_continuous_audio_without_a_new_turn(smart_turn):
    runtime, callbacks, session, vad, token = make_runtime(False, smart_turn=smart_turn)
    # Provider and semantic paths both retain sub-endpoint pauses.
    source = SampleInput(runtime, vad, token, resampled_chunk_sizes())
    try:
        audio = await source.send([0.9] * 12 + [0.1] * 3 + [0.9] * 12)
        detector = runtime._asr_detector
        if detector._semantic_adapter is not None:
            await detector._semantic_adapter.wait_idle()
        await runtime._asr_detector_dispatcher.wait_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        callbacks.on_failure.assert_not_awaited()
        callbacks.on_prepare_turn.assert_awaited_once()
        callbacks.on_final.assert_not_awaited()
        assert uploaded(session) == audio
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_endpoint_waiting_for_detector_lock_seals_both_committed_blocks(monkeypatch):
    runtime, callbacks, session, vad, token = make_runtime(False)
    source = SampleInput(runtime, vad, token, resampled_chunk_sizes())
    tasks = []
    lock_held = False
    try:
        first = await source.send([0.9] * 12 + [0.1] * 10)
        detector = runtime._asr_detector
        entered_feed = [asyncio.Event(), asyncio.Event()]
        entered_seal = asyncio.Event()
        feed_count = 0
        real_feed, real_seal = detector.feed, detector.seal_provider_candidate

        async def observe_feed(*args, **kwargs):
            nonlocal feed_count
            entered_feed[feed_count].set()
            feed_count += 1
            return await real_feed(*args, **kwargs)

        async def observe_seal(*args, **kwargs):
            entered_seal.set()
            return await real_seal(*args, **kwargs)

        monkeypatch.setattr(detector, "feed", observe_feed)
        monkeypatch.setattr(detector, "seal_provider_candidate", observe_seal)
        await detector._lock.acquire()
        lock_held = True
        first_pending = asyncio.create_task(source.send([0.9]))
        tasks.append(first_pending)
        await asyncio.wait_for(entered_feed[0].wait(), 1)
        endpoint = asyncio.create_task(
            runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        )
        tasks.append(endpoint)
        await asyncio.wait_for(entered_seal.wait(), 1)
        second_pending = asyncio.create_task(source.send([0.9]))
        tasks.append(second_pending)
        await asyncio.wait_for(entered_feed[1].wait(), 1)
        committed_boundary = source.position
        detector._lock.release()
        lock_held = False
        crossing_first, _, crossing_second = await asyncio.wait_for(
            asyncio.gather(first_pending, endpoint, second_pending), 1
        )
        monkeypatch.setattr(detector, "feed", real_feed)
        monkeypatch.setattr(detector, "seal_provider_candidate", real_seal)
        await runtime._handle_independent_asr_final(
            "first", runtime._asr_session_epoch, "qwen"
        )
        await runtime.wait_transcript_idle()
        following = await source.send([0.9] * 12)
        callbacks.on_failure.assert_not_awaited()
        assert uploaded(session) == first + crossing_first + crossing_second + following
        assert callbacks.on_prepare_turn.await_count == 2
        evidence = runtime._asr_admission_evidence[runtime._asr_prepared_turn_token]
        assert evidence.audio_start_sample >= committed_boundary
    finally:
        if lock_held:
            detector._lock.release()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_callback", ["lifecycle", "prepare"])
async def test_pending_activation_callback_cannot_lose_or_reorder_prefix(pause_callback):
    runtime, callbacks, session, vad, token = make_runtime(False)
    source = SampleInput(runtime, vad, token, resampled_chunk_sizes())
    entered, release = asyncio.Event(), asyncio.Event()
    final_task = None
    try:
        first = await source.send([0.9] * 12 + [0.1] * 10)
        await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        pending = await source.send([0.9] * 9)
        assert runtime._asr_lifecycle.has_pending_turn
        assert uploaded(session) == first

        async def paused_lifecycle(notification):
            if notification.state == "active" and not entered.is_set():
                entered.set()
                await release.wait()

        async def paused_prepare(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                await release.wait()
            return True

        if pause_callback == "lifecycle":
            callbacks.on_lifecycle.side_effect = paused_lifecycle
        else:
            callbacks.on_prepare_turn.side_effect = paused_prepare
        final_task = asyncio.create_task(runtime._handle_independent_asr_final(
            "first", runtime._asr_session_epoch, "qwen"
        ))
        await asyncio.wait_for(entered.wait(), 1)
        following = await asyncio.wait_for(source.send([0.9]), 1)
        release.set()
        await asyncio.wait_for(final_task, 1)
        await runtime.wait_transcript_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        callbacks.on_failure.assert_not_awaited()
        assert uploaded(session) == first + pending + following
        assert callbacks.on_prepare_turn.await_count == 2
        callbacks.on_final.assert_awaited_once()
    finally:
        release.set()
        if final_task is not None and not final_task.done():
            final_task.cancel()
            await asyncio.gather(final_task, return_exceptions=True)
        await runtime.close()
