"""Explicit source-deployment repair of the two pinned enrollment models."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys

from config.resource_file_lock import canonical_resource_root, resource_file_lock
from main_logic.asr_client.endpointing.asset_manifest import load_manifest, verify_asset, AssetManifestError
from main_logic.asr_client.speaker_shadow.asset_manifest import load_campplus_manifest, CampPlusAssetError

SILERO_MAXIMUM_DOWNLOAD_BYTES = 16 * 1024 * 1024


class SourceModelRepairUnavailable(RuntimeError):
    """Source-only prerequisites are unavailable when the worker starts."""

    code = "resource_repair_unavailable"


def _load_repair_manifests(project: Path):
    camp = load_campplus_manifest(project / "main_logic/asr_client/speaker_shadow/models")
    silero = load_manifest(project / "main_logic/asr_client/endpointing/models").asset("silero_vad.onnx")
    return camp, silero


def _load_source_preparer(project: Path, filename: str):
    """Load a checked source tool without requiring it in wheel packages."""
    path = project / "scripts" / filename
    module_spec = importlib.util.spec_from_file_location(f"_neko_source_{path.stem}", path)
    if module_spec is None or module_spec.loader is None:
        raise SourceModelRepairUnavailable(SourceModelRepairUnavailable.code)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def source_model_repair_available(*, validate_manifests: bool = True) -> bool:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return False
    # Non-frozen wheels/sdists need not contain the source-only preparers.
    # Discovery must advertise only what this installation can actually run.
    project = Path(__file__).resolve().parents[2]
    if not all((project / relative).is_file() for relative in (
        "scripts/prepare_speaker_model.py",
        "scripts/prepare_voice_turn_assets.py",
        "main_logic/asr_client/speaker_shadow/models/manifest.json",
        "main_logic/asr_client/endpointing/models/manifest.json",
    )):
        return False
    if not validate_manifests:
        return True
    try:
        _load_repair_manifests(project)
    except (CampPlusAssetError, AssetManifestError, OSError, UnicodeError):
        return False
    return True


def _checked_directory(directory: Path) -> Path:
    """Keep bounded repair artifacts inside the already-trusted source checkout."""
    try:
        directory = canonical_resource_root(directory)
    except ValueError as exc:
        raise OSError("unsafe repair directory") from exc
    directory.mkdir(mode=0o700, exist_ok=True)
    metadata = directory.lstat()
    if (not stat.S_ISDIR(metadata.st_mode)
            or hasattr(os, "getuid") and (metadata.st_uid != os.getuid() or metadata.st_mode & 0o077)):
        raise OSError("unsafe repair directory ownership or permissions")
    return directory


def _checked_file(path: Path) -> None:
    try:
        canonical_resource_root(path)
    except ValueError as exc:
        raise OSError("unsafe repair file") from exc
    if path.exists():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError("unsafe repair file")


def _check_staging(staging: Path, camp_filename: str) -> None:
    for entry in staging.iterdir():
        if entry.name == "repair.lock":
            _checked_file(entry)
            continue
        if entry.name not in {"campp", "silero"}:
            raise OSError("unexpected repair artifact")
        directory = _checked_directory(entry)
        filename = camp_filename if entry.name == "campp" else "silero_vad.onnx"
        for asset in directory.iterdir():
            if asset.name not in {"manifest.json", filename, filename + ".part"}:
                raise OSError("unexpected repair artifact")
            _checked_file(asset)


def repair_source_models() -> None:
    """Verify existing files or download fixed assets, never execute an installer.

    Each verified model is published atomically. Cancellation can retain a
    completed model; it never replaces a valid model with an incomplete file.
    A private cache under the trusted checkout bounds interrupted downloads.
    Shared temporary directories are never used or adopted.
    """
    if not source_model_repair_available():
        raise SourceModelRepairUnavailable(SourceModelRepairUnavailable.code)

    project = Path(__file__).resolve().parents[2]
    # Preflight both manifests before any download or model publication.
    camp_manifest, spec = _load_repair_manifests(project)
    speaker = _load_source_preparer(project, "prepare_speaker_model.py")
    endpointing = _load_source_preparer(project, "prepare_voice_turn_assets.py")
    camp_dir = project / "main_logic/asr_client/speaker_shadow/models"
    silero_dir = project / "main_logic/asr_client/endpointing/models"
    staging = _checked_directory(camp_dir / ".voice-model-repair")
    _check_staging(staging, camp_manifest.filename)
    with resource_file_lock(staging / "repair.lock"):
        _check_staging(staging, camp_manifest.filename)
        try:
            speaker.verify_campplus_asset(camp_dir, camp_manifest)
        except speaker.CampPlusAssetError:
            camp_stage = _checked_directory(staging / "campp")
            shutil.copyfile(camp_dir / "manifest.json", camp_stage / "manifest.json")
            source = speaker.prepare_speaker_model(camp_stage)
            pending = camp_dir / (camp_manifest.filename + ".voice-repair.part")
            _checked_file(pending)
            shutil.copyfile(source, pending)
            speaker._verify_transfer(pending, expected_size=camp_manifest.size_bytes, expected_sha256=camp_manifest.sha256)
            os.replace(pending, camp_dir / camp_manifest.filename)
            speaker.verify_campplus_asset(camp_dir, camp_manifest)

        # The full CLI also provisions Smart Turn. Enrollment only needs Silero,
        # so pass a manifest containing that reviewed asset to the same preparer.
        try:
            verify_asset(silero_dir, spec)
        except endpointing.AssetManifestError:
            silero_stage = _checked_directory(staging / "silero")
            (silero_stage / "manifest.json").write_text(
                json.dumps({"schema_version": 1, "assets": [asdict(spec)]}), encoding="utf-8"
            )
            source = endpointing.prepare_assets(silero_stage, maximum_asset_bytes=SILERO_MAXIMUM_DOWNLOAD_BYTES)[0]
            pending = silero_dir / (spec.filename + ".voice-repair.part")
            _checked_file(pending)
            shutil.copyfile(source, pending)
            if hashlib.sha256(pending.read_bytes()).hexdigest().lower() != spec.sha256.lower():
                raise endpointing.AssetManifestError("asset_sha256_mismatch")
            os.replace(pending, silero_dir / spec.filename)
            verify_asset(silero_dir, spec)

        # Only discard our whitelisted artifacts after both live models verify.
        # A killed/failed repair keeps a bounded cache for the next attempt.
        _check_staging(staging, camp_manifest.filename)
        for name in ("campp", "silero"):
            directory = staging / name
            if directory.exists():
                for asset in directory.iterdir():
                    asset.unlink()
                directory.rmdir()
