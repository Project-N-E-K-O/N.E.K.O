from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import kaldi_native_fbank as knf
import numpy as np
import pytest

from main_logic.asr_client.campplus import (
    CAMPPLUS_FILENAME,
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
    CAMPPLUS_SHA256,
    CAMPPLUS_SIZE_BYTES,
    CAMPPLUS_SOURCE,
    CampPlusAssetError,
    CampPlusEmbeddingModel,
    CampPlusSpeakerProfile,
    CampPlusSpeakerShadowBackend,
    CampPlusSpeakerShadowFactory,
    build_campplus_speaker_profile,
    compute_campplus_features,
    load_campplus_manifest,
)
from main_logic.asr_client.speaker_shadow import SpeakerShadowRuntime
from tools.voice_eval.prepare_speaker_model import prepare_speaker_model


ROOT = Path(__file__).resolve().parents[2]


def _pcm16(duration_seconds: float = 1.5, frequency_hz: float = 220.0) -> bytes:
    sample_count = round(16_000 * duration_seconds)
    time = np.arange(sample_count, dtype=np.float64) / 16_000
    samples = (
        0.19 * np.sin(2 * np.pi * frequency_hz * time)
        + 0.07 * np.sin(2 * np.pi * 733 * time)
        + 0.01 * np.sin(2 * np.pi * 37 * time)
    )
    return np.clip(np.rint(samples * 32767), -32768, 32767).astype("<i2").tobytes()


def _manifest_payload(*, sha256: str, size_bytes: int) -> dict[str, object]:
    return {
        "filename": CAMPPLUS_FILENAME,
        "model_id": CAMPPLUS_MODEL_ID,
        "revision": CAMPPLUS_MODEL_REVISION,
        "license": "Apache-2.0",
        "source": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-recongition-models/"
            "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
        ),
        "export_source": "k2-fsa/sherpa-onnx",
        "size_bytes": size_bytes,
        "sha256": sha256,
        "sample_rate_hz": 16000,
        "preprocessing": "kaldi-native-fbank-sniped-global-mean-v1",
        "input_contract": "float32[batch,time,80]",
        "output_contract": "float32[batch,192]",
    }


def _write_asset_dir(directory: Path, model_bytes: bytes = b"reviewed model") -> Path:
    directory.mkdir()
    digest = hashlib.sha256(model_bytes).hexdigest()
    (directory / CAMPPLUS_FILENAME).write_bytes(model_bytes)
    (directory / "manifest.json").write_text(
        json.dumps(_manifest_payload(sha256=digest, size_bytes=len(model_bytes))),
        encoding="utf-8",
    )
    return directory


def _patch_expected_asset(monkeypatch, model_bytes: bytes) -> None:
    import main_logic.asr_client.campplus as campplus

    monkeypatch.setattr(campplus, "CAMPPLUS_SIZE_BYTES", len(model_bytes))
    monkeypatch.setattr(
        campplus,
        "CAMPPLUS_SHA256",
        hashlib.sha256(model_bytes).hexdigest(),
    )


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
                "comment": f"This model is from {CAMPPLUS_MODEL_ID}",
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


def _install_fake_onnxruntime(monkeypatch, session: _FakeSession, sessions: list) -> None:
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
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)


def test_repository_manifest_pins_reviewed_model_identity() -> None:
    manifest = load_campplus_manifest(ROOT / "data" / "speaker_models")

    assert manifest.filename == CAMPPLUS_FILENAME
    assert manifest.model_id == CAMPPLUS_MODEL_ID
    assert manifest.revision == "v1.0.0"
    assert manifest.license == "Apache-2.0"
    assert manifest.source == CAMPPLUS_SOURCE
    assert manifest.size_bytes == 28_281_164
    assert manifest.sha256 == CAMPPLUS_SHA256
    assert manifest.preprocessing == "kaldi-native-fbank-sniped-global-mean-v1"
    assert manifest.input_contract == "float32[batch,time,80]"
    assert manifest.output_contract == "float32[batch,192]"


def test_asset_validation_rejects_missing_and_corrupt_weight(tmp_path, monkeypatch) -> None:
    model_bytes = b"reviewed model"
    _patch_expected_asset(monkeypatch, model_bytes)
    directory = _write_asset_dir(tmp_path / "speaker", model_bytes)
    model_path = directory / CAMPPLUS_FILENAME
    model_path.unlink()

    with pytest.raises(CampPlusAssetError, match="missing"):
        CampPlusEmbeddingModel(asset_dir=directory).validate_assets()

    model_path.write_bytes(b"corrupt")
    with pytest.raises(CampPlusAssetError, match="size"):
        CampPlusEmbeddingModel(asset_dir=directory).validate_assets()

    model_path.write_bytes(b"reviewed modeX")
    with pytest.raises(CampPlusAssetError, match="SHA-256"):
        CampPlusEmbeddingModel(asset_dir=directory).validate_assets()


def test_prepare_speaker_model_verifies_cache_and_offline_bundle(
    tmp_path, monkeypatch
) -> None:
    model_bytes = b"reviewed model"
    _patch_expected_asset(monkeypatch, model_bytes)
    directory = tmp_path / "speaker"
    directory.mkdir()
    (directory / "manifest.json").write_text(
        json.dumps(
            _manifest_payload(
                sha256=hashlib.sha256(model_bytes).hexdigest(),
                size_bytes=len(model_bytes),
            )
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / CAMPPLUS_FILENAME).write_bytes(model_bytes)

    prepared = prepare_speaker_model(directory, source_cache=cache)

    assert prepared.read_bytes() == model_bytes
    assert prepare_speaker_model(directory, offline=True) == prepared
    prepared.write_bytes(b"corrupt")
    with pytest.raises(CampPlusAssetError):
        prepare_speaker_model(directory, offline=True)


@pytest.mark.parametrize(
    ("session_kwargs", "message"),
    [
        ({"input_name": "features"}, "input name"),
        ({"input_type": "tensor(double)"}, "input type"),
        ({"input_shape": ["N", "T", 64]}, "input shape"),
        ({"output_name": "speaker"}, "output name"),
        ({"output_type": "tensor(double)"}, "output type"),
        ({"output_shape": ["N", 256]}, "output shape"),
    ],
)
def test_load_rejects_wrong_onnx_tensor_contract(
    tmp_path,
    monkeypatch,
    session_kwargs,
    message,
) -> None:
    model_bytes = b"reviewed model"
    _patch_expected_asset(monkeypatch, model_bytes)
    directory = _write_asset_dir(tmp_path / "speaker", model_bytes)
    session = _FakeSession(np.ones((1, 192), dtype=np.float32), **session_kwargs)
    _install_fake_onnxruntime(monkeypatch, session, [])
    model = CampPlusEmbeddingModel(asset_dir=directory)

    assert model.load() is False
    assert message in (model.unavailable_reason or "")
    assert model.snapshot()["load_failure_count"] == 1


def test_frontend_matches_official_kaldi_native_fbank() -> None:
    pcm16 = _pcm16(1.5)
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0

    opts = knf.FbankOptions()
    opts.frame_opts.dither = 0
    opts.frame_opts.samp_freq = 16_000
    opts.frame_opts.snip_edges = True
    opts.mel_opts.num_bins = 80
    opts.mel_opts.debug_mel = False
    fbank = knf.OnlineFbank(opts)
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


def test_embedding_is_finite_normalized_and_uses_expected_tensor(tmp_path, monkeypatch) -> None:
    model_bytes = b"reviewed model"
    _patch_expected_asset(monkeypatch, model_bytes)
    directory = _write_asset_dir(tmp_path / "speaker", model_bytes)
    raw = np.arange(1, 193, dtype=np.float32)[None, :]
    session = _FakeSession(raw)
    sessions: list[_FakeSession] = []
    _install_fake_onnxruntime(monkeypatch, session, sessions)
    model = CampPlusEmbeddingModel(asset_dir=directory)

    assert model.load() is True
    embedding = model.embedding_from_pcm16(_pcm16(), sample_rate_hz=16_000)

    assert embedding.shape == (192,)
    assert embedding.dtype == np.float32
    assert np.isfinite(embedding).all()
    assert np.linalg.norm(embedding) == pytest.approx(1.0, abs=1e-6)
    assert session.last_inputs is not None
    assert session.last_inputs["x"].shape == (1, 148, 80)
    assert model.snapshot()["inference_count"] == 1


@pytest.mark.parametrize("case", ["empty", "odd", "wrong-rate", "too-short"])
def test_embedding_rejects_invalid_pcm_boundaries(
    tmp_path,
    monkeypatch,
    case,
) -> None:
    model_bytes = b"reviewed model"
    _patch_expected_asset(monkeypatch, model_bytes)
    directory = _write_asset_dir(tmp_path / "speaker", model_bytes)
    session = _FakeSession(np.ones((1, 192), dtype=np.float32))
    _install_fake_onnxruntime(monkeypatch, session, [])
    model = CampPlusEmbeddingModel(asset_dir=directory)
    assert model.load()
    pcm16, sample_rate_hz, message = {
        "empty": (b"", 16_000, "non-empty"),
        "odd": (b"\x00", 16_000, "even"),
        "wrong-rate": (_pcm16(), 48_000, "16000"),
        "too-short": (_pcm16(1.499), 16_000, "1.5 seconds"),
    }[case]

    with pytest.raises(ValueError, match=message):
        model.embedding_from_pcm16(pcm16, sample_rate_hz=sample_rate_hz)


def test_load_unload_and_close_are_idempotent(tmp_path, monkeypatch) -> None:
    model_bytes = b"reviewed model"
    _patch_expected_asset(monkeypatch, model_bytes)
    directory = _write_asset_dir(tmp_path / "speaker", model_bytes)
    session = _FakeSession(np.ones((1, 192), dtype=np.float32))
    sessions: list[_FakeSession] = []
    _install_fake_onnxruntime(monkeypatch, session, sessions)
    model = CampPlusEmbeddingModel(asset_dir=directory)

    assert model.load()
    assert model.load()
    assert len(sessions) == 1
    assert model.unload()
    assert model.unload()
    assert model.load()
    assert len(sessions) == 2
    model.close()
    model.close()
    assert model.load() is False
    assert model.snapshot()["load_count"] == 2
    assert model.snapshot()["unload_count"] == 2


def test_profile_copies_normalizes_and_hides_reference_embedding() -> None:
    source = np.arange(1, 193, dtype=np.float32)
    profile = CampPlusSpeakerProfile(
        source,
        profile_revision=7,
        model_id=CAMPPLUS_MODEL_ID,
        model_revision=CAMPPLUS_MODEL_REVISION,
    )
    source.fill(0)

    first = profile.reference_embedding
    first.fill(0)
    second = profile.reference_embedding

    assert profile.profile_revision == 7
    assert profile.model_id == CAMPPLUS_MODEL_ID
    assert profile.model_revision == CAMPPLUS_MODEL_REVISION
    assert np.linalg.norm(second) == pytest.approx(1.0, abs=1e-6)
    assert np.any(second != 0)


class _EnrollmentModel:
    model_id = CAMPPLUS_MODEL_ID
    model_revision = CAMPPLUS_MODEL_REVISION

    def __init__(self, embeddings: list[np.ndarray]) -> None:
        self._embeddings = iter(embeddings)
        self.calls = 0

    def embedding_from_pcm16(self, pcm16: bytes, *, sample_rate_hz: int) -> np.ndarray:
        assert len(pcm16) >= 48_000
        assert sample_rate_hz == 16_000
        self.calls += 1
        return np.array(next(self._embeddings), dtype=np.float32, copy=True)


@pytest.mark.parametrize("count", [0, 1, 2, 6])
def test_enrollment_requires_three_to_five_segments(count: int) -> None:
    model = _EnrollmentModel([np.eye(192, dtype=np.float32)[0]] * max(count, 1))

    with pytest.raises(ValueError, match="3 to 5"):
        build_campplus_speaker_profile(
            model,
            [_pcm16()] * count,
            sample_rate_hz=16_000,
            profile_revision=1,
        )


def test_enrollment_rejects_short_segment_and_renormalizes_average() -> None:
    basis = np.eye(192, dtype=np.float32)
    model = _EnrollmentModel([basis[0], basis[1], basis[2]])
    with pytest.raises(ValueError, match="1.5 seconds"):
        build_campplus_speaker_profile(
            model,
            [_pcm16(), _pcm16(), _pcm16(1.0)],
            sample_rate_hz=16_000,
            profile_revision=2,
        )
    assert model.calls == 0

    model = _EnrollmentModel([basis[0], basis[1], basis[2]])
    profile = build_campplus_speaker_profile(
        model,
        [_pcm16(), _pcm16(), _pcm16()],
        sample_rate_hz=16_000,
        profile_revision=3,
    )
    expected = np.zeros(192, dtype=np.float32)
    expected[:3] = 1 / math.sqrt(3)
    assert profile.reference_embedding == pytest.approx(expected, abs=1e-6)
    assert np.linalg.norm(profile.reference_embedding) == pytest.approx(1.0, abs=1e-6)


class _BackendModel:
    model_id = CAMPPLUS_MODEL_ID
    model_revision = CAMPPLUS_MODEL_REVISION

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

    def snapshot(self) -> dict[str, int]:
        return {"inference_count": len(self.embeddings)}


def test_backend_scores_cosine_copies_reference_and_closes() -> None:
    basis = np.eye(192, dtype=np.float32)
    source = basis[0].copy()
    profile = CampPlusSpeakerProfile(source, profile_revision=1)
    model = _BackendModel({b"same": basis[0], b"other": basis[1]})
    backend = CampPlusSpeakerShadowBackend(profile, model_factory=lambda: model)
    source.fill(0)

    assert backend.load()
    assert backend.load()
    assert backend.score(b"same", 16_000) == pytest.approx(1.0, abs=1e-6)
    assert backend.score(b"other", 16_000) == pytest.approx(0.0, abs=1e-6)
    backend.close()
    backend.close()

    assert model.load_calls == 1
    assert model.close_calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        backend.score(b"same", 16_000)


def test_shadow_factory_is_zero_work_when_disabled_or_profile_missing(monkeypatch) -> None:
    import main_logic.asr_client.campplus as campplus

    validate = pytest.fail
    monkeypatch.setattr(campplus, "resolve_verified_campplus_asset", validate)
    profile = CampPlusSpeakerProfile(np.eye(192, dtype=np.float32)[0], profile_revision=1)

    assert CampPlusSpeakerShadowFactory(enabled=False, profile=profile)() is None
    assert CampPlusSpeakerShadowFactory(enabled=True, profile=None)() is None


def test_shadow_factory_contains_missing_or_corrupt_model(monkeypatch, caplog) -> None:
    import main_logic.asr_client.campplus as campplus

    profile = CampPlusSpeakerProfile(np.eye(192, dtype=np.float32)[0], profile_revision=1)

    def fail(_asset_dir):
        raise CampPlusAssetError("corrupt")

    monkeypatch.setattr(campplus, "resolve_verified_campplus_asset", fail)
    factory = CampPlusSpeakerShadowFactory(enabled=True, profile=profile)

    with caplog.at_level(logging.WARNING, logger=campplus.__name__):
        assert factory() is None

    assert "CAM++ speaker shadow factory unavailable: corrupt" in caplog.text


def test_shadow_factory_builds_lazy_runtime_without_loading_model(monkeypatch, tmp_path) -> None:
    import main_logic.asr_client.campplus as campplus

    profile = CampPlusSpeakerProfile(np.eye(192, dtype=np.float32)[0], profile_revision=4)
    verified = tmp_path / CAMPPLUS_FILENAME
    verified.write_bytes(b"model")
    validate_calls: list[Path | None] = []
    model_calls: list[Path | None] = []

    def validate(asset_dir):
        validate_calls.append(asset_dir)
        return verified

    def model_factory(*, asset_dir=None):
        model_calls.append(asset_dir)
        return _BackendModel({})

    monkeypatch.setattr(campplus, "resolve_verified_campplus_asset", validate)
    factory = CampPlusSpeakerShadowFactory(
        enabled=True,
        profile=profile,
        asset_dir=tmp_path,
        model_factory=model_factory,
    )

    runtime = factory()

    assert isinstance(runtime, SpeakerShadowRuntime)
    assert validate_calls == [tmp_path]
    assert model_calls == []


@pytest.mark.asyncio
async def test_shadow_runtime_close_overwrites_private_profile_copy(
    monkeypatch, tmp_path
) -> None:
    import main_logic.asr_client.campplus as campplus

    profile = CampPlusSpeakerProfile(np.eye(192, dtype=np.float32)[0], profile_revision=5)
    monkeypatch.setattr(
        campplus,
        "resolve_verified_campplus_asset",
        lambda _asset_dir: tmp_path / CAMPPLUS_FILENAME,
    )
    runtime = CampPlusSpeakerShadowFactory(
        enabled=True,
        profile=profile,
        asset_dir=tmp_path,
    )()
    assert runtime is not None
    private_profile = runtime._backend_factory._profile
    assert np.linalg.norm(private_profile.reference_embedding) == pytest.approx(1.0)

    await runtime.close()

    assert np.linalg.norm(private_profile._reference_embedding) == 0.0
    with pytest.raises(RuntimeError, match="closed"):
        _ = private_profile.reference_embedding
    assert np.linalg.norm(profile.reference_embedding) == pytest.approx(1.0)
