from __future__ import annotations

import pytest

from main_logic.asr_client.audio import AudioRingBuffer


def _pcm(milliseconds: int, *, sample_rate_hz: int = 16_000, value: int = 1) -> bytes:
    sample_count = sample_rate_hz * milliseconds // 1_000
    return int(value).to_bytes(2, "little", signed=True) * sample_count


def test_ring_buffer_retains_only_latest_audio() -> None:
    buffer = AudioRingBuffer(capacity_ms=700, sample_rate_hz=16_000)

    buffer.append(_pcm(500, value=1))
    dropped = buffer.append(_pcm(400, value=2))

    assert dropped == _pcm(200, value=1)
    assert buffer.duration_ms == 700
    assert buffer.peek().startswith(_pcm(300, value=1))
    assert buffer.peek().endswith(_pcm(400, value=2))


def test_drain_is_atomic_and_clears_duration() -> None:
    buffer = AudioRingBuffer(capacity_ms=800, sample_rate_hz=16_000)
    payload = _pcm(640)
    buffer.append(payload)

    assert buffer.drain() == payload
    assert buffer.duration_ms == 0
    assert buffer.peek() == b""


def test_odd_pcm_and_wrong_sample_rate_are_rejected() -> None:
    buffer = AudioRingBuffer(capacity_ms=700, sample_rate_hz=16_000)

    with pytest.raises(ValueError, match="PCM16"):
        buffer.append(b"\x00")
    with pytest.raises(ValueError, match="sample rate"):
        buffer.append(b"\x00\x00", sample_rate_hz=48_000)


def test_buffer_never_retains_more_than_capacity_for_large_chunk() -> None:
    buffer = AudioRingBuffer(capacity_ms=700, sample_rate_hz=16_000)

    dropped = buffer.append(_pcm(1_000, value=3))

    assert len(dropped) == len(_pcm(300, value=3))
    assert buffer.peek() == _pcm(700, value=3)
