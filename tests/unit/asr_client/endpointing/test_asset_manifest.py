import hashlib
import json
import sys
from pathlib import Path

import pytest

from main_logic.asr_client.endpointing import asset_manifest
from main_logic.asr_client.endpointing.asset_manifest import (
    AssetManifestError,
    load_manifest,
    resolve_verified_assets,
    verify_asset,
)


def _write_manifest(directory, *, digest):
    payload = {
        "schema_version": 1,
        "assets": [
            {
                "filename": "model.onnx",
                "version": "test",
                "source": "https://example.test/model.onnx",
                "license": "BSD-2-Clause",
                "sha256": digest,
                "input_contract": "float32[1]",
                "output_contract": "probability[1]",
            }
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")


def test_manifest_verifies_declared_asset(tmp_path):
    content = b"model"
    (tmp_path / "model.onnx").write_bytes(content)
    _write_manifest(tmp_path, digest=hashlib.sha256(content).hexdigest())
    manifest = load_manifest(tmp_path)
    assert verify_asset(tmp_path, manifest.asset("model.onnx")).name == "model.onnx"


def test_manifest_rejects_sha_mismatch(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"corrupt")
    _write_manifest(tmp_path, digest="0" * 64)
    with pytest.raises(AssetManifestError, match="SHA-256 mismatch"):
        resolve_verified_assets(["model.onnx"], override=tmp_path)


def test_manifest_rejects_path_traversal_filename(tmp_path):
    _write_manifest(tmp_path, digest="0" * 64)
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    raw["assets"][0]["filename"] = "../model.onnx"
    (tmp_path / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(AssetManifestError, match="must not contain a path"):
        load_manifest(tmp_path)


def test_generator_required_filenames_survive_candidate_fallback(monkeypatch, tmp_path):
    invalid = tmp_path / "invalid"
    valid = tmp_path / "valid"
    invalid.mkdir()
    valid.mkdir()
    content = b"model"
    _write_manifest(invalid, digest=hashlib.sha256(content).hexdigest())
    (valid / "model.onnx").write_bytes(content)
    _write_manifest(valid, digest=hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(asset_manifest, "candidate_asset_dirs", lambda override: (invalid, valid))

    required = (filename for filename in ("model.onnx",))
    directory, _, paths = resolve_verified_assets(required)
    assert directory == valid
    assert paths == {"model.onnx": valid / "model.onnx"}


def test_explicit_asset_override_never_falls_back(monkeypatch, tmp_path):
    invalid = tmp_path / "invalid"
    valid = tmp_path / "valid"
    invalid.mkdir()
    valid.mkdir()
    content = b"model"
    _write_manifest(invalid, digest=hashlib.sha256(content).hexdigest())
    (valid / "model.onnx").write_bytes(content)
    _write_manifest(valid, digest=hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(asset_manifest, "candidate_asset_dirs", lambda _override: (valid,))

    with pytest.raises(AssetManifestError, match="asset is missing"):
        resolve_verified_assets(["model.onnx"], override=invalid)


def test_source_tree_candidate_is_adjacent_models_directory(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delitem(asset_manifest.__dict__, "__compiled__", raising=False)

    candidates = asset_manifest.candidate_asset_dirs()

    assert candidates == (
        Path(asset_manifest.__file__).resolve().parent / "models",
    )


def test_pyinstaller_candidate_uses_meipass_relative_package_path(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delitem(asset_manifest.__dict__, "__compiled__", raising=False)

    assert asset_manifest.candidate_asset_dirs()[0] == (
        tmp_path / asset_manifest.ASSET_RELATIVE_PATH
    ).resolve()


def test_frozen_executable_candidate_uses_runtime_directory(monkeypatch, tmp_path):
    executable = tmp_path / "Xiao8.exe"
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.delitem(asset_manifest.__dict__, "__compiled__", raising=False)

    assert asset_manifest.candidate_asset_dirs()[0] == (
        tmp_path / asset_manifest.ASSET_RELATIVE_PATH
    ).resolve()


def test_nuitka_compiled_candidate_uses_runtime_directory(monkeypatch, tmp_path):
    executable = tmp_path / "Xiao8"
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setitem(asset_manifest.__dict__, "__compiled__", object())

    assert asset_manifest.candidate_asset_dirs()[0] == (
        tmp_path / asset_manifest.ASSET_RELATIVE_PATH
    ).resolve()


def test_candidate_directories_are_deduplicated(monkeypatch):
    source = Path(asset_manifest.__file__).resolve().parent / "models"
    monkeypatch.setattr(sys, "_MEIPASS", str(source.parents[3]), raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delitem(asset_manifest.__dict__, "__compiled__", raising=False)

    assert asset_manifest.candidate_asset_dirs().count(source.resolve()) == 1


def test_empty_asset_fails_closed(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"")
    _write_manifest(tmp_path, digest=hashlib.sha256(b"").hexdigest())

    with pytest.raises(AssetManifestError, match="missing or empty"):
        resolve_verified_assets(["model.onnx"], override=tmp_path)


def test_runtime_resolution_never_opens_the_network(monkeypatch, tmp_path):
    _write_manifest(tmp_path, digest="0" * 64)

    def fail_network(*_args, **_kwargs):
        raise AssertionError("runtime asset resolution must not access the network")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)

    with pytest.raises(AssetManifestError, match="asset is missing"):
        resolve_verified_assets(["model.onnx"], override=tmp_path)
