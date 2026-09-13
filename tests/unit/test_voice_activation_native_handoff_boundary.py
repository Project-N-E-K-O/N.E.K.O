"""Native input-boundary regressions for voice-activation session handoff."""

from __future__ import annotations

import asyncio
import base64
import math
from unittest.mock import AsyncMock

import pytest

from main_logic.omni_realtime_client import OmniRealtimeClient
from tests.unit.test_realtime_arbiter_native_path import (
    _wired_client,
    _settle,
    _finish_loop,
)


def _client(*, server_vad: bool) -> OmniRealtimeClient:
    client = object.__new__(OmniRealtimeClient)
    client._connection_generation = 7
    client._fatal_error_occurred = False
    client._is_gemini = False
    client.ws = object()
    client._has_server_vad = server_vad
    client._audio_in_buffer = False
    client._voice_handoff_input_open = False
    client._voice_handoff_input_sequence = 0
    client._voice_handoff_input_generation = 7
    client._voice_handoff_response_input_sequence = 0
    client._rnnoise_vad_active = True
    client._client_vad_active = True
    client._speech_detect_start = 1.0
    client._current_turn_host_id = "turn-1"
    client._turn_epoch = 0
    client._read_host_turn_id = lambda: "turn-1"
    client.on_response_done = None
    client.on_sid_rotate = None
    return client


def test_rnnoise_availability_and_vad_grace_do_not_permanently_block_handoff() -> None:
    client = _client(server_vad=True)

    # These are proactive/silence heuristics, not an open native utterance.
    # A short response commonly completes while both remain true.
    assert client._rnnoise_vad_active is True
    assert client._client_vad_active is True
    assert client._speech_detect_start == 1.0
    assert client.can_handoff_voice_input() is True


def test_loud_input_blocks_until_the_real_server_vad_boundary() -> None:
    client = _client(server_vad=True)

    client._note_voice_handoff_input_open()
    assert client.can_handoff_voice_input() is False

    client._audio_in_buffer = True
    client._note_voice_handoff_input_boundary()
    assert client.can_handoff_voice_input() is False

    client._audio_in_buffer = False
    assert client.can_handoff_voice_input() is False
    client._begin_response_lifecycle("response")
    assert client.can_handoff_voice_input() is True


@pytest.mark.asyncio
async def test_owned_no_server_vad_terminal_opens_the_next_handoff_immediately() -> (
    None
):
    client = _client(server_vad=False)
    client._note_voice_handoff_input_open()
    client._voice_handoff_response_input_sequence = client._voice_handoff_input_sequence

    await client._notify_turn_finished()

    assert client.can_handoff_voice_input() is True


@pytest.mark.asyncio
async def test_old_terminal_cannot_clear_new_no_server_vad_input() -> None:
    client = _client(server_vad=False)
    client._note_voice_handoff_input_open()
    client._voice_handoff_response_input_sequence = client._voice_handoff_input_sequence
    client._note_voice_handoff_input_open()

    await client._notify_turn_finished()

    assert client.can_handoff_voice_input() is False


@pytest.mark.asyncio
async def test_arbiter_stuck_release_is_not_a_provider_input_boundary() -> None:
    client = _client(server_vad=False)
    client._note_voice_handoff_input_open()
    client._voice_handoff_response_input_sequence = client._voice_handoff_input_sequence

    await client._notify_turn_finished(step_timeout=0.1)

    assert client.can_handoff_voice_input() is False


def test_replacement_generation_does_not_inherit_old_open_marker() -> None:
    client = _client(server_vad=False)
    client._note_voice_handoff_input_open()
    assert client.can_handoff_voice_input() is False

    client._connection_generation += 1

    assert client.can_handoff_voice_input() is True


@pytest.mark.asyncio
async def test_manual_commit_closes_only_the_input_sequence_it_committed() -> None:
    client = _client(server_vad=False)
    client.turn_detection_mode = type("Mode", (), {"value": "manual"})()
    # Compare equal to the production enum without importing provider config.
    from main_logic.omni_realtime_client._shared import TurnDetectionMode

    client.turn_detection_mode = TurnDetectionMode.MANUAL
    client.note_user_turn_started = lambda: None
    client._pending_input_route_identity_commit = lambda: object()
    client._apply_input_route_identity_commit = lambda _pending: None
    client._clear_uplink_resampler = lambda: None
    sent = AsyncMock()
    ticket = type("Ticket", (), {"sent": sent()})()
    client._response_arbiter = type(
        "Arbiter",
        (),
        {"enqueue": AsyncMock(return_value=ticket)},
    )()
    client._note_voice_handoff_input_open()

    await client.signal_user_activity_end()

    assert client.can_handoff_voice_input() is False
    client._begin_response_lifecycle("response")
    assert client.can_handoff_voice_input() is True


@pytest.mark.asyncio
@pytest.mark.parametrize("new_audio_waits_for_admission", [False, True])
@pytest.mark.parametrize(
    "api_type,model",
    [
        ("qwen", "qwen-omni-turbo-realtime"),
        ("openai", "gpt-realtime"),
        ("step", "step-audio-2-mini"),
        ("glm", "glm-realtime"),
    ],
)
async def test_late_server_stop_cannot_clear_new_pcm_and_next_real_boundary_recovers(
    new_audio_waits_for_admission,
    api_type,
    model,
) -> None:
    client, socket = _wired_client(api_type=api_type, model=model)
    receive_loop = asyncio.create_task(client.handle_messages())
    loud = (2000).to_bytes(2, "little", signed=True) * 1600
    silent = bytes(3200)
    pending_audio = None
    admission = client._ensure_turn_admission_lock()
    try:
        await client.stream_audio(loud)
        socket.feed(
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "old",
                "audio_start_ms": 0,
            }
        )
        await _settle()
        await client.stream_audio(silent)
        old_end_ms = math.ceil(
            sum(
                len(base64.b64decode(event["audio"]))
                for event in socket.sent
                if event["type"] == "input_audio_buffer.append"
            )
            * 1000
            / (2 * client._uplink_sample_rate)
        )
        if new_audio_waits_for_admission:
            await admission.acquire()
            pending_audio = asyncio.create_task(client.stream_audio(loud))
            await _settle()
            assert not pending_audio.done()
        else:
            await client.stream_audio(loud)
        socket.feed(
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "old",
                "audio_end_ms": old_end_ms,
            }
        )
        socket.feed({"type": "response.created", "response": {"id": "old-response"}})
        socket.feed({"type": "response.done", "response": {"id": "old-response"}})
        await _settle()
        assert client.can_handoff_voice_input() is False
        if pending_audio is not None:
            admission.release()
            await pending_audio
        socket.feed(
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "new",
                "audio_start_ms": 200,
            }
        )
        await _settle()
        await client.stream_audio(silent)
        new_end_ms = math.ceil(
            sum(
                len(base64.b64decode(event["audio"]))
                for event in socket.sent
                if event["type"] == "input_audio_buffer.append"
            )
            * 1000
            / (2 * client._uplink_sample_rate)
        )
        socket.feed(
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "new",
                "audio_end_ms": new_end_ms,
            }
        )
        socket.feed({"type": "response.created", "response": {"id": "new-response"}})
        socket.feed({"type": "response.done", "response": {"id": "new-response"}})
        await _settle()
        assert client.can_handoff_voice_input() is True
    finally:
        if admission.locked():
            admission.release()
        if pending_audio is not None:
            await asyncio.gather(pending_audio, return_exceptions=True)
        await _finish_loop(socket, receive_loop)


@pytest.mark.asyncio
@pytest.mark.parametrize("end_ms", [None, -1, "200", True])
async def test_missing_or_invalid_server_end_is_not_a_handoff_boundary(end_ms):
    client, socket = _wired_client()
    receive_loop = asyncio.create_task(client.handle_messages())
    try:
        await client.stream_audio((2000).to_bytes(2, "little", signed=True) * 1600)
        socket.feed(
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "speech",
                "audio_start_ms": 0,
            }
        )
        socket.feed(
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "speech",
                "audio_end_ms": end_ms,
            }
        )
        socket.feed({"type": "response.created", "response": {"id": "response"}})
        socket.feed({"type": "response.done", "response": {"id": "response"}})
        await _settle()
        assert not client.can_handoff_voice_input()
        # An exact later endpoint can recover; no time-based grace is used.
        socket.feed(
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "speech",
                "audio_end_ms": 100,
            }
        )
        await _settle()
        assert client.can_handoff_voice_input()
    finally:
        await _finish_loop(socket, receive_loop)


@pytest.mark.asyncio
async def test_old_item_stop_cannot_clear_a_new_server_utterance():
    client, socket = _wired_client()
    receive_loop = asyncio.create_task(client.handle_messages())
    try:
        await client.stream_audio((2000).to_bytes(2, "little", signed=True) * 1600)
        socket.feed(
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "new",
                "audio_start_ms": 0,
            }
        )
        socket.feed(
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "old",
                "audio_end_ms": 100,
            }
        )
        socket.feed({"type": "response.created", "response": {"id": "old-response"}})
        socket.feed({"type": "response.done", "response": {"id": "old-response"}})
        await _settle()
        assert client._audio_in_buffer
        assert not client.can_handoff_voice_input()
        socket.feed(
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "new",
                "audio_end_ms": 100,
            }
        )
        await _settle()
        assert client.can_handoff_voice_input()
    finally:
        await _finish_loop(socket, receive_loop)
