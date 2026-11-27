# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""In-process cache for small config files read on hot paths.

``get_core_config`` runs for nearly every model call (about 180 call sites),
and each run used to stat and read ``core_config.json`` from disk. On machines
with antivirus scanning or a cloud-synced Documents folder that adds up. This
cache keeps the text of each file in memory:

* Writers in this process call :func:`invalidate` after writing, so the next
  read sees the new content at once (``save_json_config`` does this for every
  JSON config file).
* Changes that bypass those writers (a hand edit, a cloud-save restore copying
  the whole directory, another process) are picked up by a lazy check: a read
  more than ``RECHECK_SECONDS`` after the last check stats the file once, and
  re-reads it only when its modification time, size or file id changed. The
  check runs only when someone reads; there is no background timer, so disk
  access never exceeds what the uncached reads did.

Callers get the raw text and parse it themselves, so nobody can mutate a shared
object. The cache can be switched off with ``NEKO_CONFIG_READ_CACHE=0`` (the
test suite does this, because its helpers rewrite config files directly).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

RECHECK_SECONDS = 2.0
_ENV_SWITCH = "NEKO_CONFIG_READ_CACHE"


@dataclass
class _Entry:
    text: str | None
    signature: tuple[int, int, int] | None
    checked_at: float


_lock = threading.Lock()
_entries: dict[str, _Entry] = {}
_generation = 0


def enabled() -> bool:
    """Whether reads go through the cache (``NEKO_CONFIG_READ_CACHE=0`` turns it off)."""
    return os.environ.get(_ENV_SWITCH, "1").strip() != "0"


def _key(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _signature(path: str) -> tuple[int, int, int] | None:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return None
    return (stat.st_mtime_ns, stat.st_size, getattr(stat, "st_ino", 0))


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def read_text(path: str | os.PathLike) -> str | None:
    """Return the file's text, or ``None`` when it does not exist.

    Other errors (permission, decoding) propagate like a plain read would, and
    are not cached.
    """
    key = _key(path)
    if not enabled():
        return _read(key)
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        generation = _generation
        if entry is not None and now - entry.checked_at < RECHECK_SECONDS:
            return entry.text
    signature = _signature(key)
    if entry is not None and signature == entry.signature:
        with _lock:
            if _generation == generation and _entries.get(key) is entry:
                entry.checked_at = now
        return entry.text
    text = _read(key) if signature is not None else None
    if text is None:
        signature = None
    with _lock:
        # An invalidation that raced this read wins: the text read here may
        # predate that write, so it is returned but not kept.
        if _generation == generation:
            _entries[key] = _Entry(text, signature, now)
    return text


def invalidate(path: str | os.PathLike | None = None) -> None:
    """Forget the cached text of ``path`` (every file when ``None``)."""
    global _generation
    with _lock:
        _generation += 1
        if path is None:
            _entries.clear()
        else:
            _entries.pop(_key(path), None)
