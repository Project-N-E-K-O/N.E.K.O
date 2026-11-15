"""Pinned runtime validation and loading from an explicitly supplied cache.

This provider owns no deployment discovery, downloads or publication policy.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import sys
import types
import zipfile

SUPPORTED_RUNTIME_VERSION = "1.13.8+neko.kws2"
WHEEL_NAME = "sherpa_onnx-1.13.8+neko.kws2-cp311-cp311-win_amd64.whl"
WHEEL_URL = ("https://github.com/Project-N-E-K-O/N.E.K.O/releases/download/"
             "wake-word-runtime-v1.13.8-neko.kws2-win-py311/" + WHEEL_NAME)
WHEEL_SHA256 = "3cea8dbb31416c2cb0742cdea42184259afa25cde3bbcc7c46ba0e25fe391564"
WHEEL_BYTES = 10816152
MAX_EXPANDED_BYTES = 192 * 1024 * 1024
MAX_FILES = 512
_DLL_HANDLES = []


class RuntimeBundleError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def runtime_repair_supported() -> bool:
    return (platform.system() == "Windows" and platform.machine().lower() in {"amd64", "x86_64"}
            and sys.version_info[:2] == (3, 11))


def _safe(path: Path) -> Path:
    """Reject redirected or aliased component files before executing native code."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        metadata = None
    if path.is_symlink() or metadata is not None and (
        getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1
    ):
        raise RuntimeBundleError("resource_cache_unsafe")
    return path.resolve()


def _current(root: Path) -> Path | None:
    pointer = _safe(root / "current.json")
    if not pointer.exists():
        return None
    try:
        if pointer.stat().st_size > 512:
            raise ValueError
        value = json.loads(pointer.read_text(encoding="utf-8"))
        version = value["version"]
        if (set(value) != {"version"} or not isinstance(version, str)
                or not version.startswith(WHEEL_SHA256 + "-")
                or len(version) != len(WHEEL_SHA256) + 33
                or any(c not in "0123456789abcdef" for c in version[-32:])):
            raise ValueError
        return _safe(root / version)
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED") from exc


def _digest(path: Path) -> str:
    _safe(path)
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _verified_wheel(root: Path) -> Path:
    wheel = _safe(root / WHEEL_NAME)
    if not wheel.is_file() or wheel.stat().st_size != WHEEL_BYTES or _digest(wheel) != WHEEL_SHA256:
        raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
    return wheel


def _inventory(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    entries, total, names = [], 0, set()
    for info in archive.infolist():
        path = PurePosixPath(info.orig_filename)
        total += info.file_size
        if (path.is_absolute() or ".." in path.parts or "\\" in info.orig_filename or ":" in info.orig_filename
                or stat.S_ISLNK(info.external_attr >> 16) or total > MAX_EXPANDED_BYTES
                or len(archive.infolist()) > MAX_FILES or info.filename.casefold() in names):
            raise RuntimeBundleError("resource_cache_unsafe")
        names.add(info.filename.casefold())
        if not info.is_dir() and path.parts[0] == "sherpa_onnx":
            entries.append(info)
    if not any(info.filename == "sherpa_onnx/__init__.py" for info in entries):
        raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
    return entries


def _verify_directory(root: Path, directory: Path) -> None:
    _safe(directory)
    with zipfile.ZipFile(_verified_wheel(root)) as archive:
        entries = _inventory(archive)
        expected = {entry.filename for entry in entries}
        actual = set()
        for path in directory.rglob("*"):
            _safe(path)
            if path.is_file():
                actual.add(path.relative_to(directory).as_posix())
        if actual != expected:
            raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
        for entry in entries:
            path = directory / entry.filename
            if path.stat().st_size != entry.file_size or _digest(path) != hashlib.sha256(archive.read(entry)).hexdigest():
                raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED")


def _load_directory(directory: Path):
    """Explicit extension loading also bypasses frozen package finders.

    Only disposable KWS/resource workers call this. Never unload native modules
    from a live process; an incompatible loaded runtime requires a fresh worker.
    """
    existing = sys.modules.get("sherpa_onnx")
    if existing is not None:
        if (getattr(existing, "__version__", None) != SUPPORTED_RUNTIME_VERSION
                or getattr(existing, "version", None) != SUPPORTED_RUNTIME_VERSION):
            raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
        return existing
    package = directory / "sherpa_onnx"
    libraries = package / "lib"
    if os.name == "nt":
        _DLL_HANDLES.append(os.add_dll_directory(str(libraries)))
    created = []
    old_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("sherpa_onnx", package / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["sherpa_onnx"] = module
        created.append("sherpa_onnx")
        lib = types.ModuleType("sherpa_onnx.lib")
        lib.__path__ = [str(libraries)]
        sys.modules["sherpa_onnx.lib"] = lib
        created.append("sherpa_onnx.lib")
        extensions = tuple(libraries.glob("_sherpa_onnx*.pyd"))
        if len(extensions) != 1:
            raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
        native_spec = importlib.util.spec_from_file_location("sherpa_onnx.lib._sherpa_onnx", extensions[0])
        native = importlib.util.module_from_spec(native_spec)
        sys.modules[native_spec.name] = native
        created.append(native_spec.name)
        native_spec.loader.exec_module(native)
        spec.loader.exec_module(module)
        if module.__version__ != SUPPORTED_RUNTIME_VERSION or module.version != SUPPORTED_RUNTIME_VERSION:
            raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
        return module
    except BaseException:
        for name in tuple(sys.modules):
            if name in created or name.startswith("sherpa_onnx."):
                sys.modules.pop(name, None)
        raise
    finally:
        sys.dont_write_bytecode = old_bytecode


def load_runtime(root: Path | None = None):
    """Load a verified explicit cache, or the bundled installation."""
    if root is not None:
        root = _safe(root)
        directory = _current(root)
        if directory is not None:
            _verify_directory(root, directory)
            return _load_directory(directory)
    import sherpa_onnx
    if (getattr(sherpa_onnx, "__version__", None) != SUPPORTED_RUNTIME_VERSION
            or getattr(sherpa_onnx, "version", None) != SUPPORTED_RUNTIME_VERSION):
        raise RuntimeBundleError("WAKE_WORD_RUNTIME_FIX_REQUIRED")
    return sherpa_onnx
