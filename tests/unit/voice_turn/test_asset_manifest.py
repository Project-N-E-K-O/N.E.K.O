import hashlib
import json

import pytest

from main_logic.voice_turn.asset_manifest import (
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
