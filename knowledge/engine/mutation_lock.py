"""Process-local serialization for knowledge JSON mutations."""

from __future__ import annotations

import os
import threading
from pathlib import Path


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def mutation_lock(path: str | Path):
    """Return the shared re-entrant lock for one normalized local path."""
    key = os.path.normcase(str(Path(path).resolve()))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock
