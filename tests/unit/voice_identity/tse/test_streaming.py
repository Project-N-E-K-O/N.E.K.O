from pathlib import Path
import os

import numpy as np
import pytest

from main_logic.voice_identity.tse import TseModel, TseModelError, TseStream
from main_logic.voice_identity.tse.contracts import TseAudioChunk


class IdentityModel:
    def __init__(self):
        self.calls = []

    def infer(self, spectrum, embedding, hidden, cell):
        self.calls.append((spectrum.shape[-1], float(hidden.flat[0])))
        return spectrum, hidden + 1, cell + 1


def concatenate(parts):
    return np.concatenate([part.pcm for part in parts]) if parts else np.empty(0, np.float32)


@pytest.mark.parametrize("length", [0, 1, 2, 127, 128, 129, 256, 257, 511, 512, 641, 16001])
def test_stream_has_contiguous_original_ranges_and_exact_flush(length):
    model = IdentityModel()
    stream = TseStream(model, np.ones(192, np.float32), start_sample=2000)
    rng = np.random.default_rng(11)
    pcm = rng.normal(0, 0.1, length).astype(np.float32)
    parts = []
    position = 0
    while position < length:
        count = int(rng.integers(1, 2100))
        parts.extend(stream.push(pcm[position:position + count], start_sample=2000 + position))
        position += min(count, length - position)
    parts.extend(stream.flush())
    expected_start = 2000
    for part in parts:
        assert part.start_sample == expected_start
        assert not part.pcm.flags.writeable
        expected_start = part.end_sample
    assert expected_start == 2000 + length
    assert stream.next_input_sample == stream.next_output_sample == 2000 + length
    assert all(frames <= 5 for frames, _ in model.calls)
    np.testing.assert_allclose(concatenate(parts), pcm, atol=5e-8, rtol=2e-6)


def test_stream_warmup_keeps_prefix_and_unfinished_tail():
    stream = TseStream(IdentityModel(), np.ones(192, np.float32))
    pcm = np.arange(512, dtype=np.float32) / 512
    assert stream.push(pcm[:511]) == []
    chunks = stream.push(pcm[511:])
    assert [(x.start_sample, x.end_sample) for x in chunks] == [(0, 128)]
    np.testing.assert_allclose(concatenate(chunks + stream.flush()), pcm, atol=1e-7)


def test_discontinuity_is_rejected_without_consuming_data():
    stream = TseStream(IdentityModel(), np.ones(192, np.float32), start_sample=100)
    for start in (99, 101, True):
        with pytest.raises(ValueError):
            stream.push(np.ones(640, np.float32), start_sample=start)
        assert stream.next_input_sample == 100
    stream.push(np.ones(1, np.float32), start_sample=100)
    assert stream.flush()[0].start_sample == 100


def test_independent_streams_reset_and_reference_snapshot():
    model = IdentityModel()
    reference = np.ones(192, np.float32)
    first, second = TseStream(model, reference), TseStream(model, reference)
    reference.fill(0)
    pcm = np.ones(1280, np.float32)
    one = concatenate(first.push(pcm) + first.flush())
    two = concatenate(second.push(pcm) + second.flush())
    np.testing.assert_array_equal(one, two)
    assert model.calls[0][1] == model.calls[3][1] == 0
    first.reset(start_sample=8000)
    parts = first.push(pcm) + first.flush()
    np.testing.assert_array_equal(one, concatenate(parts))
    assert parts[0].start_sample == 8000
    for operation in (lambda: first.push(pcm), first.flush):
        with pytest.raises(TseModelError):
            operation()
    first.close()
    first.close()
    with pytest.raises(TseModelError):
        first.reset()


def test_inference_failure_retires_stream_without_mixed_audio_fallback():
    class BrokenModel:
        def infer(self, *args):
            raise OSError("native inference failed")

    stream = TseStream(BrokenModel(), np.ones(192, np.float32))
    with pytest.raises(OSError):
        stream.push(np.ones(640, np.float32))
    for operation in (lambda: stream.push(np.ones(640, np.float32)), stream.flush):
        with pytest.raises(TseModelError):
            operation()


def test_chunk_and_reference_reject_invalid_data():
    for reference in (np.zeros(192), np.ones(191), np.full(192, np.nan), np.ones(192, complex)):
        with pytest.raises(ValueError):
            TseStream(IdentityModel(), reference)
    for start, end, pcm in ((0, 2, np.zeros(1)), (-1, 0, np.zeros(1)), (True, 2, np.zeros(1))):
        with pytest.raises(ValueError):
            TseAudioChunk(start, end, pcm)


def test_real_model_pcm_and_random_chunks_match_torch_golden():
    directory = os.environ.get("NEKO_TSE_TEST_ASSET_DIR")
    if not directory:
        pytest.skip("local model path not supplied")
    golden_dir = Path(os.environ.get("NEKO_TSE_TEST_GOLDEN_DIR", directory))
    if not (golden_dir / "golden.npz").is_file():
        pytest.skip("local prototype golden fixture not supplied")
    with np.load(golden_dir / "golden.npz", allow_pickle=False) as golden, TseModel(Path(directory)) as model:
        pcm, reference = golden["pcm"][0], golden["embedding"]
        stream = model.create_stream(reference)
        first = concatenate(stream.push(pcm) + stream.flush())
        np.testing.assert_allclose(first, golden["waveform"].reshape(-1), atol=2e-5, rtol=2e-4)
        stream.reset()
        rng, position, parts = np.random.default_rng(71), 0, []
        while position < len(pcm):
            count = int(rng.integers(1, 1788))
            parts.extend(stream.push(pcm[position:position + count], start_sample=position))
            position += min(count, len(pcm) - position)
        parts.extend(stream.flush())
        np.testing.assert_allclose(concatenate(parts), first, atol=2e-6, rtol=2e-4)
        for length in (1, 2, 127, 128, 129, 256, 257, 511, 1601):
            stream.reset()
            output = concatenate(stream.push(pcm[:length]) + stream.flush())
            assert output.shape == (length,)
            assert np.isfinite(output).all()
        stream.close()
