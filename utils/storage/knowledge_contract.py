"""On-disk contract shared by storage-root migration and the knowledge package.

Root migration (``utils.storage.migration``) must fence off knowledge writers
and refuse to copy a knowledge database this build cannot read. Both rules are
properties of the files on disk, not of the knowledge runtime, so they live
here in the storage layer: ``knowledge`` builds on them, and migration never
has to import upward into ``knowledge``.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import portalocker


KNOWLEDGE_SCHEMA_VERSION = 7
ROOT_BARRIER_TIMEOUT_SECONDS = 30.0

_BARRIERS_LOCK = threading.Lock()
_BARRIERS: dict[str, threading.RLock] = {}


class KnowledgeStoreError(RuntimeError):
    pass


class KnowledgeSchemaTooNewError(KnowledgeStoreError):
    def __init__(self, detected_version: int) -> None:
        self.detected_version = int(detected_version)
        self.supported_version = KNOWLEDGE_SCHEMA_VERSION
        super().__init__(
            "knowledge database schema is newer than this application supports"
        )


def assert_supported_schema(connection: sqlite3.Connection) -> None:
    """Apply the production schema-marker contract without modifying SQLite."""
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    metadata_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
    ).fetchone()
    metadata_version: int | None = None
    if metadata_table is not None:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        if row is not None:
            raw = str(row[0])
            if not raw.isdecimal() or str(int(raw)) != raw or int(raw) <= 0:
                raise KnowledgeStoreError(
                    "knowledge database schema version is invalid"
                )
            metadata_version = int(raw)

    detected_versions = tuple(
        version
        for version in (user_version, metadata_version)
        if version not in (None, 0)
    )
    too_new = tuple(
        version for version in detected_versions if version > KNOWLEDGE_SCHEMA_VERSION
    )
    if too_new:
        raise KnowledgeSchemaTooNewError(max(too_new))
    if len(set(detected_versions)) > 1:
        raise KnowledgeStoreError("knowledge database schema markers disagree")
    if user_version and metadata_version is None:
        raise KnowledgeStoreError("knowledge database schema metadata is missing")


def _canonical_root_key(knowledge_root: str | Path) -> str:
    root = Path(knowledge_root).expanduser().resolve(strict=False)
    return os.path.normcase(os.path.abspath(str(root)))


def _root_lock_path(knowledge_root: str | Path) -> Path:
    root = Path(_canonical_root_key(knowledge_root))
    root_id = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    return root.parent / "state" / "knowledge-root-locks" / f"{root_id}.lock"


@contextmanager
def knowledge_root_barrier(
    knowledge_root: str | Path,
    *,
    timeout: float = ROOT_BARRIER_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Hold the stable lock that serializes writers with root migration.

    The lock lives beside, rather than inside, ``knowledge/`` so moving or
    replacing the knowledge directory cannot silently replace the lock.
    """

    key = _canonical_root_key(knowledge_root)
    with _BARRIERS_LOCK:
        thread_lock = _BARRIERS.setdefault(key, threading.RLock())
    lock_path = _root_lock_path(key)
    # One deadline for both halves: callers map a timeout to a retryable
    # "busy", which only works if a same-process holder (a migration copying a
    # large knowledge/ tree) cannot make the in-process wait unbounded.
    budget = max(float(timeout), 0.0)
    deadline = time.monotonic() + budget
    acquired = (
        thread_lock.acquire(blocking=True, timeout=budget)
        if budget > 0
        else thread_lock.acquire(blocking=False)
    )
    if not acquired:
        raise TimeoutError("knowledge root barrier is held by another thread")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(
            lock_path,
            mode="a",
            timeout=max(deadline - time.monotonic(), 0.0),
        ):
            yield
    finally:
        thread_lock.release()
