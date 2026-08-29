from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.lifecycle import (
    FinalKey,
    VoiceIngressToken,
    VoiceTurnToken,
)
from main_logic.asr_client.transcript import (
    TranscriptDispatcher,
    TranscriptEnvelope,
)


pytestmark = pytest.mark.asyncio


def _envelope(turn_id: int) -> TranscriptEnvelope:
    token = VoiceTurnToken(
        ingress=VoiceIngressToken(1, "socket", 2, 3, 4),
        turn_id=turn_id,
    )
    return TranscriptEnvelope(token, "qwen", f"text-{turn_id}")


async def test_pending_delivery_spans_reservation_queue_and_active_dispatch() -> None:
    dispatch_started = asyncio.Event()
    release_dispatch = asyncio.Event()

    async def dispatch(_envelope: TranscriptEnvelope) -> None:
        dispatch_started.set()
        await release_dispatch.wait()

    dispatcher = TranscriptDispatcher(dispatch)
    envelope = _envelope(1)

    assert dispatcher.has_pending_delivery is False
    assert dispatcher.try_reserve(envelope.final_key) is True
    assert dispatcher.has_pending_delivery is True

    dispatcher.submit(envelope)
    assert dispatcher.has_pending_delivery is True
    await dispatch_started.wait()
    assert dispatcher.has_pending_delivery is True

    release_dispatch.set()
    await dispatcher.wait_idle()
    assert dispatcher.has_pending_delivery is False


async def test_dispatcher_reserves_capacity_and_serializes_delivery() -> None:
    release_first = asyncio.Event()
    delivered: list[int] = []

    async def dispatch(envelope: TranscriptEnvelope) -> None:
        if envelope.turn_token.turn_id == 1:
            await release_first.wait()
        delivered.append(envelope.turn_token.turn_id)

    dispatcher = TranscriptDispatcher(dispatch, capacity=2)
    first = _envelope(1)
    second = _envelope(2)

    assert dispatcher.try_reserve(first.final_key) is True
    assert dispatcher.try_reserve(second.final_key) is True
    assert dispatcher.try_reserve(FinalKey(1, "socket", 2, 3, 3)) is False
    dispatcher.submit(first)
    dispatcher.submit(second)
    await asyncio.sleep(0)
    assert delivered == []

    release_first.set()
    await dispatcher.wait_idle()
    assert delivered == [1, 2]


async def test_dispatcher_invalidation_cancels_old_core_work() -> None:
    blocked = asyncio.Event()

    async def wait_forever(_envelope: TranscriptEnvelope) -> None:
        await blocked.wait()

    dispatch = AsyncMock(side_effect=wait_forever)
    dispatcher = TranscriptDispatcher(dispatch)
    envelope = _envelope(1)
    assert dispatcher.try_reserve(envelope.final_key) is True
    dispatcher.submit(envelope)
    await asyncio.sleep(0)

    dispatcher.invalidate_all()
    await dispatcher.wait_idle()

    assert dispatch.await_count == 1


async def test_old_worker_unwind_cannot_clear_new_active_dispatch() -> None:
    old_cancelled = asyncio.Event()
    release_old = asyncio.Event()
    new_started = asyncio.Event()
    release_new = asyncio.Event()

    async def dispatch(envelope: TranscriptEnvelope) -> None:
        if envelope.turn_token.turn_id == 1:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                old_cancelled.set()
                await release_old.wait()
                raise
        new_started.set()
        await release_new.wait()

    dispatcher = TranscriptDispatcher(dispatch, capacity=1)
    old_envelope = _envelope(1)
    new_envelope = _envelope(2)
    third_envelope = _envelope(3)

    assert dispatcher.try_reserve(old_envelope.final_key) is True
    dispatcher.submit(old_envelope)
    await asyncio.sleep(0)
    old_worker = dispatcher._worker

    dispatcher.invalidate_all()
    assert dispatcher.try_reserve(new_envelope.final_key) is True
    dispatcher.submit(new_envelope)
    await asyncio.wait_for(old_cancelled.wait(), 1)
    await asyncio.wait_for(new_started.wait(), 1)
    assert dispatcher._active is new_envelope

    wait_idle = asyncio.create_task(dispatcher.wait_idle())
    await asyncio.sleep(0)
    assert wait_idle.done() is False
    assert dispatcher.try_reserve(third_envelope.final_key) is False

    release_old.set()
    assert old_worker is not None
    await asyncio.wait_for(old_worker, 1)
    assert dispatcher._active is new_envelope
    assert wait_idle.done() is False

    release_new.set()
    await asyncio.wait_for(wait_idle, 1)
    assert dispatcher._active is None


async def test_wait_idle_returns_while_next_turn_slot_is_reserved() -> None:
    # Pins the idle predicate against a plausible-looking "fix": folding
    # self._reservations into _set_idle_if_empty. A live session always holds
    # the next turn's reservation while the previous final drains
    # (runtime.py _handle_independent_asr_final -> _activate_pending_
    # independent_turn -> _prepare_independent_asr_turn), so a reservation-
    # aware predicate never settles and wait_idle() hangs forever.
    dispatcher = TranscriptDispatcher(AsyncMock(), capacity=2)
    first = _envelope(1)
    second = _envelope(2)

    assert dispatcher.try_reserve(first.final_key) is True
    assert dispatcher.try_reserve(second.final_key) is True
    dispatcher.submit(first)

    await asyncio.wait_for(dispatcher.wait_idle(), 1)
    assert second.final_key in dispatcher._reservations


async def test_invalidate_all_from_inside_dispatch_does_not_cancel_its_caller() -> None:
    """Teardown paths run ON the worker; cancelling it truncates their cleanup.

    An independent-ASR final that discovers the session is unusable calls
    `_close_independent_asr()`, which reaches `invalidate_all()` while still
    executing inside `_run()`. Cancelling the current worker there makes the
    very next await raise CancelledError, so the remaining detector/provider
    cleanup and the frontend "session ended" notification never happen.
    """
    steps: list[str] = []
    captured: dict[str, asyncio.Task] = {}

    async def dispatch(_envelope: TranscriptEnvelope) -> None:
        steps.append("start")
        captured["worker"] = asyncio.current_task()
        dispatcher.invalidate_all()
        # 收口路径在 invalidate_all 之后还有若干 await —— 它们必须照常跑完。
        await asyncio.sleep(0)
        steps.append("after-await")
        await asyncio.sleep(0)
        steps.append("cleanup-done")

    dispatcher = TranscriptDispatcher(dispatch)
    envelope = _envelope(1)
    assert dispatcher.try_reserve(envelope.final_key) is True
    dispatcher.submit(envelope)

    for _ in range(50):
        if "cleanup-done" in steps:
            break
        await asyncio.sleep(0.01)

    assert steps == ["start", "after-await", "cleanup-done"]

    # 跑完手头这条之后必须**退出**：再回去 await queue.get() 就成了和新 worker
    # 并存的僵尸，两个消费者抢同一个队列。
    worker = captured["worker"]
    for _ in range(50):
        if worker.done():
            break
        await asyncio.sleep(0.01)
    assert worker.done(), "the self-invalidated worker must exit after its envelope"
    assert not worker.cancelled()


async def test_invalidate_all_still_cancels_a_worker_from_outside() -> None:
    """The normal identity-barrier use must keep cancelling the worker."""
    started = asyncio.Event()
    finished = asyncio.Event()

    async def dispatch(_envelope: TranscriptEnvelope) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    dispatcher = TranscriptDispatcher(dispatch)
    envelope = _envelope(1)
    assert dispatcher.try_reserve(envelope.final_key) is True
    dispatcher.submit(envelope)
    await asyncio.wait_for(started.wait(), 1.0)

    dispatcher.invalidate_all()

    await asyncio.wait_for(finished.wait(), 1.0)
