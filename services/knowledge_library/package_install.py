"""Retain verified artifacts for the library service's single atomic installation authority."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from typing import Callable
import zipfile

from .models import LibraryError
from .package_build import _guarded, run_package_io
from .package_validate import (
    DEFAULT_LIMITS, PACKAGE_SUFFIX, PackageLimits, _hash, _manifest, _preflight_archive,
    hash_file, validate_package,
)
from .persistence import ensure_directory, sync_directory, sync_file


def _retain(path: Path, root: Path, digest: str, limits: PackageLimits) -> Path:
    """Keep exact verified bytes under their digest; failed copies never replace a file."""
    directory = root / "artifacts"
    ensure_directory(directory)
    target = directory / (digest + PACKAGE_SUFFIX)
    if target.exists():
        if target.is_symlink() or hash_file(target, limits.max_archive_bytes)[0] != digest:
            raise LibraryError("package_artifact_hash_mismatch")
        # A previous copy can be visible even if its directory flush failed.
        # Re-establish durability before any new installation references it.
        sync_file(target)
        sync_directory(directory)
        return target
    fd, name = tempfile.mkstemp(prefix="package-", suffix=".part", dir=directory)
    stage = Path(name)
    try:
        hashed, size = hashlib.sha256(), 0
        with os.fdopen(fd, "wb") as destination, path.open("rb") as source:
            while block := source.read(64 * 1024):
                size += len(block)
                if size > limits.max_archive_bytes:
                    raise LibraryError("package_too_large")
                destination.write(block)
                hashed.update(block)
            destination.flush()
            os.fsync(destination.fileno())
        if hashed.hexdigest() != digest:
            raise LibraryError("package_artifact_hash_mismatch")
        os.replace(stage, target)
        # The installation manifest may reference this artifact only after
        # both its bytes and its directory entry have been persisted.
        sync_directory(directory)
        return target
    finally:
        stage.unlink(missing_ok=True)


async def install_package(path: Path, root: Path, *, expected_spec: dict | None = None,
                          expected_sha256: str | None = None,
                          expected_package_id: str | None = None,
                          expected_version: str | None = None,
                          limits: PackageLimits = DEFAULT_LIMITS,
                          check_cancelled: Callable[[], None] | None = None,
                          write_guard: Callable | None = None) -> dict:
    """Prepare domain records; the caller commits them with LibraryService.install_snapshot.

    This function owns only immutable artifact retention, never a second active
    library/install manifest. An unavailable or different encoder permits keyword
    use and reports ``semantic_available=False``; document vectors are never rebuilt.
    """
    def prepare() -> dict:
        if check_cancelled is not None:
            check_cancelled()
        validated = validate_package(Path(path), expected_spec=expected_spec,
                                     expected_sha256=expected_sha256,
                                     expected_package_id=expected_package_id,
                                     expected_version=expected_version, limits=limits)
        retained = _guarded(write_guard, _retain, Path(path), Path(root), validated.artifact_sha256, limits)
        # 复制后从持久 artifact 重新验证并恢复，避免使用可能已改变的上传暂存文件。
        saved = validate_package(retained, expected_spec=expected_spec,
                                 expected_sha256=validated.artifact_sha256,
                                 expected_package_id=validated.manifest["package_id"],
                                 expected_version=validated.manifest["version"], limits=limits)
        if check_cancelled is not None:
            check_cancelled()
        return {"snapshot": saved.to_snapshot(), "semantic_available": saved.semantic_available,
                "installation": {"work_id": saved.manifest["package_id"],
                                 "version": saved.manifest["version"],
                                 "artifact_sha256": saved.artifact_sha256,
                                 # Persist an opaque identity, not an absolute root
                                 # that becomes stale after storage migration.
                                 "artifact_id": saved.artifact_sha256,
                                 "semantic_available": saved.semantic_available},
                "package": saved.metadata()}

    return await run_package_io(prepare)


def get_artifact(root: Path, artifact_id: str, *, limits: PackageLimits = DEFAULT_LIMITS) -> Path:
    """Resolve an opaque digest, never a client-provided filesystem path."""
    digest = _hash(artifact_id)
    path = Path(root) / "artifacts" / (digest + PACKAGE_SUFFIX)
    if not path.is_file() or path.is_symlink():
        raise LibraryError("package_not_found")
    if hash_file(path, limits.max_archive_bytes)[0] != digest:
        raise LibraryError("package_artifact_hash_mismatch")
    return path


def list_packages(root: Path, *, limits: PackageLimits = DEFAULT_LIMITS) -> list[dict]:
    """List bounded manifest metadata; install and download perform full integrity checks."""
    directory = Path(root) / "artifacts"
    if not directory.is_dir():
        return []
    results = []
    for path in sorted(directory.glob("*" + PACKAGE_SUFFIX)):
        try:
            digest = _hash(path.name[:-len(PACKAGE_SUFFIX)])
            size = path.stat().st_size
            if path.is_symlink() or size > limits.max_archive_bytes:
                continue
            _preflight_archive(path, size)
            with zipfile.ZipFile(path) as archive:
                manifest = _manifest(archive, limits)
            results.append({"artifact_id": digest, "sha256": digest, "size": size,
                            "name": manifest["name"], "version": manifest["version"],
                            "package_id": manifest["package_id"],
                            "embedding_spec": manifest["embedding_spec"]})
        except (LibraryError, OSError, zipfile.BadZipFile, ValueError):
            # 损坏成品不作为可下载条目展示，安装与恢复入口仍返回明确校验错误。
            continue
    return results


def package_scope(root: Path, artifact_id: str, *, limits: PackageLimits = DEFAULT_LIMITS) -> dict:
    """Review the exact export's contents without exposing text, paths or vectors.

    This is private local metadata, not the public market preview. Full package
    validation binds every listed document to the same immutable digest used by
    download and publication; the currently selected library is never consulted.
    """
    path = get_artifact(root, artifact_id, limits=limits)
    saved = validate_package(path, expected_sha256=artifact_id, limits=limits)
    metadata = saved.metadata()
    return {**{key: metadata[key] for key in ("artifact_id", "sha256", "name", "version", "size")},
            "source": dict(saved.manifest["source"]),
            "counts": dict(saved.manifest["counts"]),
            "documents": [{"document_id": row["document_id"], "name": row["name"],
                           "version": row["version"], "characters": len(row["content"])}
                          for row in saved.documents]}
