#!/usr/bin/env python3
"""Safely unpack built-in PNGTuber assets for production frontend builds."""

from __future__ import annotations

import hashlib
import json
import math
import time
import warnings
import re
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath


try:
    from .pngtuber_publish import publication_lock, recover_publication
except ImportError:
    from pngtuber_publish import publication_lock, recover_publication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKS_ROOT = PROJECT_ROOT / "frontend" / "pngtuber-packs"
OUTPUT_ROOT = PROJECT_ROOT / "static" / "pngtuber"
MANIFEST_PATH = PACKS_ROOT / "manifest.json"
IMAGE_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg", ".webp"}
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_PACKAGE_SIZE = 250 * 1024 * 1024
MAX_ARCHIVE_FILES = 1000
MAX_ARCHIVE_PARTS = 32
MAX_COMBINED_SIZE = 2 * 1024 * 1024 * 1024
MAX_COMBINED_FILES = 10000
CHUNK_SIZE = 1024 * 1024
READY_MARKER = ".builtin-pack-sha256"


def _safe_relative_path(raw_path: str) -> PurePosixPath | None:
    normalized = (raw_path or "").replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[a-zA-Z]:", normalized)
    ):
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        return None
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _validate_package(package_dir: Path) -> None:
    model = _read_json(package_dir / "model.json")
    if model.get("model_type") != "pngtuber":
        raise ValueError("model.json model_type must be pngtuber")
    config = model.get("pngtuber")
    if not isinstance(config, dict):
        raise ValueError("model.json pngtuber config is missing")

    for key, value in config.items():
        if not key.endswith("_image") or not value:
            continue
        relative = _safe_relative_path(value) if isinstance(value, str) else None
        if relative is None or relative.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError(f"invalid PNGTuber image path: {key}")
        if not (package_dir / relative.as_posix()).is_file():
            raise ValueError(f"missing PNGTuber image: {value}")

    metadata_name = config.get("layered_metadata") or config.get("metadata")
    metadata_relative = (
        _safe_relative_path(metadata_name) if isinstance(metadata_name, str) else None
    )
    if metadata_relative is None or metadata_relative.suffix.lower() != ".json":
        raise ValueError("invalid layered metadata path")
    metadata = _read_json(package_dir / metadata_relative.as_posix())
    layers = metadata.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ValueError("layered metadata contains no layers")
    for layer in layers:
        image_name = layer.get("image") if isinstance(layer, dict) else None
        relative = (
            _safe_relative_path(image_name) if isinstance(image_name, str) else None
        )
        if relative is None or relative.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("invalid layered image path")
        if not (package_dir / relative.as_posix()).is_file():
            raise ValueError(f"missing layered image: {image_name}")

    tracks = metadata.get("tracked_animations", {})
    if not isinstance(tracks, dict):
        raise ValueError("invalid tracked animations")
    reviewed = metadata.get("reviewed_actions")
    if isinstance(reviewed, dict) and reviewed.get("version") == 1:
        count = metadata.get("state_count")
        groups, catalog = reviewed.get("groups"), metadata.get("state_catalog")
        if type(count) is not int or count < 1 or not isinstance(groups, dict) or not isinstance(catalog, list):
            raise ValueError("invalid reviewed metadata")
        if len(catalog) != count or any(not isinstance(row, dict) for row in catalog):
            raise ValueError("invalid reviewed catalog")
        indices = [row.get("state_index") for row in catalog]
        if any(type(i) is not int for i in indices) or sorted(indices) != list(range(count)):
            raise ValueError("invalid reviewed catalog")
        if any(str(i) not in tracks for i in range(count)):
            raise ValueError("missing reviewed track")
        for values in groups.values():
            if not isinstance(values, list) or any(type(i) is not int or not 0 <= i < count for i in values) or len(set(values)) != len(values):
                raise ValueError("invalid reviewed group")
        idle = groups.get("idle")
        if not isinstance(idle, list) or not idle or any(tracks[str(i)].get("playback") != "loop" for i in idle):
            raise ValueError("invalid reviewed idle group")
        for track in tracks.values():
            _validate_reviewed_track(track)
    for track in [metadata.get("tracked_animation"), *tracks.values()]:
        if track is None:
            continue
        if not isinstance(track, dict) or not isinstance(track.get("frames"), list):
            raise ValueError("invalid tracked animation frames")
        for frame in track["frames"]:
            if not isinstance(frame, dict):
                raise ValueError("invalid tracked frame")
            names = [frame.get("image")]
            if frame.get("mouth_patch") is not None:
                patch = frame["mouth_patch"]
                names.append(patch.get("image") if isinstance(patch, dict) else None)
            for name in names:
                relative = _safe_relative_path(name) if isinstance(name, str) else None
                if relative is None or relative.suffix.lower() not in IMAGE_EXTENSIONS:
                    raise ValueError("invalid tracked image path")
                if not (package_dir / relative.as_posix()).is_file():
                    raise ValueError(f"missing tracked image: {name}")


def _validate_reviewed_track(track):
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)
    if not isinstance(track, dict) or not isinstance(track.get("frames"), list) or not 1 <= len(track["frames"]) <= 2000:
        raise ValueError("invalid reviewed frames")
    if track.get("playback") not in ("once", "loop"):
        raise ValueError("invalid reviewed playback")
    end = 0
    for frame in track["frames"]:
        if not isinstance(frame, dict) or not finite(frame.get("time_ms")) or frame["time_ms"] != end or not finite(frame.get("duration_ms")) or frame["duration_ms"] <= 0:
            raise ValueError("invalid reviewed timeline")
        if any(not finite(frame.get(key)) for key in ("x", "y")):
            raise ValueError("invalid reviewed mouth position")
        end += frame["duration_ms"]
    if not finite(track.get("duration_ms")) or track["duration_ms"] != end:
        raise ValueError("invalid reviewed duration")
    if track.get("lipsync") is not False:
        crop = track.get("mouth_crop")
        if not isinstance(crop, list) or len(crop) != 4 or not all(finite(v) for v in crop) or min(crop[:2]) < 0 or min(crop[2:]) <= 0:
            raise ValueError("invalid reviewed mouth crop")


def parts_fingerprint(parts: list[dict]) -> str:
    """Bind part names, checksums and declared sizes to one installation marker."""
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _archive_parts(model: dict) -> list[dict]:
    parts = model.get("archives")
    if parts is None:
        parts = [
            {
                key: model.get(key)
                for key in ("archive", "archive_sha256", "file_count", "unpacked_size")
            }
        ]
    else:
        if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_ARCHIVE_PARTS:
            raise ValueError("invalid built-in PNGTuber archive parts")
        if any(not isinstance(part, dict) for part in parts):
            raise ValueError("invalid archive part")
        if model.get("archive_sha256") != parts_fingerprint(parts):
            raise ValueError("archive parts fingerprint mismatch")
        if model.get("archive") != parts[0].get("archive"):
            raise ValueError("first archive does not match manifest")
    seen = set()
    for part in parts:
        name = part.get("archive")
        path = _safe_relative_path(name) if isinstance(name, str) else None
        if (
            path is None
            or len(path.parts) != 1
            or path.suffix != ".zip"
            or name != path.as_posix()
        ):
            raise ValueError("invalid built-in PNGTuber archive name")
        if name.casefold() in seen:
            raise ValueError("duplicate archive part")
        seen.add(name.casefold())
        if not re.fullmatch(r"[0-9a-f]{64}", str(part.get("archive_sha256") or "")):
            raise ValueError("invalid built-in PNGTuber archive hash")
        count, size = part.get("file_count"), part.get("unpacked_size")
        if (
            type(count) is not int
            or type(size) is not int
            or not 1 <= count <= MAX_ARCHIVE_FILES
            or not 0 <= size <= MAX_PACKAGE_SIZE
        ):
            raise ValueError("archive limits exceeded")
    count = sum(part["file_count"] for part in parts)
    size = sum(part["unpacked_size"] for part in parts)
    if count > MAX_COMBINED_FILES or size > MAX_COMBINED_SIZE:
        raise ValueError("combined archive limits exceeded")
    if model.get("file_count") != count or model.get("unpacked_size") != size:
        raise ValueError("archive totals do not match manifest")
    return parts


def _unpack_model(
    model: dict, packs_root: Path = PACKS_ROOT, output_root: Path = OUTPUT_ROOT
) -> Path:
    folder = model.get("folder")
    folder_path = _safe_relative_path(folder) if isinstance(folder, str) else None
    if (
        folder_path is None
        or len(folder_path.parts) != 1
        or folder != folder_path.as_posix()
    ):
        raise ValueError("invalid built-in PNGTuber folder")
    folder_name = folder_path.name

    parts = _archive_parts(model)
    expected_hash = model["archive_sha256"]

    target_dir = output_root / folder_name
    backup_dir = output_root / f".{folder_name}.backup"
    if backup_dir.is_symlink() or target_dir.is_symlink():
        raise ValueError("unsafe installed package directory")
    if backup_dir.exists():
        if target_dir.exists():
            _cleanup_backup(backup_dir)
        else:
            backup_dir.rename(target_dir)
    marker = target_dir / READY_MARKER
    existing_files = (
        sum(
            1
            for path in target_dir.rglob("*")
            if path.is_file() and path.name != READY_MARKER
        )
        if target_dir.is_dir()
        else 0
    )
    if (
        marker.is_file()
        and (target_dir / "model.json").is_file()
        and existing_files == model.get("file_count")
        and marker.read_text(encoding="utf-8").strip() == expected_hash
    ):
        try:
            _validate_package(target_dir)
        except (ValueError, OSError, json.JSONDecodeError):
            pass  # An interrupted or damaged installation must be rebuilt.
        else:
            print(f"[build_frontend] PNGTuber {folder} up to date, skip")
            return target_dir

    for part in parts:
        source_archive = packs_root / part["archive"]
        if (
            not source_archive.is_file()
            or _sha256_file(source_archive) != part["archive_sha256"]
        ):
            raise ValueError(f"archive missing or checksum failed: {source_archive}")
        if (
            "archive_size" in part
            and source_archive.stat().st_size != part["archive_size"]
        ):
            raise ValueError(f"archive size mismatch: {source_archive}")

    output_root.mkdir(parents=True, exist_ok=True)
    temp_dir = output_root / f".{folder_name}.{uuid.uuid4().hex}.tmp"
    temp_dir.mkdir()
    try:
        seen: set[str] = set()
        for part in parts:
            source_archive = packs_root / part["archive"]
            with zipfile.ZipFile(source_archive) as archive:
                infos = [info for info in archive.infolist() if not info.is_dir()]
                unpacked_size = sum(info.file_size for info in infos)
                if len(infos) > MAX_ARCHIVE_FILES or unpacked_size > MAX_PACKAGE_SIZE:
                    raise ValueError(f"archive limits exceeded: {part['archive']}")
                if (
                    part.get("file_count") != len(infos)
                    or part.get("unpacked_size") != unpacked_size
                ):
                    raise ValueError(
                        f"archive does not match manifest: {part['archive']}"
                    )

                for info in infos:
                    relative = _safe_relative_path(info.filename)
                    unix_mode = info.external_attr >> 16
                    if relative is None or stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                        raise ValueError(f"unsafe archive path: {info.filename}")
                    normalized = relative.as_posix().casefold()
                    if normalized in seen or info.file_size > MAX_FILE_SIZE:
                        raise ValueError(f"invalid archive member: {info.filename}")
                    seen.add(normalized)
                    destination = (temp_dir / relative.as_posix()).resolve()
                    destination.relative_to(temp_dir.resolve())
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output, CHUNK_SIZE)
        _validate_package(temp_dir)
        (temp_dir / READY_MARKER).write_text(expected_hash, encoding="utf-8")
        if target_dir.exists():
            target_dir.rename(backup_dir)
        try:
            temp_dir.rename(target_dir)
        except Exception:
            if backup_dir.exists() and not target_dir.exists():
                backup_dir.rename(target_dir)
            raise
        if backup_dir.exists():
            _cleanup_backup(backup_dir)
        print(f"[build_frontend] PNGTuber {folder} unpacked")
        return target_dir
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def _cleanup_backup(path: Path):
    try:
        shutil.rmtree(path)
    except OSError as error:
        warnings.warn(f"Installed PNGTuber retained; backup cleanup deferred: {error}", RuntimeWarning)


def unpack_model(model: dict, packs_root: Path = PACKS_ROOT, output_root: Path = OUTPUT_ROOT) -> Path:
    folder = model.get("folder")
    if not isinstance(folder, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", folder):
        raise ValueError("invalid built-in PNGTuber folder")
    # The pack lock prevents observing an in-progress multi-file publication.
    with publication_lock(packs_root, "publish"), publication_lock(output_root, folder):
        recover_publication(packs_root)
        if output_root.is_dir():
            for path in output_root.glob(f".{folder}.*.tmp"):
                if re.fullmatch(r"\." + re.escape(folder) + r"\.[0-9a-f]{32}\.tmp", path.name) and not path.is_symlink() and path.is_dir() and time.time() - path.stat().st_mtime > 86400:
                    path.resolve().relative_to(output_root.resolve())
                    _cleanup_backup(path)
        return _unpack_model(model, packs_root, output_root)


def main() -> None:
    manifest = _read_json(MANIFEST_PATH)
    models = manifest.get("models")
    if manifest.get("version") != 1 or not isinstance(models, list):
        raise ValueError("invalid built-in PNGTuber manifest")
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("invalid built-in PNGTuber manifest entry")
        unpack_model(model)


if __name__ == "__main__":
    main()
