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


def _guard_enabled() -> bool:
    raw = os.environ.get(PARENT_GUARD_ENV, "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _configured_parent_pid() -> Optional[int]:
    raw = os.environ.get(PARENT_PID_ENV, "").strip()
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if value > 1 else None


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

    def _on_pdeathsig(self, _signum, _frame) -> None:
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
            signal.signal(sig, self._on_pdeathsig)
        except (ValueError, OSError, RuntimeError):
            # Not the main thread, or the signal cannot be caught here. Refuse
            # to arm rather than arm a signal that would kill us uncaught; the
            # owner poll still covers this process.
            return False

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
                return False
        except Exception:
            return False

        self._note("pdeathsig")
        # PR_SET_PDEATHSIG only fires for deaths that happen *after* the call.
        # If the parent already died during startup the signal never comes, so
        # re-read the parent now that the trap is armed.
        if _safe_getppid() != self._parent_pid:
            self.fire("pdeathsig_late_install")
        return True

    def _install_stdin_eof(self) -> bool:
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
                    self.fire("stdin_eof")
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
        except Exception:
            return False

        if not handle:
            # The pid is already gone: there is nothing to wait on and the owner
            # is definitively absent.
            self.fire("parent_handle_absent")
            return True

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
                    pass

        thread = threading.Thread(target=_watch, name="neko-parent-handle", daemon=True)
        thread.start()
        self._threads.append(thread)
        self._note("parent_handle")
        return True

    def _install_owner_poll(self, interval: float) -> bool:
        if os.name != "posix":
            return False

        if self._owner_is_direct_parent:
            mechanism = "ppid_poll"

            def _owner_gone() -> bool:
                # Re-parenting is the signal: our parent died and init adopted us.
                return _safe_getppid() != self._parent_pid
        else:
            mechanism = "owner_poll"

            def _owner_gone() -> bool:
                # Handoff generation: we can only ask whether the named pid is
                # still there. EPERM means it exists but is not ours, which is
                # still alive; anything unexpected is unknown, so we keep waiting.
                try:
                    os.kill(self._parent_pid, 0)
                except ProcessLookupError:
                    return True
                except OSError:
                    return False
                return False

        def _watch() -> None:
            while not self._stop.wait(interval):
                if _owner_gone():
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
        guard._install_stdin_eof()
    guard._install_parent_handle()
    guard._install_owner_poll(poll_interval)
    return guard


def install_child_guard() -> bool:
    """Arm the zero-cost parent-death trap inside a launcher-managed child.

    Children are already covered by the launcher's ordered shutdown, by the
    Windows Job Object and by the process-group sweep. This adds the one
    mechanism that costs nothing and needs no thread, so a launcher that dies
    without running any cleanup still cannot leave servers behind on Linux.
    """
    if not _guard_enabled() or not sys.platform.startswith("linux"):
        return False
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
        return prctl(_PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0) == 0
    except Exception:
        return False
