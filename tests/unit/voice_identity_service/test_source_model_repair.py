"""Repair uses pinned transfers, keeps valid files, and only fetches enrollment assets."""

from dataclasses import asdict, replace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from main_logic.voice_identity_service import source_model_repair as repair
from scripts import prepare_speaker_model as speaker
from scripts import prepare_voice_turn_assets as endpointing


@pytest.fixture
def assets(tmp_path, monkeypatch):
    project = tmp_path / "project"
    monkeypatch.setattr(repair, "__file__", str(project / "main_logic/voice_identity_service/source_model_repair.py"))
    camp = project / "main_logic/asr_client/speaker_shadow/models"
    silero = project / "main_logic/asr_client/endpointing/models"
    camp.mkdir(parents=True); silero.mkdir(parents=True)
    scripts = project / "scripts"
    scripts.mkdir()
    for preparer in (speaker, endpointing):
        shutil.copyfile(preparer.__file__, scripts / Path(preparer.__file__).name)
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


@pytest.mark.parametrize("model", ["campp", "silero"])
@pytest.mark.parametrize("contents", [b"not json", b"\xff", b"{}"])
def test_malformed_manifest_never_advertises_repair_or_writes_a_model(assets, model, contents):
    camp, silero, pinned, _, _, requested = assets
    directory = camp if model == "campp" else silero
    (directory / "manifest.json").write_bytes(contents)
    assert repair.source_model_repair_available() is False
    with pytest.raises(RuntimeError, match="resource_repair_unavailable"):
        repair.repair_source_models()
    assert requested == []
    assert not (camp / pinned.filename).exists()
    assert not (silero / "silero_vad.onnx").exists()
    assert not (camp / ".voice-model-repair").exists()


def test_missing_silero_declaration_does_not_advertise_repair(assets):
    _, silero, _, _, _, requested = assets
    manifest = json.loads((silero / "manifest.json").read_text())
    manifest["assets"] = [asset for asset in manifest["assets"] if asset["filename"] != "silero_vad.onnx"]
    (silero / "manifest.json").write_text(json.dumps(manifest))
    assert repair.source_model_repair_available() is False
    assert requested == []


@pytest.mark.asyncio
async def test_reservation_defers_manifest_reads_to_discovery_and_worker(assets, monkeypatch):
    from main_logic.voice_identity_service import resource_manager
    manager = resource_manager.VoiceResourceManager(lambda: False)
    def unexpected_read(_):
        raise AssertionError("manifest reads must not run during synchronous reservation")
    monkeypatch.setattr(repair, "_load_repair_manifests", unexpected_read)
    try:
        operation = manager.reserve("repair")
        assert operation["state"] == "reserved"
        await manager.cancel(operation["operation_id"])
    finally:
        await manager.close()


@pytest.mark.parametrize("model", ["campp", "silero"])
def test_shared_temp_precreation_cannot_overwrite_an_unrelated_file(assets, monkeypatch, tmp_path, model):
    camp, _, _, _, _, _ = assets
    monkeypatch.setattr(repair, "tempfile", SimpleNamespace(gettempdir=lambda: str(tmp_path)), raising=False)
    project = Path(repair.__file__).resolve().parents[2]
    identity = hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:24]
    attacker_stage = tmp_path / ("neko-voice-model-repair-" + identity) / model
    attacker_stage.mkdir(parents=True)
    victim = tmp_path / "unrelated-user-file"
    victim.write_bytes(b"must survive repair")
    os.link(victim, attacker_stage / "manifest.json")
    repair.repair_source_models()
    assert victim.read_bytes() == b"must survive repair"
    assert (attacker_stage / "manifest.json").read_bytes() == b"must survive repair"
    staging = camp / ".voice-model-repair"
    assert {path.name for path in staging.iterdir()} == {"repair.lock"}


@pytest.mark.parametrize("relative", [
    "repair.lock", "campp/manifest.json", "campp/campplus-zh-en-advanced.onnx",
    "campp/campplus-zh-en-advanced.onnx.part", "silero/manifest.json",
    "silero/silero_vad.onnx", "silero/silero_vad.onnx.part",
])
def test_existing_staging_hard_links_are_rejected_before_writes(assets, tmp_path, relative):
    camp, _, _, _, _, requested = assets
    target = camp / ".voice-model-repair" / relative
    target.parent.mkdir(mode=0o700, parents=True)
    # parents=True uses the default mode for intermediate directories on POSIX.
    (camp / ".voice-model-repair").chmod(0o700)
    victim = tmp_path / "unrelated-file"
    victim.write_bytes(b"preserve this data")
    os.link(victim, target)
    with pytest.raises(OSError, match="unsafe repair file"):
        repair.repair_source_models()
    assert victim.read_bytes() == b"preserve this data"
    assert requested == []


@pytest.mark.parametrize("relative", ["", "campp", "silero"])
def test_existing_staging_directory_links_are_rejected(assets, tmp_path, relative):
    camp, _, _, _, _, requested = assets
    staging = camp / ".voice-model-repair"
    target = staging / relative if relative else staging
    if relative:
        staging.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "manifest.json"
    victim.write_bytes(b"preserve this data")
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(target))
    else:
        target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="unsafe repair directory"):
        repair.repair_source_models()
    assert victim.read_bytes() == b"preserve this data"
    assert requested == []


def test_manifest_reparse_point_is_rejected_without_touching_its_target(assets, tmp_path):
    camp, _, _, _, _, requested = assets
    staging = camp / ".voice-model-repair"
    staging.mkdir(mode=0o700)
    child = staging / "campp"
    child.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "keep"
    victim.write_bytes(b"preserve this data")
    target = child / "manifest.json"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(target))
    else:
        target.symlink_to(victim)
    with pytest.raises(OSError, match="unsafe repair file"):
        repair.repair_source_models()
    assert victim.read_bytes() == b"preserve this data"
    assert requested == []


@pytest.mark.parametrize("relative", ["unrelated", "campp/unrelated", "silero/unrelated"])
def test_unrecognized_staging_data_is_preserved_and_blocks_repair(assets, relative):
    camp, _, _, _, _, requested = assets
    staging = camp / ".voice-model-repair"
    staging.mkdir(mode=0o700)
    target = staging / relative
    target.parent.mkdir(mode=0o700, exist_ok=True)
    target.write_bytes(b"unrecognized data")
    with pytest.raises(OSError, match="unexpected repair artifact"):
        repair.repair_source_models()
    assert target.read_bytes() == b"unrecognized data"
    assert requested == []


def test_unsafe_publication_pending_file_is_not_followed(assets, tmp_path):
    camp, _, pinned, _, _, _ = assets
    victim = tmp_path / "unrelated-file"
    victim.write_bytes(b"preserve this data")
    os.link(victim, camp / (pinned.filename + ".voice-repair.part"))
    with pytest.raises(OSError, match="unsafe repair file"):
        repair.repair_source_models()
    assert victim.read_bytes() == b"preserve this data"
    assert not (camp / pinned.filename).exists()


def test_concurrent_repair_reports_busy_and_retry_succeeds(assets):
    from config.resource_file_lock import resource_file_lock
    from main_logic.voice_identity_service import resource_manager
    camp, _, pinned, camp_bytes, _, requested = assets
    staging = camp / ".voice-model-repair"
    staging.mkdir(mode=0o700)
    messages, closed = [], []
    reply = SimpleNamespace(send=messages.append, close=lambda: closed.append(True))
    with resource_file_lock(staging / "repair.lock"):
        resource_manager._resource_worker(reply, "repair", False, None, b"")
        assert messages == [{"ok": False, "reason": "resource_operation_busy"}]
        assert requested == []
    assert closed == [True]
    repair.repair_source_models()
    assert (camp / pinned.filename).read_bytes() == camp_bytes


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory ownership and mode")
def test_non_private_staging_directory_is_not_adopted(assets):
    camp, _, _, _, _, requested = assets
    staging = camp / ".voice-model-repair"
    staging.mkdir(mode=0o777)
    staging.chmod(0o777)
    with pytest.raises(OSError, match="ownership or permissions"):
        repair.repair_source_models()
    assert requested == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["manifest_corrupt", "manifest_removed", "preparer_removed", "frozen"])
async def test_worker_preserves_unavailable_reason_when_installation_changes_after_reservation(assets, monkeypatch, change):
    from main_logic.voice_identity_service import resource_manager
    camp, silero, _, _, _, requested = assets
    manager = resource_manager.VoiceResourceManager(lambda: False)
    messages, closed = [], []
    reply = SimpleNamespace(send=messages.append, close=lambda: closed.append(True))
    operation = manager.reserve("repair")
    try:
        if change == "manifest_corrupt":
            (silero / "manifest.json").write_bytes(b"not json")
        elif change == "manifest_removed":
            (camp / "manifest.json").unlink()
        elif change == "preparer_removed":
            (Path(repair.__file__).resolve().parents[2] / "scripts/prepare_speaker_model.py").unlink()
        else:
            monkeypatch.setattr(repair.sys, "frozen", True, raising=False)
        resource_manager._resource_worker(reply, "repair", False, None, b"")
        assert messages == [{"ok": False, "reason": "resource_repair_unavailable"}]
        assert closed == [True]
        assert requested == []
        assert not (camp / ".voice-model-repair").exists()
    finally:
        await manager.cancel(operation["operation_id"])
        await manager.close()


@pytest.mark.parametrize("message", ["resource_repair_unavailable", "unexpected runtime failure"])
def test_worker_does_not_reclassify_unrelated_runtime_errors(monkeypatch, message):
    from main_logic.voice_identity_service import resource_manager
    def fail():
        raise RuntimeError(message)
    monkeypatch.setattr(repair, "repair_source_models", fail)
    messages, closed = [], []
    reply = SimpleNamespace(send=messages.append, close=lambda: closed.append(True))
    resource_manager._resource_worker(reply, "repair", False, None, b"")
    assert messages == [{"ok": False, "reason": "resource_worker_failed"}]
    assert closed == [True]


def test_source_repair_uses_checked_checkout_scripts_without_package_imports(assets, monkeypatch):
    camp, silero, pinned, camp_bytes, silero_bytes, requested = assets
    scripts = Path(repair.__file__).resolve().parents[2] / "scripts"
    for filename in ("prepare_speaker_model.py", "prepare_voice_turn_assets.py"):
        with (scripts / filename).open("a", encoding="utf-8") as script:
            script.write("\nPath(__file__).with_suffix('.loaded').touch()\n")
    # A source-only checkout tool must not require an installed scripts package
    # or reuse a same-named module from another checkout on sys.path.
    monkeypatch.setitem(repair.sys.modules, "scripts", None)
    repair.repair_source_models()
    assert (scripts / "prepare_speaker_model.loaded").is_file()
    assert (scripts / "prepare_voice_turn_assets.loaded").is_file()
    assert (camp / pinned.filename).read_bytes() == camp_bytes
    assert (silero / "silero_vad.onnx").read_bytes() == silero_bytes
    assert len(requested) == 2
