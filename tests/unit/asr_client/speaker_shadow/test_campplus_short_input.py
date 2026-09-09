from __future__ import annotations

import json
import sys
from types import SimpleNamespace
import wave

import numpy as np
import pytest

import main_logic.asr_client.speaker_shadow.campplus as campplus
import scripts.probe_campplus_short_input as short_probe
from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CampPlusAssetError,
    resolve_verified_campplus_asset,
)
from main_logic.asr_client.speaker_shadow.campplus import (
    CAMPPLUS_EXECUTABLE_MINIMUM_FRAMES,
    CAMPPLUS_EXECUTABLE_MINIMUM_SAMPLES,
    CAMPPLUS_MINIMUM_SAMPLES,
    CampPlusBackendFactory,
    CampPlusEmbeddingModel,
    CampPlusSpeakerShadowBackend,
)
from scripts.probe_campplus_short_input import _read_wav_pcm16


def _pcm16(sample_count: int) -> bytes:
    indices = np.arange(sample_count, dtype=np.float64)
    waveform = 0.2 * np.sin(2 * np.pi * 220 * indices / 16_000)
    return np.rint(waveform * 32767).astype("<i2").tobytes()


class _Session:
    def __init__(self, output: np.ndarray | None = None) -> None:
        self.output = (
            np.ones((1, 192), dtype=np.float32)
            if output is None
            else np.asarray(output, dtype=np.float32)
        )
        self.inputs: list[np.ndarray] = []

    def run(self, output_names, inputs, run_options=None):
        assert output_names == ["embedding"]
        assert run_options is not None
        self.inputs.append(np.array(inputs["x"], copy=True))
        return [np.array(self.output, copy=True)]


def _loaded_model(monkeypatch, session: _Session) -> CampPlusEmbeddingModel:
    class _RunOptions:
        terminate = False

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(RunOptions=_RunOptions),
    )
    model = CampPlusEmbeddingModel()
    model._session = session
    return model


def test_short_probe_uses_exact_pcm_while_production_guard_stays_unchanged(
    monkeypatch,
) -> None:
    session = _Session()
    model = _loaded_model(monkeypatch, session)
    pcm16 = _pcm16(720)

    with pytest.raises(ValueError, match="pcm_too_short"):
        model.embedding_from_pcm16(pcm16, sample_rate_hz=16_000)

    embedding = model.probe_short_input_embedding_from_pcm16(
        pcm16,
        sample_rate_hz=16_000,
    )

    assert CAMPPLUS_MINIMUM_SAMPLES == 24_000
    assert CAMPPLUS_EXECUTABLE_MINIMUM_FRAMES == 3
    assert CAMPPLUS_EXECUTABLE_MINIMUM_SAMPLES == 720
    assert len(session.inputs) == 1
    assert session.inputs[0].shape == (1, 3, 80)
    assert embedding.shape == (192,)
    assert np.linalg.norm(embedding) == pytest.approx(1.0, abs=1e-6)
    embedding.fill(0)
    model.close()


class _RoutingModel:
    def __init__(self) -> None:
        self.production_calls = 0
        self.short_calls = 0

    def load(self) -> bool:
        return True

    def embedding_from_pcm16(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> np.ndarray:
        self.production_calls += 1
        return np.eye(192, dtype=np.float32)[0]

    def probe_short_input_embedding_from_pcm16(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> np.ndarray:
        self.short_calls += 1
        return np.eye(192, dtype=np.float32)[0]

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("allow_short_input", "production_calls", "short_calls"),
    [(False, 1, 0), (True, 0, 1)],
)
def test_backend_short_input_routing_is_explicit_and_default_off(
    allow_short_input,
    production_calls,
    short_calls,
) -> None:
    model = _RoutingModel()
    reference = np.eye(192, dtype=np.float32)[0]
    backend = CampPlusSpeakerShadowBackend(
        reference,
        model_factory=lambda: model,
        allow_short_input=allow_short_input,
    )

    assert backend.load()
    assert backend.score(_pcm16(720), 16_000) == pytest.approx(1.0)
    assert model.production_calls == production_calls
    assert model.short_calls == short_calls
    backend.close()


def test_factory_preserves_explicit_short_input_opt_in() -> None:
    reference = np.eye(192, dtype=np.float32)[0]
    default_factory = CampPlusBackendFactory(reference)
    short_factory = CampPlusBackendFactory(reference, allow_short_input=True)

    default_backend = default_factory()
    short_backend = short_factory()
    try:
        assert default_backend._allow_short_input is False
        assert short_backend._allow_short_input is True
    finally:
        default_backend.close()
        short_backend.close()
        default_factory.close()
        short_factory.close()


def test_wav_probe_input_is_read_byte_for_byte(tmp_path) -> None:
    pcm16 = _pcm16(913)
    wav_path = tmp_path / "voice sample.wav"
    with wave.open(str(wav_path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(pcm16)

    actual = _read_wav_pcm16(wav_path)

    assert actual == pcm16
    assert len(actual) == 913 * 2


def test_wav_cli_probes_exact_window_rejects_oversize_and_hides_path(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    pcm16 = _pcm16(913)
    wav_path = tmp_path / "private voice sample.wav"
    with wave.open(str(wav_path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(pcm16)

    captured_pcm16: list[bytes] = []

    class _ProbeModel:
        def __init__(self, asset_dir=None) -> None:
            pass

        def load(self) -> bool:
            return True

        def probe_short_input_embedding_from_pcm16(
            self,
            value: bytes,
            *,
            sample_rate_hz: int,
        ) -> np.ndarray:
            assert sample_rate_hz == 16_000
            captured_pcm16.append(value)
            return np.eye(192, dtype=np.float32)[0]

        def close(self) -> None:
            pass

    monkeypatch.setattr(short_probe, "CampPlusEmbeddingModel", _ProbeModel)

    assert (
        short_probe.main(
            [
                "--wav",
                str(wav_path),
                "--samples",
                "720",
                "2000",
                "--repetitions",
                "2",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    report = json.loads(output)
    assert captured_pcm16 == [pcm16[: 720 * 2], pcm16[: 720 * 2]]
    assert report["source_kind"] == "wav"
    assert report["windows"][0]["sample_count"] == 720
    assert report["windows"][0]["input_transforms"] == []
    assert report["windows"][1]["error"] == "wav_window_exceeds_source"
    assert str(wav_path) not in output
    assert wav_path.name not in output


def test_pinned_real_model_has_three_frame_finite_execution_boundary() -> None:
    pytest.importorskip("onnxruntime")
    try:
        model_path = resolve_verified_campplus_asset()
    except CampPlusAssetError:
        pytest.skip("verified bundled CAM++ asset unavailable")
    model = CampPlusEmbeddingModel(asset_dir=model_path.parent)
    assert model.load()
    try:
        for sample_count in (400, 560, 719):
            with pytest.raises(ValueError, match="embedding_non_finite"):
                model.probe_short_input_embedding_from_pcm16(
                    _pcm16(sample_count),
                    sample_rate_hz=16_000,
                )
        first = model.probe_short_input_embedding_from_pcm16(
            _pcm16(720),
            sample_rate_hz=16_000,
        )
        second = model.probe_short_input_embedding_from_pcm16(
            _pcm16(720),
            sample_rate_hz=16_000,
        )
    finally:
        model.close()

    assert first.shape == second.shape == (192,)
    assert np.isfinite(first).all()
    assert np.linalg.norm(first) == pytest.approx(1.0, abs=1e-6)
    assert np.array_equal(first, second)
    first.fill(0)
    second.fill(0)


@pytest.mark.parametrize(
    ("pcm16", "sample_rate_hz", "error"),
    [
        (b"", 16_000, "pcm_invalid"),
        (b"\x00", 16_000, "pcm_invalid"),
        (bytearray(b"\x00\x00" * 720), 16_000, "pcm_invalid"),
        (_pcm16(720), 48_000, "sample_rate_mismatch"),
        (_pcm16(399), 16_000, "pcm_below_feature_minimum"),
    ],
)
def test_short_probe_rejects_invalid_or_below_model_pcm(
    monkeypatch,
    pcm16,
    sample_rate_hz,
    error,
) -> None:
    model = _loaded_model(monkeypatch, _Session())

    with pytest.raises(ValueError, match=error):
        model.probe_short_input_embedding_from_pcm16(
            pcm16,
            sample_rate_hz=sample_rate_hz,
        )

    model.close()


def test_short_probe_rejects_non_finite_features_before_onnx(
    monkeypatch,
) -> None:
    session = _Session()
    model = _loaded_model(monkeypatch, session)
    monkeypatch.setattr(
        campplus,
        "compute_campplus_features",
        lambda *_args, **_kwargs: np.full(
            (3, 80),
            np.nan,
            dtype=np.float32,
        ),
    )

    with pytest.raises(ValueError, match="features_non_finite"):
        model.probe_short_input_embedding_from_pcm16(
            _pcm16(720),
            sample_rate_hz=16_000,
        )

    assert session.inputs == []
    model.close()


def test_short_probe_wipes_observable_arrays_after_inference_error(
    monkeypatch,
) -> None:
    feature_buffer = np.full((3, 80), 7.0, dtype=np.float32)
    output_buffer = np.zeros((1, 192), dtype=np.float32)

    class _ObservableSession:
        input_tensor: np.ndarray | None = None

        def run(self, output_names, inputs, run_options=None):
            assert output_names == ["embedding"]
            assert run_options is not None
            self.input_tensor = inputs["x"]
            return [output_buffer]

    session = _ObservableSession()
    model = _loaded_model(monkeypatch, session)  # type: ignore[arg-type]
    monkeypatch.setattr(
        campplus,
        "compute_campplus_features",
        lambda *_args, **_kwargs: feature_buffer,
    )

    with pytest.raises(ValueError, match="embedding_norm"):
        model.probe_short_input_embedding_from_pcm16(
            _pcm16(720),
            sample_rate_hz=16_000,
        )

    assert session.input_tensor is not None
    assert not np.any(feature_buffer)
    assert not np.any(session.input_tensor)
    assert not np.any(output_buffer)
    model.close()


@pytest.mark.parametrize(
    ("output", "error"),
    [
        (np.full((1, 192), np.inf, dtype=np.float32), "embedding_non_finite"),
        (np.zeros((1, 192), dtype=np.float32), "embedding_norm"),
    ],
)
def test_short_probe_rejects_unsafe_embedding_output(
    monkeypatch,
    output,
    error,
) -> None:
    model = _loaded_model(monkeypatch, _Session(output))

    with pytest.raises(ValueError, match=error):
        model.probe_short_input_embedding_from_pcm16(
            _pcm16(720),
            sample_rate_hz=16_000,
        )

    model.close()
