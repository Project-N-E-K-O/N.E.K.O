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
