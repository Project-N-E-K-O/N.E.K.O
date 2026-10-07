"""Pinned, bounded KWS bundles published through a single atomic pointer.

Published directories are immutable and never removed while this model revision
is supported. This is also the pin for processes using an older current pointer.
The cache budget refuses further installs instead of deleting live model files.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import threading
import urllib.request
import uuid

from config.voice_wake_word import wake_word_cache_root
from config.resource_file_lock import ResourceFileLockBusy, canonical_resource_root, resource_file_lock

MODEL_NAME = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{MODEL_NAME}.tar.bz2"
MODEL_SHA256 = "68447f4fbc67e70eee3a93961f36e81e98f47aef73ce7e7ca00885c6cd3616a6"
ASSETS = (
    "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
    "decoder-epoch-13-avg-2-chunk-8-left-64.onnx",
    "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
    "tokens.txt",
)
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ASSET_BYTES = 16 * 1024 * 1024
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_VERSIONS = 3
_STAGE_PREFIX = "stage-neko-kws-"
_STAGE_OWNER = b"NEKO-KWS-CACHE-V1"


class WakeWordBundleError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _valid_version_name(version: object) -> bool:
    return (version == MODEL_SHA256 or isinstance(version, str) and
            version.startswith(MODEL_SHA256 + "-") and len(version) == len(MODEL_SHA256) + 33
            and all(character in "0123456789abcdef" for character in version[-32:]))


def _matches_bundle(directory: Path, hashes: dict[str, str]) -> bool:
    try:
        _safe_root(directory)
        manifest = directory / "bundle.json"
        if manifest.is_symlink() or manifest.stat().st_size > 8192:
            return False
        if json.loads(manifest.read_text(encoding="utf-8")) != hashes:
            return False
        for name, digest in hashes.items():
            asset = directory / name
            if _safe_root(asset).parent != directory or _hash(asset) != digest:
                return False
        return True
    except (OSError, ValueError, TypeError, WakeWordBundleError):
        return False


def default_cache_root() -> Path:
    """Return a per-user cache path without probing or creating it."""
    try:
        return wake_word_cache_root()
    except (OSError, RuntimeError) as exc:
        raise WakeWordBundleError("resource_storage_unavailable") from exc


def _hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise WakeWordBundleError("operation_cancelled")


def _safe_root(root: Path) -> Path:
    # Check each cache boundary/descendant, not trusted redirected ancestors.
    try:
        return canonical_resource_root(root)
    except ValueError as exc:
        raise WakeWordBundleError("resource_cache_unsafe") from exc


def _remove_abandoned_stages(root: Path) -> None:
    """Only remove our marked, bounded artifacts; never follow reparse points."""
    for stage in root.glob(f"{_STAGE_PREFIX}*"):
        if _safe_root(stage).parent != root or not stage.is_dir():
            raise WakeWordBundleError("resource_cache_unsafe")
        children = tuple(stage.iterdir())
        if not children:
            stage.rmdir()  # Interrupted between mkdir and owner marker.
            continue
        marker = stage / ".owner"
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 64:
            raise WakeWordBundleError("resource_cache_unsafe")
        if marker.read_bytes() != _STAGE_OWNER:
            # A crash before the first marker write can leave this empty file.
            if len(children) != 1 or marker.stat().st_size != 0:
                raise WakeWordBundleError("resource_cache_unsafe")
        for child in children:
            _safe_root(child)
            if child.name == "bundle" and child.is_dir():
                for asset in child.iterdir():
                    if (_safe_root(asset).parent != child or not asset.is_file()
                            or asset.name not in {*ASSETS, "bundle.json"}
                            or asset.stat().st_size > MAX_ASSET_BYTES):
                        raise WakeWordBundleError("resource_cache_unsafe")
            elif (not child.is_file() or child.name not in {".owner", "archive.tar.bz2", "current.json"}
                  or child.stat().st_size > MAX_ARCHIVE_BYTES):
                raise WakeWordBundleError("resource_cache_unsafe")
        # Retain ownership proof until every payload is gone. Interrupted
        # cleanup can then be retried without treating our debris as foreign.
        for child in children:
            if child == marker:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        marker.unlink()
        stage.rmdir()


@contextmanager
def _staging_directory(root: Path):
    stage = Path(tempfile.mkdtemp(prefix=_STAGE_PREFIX, dir=root))
    try:
        yield stage
    finally:
        _remove_abandoned_stages(root)


@contextmanager
def _installation_lock(root: Path):
    """OS-owned advisory lock is released even when the process is terminated."""
    try:
        with resource_file_lock(root / "install.lock"):
            yield
    except ResourceFileLockBusy as exc:
        raise WakeWordBundleError("resource_operation_busy") from exc


def resolve_cached_model_dir(root: Path | None = None) -> Path | None:
    """Validate the published pointer and all asset hashes; never mutate cache."""
    try:
        root = _safe_root(root or default_cache_root())
        pointer = root / "current.json"
        if pointer.is_symlink():
            raise WakeWordBundleError("wake_model_invalid")
        if not pointer.exists():
            return None
        if pointer.is_symlink() or pointer.stat().st_size > 4096:
            raise ValueError
        data = json.loads(pointer.read_text(encoding="utf-8"))
        if type(data) is not dict or set(data) != {"schema", "version", "model"}:
            raise ValueError
        version = data["version"]
        if type(data["schema"]) is not int or data["schema"] != 1 or data["model"] != MODEL_NAME or not _valid_version_name(version):
            raise ValueError
        directory = root / "versions" / version
        if directory.is_symlink() or directory.resolve() != directory:
            raise ValueError
        manifest_path = directory / "bundle.json"
        if manifest_path.stat().st_size > 8192 or manifest_path.is_symlink():
            raise ValueError
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if type(manifest) is not dict or set(manifest) != set(ASSETS):
            raise ValueError
        for name, digest in manifest.items():
            asset = directory / name
            if asset.is_symlink() or not asset.is_file() or not 0 < asset.stat().st_size <= MAX_ASSET_BYTES:
                raise ValueError
            if _hash(asset) != digest:
                raise ValueError
        return directory
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise WakeWordBundleError("wake_model_invalid") from exc


def install_bundle(
    root: Path,
    archive: Path | None = None,
    *,
    cancel: threading.Event | None = None,
    validate=None,
    commit_lock: threading.Lock | None = None,
    publish: bool = True,
) -> Path:
    """Authenticate, stage, validate, then publish the complete fixed bundle."""
    cancel = cancel or threading.Event()
    commit_lock = commit_lock or threading.Lock()
    root = _safe_root(root)
    root.mkdir(parents=True, exist_ok=True)
    with _installation_lock(root):
        versions = root / "versions"
        _safe_root(versions)
        versions.mkdir(exist_ok=True)
        _remove_abandoned_stages(root)
        existing = []
        for version in versions.iterdir():
            existing.append(version)
            if len(existing) > MAX_VERSIONS:
                raise WakeWordBundleError("resource_cache_full")
        size = 0
        for version in existing:
            if _safe_root(version).parent != versions or not version.is_dir():
                raise WakeWordBundleError("resource_cache_unsafe")
            for asset in version.iterdir():
                if _safe_root(asset).parent != version or not asset.is_file():
                    raise WakeWordBundleError("resource_cache_unsafe")
                size += asset.stat().st_size
        target = versions / MODEL_SHA256
        if size + MAX_ARCHIVE_BYTES + len(ASSETS) * MAX_ASSET_BYTES > MAX_CACHE_BYTES:
            raise WakeWordBundleError("resource_cache_full")
        with _staging_directory(root) as temporary:
            stage = Path(temporary)
            with (stage / ".owner").open("wb") as marker:
                marker.write(_STAGE_OWNER)
                marker.flush()
                os.fsync(marker.fileno())
            source = archive or stage / "archive.tar.bz2"
            if archive is None:
                request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "NEKO-model-provisioner"})
                with urllib.request.urlopen(request, timeout=10) as response, source.open("wb") as out:
                    count = 0
                    while block := response.read(1024 * 1024):
                        _check_cancel(cancel)
                        count += len(block)
                        if count > MAX_ARCHIVE_BYTES:
                            raise WakeWordBundleError("resource_download_too_large")
                        out.write(block)
            _check_cancel(cancel)
            if source.stat().st_size > MAX_ARCHIVE_BYTES or _hash(source) != MODEL_SHA256:
                raise WakeWordBundleError("resource_archive_invalid")
            bundle_dir = stage / "bundle"
            bundle_dir.mkdir()
            hashes = {}
            with tarfile.open(source, "r:bz2") as bundle:
                members = bundle.getmembers()
                for name in ASSETS:
                    matched = [member for member in members if member.name == f"{MODEL_NAME}/{name}"]
                    if len(matched) != 1 or not matched[0].isfile() or not 0 < matched[0].size <= MAX_ASSET_BYTES:
                        raise WakeWordBundleError("resource_archive_invalid")
                    _check_cancel(cancel)
                    with bundle.extractfile(matched[0]) as data, (bundle_dir / name).open("wb") as out:
                        shutil.copyfileobj(data, out, length=1024 * 1024)
                        out.flush()
                        os.fsync(out.fileno())
                    hashes[name] = _hash(bundle_dir / name)
            with (bundle_dir / "bundle.json").open("w", encoding="utf-8") as out:
                json.dump(hashes, out)
                out.flush()
                os.fsync(out.fileno())
            _check_cancel(cancel)
            if validate is not None:
                validate(bundle_dir)
            _check_cancel(cancel)
            reusable = next((directory for directory in existing if _valid_version_name(directory.name)
                             and _matches_bundle(directory, hashes)), None)
            if reusable is not None:
                target = reusable
            elif target.exists():
                # Repair publishes a new immutable version. Existing
                # processes can still own files in the damaged directory.
                target = versions / (MODEL_SHA256 + "-" + uuid.uuid4().hex)
            if not target.exists():
                if len(existing) >= MAX_VERSIONS:
                    raise WakeWordBundleError("resource_cache_full")
                bundle_dir.replace(target)
            if not publish:
                return target
            pointer = stage / "current.json"
            with pointer.open("w", encoding="utf-8") as handle:
                json.dump({"schema": 1, "version": target.name, "model": MODEL_NAME}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            with commit_lock:
                _check_cancel(cancel)
                os.replace(pointer, root / "current.json")
            return target


def publish_bundle_version(root: Path, version: str) -> None:
    """Publish a worker-validated immutable version under the deployment lock.

    Only the operation owner calls this after entering its non-cancellable
    commit boundary. No network, native model work or arbitrary paths run here.
    """
    root = _safe_root(root)
    if not _valid_version_name(version):
        raise WakeWordBundleError("wake_model_invalid")
    with _installation_lock(root):
        directory = root / "versions" / version
        if _safe_root(directory).parent != root / "versions":
            raise WakeWordBundleError("wake_model_invalid")
        manifest = directory / "bundle.json"
        if manifest.is_symlink() or manifest.stat().st_size > 8192:
            raise WakeWordBundleError("wake_model_invalid")
        try:
            hashes = json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError, TypeError) as exc:
            raise WakeWordBundleError("wake_model_invalid") from exc
        if (type(hashes) is not dict or set(hashes) != set(ASSETS)
                or any(not 0 < (directory / name).stat().st_size <= MAX_ASSET_BYTES for name in ASSETS)
                or not _matches_bundle(directory, hashes)):
            raise WakeWordBundleError("wake_model_invalid")
        pointer = root / "current.pending"
        if pointer.is_symlink() or pointer.exists() and (not pointer.is_file() or pointer.stat().st_size > 4096):
            raise WakeWordBundleError("resource_cache_unsafe")
        try:
            with pointer.open("w", encoding="utf-8") as handle:
                json.dump({"schema": 1, "version": version, "model": MODEL_NAME}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pointer, root / "current.json")
        finally:
            pointer.unlink(missing_ok=True)


def is_bundle_version_published(root: Path, version: str) -> bool:
    """Recover an interrupted publisher's receipt from the atomic pointer."""
    try:
        root = _safe_root(root)
        pointer = root / "current.json"
        if pointer.is_symlink() or pointer.stat().st_size > 4096:
            return False
        data = json.loads(pointer.read_text(encoding="utf-8"))
        return (type(data) is dict and type(data.get("schema")) is int and
                data == {"schema": 1, "version": version, "model": MODEL_NAME})
    except (OSError, ValueError, TypeError, WakeWordBundleError):
        return False
