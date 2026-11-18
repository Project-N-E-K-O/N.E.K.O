"""Cached FileLocks must stay usable in fork children (filelock 4 rejects inherited instances).

On Linux the launcher forks the servers through ``multiprocessing``. A startup
cloudsave import can fill the recent-file lock cache first, and chat-sync writers
are module-level singletons, so both caches reach the children. Each case checks
the same three things for ``utils.recent_file`` and ``services.chat_sync.writer``:
a cached lock works in the child, the child still respects a lock the parent holds,
and the parent's lock is untouched afterwards.

The parent holds the file through a separate FileLock rather than the cached
object: a cached object held at fork time would also carry its in-process gate
into the child locked, which would block the child before the file stage.
"""

from __future__ import annotations

import multiprocessing
import sys

import pytest
from filelock import FileLock

from services.chat_sync import writer as chat_sync_writer
from utils import recent_file

pytestmark = pytest.mark.skipif(
    sys.platform == 'win32' or 'fork' not in multiprocessing.get_all_start_methods(),
    reason='fork start method is unavailable; spawn children rebuild their caches on import',
)

_JOIN_TIMEOUT = 30


def _run_in_fork_child(target, *args) -> int:
    process = multiprocessing.get_context('fork').Process(target=target, args=args)
    process.start()
    process.join(_JOIN_TIMEOUT)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail('fork child hung')
    return process.exitcode


# ── recent_file ─────────────────────────────────────────────────────────────


def _recent_child_uses_cached_lock(path):
    lock = recent_file.recent_file_lock(path)
    if not lock.acquire(timeout=5):
        raise SystemExit(2)
    lock.release()
    with recent_file.recent_file_access(path, timeout=5):
        pass


def _recent_child_respects_parent_hold(path):
    lock = recent_file.recent_file_lock(path)
    if lock.acquire(timeout=0.2):
        lock.release()
        raise SystemExit(3)


def test_recent_file_cached_lock_works_after_fork(tmp_path):
    path = tmp_path / 'Role' / 'recent.json'
    with recent_file.recent_file_access(path):
        pass

    assert _run_in_fork_child(_recent_child_uses_cached_lock, path) == 0
    with recent_file.recent_file_access(path, timeout=5):
        pass


def _recent_child_uses_lock_object(lock):
    if not lock.acquire(timeout=5):
        raise SystemExit(2)
    lock.release()


def test_recent_file_lock_outside_cache_works_after_fork(tmp_path, monkeypatch):
    """A lock a caller still holds after it left the cache is rebuilt too."""
    lock = recent_file.recent_file_lock(tmp_path / 'Role' / 'recent.json')
    with lock:
        pass
    monkeypatch.setattr(recent_file, '_LOCKS', {})

    assert _run_in_fork_child(_recent_child_uses_lock_object, lock) == 0
    with lock:
        pass


def test_recent_file_fork_child_respects_parent_hold(tmp_path):
    path = tmp_path / 'Role' / 'recent.json'
    lock = recent_file.recent_file_lock(path)
    with lock:
        pass
    foreign = FileLock(lock._file_path, thread_local=False)
    with foreign:
        assert _run_in_fork_child(_recent_child_respects_parent_hold, path) == 0
    assert lock.acquire(timeout=5)
    lock.release()


# ── chat_sync writer ────────────────────────────────────────────────────────


def _writer_child_uses_cached_lock(directory):
    with chat_sync_writer.registered_writer(directory).locked():
        pass


def _writer_child_respects_parent_hold(directory):
    writer = chat_sync_writer.registered_writer(directory)
    writer._file_lock.timeout = 0.2
    try:
        with writer.locked():
            raise SystemExit(3)
    except chat_sync_writer.WriterQuarantined:
        pass


def _new_writer(tmp_path):
    directory = tmp_path / 'sync'
    return directory, chat_sync_writer.ChatSyncWriter(directory, tmp_path / 'installation' / 'writer.json')


def test_chat_sync_writer_cached_lock_works_after_fork(tmp_path):
    directory, writer = _new_writer(tmp_path)
    with writer.locked():
        pass

    assert _run_in_fork_child(_writer_child_uses_cached_lock, directory) == 0
    with writer.locked():
        pass


def _writer_child_uses_writer_object(writer):
    with writer.locked():
        pass


def test_chat_sync_writer_replaced_in_registry_works_after_fork(tmp_path):
    """A runtime may still hold a writer a newer one replaced in the registry."""
    directory, replaced = _new_writer(tmp_path)
    with replaced.locked():
        pass
    _, current = _new_writer(tmp_path)
    assert chat_sync_writer.registered_writer(directory) is current

    assert _run_in_fork_child(_writer_child_uses_writer_object, replaced) == 0
    with replaced.locked():
        pass


def test_chat_sync_writer_fork_child_respects_parent_hold(tmp_path):
    directory, writer = _new_writer(tmp_path)
    with writer.locked():
        pass
    foreign = FileLock(writer._file_lock.lock_file)
    with foreign:
        assert _run_in_fork_child(_writer_child_respects_parent_hold, directory) == 0
    with writer.locked():
        pass
