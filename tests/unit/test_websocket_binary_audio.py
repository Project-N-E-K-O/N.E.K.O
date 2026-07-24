from __future__ import annotations

import json
import struct

import pytest

import main_routers.websocket_router as websocket_router
from main_routers.websocket_router import _decode_binary_audio_frame


def test_binary_audio_frame_decodes_pcm_and_sample_rate() -> None:
    payload = struct.pack("<4sI3h", b"NEKO", 48_000, 1, -2, 3)

    message = _decode_binary_audio_frame(payload)

    assert message == {
        "action": "stream_data",
        "input_type": "audio",
        "sample_rate_hz": 48_000,
        "data": [1, -2, 3],
    }


@pytest.mark.parametrize(
    "payload",
    [
        b"bad",
        struct.pack("<4sIh", b"FAIL", 16_000, 1),
        struct.pack("<4sIh", b"NEKO", 44_100, 1),
        struct.pack("<4sI", b"NEKO", 16_000) + b"\x00",
    ],
)
def test_binary_audio_frame_rejects_invalid_contract(payload: bytes) -> None:
    with pytest.raises(ValueError, match="VOICE_BINARY_FRAME_INVALID"):
        _decode_binary_audio_frame(payload)


def test_binary_audio_frame_rejects_more_than_one_second_before_pcm_unpack() -> None:
    payload = struct.pack("<4sI", b"NEKO", 48_000) + (b"\x00\x00" * 48_001)

    with pytest.raises(ValueError, match="VOICE_BINARY_FRAME_INVALID: frame is too large"):
        _decode_binary_audio_frame(payload)


@pytest.mark.asyncio
async def test_websocket_drops_bad_binary_frame_and_processes_next_message(
    monkeypatch,
) -> None:
    class _Manager:
        def __init__(self) -> None:
            self.pending_agent_callbacks = []
            self.websocket = None
            self.cleanup_calls = 0

        def _begin_voice_input_connection(self, _connection_id: str) -> None:
            return None

        async def cleanup(self, *, expected_websocket) -> None:
            assert expected_websocket is websocket
            self.cleanup_calls += 1

    class _WebSocket:
        client = "test-client"

        def __init__(self) -> None:
            self.events = [
                {"type": "websocket.receive", "bytes": b"bad"},
                {
                    "type": "websocket.receive",
                    "text": json.dumps({"action": "ping"}),
                },
                {"type": "websocket.disconnect", "code": 1000},
            ]
            self.sent_text: list[str] = []

        async def accept(self) -> None:
            return None

        async def receive(self) -> dict:
            return self.events.pop(0)

        async def send_text(self, payload: str) -> None:
            self.sent_text.append(payload)

    manager = _Manager()
    websocket = _WebSocket()
    session_ids: dict[str, object] = {}
    monkeypatch.setattr(websocket_router, "get_config_manager", lambda: object())
    monkeypatch.setattr(
        websocket_router,
        "get_session_manager",
        lambda: {"Lan": manager},
    )
    monkeypatch.setattr(
        websocket_router,
        "get_session_id",
        lambda: session_ids,
    )

    await websocket_router.websocket_endpoint(websocket, "Lan")

    assert [json.loads(payload) for payload in websocket.sent_text] == [
        {"type": "pong"}
    ]
    assert manager.cleanup_calls == 1
