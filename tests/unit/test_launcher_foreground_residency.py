# -*- coding: utf-8 -*-
"""Foreground residency: the runtime never daemonizes and never outlives its owner.

Two kinds of test here, on purpose:

* **Real-process tests** for the guard itself. Whether a process actually dies
  when its parent does is an OS question; a mocked answer would only confirm what
  we already believe.
* **Contract tests** (source-level) for the *absence* of detachment primitives.
  A regression here is somebody re-adding ``setsid`` or ``DETACHED_PROCESS``
  somewhere new, which no behavioural test would catch until it shipped.
"""

import atexit
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from tests.fake_clock import patch_module_clock
from utils import parent_guard

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_CORE = PROJECT_ROOT / "launcher_core"



def _preset_event() -> threading.Event:
    """An Event that is already set — stands in for a cleanup that has finished."""
    event = threading.Event()
    event.set()
    return event

@pytest.fixture(autouse=True)
def restore_launcher_module_state():
    """Undo module globals that the launcher sets on itself.

    _handle_owner_death sets _owner_death_in_progress and
    install_parent_death_guard sets _parent_death_guard — production code
    assigning to its own globals, which monkeypatch cannot know about. Left
    behind, the first leaks into every later test that reaches a path guarded by
    it, and the second leaves a stub guard object standing in for the real one.
    """
    from launcher_core import runtime as launcher

    saved = (launcher._owner_death_in_progress, launcher._parent_death_guard,
             launcher._owner_death_finisher)
    yield
    (launcher._owner_death_in_progress, launcher._parent_death_guard,
     launcher._owner_death_finisher) = saved


@pytest.fixture
def preserved_signal_handlers():
    """Restore dispositions the child-policy helper deliberately overwrites."""
    names = [n for n in ("SIGINT", "SIGTERM", "SIGBREAK") if hasattr(signal, n)]
    saved = {}
    for name in names:
        sig = getattr(signal, name)
        try:
            saved[sig] = signal.getsignal(sig)
        except (ValueError, OSError):
            # Signal unavailable on this platform, or we are off the main
            # thread: nothing saved means nothing to restore below.
            pass
    yield
    for sig, handler in saved.items():
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, TypeError):
            # Best-effort restore during teardown; a failure here must not mask
            # the assertion result of the test that just ran.
            pass


# ---------------------------------------------------------------------------
#  Contract: no detachment primitives anywhere in the launcher
# ---------------------------------------------------------------------------

FORBIDDEN_DETACH_PATTERNS = {
    "os.setsid": re.compile(r"\bos\.setsid\s*\("),
    "start_new_session": re.compile(r"\bstart_new_session\b"),
    "DETACHED_PROCESS": re.compile(r"\bDETACHED_PROCESS\b"),
    "CREATE_NEW_PROCESS_GROUP": re.compile(r"\bCREATE_NEW_PROCESS_GROUP\b"),
    "os.fork": re.compile(r"\bos\.fork\s*\("),
    "os.setpgrp": re.compile(r"\bos\.setpgrp\s*\("),
}


def _executable_lines(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, text)`` for code only — comments and strings stripped.

    The launcher's own prose explains *why* each detachment primitive was
    removed, so a plain grep would flag the documentation of the invariant as a
    violation of it.
    """
    import io
    import tokenize

    lines: dict[int, list[str]] = {}
    with io.StringIO(path.read_text(encoding="utf-8")) as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE):
                continue
            lines.setdefault(token.start[0], []).append(token.string)
    return [(lineno, "".join(parts)) for lineno, parts in sorted(lines.items())]


@pytest.mark.unit
@pytest.mark.parametrize("name,pattern", sorted(FORBIDDEN_DETACH_PATTERNS.items()))
def test_launcher_core_contains_no_detachment_primitive(name, pattern):
    """The launcher is a foreground process; nothing in it may escape its owner.

    ``os.setsid`` used to sit in every server child and ``DETACHED_PROCESS`` /
    ``start_new_session`` in the storage-restart relaunch. Both handed downstream
    a runtime it had not spawned and could not prove it owned.
    """
    offenders = []
    for path in sorted(LAUNCHER_CORE.glob("*.py")):
        for lineno, text in _executable_lines(path):
            if pattern.search(text):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {text}")
    assert not offenders, f"{name} reintroduces detachment:\n" + "\n".join(offenders)


@pytest.mark.unit
def test_cleanup_does_not_close_the_job_handle_it_is_a_member_of():
    """Closing a KILL_ON_JOB_CLOSE job we belong to would kill us mid-cleanup."""
    source = (LAUNCHER_CORE / "runtime.py").read_text(encoding="utf-8")
    cleanup = source.split("def cleanup_servers(")[1].split("\ndef ")[0]
    assert "CloseHandle(JOB_HANDLE)" not in cleanup


# ---------------------------------------------------------------------------
#  Relaunch stays attached
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_storage_relaunch_stays_in_the_owner_process_group(monkeypatch):
    from launcher_core import runtime as launcher

    captured = {}

    def _fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(launcher.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(launcher, "_build_launcher_relaunch_command", lambda: ["python", "launcher.py"])
    monkeypatch.setattr(launcher, "_relax_job_kill_on_close", lambda: None)

    launcher._spawn_restarted_launcher()

    kwargs = captured["kwargs"]
    assert "start_new_session" not in kwargs
    assert "creationflags" not in kwargs
    # stdio is inherited, so the replacement keeps writing NEKO_EVENT lines down
    # the same pipe the owner is already reading.
    assert "stdout" not in kwargs and "stderr" not in kwargs and "stdin" not in kwargs

    env = kwargs["env"]
    assert env[launcher.RESTART_HANDOFF_ENV] == "1"
    assert "_NEKO_MAIN_SERVER_INITIALIZED" not in env
    # The replacement watches the real owner, not the launcher that is exiting.
    assert env[parent_guard.PARENT_PID_ENV] == str(os.getppid())


@pytest.mark.unit
def test_storage_restart_prefers_owner_relaunch_over_self_spawn(monkeypatch):
    from launcher_core import runtime as launcher

    spawned = {"called": False}
    monkeypatch.setenv(launcher.OWNER_RELAUNCH_ENV, "1")
    monkeypatch.setattr(launcher, "_spawn_restarted_launcher",
                        lambda: spawned.__setitem__("called", True))
    monkeypatch.setattr(launcher, "release_single_instance_ownership", lambda: None)
    monkeypatch.setattr(launcher, "_resolve_storage_layout_for_launch",
                        lambda: {"migration_result": {"attempted": True, "completed": True}, "layout": {}})
    monkeypatch.setattr(launcher, "get_config_manager",
                        lambda *_a, **_k: type("_CM", (), {"load_root_state": staticmethod(dict)})())

    events = []
    monkeypatch.setattr(launcher, "emit_frontend_event",
                        lambda event, payload=None: events.append((event, payload)))

    assert launcher._maybe_schedule_storage_restart() is True
    assert spawned["called"] is False, "a foreground process must not resurrect itself"
    assert [p["relaunch"] for e, p in events if e == "storage_migration_restart"] == ["owner"]


# ---------------------------------------------------------------------------
#  Child signal policy replaces setsid without detaching
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="SIGINT shielding is POSIX-specific")
def test_child_policy_shields_sigint_without_leaving_the_process_group(
    preserved_signal_handlers, monkeypatch
):
    from launcher_core import runtime as launcher

    original_pgid = os.getpgid(0)
    monkeypatch.setattr(launcher.single_instance, "drop_inherited_reference", lambda: None)

    launcher._apply_child_process_signal_policy()

    assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    assert signal.getsignal(signal.SIGTERM) is launcher._handle_child_termination_signal
    # The whole point: still in the launcher's group, so a group sweep reaches us.
    assert os.getpgid(0) == original_pgid


@pytest.mark.unit
def test_child_policy_drops_inherited_launcher_teardown(preserved_signal_handlers, monkeypatch):
    from launcher_core import runtime as launcher

    dropped = {"lock": False}
    monkeypatch.setattr(launcher.single_instance, "drop_inherited_reference",
                        lambda: dropped.__setitem__("lock", True))

    atexit.register(launcher.cleanup_servers)
    try:
        launcher._apply_child_process_signal_policy()
    finally:
        atexit.unregister(launcher.cleanup_servers)

    assert dropped["lock"] is True


@pytest.mark.unit
def test_child_termination_signal_runs_the_registered_graceful_stop(preserved_signal_handlers):
    from launcher_core import runtime as launcher

    stopped = []
    launcher._child_graceful_stop_hooks.clear()
    launcher.register_child_graceful_stop_hook(lambda: stopped.append("uvicorn"))
    try:
        launcher._handle_child_termination_signal(signal.SIGTERM, None)
    finally:
        launcher._child_graceful_stop_hooks.clear()

    assert stopped == ["uvicorn"]


@pytest.mark.unit
def test_child_termination_signal_exits_when_nothing_is_registered(preserved_signal_handlers):
    from launcher_core import runtime as launcher

    launcher._child_graceful_stop_hooks.clear()
    with pytest.raises(SystemExit):
        launcher._handle_child_termination_signal(signal.SIGTERM, None)


@pytest.mark.unit
def test_uvicorn_cannot_take_the_signal_handlers_back():
    """Each child server must pin the launcher's policy over uvicorn's own."""
    source = (LAUNCHER_CORE / "runtime.py").read_text(encoding="utf-8")
    for entry in ("run_memory_server", "run_agent_server", "run_main_server"):
        body = source.split(f"def {entry}(")[1].split("\ndef ")[0]
        assert "_apply_child_process_signal_policy()" in body, entry
        assert "_disable_uvicorn_signal_handlers(server)" in body, entry


# ---------------------------------------------------------------------------
#  Group sweep is only ever performed by a group leader
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_group_sweep_refuses_when_we_do_not_lead_the_group(monkeypatch):
    from launcher_core import runtime as launcher

    killed = []
    monkeypatch.setattr(launcher.os, "getpgid", lambda _pid: os.getpid() + 1)
    monkeypatch.setattr(launcher.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    assert launcher._own_process_group_id() is None
    assert launcher._sweep_own_process_group(signal.SIGTERM) is False
    assert killed == [], "signalling somebody else's group could kill the owner"


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_group_sweep_signals_the_group_when_we_lead_it(monkeypatch):
    from launcher_core import runtime as launcher

    killed = []
    monkeypatch.setattr(launcher.os, "getpgid", lambda _pid: os.getpid())
    monkeypatch.setattr(launcher.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    assert launcher._sweep_own_process_group(signal.SIGTERM) is True
    assert killed == [(os.getpid(), signal.SIGTERM)]


# ---------------------------------------------------------------------------
#  The guard itself, against real processes
# ---------------------------------------------------------------------------

_GUARDED_CHILD = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, {root!r})
    from utils import parent_guard

    marker = {marker!r}

    def _on_death(mechanism):
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(mechanism)
        os._exit(0)

    guard = parent_guard.install(
        _on_death, poll_interval={poll_interval}, watch_stdin={watch_stdin}
    )
    with open({armed!r}, "w", encoding="utf-8") as handle:
        handle.write(",".join(guard.mechanisms))
    while True:
        time.sleep(0.05)
    """
)


def _wait_for(path: Path, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix" or sys.platform.startswith("linux"),
                    reason="the poll fallback is for POSIX platforms without pdeathsig")
def test_child_guard_falls_back_to_polling_without_pdeathsig():
    """macOS children must watch the launcher too.

    install_child_guard is the only place a child server arms anything — they
    never call parent_guard.install() — so a Linux-only implementation left the
    macOS servers with nothing watching the launcher at all.
    """
    assert parent_guard.install_child_guard(os.getppid()) is True


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="group broadcast is a POSIX shape")
def test_child_defers_to_the_launcher_on_a_group_broadcast(monkeypatch):
    """A group-wide TERM must not let a child outrun the launcher's ordering.

    kill -- -<pgid> reaches the launcher and all three servers at the same
    instant. If Memory stops immediately, Main's release call has nobody to talk
    to. os.setsid() used to hide children from such a broadcast; removing it is
    what exposed them, so the ordering is restored here instead.
    """
    from launcher_core import runtime as launcher

    stopped = []
    event = threading.Event()
    monkeypatch.setattr(launcher, "_child_graceful_stop_hooks", [lambda: stopped.append("stop")])
    monkeypatch.setattr(launcher, "_launcher_shutdown_event", event)
    monkeypatch.setattr(launcher, "_spawning_launcher_pid", os.getppid())

    launcher._handle_child_termination_signal(signal.SIGTERM, None)
    time.sleep(0.2)
    assert stopped == [], "child stopped while the launcher was still driving"

    # The launcher reaches us in its own order; only then do we stop.
    event.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not stopped:
        time.sleep(0.02)
    assert stopped == ["stop"]


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="group broadcast is a POSIX shape")
def test_child_stops_on_its_own_when_no_launcher_is_driving(monkeypatch):
    """The deferral is bounded: an absent launcher must not buy indefinite life."""
    from launcher_core import runtime as launcher

    stopped = []
    monkeypatch.setattr(launcher, "_child_graceful_stop_hooks", [lambda: stopped.append("stop")])
    monkeypatch.setattr(launcher, "_launcher_shutdown_event", None)
    monkeypatch.setattr(launcher, "_spawning_launcher_pid", 0)

    launcher._handle_child_termination_signal(signal.SIGTERM, None)
    assert stopped == ["stop"], "a child with no launcher must stop immediately"


@pytest.mark.unit
@pytest.mark.skipif(sys.platform != "win32", reason="parent_handle is the win32 mechanism")
def test_parent_handle_arms_against_a_live_owner():
    """Windows has exactly one mechanism, and nothing else asserted it existed.

    Every real-process guard test here is POSIX-gated, and the survivors only
    ever assert that mechanisms is *empty* — so the whole Windows leg passed
    identically with every installer stubbed to return False. Since pdeathsig is
    Linux and both stdin_eof and ppid_poll are POSIX, that left the one mechanism
    Windows residency depends on with no assertion anywhere.
    """
    guard = parent_guard.install(lambda _m: None, poll_interval=60)
    try:
        assert "parent_handle" in guard.mechanisms, guard.mechanisms
        assert not guard.fired
    finally:
        guard.stop()


@pytest.mark.unit
@pytest.mark.skipif(sys.platform != "win32", reason="win32 parent-handle wait")
def test_guarded_process_dies_when_its_owner_exits_on_windows(tmp_path):
    """The other half: not just armed, but actually observing the owner exit."""
    marker = tmp_path / "fired"
    armed = tmp_path / "armed"
    child_file = tmp_path / "guarded_child_win.py"
    child_file.write_text(
        _GUARDED_CHILD.format(
            root=str(PROJECT_ROOT), marker=str(marker), armed=str(armed),
            watch_stdin="False", poll_interval="600",
        ),
        encoding="utf-8",
    )

    # Two Windows-specific shapes are needed here.
    #
    # The owner must PRECEDE the guarded process: _windows_parent_precedes_us
    # compares creation times, so a victim spawned before its watcher is reported
    # as a recycled pid instead. Hence the middle process.
    #
    # And the owner must be named explicitly. sys.executable reaches the real
    # interpreter through a shim on Windows (the CI probe in this workflow
    # measures it), so the child's getppid() is the shim, not the middle process
    # — and the shim outlives the middle, so a guard left to infer its owner
    # watches something that never exits. NEKO_OWNER_PID points it at the process
    # whose death is actually under test.
    middle_source = textwrap.dedent(
        f"""
        import os, subprocess, sys, time
        env = dict(os.environ)
        env["NEKO_OWNER_PID"] = str(os.getpid())
        proc = subprocess.Popen(
            [sys.executable, {str(child_file)!r}],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not os.path.exists({str(armed)!r}):
            time.sleep(0.05)
        print(proc.pid, flush=True)
        """
    )
    middle = subprocess.run(
        [sys.executable, "-c", middle_source],
        capture_output=True, text=True, timeout=60,
    )
    assert middle.returncode == 0, middle.stderr
    child_pid = int(middle.stdout.strip())

    try:
        assert _wait_for(armed)
        assert "parent_handle" in armed.read_text(encoding="utf-8")
        assert _wait_for(marker, timeout=15), "the guard never observed its owner exit"
        assert marker.read_text(encoding="utf-8") == "parent_handle"
    finally:
        subprocess.run(["taskkill", "/F", "/PID", str(child_pid)], capture_output=True)


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="needs POSIX re-parenting semantics")
def test_guarded_process_dies_when_its_real_parent_dies(tmp_path):
    """Kill the parent; the guarded grandchild must clean itself up."""
    marker = tmp_path / "fired"
    armed = tmp_path / "armed"
    child_source = _GUARDED_CHILD.format(
        root=str(PROJECT_ROOT), marker=str(marker), armed=str(armed),
        watch_stdin="False", poll_interval="0.1",
    )
    child_file = tmp_path / "guarded_child.py"
    child_file.write_text(child_source, encoding="utf-8")

    # The middle process spawns the guarded child, waits until the guard is armed
    # (so we test "owner alive at install, dies later" rather than a startup
    # race), and then exits — leaving the child re-parented, i.e. orphaned.
    # It must not share its stdout pipe with the child, or capture_output below
    # would block until the child itself exits.
    middle_source = textwrap.dedent(
        f"""
        import os, subprocess, sys, time
        proc = subprocess.Popen(
            [sys.executable, {str(child_file)!r}],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not os.path.exists({str(armed)!r}):
            time.sleep(0.05)
        print(proc.pid, flush=True)
        """
    )
    middle = subprocess.run(
        [sys.executable, "-c", middle_source],
        capture_output=True, text=True, timeout=60,
    )
    assert middle.returncode == 0, middle.stderr
    child_pid = int(middle.stdout.strip())

    # Everything below runs under try/finally: the child is an orphan in an
    # infinite sleep, so any assertion that fires before the liveness check at
    # the end would otherwise leave it running on the machine until the box (or
    # the CI runner) goes away.
    try:
        assert _wait_for(armed), "guard never reported which mechanisms it armed"
        armed_mechanisms = armed.read_text(encoding="utf-8").split(",")
        assert armed_mechanisms != [""], "no parent-death mechanism could be armed"
        if sys.platform.startswith("linux"):
            # The kernel trap is the point on Linux. If it silently stops being
            # armed the guarantee quietly degrades to a poll, and every assertion
            # below still passes because the poll covers for it.
            assert "pdeathsig" in armed_mechanisms, armed_mechanisms

        # Not merely "did it exit" — it must have run its *callback*. A mechanism
        # that kills the process without running cleanup (a parent-death signal
        # armed with no handler installed for it) satisfies "exited" and still
        # leaves every grandchild behind.
        assert _wait_for(marker), (
            f"guard armed {armed_mechanisms} but its callback never ran "
            "(the process may have died without cleaning up)"
        )
        assert marker.read_text(encoding="utf-8") in (
            "ppid_poll", "pdeathsig", "pdeathsig_late_install",
        )

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:  # pragma: no cover - only on failure
            pytest.fail("guarded process did not exit after its parent died")
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            # Already gone, which is the outcome the test wanted anyway; this
            # reap only matters when an assertion above fired first.
            pass


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="stdin-pipe EOF guard is POSIX-only")
def test_guarded_process_dies_when_the_owner_pipe_closes(tmp_path):
    """The instant path: the owner dies and its write end of our stdin goes away.

    The owner must really exit here rather than just close the pipe. EOF alone
    does not mean the owner died — a sibling can hold the write end, and an owner
    is free to close our stdin and keep running — so the guard confirms the death
    before firing. Closing the pipe under a live owner is covered by
    ``test_stdin_eof_without_owner_death_does_not_fire``.
    """
    marker = tmp_path / "fired"
    armed = tmp_path / "armed"
    child_file = tmp_path / "guarded_child_stdin.py"
    child_file.write_text(
        _GUARDED_CHILD.format(
            root=str(PROJECT_ROOT), marker=str(marker), armed=str(armed),
            # Long enough that the owner poll cannot be what fires: this test is
            # about the stdin path specifically.
            watch_stdin="True", poll_interval="600",
        ),
        encoding="utf-8",
    )

    # The middle process owns the child and holds the write end of its stdin.
    # Its exit closes that end and re-parents the child in one step, which is
    # what a real owner's death looks like.
    middle_source = textwrap.dedent(
        f"""
        import os, subprocess, sys, time
        proc = subprocess.Popen(
            [sys.executable, {str(child_file)!r}],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not os.path.exists({str(armed)!r}):
            time.sleep(0.05)
        print(proc.pid, flush=True)
        """
    )
    middle = subprocess.run(
        [sys.executable, "-c", middle_source],
        capture_output=True, text=True, timeout=60,
    )
    assert middle.returncode == 0, middle.stderr
    child_pid = int(middle.stdout.strip())

    try:
        assert _wait_for(armed)
        assert "stdin_eof" in armed.read_text(encoding="utf-8")

        assert _wait_for(marker, timeout=10), "EOF on the owner pipe did not trigger the guard"
        fired = marker.read_text(encoding="utf-8")
        if sys.platform.startswith("linux"):
            # pdeathsig is armed here too and the kernel delivers it the instant
            # the owner exits, so it legitimately beats the stdin watcher's
            # confirmation step. Either mechanism firing proves residency; which
            # one wins is a race we must not pin. The stdin path's own contract —
            # that it does *not* fire without a death — is pinned deterministically
            # by test_stdin_eof_without_owner_death_does_not_fire.
            assert fired in ("stdin_eof", "pdeathsig"), fired
        else:
            assert fired == "stdin_eof", fired
    finally:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            # Already gone, which is the outcome the test wanted anyway; this
            # reap only matters when an assertion above fired first.
            pass


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="stdin pipe guard is POSIX-only")
def test_stdin_eof_without_owner_death_does_not_fire(tmp_path):
    """A closed stdin while the owner is alive must not be read as its death.

    Whoever holds the write end is not necessarily the owner, and an owner may
    close our stdin for its own reasons. Acting on that would release the
    single-instance lock and sweep our process group out from under a healthy
    owner, so the guard confirms the death first and stays quiet when it cannot.
    """
    marker = tmp_path / "fired"
    armed = tmp_path / "armed"
    child_file = tmp_path / "guarded_child_live_owner.py"
    child_file.write_text(
        _GUARDED_CHILD.format(
            root=str(PROJECT_ROOT), marker=str(marker), armed=str(armed),
            watch_stdin="True", poll_interval="600",
        ),
        encoding="utf-8",
    )

    # pytest stays alive as the owner and simply closes the pipe.
    proc = subprocess.Popen([sys.executable, str(child_file)], stdin=subprocess.PIPE)
    try:
        assert _wait_for(armed)
        assert "stdin_eof" in armed.read_text(encoding="utf-8")

        proc.stdin.close()
        assert not _wait_for(marker, timeout=3), (
            "guard fired on stdin EOF while its owner was still alive"
        )
        assert proc.poll() is None, "guarded process exited while its owner was alive"
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.unit
def test_guard_does_not_arm_when_there_was_never_an_owner(monkeypatch):
    """Started by launchd/systemd: no owner to watch, and none was ever lost."""
    monkeypatch.setattr(parent_guard.os, "getppid", lambda: 1)
    monkeypatch.setattr(parent_guard, "_PPID_AT_IMPORT", 1)
    guard = parent_guard.install(lambda _m: None)
    try:
        assert guard.mechanisms == ()
        assert not guard.fired
    finally:
        guard.stop()


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="re-parenting to init is POSIX")
def test_guard_reports_an_owner_that_died_during_startup(monkeypatch):
    """ppid 1 is ambiguous, and the ambiguity used to resolve the wrong way.

    An owner that exits while we are still importing leaves us adopted by init,
    so install() sees ppid 1 and armed nothing — leaving a runtime holding the
    lock and the ports with no owner and no way to ever notice. Having had a
    parent at import time and not having one now can only mean it exited.
    """
    fired = []
    monkeypatch.setattr(parent_guard.os, "getppid", lambda: 1)
    monkeypatch.setattr(parent_guard, "_PPID_AT_IMPORT", 4242)
    guard = parent_guard.install(fired.append)
    try:
        assert guard.mechanisms == ()
        assert guard.fired
        assert fired == ["orphaned_during_startup"]
    finally:
        guard.stop()


@pytest.mark.unit
def test_guard_can_be_disabled_by_environment(monkeypatch):
    monkeypatch.setenv(parent_guard.PARENT_GUARD_ENV, "0")
    guard = parent_guard.install(lambda _m: None)
    try:
        assert guard.mechanisms == ()
    finally:
        guard.stop()


@pytest.mark.unit
def test_guard_watches_the_pid_the_owner_named(monkeypatch):
    monkeypatch.setenv(parent_guard.PARENT_PID_ENV, "4242")
    guard = parent_guard.install(lambda _m: None, poll_interval=60)
    try:
        assert guard.parent_pid == 4242
    finally:
        guard.stop()


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="needs POSIX process semantics")
def test_handoff_generation_does_not_fire_when_its_spawner_exits(tmp_path):
    """The replacement launcher watches the owner, not the launcher that spawned it.

    A generation handoff means our direct parent exits on purpose immediately
    after spawning us. A guard keyed on "our parent changed" would kill the
    replacement the moment it started.
    """
    fired = []
    # The named owner is this test process, which is emphatically not our parent.
    guard = parent_guard.install(fired.append, parent_pid=os.getpid(), poll_interval=0.05)
    try:
        assert guard.owner_is_direct_parent is False
        assert "owner_poll" in guard.mechanisms
        assert "ppid_poll" not in guard.mechanisms
        assert "pdeathsig" not in guard.mechanisms
        time.sleep(0.4)
        assert fired == [], "guard fired even though the named owner is alive"
    finally:
        guard.stop()


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="needs POSIX process semantics")
def test_handoff_generation_fires_when_the_named_owner_dies(tmp_path):
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    fired = []
    guard = parent_guard.install(fired.append, parent_pid=victim.pid, poll_interval=0.05)
    try:
        assert "owner_poll" in guard.mechanisms
        victim.kill()
        victim.wait(timeout=10)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not fired:
            time.sleep(0.05)
        assert fired == ["owner_poll"]
    finally:
        guard.stop()
        if victim.poll() is None:  # pragma: no cover - only on failure
            victim.kill()


@pytest.mark.unit
def test_guard_fires_only_once():
    fired = []
    guard = parent_guard.ParentDeathGuard(fired.append, os.getpid())
    guard.fire("a")
    guard.fire("b")
    assert fired == ["a"]


# ---------------------------------------------------------------------------
#  Launcher wiring
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_install_parent_death_guard_reports_what_it_armed(monkeypatch):
    from launcher_core import runtime as launcher

    events = []
    monkeypatch.setattr(launcher, "emit_frontend_event",
                        lambda event, payload=None: events.append((event, payload)))

    class _Guard:
        parent_pid = 4242
        mechanisms = ("pdeathsig", "ppid_poll")
        owner_start_token = ""

    monkeypatch.setattr(launcher.parent_guard, "install", lambda *_a, **_k: _Guard())
    guard = launcher.install_parent_death_guard()

    assert guard.parent_pid == 4242
    assert ("foreground_residency", {
        "owner_pid": 4242,
        "mechanisms": ["pdeathsig", "ppid_poll"],
        "guaranteed": True,
    }) in events


@pytest.mark.unit
def test_install_parent_death_guard_admits_when_nothing_is_armed(monkeypatch):
    from launcher_core import runtime as launcher

    events = []
    monkeypatch.setattr(launcher, "emit_frontend_event",
                        lambda event, payload=None: events.append((event, payload)))

    class _Guard:
        parent_pid = 1
        mechanisms = ()
        owner_start_token = ""

    monkeypatch.setattr(launcher.parent_guard, "install", lambda *_a, **_k: _Guard())
    launcher.install_parent_death_guard()

    payload = dict(events[-1][1])
    assert payload["guaranteed"] is False
    assert payload["mechanisms"] == []


@pytest.mark.unit
def test_owner_death_cleans_up_then_exits(monkeypatch):
    from launcher_core import runtime as launcher

    order = []
    monkeypatch.setattr(launcher, "_mark_expected_launcher_shutdown",
                        lambda: order.append("mark"))
    monkeypatch.setattr(launcher, "emit_frontend_event",
                        lambda event, payload=None: order.append(("event", event)))
    monkeypatch.setattr(launcher, "cleanup_servers", lambda: order.append("cleanup"))
    # The stub replaces the function whose finally publishes completion, so say
    # so explicitly rather than letting the teardown wait out a cleanup that no
    # longer exists.
    monkeypatch.setattr(launcher, "_cleanup_complete", _preset_event())
    monkeypatch.setattr(launcher.single_instance, "release_single_instance",
                        lambda: order.append("release"))
    monkeypatch.setattr(launcher, "_own_process_group_id", lambda: None)
    monkeypatch.setattr(launcher.os, "_exit", lambda code: order.append(("exit", code)))

    launcher._handle_owner_death("stdin_eof")

    # Bounded join before asserting. _handle_owner_death hands off to a thread and
    # returns immediately, so without this the assertions race the teardown — and
    # worse, a thread that outlives the test runs after monkeypatch has restored
    # the real os._exit and os.killpg, which can take the whole pytest session
    # down or, observed in practice, let a failing session exit 0.
    finisher = launcher._owner_death_finisher
    assert finisher is not None
    finisher.join(10)
    assert not finisher.is_alive(), "owner-death finisher outlived the test"

    # No "release": the lock is deliberately held until the process dies, so that
    # it is never free while this generation is still sweeping its process group.
    assert order == [
        "mark",
        ("event", "owner_exit"),
        "cleanup",
        ("exit", 0),
    ]


@pytest.mark.unit
def test_sigterm_yields_once_the_guard_has_fired(monkeypatch):
    """The gap between guard.fired and _owner_death_in_progress is not a hole.

    fire() sets one flag and _handle_owner_death sets the other a few dozen
    bytecodes later; on Linux the pdeathsig callback runs on this same thread, so
    a concurrent SIGTERM can land in between. Taking the ordinary path there
    raised SystemExit straight out of fire(), skipping the callback entirely.
    """
    from launcher_core import runtime as launcher

    class _FiredGuard:
        fired = True
        parent_pid = 4242
        owner_is_direct_parent = True

    died = []
    monkeypatch.setattr(launcher, "_parent_death_guard", _FiredGuard())
    monkeypatch.setattr(launcher, "_owner_death_in_progress", False)
    monkeypatch.setattr(launcher, "_handle_owner_death", lambda m: died.append(m))
    monkeypatch.setattr(launcher, "cleanup_servers", lambda: died.append("cleanup"))

    # Returns instead of raising SystemExit: the owner-death teardown owns this.
    launcher._handle_termination_signal(signal.SIGTERM, None)
    assert died == [], died


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="the orphan heuristic is POSIX-only")
def test_plain_sigterm_in_a_handoff_generation_is_not_read_as_owner_death(monkeypatch):
    """A handoff generation's parent is *meant* to be gone.

    Its owner is the grandparent, so "getppid() is not the owner" holds in normal
    operation. Using that as evidence of owner death would turn every ordinary
    stop request into a full teardown plus a process-group kill.
    """
    from launcher_core import runtime as launcher

    class _HandoffGuard:
        fired = False
        parent_pid = 424242          # the original owner, our grandparent
        owner_is_direct_parent = False

    died = []
    monkeypatch.setattr(launcher, "_parent_death_guard", _HandoffGuard())
    monkeypatch.setattr(launcher, "_owner_death_in_progress", False)
    monkeypatch.setattr(launcher, "_handle_owner_death",
                        lambda mechanism: died.append(mechanism))
    monkeypatch.setattr(launcher, "_mark_expected_launcher_shutdown", lambda: None)
    monkeypatch.setattr(launcher, "cleanup_servers", lambda: None)
    monkeypatch.setattr(launcher, "_cleanup_complete", _preset_event())

    with pytest.raises(SystemExit):
        launcher._handle_termination_signal(signal.SIGTERM, None)

    assert died == [], "an ordinary SIGTERM was mistaken for the owner dying"


@pytest.mark.unit
def test_owner_death_drives_the_merged_ordered_shutdown_first(monkeypatch):
    """Merged mode holds the servers in-process, so cleanup_servers sees nothing.

    Without the hand-off, owner death would run straight to os._exit(0) and cut
    off Main's release/cloudsave sequence — the very work the ordered shutdown
    exists to complete.
    """
    from launcher_core import runtime as launcher

    order = []
    requested = []
    # _handle_owner_death sets this module global and never clears it, so without
    # monkeypatch owning the restore it would stay True for the rest of the
    # session and silently short-circuit _handle_termination_signal in every
    # later test.
    monkeypatch.setattr(launcher, "_owner_death_in_progress", False)
    monkeypatch.setattr(launcher, "_mark_expected_launcher_shutdown", lambda: None)
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_a, **_k: None)
    monkeypatch.setattr(launcher, "cleanup_servers", lambda: order.append("cleanup"))
    # The stub replaces the function whose finally publishes completion, so say
    # so explicitly rather than letting the teardown wait out a cleanup that no
    # longer exists.
    monkeypatch.setattr(launcher, "_cleanup_complete", _preset_event())
    monkeypatch.setattr(launcher.single_instance, "release_single_instance",
                        lambda: order.append("release"))
    monkeypatch.setattr(launcher, "_own_process_group_id", lambda: None)
    monkeypatch.setattr(launcher.os, "_exit", lambda code: order.append(("exit", code)))

    def _requester(*, reason):
        requested.append(reason)
        # Stand in for the async coordinator finishing the ordered shutdown.
        launcher._merged_shutdown_complete.set()

    monkeypatch.setattr(launcher, "_merged_shutdown_request", _requester)
    monkeypatch.setattr(launcher, "_merged_shutdown_complete", threading.Event())

    launcher._handle_owner_death("stdin_eof")

    # The teardown runs on its own thread so the merged loop (which lives on the
    # main thread) can actually make the progress we are waiting for.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and ("exit", 0) not in order:
        time.sleep(0.02)

    assert requested == ["owner_death:stdin_eof"], requested
    assert order == ["cleanup", ("exit", 0)], order


@pytest.mark.unit
@pytest.mark.skipif(os.name != "posix", reason="killpg/SIGKILL are POSIX-only")
def test_owner_death_escalates_term_then_kill_across_the_group(monkeypatch):
    """Grandchildren the launcher never recorded get an ordered chance first."""
    from launcher_core import runtime as launcher

    killed = []
    monkeypatch.setattr(launcher, "_mark_expected_launcher_shutdown", lambda: None)
    monkeypatch.setattr(launcher, "emit_frontend_event", lambda *_a, **_k: None)
    monkeypatch.setattr(launcher, "cleanup_servers", lambda: None)
    monkeypatch.setattr(launcher, "_cleanup_complete", _preset_event())
    monkeypatch.setattr(launcher.single_instance, "release_single_instance", lambda: None)
    monkeypatch.setattr(launcher, "_own_process_group_id", lambda: os.getpid())
    monkeypatch.setattr(launcher.os, "getpgid", lambda _pid: os.getpid())
    monkeypatch.setattr(launcher.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    patch_module_clock(monkeypatch, launcher, sleep=lambda _s: killed.append(("grace",)))
    monkeypatch.setattr(launcher.os, "_exit", lambda _code: None)

    launcher._handle_owner_death("parent_handle")

    # Bounded join before asserting. _handle_owner_death hands off to a thread and
    # returns immediately, so without this the assertions race the teardown — and
    # worse, a thread that outlives the test runs after monkeypatch has restored
    # the real os._exit and os.killpg, which can take the whole pytest session
    # down or, observed in practice, let a failing session exit 0.
    finisher = launcher._owner_death_finisher
    assert finisher is not None
    finisher.join(10)
    assert not finisher.is_alive(), "owner-death finisher outlived the test"

    assert killed == [
        (os.getpid(), signal.SIGTERM),
        ("grace",),
        (os.getpid(), signal.SIGKILL),
    ]


@pytest.mark.unit
def test_single_instance_acquisition_publishes_the_winner(monkeypatch):
    from launcher_core import runtime as launcher

    events = []
    monkeypatch.setattr(launcher, "emit_frontend_event",
                        lambda event, payload=None: events.append((event, payload)))

    class _Handle:
        record_file = Path("/tmp/record.json")
        lock_file = Path("/tmp/record.lock")
        held = True

        def record(self):
            return {"instance_id": "abc", "pid": 7}

    monkeypatch.setattr(launcher.single_instance, "acquire_single_instance",
                        lambda **_kwargs: _Handle())
    monkeypatch.setattr(launcher, "_parent_death_guard", None)

    try:
        assert launcher._acquire_single_instance_ownership() is True
    finally:
        launcher._single_instance_handle = None

    role = [p["role"] for e, p in events if e == "single_instance"]
    assert role == ["owner"]


@pytest.mark.unit
def test_losing_the_lock_hands_the_frontend_the_winner_instead_of_a_hint(monkeypatch):
    from launcher_core import runtime as launcher

    events = []
    monkeypatch.setattr(launcher, "emit_frontend_event",
                        lambda event, payload=None: events.append((event, payload)))
    monkeypatch.setattr(launcher.single_instance, "acquire_single_instance",
                        lambda **_kwargs: None)
    winner = {"instance_id": "winner", "pid": 99, "ports": {"MAIN_SERVER_PORT": 48911}}
    # Status and record come from one probe, so the loser cannot read a status
    # that disagrees with the record it reports.
    monkeypatch.setattr(launcher.single_instance, "owner_status",
                        lambda: (launcher.single_instance.OWNER_OWNED, winner))
    monkeypatch.setattr(launcher.single_instance, "read_owner_record", lambda: winner)
    monkeypatch.setattr(launcher, "_parent_death_guard", None)

    assert launcher._acquire_single_instance_ownership() is False

    by_event = {e: p for e, p in events}
    assert by_event["single_instance"]["role"] == "duplicate"
    assert by_event["single_instance"]["owner"]["ports"]["MAIN_SERVER_PORT"] == 48911
    # The legacy event stays, so an older frontend still recognises the scenario.
    assert by_event["startup_in_progress"]["owner"]["instance_id"] == "winner"


@pytest.mark.unit
def test_unreadable_lock_does_not_block_startup(monkeypatch):
    from launcher_core import runtime as launcher

    events = []
    monkeypatch.setattr(launcher, "emit_frontend_event",
                        lambda event, payload=None: events.append((event, payload)))

    def _raise(**_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(launcher.single_instance, "acquire_single_instance", _raise)
    monkeypatch.setattr(launcher, "_parent_death_guard", None)

    assert launcher._acquire_single_instance_ownership() is True
    assert [p["role"] for e, p in events if e == "single_instance"] == ["unverified"]
