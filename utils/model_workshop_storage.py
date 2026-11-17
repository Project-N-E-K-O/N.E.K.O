"""Read-only storage probes, independent of the higher-level workshop services.

These helpers preserve unknown data and never recover, create or delete anything.
They do not interpret a registry row as permission to access a model.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import portalocker


class ModelWorkshopStorageError(ValueError):
    """A stable storage-boundary error, without exposing local paths."""


def is_redirecting_reparse(info) -> bool:
    """Match storage migration's name-surrogate rule, allowing cloud data tags."""
    tag = getattr(info, "st_reparse_tag", None)
    if tag is not None:
        return bool(int(tag) & 0x20000000)
    # Older stat providers without tags cannot prove a reparse point is data.
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _ordinary(path: Path):
    """Only a missing entry proves absence; reject junctions and symbolic links."""
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or is_redirecting_reparse(info)
        or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
        or not info.st_ino
    ):
        raise ValueError("unsafe workshop storage")
    return info


def _registry(path: Path):
    """Read a bounded envelope; detailed row validation remains in the service."""
    before = _ordinary(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > 16 * 1024 * 1024:
        raise ValueError("unreadable workshop registry")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as source:
        actual = os.fstat(source.fileno())
        if (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(actual.st_mode):
            raise ValueError("changed workshop registry")
        raw = source.read(16 * 1024 * 1024 + 1)
        after = os.fstat(source.fileno())
        if len(raw) > 16 * 1024 * 1024 or (actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("changed workshop registry")

    def unique(pairs):
        # Duplicate envelope fields must not hide meaningful state as empty.
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate registry field")
            result[key] = value
        return result

    data = json.loads(raw, object_pairs_hook=unique)
    if (
        not isinstance(data, dict)
        or set(data) != {"schema", "installed", "attempts"}
        or data["schema"] != "neko.model.installed.v1"
        or not isinstance(data["installed"], dict)
        or not isinstance(data["attempts"], dict)
    ):
        raise ValueError("unrecognized workshop registry")
    return data


def _atomic_temporary(path: Path) -> bool:
    """Recognize the real atomic writer's regular temporaries, never links/dirs."""
    if not path.name.startswith(".nkatmp"):
        return False
    from utils.file_utils import _STALE_TMP_RE

    return bool(_STALE_TMP_RE.fullmatch(path.name)) and stat.S_ISREG(_ordinary(path).st_mode)


def _has_transactions(directory: Path) -> bool:
    """A leftover atomic-write temporary is not a durable install journal."""
    return any(not _atomic_temporary(child) for child in directory.iterdir())


def _layout(directory: Path):
    """Bound traversal and refuse unknown metadata rather than discarding it."""
    if not stat.S_ISDIR(_ordinary(directory).st_mode):
        raise ValueError("unsafe workshop directory")
    entries = {child.name: child for child in directory.iterdir() if not _atomic_temporary(child)}
    if entries.keys() - {"installed.json", "locks", "staging", "transactions"}:
        raise ValueError("unknown workshop metadata")
    for name in ("locks", "staging", "transactions"):
        if name in entries and not stat.S_ISDIR(_ordinary(entries[name]).st_mode):
            raise ValueError("unsafe workshop directory")
    # Temporary bytes do not make an empty root user-owned. Links, unreadable
    # trees and oversized listings do: never allow an emptiness probe to erase them.
    count = 0

    def failed(error):
        raise error

    for parent, dirs, files in os.walk(directory, onerror=failed, followlinks=False):
        for name in dirs + files:
            count += 1
            if count > 8192:
                raise ValueError("workshop listing too large")
            _ordinary(Path(parent) / name)
    if "locks" in entries:
        locks = list(entries["locks"].iterdir())
        if any(p.name != "writer.lock" or not stat.S_ISREG(_ordinary(p).st_mode) or p.stat().st_size for p in locks):
            raise ValueError("unknown workshop lock")
    return entries


def model_workshop_has_user_content(directory: Path) -> bool:
    """Ignore only recognized empty infrastructure and unpublished stage bytes."""
    try:
        entries = _layout(directory)
        if "transactions" in entries and _has_transactions(entries["transactions"]):
            return True  # Pending/unknown logs are evidence, never a disposable cache.
        if "installed.json" in entries:
            data = _registry(entries["installed.json"])
            return bool(data["installed"] or data["attempts"])
        return False
    except FileNotFoundError:
        # A disappearance inside an existing tree is uncertain, not emptiness.
        return os.path.lexists(directory)
    except (OSError, ValueError, RecursionError):
        return True


def inspect_model_workshop_metadata(root: Path):
    """Validate storage evidence without acquiring or releasing any writer."""
    directory = root / "model_workshop"
    try:
        _ordinary(directory)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ModelWorkshopStorageError("model_workshop_state_unreadable") from exc
    try:
        entries = _layout(directory)
        if "transactions" in entries and _has_transactions(entries["transactions"]):
            raise ModelWorkshopStorageError("model_workshop_pending")
        if "installed.json" in entries:
            _registry(entries["installed.json"])
        return entries
    except ModelWorkshopStorageError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise ModelWorkshopStorageError("model_workshop_state_unreadable") from exc


def assert_model_workshop_stopped(root: Path) -> None:
    """Probe an existing OS owner without initializing or recovering its service.

    The launcher calls this after servers stop. Main must first quiesce its own
    idle owner under the storage write fence; an earlier HTTP preflight alone is
    not authorization to copy. Never hold this handle while moving the lock file.
    """
    directory = root / "model_workshop"
    # Only lstat-confirmed absence is empty. lexists() collapses lookup errors
    # into False, which must not authorize moving unreadable private metadata.
    try:
        _ordinary(directory)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        raise ModelWorkshopStorageError("model_workshop_state_unreadable") from exc
    handle = None
    locked = False
    try:
        lock = directory / "locks/writer.lock"
        before = _ordinary(lock)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("unsafe workshop lock")
        fd = os.open(lock, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        handle = os.fdopen(fd, "r+b", buffering=0)
        actual = os.fstat(handle.fileno())
        if actual.st_nlink != 1 or (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("changed workshop lock")
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
            locked = True
        except portalocker.exceptions.LockException as exc:
            raise ModelWorkshopStorageError("model_workshop_busy") from exc
        # Probe ownership before examining potentially changing metadata: an
        # active writer is busy, not corrupt. Never recover or sweep here.
        if inspect_model_workshop_metadata(root) is None:
            raise ModelWorkshopStorageError("model_workshop_state_unreadable")
    except ModelWorkshopStorageError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise ModelWorkshopStorageError("model_workshop_state_unreadable") from exc
    finally:
        if handle is not None:
            try:
                if locked:
                    portalocker.unlock(handle)
            finally:
                handle.close()
