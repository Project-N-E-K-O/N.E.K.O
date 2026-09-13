"""Gemini terminal events must not borrow a successor's native input marker."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.omni_realtime_client import _gemini_support, _transport
from tests.fake_clock import patch_module_clock
from tests.unit.test_external_visual_delivery import _make_client
from tests.unit.test_voice_session import (
    _gemini_response, _gemini_output_text, _gemini_model_turn_audio,
)


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("interruption", ["local", "provider"])
async def test_cancelled_tail_cannot_close_successor_input_and_next_turn_recovers(interruption, monkeypatch):
    clock = [1000.0]
    for module in (_gemini_support, _transport):
        patch_module_clock(monkeypatch, module, time=lambda: clock[0])
    client = _make_client("gemini", "gemini-test")
    session = SimpleNamespace(send_realtime_input=AsyncMock())
    client._gemini_session = session
    client.on_text_delta = AsyncMock()
    generation = client._connection_generation

    async def receive(**fields):
        await client._process_gemini_response(
            _gemini_response(**fields), provider_session=session,
            connection_generation=generation,
        )

    loud = (3000).to_bytes(2, "little") * 1600
    try:
        await client.stream_audio(loud)
        await receive(output_transcription=_gemini_output_text("first response"))
        old_sequence = client._voice_handoff_response_input_sequence
        clock[0] += 0.1
        await client.stream_audio(loud)
        successor_sequence = client._voice_handoff_input_sequence
        assert successor_sequence > old_sequence
        if interruption == "local":
            await client.handle_interruption()
        else:
            await receive(interrupted=True)

        await receive(output_transcription=_gemini_output_text("cancelled tail"))
        assert client._interrupted
        await receive(turn_complete=True)
        assert client._voice_handoff_input_open
        assert not client.can_handoff_voice_input()
        assert client._voice_handoff_response_input_sequence == old_sequence

        # Existing turn recognition accepts new input transcription followed
        # by content. Its own terminal must recover normal handoff permission.
        await receive(input_transcription=_gemini_output_text("next input"))
        await receive(output_transcription=_gemini_output_text("next response"))
        assert not client._interrupted
        await receive(turn_complete=True)
        assert client._voice_handoff_response_input_sequence == successor_sequence
        assert client.can_handoff_voice_input()
        assert session.send_realtime_input.await_count == 2
    finally:
        await client.close()


async def test_normal_gemini_terminal_closes_its_own_input():
    client = _make_client("gemini", "gemini-test")
    session = SimpleNamespace(send_realtime_input=AsyncMock())
    client._gemini_session = session
    try:
        await client.stream_audio((3000).to_bytes(2, "little") * 1600)
        assert not client.can_handoff_voice_input()
        await client._process_gemini_response(
            _gemini_response(output_transcription=_gemini_output_text("response"),
                             turn_complete=True),
            provider_session=session, connection_generation=client._connection_generation,
        )
        assert client.can_handoff_voice_input()
    finally:
        await client.close()


@pytest.mark.parametrize("callback", ["on_new_message", "on_text_delta", "on_audio_delta"])
@pytest.mark.parametrize("change", ["interrupt", "new-input", "connection", "cancel-task"])
async def test_terminal_await_cannot_close_revoked_or_successor_input(callback, change):
    client = _make_client("gemini", "gemini-test")
    session = SimpleNamespace(send_realtime_input=AsyncMock())
    client._gemini_session = session
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait_in_callback(*args):
        entered.set()
        await release.wait()

    setattr(client, callback, wait_in_callback)
    task = None
    loud = (3000).to_bytes(2, "little") * 1600
    try:
        await client.stream_audio(loud)
        task = asyncio.create_task(client._process_gemini_response(
            _gemini_response(output_transcription=_gemini_output_text("response"),
                             model_turn=_gemini_model_turn_audio(), turn_complete=True),
            provider_session=session, connection_generation=client._connection_generation,
        ))
        async with asyncio.timeout(2):
            await entered.wait()
        if change == "interrupt":
            await client.handle_interruption()
        elif change == "cancel-task":
            task.cancel()
        else:
            if change == "connection":
                client._gemini_session = SimpleNamespace(send_realtime_input=AsyncMock())
                client._connection_generation += 1
            await client.stream_audio(loud)
        release.set()
        if change == "cancel-task":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        assert client._voice_handoff_input_open
        assert not client.can_handoff_voice_input()
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await client.close()
