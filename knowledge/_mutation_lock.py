"""Thread- and process-safe serialization for knowledge mutations."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import portalocker


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, "_MutationLock"] = {}
MUTATION_LOCK_TIMEOUT_SECONDS = 30.0


class _MutationLock:
    def __init__(self, target: Path) -> None:
        self._thread_lock = threading.RLock()
        self._local = threading.local()
        self._lock_path = target.with_name(f".{target.name}.mutation.lock")

    def __enter__(self):
        self._thread_lock.acquire()
        depth = int(getattr(self._local, "depth", 0))
        try:
            if depth == 0:
                self._lock_path.parent.mkdir(parents=True, exist_ok=True)
                file_lock = portalocker.Lock(
                    self._lock_path,
                    mode="a",
                    timeout=MUTATION_LOCK_TIMEOUT_SECONDS,
                )
                file_lock.acquire()
                self._local.file_lock = file_lock
            self._local.depth = depth + 1
            return self
        except Exception:
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        depth = int(getattr(self._local, "depth", 1)) - 1
        try:
            if depth == 0:
                file_lock = self._local.file_lock
                file_lock.release()
                del self._local.file_lock
                del self._local.depth
            else:
                self._local.depth = depth
        finally:
            self._thread_lock.release()


def mutation_lock(path: str | Path):
    """Return the shared re-entrant lock for one normalized local path."""
    key = os.path.normcase(str(Path(path).resolve()))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _MutationLock(Path(key))
            _LOCKS[key] = lock
        return lock
