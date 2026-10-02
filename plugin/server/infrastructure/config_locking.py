from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.config_locking")


class LockableFile(Protocol):
    def fileno(self) -> int: ...

    def seek(self, offset: int, whence: int = 0) -> int: ...

    def tell(self) -> int: ...


_plugin_update_locks: dict[str, threading.RLock] = {}
_plugin_update_locks_guard = threading.Lock()

if sys.platform == "win32":
    try:
        import msvcrt as _msvcrt
    except ImportError:
        _msvcrt = None
    _fcntl = None
else:
    _msvcrt = None
    try:
        import fcntl as _fcntl
    except ImportError:
        _fcntl = None


def get_plugin_update_lock(plugin_id: str) -> threading.RLock:
    with _plugin_update_locks_guard:
        lock = _plugin_update_locks.get(plugin_id)
        if lock is None:
            # Runtime-config initialization can be reached from a resolver
            # that already owns this plugin's read/write transaction.  A
            # re-entrant lock keeps that nested synchronous path safe while
            # preserving one shared lock per plugin.
            lock = threading.RLock()
            _plugin_update_locks[plugin_id] = lock
        return lock


@contextmanager
def file_lock(file_obj: LockableFile):
    if _msvcrt is None and _fcntl is None:
        logger.warning(
            "File locking backend unavailable on current platform; write safety is reduced",
        )
        yield
        return

    if _msvcrt is not None:
        file_obj.seek(0, os.SEEK_END)
        size = file_obj.tell()
        file_obj.seek(0, os.SEEK_SET)
        lock_size = size if size > 0 else 1
        _msvcrt.locking(file_obj.fileno(), _msvcrt.LK_LOCK, lock_size)
        try:
            yield
        finally:
            file_obj.seek(0, os.SEEK_SET)
            _msvcrt.locking(file_obj.fileno(), _msvcrt.LK_UNLCK, lock_size)
        return

    if _fcntl is None:
        yield
        return

    _fcntl.flock(file_obj.fileno(), _fcntl.LOCK_EX)
    try:
        yield
    finally:
        _fcntl.flock(file_obj.fileno(), _fcntl.LOCK_UN)


@contextmanager
def plugin_config_file_lock(config_path: Path):
    """Coordinate config snapshots across the server and plugin processes."""
    lock_path = config_path.with_name(f"{config_path.name}.lock")
    # A mocked resolver path or a not-yet-created runtime directory cannot have
    # a shared lock file. The config itself is unavailable in that case, so let
    # the caller retain its existing error/fixture behavior.
    try:
        lock_file = lock_path.open("a+b")
    except FileNotFoundError:
        yield
        return
    with lock_file:
        with file_lock(lock_file):
            yield
