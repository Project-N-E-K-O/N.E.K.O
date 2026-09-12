"""A real Qwen worker error reaches the protected-delivery failure contract."""

import json
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client._infra import AsrSessionConfig, _RealtimeAsrSessionImpl
from main_logic.asr_client.workers import qwen
from tests.unit.test_asr_protected_prefix import _close, _cold_runtime
from tests.unit.test_asr_workers import _FakeConnector, _FakeWebSocket, _wait_until


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protected,written,expected_code",
    [
        (True, False, "ASR_INPUT_DELIVERY_FAILED"),
        (True, True, "ASR_INPUT_DELIVERY_UNCERTAIN"),
        (False, True, "ASR_INDEPENDENT_FAILED"),
    ],
)
async def test_worker_callback_classifies_once_after_real_socket_write(
    monkeypatch,
    protected,
    written,
    expected_code,
):
    manager, lifecycle, _, token, prefix, *_ = _cold_runtime()

    async def on_send(socket, payload):
        if json.loads(payload)["type"] == "session.update":
            await socket.server_send({"type": "session.updated"})

    websocket = _FakeWebSocket(on_send=on_send)
    connector = _FakeConnector(websocket)
    monkeypatch.setattr(qwen.websockets, "connect", connector)
    receiver = manager._asr_runtime
    epoch = receiver._asr_session_epoch

    async def on_error(_message):
        if receiver._asr_session is session and receiver._asr_session_epoch == epoch:
            await receiver._handle_independent_asr_error(epoch, "qwen")

    session = _RealtimeAsrSessionImpl(
        worker_fn=qwen.qwen_asr_worker,
        api_key="test-key",
        config=AsrSessionConfig(endpointing_mode="provider"),
        on_input_transcript=AsyncMock(),
        on_connection_error=on_error,
    )

    def codes():
        result = []
        for call in manager.send_status.await_args_list:
            try:
                result.append(json.loads(call.args[0]).get("code"))
            except (ValueError, TypeError, IndexError):
                pass
        return result

    try:
        await session.connect()
        receiver._asr_session = session
        if protected:
            receiver._asr_protected_prefix = prefix
            lifecycle.protect_unsent_prefix()
            lifecycle.accept_audio(b"\x01\x00" * 160, sample_rate_hz=16000)
        if written:
            await session.stream_audio(b"\x01\x00" * 160, sample_rate_hz=16000)
            await _wait_until(lambda: session.transport_written_audio_bytes == 320)
        else:
            await _wait_until(lambda: session.transport_write_attempted is False)
        await websocket.server_send(
            {"type": "error", "error": {"code": "server_error"}}
        )
        await _wait_until(lambda: expected_code in codes())
        assert codes().count(expected_code) == 1
        assert (
            len(
                [
                    code
                    for code in codes()
                    if code
                    in {
                        "ASR_INPUT_DELIVERY_FAILED",
                        "ASR_INPUT_DELIVERY_UNCERTAIN",
                        "ASR_INDEPENDENT_FAILED",
                    }
                ]
            )
            == 1
        )
        assert lifecycle.pending_connect_bytes == 0
        assert manager._asr_route_mode == "blocked"
        assert len(connector.calls) == 1
        await _wait_until(lambda: websocket.closed)
    finally:
        await session.close()
        await _close(manager)
