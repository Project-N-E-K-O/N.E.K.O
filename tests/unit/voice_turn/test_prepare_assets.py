import hashlib
import json

import pytest

from main_logic.voice_turn.asset_manifest import AssetManifestError
from tools.voice_eval.prepare_voice_turn_assets import prepare_assets


def _manifest(directory, source, digest):
    payload = {
        "schema_version": 1,
        "assets": [
            {
                "filename": "model.onnx",
                "version": "test",
                "source": source,
                "license": "MIT",
                "sha256": digest,
                "input_contract": "test",
                "output_contract": "test",
            }
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")


def test_prepare_assets_downloads_and_atomically_verifies(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"reviewed model")
    output = tmp_path / "output"
    output.mkdir()
    _manifest(output, source.as_uri(), hashlib.sha256(source.read_bytes()).hexdigest())
    paths = prepare_assets(output)
    assert paths[0].read_bytes() == b"reviewed model"
    assert not (output / "model.onnx.part").exists()


def test_offline_mode_rejects_missing_asset(tmp_path):
    _manifest(tmp_path, "https://example.invalid/model", "0" * 64)
    with pytest.raises(AssetManifestError):
        prepare_assets(tmp_path, offline=True)


def test_source_cache_is_verified_before_install(tmp_path):
    output = tmp_path / "output"
    cache = tmp_path / "cache"
    output.mkdir()
    cache.mkdir()
    (cache / "model.onnx").write_bytes(b"wrong")
    _manifest(output, "https://example.invalid/model", "0" * 64)
    with pytest.raises(AssetManifestError, match="cache SHA-256 mismatch"):
        prepare_assets(output, source_cache=cache)
