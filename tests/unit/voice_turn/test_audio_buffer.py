import numpy as np
import pytest

from main_logic.voice_turn.audio_buffer import Pcm16RingBuffer


def _pcm(values):
    return np.asarray(values, dtype="<i2").tobytes()


def test_ring_buffer_preserves_order_and_trims_oldest_samples():
    buffer = Pcm16RingBuffer(max_seconds=4 / 16_000)
    buffer.append(_pcm([1, 2, 3]))
    buffer.append(_pcm([4, 5, 6]))
    assert np.frombuffer(buffer.snapshot_bytes(), dtype="<i2").tolist() == [3, 4, 5, 6]


def test_chunk_larger_than_capacity_keeps_only_tail():
    buffer = Pcm16RingBuffer(max_seconds=3 / 16_000)
    buffer.append(_pcm([10, 20, 30, 40]))
    assert np.frombuffer(buffer.snapshot_bytes(), dtype="<i2").tolist() == [20, 30, 40]


def test_snapshot_is_independent_from_reset():
    buffer = Pcm16RingBuffer(max_seconds=1)
    buffer.append(_pcm([100, -100]))
    snapshot = buffer.snapshot_float32()
    buffer.reset()
    assert snapshot.tolist() == pytest.approx([100 / 32768, -100 / 32768])
    assert buffer.sample_count == 0


def test_rejects_partial_pcm16_sample():
    buffer = Pcm16RingBuffer()
    with pytest.raises(ValueError):
        buffer.append(b"\x01")


def test_rejects_positive_duration_smaller_than_one_sample():
    with pytest.raises(ValueError, match="at least one sample"):
        Pcm16RingBuffer(max_seconds=0.5 / 16_000)
