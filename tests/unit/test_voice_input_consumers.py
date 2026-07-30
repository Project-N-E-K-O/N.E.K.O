from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_input.consumers.core_chat import CoreChatVoiceInputConsumer
from main_logic.voice_input.consumers.game import GameVoiceInputConsumer
from main_logic.voice_turn.contracts import (
    VoiceIngressToken,
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)


pytestmark = pytest.mark.asyncio


def _token(turn_id: int = 1) -> VoiceTurnToken:
    return VoiceTurnToken(
        ingress=VoiceIngressToken(
            connection_id="connection",
            lease_generation=7,
            route_generation=9,
            audio_generation=13,
            session_epoch=11,
        ),
        turn_id=turn_id,
    )


async def test_core_consumer_cancels_the_session_captured_at_prepare() -> None:
    original_session = object()
    current_session = [original_session]
    prepared = AsyncMock(return_value=True)
    cancelled = AsyncMock()
    consumer = CoreChatVoiceInputConsumer(
        session_ref=lambda: current_session[0],
        on_prepare=prepared,
        on_partial_event=AsyncMock(),
        on_final_event=AsyncMock(),
        on_cancelled_event=cancelled,
    )
    token = _token()

    assert await consumer.prepare_turn(token) is True
    current_session[0] = object()
    await consumer.on_cancelled(token, "consumer_switched")

    context = cancelled.await_args.args[0]
    assert context.token == token
    assert context.external_turn_id == "asr-11-1"
    assert context.session_ref is original_session
    assert cancelled.await_args.args[1] == "consumer_switched"


async def test_core_consumer_final_uses_prepared_context_once() -> None:
    original_session = object()
    final = AsyncMock()
    cancelled = AsyncMock()
    consumer = CoreChatVoiceInputConsumer(
        session_ref=lambda: original_session,
        on_prepare=AsyncMock(return_value=True),
        on_partial_event=AsyncMock(),
        on_final_event=final,
        on_cancelled_event=cancelled,
    )
    token = _token()
    event = VoiceTranscriptEvent(turn_token=token, provider="qwen", text="hello")

    assert await consumer.prepare_turn(token) is True
    await consumer.on_final(event)
    await consumer.on_cancelled(token, "late_cancel")

    assert final.await_count == 1
    assert final.await_args.args[0] == event
    assert final.await_args.args[1].session_ref is original_session
    cancelled.assert_not_awaited()


async def test_core_consumer_rejected_prepare_retains_context_for_cancel() -> None:
    session = object()
    cancelled = AsyncMock()
    consumer = CoreChatVoiceInputConsumer(
        session_ref=lambda: session,
        on_prepare=AsyncMock(return_value=False),
        on_partial_event=AsyncMock(),
        on_final_event=AsyncMock(),
        on_cancelled_event=cancelled,
    )
    token = _token()

    assert await consumer.prepare_turn(token) is False
    await consumer.on_cancelled(token, "prepare_rejected")
    await consumer.on_cancelled(token, "duplicate_cancel")

    cancelled.assert_awaited_once()
    context, reason = cancelled.await_args.args
    assert context.token == token
    assert context.session_ref is session
    assert reason == "prepare_rejected"


async def test_core_consumer_cancelled_prepare_retains_context_for_cancel() -> None:
    session = object()
    cancelled = AsyncMock()
    consumer = CoreChatVoiceInputConsumer(
        session_ref=lambda: session,
        on_prepare=AsyncMock(side_effect=asyncio.CancelledError),
        on_partial_event=AsyncMock(),
        on_final_event=AsyncMock(),
        on_cancelled_event=cancelled,
    )
    token = _token()

    with pytest.raises(asyncio.CancelledError):
        await consumer.prepare_turn(token)
    await consumer.on_cancelled(token, "prepare_cancelled")

    context, reason = cancelled.await_args.args
    assert context.token == token
    assert context.session_ref is session
    assert reason == "prepare_cancelled"


async def test_game_consumer_uses_token_derived_request_id(monkeypatch) -> None:
    routed = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda name: name == "Lan",
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        routed,
    )
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")
    token = _token(turn_id=3)
    event = VoiceTranscriptEvent(turn_token=token, provider="qwen", text="play")

    assert consumer.is_available() is True
    assert await consumer.prepare_turn(token) is True
    await consumer.on_final(event)

    routed.assert_awaited_once_with("Lan", "play", request_id="asr-11-3")


async def test_game_consumer_surfaces_route_delivery_failure(monkeypatch) -> None:
    routed = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        routed,
    )
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")
    event = VoiceTranscriptEvent(
        turn_token=_token(turn_id=4),
        provider="qwen",
        text="play",
    )

    with pytest.raises(RuntimeError, match="GAME_VOICE_TRANSCRIPT_NOT_ROUTED"):
        await consumer.on_final(event)

    routed.assert_awaited_once_with("Lan", "play", request_id="asr-11-4")


async def test_game_consumer_is_fail_closed_when_route_is_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: False,
    )
    consumer = GameVoiceInputConsumer(lanlan_name=lambda: "Lan")

    assert consumer.is_available() is False
    assert await consumer.prepare_turn(_token()) is False
