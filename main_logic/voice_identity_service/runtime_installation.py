"""Deployment-owned delivery and publication of the pinned wake runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import urllib.error
import urllib.request
import uuid
import zipfile

from config.resource_file_lock import canonical_resource_root, resource_file_lock
from config.voice_wake_word import wake_word_cache_root
from main_logic.voice_input.wake_word.runtime_bundle import (
    RuntimeBundleError, WHEEL_NAME, WHEEL_URL, WHEEL_SHA256, WHEEL_BYTES,
    MAX_EXPANDED_BYTES, MAX_FILES, _safe, _digest, _verified_wheel, _inventory,
    _verify_directory, _current, _load_directory, runtime_repair_supported,
    load_runtime as load_runtime_bundle,
)


def runtime_cache_root() -> Path:
    """Resolve deployment configuration without probing or importing native code."""
    try:
        return wake_word_cache_root().parent / "wake-runtime"
    except (OSError, RuntimeError) as exc:
        raise RuntimeBundleError("resource_storage_unavailable") from exc


def load_runtime():
    """Resource workers use the same explicit cache as the session factory."""
    return load_runtime_bundle(runtime_cache_root())


def _root(root: Path | None = None) -> Path:
    try:
        return canonical_resource_root(root or runtime_cache_root())
    except ValueError as exc:
        raise RuntimeBundleError("resource_cache_unsafe") from exc
    except (OSError, RuntimeError) as exc:
        raise RuntimeBundleError("resource_storage_unavailable") from exc


def runtime_present(root: Path | None = None) -> bool:
    """Discovery only; native imports and content validation belong to workers."""
    try:
        directory = _current(_root(root))
        return directory is not None and (directory / "sherpa_onnx" / "__init__.py").is_file()
    except (OSError, RuntimeBundleError):
        return False


def stage_runtime(*, root: Path | None = None, on_progress=None) -> dict:
    if not runtime_repair_supported():
        raise RuntimeBundleError("WAKE_WORD_PLATFORM_UNSUPPORTED")
    root = _root(root)
    root.mkdir(parents=True, exist_ok=True)
    def report(phase, **details):
        if on_progress:
            on_progress({"phase": phase, "resource": "wake_runtime", **details})
    with resource_file_lock(root / "install.lock"):
        stage = _safe(root / "pending")
        try:
            final = _current(root)
            if final is not None:
                _verify_directory(root, final)
                _load_directory(final)
                return {"installed": True}
        except (RuntimeBundleError, OSError):
            # Preserve the damaged generation; repair publishes a fresh one.
            pass
        if len(tuple(root.glob(WHEEL_SHA256 + "-*"))) >= 3:
            raise RuntimeBundleError("resource_cache_full")
        report("checking")
        try:
            wheel = _verified_wheel(root)
        except RuntimeBundleError:
            wheel = _safe(root / WHEEL_NAME)
            partial = _safe(root / "download.partial")
            # Fixed bounded staging names; interrupted attempts overwrite only
            # this component's partial download, never a published directory.
            try:
                with urllib.request.urlopen(WHEEL_URL, timeout=30) as response, partial.open("wb") as output:
                    received = 0
                    report("downloading", downloaded_bytes=0, total_bytes=WHEEL_BYTES, attempt=1)
                    while chunk := response.read(128 * 1024):
                        received += len(chunk)
                        if received > WHEEL_BYTES:
                            raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
                        output.write(chunk)
                        report("downloading", downloaded_bytes=received, total_bytes=WHEEL_BYTES, attempt=1)
                    output.flush()
                    os.fsync(output.fileno())
            except (urllib.error.URLError, TimeoutError) as exc:
                raise RuntimeBundleError("resource_model_repair_failed") from exc
            report("verifying")
            if partial.stat().st_size != WHEEL_BYTES or _digest(partial) != WHEEL_SHA256:
                raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
            os.replace(partial, wheel)
        if stage.exists():
            # Clean only our fixed, bounded inventory. Refuse links/unknown
            # contents rather than deleting something we cannot attribute.
            _safe(stage)
            paths = tuple(stage.rglob("*"))
            if len(paths) > MAX_FILES or sum(p.stat().st_size for p in paths if p.is_file()) > MAX_EXPANDED_BYTES:
                raise RuntimeBundleError("resource_cache_unsafe")
            for path in paths:
                _safe(path)
            with zipfile.ZipFile(wheel) as archive:
                allowed = {entry.filename for entry in _inventory(archive)}
            if any(p.is_file() and p.relative_to(stage).as_posix() not in allowed for p in paths):
                raise RuntimeBundleError("resource_cache_unsafe")
            shutil.rmtree(stage)
        stage.mkdir()
        report("verifying")
        with zipfile.ZipFile(wheel) as archive:
            for entry in _inventory(archive):
                destination = stage / entry.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, 128 * 1024)
        _verify_directory(root, stage)
        report("loading")
        _load_directory(stage)
        ticket = uuid.uuid4().hex
        _safe(root / "pending.id").write_text(ticket, encoding="ascii")
        return {"installed": False, "ticket": ticket}


def publish_runtime(root: Path, ticket: str) -> dict:
    """Owner-only bounded commit, after the staging worker has retired."""
    root = _root(root)
    if not isinstance(ticket, str) or len(ticket) != 32 or any(c not in "0123456789abcdef" for c in ticket):
        raise RuntimeBundleError("resource_cache_unsafe")
    with resource_file_lock(root / "install.lock"):
        marker = _safe(root / "pending.id")
        if marker.stat().st_size != 32 or marker.read_text(encoding="ascii") != ticket:
            raise RuntimeBundleError("resource_operation_busy")
        stage = _safe(root / "pending")
        _verify_directory(root, stage)
        final = _safe(root / (WHEEL_SHA256 + "-" + ticket))
        if len(tuple(root.glob(WHEEL_SHA256 + "-*"))) >= 3:
            raise RuntimeBundleError("resource_cache_full")
        os.rename(stage, final)
        temporary = _safe(root / "current.pending")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump({"version": final.name}, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, _safe(root / "current.json"))
    return {"installed": True}


def runtime_version_published(root: Path, ticket: str) -> bool:
    try:
        current = _current(_root(root))
        return current is not None and current.name == WHEEL_SHA256 + "-" + ticket
    except (OSError, RuntimeBundleError):
        return False
