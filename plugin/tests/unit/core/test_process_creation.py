"""Process creation coordination, deadlines and child-side lock ownership."""
from __future__ import annotations

import multiprocessing
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from utils import process_creation


@pytest.fixture
def occupied_creation_window():
    entered = threading.Event()
    release = threading.Event()

    def blocked_start():
        entered.set()
        if not release.wait(5):
            raise AssertionError("test did not release creation window")

    thread = threading.Thread(
        target=lambda: process_creation.start(SimpleNamespace(start=blocked_start)), daemon=True,
    )
    thread.start()
    assert entered.wait(3)
    try:
        yield release
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()


def test_waiting_creation_spends_its_deadline_without_starting_a_process(occupied_creation_window):
    launched = []
    with pytest.raises(process_creation.ProcessCreationTimeout):
        process_creation.start(
            SimpleNamespace(start=lambda: launched.append(True)), deadline=time.monotonic() + 0.03,
        )
    assert launched == []


def test_cancelled_waiter_never_starts_later(occupied_creation_window):
    cancelled = threading.Event()
    attempted = threading.Event()
    retired = threading.Event()
    launched = []

    def wait():
        attempted.set()
        try:
            process_creation.start(SimpleNamespace(start=lambda: launched.append(True)), cancel_event=cancelled)
        except process_creation.ProcessCreationCancelled:
            retired.set()

    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    assert attempted.wait(3)
    cancelled.set()
    try:
        assert retired.wait(3), "cancellation must finish while the creator is still blocked"
    finally:
        occupied_creation_window.set()
        thread.join(3)
    assert not thread.is_alive()
    assert launched == []


def test_popen_rejects_expired_or_cancelled_requests_before_allocating(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a retired request created a subprocess")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(process_creation.ProcessCreationCancelled):
        process_creation.popen([sys.executable], cancel_event=cancelled)
    with pytest.raises(process_creation.ProcessCreationTimeout):
        process_creation.popen([sys.executable], deadline=time.monotonic() - 1)


def test_creation_exception_preserves_error_and_releases_the_window():
    error = OSError("creation failed")

    def fail():
        raise error

    with pytest.raises(OSError) as caught:
        process_creation.start(SimpleNamespace(start=fail))
    assert caught.value is error
    created = []
    process_creation.start(SimpleNamespace(start=lambda: created.append(True)), deadline=time.monotonic() + 1)
    assert created == [True]


def test_running_subprocess_does_not_own_the_creation_window():
    process = process_creation.popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        created = []
        process_creation.start(SimpleNamespace(start=lambda: created.append(True)), deadline=time.monotonic() + 1)
        assert created == [True]
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=3)


def _create_again_in_child(connection):
    try:
        process = process_creation.popen([sys.executable, "-c", "pass"], deadline=time.monotonic() + 1)
        connection.send(process.wait(timeout=3))
    finally:
        connection.close()


@pytest.mark.parametrize("method", ["fork", "spawn", "forkserver"])
def test_child_can_create_its_own_subprocess(method):
    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"{method} is unavailable on this platform")
    context = multiprocessing.get_context(method)
    receive, send = context.Pipe(duplex=False)
    child = context.Process(target=_create_again_in_child, args=(send,))
    try:
        process_creation.start(child)
        send.close()
        assert receive.poll(5), "child inherited a locked creation window"
        assert receive.recv() == 0
        child.join(3)
        assert child.exitcode == 0
    finally:
        send.close()
        receive.close()
        if child.pid is not None:
            if child.is_alive():
                child.kill()
            child.join(3)
            child.close()
