from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import textwrap
import wave
from pathlib import Path

import numpy as np
import pytest

import scripts.evaluate_campplus_shadow as evaluator
import scripts.prepare_speaker_model as preparer
from main_logic.asr_client.speaker_shadow.asset_manifest import CampPlusAssetError


ROOT = Path(__file__).resolve().parents[4]
PREPARER = ROOT / "scripts" / "prepare_speaker_model.py"
MODEL_FILENAME = "campplus-zh-en-advanced.onnx"


def _manifest(directory: Path, *, payload: bytes, source: str = "https://example.test/model") -> None:
    manifest = {
        "filename": MODEL_FILENAME,
        "model_id": "iic/speech_campplus_sv_zh_en_16k-common_advanced",
        "revision": "v1.0.0",
        "license": "Apache-2.0",
        "source": source,
        "export_source": "k2-fsa/sherpa-onnx",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "sample_rate_hz": 16_000,
        "preprocessing": "kaldi-native-fbank-sniped-global-mean-v1",
        "input_contract": "float32[batch,time,80]",
        "output_contract": "float32[batch,192]",
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class _Response(io.BytesIO):
    def geturl(self) -> str:
        return "https://example.test/model"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_preparer_downloads_atomically_and_verifies_size_and_sha(
    tmp_path,
    monkeypatch,
) -> None:
    payload = b"reviewed model"
    _manifest(tmp_path, payload=payload)
    monkeypatch.setattr(
        preparer._asset_manifest,
        "_EXPECTED_MANIFEST",
        preparer._asset_manifest.CampPlusManifest(
            **json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        ),
    )
    monkeypatch.setattr(
        preparer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(payload),
    )

    prepared = preparer.prepare_speaker_model(tmp_path)

    assert prepared.read_bytes() == payload
    assert not (tmp_path / f"{MODEL_FILENAME}.part").exists()


def test_preparer_offline_and_source_cache_are_fail_closed(tmp_path, monkeypatch) -> None:
    payload = b"reviewed model"
    output = tmp_path / "output"
    cache = tmp_path / "cache"
    output.mkdir()
    cache.mkdir()
    _manifest(output, payload=payload)
    monkeypatch.setattr(
        preparer._asset_manifest,
        "_EXPECTED_MANIFEST",
        preparer._asset_manifest.CampPlusManifest(
            **json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        ),
    )

    with pytest.raises(CampPlusAssetError):
        preparer.prepare_speaker_model(output, offline=True)

    (cache / MODEL_FILENAME).write_bytes(b"wrong model")
    with pytest.raises(CampPlusAssetError, match="asset_size_mismatch"):
        preparer.prepare_speaker_model(output, source_cache=cache)
    assert not (output / MODEL_FILENAME).exists()
    assert not (output / f"{MODEL_FILENAME}.part").exists()

    (cache / MODEL_FILENAME).write_bytes(payload)
    assert preparer.prepare_speaker_model(output, source_cache=cache).read_bytes() == payload
    assert preparer.prepare_speaker_model(output, offline=True).read_bytes() == payload


def test_preparer_rejects_non_https_download_source(tmp_path, monkeypatch) -> None:
    payload = b"reviewed model"
    _manifest(tmp_path, payload=payload, source="file:///local/model.onnx")
    monkeypatch.setattr(
        preparer._asset_manifest,
        "_EXPECTED_MANIFEST",
        preparer._asset_manifest.CampPlusManifest(
            **json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        ),
    )

    with pytest.raises(CampPlusAssetError, match="manifest_invalid"):
        preparer.prepare_speaker_model(tmp_path)

    assert not (tmp_path / MODEL_FILENAME).exists()


def test_preparer_removes_partial_file_after_bad_download(tmp_path, monkeypatch) -> None:
    payload = b"reviewed model"
    _manifest(tmp_path, payload=payload)
    monkeypatch.setattr(
        preparer._asset_manifest,
        "_EXPECTED_MANIFEST",
        preparer._asset_manifest.CampPlusManifest(
            **json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        ),
    )
    monkeypatch.setattr(
        preparer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"corrupt model"),
    )

    with pytest.raises(CampPlusAssetError, match="size|SHA-256"):
        preparer.prepare_speaker_model(tmp_path)

    assert not (tmp_path / MODEL_FILENAME).exists()
    assert not (tmp_path / f"{MODEL_FILENAME}.part").exists()


def test_preparer_runs_on_bare_interpreter_without_numpy() -> None:
    driver = textwrap.dedent(
        """
        import runpy
        import sys

        class _BlockNumpy:
            def find_spec(self, name, path=None, target=None):
                if name == "numpy" or name.startswith("numpy."):
                    raise ModuleNotFoundError("numpy blocked")
                return None

        sys.meta_path.insert(0, _BlockNumpy())
        script, asset_dir = sys.argv[1], sys.argv[2]
        module = runpy.run_path(script, run_name="speaker_preparer_probe")
        manifest = module["load_campplus_manifest"](__import__("pathlib").Path(asset_dir))
        print(f"manifest {manifest.filename}")
        """
    )

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            driver,
            str(PREPARER),
            str(ROOT / "main_logic" / "asr_client" / "speaker_shadow" / "models"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"manifest {MODEL_FILENAME}" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def _wav(path: Path, samples: np.ndarray, *, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.asarray(samples, dtype="<i2").tobytes())


class _EvaluationModel:
    embeddings: list[np.ndarray] = []
    calls: list[int] = []
    closed = 0

    def __init__(self, *, asset_dir=None) -> None:
        self.asset_dir = asset_dir

    def load(self) -> bool:
        return True

    def embedding_from_pcm16(self, pcm16: bytes, *, sample_rate_hz: int) -> np.ndarray:
        assert sample_rate_hz == 16_000
        type(self).calls.append(len(pcm16))
        embedding = np.zeros(192, dtype=np.float32)
        embedding[len(type(self).embeddings) % 3] = 1.0
        type(self).embeddings.append(embedding)
        return embedding

    def close(self) -> None:
        type(self).closed += 1


class _EvaluationBackend:
    received_lengths: list[int] = []
    private_references: list[np.ndarray] = []

    def __init__(self, reference_embedding, **_kwargs) -> None:
        reference = np.array(reference_embedding, dtype=np.float32, copy=True)
        type(self).private_references.append(reference)
        self.reference = reference

    def load(self) -> bool:
        return True

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        assert sample_rate_hz == 16_000
        type(self).received_lengths.append(len(pcm16))
        return 0.75

    def close(self) -> None:
        self.reference.fill(0)


def test_evaluation_reports_only_ephemeral_indexed_scores(monkeypatch, tmp_path) -> None:
    _EvaluationModel.embeddings = []
    _EvaluationModel.calls = []
    _EvaluationModel.closed = 0
    _EvaluationBackend.received_lengths = []
    _EvaluationBackend.private_references = []
    monkeypatch.setattr(evaluator, "CampPlusEmbeddingModel", _EvaluationModel)
    monkeypatch.setattr(evaluator, "CampPlusSpeakerShadowBackend", _EvaluationBackend)
    samples = np.arange(16_000 * 5, dtype=np.int16)
    references = [tmp_path / f"reference-{index}.wav" for index in range(3)]
    candidate = tmp_path / "private-candidate.wav"
    for path in [*references, candidate]:
        _wav(path, samples)

    report = evaluator.evaluate(
        reference_paths=references,
        candidate_paths=[candidate],
        asset_dir=tmp_path / "models",
    )

    assert report == {
        "schema_version": 1,
        "reference_segment_count": 3,
        "candidate_count": 1,
        "observations": [
            {
                "candidate_index": 0,
                "similarity": 0.75,
                "would_block": {
                    "0.40": False,
                    "0.44": False,
                    "0.48": False,
                    "0.52": False,
                    "0.55": False,
                },
            }
        ],
    }
    serialized = json.dumps(report, sort_keys=True).lower()
    assert "private-candidate" not in serialized
    assert "reference-" not in serialized
    assert "embedding" not in serialized
    assert "pcm" not in serialized
    assert _EvaluationModel.calls == [160_000, 160_000, 160_000]
    assert _EvaluationBackend.received_lengths == [128_000]
    assert all(np.count_nonzero(value) == 0 for value in _EvaluationModel.embeddings)
    assert all(
        np.count_nonzero(value) == 0
        for value in _EvaluationBackend.private_references
    )
    assert _EvaluationModel.closed == 1


@pytest.mark.parametrize("count", [0, 1, 2, 6])
def test_evaluation_requires_three_to_five_reference_segments(count: int) -> None:
    with pytest.raises(ValueError, match="3 to 5"):
        evaluator.evaluate(
            reference_paths=[Path(f"reference-{index}.wav") for index in range(count)],
            candidate_paths=[Path("candidate.wav")],
            asset_dir=None,
        )


def test_evaluation_rejects_non_mono_pcm16_16khz_wav(tmp_path) -> None:
    path = tmp_path / "stereo.wav"
    _wav(path, np.zeros(48_000, dtype=np.int16), channels=2)

    with pytest.raises(ValueError, match="mono PCM16LE at 16 kHz"):
        evaluator._read_pcm16(path)
