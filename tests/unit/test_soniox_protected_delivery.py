"""Protected Soniox input must not inherit provider-managed uncertain replay."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client._infra import AsrSessionConfig, _RealtimeAsrSessionImpl
from main_logic.asr_client.workers import soniox
from tests.unit.test_asr_protected_prefix import _close, _cold_runtime
from tests.unit.test_asr_workers import _FakeConnector, _FakeWebSocket, _wait_until


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
