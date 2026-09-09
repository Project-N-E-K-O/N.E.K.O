from __future__ import annotations

import json
from pathlib import Path
import wave

import numpy as np
import pytest

from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
)
from main_logic.voice_identity_service.calibration import (
    CalibrationError,
    CalibrationExample,
    REFERENCE_FORMATION_PROTOCOL,
)
import scripts.prepare_voice_identity_calibration_dataset as builder


def _write_wav(path: Path, samples: np.ndarray, *, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(samples.astype("<i2", copy=False).tobytes())


def _protocol() -> dict[str, object]:
    return {
        "model_id": CAMPPLUS_MODEL_ID,
        "model_revision": CAMPPLUS_MODEL_REVISION,
        "embedding_dimension": 192,
        "preprocessing_contract_id": "owner-campplus-desktop-v1",
        "preprocessing_revision": 1,
        "noise_reduction_enabled": False,
        "reference_formation_protocol": REFERENCE_FORMATION_PROTOCOL,
        "reference_recording_count": 3,
        "application_scope": "terminal_short_v1",
        "minimum_audio_ms": 45.0,
        "maximum_audio_ms_exclusive": 1500.0,
    }


def _trial() -> dict[str, object]:
    return {
        "example_id": "example-opaque",
        "group_id": "group-opaque",
        "reference_speaker_id": "speaker-reference-opaque",
        "candidate_speaker_id": "speaker-reference-opaque",
        "candidate_session_id": "session-candidate-opaque",
        "reference_session_ids": ["session-r1", "session-r2", "session-r3"],
        "candidate_recording_family_id": "family-candidate-opaque",
        "reference_recording_family_ids": ["family-r1", "family-r2", "family-r3"],
        "source_recording_id": "recording-candidate-opaque",
        "reference_recording_ids": ["recording-r1", "recording-r2", "recording-r3"],
        "split": "train",
        "label": "owner",
        "candidate_wav": "candidate-secret.wav",
        "reference_wavs": ["reference-secret-1.wav", "reference-secret-2.wav", "reference-secret-3.wav"],
    }


def _second_trial(*, split: str = "dev") -> dict[str, object]:
    trial = _trial()
    trial.update(
        {
            "example_id": "example-opaque-2",
            "group_id": "group-opaque-2",
            "reference_speaker_id": "speaker-reference-opaque-2",
            "candidate_speaker_id": "speaker-reference-opaque-2",
            "candidate_session_id": "session-candidate-opaque-2",
            "reference_session_ids": ["session-2-r1", "session-2-r2", "session-2-r3"],
            "candidate_recording_family_id": "family-candidate-opaque-2",
            "reference_recording_family_ids": ["family-2-r1", "family-2-r2", "family-2-r3"],
            "source_recording_id": "recording-candidate-opaque-2",
            "reference_recording_ids": ["recording-2-r1", "recording-2-r2", "recording-2-r3"],
            "split": split,
            "candidate_wav": "candidate-secret-2.wav",
            "reference_wavs": ["reference-2-1.wav", "reference-2-2.wav", "reference-2-3.wav"],
        }
    )
    return trial


def _write_valid_trial_audio(tmp_path: Path, trial: dict[str, object], seed: int) -> None:
    for index, name in enumerate(trial["reference_wavs"]):
        _write_wav(tmp_path / name, np.full(48_000, seed + index + 1, dtype=np.int16))
    candidate = np.array([seed, -seed, seed + 7, -(seed + 7)] * 180, dtype=np.int16)
    _write_wav(tmp_path / trial["candidate_wav"], candidate)


class _Model:
    model_id = CAMPPLUS_MODEL_ID
    model_revision = CAMPPLUS_MODEL_REVISION

    def __init__(self) -> None:
        self.production_lengths: list[int] = []
        self.production_peaks: list[int] = []
        self.short_lengths: list[int] = []
        self.returned: list[np.ndarray] = []
        self.closed = False

    def load(self) -> bool:
        return True

    def _embedding(self) -> np.ndarray:
        value = np.zeros(192, dtype=np.float32)
        value[0] = 1.0
        self.returned.append(value)
        return value

    def embedding_from_pcm16(self, pcm16: bytes, *, sample_rate_hz: int) -> np.ndarray:
        assert type(pcm16) is bytes
        assert sample_rate_hz == 16_000
        self.production_lengths.append(len(pcm16) // 2)
        self.production_peaks.append(int(np.max(np.frombuffer(pcm16, dtype="<i2"))))
        return self._embedding()

    def probe_short_input_embedding_from_pcm16(
        self, pcm16: bytes, *, sample_rate_hz: int
    ) -> np.ndarray:
        assert type(pcm16) is bytes
        assert sample_rate_hz == 16_000
        self.short_lengths.append(len(pcm16) // 2)
        return self._embedding()

    def close(self) -> None:
        self.closed = True


def test_builder_uses_three_long_references_and_exact_unpadded_candidate(tmp_path) -> None:
    candidate = np.array([0, 328, -328, 32760, -32760] * 144, dtype=np.int16)
    trial = _trial()
    for index, name in enumerate(trial["reference_wavs"]):
        reference = np.concatenate(
            (
                np.full(48_000, 1000 + index * 100, dtype=np.int16),
                np.full(2_000, 20_000, dtype=np.int16),
            )
        )
        _write_wav(tmp_path / name, reference)
    _write_wav(tmp_path / trial["candidate_wav"], candidate)
    model = _Model()

    dataset = builder.build_dataset(
        {"protocol": _protocol(), "trials": [trial]},
        manifest_dir=tmp_path,
        model_factory=lambda: model,
    )

    assert model.production_lengths == [48_000, 48_000, 48_000]
    assert model.production_peaks == [1000, 1100, 1200]
    assert model.short_lengths == [720]
    assert model.closed
    assert all(not np.any(value) for value in model.returned)
    assert dataset["schema_version"] == 1
    assert len(dataset["examples"]) == 1
    example = CalibrationExample.from_dict(dataset["examples"][0])
    assert example.features.audio_ms == 45.0
    assert example.features.raw_similarity == pytest.approx(1.0)
    assert example.features.rms == pytest.approx(0.632)
    assert example.features.peak == pytest.approx(1.0)
    assert example.features.near_silence == pytest.approx(0.6)
    assert example.features.clipping == pytest.approx(0.4)
    serialized = json.dumps(dataset)
    assert "secret.wav" not in serialized
    assert "pcm16" not in serialized
    assert "candidate_wav" not in dataset["examples"][0]
    assert "reference_wavs" not in dataset["examples"][0]


@pytest.mark.parametrize(
    "payload",
    [
        '{"protocol": {}, "protocol": {}, "trials": []}',
        '{"protocol": {}, "trials": NaN}',
        '[]',
    ],
)
def test_manifest_parser_rejects_duplicate_nonfinite_and_wrong_top_level(
    tmp_path, payload
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(CalibrationError):
        builder.load_manifest(path)


def test_trial_requires_exact_json_arrays() -> None:
    trial = _trial()
    trial["reference_wavs"] = tuple(trial["reference_wavs"])
    model = _Model()
    with pytest.raises(CalibrationError, match="reference_wavs"):
        builder.build_dataset(
            {"protocol": _protocol(), "trials": [trial]},
            manifest_dir=Path("unused"),
            model_factory=lambda: model,
        )
    assert model.closed


def test_trial_rejects_repeated_resolved_wav_path(tmp_path) -> None:
    trial = _trial()
    trial["candidate_wav"] = trial["reference_wavs"][0]
    model = _Model()
    with pytest.raises(CalibrationError, match="paths must be distinct"):
        builder.build_dataset(
            {"protocol": _protocol(), "trials": [trial]},
            manifest_dir=tmp_path,
            model_factory=lambda: model,
        )
    assert model.closed


def test_reference_files_with_identical_model_input_are_rejected(tmp_path) -> None:
    trial = _trial()
    identical = np.full(48_000, 111, dtype=np.int16)
    for name in trial["reference_wavs"]:
        _write_wav(tmp_path / name, identical)
    model = _Model()
    with pytest.raises(CalibrationError, match="distinct content"):
        builder.build_dataset(
            {"protocol": _protocol(), "trials": [trial]},
            manifest_dir=tmp_path,
            model_factory=lambda: model,
        )
    assert model.closed


def test_candidate_reference_exact_substring_is_rejected(tmp_path) -> None:
    trial = _trial()
    for index, name in enumerate(trial["reference_wavs"]):
        _write_wav(tmp_path / name, np.full(48_000, 500 + index, dtype=np.int16))
    _write_wav(tmp_path / trial["candidate_wav"], np.full(720, 500, dtype=np.int16))
    model = _Model()
    with pytest.raises(CalibrationError, match="substring"):
        builder.build_dataset(
            {"protocol": _protocol(), "trials": [trial]},
            manifest_dir=tmp_path,
            model_factory=lambda: model,
        )
    assert model.short_lengths == []
    assert model.closed


def test_manifest_rejects_content_reuse_across_splits(tmp_path) -> None:
    first, second = _trial(), _second_trial()
    _write_valid_trial_audio(tmp_path, first, 100)
    _write_valid_trial_audio(tmp_path, second, 200)
    shared = np.array([100, -100, 107, -107] * 180, dtype=np.int16)
    _write_wav(tmp_path / second["candidate_wav"], shared)
    model = _Model()
    with pytest.raises(CalibrationError, match="aliased or crosses"):
        builder.build_dataset(
            {"protocol": _protocol(), "trials": [first, second]},
            manifest_dir=tmp_path,
            model_factory=lambda: model,
        )
    assert model.closed


def test_manifest_rejects_same_recording_id_with_changed_content(tmp_path) -> None:
    first, second = _trial(), _second_trial(split="train")
    second["source_recording_id"] = first["source_recording_id"]
    _write_valid_trial_audio(tmp_path, first, 100)
    _write_valid_trial_audio(tmp_path, second, 200)
    model = _Model()
    with pytest.raises(CalibrationError, match="inconsistent content"):
        builder.build_dataset(
            {"protocol": _protocol(), "trials": [first, second]},
            manifest_dir=tmp_path,
            model_factory=lambda: model,
        )
    assert model.closed


def test_manifest_rejects_cross_trial_candidate_reference_substring(tmp_path) -> None:
    first, second = _trial(), _second_trial(split="train")
    _write_valid_trial_audio(tmp_path, first, 100)
    _write_valid_trial_audio(tmp_path, second, 200)
    reference_prefix = np.arange(48_000, dtype=np.int16)
    _write_wav(tmp_path / first["reference_wavs"][0], reference_prefix)
    _write_wav(tmp_path / second["candidate_wav"], reference_prefix[500:1220])
    model = _Model()
    with pytest.raises(CalibrationError, match="substring"):
        builder.build_dataset(
            {"protocol": _protocol(), "trials": [first, second]},
            manifest_dir=tmp_path,
            model_factory=lambda: model,
        )
    assert model.production_lengths == []
    assert model.closed


def test_same_recording_id_content_and_split_may_be_reused(tmp_path) -> None:
    first, second = _trial(), _second_trial(split="train")
    second["source_recording_id"] = first["source_recording_id"]
    _write_valid_trial_audio(tmp_path, first, 100)
    _write_valid_trial_audio(tmp_path, second, 200)
    shared_candidate = np.array([100, -100, 107, -107] * 180, dtype=np.int16)
    _write_wav(tmp_path / second["candidate_wav"], shared_candidate)
    model = _Model()
    dataset = builder.build_dataset(
        {"protocol": _protocol(), "trials": [first, second]},
        manifest_dir=tmp_path,
        model_factory=lambda: model,
    )
    assert len(dataset["examples"]) == 2
    assert model.closed


def test_builder_rejects_preprocessing_protocol_mismatch_before_reading_wav() -> None:
    protocol = _protocol()
    protocol["preprocessing_contract_id"] = "unrelated-preprocessing-v1"
    model = _Model()
    with pytest.raises(CalibrationError, match="protocol does not match"):
        builder.build_dataset(
            {"protocol": protocol, "trials": [_trial()]},
            manifest_dir=Path("unused"),
            model_factory=lambda: model,
        )
    assert model.closed


def test_invalid_wav_contract_fails_and_closes_model(tmp_path) -> None:
    trial = _trial()
    stereo = np.zeros(48_000, dtype=np.int16)
    _write_wav(tmp_path / trial["reference_wavs"][0], stereo, channels=2)
    model = _Model()
    with pytest.raises(CalibrationError, match="mono"):
        builder.build_dataset(
            {"protocol": _protocol(), "trials": [trial]},
            manifest_dir=tmp_path,
            model_factory=lambda: model,
        )
    assert model.closed


def test_reference_requires_three_seconds_without_padding(tmp_path) -> None:
    trial = _trial()
    _write_wav(
        tmp_path / trial["reference_wavs"][0],
        np.zeros(47_999, dtype=np.int16),
    )
    model = _Model()
    with pytest.raises(CalibrationError, match="at_least_3_seconds"):
        builder.build_dataset(
            {"protocol": _protocol(), "trials": [trial]},
            manifest_dir=tmp_path,
            model_factory=lambda: model,
        )
    assert model.production_lengths == []
    assert model.closed


@pytest.mark.parametrize("sample_count", [719, 24_000])
def test_candidate_must_stay_inside_terminal_short_sample_domain(
    tmp_path, sample_count
) -> None:
    trial = _trial()
    for index, name in enumerate(trial["reference_wavs"]):
        _write_wav(tmp_path / name, np.full(48_000, index + 1, dtype=np.int16))
    _write_wav(
        tmp_path / trial["candidate_wav"],
        np.zeros(sample_count, dtype=np.int16),
    )
    model = _Model()
    with pytest.raises(CalibrationError, match="720_to_23999_samples"):
        builder.build_dataset(
            {"protocol": _protocol(), "trials": [trial]},
            manifest_dir=tmp_path,
            model_factory=lambda: model,
        )
    assert model.short_lengths == []
    assert model.closed


def test_candidate_accepts_terminal_short_upper_adjacent_sample(tmp_path) -> None:
    trial = _trial()
    for index, name in enumerate(trial["reference_wavs"]):
        _write_wav(tmp_path / name, np.full(48_000, index + 10, dtype=np.int16))
    candidate = np.resize(np.array([101, -207, 313], dtype=np.int16), 23_999)
    _write_wav(tmp_path / trial["candidate_wav"], candidate)
    model = _Model()
    dataset = builder.build_dataset(
        {"protocol": _protocol(), "trials": [trial]},
        manifest_dir=tmp_path,
        model_factory=lambda: model,
    )
    assert len(dataset["examples"]) == 1
    assert model.short_lengths == [23_999]
    assert model.closed


def test_atomic_write_failure_leaves_no_partial_output(tmp_path, monkeypatch) -> None:
    output = tmp_path / "dataset.json"

    def fail_dump(*_args, **_kwargs):
        raise OSError("simulated")

    monkeypatch.setattr(builder.json, "dump", fail_dump)
    with pytest.raises(OSError, match="simulated"):
        builder.write_dataset_atomic(output, {"schema_version": 1, "examples": []})

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_cli_rejects_invalid_manifest_without_traceback_or_partial_output(
    tmp_path, capsys
) -> None:
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "dataset.json"
    manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        builder.main(["--manifest", str(manifest), "--output", str(output)])
    assert raised.value.code == 2
    stderr = capsys.readouterr().err
    assert "Traceback" not in stderr
    assert not output.exists()
