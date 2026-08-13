#!/usr/bin/env python3
"""Safely unpack built-in PNGTuber assets for production frontend builds."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKS_ROOT = PROJECT_ROOT / "frontend" / "pngtuber-packs"
OUTPUT_ROOT = PROJECT_ROOT / "static" / "pngtuber"
MANIFEST_PATH = PACKS_ROOT / "manifest.json"
IMAGE_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg", ".webp"}
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_PACKAGE_SIZE = 250 * 1024 * 1024
MAX_ARCHIVE_FILES = 1000
CHUNK_SIZE = 1024 * 1024
READY_MARKER = ".builtin-pack-sha256"


def _safe_relative_path(raw_path: str) -> PurePosixPath | None:
    normalized = (raw_path or "").replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[a-zA-Z]:", normalized):
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
        relative = _safe_relative_path(image_name) if isinstance(image_name, str) else None
        if relative is None or relative.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("invalid layered image path")
        if not (package_dir / relative.as_posix()).is_file():
            raise ValueError(f"missing layered image: {image_name}")


def unpack_model(model: dict, packs_root: Path = PACKS_ROOT, output_root: Path = OUTPUT_ROOT) -> Path:
    folder = model.get("folder")
    folder_path = _safe_relative_path(folder) if isinstance(folder, str) else None
    if (
        folder_path is None
        or len(folder_path.parts) != 1
        or folder != folder_path.as_posix()
    ):
        raise ValueError("invalid built-in PNGTuber folder")
    folder_name = folder_path.name

    archive_name = model.get("archive")
    archive_path = _safe_relative_path(archive_name) if isinstance(archive_name, str) else None
    if archive_path is None or len(archive_path.parts) != 1 or archive_path.suffix != ".zip":
        raise ValueError("invalid built-in PNGTuber archive name")
    expected_hash = str(model.get("archive_sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("invalid built-in PNGTuber archive hash")

    target_dir = output_root / folder_name
    backup_dir = output_root / f".{folder_name}.backup"
    if backup_dir.exists():
        if target_dir.exists():
            shutil.rmtree(backup_dir)
        else:
            backup_dir.rename(target_dir)
    marker = target_dir / READY_MARKER
    existing_files = (
        sum(1 for path in target_dir.rglob("*") if path.is_file() and path.name != READY_MARKER)
        if target_dir.is_dir()
        else 0
    )
    if (
        marker.is_file()
        and (target_dir / "model.json").is_file()
        and (target_dir / "metadata.pngtube-remix.json").is_file()
        and existing_files == model.get("file_count")
        and marker.read_text(encoding="utf-8").strip() == expected_hash
    ):
        print(f"[build_frontend] PNGTuber {folder} up to date, skip")
        return target_dir

    source_archive = packs_root / archive_path.as_posix()
    if not source_archive.is_file() or _sha256_file(source_archive) != expected_hash:
        raise ValueError(f"archive missing or checksum failed: {source_archive}")

    output_root.mkdir(parents=True, exist_ok=True)
    temp_dir = output_root / f".{folder_name}.{uuid.uuid4().hex}.tmp"
    temp_dir.mkdir()
    try:
        with zipfile.ZipFile(source_archive) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            unpacked_size = sum(info.file_size for info in infos)
            if len(infos) > MAX_ARCHIVE_FILES or unpacked_size > MAX_PACKAGE_SIZE:
                raise ValueError(f"archive limits exceeded: {archive_name}")
            if model.get("file_count") != len(infos) or model.get("unpacked_size") != unpacked_size:
                raise ValueError(f"archive does not match manifest: {archive_name}")

            seen: set[str] = set()
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
            shutil.rmtree(backup_dir)
        print(f"[build_frontend] PNGTuber {folder} unpacked")
        return target_dir
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


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
