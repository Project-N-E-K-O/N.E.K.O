"""Protected Soniox input must not inherit provider-managed uncertain replay."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client._infra import AsrSessionConfig, _RealtimeAsrSessionImpl
from main_logic.asr_client.workers import soniox
from tests.support.asr_delivery_fakes import _close, _cold_runtime
from tests.support.realtime_harness import _FakeConnector, _FakeWebSocket, _wait_until


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [True, False])
@pytest.mark.parametrize("mid_send", [True, False])
async def test_soniox_protected_prefix_blocks_replay_but_preserves_ordinary_policy(
    monkeypatch,
    protected,
    mid_send,
):
    entered = asyncio.Event()

    class Socket(_FakeWebSocket):
        async def send(self, payload):
            if isinstance(payload, bytes) and payload:
                entered.set()
                if mid_send:
                    await asyncio.Event().wait()
            await super().send(payload)

    first, second = Socket(), _FakeWebSocket()
    connector = _FakeConnector(first, second)
    monkeypatch.setattr(soniox.websockets, "connect", connector)
    manager, lifecycle, _, token, prefix, *_ = _cold_runtime()
    receiver = manager._asr_runtime
    epoch = receiver._asr_session_epoch

    async def on_error(_message):
        if receiver._asr_session is session and receiver._asr_session_epoch == epoch:
            await receiver._handle_independent_asr_error(epoch, "soniox")

    session = _RealtimeAsrSessionImpl(
        worker_fn=soniox.soniox_asr_worker,
        api_key="test-key",
        config=AsrSessionConfig(endpointing_mode="provider"),
        on_input_transcript=AsyncMock(),
        on_connection_error=on_error,
    )
    try:
        await session.connect()
        receiver._asr_session = session
        if protected:
            receiver._asr_protected_prefix = prefix
            session.protect_audio_delivery()
        pcm = b"\x20\x10" * 320
        await session.stream_audio(pcm, sample_rate_hz=16000)
        await asyncio.wait_for(entered.wait(), 1)
        assert session.transport_write_attempted is True
        assert session.transport_written_audio_bytes == (0 if mid_send else len(pcm))
        await first.server_end()
        if protected:
            await _wait_until(lambda: manager._asr_route_mode == "blocked")
            await _wait_until(
                lambda: any(
                    "ASR_INPUT_DELIVERY_UNCERTAIN" in str(call.args[0])
                    for call in manager.send_status.await_args_list
                )
            )
            codes = []
            for call in manager.send_status.await_args_list:
                try:
                    codes.append(json.loads(call.args[0]).get("code"))
                except (ValueError, TypeError, IndexError):
                    pass
            assert "ASR_INPUT_DELIVERY_UNCERTAIN" in codes
            assert len(connector.calls) == 1
            assert second.sent == []
        else:
            await _wait_until(lambda: len(connector.calls) == 2)
            await _wait_until(lambda: pcm in second.sent)
            assert manager._asr_route_mode == "independent"
    finally:
        await session.close()
        await _close(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize("completion_mode", ["manual_fin", "provider_end", "clear"])
async def test_soniox_protected_idle_disconnect_after_completed_turn_reconnects(
    monkeypatch,
    completion_mode,
):
    """F0010: protected session with empty replay_audio and no wire audio must reconnect cleanly."""
    first, second, third = _FakeWebSocket(), _FakeWebSocket(), _FakeWebSocket()
    connector = _FakeConnector(first, second, third)
    monkeypatch.setattr(soniox.websockets, "connect", connector)
    manager, lifecycle, _, token, prefix, *_ = _cold_runtime()
    receiver = manager._asr_runtime
    epoch = receiver._asr_session_epoch

    async def on_error(_message):
        if receiver._asr_session is session and receiver._asr_session_epoch == epoch:
            await receiver._handle_independent_asr_error(epoch, "soniox")

    endpointing_mode = "manual" if completion_mode == "manual_fin" else "provider"
    transcripts = []

    async def on_transcript(text, *_args, **_kwargs):
        transcripts.append(text)

    session = _RealtimeAsrSessionImpl(
        worker_fn=soniox.soniox_asr_worker,
        api_key="test-key",
        config=AsrSessionConfig(endpointing_mode=endpointing_mode),
        on_input_transcript=on_transcript,
        on_connection_error=on_error,
    )
    try:
        await session.connect()
        receiver._asr_session = session
        receiver._asr_protected_prefix = prefix
        session.protect_audio_delivery()

        pcm1 = b"\x20\x10" * 320
        await session.stream_audio(pcm1, sample_rate_hz=16000)
        await _wait_until(lambda: pcm1 in first.sent)
        assert session.transport_write_attempted is True

        if completion_mode == "manual_fin":
            await session.signal_user_activity_end()
            await first.server_send({
                "tokens": [{"text": "turn1", "is_final": True}, {"text": "<fin>"}]
            })
            await _wait_until(lambda: "turn1" in transcripts)
        elif completion_mode == "provider_end":
            # 320 samples at 16kHz = 20ms; end_ms=20 covers all sent audio.
            await first.server_send({
                "tokens": [
                    {"text": "turn1", "is_final": True, "end_ms": 20},
                    {"text": "<end>"},
                ]
            })
            await _wait_until(lambda: "turn1" in transcripts)
        else:
            await session.clear_audio_buffer()
            await _wait_until(lambda: len(connector.calls) == 2)
            # Disconnect the reset socket while idle -> must reconnect cleanly to third.
            await second.server_end()
            await _wait_until(lambda: len(connector.calls) == 3)
            assert manager._asr_route_mode != "blocked"
            assert not any(isinstance(item, bytes) for item in third.sent)
            return

        # Idle disconnect after Turn 1 completed cleanly (replay_audio empty, has_wire_audio=False).
        await first.server_end()
        await _wait_until(lambda: len(connector.calls) == 2)
        assert manager._asr_route_mode != "blocked"
        # Config JSON only; no replayed PCM bytes on the replacement socket.
        assert not any(isinstance(item, bytes) for item in second.sent)

        pcm2 = b"\x40\x10" * 320
        await session.stream_audio(pcm2, sample_rate_hz=16000)
        await _wait_until(lambda: pcm2 in second.sent)
        if endpointing_mode == "manual":
            await session.signal_user_activity_end()
        await second.server_send({
            "tokens": [
                {"text": "turn2", "is_final": True, "end_ms": 20},
                {"text": "<fin>" if endpointing_mode == "manual" else "<end>"},
            ]
        })
        await _wait_until(lambda: "turn2" in transcripts)
    finally:
        await session.close()
        await _close(manager)


pytestmark = pytest.mark.integration_serial
