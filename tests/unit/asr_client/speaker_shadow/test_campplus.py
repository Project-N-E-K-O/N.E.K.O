from __future__ import annotations

import pickle
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import main_logic.asr_client.speaker_shadow.campplus as campplus
from main_logic.asr_client.speaker_shadow.campplus import (
    CampPlusBackendFactory,
    CampPlusEmbeddingModel,
    CampPlusSpeakerShadowBackend,
    compute_campplus_features,
)


def _pcm16(duration_seconds: float = 1.5, frequency_hz: float = 220.0) -> bytes:
    sample_count = round(16_000 * duration_seconds)
    time = np.arange(sample_count, dtype=np.float64) / 16_000
    samples = (
        0.19 * np.sin(2 * np.pi * frequency_hz * time)
        + 0.07 * np.sin(2 * np.pi * 733 * time)
        + 0.01 * np.sin(2 * np.pi * 37 * time)
    )
    return np.clip(np.rint(samples * 32767), -32768, 32767).astype("<i2").tobytes()


class _FakeSession:
    def __init__(
        self,
        output: np.ndarray,
        *,
        input_name: str = "x",
        input_type: str = "tensor(float)",
        input_shape: list[object] | None = None,
        output_name: str = "embedding",
        output_type: str = "tensor(float)",
        output_shape: list[object] | None = None,
    ) -> None:
        self.output = np.asarray(output, dtype=np.float32)
        self.input = SimpleNamespace(
            name=input_name,
            type=input_type,
            shape=input_shape or ["N", "T", 80],
        )
        self.output_info = SimpleNamespace(
            name=output_name,
            type=output_type,
            shape=output_shape or ["N", 192],
        )
        self.last_inputs: dict[str, np.ndarray] | None = None

    def get_inputs(self):
        return [self.input]

    def get_outputs(self):
        return [self.output_info]

    def get_modelmeta(self):
        return SimpleNamespace(
            custom_metadata_map={
                "framework": "3d-speaker",
                "language": "Chinese-English",
                "url": (
                    "https://www.modelscope.cn/models/iic/"
                    "speech_campplus_sv_zh_en_16k-common_advanced/summary"
                ),
                "comment": (
                    "iic/speech_campplus_sv_zh_en_16k-common_advanced"
                ),
                "feature_normalize_type": "global-mean",
                "sample_rate": "16000",
                "output_dim": "192",
                "normalize_samples": "1",
            }
        )

    def run(self, output_names, inputs):
        assert output_names == ["embedding"]
        self.last_inputs = inputs
        return [self.output]


def _install_fake_onnxruntime(
    monkeypatch,
    session: _FakeSession,
    sessions: list[_FakeSession],
) -> None:
    class _SessionOptions:
        pass

    def create_session(*_args, **_kwargs):
        sessions.append(session)
        return session

    fake = SimpleNamespace(
        SessionOptions=_SessionOptions,
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"),
        GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL="all"),
        InferenceSession=create_session,
        get_available_providers=lambda: ["CPUExecutionProvider"],
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)


def _install_verified_model_path(monkeypatch, tmp_path: Path) -> Path:
    model_path = tmp_path / "campplus-zh-en-advanced.onnx"
    model_path.write_bytes(b"verified by asset layer")
    monkeypatch.setattr(
        campplus,
        "resolve_verified_campplus_asset",
        lambda _asset_dir=None: model_path,
    )
    return model_path


def test_embedding_model_constructor_is_zero_io_and_onnxruntime_is_lazy(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[object] = []

    def fail_resolve(asset_dir=None):
        calls.append(asset_dir)
        raise AssertionError("constructor must not resolve assets")

    monkeypatch.setattr(campplus, "resolve_verified_campplus_asset", fail_resolve)
    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)

    model = CampPlusEmbeddingModel(asset_dir=tmp_path)

    assert calls == []
    assert "onnxruntime" not in sys.modules
    model.close()


@pytest.mark.parametrize(
    ("session_kwargs", "expected_token"),
    [
        ({"input_name": "features"}, "onnx_input_name"),
        ({"input_type": "tensor(double)"}, "onnx_input_type"),
        ({"input_shape": ["N", "T", 64]}, "onnx_input_shape"),
        ({"output_name": "speaker"}, "onnx_output_name"),
        ({"output_type": "tensor(double)"}, "onnx_output_type"),
        ({"output_shape": ["N", 256]}, "onnx_output_shape"),
    ],
)
def test_load_rejects_wrong_onnx_tensor_contract(
    tmp_path,
    monkeypatch,
    session_kwargs,
    expected_token,
) -> None:
    _install_verified_model_path(monkeypatch, tmp_path)
    session = _FakeSession(np.ones((1, 192), dtype=np.float32), **session_kwargs)
    _install_fake_onnxruntime(monkeypatch, session, [])
    model = CampPlusEmbeddingModel(asset_dir=tmp_path)

    with pytest.raises(ValueError, match=expected_token):
        model._validate_session_contract(session)
    assert model.load() is False
    model.close()


def test_frontend_matches_official_kaldi_native_fbank() -> None:
    knf = pytest.importorskip("kaldi_native_fbank")
    pcm16 = _pcm16(1.5)
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
    options = knf.FbankOptions()
    options.frame_opts.dither = 0
    options.frame_opts.samp_freq = 16_000
    options.frame_opts.snip_edges = True
    options.mel_opts.num_bins = 80
    options.mel_opts.debug_mel = False
    fbank = knf.OnlineFbank(options)
    fbank.accept_waveform(16_000, samples)
    fbank.input_finished()
    expected = np.stack(
        [fbank.get_frame(index) for index in range(fbank.num_frames_ready)],
        axis=0,
    ).astype(np.float32)
    expected -= expected.mean(axis=0, keepdims=True)

    actual = compute_campplus_features(pcm16, sample_rate_hz=16_000)

    assert actual.shape == expected.shape == (148, 80)
    assert actual.dtype == np.float32
    assert np.max(np.abs(actual - expected)) <= 1e-3
    assert np.mean(np.abs(actual - expected)) <= 1e-4


def test_embedding_is_finite_l2_normalized_and_uses_expected_tensor(
    tmp_path,
    monkeypatch,
) -> None:
    _install_verified_model_path(monkeypatch, tmp_path)
    raw = np.arange(1, 193, dtype=np.float32)[None, :]
    session = _FakeSession(raw)
    sessions: list[_FakeSession] = []
    _install_fake_onnxruntime(monkeypatch, session, sessions)
    model = CampPlusEmbeddingModel(asset_dir=tmp_path)

    assert model.load() is True
    embedding = model.embedding_from_pcm16(_pcm16(), sample_rate_hz=16_000)

    assert len(sessions) == 1
    assert embedding.shape == (192,)
    assert embedding.dtype == np.float32
    assert np.isfinite(embedding).all()
    assert np.linalg.norm(embedding) == pytest.approx(1.0, abs=1e-6)
    assert session.last_inputs is not None
    assert session.last_inputs["x"].shape == (1, 148, 80)
    model.close()


@pytest.mark.parametrize("case", ["empty", "odd", "wrong-rate", "too-short"])
def test_embedding_rejects_invalid_pcm_boundaries(
    tmp_path,
    monkeypatch,
    case,
) -> None:
    _install_verified_model_path(monkeypatch, tmp_path)
    session = _FakeSession(np.ones((1, 192), dtype=np.float32))
    _install_fake_onnxruntime(monkeypatch, session, [])
    model = CampPlusEmbeddingModel(asset_dir=tmp_path)
    assert model.load()
    pcm16, sample_rate_hz, message = {
        "empty": (b"", 16_000, "pcm_invalid"),
        "odd": (b"\x00", 16_000, "pcm_invalid"),
        "wrong-rate": (_pcm16(), 48_000, "sample_rate_mismatch"),
        "too-short": (_pcm16(1.499), 16_000, "pcm_too_short"),
    }[case]

    with pytest.raises(ValueError, match=message):
        model.embedding_from_pcm16(pcm16, sample_rate_hz=sample_rate_hz)
    model.close()


def test_load_and_close_are_idempotent(tmp_path, monkeypatch) -> None:
    _install_verified_model_path(monkeypatch, tmp_path)
    session = _FakeSession(np.ones((1, 192), dtype=np.float32))
    sessions: list[_FakeSession] = []
    _install_fake_onnxruntime(monkeypatch, session, sessions)
    model = CampPlusEmbeddingModel(asset_dir=tmp_path)

    assert model.load()
    assert model.load()
    assert len(sessions) == 1
    model.close()
    model.close()
    assert model.load() is False


class _BackendModel:
    def __init__(self, embeddings: dict[bytes, np.ndarray]) -> None:
        self.embeddings = embeddings
        self.load_calls = 0
        self.close_calls = 0

    def load(self) -> bool:
        self.load_calls += 1
        return True

    def embedding_from_pcm16(self, pcm16: bytes, *, sample_rate_hz: int) -> np.ndarray:
        assert sample_rate_hz == 16_000
        return np.array(self.embeddings[pcm16], dtype=np.float32, copy=True)

    def close(self) -> None:
        self.close_calls += 1


def test_backend_scores_cosine_copies_reference_and_wipes_on_close() -> None:
    basis = np.eye(192, dtype=np.float32)
    source = basis[0].copy()
    model = _BackendModel({b"same": basis[0], b"other": basis[1]})
    backend = CampPlusSpeakerShadowBackend(source, model_factory=lambda: model)
    private_reference = backend._reference._storage
    source.fill(0)

    assert backend.load()
    assert backend.load()
    assert backend.score(b"same", 16_000) == pytest.approx(1.0, abs=1e-6)
    assert backend.score(b"other", 16_000) == pytest.approx(0.0, abs=1e-6)
    backend.close()
    backend.close()

    assert model.load_calls == 1
    assert model.close_calls == 1
    assert not any(private_reference)
    with pytest.raises(RuntimeError, match="backend_closed"):
        backend.score(b"same", 16_000)


def test_backend_factory_is_zero_io_spawn_pickleable_and_wipes_parent_copy(
    monkeypatch,
    tmp_path,
) -> None:
    resolve_calls: list[object] = []

    def fail_resolve(asset_dir=None):
        resolve_calls.append(asset_dir)
        raise AssertionError("parent factory must not resolve assets")

    monkeypatch.setattr(campplus, "resolve_verified_campplus_asset", fail_resolve)
    source = np.arange(1, 193, dtype=np.float32)
    factory = CampPlusBackendFactory(source, asset_dir=tmp_path)
    private_reference = factory._reference._storage
    source.fill(0)

    payload = pickle.dumps(factory)
    restored = pickle.loads(payload)

    assert resolve_calls == []
    assert any(private_reference)
    assert "embedding" not in repr(factory).lower()
    restored.close()
    factory.close()
    factory.close()
    assert not any(private_reference)
    with pytest.raises(RuntimeError, match="backend_factory_closed"):
        factory()


def test_backend_does_not_log_pcm_embedding_similarity_or_identity(caplog) -> None:
    pcm16 = b"\x42\x13" * 24_000
    basis = np.eye(192, dtype=np.float32)
    model = _BackendModel({pcm16: basis[1]})
    backend = CampPlusSpeakerShadowBackend(
        basis[0],
        model_factory=lambda: model,
    )

    assert backend.load()
    assert backend.score(pcm16, 16_000) == pytest.approx(0.0, abs=1e-6)
    backend.close()

    logged = caplog.text.lower()
    assert "4213" not in logged
    assert "embedding" not in logged
    assert "similarity" not in logged
    assert "candidate" not in logged
