# -*- coding: utf-8 -*-
"""Single-instance self-proof: real OS locks, real processes.

These deliberately avoid mocking the lock. The whole point of the primitive is
that the *kernel* releases it when the holder dies, so a mock would only test
our imagination of the OS.
"""

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from tests.fake_clock import patch_module_clock
from utils import single_instance

_HOLDER_SCRIPT = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, {root!r})
    from utils import single_instance

    handle = single_instance.acquire_single_instance(
        instance_id="holder-instance",
        launch_id="holder-launch",
        ports={{"MAIN_SERVER_PORT": 48911}},
    )
    if handle is None:
        print("LOST", flush=True)
        raise SystemExit(3)
    # Report our own pid: the identity under test is "the record names the
    # process that holds the lock", and only that process can state it. The
    # spawner's Popen.pid is a second-hand answer that is wrong wherever the
    # interpreter is reached through a shim.
    print("HELD", os.getpid(), flush=True)
    while True:
        time.sleep(0.05)
    """
)


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(single_instance.RUNTIME_STATE_DIR_ENV, str(tmp_path / "runtime"))
    yield tmp_path / "runtime"
    single_instance.release_single_instance()


def _start_holder(runtime_dir) -> subprocess.Popen:
    env = dict(os.environ)
    env[single_instance.RUNTIME_STATE_DIR_ENV] = str(runtime_dir)
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SCRIPT.format(root=_project_root())],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    line = proc.stdout.readline().strip()
    marker, _, reported = line.partition(" ")
    assert marker == "HELD", f"holder did not take the lock: {line!r} / {proc.stderr.read()!r}"
    # The pid the holder reports for itself. Usually identical to proc.pid, but
    # not where sys.executable is a shim that re-launches the real interpreter.
    proc.holder_pid = int(reported)
    return proc


@pytest.mark.unit
def test_owner_status_is_free_before_anyone_starts(runtime_dir):
    assert single_instance.owner_status() == (single_instance.OWNER_FREE, None)
    assert single_instance.read_owner_record() is None


@pytest.mark.unit
def test_acquire_publishes_an_authoritative_record(runtime_dir):
    handle = single_instance.acquire_single_instance(
        instance_id="abc123",
        launch_id="launch-1",
        ports={"MAIN_SERVER_PORT": 48911, "MEMORY_SERVER_PORT": 48912},
        extra={"owner_pid": 4242},
    )
    assert handle is not None

    on_disk = json.loads(handle.record_file.read_text(encoding="utf-8"))
    assert on_disk["schema"] == single_instance.RECORD_SCHEMA_VERSION
    assert on_disk["state"] == single_instance.STATE_STARTING
    assert on_disk["instance_id"] == "abc123"
    assert on_disk["launch_id"] == "launch-1"
    assert on_disk["pid"] == os.getpid()
    assert on_disk["parent_pid"] == os.getppid()
    assert on_disk["ports"]["MAIN_SERVER_PORT"] == 48911
    assert on_disk["owner_pid"] == 4242

    handle.publish(state=single_instance.STATE_READY, ports={"MAIN_SERVER_PORT": 49000})
    updated = json.loads(handle.record_file.read_text(encoding="utf-8"))
    assert updated["state"] == single_instance.STATE_READY
    assert updated["ports"] == {"MAIN_SERVER_PORT": 49000}
    # Untouched fields survive a partial publish.
    assert updated["instance_id"] == "abc123"


@pytest.mark.unit
def test_release_drops_the_record_and_frees_the_lock(runtime_dir):
    handle = single_instance.acquire_single_instance(instance_id="abc123")
    assert handle is not None
    record_file = handle.record_file

    handle.release()
    assert not record_file.exists()
    assert single_instance.owner_status()[0] == single_instance.OWNER_FREE
    # Idempotent: a second release must not raise or resurrect state.
    handle.release()


@pytest.mark.unit
def test_a_second_process_is_refused_and_learns_who_won(runtime_dir):
    holder = _start_holder(runtime_dir)
    try:
        assert single_instance.acquire_single_instance(instance_id="loser") is None

        status, record = single_instance.owner_status()
        assert status == single_instance.OWNER_OWNED
        assert record is not None
        assert record["pid"] == holder.holder_pid, (
            f"record names pid {record['pid']}, holder reports {holder.holder_pid}, "
            f"spawner saw {holder.pid}, this process is {os.getpid()}; record={record!r}"
        )
        assert record["instance_id"] == "holder-instance"
        # This is the whole point: the loser is handed the winner's ports rather
        # than being told to go probe for them.
        assert record["ports"]["MAIN_SERVER_PORT"] == 48911
    finally:
        holder.kill()
        holder.wait(timeout=10)


@pytest.mark.unit
def test_the_kernel_frees_the_lock_when_the_holder_is_killed(runtime_dir):
    """A SIGKILLed holder leaves a record behind — and it must not be believed."""
    holder = _start_holder(runtime_dir)
    record_file = runtime_dir / single_instance.RECORD_FILE_NAME
    assert record_file.exists()

    holder.kill()
    holder.wait(timeout=10)

    # The stale record is still on disk...
    assert record_file.exists()
    # ...and is correctly reported as belonging to nobody, because liveness is
    # proven by the lock rather than by the record.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if single_instance.owner_status()[0] == single_instance.OWNER_FREE:
            break
        time.sleep(0.05)
    assert single_instance.owner_status() == (single_instance.OWNER_FREE, None)
    assert single_instance.read_owner_record() is None

    # And the next launcher takes over with no stale-lock recovery step at all.
    handle = single_instance.acquire_single_instance(instance_id="successor")
    assert handle is not None
    assert json.loads(record_file.read_text(encoding="utf-8"))["instance_id"] == "successor"


@pytest.mark.unit
def test_handoff_retries_wait_out_the_outgoing_holder(runtime_dir, monkeypatch):
    holder = _start_holder(runtime_dir)
    sleeps: list[float] = []
    released = {"done": False}

    real_sleep = time.sleep

    def _fake_sleep(seconds):
        # Only the retry loop uses this interval; subprocess bookkeeping sleeps
        # with its own much smaller backoff values and must not be counted.
        if seconds == 0.05:
            sleeps.append(seconds)
            if len(sleeps) == 2 and not released["done"]:
                released["done"] = True
                holder.kill()
                holder.wait(timeout=10)
            # Still yield real time. Popen.wait() returns when the process we
            # spawned exits, which on Windows is a shim rather than the
            # interpreter holding the lock, so the kernel may not have dropped
            # the lock yet. Collapsing the interval to zero would burn all the
            # retries inside that gap and conclude the handoff failed.
            real_sleep(0.02)
            return
        real_sleep(seconds)

    patch_module_clock(monkeypatch, single_instance, sleep=_fake_sleep)

    handle = single_instance.acquire_single_instance(
        instance_id="successor",
        retries=20,
        retry_interval=0.05,
    )
    assert handle is not None, "handoff should have waited for the outgoing holder"
    assert released["done"]
    # Bounded: it waited, but it did not wait forever.
    assert 2 <= len(sleeps) <= 20


@pytest.mark.unit
def test_without_handoff_the_loser_gives_up_immediately(runtime_dir, monkeypatch):
    holder = _start_holder(runtime_dir)
    slept: list[float] = []
    patch_module_clock(monkeypatch, single_instance, sleep=lambda s: slept.append(s))
    try:
        assert single_instance.acquire_single_instance(instance_id="loser") is None
        assert slept == []
    finally:
        holder.kill()
        holder.wait(timeout=10)


@pytest.mark.unit
def test_unreachable_lock_directory_raises_instead_of_lying(tmp_path, monkeypatch):
    """An unreadable lock location is ``unknown``, never ``free`` and never ``owned``."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv(single_instance.RUNTIME_STATE_DIR_ENV, str(blocked / "runtime"))

    with pytest.raises(OSError):
        single_instance.acquire_single_instance(instance_id="abc")

    assert single_instance.owner_status() == (single_instance.OWNER_UNKNOWN, None)


@pytest.mark.unit
def test_a_lock_error_that_is_not_contention_stays_unknown(runtime_dir, monkeypatch):
    """ENOLCK is not evidence of another instance — it is no evidence at all."""
    import errno as _errno

    def _broken_lock(_fd):
        raise OSError(_errno.ENOLCK, "no locks available")

    monkeypatch.setattr(single_instance, "_try_lock_fd", _broken_lock)

    assert single_instance.owner_status() == (single_instance.OWNER_UNKNOWN, None)
    with pytest.raises(OSError):
        single_instance.acquire_single_instance(instance_id="abc")


@pytest.mark.unit
def test_contention_is_reported_as_owned_not_as_an_error(runtime_dir, monkeypatch):
    def _busy(_fd):
        raise single_instance.LockHeldByAnother()

    monkeypatch.setattr(single_instance, "_try_lock_fd", _busy)

    assert single_instance.owner_status()[0] == single_instance.OWNER_OWNED
    assert single_instance.acquire_single_instance(instance_id="abc") is None


@pytest.mark.unit
def test_startup_lock_facade_delegates_to_the_single_primitive(runtime_dir):
    from utils import port_utils

    assert port_utils.acquire_startup_lock() is True
    assert single_instance.active_handle() is not None

    port_utils.release_startup_lock()
    assert single_instance.active_handle() is None
    assert single_instance.owner_status()[0] == single_instance.OWNER_FREE


@pytest.mark.unit
def test_startup_lock_facade_reports_a_live_holder(runtime_dir):
    from utils import port_utils

    holder = _start_holder(runtime_dir)
    try:
        assert port_utils.acquire_startup_lock() is False
    finally:
        holder.kill()
        holder.wait(timeout=10)


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="fork semantics are POSIX-only")
def test_forked_child_dropping_its_reference_does_not_free_the_parent_lock(runtime_dir):
    handle = single_instance.acquire_single_instance(instance_id="parent-owned")
    assert handle is not None

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the forked child
        try:
            os.close(read_fd)
            single_instance.drop_inherited_reference()
            status = single_instance.owner_status()[0]
            os.write(write_fd, status.encode("ascii"))
        finally:
            os._exit(0)

    os.close(write_fd)
    try:
        child_view = os.read(read_fd, 64).decode("ascii")
    finally:
        os.close(read_fd)
        os.waitpid(pid, 0)

    # The child let go of its inherited descriptor, yet the parent still holds
    # the lock — flock lives on the open file description, not on the fd.
    assert child_view == single_instance.OWNER_OWNED
    assert handle.record_file.exists()
    assert single_instance.active_handle() is handle


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="POSIX path derivation")
def test_lock_path_does_not_drift_with_the_ambient_environment(monkeypatch, tmp_path):
    """Two launches by one user must resolve one lock, however they were started.

    XDG_RUNTIME_DIR is present in a desktop session and absent under cron, plain
    SSH, `su`, a system unit or a container; HOME is whatever a sandbox, launchd
    job or wrapper script says it is. Deriving the lock path from either meant the
    same uid took two unrelated locks and both launchers called themselves the
    owner — the uniqueness proof degrading back into port probing.
    """
    monkeypatch.delenv(single_instance.RUNTIME_STATE_DIR_ENV, raising=False)

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run-user"))
    monkeypatch.setenv("HOME", str(tmp_path / "sandbox-home"))
    sandboxed = single_instance.lock_path()

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    bare = single_instance.lock_path()

    assert sandboxed == bare, (
        f"lock path drifted with the environment: {sandboxed} vs {bare}"
    )
