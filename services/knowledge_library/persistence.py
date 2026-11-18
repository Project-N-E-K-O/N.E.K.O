"""Bound domain records and persist files before activating their manifest."""

from contextlib import suppress
import json
import os
from pathlib import Path
import tempfile

from utils.file_utils import _replace_with_busy_retry

from .models import LibraryError


# The writer and formal reader share one limit. Compressed package limits do
# not bound the JSON representation of decoded float vectors.
MAX_METADATA_BYTES = 128 * 1024 * 1024


class CommittedWriteError(OSError):
    """The replacement is visible, but directory durability is unconfirmed."""


def sync_directory(path: Path) -> None:
    """Persist directory entries on platforms exposing a directory fsync."""
    if os.name == "nt":
        # Python has no equivalent portable directory handle on Windows.
        # File fsync and the existing Windows replace retry still apply.
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_file(path: Path) -> None:
    """Flush a closed completed artifact or index before publishing its name."""
    # Windows FlushFileBuffers requires a handle with GENERIC_WRITE. Opening
    # r+b grants that access without truncating or changing immutable bytes.
    with path.open("r+b" if os.name == "nt" else "rb") as stream:
        os.fsync(stream.fileno())


def ensure_directory(path: Path) -> None:
    """Persist newly created directory entries from the existing ancestor down."""
    missing = []
    parent = Path(path)
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        sync_directory(directory.parent)


def write_json(path: Path, record: dict) -> None:
    """Stream a bounded record and atomically replace only fully written data."""
    if not isinstance(record, dict):
        raise LibraryError("metadata_invalid")
    path = Path(path)
    ensure_directory(path.parent)
    descriptor, name = tempfile.mkstemp(prefix=".record-", suffix=".tmp", dir=path.parent)
    stage = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            size = 0
            encoder = json.JSONEncoder(ensure_ascii=False, indent=2, allow_nan=False)
            for part in encoder.iterencode(record):
                raw = part.encode("utf-8")
                size += len(raw)
                if size > MAX_METADATA_BYTES:
                    raise LibraryError("metadata_too_large")
                stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Reuse the existing Windows busy-file retry without buffering the
        # entire encoded record in memory as atomic_write_json would do.
        _replace_with_busy_retry(stage, path)
        try:
            sync_directory(path.parent)
        except OSError:
            # Callers owning an activation pointer must reconcile its actual
            # contents rather than treating a post-replace error as rollback.
            raise CommittedWriteError("storage_sync_unconfirmed") from None
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, LibraryError):
            raise
        raise LibraryError("metadata_invalid") from error
    finally:
        with suppress(OSError):
            stage.unlink(missing_ok=True)
