"""Private workshop paths and filesystem operations; importing this creates nothing."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .bundle import _constant, _object


class WorkshopError(ValueError):
    """Stable internal code, translated by the later HTTP/UI adapter."""


def identity(path: Path) -> list[int]:
    """Use no-follow device/file identity, never a timestamp or content hash."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise WorkshopError("model_workshop_unsafe_path")
    kind = stat.S_IFMT(info.st_mode)
    if kind not in (stat.S_IFREG, stat.S_IFDIR) or not info.st_ino:
        raise WorkshopError("model_workshop_identity_unavailable")
    return [info.st_dev, info.st_ino, kind]


def regular(path: Path, flags: int = os.O_RDONLY):
    """Open without following a link; verify the opened object and pathname agree."""
    before = identity(path) if not flags & os.O_EXCL else None
    fd = os.open(
        path,
        flags
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        info = os.fstat(fd)
        actual = [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]
        if (
            actual[2] != stat.S_IFREG
            or identity(path) != actual
            or before not in (None, actual)
        ):
            raise WorkshopError("model_workshop_unsafe_path")
        return os.fdopen(
            fd, "r+b" if flags & (os.O_WRONLY | os.O_RDWR) else "rb", buffering=0
        )
    except BaseException:
        os.close(fd)
        raise


def sync_directory(path: Path) -> None:
    """Persist directory entries where supported, as in the existing storage code."""
    if os.name == "nt":
        # Windows uses atomic rename and flushed files; power-loss durability is
        # a separate platform acceptance check, not a POSIX fsync claim.
        return
    fd = os.open(
        path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_all(handle, raw: bytes) -> None:
    """A short regular-file write must not turn into a published truncated resource."""
    remaining = memoryview(raw)
    while remaining:
        count = handle.write(remaining)
        if not count:
            raise OSError(errno.EIO, "model workshop short write")
        remaining = remaining[count:]


def read_json(path: Path, limit: int):
    """Only genuine absence is empty; malformed, linked or unreadable data fails closed."""
    try:
        handle = regular(path)
    except FileNotFoundError:
        if os.path.lexists(path):
            raise WorkshopError("model_workshop_state_unreadable")
        return None
    except (OSError, ValueError) as exc:
        raise WorkshopError("model_workshop_state_unreadable") from exc
    try:
        with handle:
            before = os.fstat(handle.fileno())
            if before.st_size > limit:
                raise WorkshopError("model_workshop_state_unreadable")
            raw = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
            if len(raw) > limit or (
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise WorkshopError("model_workshop_state_unreadable")
            if identity(path)[:2] != [after.st_dev, after.st_ino]:
                raise WorkshopError("model_workshop_state_unreadable")
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant
        )
        if not isinstance(data, dict):
            raise WorkshopError("model_workshop_state_unreadable")
        return data
    except (OSError, ValueError, RecursionError) as exc:
        # Absence after a successful open is a concurrent change, never a new
        # registry: callers must not replace an unreadable state with {}.
        raise WorkshopError("model_workshop_state_unreadable") from exc


def write_json(path: Path, data, limit: int) -> None:
    """Replace checked metadata atomically; model publication uses a different primitive."""
    from utils.file_utils import atomic_write_text

    raw = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > limit:
        raise WorkshopError("model_workshop_state_too_large")
    if os.path.lexists(path):
        if identity(path)[2] != stat.S_IFREG:
            raise WorkshopError("model_workshop_unsafe_path")
    atomic_write_text(path, raw, create_parents=False)
    sync_directory(path.parent)


def publish_directory(source: Path, target: Path) -> None:
    """Use the same native exclusive rename flags as storage migration, with no copy fallback."""
    if os.name == "nt":
        os.rename(
            source, target
        )  # Windows rename refuses even an existing empty directory.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is not None:
            rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            result = rename(os.fsencode(source), os.fsencode(target), 4)
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is not None:
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
            result = rename(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    else:
        rename = None
    if rename is None:
        raise WorkshopError("model_workshop_publication_unsupported")
    if result:
        code = ctypes.get_errno()
        raise OSError(code or errno.EIO, os.strerror(code or errno.EIO), str(target))


@dataclass(frozen=True)
class WorkshopPaths:
    root: Path
    live2d: Path
    vrm: Path
    mmd: Path
    root_identity: tuple[int, ...]

    @classmethod
    def capture(cls, config):
        """Resolve aliases of the runtime root, but never follow model-directory links."""
        root = Path(config.app_docs_dir).resolve()
        values = [
            Path(getattr(config, key)).absolute()
            for key in ("live2d_dir", "vrm_dir", "mmd_dir")
        ]
        if any(
            path.parent.resolve() != root or path.name != name
            for path, name in zip(values, ("live2d", "vrm", "mmd"))
        ):
            raise WorkshopError("model_workshop_unsafe_path")
        return cls(
            root, root / "live2d", root / "vrm", root / "mmd", tuple(identity(root))
        )

    @property
    def private(self):
        return self.root / "model_workshop"

    def checked(self, relative: str) -> Path:
        """Check every existing ancestor, including junctions, before touching a managed path."""
        if tuple(identity(self.root)) != self.root_identity:
            raise WorkshopError("model_workshop_root_changed")
        parts = relative.split("/")
        if any(
            not part or part in (".", "..") or "\\" in part or ":" in part
            for part in parts
        ):
            raise WorkshopError("model_workshop_unsafe_path")
        current = self.root
        for part in parts:
            if os.path.lexists(current) and identity(current)[2] != stat.S_IFDIR:
                raise WorkshopError("model_workshop_unsafe_path")
            current = current / part
            if os.path.lexists(current):
                identity(current)
        return current

    def mkdir(self, relative: str) -> Path:
        """Create each private ancestor explicitly and persist its parent entry."""
        parts = relative.split("/")
        for count in range(1, len(parts) + 1):
            path = self.checked("/".join(parts[:count]))
            try:
                path.mkdir(mode=0o700)
                sync_directory(path.parent)
            except FileExistsError:
                pass
            if (
                identity(path)[2] != stat.S_IFDIR
                or path.stat().st_dev != self.root_identity[0]
            ):
                raise WorkshopError("model_workshop_unsafe_path")
        return path

    @contextmanager
    def transaction(self, config):
        """Enter/exit the existing write fence in one thread and recheck the captured root."""
        from utils.cloudsave_runtime import cloudsave_writable_transaction
        from utils.storage.location_bootstrap import (
            get_storage_startup_blocking_reason_readonly,
        )

        if get_storage_startup_blocking_reason_readonly(config):
            raise WorkshopError("model_workshop_storage_blocked")
        with cloudsave_writable_transaction(
            config, operation="save", target="model_workshop"
        ):
            if self != self.capture(config):
                raise WorkshopError("model_workshop_root_changed")
            yield
