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


@pytest.mark.unit
def test_storage_restart_requires_every_old_server_to_be_dead():
    """A file lock cannot substitute for proof that old Main exited."""

    source = (LAUNCHER_CORE / "runtime.py").read_text(encoding="utf-8")
    assert "if allow_storage_restart and not has_alive and not descendants_alive:" in source


@pytest.mark.unit
def test_descendants_are_only_settled_for_a_storage_restart(monkeypatch):
    """An ordinary exit must leave programs a server opened for the user alone."""
    from launcher_core import runtime

    calls = []
    monkeypatch.setattr(runtime, "_settle_surviving_descendants", lambda descendants: calls.append(descendants) or False)
    monkeypatch.setattr(runtime, "_teardown_descendants", [])

    assert runtime._descendants_block_storage_restart(False) is False
    assert calls == []
    assert runtime._descendants_block_storage_restart(True) is False
    assert calls == [[]]
    source = (LAUNCHER_CORE / "runtime.py").read_text(encoding="utf-8")
    assert "descendants_alive = _descendants_block_storage_restart(allow_storage_restart)" in source


@pytest.mark.unit
def test_storage_restart_is_blocked_when_descendants_are_unknown(monkeypatch):
    """No psutil, or a server that could not be inspected: its orphans are
    out of reach, so the restart must not go ahead on that basis."""
    from launcher_core import runtime

    monkeypatch.setattr(runtime, "_teardown_descendants", None)

    assert runtime._descendants_block_storage_restart(True) is True
    assert runtime._descendants_block_storage_restart(False) is False


@pytest.mark.unit
def test_an_exited_server_is_skipped_not_unknown():
    """In multiprocessing mode Main exits on its own before every migration
    restart; that must not make the descendants count as unknown."""
    from launcher_core import runtime

    class _Exited:
        pid = 4242

        def is_alive(self):
            return False

    assert runtime._snapshot_server_descendants([{"process": _Exited()}]) == []


@pytest.mark.unit
def test_descendants_of_a_running_server_that_cannot_be_inspected_are_unknown(monkeypatch):
    psutil = pytest.importorskip("psutil")
    from launcher_core import runtime

    class _Running:
        pid = os.getpid()

        def is_alive(self):
            return True

    real_process = psutil.Process

    def _server_denied(pid=None):
        if pid == _Running.pid:
            raise psutil.AccessDenied(pid)
        return real_process(pid)

    monkeypatch.setattr(psutil, "Process", _server_denied)

    assert runtime._snapshot_server_descendants([{"process": _Running()}]) is None


class _PopenServer:
    """A real process behind the multiprocessing.Process surface cleanup_servers uses."""

    def __init__(self, popen):
        self._popen = popen
        self.pid = popen.pid

    def is_alive(self):
        return self._popen.poll() is None

    def join(self, timeout=None):
        try:
            self._popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return  # still running; cleanup_servers escalates

    def terminate(self):
        self._popen.terminate()

    def kill(self):
        self._popen.kill()

    @property
    def exitcode(self):
        return self._popen.poll()


@pytest.mark.unit
def test_migration_restart_goes_ahead_after_main_exits_on_its_own(monkeypatch):
    """The real cleanup_servers, no stand-in: Main has already shut itself
    down for the migration while Memory still runs. The restart must still
    be scheduled."""
    pytest.importorskip("psutil")
    from launcher_core import runtime

    interpreter = getattr(sys, "_base_executable", "") or sys.executable
    main = subprocess.Popen([interpreter, "-c", "pass"])
    main.wait(timeout=20)
    memory = subprocess.Popen([interpreter, "-c", "import time; time.sleep(120)"])
    try:
        servers = [
            {"name": "Main", "module": "main_server", "process": _PopenServer(main), "graceful_shutdown_timeout": 0.2},
            {"name": "Memory", "module": "memory_server", "process": _PopenServer(memory), "graceful_shutdown_timeout": 0.2},
        ]
        monkeypatch.setattr(runtime, "SERVERS", servers)
        monkeypatch.setattr(runtime, "_cleanup_done", False)
        monkeypatch.setattr(runtime, "_teardown_descendants", None)
        monkeypatch.setattr(runtime, "_teardown_snapshot_taken", False)

        runtime.cleanup_servers()

        assert memory.poll() is not None
        assert runtime._teardown_descendants == []
        assert runtime._descendants_block_storage_restart(True) is False
    finally:
        memory.kill()


@pytest.mark.unit
def test_descendants_are_taken_before_the_first_teardown_step():
    """The startup restart path tears down inside the try block; a snapshot
    taken in the finally block would find nothing left to check."""
    import inspect

    from launcher_core import runtime

    source = inspect.getsource(runtime.cleanup_servers)
    teardown_try = source.index("    try:\n")
    snapshot = source.index("_take_teardown_snapshot_once()")
    first_teardown = source.index("for server in _iter_servers_for_shutdown():")
    assert teardown_try < snapshot < first_teardown


@pytest.mark.unit
def test_merged_mode_takes_the_snapshot_before_its_ordered_shutdown():
    """Merged mode stops the plugin hosts in its ordered shutdown, before
    cleanup_servers; the snapshot must come first or their subprocesses are
    no longer found."""
    import inspect

    from launcher_core import runtime

    source = inspect.getsource(runtime.run_merged_servers)
    assert source.index("_take_teardown_snapshot_once()") < source.index(
        "await _shutdown_merged_servers_in_order(servers_by_name, tasks)"
    )


@pytest.mark.unit
def test_the_teardown_snapshot_is_taken_only_once(monkeypatch):
    from launcher_core import runtime

    snapshots = iter([[("first", True)], [("second", True)]])
    monkeypatch.setattr(runtime, "_snapshot_server_descendants", lambda servers: next(snapshots))
    monkeypatch.setattr(runtime, "_teardown_snapshot_taken", False)
    monkeypatch.setattr(runtime, "_teardown_descendants", None)
    monkeypatch.setattr(runtime, "_running_descendants", [])

    runtime._take_teardown_snapshot_once()
    runtime._take_teardown_snapshot_once()

    assert runtime._teardown_descendants == [("first", True)]


@pytest.mark.unit
def test_teardown_keeps_descendants_only_the_running_snapshot_saw(monkeypatch):
    """Multiprocess mode: Main shuts itself down for the migration before any
    teardown snapshot; what the monitoring loop saw while it ran still
    counts."""
    from launcher_core import runtime

    monkeypatch.setattr(runtime, "_snapshot_server_descendants", lambda servers: [("still-found", True)])
    monkeypatch.setattr(runtime, "_teardown_snapshot_taken", False)
    monkeypatch.setattr(runtime, "_teardown_descendants", None)
    monkeypatch.setattr(runtime, "_running_descendants", [("orphan-of-main", True), ("still-found", True)])

    runtime._take_teardown_snapshot_once()

    assert runtime._teardown_descendants == [("still-found", True), ("orphan-of-main", True)]


@pytest.mark.unit
def test_unknown_teardown_descendants_stay_unknown_despite_a_running_snapshot(monkeypatch):
    from launcher_core import runtime

    monkeypatch.setattr(runtime, "_snapshot_server_descendants", lambda servers: None)
    monkeypatch.setattr(runtime, "_teardown_snapshot_taken", False)
    monkeypatch.setattr(runtime, "_teardown_descendants", [])
    monkeypatch.setattr(runtime, "_running_descendants", [("seen-earlier", True)])

    runtime._take_teardown_snapshot_once()

    assert runtime._teardown_descendants is None


@pytest.mark.unit
def test_the_running_snapshot_is_refreshed_from_startup_on():
    """Before the monitoring loop's first sleep, and while waiting for the
    servers to get ready: Main can end itself for a storage restart in
    either window."""
    import inspect

    from launcher_core import runtime

    import ast

    source = (LAUNCHER_CORE / "runtime.py").read_text(encoding="utf-8")

    def _calls(body):
        return [
            ast.unparse(statement.value.func)
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
            else ""
            for statement in body
        ]

    monitor_loops = [
        _calls(node.body)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.While) and "time.sleep" in _calls(node.body)
        and "_refresh_running_descendants" in _calls(node.body)
    ]
    assert monitor_loops
    assert all(
        calls.index("_refresh_running_descendants") < calls.index("time.sleep") for calls in monitor_loops
    )
    # The one-by-one import waits, too: Main runs while Agent imports.
    import_waits = [
        node.body
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.While) and "evt.wait" in ast.unparse(node)
    ]
    assert import_waits
    assert all(_calls(body)[0] == "_refresh_running_descendants" for body in import_waits)
    wait_source = inspect.getsource(runtime.wait_for_servers)
    # Before the early-exit check of each poll, so a Main that ends itself
    # during startup was seen at least once while it ran.
    assert wait_source.index("_refresh_running_descendants()") < wait_source.index(
        "if proc is not None and not proc.is_alive()"
    )


@pytest.mark.unit
def test_a_failed_refresh_makes_the_teardown_snapshot_unknown(monkeypatch):
    """Stale evidence may miss a newer descendant; the restart is blocked
    until a refresh succeeds again."""
    from launcher_core import runtime

    snapshots = iter([None, [("found", True)]])
    monkeypatch.setattr(runtime, "_snapshot_server_descendants", lambda servers: next(snapshots))
    monkeypatch.setattr(runtime, "_running_descendants", [("seen-earlier", True)])
    monkeypatch.setattr(runtime, "_running_descendants_known", True)
    monkeypatch.setattr(runtime, "_teardown_snapshot_taken", False)
    monkeypatch.setattr(runtime, "_teardown_descendants", [])

    runtime._refresh_running_descendants()  # cannot inspect a running server
    runtime._take_teardown_snapshot_once()  # the teardown itself succeeds

    assert runtime._teardown_descendants is None


@pytest.mark.unit
def test_a_successful_refresh_clears_the_unknown_state(monkeypatch):
    from launcher_core import runtime

    class _Running:
        def is_alive(self):
            return True

    monkeypatch.setattr(runtime, "SERVERS", [{"name": "Main", "process": _Running()}])
    snapshots = iter([None, []])
    monkeypatch.setattr(runtime, "_snapshot_server_descendants", lambda servers: next(snapshots))
    monkeypatch.setattr(runtime, "_running_descendants", [])
    monkeypatch.setattr(runtime, "_running_descendants_known", True)
    monkeypatch.setattr(runtime, "_uninspected_servers", set())

    runtime._refresh_running_descendants()
    runtime._refresh_running_descendants()

    assert runtime._running_descendants_known is True


# Spawning the base interpreter keeps a Windows venv's python.exe stub out of
# the process tree, so the tree looks the way multiprocessing builds it.
_INTERPRETER = getattr(sys, "_base_executable", "") or sys.executable


class _TrackedServer:
    def __init__(self, popen):
        self.pid = popen.pid
        self._popen = popen

    def is_alive(self):
        return self._popen.poll() is None


def _wait_for_pid(pid_file, server):
    deadline = time.monotonic() + 20
    while not (pid_file.exists() and pid_file.read_text().strip()):
        if time.monotonic() > deadline:
            server.kill()
            pytest.fail(f"the stand-in process tree never wrote {pid_file.name}")
        time.sleep(0.05)
    return int(pid_file.read_text())


def _spawn_server_with_a_child(tmp_path):
    """A stand-in server that starts a long-lived child (a plugin host)."""
    pid_file = tmp_path / "child.pid"
    server_code = textwrap.dedent(
        f"""
        import subprocess, time
        child = subprocess.Popen([{_INTERPRETER!r}, "-c", "import time; time.sleep(120)"])
        open({str(pid_file)!r}, "w").write(str(child.pid))
        time.sleep(120)
        """
    )
    server = subprocess.Popen([_INTERPRETER, "-c", server_code])
    return server, _wait_for_pid(pid_file, server)


def _orphan_a_child(tmp_path):
    from launcher_core import runtime

    server, child_pid = _spawn_server_with_a_child(tmp_path)
    descendants = runtime._snapshot_server_descendants([{"process": _TrackedServer(server)}])
    server.kill()
    server.wait(timeout=10)
    return server, child_pid, descendants


def _kill_quietly(psutil, server, *pids):
    server.kill()
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except psutil.NoSuchProcess:
            continue  # already stopped by the code under test


@pytest.mark.unit
def test_storage_restart_stops_our_own_child_that_outlived_its_server(tmp_path):
    """A plugin host (daemon=False, same executable) survives its server
    being killed; it is stopped before the restart can go ahead."""
    psutil = pytest.importorskip("psutil")
    from launcher_core import runtime

    server, child_pid, descendants = _orphan_a_child(tmp_path)
    try:
        assert [(process.pid, own) for process, own in descendants] == [(child_pid, True)]
        assert psutil.pid_exists(child_pid)

        assert runtime._settle_surviving_descendants(descendants) is False
        assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
    finally:
        _kill_quietly(psutil, server, child_pid)


@pytest.mark.unit
def test_a_program_a_plugin_started_with_our_interpreter_is_not_ours(tmp_path):
    """A plugin may run a user program through sys.executable; it runs the
    same executable but is no plugin host, so it is never stopped."""
    psutil = pytest.importorskip("psutil")
    from launcher_core import runtime

    host_pid_file = tmp_path / "host.pid"
    program_pid_file = tmp_path / "program.pid"
    host_code = textwrap.dedent(
        f"""
        import subprocess, time
        program = subprocess.Popen([{_INTERPRETER!r}, "-c", "import time; time.sleep(120)"])
        open({str(program_pid_file)!r}, "w").write(str(program.pid))
        time.sleep(120)
        """
    )
    server_code = textwrap.dedent(
        f"""
        import subprocess, time
        host = subprocess.Popen([{_INTERPRETER!r}, "-c", {host_code!r}])
        open({str(host_pid_file)!r}, "w").write(str(host.pid))
        time.sleep(120)
        """
    )
    server = subprocess.Popen([_INTERPRETER, "-c", server_code])
    host_pid = _wait_for_pid(host_pid_file, server)
    program_pid = _wait_for_pid(program_pid_file, server)
    try:
        descendants = runtime._snapshot_server_descendants([{"process": _TrackedServer(server)}])
        ownership = {process.pid: own for process, own in descendants}
        assert ownership == {host_pid: True, program_pid: False}
        server.kill()
        server.wait(timeout=10)

        assert runtime._settle_surviving_descendants(descendants) is True
        assert psutil.Process(program_pid).is_running()
    finally:
        _kill_quietly(psutil, server, host_pid, program_pid)


@pytest.mark.unit
def test_storage_restart_never_touches_a_program_opened_for_the_user(tmp_path):
    """Another executable (an app the user had a server open) is left
    running; it only keeps the restart from going ahead."""
    psutil = pytest.importorskip("psutil")
    from launcher_core import runtime

    server, child_pid, descendants = _orphan_a_child(tmp_path)
    try:
        foreign = [(process, False) for process, _own in descendants]

        assert runtime._settle_surviving_descendants(foreign) is True
        assert psutil.Process(child_pid).is_running()
    finally:
        _kill_quietly(psutil, server, child_pid)


@pytest.mark.unit
def test_storage_restart_waits_for_a_child_that_cannot_be_stopped(tmp_path, monkeypatch):
    psutil = pytest.importorskip("psutil")
    from launcher_core import runtime

    server, child_pid, descendants = _orphan_a_child(tmp_path)
    try:
        def _refuse(self):
            raise psutil.AccessDenied(self.pid)

        monkeypatch.setattr(psutil.Process, "terminate", _refuse)
        monkeypatch.setattr(psutil.Process, "kill", _refuse)
        monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout=None: ([], list(procs)))

        assert runtime._settle_surviving_descendants(descendants) is True
    finally:
        monkeypatch.undo()
        _kill_quietly(psutil, server, child_pid)


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

    def _write_marker(path, text):
        # Write-then-rename, because the test side waits on path.exists() and
        # then reads immediately. `open(path, "w")` publishes a ZERO-LENGTH
        # file first and fills it afterwards, so the reader could win that gap
        # and read "" -- which is exactly how this suite failed on CI:
        #   AssertionError: assert '' in ('stdin_eof', 'pdeathsig')
        # POSIX rename is atomic within a filesystem (these tests are
        # POSIX-only), so the marker becomes visible already complete.
        temp_path = path + ".partial"
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)

    def _on_death(mechanism):
        _write_marker(marker, mechanism)
        os._exit(0)

    guard = parent_guard.install(
        _on_death, poll_interval={poll_interval}, watch_stdin={watch_stdin}
    )
    _write_marker({armed!r}, ",".join(guard.mechanisms))
    while True:
        time.sleep(0.05)
    """
)


def _wait_for(path: Path, timeout: float = 15.0) -> bool:
    """Wait for ``path`` to appear.

    Callers read the file immediately afterwards, so anything that writes one
    of these markers must publish it ATOMICALLY -- see ``_write_marker`` in
    ``_GUARDED_CHILD``. A plain ``open(path, "w")`` creates the file empty and
    fills it after, and this returns on the create, so the reader can win that
    gap and see "". That is not hypothetical: it is what made this suite flake
    on CI as ``assert '' in ('stdin_eof', 'pdeathsig')``.
    """
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



@pytest.mark.unit
def test_merged_mode_snapshots_the_launchers_plugin_hosts_only(tmp_path):
    """Packaged builds run the servers inside the launcher; its plugin hosts
    (same executable) and what they started are checked, while another
    child of the launcher -- on Windows its conhost.exe -- is left out, or
    it would block every migration restart."""
    psutil = pytest.importorskip("psutil")
    from launcher_core import runtime

    grandchild_pid_file = tmp_path / "grandchild.pid"
    host_code = textwrap.dedent(
        f"""
        import subprocess, time
        program = subprocess.Popen([{_INTERPRETER!r}, "-c", "import time; time.sleep(120)"])
        open({str(grandchild_pid_file)!r}, "w").write(str(program.pid))
        time.sleep(120)
        """
    )
    host = subprocess.Popen([_INTERPRETER, "-c", host_code])
    other_command = ["ping", "-n", "120", "127.0.0.1"] if os.name == "nt" else ["sleep", "120"]
    other = subprocess.Popen(other_command, stdout=subprocess.DEVNULL)
    grandchild_pid = _wait_for_pid(grandchild_pid_file, host)
    try:
        descendants = runtime._snapshot_server_descendants(
            [{"name": "Main", "process": None}, {"name": "Memory", "process": None}]
        )
        ownership = {process.pid: own for process, own in descendants}
        assert ownership.get(host.pid) is True
        assert ownership.get(grandchild_pid) is False
        assert other.pid not in ownership
    finally:
        _kill_quietly(psutil, host, grandchild_pid)
        other.kill()
        other.wait(timeout=10)



@pytest.mark.unit
def test_merged_mode_snapshot_is_unknown_when_a_host_exits_during_the_scan(monkeypatch):
    """What a host started is reparented the moment it exits and cannot be
    found again; the snapshot cannot be complete, so it is unknown."""
    psutil = pytest.importorskip("psutil")
    from launcher_core import runtime

    hosts = [subprocess.Popen([_INTERPRETER, "-c", "import time; time.sleep(120)"]) for _ in range(2)]
    exited_pid = hosts[0].pid
    real_children = psutil.Process.children

    def _children(self, recursive=False):
        if recursive and self.pid == exited_pid:
            raise psutil.NoSuchProcess(self.pid)
        return real_children(self, recursive=recursive)

    monkeypatch.setattr(psutil.Process, "children", _children)
    try:
        assert runtime._snapshot_server_descendants([{"name": "Main", "process": None}]) is None
    finally:
        monkeypatch.undo()
        for host in hosts:
            host.kill()
            host.wait(timeout=10)



@pytest.mark.unit
def test_running_snapshot_survives_the_tick_after_main_exits(tmp_path, monkeypatch):
    """The loop's real order, real processes: refresh while Main runs, Main
    exits on its own (leaving a child), the next tick refreshes again before
    noticing, then teardown. The orphan must still be in the snapshot."""
    psutil = pytest.importorskip("psutil")
    from launcher_core import runtime

    pid_file = tmp_path / "child.pid"
    exit_flag = tmp_path / "exit-now"
    main_code = textwrap.dedent(
        f"""
        import os, subprocess, time
        child = subprocess.Popen([{_INTERPRETER!r}, "-c", "import time; time.sleep(120)"])
        open({str(pid_file)!r}, "w").write(str(child.pid))
        while not os.path.exists({str(exit_flag)!r}):
            time.sleep(0.05)
        """
    )
    main = subprocess.Popen([_INTERPRETER, "-c", main_code])
    child_pid = _wait_for_pid(pid_file, main)
    try:
        monkeypatch.setattr(runtime, "SERVERS", [{"name": "Main", "module": "main_server", "process": _PopenServer(main)}])
        monkeypatch.setattr(runtime, "_running_descendants", [])
        monkeypatch.setattr(runtime, "_teardown_snapshot_taken", False)
        monkeypatch.setattr(runtime, "_teardown_descendants", None)

        runtime._refresh_running_descendants()  # tick N: Main running
        exit_flag.write_text("", encoding="utf-8")
        main.wait(timeout=20)  # Main shuts itself down during the sleep
        runtime._refresh_running_descendants()  # tick N+1 refreshes first
        runtime._take_teardown_snapshot_once()  # then the loop breaks into teardown

        ownership = {process.pid: own for process, own in runtime._teardown_descendants}
        assert ownership.get(child_pid) is True
        assert runtime._settle_surviving_descendants(runtime._teardown_descendants) is False
        assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
    finally:
        _kill_quietly(psutil, main, child_pid)


@pytest.mark.unit
def test_running_snapshot_drops_processes_that_exited(monkeypatch):
    from launcher_core import runtime

    class _Gone:
        def is_running(self):
            return False

    gone = _Gone()
    monkeypatch.setattr(runtime, "_snapshot_server_descendants", lambda servers: [])
    monkeypatch.setattr(runtime, "_running_descendants", [(gone, True)])

    runtime._refresh_running_descendants()

    assert runtime._running_descendants == []



class _FakeServerProcess:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


@pytest.mark.unit
def test_uncertainty_outlasts_a_server_that_exits_before_it_is_inspected_again(monkeypatch):
    """A refresh failed while Main ran; Main then exited. A later refresh that
    no longer sees Main must not clear the uncertainty: an orphan it started
    in between is out of reach."""
    from launcher_core import runtime

    main = _FakeServerProcess()
    monkeypatch.setattr(runtime, "SERVERS", [{"name": "Main", "process": main}])
    snapshots = iter([None, []])
    monkeypatch.setattr(runtime, "_snapshot_server_descendants", lambda servers: next(snapshots))
    monkeypatch.setattr(runtime, "_running_descendants", [])
    monkeypatch.setattr(runtime, "_running_descendants_known", True)
    monkeypatch.setattr(runtime, "_uninspected_servers", set())

    runtime._refresh_running_descendants()  # cannot inspect Main
    main.alive = False
    runtime._refresh_running_descendants()  # "succeeds" without Main

    assert runtime._running_descendants_known is False


@pytest.mark.unit
def test_uncertainty_ends_once_every_missed_server_is_inspected_alive(monkeypatch):
    from launcher_core import runtime

    monkeypatch.setattr(runtime, "SERVERS", [{"name": "Main", "process": _FakeServerProcess()}])
    snapshots = iter([None, []])
    monkeypatch.setattr(runtime, "_snapshot_server_descendants", lambda servers: next(snapshots))
    monkeypatch.setattr(runtime, "_running_descendants", [])
    monkeypatch.setattr(runtime, "_running_descendants_known", True)
    monkeypatch.setattr(runtime, "_uninspected_servers", set())

    runtime._refresh_running_descendants()
    runtime._refresh_running_descendants()

    assert runtime._running_descendants_known is True



@pytest.mark.unit
def test_uncertainty_from_a_scan_without_tracked_servers_does_not_clear(monkeypatch):
    """Merged mode tracks no server process; a failed scan there cannot be
    "re-inspected alive", so later successful scans must not clear it."""
    from launcher_core import runtime

    monkeypatch.setattr(runtime, "SERVERS", [{"name": "Main", "process": None}])
    snapshots = iter([None, [], []])
    monkeypatch.setattr(runtime, "_snapshot_server_descendants", lambda servers: next(snapshots))
    monkeypatch.setattr(runtime, "_running_descendants", [])
    monkeypatch.setattr(runtime, "_running_descendants_known", True)
    monkeypatch.setattr(runtime, "_uninspected_servers", set())

    runtime._refresh_running_descendants()
    runtime._refresh_running_descendants()
    runtime._refresh_running_descendants()

    assert runtime._running_descendants_known is False



@pytest.mark.unit
@pytest.mark.parametrize("failure", ["exited", "denied"])
def test_merged_mode_snapshot_is_unknown_when_a_childs_executable_cannot_be_read(monkeypatch, failure):
    """A child whose executable cannot be read may be a plugin host: skipped
    as a non-host, what it started would never be checked."""
    psutil = pytest.importorskip("psutil")
    from launcher_core import runtime

    hosts = [subprocess.Popen([_INTERPRETER, "-c", "import time; time.sleep(120)"]) for _ in range(2)]
    unreadable_pid = hosts[0].pid
    real_exe = psutil.Process.exe

    def _exe(self):
        if self.pid == unreadable_pid:
            if failure == "exited":
                raise psutil.NoSuchProcess(self.pid)
            raise psutil.AccessDenied(self.pid)
        return real_exe(self)

    monkeypatch.setattr(psutil.Process, "exe", _exe)
    try:
        assert runtime._snapshot_server_descendants([{"name": "Main", "process": None}]) is None
    finally:
        monkeypatch.undo()
        for host in hosts:
            host.kill()
            host.wait(timeout=10)
