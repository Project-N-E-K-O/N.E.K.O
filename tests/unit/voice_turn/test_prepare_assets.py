import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import main_logic.voice_turn.asset_manifest as asset_manifest
import scripts.prepare_voice_turn_assets as preparer
from main_logic.voice_turn.asset_manifest import AssetManifestError
from scripts.prepare_voice_turn_assets import prepare_assets


SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "prepare_voice_turn_assets.py"

# Runs the preparer on a bare interpreter (-I -S: no site-packages, no env
# influence) with NumPy imports force-blocked, mirroring the Docker build
# step that executes the script before `uv sync` installs dependencies.
_NO_NUMPY_DRIVER = textwrap.dedent(
    """
    import runpy
    import sys

    class _BlockNumpy:
        def find_spec(self, name, path=None, target=None):
            if name == "numpy" or name.startswith("numpy."):
                raise ModuleNotFoundError("numpy blocked: preparer must be stdlib-only")
            return None

    sys.meta_path.insert(0, _BlockNumpy())
    script, asset_dir = sys.argv[1], sys.argv[2]
    sys.argv = [script, "--offline", "--asset-dir", asset_dir]
    runpy.run_path(script, run_name="__main__")
    """
)


def _run_preparer_without_numpy(asset_dir):
    return subprocess.run(
        [sys.executable, "-I", "-S", "-c", _NO_NUMPY_DRIVER, str(SCRIPT_PATH), str(asset_dir)],
        capture_output=True,
        text=True,
        timeout=60,
    )


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


def test_prepare_assets_downloads_and_atomically_verifies(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.write_bytes(b"reviewed model")
    output = tmp_path / "output"
    output.mkdir()
    _manifest(output, source.as_uri(), hashlib.sha256(source.read_bytes()).hexdigest())
    # This test drives the real download path with a file:// URL; production
    # only allows https (see test_download_rejects_a_non_https_source).
    monkeypatch.setattr(
        asset_manifest, "DOWNLOADABLE_SOURCE_SCHEMES", frozenset({"https", "file"})
    )
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


def test_download_sha_mismatch_removes_partial_file(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.write_bytes(b"corrupt model")
    output = tmp_path / "output"
    output.mkdir()
    _manifest(output, source.as_uri(), "0" * 64)
    monkeypatch.setattr(
        asset_manifest, "DOWNLOADABLE_SOURCE_SCHEMES", frozenset({"https", "file"})
    )

    with pytest.raises(AssetManifestError, match="download SHA-256 mismatch"):
        prepare_assets(output)
    assert not (output / "model.onnx").exists()
    assert not (output / "model.onnx.part").exists()


def test_preparer_exception_identity_matches_package_module():
    # The script loads asset_manifest by file path; the sys.modules
    # registration must keep a single class identity so AssetManifestError
    # raised by the script is catchable via the package import.
    assert preparer.AssetManifestError is asset_manifest.AssetManifestError
    assert sys.modules["main_logic.voice_turn.asset_manifest"] is asset_manifest


def test_preparer_dynamic_load_first_shares_exception_identity():
    # This process imports the package before the preparer, so the in-process
    # identity test above never exercises the path-based loader. Run the
    # reversed order in a subprocess: the preparer's dynamic load registers
    # the module first, and the later package import must reuse it.
    driver = textwrap.dedent(
        """
        import sys

        import scripts.prepare_voice_turn_assets as preparer

        assert "main_logic.voice_turn.asset_manifest" in sys.modules, (
            "preparer import must register the path-loaded manifest module"
        )
        import main_logic.voice_turn.asset_manifest as asset_manifest

        assert preparer.AssetManifestError is asset_manifest.AssetManifestError
        assert sys.modules["main_logic.voice_turn.asset_manifest"] is asset_manifest
        print("identity-ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=str(SCRIPT_PATH.parents[1]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "identity-ok" in result.stdout


def test_preparer_verifies_assets_without_numpy(tmp_path):
    # Docker builds run the preparer before project deps exist; it must work
    # end to end on a stdlib-only interpreter with numpy unimportable.
    asset = tmp_path / "model.onnx"
    asset.write_bytes(b"reviewed model")
    _manifest(
        tmp_path,
        "https://example.invalid/model",
        hashlib.sha256(asset.read_bytes()).hexdigest(),
    )

    result = _run_preparer_without_numpy(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "verified model.onnx" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_preparer_reports_manifest_error_without_numpy(tmp_path):
    # Negative validation: on a bad asset the bare interpreter must surface
    # the manifest error, not die earlier on a numpy import.
    asset = tmp_path / "model.onnx"
    asset.write_bytes(b"tampered model")
    _manifest(tmp_path, "https://example.invalid/model", "0" * 64)

    result = _run_preparer_without_numpy(tmp_path)

    assert result.returncode != 0
    assert "SHA-256 mismatch" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "Traceback" not in result.stderr


def test_download_rejects_a_non_https_source(tmp_path):
    # The manifest is in-repo and every downloaded byte is SHA-256 checked
    # before install, so a hostile source cannot substitute content -- but it
    # can still turn the build host into a blind-SSRF probe (the failure text
    # distinguishes "Connection refused" from "timed out"). Gate the transport
    # at the download call site, never at manifest load: verifying an
    # already-present asset never reads `source`, and rejecting there would
    # turn a metadata typo into a runtime voice-turn outage.
    source = tmp_path / "source.bin"
    source.write_bytes(b"reviewed model")
    output = tmp_path / "output"
    output.mkdir()
    _manifest(output, source.as_uri(), hashlib.sha256(source.read_bytes()).hexdigest())

    with pytest.raises(AssetManifestError, match="asset source must use one of"):
        prepare_assets(output)
    assert not (output / "model.onnx").exists()


def test_offline_verification_ignores_the_source_scheme(tmp_path):
    # A non-https source must not make an on-disk, digest-matching asset
    # unloadable: the runtime path never consults `source`.
    payload = b"reviewed model"
    (tmp_path / "model.onnx").write_bytes(payload)
    _manifest(tmp_path, "ftp://example.invalid/model", hashlib.sha256(payload).hexdigest())

    paths = prepare_assets(tmp_path, offline=True)
    assert paths[0].read_bytes() == payload
