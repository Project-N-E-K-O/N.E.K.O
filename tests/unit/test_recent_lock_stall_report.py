"""Stall reports and bounded waits for the recent-file and settle locks (issue #3601)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from filelock import FileLock

from tests.fake_clock import patch_module_clock
from utils import recent_file, stall_report


@pytest.fixture
def isolated_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(recent_file.tempfile, 'gettempdir', lambda: str(tmp_path / 'locks'))
    return tmp_path


@pytest.fixture
def captured_reports(monkeypatch):
    reports = []
    monkeypatch.setattr(
        stall_report, 'report_stall',
        lambda message, **kwargs: reports.append((message, kwargs)),
    )
    return reports


@pytest.fixture
def fresh_stall_budget(monkeypatch):
    monkeypatch.setattr(stall_report, '_last_line_at', None)
    monkeypatch.setattr(stall_report, '_last_dump_at', None)
    monkeypatch.setattr(stall_report, '_dump_count', 0)
    monkeypatch.setattr(stall_report, '_suppressed_lines', 0)


def _hold_in_thread(lock, name):
    acquired, release = threading.Event(), threading.Event()

    def _holder():
        with lock:
            acquired.set()
            release.wait(10)

    thread = threading.Thread(target=_holder, name=name)
    thread.start()
    assert acquired.wait(5)
    return thread, release


@pytest.mark.unit
def test_timed_access_gives_up_without_keeping_the_thread_lock(isolated_locks, captured_reports):
    path = isolated_locks / 'Role' / 'recent.json'
    lock = recent_file.recent_file_lock(path)
    holder, release = _hold_in_thread(lock, 'slow-holder')
    try:
        started = time.monotonic()
        with pytest.raises(recent_file.RecentFileLockTimeout):
            with recent_file.recent_file_access(path, timeout=0.2):
                pytest.fail('entered a lock another thread holds')
        assert time.monotonic() - started < 2
    finally:
        release.set()
        holder.join(5)
    # The timed-out waiter must not have left either layer held.
    assert lock.acquire(timeout=1)
    lock.release()
    assert len(captured_reports) == 1
    message, kwargs = captured_reports[0]
    assert '放弃等待' in message and 'slow-holder' in message
    assert kwargs['holder_thread_id'] == holder.ident


@pytest.mark.unit
def test_untimed_wait_reports_the_holder_then_keeps_waiting(isolated_locks, captured_reports, monkeypatch):
    monkeypatch.setattr(recent_file, '_STALL_REPORT_AFTER_SECONDS', 0.05)
    path = isolated_locks / 'Role' / 'recent.json'
    path.parent.mkdir(parents=True)
    path.write_text('[]', encoding='utf-8')
    lock = recent_file.recent_file_lock(path)
    holder, release = _hold_in_thread(lock, 'slow-holder')
    entered = threading.Event()

    def _waiter():
        with recent_file.recent_file_access(path):
            entered.set()

    waiter = threading.Thread(target=_waiter)
    waiter.start()
    try:
        deadline = time.monotonic() + 5
        while not captured_reports and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not entered.is_set()
    finally:
        release.set()
        holder.join(5)
    waiter.join(5)
    assert entered.is_set()
    message, kwargs = captured_reports[0]
    assert '仍在等待' in message and 'slow-holder' in message
    assert kwargs['holder_thread_id'] == holder.ident


@pytest.mark.unit
def test_file_lock_held_elsewhere_is_reported_as_another_process(isolated_locks, captured_reports):
    path = isolated_locks / 'Role' / 'recent.json'
    lock = recent_file.recent_file_lock(path)
    with lock:
        lock_file = lock._file.lock_file
    # A second handle on the lock file stands in for another process.
    foreign = FileLock(lock_file, thread_local=False)
    foreign.acquire()
    try:
        assert not lock.acquire(timeout=0.2)
        assert not lock.locked()
        assert lock._holder is None
    finally:
        foreign.release()
    message, kwargs = captured_reports[0]
    assert '跨进程' in message
    assert 'holder_thread_id' not in kwargs


@pytest.mark.unit
def test_thread_still_waiting_on_the_file_lock_is_named_as_holder(isolated_locks, captured_reports):
    path = isolated_locks / 'Role' / 'recent.json'
    lock = recent_file.recent_file_lock(path)
    with lock:
        lock_file = lock._file.lock_file
    foreign = FileLock(lock_file, thread_local=False)
    foreign.acquire()
    got = []

    def _first_waiter():
        got.append(lock.acquire(timeout=5))
        lock.release()

    first = threading.Thread(target=_first_waiter, name='file-stage-waiter')
    first.start()
    try:
        deadline = time.monotonic() + 5
        while not lock.locked() and time.monotonic() < deadline:
            time.sleep(0.01)
        # The first waiter holds the thread lock and is parked on the other "process".
        assert not lock.acquire(timeout=0.2)
    finally:
        foreign.release()
    first.join(5)
    assert got == [True]
    message, kwargs = next(
        (m, k) for m, k in captured_reports if '进程内' in m
    )
    assert 'file-stage-waiter' in message
    assert kwargs['holder_thread_id'] == first.ident


@pytest.mark.unit
def test_read_queued_behind_a_full_executor_times_out_and_reports(tmp_path, isolated_locks, captured_reports):
    from concurrent.futures import ThreadPoolExecutor

    from tests.unit.test_recent_file_lock import _make_manager

    mgr, name, _path = _make_manager(tmp_path)
    release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)

    async def _run():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(executor)
        busy = loop.run_in_executor(None, release.wait, 10)
        started = time.monotonic()
        try:
            with pytest.raises(recent_file.RecentFileLockTimeout):
                await mgr.aget_recent_history(name, lock_timeout=0.2)
            return time.monotonic() - started
        finally:
            release.set()
            await busy

    try:
        elapsed = asyncio.run(_run())
    finally:
        executor.shutdown(wait=True)
    assert elapsed < 2
    message, kwargs = captured_reports[0]
    assert '线程池排队' in message and name in message
    assert kwargs['loop'] is not None


@pytest.mark.unit
def test_stall_report_limits_lines_and_dumps(fresh_stall_budget, monkeypatch):
    logged = []
    monkeypatch.setattr(
        stall_report.logger, 'warning',
        lambda fmt, *args: logged.append(fmt % args),
    )
    clock = [1000.0]
    patch_module_clock(monkeypatch, stall_report, monotonic=lambda: clock[0])

    stall_report.report_stall('first', holder_thread_id=threading.get_ident())
    stall_report.report_stall('suppressed')
    clock[0] += stall_report._LINE_MIN_INTERVAL_SECONDS
    stall_report.report_stall('second')

    assert len(logged) == 2
    assert 'stack dump 1/' in logged[0] and '[HOLDER, REPORTER]' in logged[0]
    # Within the dump interval: one line, no stacks, and the suppressed wait is counted.
    assert 'second' in logged[1] and '另有 1 次' in logged[1]
    assert 'stack dump' not in logged[1]

    for _ in range(stall_report._MAX_DUMPS_PER_PROCESS + 3):
        clock[0] += stall_report._DUMP_MIN_INTERVAL_SECONDS
        stall_report.report_stall('later')
    dumps = [line for line in logged if 'stack dump' in line]
    assert len(dumps) == stall_report._MAX_DUMPS_PER_PROCESS
    assert all(len(line) < stall_report._MAX_DUMP_CHARS + 1000 for line in logged)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stall_report_lists_event_loop_tasks(fresh_stall_budget, monkeypatch):
    logged = []
    monkeypatch.setattr(
        stall_report.logger, 'warning',
        lambda fmt, *args: logged.append(fmt % args),
    )
    blocker = asyncio.Event()

    async def inner_wait_under_lock():
        await blocker.wait()

    async def parked_holder():
        await inner_wait_under_lock()

    task = asyncio.create_task(parked_holder(), name='parked-holder')
    await asyncio.sleep(0)
    stall_report.report_stall('settle', loop=asyncio.get_running_loop())
    blocker.set()
    await task
    assert '--- task parked-holder' in logged[0]
    # The whole await chain, not just the task's outermost frame.
    section = logged[0].split('--- task parked-holder', 1)[1]
    assert section.index('parked_holder') < section.index('inner_wait_under_lock') < section.index('in wait')
    assert 'wait_for=<Future pending' in section.split('--- task', 1)[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_settle_wait_reports_once_without_changing_who_gets_the_lock(captured_reports, monkeypatch):
    from app.memory_server import runtime

    monkeypatch.setattr(runtime, '_SETTLE_STALL_REPORT_AFTER_SECONDS', 0.05)
    lock = asyncio.Lock()
    monkeypatch.setattr(runtime, '_get_settle_lock', lambda _name: lock)
    order = []

    async def waiter():
        async with runtime._hold_settle_lock_reporting_stalls('Neko', '/new_dialog'):
            order.append('waiter')

    await lock.acquire()
    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.15)
    assert not order
    lock.release()
    await task
    assert order == ['waiter']
    assert not lock.locked()
    assert len(captured_reports) == 1
    message, kwargs = captured_reports[0]
    assert '/new_dialog' in message and 'Neko' in message
    assert kwargs['loop'] is asyncio.get_running_loop()

    # A free lock is taken without any report, and errors still release it.
    with pytest.raises(RuntimeError):
        async with runtime._hold_settle_lock_reporting_stalls('Neko', '/new_dialog'):
            raise RuntimeError('boom')
    await asyncio.sleep(0.1)
    assert not lock.locked()
    assert len(captured_reports) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_returns_503_when_the_lock_wait_times_out():
    from app import memory_server

    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(
            side_effect=recent_file.RecentFileLockTimeout('busy'),
        ),
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.routes.logger, "warning") as warning:
        response = await memory_server.get_recent_history("test_char")

    assert response.status_code == 503
    # The lock's rate-limited stall report is the only log line; no per-request warning.
    warning.assert_not_called()
    assert json.loads(response.body)["status"] == "error"
    kwargs = fake_recent.aget_recent_history.await_args.kwargs
    assert 0 < kwargs["lock_timeout"] < 5
