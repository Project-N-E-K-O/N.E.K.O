# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Foreground-residency guard: the runtime never outlives its owner.

``projectneko_server`` is a *foreground* process.  Whoever starts it — the
Electron desktop shell, a terminal, CI — owns it for its whole life.  Two rules
follow, and this module enforces the second one:

1. **Never daemonize.**  No ``setsid``, no ``DETACHED_PROCESS``, no re-parenting
   to init.  That is a property of how the launcher spawns things and lives in
   ``launcher_core.runtime``.
2. **Parent death is self-termination.**  If the owner disappears — clean exit,
   crash, ``SIGKILL``, power-cut of the UI process — the runtime tears its whole
   topology down instead of surviving as an orphan holding ports.

Rule 2 removes the need for an external anchor (a shell supervisor holding the
process group, a Windows Job holder owned by the parent, a persistent ownership
lease replayed on next boot).  Those exist only because a child could outlive
its parent; once it cannot, they have nothing left to recover.

Mechanisms, best-first per platform, all of them redundant on purpose:

============  ==========================================================
``pdeathsig`` Linux ``prctl(PR_SET_PDEATHSIG)`` — kernel-delivered, instant.
``stdin_eof`` POSIX, when fd 0 is a pipe from the parent — instant, and the
              only instant mechanism available on macOS.
``parent_handle``
              Windows ``WaitForSingleObject`` on a handle to the parent,
              opened at install time and verified against process creation
              times so a recycled pid cannot be mistaken for the parent.
``ppid_poll`` POSIX backstop — notices re-parenting within one interval.
============  ==========================================================

Every mechanism funnels into one idempotent callback.  Arming zero mechanisms
is reported honestly via :attr:`ParentDeathGuard.mechanisms` rather than
pretending the guarantee holds.
"""

from __future__ import annotations

import ctypes
import os
import signal
import stat
import sys
import threading
import time
from typing import Callable, Optional

#: Set to ``0``/``false`` to disable the guard entirely (debugging, profilers
#: that re-parent their target, ``gdb``-style workflows).
PARENT_GUARD_ENV = "NEKO_PARENT_DEATH_GUARD"

#: Overrides the pid the guard watches. Used for a generation handoff, where a
#: replacement launcher must watch its *grandparent* (the real owner) rather
#: than the outgoing launcher that spawned it.
PARENT_PID_ENV = "NEKO_OWNER_PID"

DEFAULT_POLL_INTERVAL = 1.0

_PR_SET_PDEATHSIG = 1


def _owner_death_signal():
    """The signal ``PR_SET_PDEATHSIG`` should raise in a *launcher*, or ``None``.

    Deliberately not ``SIGTERM``. The launcher already gives ``SIGTERM`` a
    meaning of its own — "somebody asked me to stop" — and installs its own
    ordered-shutdown handler for it in ``register_shutdown_hooks()``. Reusing
    it here collided in both directions: before those hooks are registered the
    default disposition simply killed the process, so the parent-death callback
    never ran at all, and afterwards the owner's death was indistinguishable
    from an ordinary stop request and skipped the process-group sweep that only
    ``_handle_owner_death`` performs.

    A real-time signal has no default meaning to collide with, so "the owner
    died" stays its own fact. ``SIGUSR2`` is the fallback for a libc without
    real-time signals.

    Note this applies to the launcher only: ``install_child_guard`` keeps
    ``SIGTERM``, because in a child server SIGTERM *is* the wanted action — the
    child installs a graceful-stop handler for it before arming the trap.
    """
    for name in ("SIGRTMIN", "SIGUSR2"):
        sig = getattr(signal, name, None)
        if sig is not None:
            return sig
    return None

# Windows constants
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
#: OpenProcess sets this when the pid matches no live process. Any *other*
#: failure means "could not look", which is not evidence of death.
_ERROR_INVALID_PARAMETER = 87


def _guard_enabled() -> bool:
    raw = os.environ.get(PARENT_GUARD_ENV, "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _configured_parent_pid() -> Optional[int]:
    raw = os.environ.get(PARENT_PID_ENV, "").strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if value > 1 else None


def _posix_start_token(pid: int) -> str:
    """Boot-scoped start time for ``pid``, or ``""`` when it cannot be read.

    Closes the pid-reuse window on the handoff path, where the owner is a
    grandparent and the only available liveness test is ``os.kill(pid, 0)`` —
    which happily reports an unrelated process that inherited the number. The
    Windows watcher already guards this with process creation times; this is the
    POSIX counterpart.

    Linux only. Elsewhere the empty token means "unavailable" and is never
    treated as a match, so behaviour there is exactly what it is today.
    """
    if not sys.platform.startswith("linux"):
        return ""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            raw = handle.read().decode("utf-8", errors="replace")
        # The comm field can contain spaces and parentheses; everything after
        # the last ") " is positional.
        closing = raw.rfind(") ")
        if closing < 0:
            return ""
        # starttime is field 22, i.e. index 19 once comm has been stripped.
        start_ticks = raw[closing + 2:].split()[19]
    except (OSError, IndexError, ValueError):
        return ""
    try:
        with open("/proc/sys/kernel/random/boot_id", "rb") as handle:
            boot_id = handle.read().decode("ascii", errors="replace").strip()
    except OSError:
        # Without a boot id the tick count alone is ambiguous across reboots.
        return ""
    return f"{boot_id}:{start_ticks}" if boot_id else ""


def _posix_pid_is_zombie(pid: int) -> bool:
    """True only for a pid that has exited and is merely awaiting reaping.

    On the handoff path the owner is a grandparent, so liveness rests entirely on
    ``os.kill(pid, 0)`` — which keeps succeeding for a zombie, whose start token
    is unchanged too. Without this the runtime stays up, holding the
    single-instance lock, until somebody reaps a process that is already dead.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            raw = handle.read().decode("utf-8", errors="replace")
        closing = raw.rfind(") ")
        if closing < 0:
            return False
        fields = raw[closing + 2:].split()
        state = fields[0]               # field 3
        num_threads = int(fields[17])   # field 20
    except (OSError, IndexError, ValueError):
        return False
    # A thread-group leader whose main thread called pthread_exit also reports
    # 'Z' while its siblings run on, and that process is perfectly alive. Only
    # treat it as dead once no thread is left.
    return state == "Z" and num_threads <= 1


def _safe_getppid() -> int:
    getppid = getattr(os, "getppid", None)
    if not callable(getppid):
        return 0
    try:
        return int(getppid())
    except Exception:
        return 0


class ParentDeathGuard:
    """Handle for an installed guard. Fires ``on_parent_death`` at most once."""

    def __init__(self, on_parent_death: Callable[[str], None], parent_pid: int):
        self._callback = on_parent_death
        self._parent_pid = parent_pid
        # The owner is usually our direct parent, but a generation handoff points
        # us at a *grandparent* instead: the outgoing process spawned us and is
        # about to exit on purpose. Mechanisms keyed on "our parent changed" are
        # only valid in the first case; the second must ask after the named pid.
        self._owner_is_direct_parent = (parent_pid == _safe_getppid())
        # Captured while the owner is known to be alive, so a later mismatch
        # means this pid now belongs to somebody else. Only consulted on the
        # handoff path; the direct-parent test reads getppid(), which the kernel
        # keeps honest.
        self._parent_start_token = (
            "" if self._owner_is_direct_parent else _posix_start_token(parent_pid)
        )
        self._fired = threading.Event()
        self._stop = threading.Event()
        self._fire_lock = threading.Lock()
        self._mechanisms: list[str] = []
        self._threads: list[threading.Thread] = []

    @property
    def parent_pid(self) -> int:
        return self._parent_pid

    @property
    def mechanisms(self) -> tuple[str, ...]:
        return tuple(self._mechanisms)

    @property
    def fired(self) -> bool:
        return self._fired.is_set()

    def _note(self, mechanism: str) -> None:
        self._mechanisms.append(mechanism)

    def fire(self, mechanism: str) -> None:
        """Report parent death. Safe to call from any thread, any number of times."""
        with self._fire_lock:
            if self._fired.is_set():
                return
            self._fired.set()
        self._stop.set()
        try:
            self._callback(mechanism)
        except Exception as exc:  # pragma: no cover - callback owns its errors
            print(f"[ParentGuard] parent-death callback failed: {exc}", flush=True)

    def stop(self) -> None:
        """Disarm the polling mechanisms (the kernel-level ones stay armed)."""
        self._stop.set()

    # -- mechanism installers --------------------------------------------

    @property
    def owner_is_direct_parent(self) -> bool:
        return self._owner_is_direct_parent

    def _owner_gone(self) -> bool:
        """Is the process we are watching actually gone? Conservative on doubt."""
        if self._owner_is_direct_parent:
            # Re-parenting is the signal: our parent died and init adopted us.
            return _safe_getppid() != self._parent_pid
        # Handoff generation: we can only ask whether the named pid is still
        # there. EPERM means it exists but is not ours, which is still alive;
        # anything unexpected is unknown, so we keep waiting.
        try:
            os.kill(self._parent_pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        if _posix_pid_is_zombie(self._parent_pid):
            return True
        # The pid answers, but a pid is not an identity: if the owner exited and
        # the number was recycled before this poll ran, we would be watching a
        # stranger forever. A start token that no longer matches the one taken
        # while the owner was alive says exactly that happened. An empty token on
        # either side means "cannot tell", which must not be read as death.
        if self._parent_start_token:
            current = _posix_start_token(self._parent_pid)
            if current and current != self._parent_start_token:
                return True
        return False

    def _on_pdeathsig(self, _signum, _frame) -> None:
        # PR_SET_PDEATHSIG follows the *thread* that created us, not the owner's
        # whole process. An owner that spawns the runtime from a short-lived
        # worker thread gets this signal when that thread exits, while the owner
        # itself is perfectly healthy — and firing then would tear down a live
        # runtime. Confirm before acting; if the owner really is gone this costs
        # one getppid(), and if it is not, the poll stays armed for the real
        # death.
        if not self._owner_gone():
            return
        self.fire("pdeathsig")

    def _install_pdeathsig(self) -> bool:
        if not sys.platform.startswith("linux"):
            return False
        # PR_SET_PDEATHSIG watches our *parent*. During a generation handoff that
        # is the outgoing process, whose exit is expected and must not tear the
        # replacement down with it.
        if not self._owner_is_direct_parent:
            return False

        sig = _owner_death_signal()
        if sig is None:
            return False

        # The handler must exist *before* the trap is armed. Every candidate
        # signal terminates the process by default, so arming first would leave
        # a window in which the owner's death kills us silently — which is
        # exactly the bug this ordering exists to prevent.
        try:
            previous = signal.signal(sig, self._on_pdeathsig)
        except (ValueError, OSError, RuntimeError):
            # Not the main thread, or the signal cannot be caught here. Refuse
            # to arm rather than arm a signal that would kill us uncaught; the
            # owner poll still covers this process.
            return False

        def _restore() -> None:
            # Arming failed, so nothing will ever send us this signal on the
            # owner's behalf — but our handler is still installed. Leaving it
            # there means an unrelated delivery of an otherwise unused signal
            # would run the owner-death teardown on a healthy runtime.
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError, RuntimeError, TypeError):
                pass

        try:
            libc = ctypes.CDLL(None, use_errno=True)
            prctl = libc.prctl
            prctl.argtypes = [
                ctypes.c_int,
                ctypes.c_ulong,
                ctypes.c_ulong,
                ctypes.c_ulong,
                ctypes.c_ulong,
            ]
            prctl.restype = ctypes.c_int
            if prctl(_PR_SET_PDEATHSIG, int(sig), 0, 0, 0) != 0:
                _restore()
                return False
        except Exception:
            _restore()
            return False

        self._note("pdeathsig")
        # PR_SET_PDEATHSIG only fires for deaths that happen *after* the call.
        # If the parent already died during startup the signal never comes, so
        # re-read the parent now that the trap is armed.
        if _safe_getppid() != self._parent_pid:
            self.fire("pdeathsig_late_install")
        return True

    def _install_stdin_eof(self, confirm_budget: float = DEFAULT_POLL_INTERVAL) -> bool:
        if os.name != "posix":
            return False
        try:
            mode = os.fstat(0).st_mode
        except OSError:
            return False
        # A pipe or socket on fd 0 means the parent holds the write end; its
        # death closes it and we see EOF. A tty, a regular file or /dev/null
        # would either never EOF or EOF immediately, so neither is armed.
        if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
            return False

        def _watch() -> None:
            while True:
                try:
                    chunk = os.read(0, 65536)
                except OSError:
                    return
                if not chunk:
                    # EOF is a hint, not proof. "fd 0 is a pipe, so the parent
                    # holds the write end" is only true if nobody else inherited
                    # it — a sibling that outlives the owner, or an owner that
                    # simply closes our stdin, produces the same EOF while the
                    # owner is alive. Firing on that would release the
                    # single-instance lock and SIGKILL our own process group out
                    # from under a healthy owner.
                    #
                    # Confirm with the same test the poll uses, bounded: the
                    # kernel closes the fd before it re-parents us, so on a real
                    # death getppid() lags EOF by a moment. If the budget expires
                    # with the owner still alive, this EOF was not its death —
                    # retire this mechanism and leave the owner poll on watch.
                    deadline = time.monotonic() + max(0.0, float(confirm_budget))
                    while True:
                        if self._owner_gone():
                            self.fire("stdin_eof")
                            return
                        if time.monotonic() >= deadline:
                            return
                        if self._stop.wait(0.05):
                            return

        thread = threading.Thread(target=_watch, name="neko-parent-stdin-eof", daemon=True)
        thread.start()
        self._threads.append(thread)
        self._note("stdin_eof")
        return True

    def _install_parent_handle(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

            handle = kernel32.OpenProcess(
                _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                self._parent_pid,
            )
            open_error = 0 if handle else ctypes.get_last_error()
        except Exception:
            return False

        if not handle:
            if open_error == _ERROR_INVALID_PARAMETER:
                # Windows reports a pid that matches no live process this way, so
                # the owner really is gone: nothing to wait on.
                self.fire("parent_handle_absent")
                return True
            # Any other error means we could not *look*, not that the owner is
            # absent — ERROR_ACCESS_DENIED is routine when the owner runs as a
            # different user, as SYSTEM, or inside an AppContainer. Treating that
            # as proof of death would kill a healthy runtime on startup. Same
            # discipline as the inconclusive verdict below: leave the mechanism
            # unarmed and report it honestly.
            print(
                f"[ParentGuard] Cannot open owner process {self._parent_pid} "
                f"(win32 error {open_error}); parent-handle mechanism not armed",
                flush=True,
            )
            return False

        verdict = _windows_parent_precedes_us(kernel32, handle)
        if verdict is False:
            # The pid was recycled after our real parent died — the process we
            # just opened started *after* we did, so it cannot be our parent.
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            self.fire("parent_handle_recycled")
            return True
        if verdict is None:
            # Inconclusive: waiting on a possibly-wrong handle could terminate a
            # healthy runtime, so leave this mechanism unarmed and say so.
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            return False

        def _watch() -> None:
            try:
                while not self._stop.is_set():
                    result = kernel32.WaitForSingleObject(handle, 1000)
                    if result == _WAIT_OBJECT_0:
                        self.fire("parent_handle")
                        return
                    if result != _WAIT_TIMEOUT:
                        return
            finally:
                try:
                    kernel32.CloseHandle(handle)
                except Exception:
                    # Best-effort close on the way out of the watcher thread: the
                    # handle dies with the process anyway, and raising here would
                    # only surface a traceback after fire() has already run.
                    pass

        thread = threading.Thread(target=_watch, name="neko-parent-handle", daemon=True)
        thread.start()
        self._threads.append(thread)
        self._note("parent_handle")
        return True

    def _install_owner_poll(self, interval: float) -> bool:
        if os.name != "posix":
            return False

        mechanism = "ppid_poll" if self._owner_is_direct_parent else "owner_poll"

        def _watch() -> None:
            while not self._stop.wait(interval):
                if self._owner_gone():
                    self.fire(mechanism)
                    return

        thread = threading.Thread(target=_watch, name="neko-owner-poll", daemon=True)
        thread.start()
        self._threads.append(thread)
        self._note(mechanism)
        return True


def _windows_parent_precedes_us(kernel32, parent_handle) -> Optional[bool]:
    """Did ``parent_handle``'s process start before this one?

    ``True``/``False`` are conclusive; ``None`` means the times could not be
    read and the caller must not draw a conclusion either way.
    """

    class _FILETIME(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    def _creation_time(handle) -> Optional[int]:
        creation = _FILETIME()
        exited = _FILETIME()
        kernel_time = _FILETIME()
        user_time = _FILETIME()
        try:
            ok = kernel32.GetProcessTimes(
                ctypes.c_void_p(handle) if not isinstance(handle, ctypes.c_void_p) else handle,
                ctypes.byref(creation),
                ctypes.byref(exited),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            )
        except Exception:
            return None
        if not ok:
            return None
        return (creation.high << 32) | creation.low

    try:
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        own_handle = kernel32.GetCurrentProcess()
    except Exception:
        return None

    parent_created = _creation_time(parent_handle)
    own_created = _creation_time(own_handle)
    if parent_created is None or own_created is None:
        return None
    return parent_created <= own_created


def install(
    on_parent_death: Callable[[str], None],
    *,
    parent_pid: Optional[int] = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    watch_stdin: bool = True,
) -> ParentDeathGuard:
    """Arm every parent-death mechanism this platform supports.

    ``on_parent_death(mechanism)`` runs at most once, on a daemon thread or in a
    signal-adjacent context; it must be quick to start and must not assume it
    owns the main thread.
    """
    resolved_parent = parent_pid if parent_pid is not None else _configured_parent_pid()
    if resolved_parent is None:
        resolved_parent = _safe_getppid()

    guard = ParentDeathGuard(on_parent_death, int(resolved_parent))

    if not _guard_enabled():
        return guard

    # A parent pid of 0/1 means we are already orphaned (launchd, systemd, a
    # daemonized ancestor). There is nothing to watch, and polling would fire
    # spuriously the moment the value never changes.
    if guard.parent_pid <= 1:
        return guard

    guard._install_pdeathsig()
    if watch_stdin:
        guard._install_stdin_eof(poll_interval)
    guard._install_parent_handle()
    guard._install_owner_poll(poll_interval)
    return guard


def install_child_guard(expected_parent_pid: Optional[int] = None) -> bool:
    """Arm the zero-cost parent-death trap inside a launcher-managed child.

    Children are already covered by the launcher's ordered shutdown, by the
    Windows Job Object and by the process-group sweep. This adds the one
    mechanism that costs nothing and needs no thread, so a launcher that dies
    without running any cleanup still cannot leave servers behind on Linux.

    ``expected_parent_pid`` is the launcher's pid as recorded *before* the fork.
    ``PR_SET_PDEATHSIG`` only reports deaths that happen after it is armed, so a
    launcher that dies in the window between forking us and this call would arm
    the trap against our adopter — which never dies — and the server would run
    on holding its port. Comparing against a pid captured before the fork closes
    the whole window; re-reading it here would not, because both reads would
    already return the adopter.
    """
    if not _guard_enabled() or not sys.platform.startswith("linux"):
        return False

    # A forked child inherits the launcher's handler for the owner-death signal,
    # still closed over the launcher's owner pid. Nothing in this runtime sends
    # that signal, so this is hygiene rather than a live defect — but a stray
    # delivery would run an owner-death teardown inside a server process.
    # SIG_IGN, not SIG_DFL: the default action for these signals is termination,
    # which would trade "wrong teardown" for "killed with no cleanup at all".
    inherited = _owner_death_signal()
    if inherited is not None:
        try:
            signal.signal(inherited, signal.SIG_IGN)
        except (ValueError, OSError, RuntimeError):
            # Not the main thread, or the signal cannot be set here; the stale
            # handler is inert anyway since nothing sends it.
            pass

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        prctl.restype = ctypes.c_int
        if prctl(_PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0) != 0:
            return False
    except Exception:
        return False

    # Assumes our parent *is* the launcher, which holds for the "fork" and
    # "spawn" start methods. Under "forkserver" the parent is the fork server,
    # so this would fire for every child; main_server forces "fork" on POSIX and
    # the project pins Python 3.11, where "fork" is also the Linux default.
    if expected_parent_pid and _safe_getppid() != int(expected_parent_pid):
        # The launcher died before we armed. Deliver the signal the trap would
        # have delivered, so this takes the same path a real late death takes.
        signal.raise_signal(signal.SIGTERM)
    return True
