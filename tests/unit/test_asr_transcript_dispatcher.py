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
