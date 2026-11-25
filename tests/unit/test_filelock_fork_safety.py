"""Cached FileLocks must stay usable in fork children (filelock 4 rejects inherited instances).

On Linux the launcher forks the servers through ``multiprocessing``. A startup
cloudsave import can fill the recent-file lock cache first, and chat-sync writers
are module-level singletons, so both caches reach the children. The cases are
mirrored for ``utils.recent_file`` and ``services.chat_sync.writer``:

- a cached lock, or one still referenced outside the cache, works in the child,
  and the parent's lock is untouched afterwards;
- the child still respects a file lock the parent holds;
- an in-process gate another parent thread held at fork time is fresh in the
  child, while one the forking thread holds is kept;
- module-level guards another parent thread held at fork time are fresh in the child.
"""

from __future__ import annotations

import multiprocessing
import sys
import threading

import pytest
from filelock import FileLock

from services.chat_sync import writer as chat_sync_writer
from utils import recent_file

pytestmark = pytest.mark.skipif(
    sys.platform == 'win32' or 'fork' not in multiprocessing.get_all_start_methods(),
    reason='fork start method is unavailable; spawn children rebuild their caches on import',
)

_JOIN_TIMEOUT = 30


class _UnprintableError(Exception):
    def __str__(self):
        raise ValueError('no message')


def _raise(exc):
    def reset():
        raise exc
    return reset


# (exception raised by one object's rebuild, summary the hook must record)
_REBUILD_FAILURES = [
    pytest.param(RuntimeError('rebuild broke'), ('RuntimeError', 'rebuild broke'), id='plain'),
    pytest.param(_UnprintableError(), ('_UnprintableError', '<unprintable exception>'), id='unprintable'),
]


@pytest.fixture(autouse=True)
def _isolated_caches(monkeypatch):
    """Keep this module's locks and writers out of the process-wide caches."""
    monkeypatch.setattr(recent_file, '_LOCKS', {})
    monkeypatch.setattr(chat_sync_writer, '_registry', {})


def _run_in_fork_child(target, *args, timeout=_JOIN_TIMEOUT) -> int:
    process = multiprocessing.get_context('fork').Process(target=target, args=args)
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail('fork child hung')
    return process.exitcode


def _hold_in_other_thread(enter, exit_):
    """Hold a lock from a second parent thread; release it when the returned event is set."""
    held, release = threading.Event(), threading.Event()

    def holder():
        enter()
        held.set()
        release.wait(_JOIN_TIMEOUT)
        exit_()

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert held.wait(_JOIN_TIMEOUT)
    return thread, release


def _release_soon(release):
    """Let the parent's holder go shortly after the child starts waiting."""
    timer = threading.Timer(0.5, release.set)
    timer.start()
    return timer


# ── recent_file ─────────────────────────────────────────────────────────────


def _recent_child_uses_cached_lock(path):
    lock = recent_file.recent_file_lock(path)
    if not lock.acquire(timeout=5):
        raise SystemExit(2)
    lock.release()
    with recent_file.recent_file_access(path, timeout=5):
        pass


def _recent_child_uses_lock_object(lock):
    if not lock.acquire(timeout=5):
        raise SystemExit(2)
    lock.release()


def _recent_child_respects_parent_hold(path):
    lock = recent_file.recent_file_lock(path)
    if lock.acquire(blocking=False):
        lock.release()
        raise SystemExit(3)


def _recent_child_keeps_forking_thread_gate(lock):
    if not lock.locked():
        raise SystemExit(4)


def test_recent_file_cached_lock_works_after_fork(tmp_path):
    path = tmp_path / 'Role' / 'recent.json'
    with recent_file.recent_file_access(path):
        pass

    assert _run_in_fork_child(_recent_child_uses_cached_lock, path) == 0
    with recent_file.recent_file_access(path, timeout=5):
        pass


def test_recent_file_lock_outside_cache_works_after_fork(tmp_path, monkeypatch):
    """A lock a caller still holds after it left the cache is rebuilt too."""
    lock = recent_file.recent_file_lock(tmp_path / 'Role' / 'recent.json')
    with lock:
        pass
    monkeypatch.setattr(recent_file, '_LOCKS', {})

    assert _run_in_fork_child(_recent_child_uses_lock_object, lock) == 0
    with lock:
        pass


def _recent_child_uses_lock_object_after_one_failure(lock, expected):
    if recent_file._FORK_RESET_FAILURES != [expected]:
        raise SystemExit(10)
    _recent_child_uses_lock_object(lock)


@pytest.mark.parametrize(('failure', 'expected'), _REBUILD_FAILURES)
def test_recent_file_one_failed_rebuild_does_not_skip_the_rest(tmp_path, monkeypatch, failure, expected):
    broken = recent_file.recent_file_lock(tmp_path / 'Broken' / 'recent.json')
    lock = recent_file.recent_file_lock(tmp_path / 'Role' / 'recent.json')
    monkeypatch.setattr(broken, '_reset_after_fork', _raise(failure))
    # Ordered so the failing entry comes first; a WeakSet's order is arbitrary.
    monkeypatch.setattr(recent_file, '_FORK_RESET_LOCKS', [broken, lock])

    assert _run_in_fork_child(_recent_child_uses_lock_object_after_one_failure, lock, expected) == 0


def _recent_child_gate_is_fresh(lock):
    if lock._thread.locked() or lock._holder is not None:
        raise SystemExit(9)


def _fail_new_file_lock():
    raise OSError('cannot build lock')


def test_recent_file_failed_file_lock_rebuild_still_refreshes_gate(tmp_path, monkeypatch):
    lock = recent_file.recent_file_lock(tmp_path / 'Role' / 'recent.json')
    thread, release = _hold_in_other_thread(lock.acquire, lock.release)
    monkeypatch.setattr(lock, '_new_file_lock', _fail_new_file_lock)
    try:
        assert _run_in_fork_child(_recent_child_gate_is_fresh, lock) == 0
    finally:
        release.set()
        thread.join(_JOIN_TIMEOUT)


def _hold_guards_in_other_thread(guards):
    def enter():
        for guard in guards:
            guard.acquire()

    def exit_():
        for guard in reversed(guards):
            guard.release()

    return _hold_in_other_thread(enter, exit_)


def _recent_child_passes_module_guards(path):
    recent_file.recent_file_lock(path)
    recent_file.get_recent_pending_unlocked(path)


def test_recent_file_module_guards_held_by_other_thread_are_fresh_in_child(tmp_path):
    """A guard another parent thread held at fork must not hang the child's first lookup."""
    path = tmp_path / 'Role' / 'recent.json'
    thread, release = _hold_guards_in_other_thread([recent_file._LOCKS_GUARD, recent_file._PENDING_GUARD])
    try:
        assert _run_in_fork_child(_recent_child_passes_module_guards, path, timeout=10) == 0
    finally:
        release.set()
        thread.join(_JOIN_TIMEOUT)


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


def test_recent_file_gate_held_by_other_thread_is_fresh_in_child(tmp_path):
    """The child waits only for the parent's file lock, not for a thread it never had."""
    lock = recent_file.recent_file_lock(tmp_path / 'Role' / 'recent.json')
    thread, release = _hold_in_other_thread(lock.acquire, lock.release)
    timer = _release_soon(release)
    try:
        assert _run_in_fork_child(_recent_child_uses_lock_object, lock) == 0
    finally:
        release.set()
        timer.cancel()
        thread.join(_JOIN_TIMEOUT)
    with lock:
        pass


def test_recent_file_gate_held_by_forking_thread_is_kept(tmp_path):
    lock = recent_file.recent_file_lock(tmp_path / 'Role' / 'recent.json')
    with lock:
        assert _run_in_fork_child(_recent_child_keeps_forking_thread_gate, lock) == 0


# ── chat_sync writer ────────────────────────────────────────────────────────


def _new_writer(tmp_path):
    directory = tmp_path / 'sync'
    return directory, chat_sync_writer.ChatSyncWriter(directory, tmp_path / 'installation' / 'writer.json')


def _writer_child_uses_cached_lock(directory):
    with chat_sync_writer.registered_writer(directory).locked():
        pass


def _writer_child_uses_writer_object(writer):
    with writer.locked():
        pass


def _writer_child_respects_parent_hold(directory):
    writer = chat_sync_writer.registered_writer(directory)
    writer._file_lock.timeout = 0
    try:
        with writer.locked():
            raise SystemExit(3)
    except chat_sync_writer.WriterQuarantined:
        pass


def _writer_child_keeps_forking_thread_mutex(writer):
    if not writer._mutex._is_owned():
        raise SystemExit(4)


def test_chat_sync_writer_cached_lock_works_after_fork(tmp_path):
    directory, writer = _new_writer(tmp_path)
    with writer.locked():
        pass

    assert _run_in_fork_child(_writer_child_uses_cached_lock, directory) == 0
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


def _writer_child_uses_writer_object_after_one_failure(writer, expected):
    if chat_sync_writer._FORK_RESET_FAILURES != [expected]:
        raise SystemExit(10)
    _writer_child_uses_writer_object(writer)


@pytest.mark.parametrize(('failure', 'expected'), _REBUILD_FAILURES)
def test_chat_sync_writer_one_failed_rebuild_does_not_skip_the_rest(tmp_path, monkeypatch, failure, expected):
    _, broken = _new_writer(tmp_path / 'broken')
    _, writer = _new_writer(tmp_path)
    monkeypatch.setattr(broken, '_reset_after_fork', _raise(failure))
    # Ordered so the failing entry comes first; a WeakSet's order is arbitrary.
    monkeypatch.setattr(chat_sync_writer, '_FORK_RESET_WRITERS', [broken, writer])

    assert _run_in_fork_child(_writer_child_uses_writer_object_after_one_failure, writer, expected) == 0


def test_chat_sync_writer_fork_child_respects_parent_hold(tmp_path):
    directory, writer = _new_writer(tmp_path)
    with writer.locked():
        pass
    foreign = FileLock(writer._file_lock.lock_file)
    with foreign:
        assert _run_in_fork_child(_writer_child_respects_parent_hold, directory) == 0
    with writer.locked():
        pass


def test_chat_sync_writer_mutex_held_by_other_thread_is_fresh_in_child(tmp_path):
    """The child waits only for the parent's file lock, not for a thread it never had."""
    _, writer = _new_writer(tmp_path)
    context = writer.locked()
    thread, release = _hold_in_other_thread(context.__enter__, lambda: context.__exit__(None, None, None))
    timer = _release_soon(release)
    try:
        assert _run_in_fork_child(_writer_child_uses_writer_object, writer) == 0
    finally:
        release.set()
        timer.cancel()
        thread.join(_JOIN_TIMEOUT)
    with writer.locked():
        pass


def _writer_child_check_is_quarantined(writer, forked):
    forked.set()
    read = writer._read
    seen = []

    def recording_read():
        state = read()
        seen.append(state)
        return state

    writer._read = recording_read
    try:
        writer.check()
    except chat_sync_writer.WriterQuarantined:
        # A file-lock timeout raises the same error without reading the witness.
        if seen and seen[-1]['quarantined']:
            return
        raise SystemExit(6)
    raise SystemExit(5)


def test_chat_sync_writer_fork_during_other_thread_recover_stays_quarantined(tmp_path):
    """A recover() another thread was running at fork must not leave the child skipping checks."""
    _, writer = _new_writer(tmp_path)
    with writer.initialize():
        pass
    writer.check()
    staging = threading.Event()
    child_started = []
    forked = multiprocessing.get_context('fork').Event()

    def stage():
        staging.set()
        # Abort only after the child exists, so it inherits _initializing=True; the
        # witness stays quarantined and the child's check (waiting on the file lock) must refuse.
        child_started.append(forked.wait(_JOIN_TIMEOUT))
        raise RuntimeError('staging aborted')

    def run_recover():
        with pytest.raises(RuntimeError):
            writer.recover(stage)

    thread = threading.Thread(target=run_recover, daemon=True)
    thread.start()
    assert staging.wait(_JOIN_TIMEOUT)
    try:
        assert _run_in_fork_child(_writer_child_check_is_quarantined, writer, forked) == 0
    finally:
        forked.set()
        thread.join(_JOIN_TIMEOUT)
    # A timed-out wait would abort recovery before the fork and let the child pass vacuously.
    assert child_started == [True]


def _writer_child_does_not_inherit_trust(writer):
    if writer.local_visibility_trusted():
        raise SystemExit(7)
    writer.check()
    if not writer.local_visibility_trusted():
        raise SystemExit(8)


def test_chat_sync_writer_child_does_not_inherit_verified_heads(tmp_path):
    _, writer = _new_writer(tmp_path)
    with writer.initialize():
        pass
    writer.check()
    assert writer.local_visibility_trusted()

    assert _run_in_fork_child(_writer_child_does_not_inherit_trust, writer) == 0
    assert writer.local_visibility_trusted()


def _writer_child_trust_is_dropped(writer):
    if writer._trusted_heads is not None:
        raise SystemExit(9)


def test_chat_sync_writer_failed_file_lock_rebuild_still_drops_trust(tmp_path, monkeypatch):
    _, writer = _new_writer(tmp_path)
    with writer.initialize():
        pass
    writer.check()
    monkeypatch.setattr(writer, '_new_file_lock', _fail_new_file_lock)

    assert _run_in_fork_child(_writer_child_trust_is_dropped, writer) == 0


def _writer_child_passes_module_guard(directory, witness_path):
    chat_sync_writer.registered_writer(directory)
    chat_sync_writer.ChatSyncWriter(directory, witness_path)


def test_chat_sync_writer_module_guard_held_by_other_thread_is_fresh_in_child(tmp_path):
    """A guard another parent thread held at fork must not hang the child's first lookup."""
    directory, witness_path = tmp_path / 'sync', tmp_path / 'installation' / 'writer.json'
    thread, release = _hold_guards_in_other_thread([chat_sync_writer._registry_lock])
    try:
        assert _run_in_fork_child(_writer_child_passes_module_guard, directory, witness_path, timeout=10) == 0
    finally:
        release.set()
        thread.join(_JOIN_TIMEOUT)


def test_chat_sync_writer_mutex_held_by_forking_thread_is_kept(tmp_path):
    _, writer = _new_writer(tmp_path)
    with writer._mutex:
        assert _run_in_fork_child(_writer_child_keeps_forking_thread_mutex, writer) == 0
