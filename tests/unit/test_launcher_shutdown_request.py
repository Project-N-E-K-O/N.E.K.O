# -*- coding: utf-8 -*-
"""The launcher's ordered shutdown must not hang on a child that cannot answer.

``multiprocessing.Event.set()`` wakes every thread sleeping in ``wait()`` and then
waits, with no timeout, for each of them to acknowledge. A server child that got
the same group-wide signal as the launcher may already be past the point where its
watcher thread can run; called inline, ``set()`` then blocks the launcher forever
although every server has exited.

These tests build that situation for real: a spawned child parks a daemon thread
in ``event.wait()`` and dies without waking it. Every call that would hang without
the fix runs on a daemon thread joined with a timeout, so a regression shows up as
a failed assertion instead of a stuck test run.
"""

import multiprocessing
import os
import threading
import time

import pytest

# Imported lazily inside the tests: the spawned child imports this module to find
# its target, and must not drag the launcher in with it.

#: Upper bound for anything that is supposed to return promptly.
_BOUNDED_SECONDS = 30.0


def _park_watcher_then_die(event, parked) -> None:
    """Child body: sleep on ``event`` from a daemon thread, then die without waking it."""
    entering = threading.Event()

    def _watch() -> None:
        entering.set()
        event.wait()

    threading.Thread(target=_watch, name="shutdown-watch", daemon=True).start()
    entering.wait()
    # The watcher registers itself as a sleeper a few instructions after
    # ``entering`` is set; give it ample time to get there.
    time.sleep(0.5)
    parked.set()
    # No interpreter teardown, no acknowledgement: the sleeper simply stops existing.
    os._exit(0)


@pytest.fixture
def event_with_dead_sleeper():
    """A multiprocessing Event whose only sleeper died without acknowledging."""
    ctx = multiprocessing.get_context("spawn")
    event = ctx.Event()
    parked = ctx.Event()
    child = ctx.Process(target=_park_watcher_then_die, args=(event, parked))
    child.start()
    try:
        assert parked.wait(timeout=60), "child never parked its watcher thread"
        child.join(timeout=30)
        assert not child.is_alive(), "child did not exit"
        yield event, child
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=10)


class _AliveUntilJoined:
    """What cleanup_servers sees while a child is still finalizing: alive, then gone."""

    pid = 43210

    def __init__(self) -> None:
        self.alive = True
        self.join_calls: list = []
        self.terminate_called = False

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout=None) -> None:
        self.join_calls.append(timeout)
        self.alive = False

    def terminate(self) -> None:
        self.terminate_called = True
        self.alive = False

    def kill(self) -> None:
        self.alive = False


class _CompletedEvent:
    def wait(self, timeout=None) -> bool:
        return True


def _run_bounded(fn):
    """Run ``fn`` on a daemon thread; return (finished, result, seconds)."""
    result: list = []

    def _call() -> None:
        result.append(fn())

    started = time.monotonic()
    worker = threading.Thread(target=_call, name="bounded-call", daemon=True)
    worker.start()
    worker.join(timeout=_BOUNDED_SECONDS)
    elapsed = time.monotonic() - started
    return (not worker.is_alive()), (result[0] if result else None), elapsed


@pytest.mark.unit
@pytest.mark.timeout(180)
def test_shutdown_request_returns_when_the_sleeper_never_acknowledges(event_with_dead_sleeper):
    """The child still counts as alive (it is finalizing) but will never answer."""
    from launcher_core import runtime as launcher

    event, _child = event_with_dead_sleeper
    proc = _AliveUntilJoined()

    finished, acknowledged, elapsed = _run_bounded(
        lambda: launcher._request_server_shutdown("Agent Server", proc, event, timeout=0.5)
    )

    assert finished, "the shutdown request hung on a sleeper that can no longer acknowledge"
    # False is also what proves the fixture built the hang: with no dead sleeper
    # registered, set() would simply have returned and this would be True.
    assert acknowledged is False
    assert elapsed < 10.0


@pytest.mark.unit
@pytest.mark.timeout(180)
def test_shutdown_request_stops_waiting_once_the_child_is_gone(event_with_dead_sleeper):
    """A dead child ends the wait at once; the budget is not sat out."""
    from launcher_core import runtime as launcher

    event, child = event_with_dead_sleeper

    finished, acknowledged, elapsed = _run_bounded(
        lambda: launcher._request_server_shutdown("Agent Server", child, event, timeout=_BOUNDED_SECONDS * 4)
    )

    assert finished
    assert acknowledged is False
    assert elapsed < 10.0


@pytest.mark.unit
@pytest.mark.timeout(180)
def test_cleanup_servers_finishes_when_a_child_died_sleeping_on_its_shutdown_event(
    event_with_dead_sleeper, monkeypatch
):
    """The whole ordered shutdown, with the real Event: it must reach the end."""
    from launcher_core import runtime as launcher

    event, _child = event_with_dead_sleeper
    stuck = _AliveUntilJoined()
    healthy = _AliveUntilJoined()
    healthy_event = multiprocessing.get_context("spawn").Event()

    monkeypatch.setattr(launcher, "SHUTDOWN_REQUEST_ACK_SECONDS", 0.5)
    monkeypatch.setattr(launcher, "_cleanup_done", False)
    monkeypatch.setattr(launcher, "_cleanup_complete", threading.Event())
    monkeypatch.setattr(launcher, "_teardown_snapshot_taken", True)
    monkeypatch.setattr(launcher, "JOB_HANDLE", None)
    monkeypatch.setattr(
        launcher,
        "SERVERS",
        [
            {
                "name": "Main Server",
                "module": "main_server",
                "process": stuck,
                "shutdown_event": event,
                "shutdown_complete_event": _CompletedEvent(),
                "graceful_shutdown_timeout": 20,
            },
            {
                "name": "Memory Server",
                "module": "memory_server",
                "process": healthy,
                "shutdown_event": healthy_event,
                "shutdown_complete_event": _CompletedEvent(),
                "graceful_shutdown_timeout": 12,
            },
        ],
        raising=False,
    )

    finished, _result, _elapsed = _run_bounded(launcher.cleanup_servers)

    assert finished, "cleanup_servers hung in shutdown_event.set()"
    assert launcher._cleanup_complete.is_set()
    # The stuck server still went through the rest of its steps, and the server
    # after it was still asked to stop: the order and the escalation are intact.
    assert stuck.join_calls == [2]
    assert healthy_event.is_set()
    assert healthy.join_calls == [2]
    assert not stuck.terminate_called and not healthy.terminate_called


@pytest.mark.unit
@pytest.mark.timeout(180)
def test_shutdown_request_is_acknowledged_by_a_live_sleeper():
    """The ordinary case is unchanged: the sleeper wakes and the request reports success."""
    from launcher_core import runtime as launcher

    event = multiprocessing.get_context("spawn").Event()
    woke = threading.Event()

    def _watch() -> None:
        event.wait()
        woke.set()

    threading.Thread(target=_watch, name="shutdown-watch", daemon=True).start()
    proc = _AliveUntilJoined()

    finished, acknowledged, _elapsed = _run_bounded(
        lambda: launcher._request_server_shutdown("Main Server", proc, event, timeout=_BOUNDED_SECONDS)
    )

    assert finished
    assert acknowledged is True
    assert woke.wait(timeout=10)
    assert event.is_set()
