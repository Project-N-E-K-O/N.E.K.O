"""Repair uses pinned transfers, keeps valid files, and only fetches enrollment assets."""

from dataclasses import asdict, replace
import hashlib
import io
import json

import pytest

from main_logic.voice_identity_service import source_model_repair as repair
from scripts import prepare_speaker_model as speaker
from scripts import prepare_voice_turn_assets as endpointing


@pytest.fixture
def assets(tmp_path, monkeypatch):
    project = tmp_path / "project"
    monkeypatch.setattr(repair, "__file__", str(project / "main_logic/voice_identity_service/source_model_repair.py"))
    monkeypatch.setattr(repair.tempfile, "gettempdir", lambda: str(tmp_path))
    camp = project / "main_logic/asr_client/speaker_shadow/models"
    silero = project / "main_logic/asr_client/endpointing/models"
    camp.mkdir(parents=True); silero.mkdir(parents=True)
    scripts = project / "scripts"
    scripts.mkdir()
    for name in ("prepare_speaker_model.py", "prepare_voice_turn_assets.py"):
        (scripts / name).touch()
    camp_bytes, silero_bytes = b"reviewed-campp-fixture", b"reviewed-silero-fixture"
    pinned = replace(speaker._asset_manifest._EXPECTED_MANIFEST, size_bytes=len(camp_bytes), sha256=hashlib.sha256(camp_bytes).hexdigest())
    monkeypatch.setattr(speaker._asset_manifest, "_EXPECTED_MANIFEST", pinned)
    (camp / "manifest.json").write_text(json.dumps(asdict(pinned)))
    spec = dict(filename="silero_vad.onnx", version="fixture", source="https://assets.invalid/silero", license="MIT",
                sha256=hashlib.sha256(silero_bytes).hexdigest(), input_contract="input", output_contract="output")
    unrelated = {**spec, "filename": "smart_turn_v3.onnx", "source": "https://assets.invalid/unrelated"}
    (silero / "manifest.json").write_text(json.dumps({"schema_version": 1, "assets": [spec, unrelated]}))
    requested = []

    class Response(io.BytesIO):
        def geturl(self):
            return "https://assets.invalid/verified"

    def fetch(request, timeout):
        requested.append(request.full_url)
        assert timeout == 60
        if request.full_url == pinned.source:
            return Response(camp_bytes)
        assert request.full_url == spec["source"], "repair must not download Smart Turn"
        return Response(silero_bytes)

    monkeypatch.setattr(speaker.urllib.request, "urlopen", fetch)
    return camp, silero, pinned, camp_bytes, silero_bytes, requested


def test_repair_downloads_and_verifies_only_missing_enrollment_models(assets):
    camp, silero, pinned, camp_bytes, silero_bytes, requested = assets
    repair.repair_source_models()
    assert (camp / pinned.filename).read_bytes() == camp_bytes
    assert (silero / "silero_vad.onnx").read_bytes() == silero_bytes
    assert len(requested) == 2
    assert not (silero / "smart_turn_v3.onnx").exists()
    repair.repair_source_models()
    assert len(requested) == 2, "already verified assets must not be downloaded again"


def test_corrupt_transfer_never_replaces_an_existing_model(assets, monkeypatch):
    camp, silero, pinned, _, _, _ = assets
    original = b"original-invalid-file"
    (camp / pinned.filename).write_bytes(original)

    class Response(io.BytesIO):
        def geturl(self):
            return pinned.source

    monkeypatch.setattr(speaker.urllib.request, "urlopen", lambda *a, **k: Response(b"x" * pinned.size_bytes))
    with pytest.raises(speaker.CampPlusAssetError, match="asset_sha256_mismatch"):
        repair.repair_source_models()
    assert (camp / pinned.filename).read_bytes() == original
    assert not (silero / "silero_vad.onnx").exists()


def test_packaged_environment_cannot_run_source_repair(monkeypatch):
    monkeypatch.setattr(repair.sys, "frozen", True, raising=False)
    assert repair.source_model_repair_available() is False
    with pytest.raises(RuntimeError, match="resource_repair_unavailable"):
        repair.repair_source_models()


def test_installation_without_preparer_scripts_does_not_advertise_one_click_repair(tmp_path, monkeypatch):
    # The wheel and sdist ship main_logic but omit the scripts directory.
    monkeypatch.setattr(repair, "__file__", str(tmp_path / "main_logic/voice_identity_service/source_model_repair.py"))
    assert repair.source_model_repair_available() is False
    with pytest.raises(RuntimeError, match="resource_repair_unavailable"):
        repair.repair_source_models()


@pytest.mark.parametrize("relative", [
    "scripts/prepare_speaker_model.py", "scripts/prepare_voice_turn_assets.py",
    "main_logic/asr_client/speaker_shadow/models/manifest.json",
    "main_logic/asr_client/endpointing/models/manifest.json",
])
def test_source_repair_requires_both_preparers_and_manifests(assets, relative):
    from pathlib import Path
    assert repair.source_model_repair_available() is True
    (Path(repair.__file__).resolve().parents[2] / relative).unlink()
    assert repair.source_model_repair_available() is False


def test_failed_second_model_preserves_the_verified_first_model_and_retry_reuses_it(assets, monkeypatch):
    camp, silero, pinned, camp_bytes, silero_bytes, requested = assets
    original_fetch = speaker.urllib.request.urlopen
    def fail_second(request, timeout):
        if request.full_url == pinned.source:
            return original_fetch(request, timeout)
        return io.BytesIO(b"invalid-silero-download")
    with monkeypatch.context() as failed_attempt:
        failed_attempt.setattr(speaker.urllib.request, "urlopen", fail_second)
        with pytest.raises(endpointing.AssetManifestError, match="SHA-256 mismatch"):
            repair.repair_source_models()
    assert (camp / pinned.filename).read_bytes() == camp_bytes
    assert not (silero / "silero_vad.onnx").exists()
    repair.repair_source_models()
    assert (silero / "silero_vad.onnx").read_bytes() == silero_bytes
    assert requested.count(pinned.source) == 1


def test_worker_reports_model_download_failure_without_publishing_ready(monkeypatch):
    from main_logic.voice_identity_service import resource_manager
    from utils import audio_processor
    monkeypatch.setattr(audio_processor, "DEBUG_SAVE_AUDIO", False)
    def fail():
        raise speaker.CampPlusAssetError("asset_missing")
    monkeypatch.setattr(repair, "repair_source_models", fail)
    class Connection:
        result = None
        closed = False
        def send(self, result):
            self.result = result
        def close(self):
            self.closed = True
    connection = Connection()
    resource_manager._resource_worker(connection, "repair", False, None, b"")
    assert connection.result == {"ok": False, "reason": "resource_model_repair_failed"}
    assert connection.closed


@pytest.mark.parametrize("model", ["campp", "silero"])
def test_oversized_response_is_stopped_before_filling_the_staging_directory(assets, monkeypatch, model):
    from types import SimpleNamespace
    camp, silero, pinned, camp_bytes, _, _ = assets
    maximum = pinned.size_bytes if model == "campp" else 16 * 1024 * 1024
    if model == "silero":
        (camp / pinned.filename).write_bytes(camp_bytes)
    responses = []
    class OversizedResponse:
        consumed = 0
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def geturl(self): return pinned.source
        def read(self, count):
            count = min(count, maximum * 2 - self.consumed)
            self.consumed += count
            return b"x" * count
    def fetch(*_, **__):
        response = OversizedResponse()
        responses.append(response)
        return response
    monkeypatch.setattr(speaker.urllib.request, "urlopen", fetch)
    monkeypatch.setattr(speaker, "time", SimpleNamespace(sleep=lambda _: None))
    with pytest.raises((speaker.CampPlusAssetError, endpointing.AssetManifestError)):
        repair.repair_source_models()
    assert responses
    assert all(response.consumed <= maximum + 1 for response in responses)
    assert not (silero / "silero_vad.onnx").exists()
    if model == "campp":
        assert not (camp / pinned.filename).exists()
    else:
        assert (camp / pinned.filename).read_bytes() == camp_bytes
