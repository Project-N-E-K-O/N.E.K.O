from __future__ import annotations

import struct

import pytest

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
