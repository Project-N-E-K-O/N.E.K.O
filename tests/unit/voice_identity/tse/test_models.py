from pathlib import Path
from types import SimpleNamespace
import io
import os

import numpy as np
import pytest

from main_logic.voice_identity.tse import models
from main_logic.voice_identity.tse.contracts import STATE_SHAPE, TseModelError


def metadata(name, shape):
    return SimpleNamespace(name=name, shape=shape, type="tensor(float)")


class EncoderSession:
    def get_inputs(self):
        return [metadata("fbank", (1, "frames", 80))]

    def get_outputs(self):
        return [metadata("embedding", (1, 192))]

    def run(self, names, inputs):
        return [np.arange(1, 193, dtype=np.float32)[None]]


class SeparatorSession:
    def get_inputs(self):
        return [metadata("spectrum", (1, 2, 257, "frames")), metadata("embedding", (1, 192)),
                metadata("hidden", STATE_SHAPE), metadata("cell", STATE_SHAPE)]

    def get_outputs(self):
        return [metadata("estimated", (1, 2, 257, "frames")),
                metadata("next_hidden", STATE_SHAPE), metadata("next_cell", STATE_SHAPE)]

    def run(self, names, inputs):
        return [inputs["spectrum"], inputs["hidden"], inputs["cell"]]


def test_encoder_preserves_raw_space_and_accepts_only_three_references(monkeypatch, tmp_path):
    np.savez(tmp_path / "tse_frontend_constants.npz", mel=np.ones((80, 257), np.float32), hamming=np.ones(400, np.float32))
    monkeypatch.setattr(models, "_make_session", lambda path: EncoderSession())
    with models.TseEncoder(tmp_path) as encoder:
        pcm = np.zeros(48000, np.float32)
        result = encoder.encode_references([pcm, pcm, pcm])
        np.testing.assert_array_equal(result, np.arange(1, 193, dtype=np.float32))
        assert np.linalg.norm(result) > 100
        for count in (0, 1, 2, 4):
            with pytest.raises(ValueError):
                encoder.encode_references([pcm] * count)
        with pytest.raises(TseModelError):
            encoder.encode(np.zeros(2))
    with pytest.raises(TseModelError):
        encoder.encode(pcm)


def test_model_contract_inference_and_terminal_close(monkeypatch, tmp_path):
    session = SeparatorSession()
    monkeypatch.setattr(models, "_make_session", lambda path: session)
    with models.TseModel(tmp_path) as model:
        stream = model.create_stream(np.ones(192, np.float32))
        assert sum(part.pcm.size for part in stream.push(np.ones(640, np.float32)) + stream.flush()) == 640
        spectrum, zero = np.ones((1, 2, 257, 1), np.float32), np.zeros(STATE_SHAPE, np.float32)
        session.run = lambda *args: [spectrum * np.nan, zero, zero]
        with pytest.raises(TseModelError):
            model.infer(spectrum, np.ones((1, 192), np.float32), zero, zero)
    with pytest.raises(TseModelError):
        model.create_stream(np.ones(192))
    with pytest.raises(TseModelError):
        model.infer(spectrum, np.ones((1, 192), np.float32), zero, zero)


@pytest.mark.parametrize("failure", ["name", "dtype", "rank", "shape"])
def test_model_metadata_rejects_incompatible_session(failure):
    session = EncoderSession()
    item = metadata("fbank", (1, "frames", 80))
    if failure == "name":
        item.name = "waveform"
    elif failure == "dtype":
        item.type = "tensor(double)"
    elif failure == "rank":
        item.shape = (1, 80)
    else:
        item.shape = (1, "frames", 64)
    session.get_inputs = lambda: [item]
    with pytest.raises(TseModelError):
        models._check_metadata(session, {"fbank": (1, None, 80)}, {"embedding": (1, 192)})


def test_model_rejects_unrecognized_size_and_hash_before_session_loading():
    with pytest.raises(TseModelError, match="unrecognized"):
        models._make_session(Path("another-model.onnx"))
    wrong_size = SimpleNamespace(name="tse_ecapa_fp32.onnx", stat=lambda: SimpleNamespace(st_size=5))
    with pytest.raises(TseModelError, match="size mismatch"):
        models._make_session(wrong_size)
    corrupt = SimpleNamespace(name="tse_ecapa_fp32.onnx", stat=lambda: SimpleNamespace(st_size=24897704),
                              open=lambda mode: io.BytesIO(b"incorrect model content"))
    with pytest.raises(TseModelError, match="hash mismatch"):
        models._make_session(corrupt)


def test_real_models_match_frozen_spectral_and_enrollment_golden():
    directory = os.environ.get("NEKO_TSE_TEST_ASSET_DIR")
    if not directory:
        pytest.skip("set NEKO_TSE_TEST_ASSET_DIR; CI does not download models")
    golden_dir = Path(os.environ.get("NEKO_TSE_TEST_GOLDEN_DIR", directory))
    if not (golden_dir / "golden.npz").is_file():
        pytest.skip("local prototype golden fixture not supplied")
    with np.load(golden_dir / "golden.npz", allow_pickle=False) as golden, models.TseModel(Path(directory)) as model:
        zero = np.zeros(STATE_SHAPE, np.float32)
        estimated, hidden, cell = model.infer(golden["spectrum"], golden["embedding"], zero, zero)
        for actual, key in ((estimated, "estimated"), (hidden, "hidden"), (cell, "cell")):
            np.testing.assert_allclose(actual, golden[key], atol=2e-4, rtol=2e-4)
        assert model._session.get_providers() == ["CPUExecutionProvider"]
    with np.load(golden_dir / "enrollment_golden.npz", allow_pickle=False) as golden, models.TseEncoder(Path(directory)) as encoder:
        for name in ("normal", "quiet", "silence"):
            features = encoder._frontend.fbank(golden[f"{name}_pcm"], noise=golden[f"{name}_noise"])
            actual = encoder._session.run(["embedding"], {"fbank": features})[0]
            np.testing.assert_allclose(actual, golden[f"{name}_emb"], atol=2e-4, rtol=2e-4)
        first = encoder.encode(golden["normal_pcm"])
        np.testing.assert_array_equal(first, encoder.encode(golden["normal_pcm"]))
        assert first.shape == (192,)
        assert not np.isclose(np.linalg.norm(first), 1)
