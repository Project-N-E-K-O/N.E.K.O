"""Explicit source-deployment repair of the two pinned enrollment models."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from filelock import FileLock

SILERO_MAXIMUM_DOWNLOAD_BYTES = 16 * 1024 * 1024

def source_model_repair_available() -> bool:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return False
    # Non-frozen wheels/sdists need not contain the source-only preparers.
    # Discovery must advertise only what this installation can actually run.
    project = Path(__file__).resolve().parents[2]
    return all((project / relative).is_file() for relative in (
        "scripts/prepare_speaker_model.py",
        "scripts/prepare_voice_turn_assets.py",
        "main_logic/asr_client/speaker_shadow/models/manifest.json",
        "main_logic/asr_client/endpointing/models/manifest.json",
    ))


def repair_source_models() -> None:
    """Verify existing files or download fixed assets, never execute an installer.

    Each verified model is published atomically. Cancellation can retain a
    completed model; it never replaces a valid model with an incomplete file.
    A fixed per-checkout staging area bounds interrupted downloads across retries.
    """
    if not source_model_repair_available():
        raise RuntimeError("resource_repair_unavailable")

    from scripts import prepare_speaker_model as speaker
    from scripts import prepare_voice_turn_assets as endpointing
    from main_logic.asr_client.endpointing.asset_manifest import load_manifest, verify_asset

    project = Path(__file__).resolve().parents[2]
    identity = hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:24]
    staging = Path(tempfile.gettempdir()) / ("neko-voice-model-repair-" + identity)
    staging.mkdir(parents=True, exist_ok=True)
    with FileLock(str(staging / "repair.lock"), timeout=0):
        camp_dir = project / "main_logic/asr_client/speaker_shadow/models"
        silero_dir = project / "main_logic/asr_client/endpointing/models"
        camp_manifest = speaker.load_campplus_manifest(camp_dir)
        try:
            speaker.verify_campplus_asset(camp_dir, camp_manifest)
        except speaker.CampPlusAssetError:
            camp_stage = staging / "campp"
            camp_stage.mkdir(exist_ok=True)
            shutil.copyfile(camp_dir / "manifest.json", camp_stage / "manifest.json")
            source = speaker.prepare_speaker_model(camp_stage)
            pending = camp_dir / (camp_manifest.filename + ".voice-repair.part")
            shutil.copyfile(source, pending)
            speaker._verify_transfer(pending, expected_size=camp_manifest.size_bytes, expected_sha256=camp_manifest.sha256)
            os.replace(pending, camp_dir / camp_manifest.filename)
            speaker.verify_campplus_asset(camp_dir, camp_manifest)

        # The full CLI also provisions Smart Turn. Enrollment only needs Silero,
        # so pass a manifest containing that reviewed asset to the same preparer.
        spec = load_manifest(silero_dir).asset("silero_vad.onnx")
        try:
            verify_asset(silero_dir, spec)
        except endpointing.AssetManifestError:
            silero_stage = staging / "silero"
            silero_stage.mkdir(exist_ok=True)
            (silero_stage / "manifest.json").write_text(
                json.dumps({"schema_version": 1, "assets": [asdict(spec)]}), encoding="utf-8"
            )
            source = endpointing.prepare_assets(silero_stage, maximum_asset_bytes=SILERO_MAXIMUM_DOWNLOAD_BYTES)[0]
            pending = silero_dir / (spec.filename + ".voice-repair.part")
            shutil.copyfile(source, pending)
            if hashlib.sha256(pending.read_bytes()).hexdigest().lower() != spec.sha256.lower():
                raise endpointing.AssetManifestError("asset_sha256_mismatch")
            os.replace(pending, silero_dir / spec.filename)
            verify_asset(silero_dir, spec)
