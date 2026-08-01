from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

import main_logic.asr_client.speaker_shadow.asset_manifest as asset_manifest
from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CampPlusAssetError,
    candidate_campplus_asset_directories,
    load_campplus_manifest,
    resolve_verified_campplus_asset,
    verify_campplus_asset,
)


ROOT = Path(__file__).resolve().parents[4]
MODELS = ROOT / "main_logic" / "asr_client" / "speaker_shadow" / "models"
ASSET_RELATIVE_PATH = (
    Path("main_logic") / "asr_client" / "speaker_shadow" / "models"
)
MODEL_FILENAME = "campplus-zh-en-advanced.onnx"
MODEL_SHA256 = "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"


def _manifest_payload(*, payload: bytes) -> dict[str, object]:
    return {
        "filename": MODEL_FILENAME,
        "model_id": "iic/speech_campplus_sv_zh_en_16k-common_advanced",
        "revision": "v1.0.0",
        "license": "Apache-2.0",
        "source": (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
            "speaker-recongition-models/"
            "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
        ),
        "export_source": "k2-fsa/sherpa-onnx",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "sample_rate_hz": 16_000,
        "preprocessing": "kaldi-native-fbank-sniped-global-mean-v1",
        "input_contract": "float32[batch,time,80]",
        "output_contract": "float32[batch,192]",
    }


def _write_asset_dir(directory: Path, payload: bytes = b"reviewed model") -> Path:
    directory.mkdir(parents=True)
    (directory / MODEL_FILENAME).write_bytes(payload)
    (directory / "manifest.json").write_text(
        json.dumps(_manifest_payload(payload=payload)),
        encoding="utf-8",
    )
    return directory


def _patch_expected_manifest(monkeypatch, payload: bytes) -> None:
    monkeypatch.setattr(
        asset_manifest,
        "_EXPECTED_MANIFEST",
        asset_manifest.CampPlusManifest(**_manifest_payload(payload=payload)),
    )


def test_repository_manifest_pins_reviewed_model_identity() -> None:
    manifest = load_campplus_manifest(MODELS)

    assert manifest.filename == MODEL_FILENAME
    assert manifest.model_id == "iic/speech_campplus_sv_zh_en_16k-common_advanced"
    assert manifest.revision == "v1.0.0"
    assert manifest.license == "Apache-2.0"
    assert manifest.source.startswith("https://")
    assert manifest.export_source == "k2-fsa/sherpa-onnx"
    assert manifest.size_bytes == 28_281_164
    assert manifest.sha256 == MODEL_SHA256
    assert manifest.sample_rate_hz == 16_000
    assert manifest.preprocessing == "kaldi-native-fbank-sniped-global-mean-v1"
    assert manifest.input_contract == "float32[batch,time,80]"
    assert manifest.output_contract == "float32[batch,192]"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.pop("revision"), "manifest_invalid"),
        (lambda value: value.__setitem__("filename", "../model.onnx"), "manifest_invalid"),
        (lambda value: value.__setitem__("size_bytes", 0), "manifest_invalid"),
        (lambda value: value.__setitem__("sha256", "not-a-digest"), "manifest_invalid"),
        (
            lambda value: value.__setitem__("sample_rate_hz", 48_000),
            "manifest_identity_mismatch",
        ),
    ],
)
def test_manifest_rejects_malformed_security_fields(
    tmp_path,
    mutation,
    message,
) -> None:
    payload = _manifest_payload(payload=b"model")
    mutation(payload)
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CampPlusAssetError, match=message):
        load_campplus_manifest(tmp_path)


def test_asset_verification_rejects_missing_empty_wrong_size_and_wrong_sha(
    tmp_path,
    monkeypatch,
) -> None:
    payload = b"reviewed model"
    _patch_expected_manifest(monkeypatch, payload)
    directory = _write_asset_dir(tmp_path / "speaker", payload)
    model_path = directory / MODEL_FILENAME

    assert verify_campplus_asset(directory) == model_path

    model_path.unlink()
    with pytest.raises(CampPlusAssetError, match="asset_missing"):
        verify_campplus_asset(directory)

    model_path.write_bytes(b"")
    with pytest.raises(CampPlusAssetError, match="asset_size_mismatch"):
        verify_campplus_asset(directory)

    model_path.write_bytes(b"short")
    with pytest.raises(CampPlusAssetError, match="asset_size_mismatch"):
        verify_campplus_asset(directory)

    corrupt = b"reviewed modeX"
    assert len(corrupt) == len(b"reviewed model")
    model_path.write_bytes(corrupt)
    with pytest.raises(CampPlusAssetError, match="asset_sha256_mismatch"):
        verify_campplus_asset(directory)


def test_explicit_override_is_the_only_candidate(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "runtime" / "Xiao8.exe"))

    override = tmp_path / "explicit"

    assert candidate_campplus_asset_directories(override) == (override.resolve(),)


def test_default_candidate_order_is_bundle_executable_then_package(
    monkeypatch,
    tmp_path,
) -> None:
    bundle = tmp_path / "bundle"
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(runtime / "Xiao8.exe"))

    candidates = candidate_campplus_asset_directories()

    assert candidates == (
        (bundle / ASSET_RELATIVE_PATH).resolve(),
        (runtime / ASSET_RELATIVE_PATH).resolve(),
        MODELS.resolve(),
    )


def test_candidate_directories_are_deduplicated(monkeypatch) -> None:
    package_root = MODELS.parents[3]
    monkeypatch.setattr(sys, "_MEIPASS", str(package_root), raising=False)
    monkeypatch.setattr(
        sys,
        "executable",
        str(package_root / "Xiao8.exe"),
    )

    candidates = candidate_campplus_asset_directories()

    assert candidates.count(MODELS.resolve()) == 1


def test_invalid_explicit_override_never_falls_back(monkeypatch, tmp_path) -> None:
    invalid = tmp_path / "invalid"
    valid = _write_asset_dir(tmp_path / "valid")
    monkeypatch.setattr(
        asset_manifest,
        "candidate_campplus_asset_directories",
        lambda override=None: (override.resolve(),) if override is not None else (valid,),
    )

    with pytest.raises(CampPlusAssetError):
        resolve_verified_campplus_asset(invalid)


def test_runtime_resolution_never_opens_network(monkeypatch, tmp_path) -> None:
    def fail_network(*_args, **_kwargs):
        raise AssertionError("runtime CAM++ resolution must remain offline")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)

    with pytest.raises(CampPlusAssetError):
        resolve_verified_campplus_asset(tmp_path)
