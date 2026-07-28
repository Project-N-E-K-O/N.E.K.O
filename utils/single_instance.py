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

"""Single-instance self-proof for the N.E.K.O runtime.

The launcher used to let *observers* work out whether a runtime was already
alive: probe three default ports, demand a ``/health`` response on each, and
require all three to report the same ``instance_id``.  That is discovery-based
arbitration — it can only ever be a guess, it races with a runtime that is
half-way through binding its ports, and it forces every consumer (the Electron
shell most of all) to reimplement the same fragile inference.

This module replaces the inference with a *proof*:

* **Uniqueness** is held by an OS file lock (``flock`` on POSIX,
  ``msvcrt.locking`` on Windows) that is taken for the whole process lifetime.
  The kernel releases it when the holding process dies, however it dies, so
  there is no stale-lock recovery path to get wrong.
* **Identity** is published by the lock holder into a sibling JSON record:
  pid, parent pid, instance id, launch id, executable, negotiated ports.
  A reader never has to guess any of it.
* **Liveness of the record** is proven by the lock, not by the record: a record
  whose lock can be taken is by definition stale.  ``owner_status()`` returns
  ``owned`` / ``free`` / ``unknown`` and never collapses ``unknown`` into either
  of the other two.

Consumers that cannot take a POSIX/Windows file lock (notably the Electron
shell, which speaks Node) get the same proof in two cheaper forms: the record
carries ``parent_pid``, so an owner recognises its own runtime by comparing it
against its own pid, and the launcher mirrors the record onto stdout as a
``NEKO_EVENT`` so the normal spawn path needs no file read at all.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

RECORD_SCHEMA_VERSION = 1

#: Directory override, mainly for tests and for sandboxed/portable installs.
RUNTIME_STATE_DIR_ENV = "NEKO_RUNTIME_STATE_DIR"

LOCK_FILE_NAME = "launcher.lock"
RECORD_FILE_NAME = "launcher.json"

#: Record states, in the order they are reached.
STATE_STARTING = "starting"
STATE_READY = "ready"

#: ``owner_status()`` verdicts. ``unknown`` means the filesystem could not be
#: consulted — it must never be treated as either of the other two.
OWNER_OWNED = "owned"
OWNER_FREE = "free"
OWNER_UNKNOWN = "unknown"

_state_lock = threading.Lock()
_active_handle: "SingleInstanceHandle | None" = None


def runtime_state_dir() -> Path:
    """Return the per-user directory holding the lock and the record.

    Deliberately *not* the shared system temp dir by default: on a multi-user
    Linux box ``/tmp/neko_launcher.lock`` created by the first user is not
    writable by the second, which would make the second user's launcher
    conclude "another instance is running" forever.
    """
    override = os.environ.get(RUNTIME_STATE_DIR_ENV, "").strip()
    if override:
        return Path(override)

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "N.E.K.O" / "runtime"
        return Path(tempfile.gettempdir()) / "N.E.K.O" / "runtime"

    if sys.platform == "darwin":
        try:
            return Path.home() / "Library" / "Application Support" / "N.E.K.O" / "runtime"
        except Exception:
            pass
    else:
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if xdg_runtime:
            return Path(xdg_runtime) / "neko"

    suffix = ""
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        try:
            suffix = f"-{getuid()}"
        except Exception:
            suffix = ""
    return Path(tempfile.gettempdir()) / f"neko-runtime{suffix}"


def lock_path() -> Path:
    return runtime_state_dir() / LOCK_FILE_NAME


def record_path() -> Path:
    return runtime_state_dir() / RECORD_FILE_NAME


# ---------------------------------------------------------------------------
#  Platform lock primitives
# ---------------------------------------------------------------------------

class LockHeldByAnother(Exception):
    """Somebody else holds the lock — a conclusive answer, not an error."""


#: Errnos that mean "the lock is taken". Everything else is a real failure and
#: must stay ``unknown``: reporting ENOLCK (no locks available on the fs) or
#: EBADF as "another instance is running" would refuse to start for a reason the
#: user cannot see or clear.
_CONTENTION_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    errno.EWOULDBLOCK,
    getattr(errno, "EDEADLK", 35),
    getattr(errno, "EDEADLOCK", 36),
}


def _try_lock_fd(fd: int) -> None:
    """Take an exclusive, non-blocking lock on ``fd``.

    Returns normally when the lock is held. Raises :class:`LockHeldByAnother`
    when somebody else holds it, and ``OSError`` when the platform could not
    answer at all — the caller must keep those two apart.
    """
    try:
        if sys.platform == "win32":
            import msvcrt

            # Byte 0 only. Windows allows locking a range past EOF, so this
            # works on a freshly created empty file. The lock is dropped by the
            # kernel when the handle closes, including on abnormal termination.
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return

        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in _CONTENTION_ERRNOS:
            raise LockHeldByAnother() from exc
        raise


def _unlock_fd(fd: int) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        # Closing the descriptor releases the lock anyway; an explicit unlock
        # failure must not stop the caller from closing it.
        pass


def _open_lock_file(path: Path) -> int:
    # Owner-only, and it matters more than it looks. The POSIX fallback location
    # is a predictable path under the shared temp dir, and mkdir's 0o777 minus
    # whatever umask happens to be set can leave it writable by other local
    # users. The lock file's own 0o600 does not help there: anyone who can write
    # the *directory* can unlink launcher.lock while it is held and drop a fresh
    # inode in its place, after which two launchers hold two different locks and
    # the uniqueness proof is silently gone.
    _ensure_private_dir(path.parent)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0)
    return os.open(str(path), flags, 0o600)


def _ensure_private_dir(directory: Path) -> None:
    """Create ``directory`` owner-only, and refuse a pre-existing loose one."""
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    except FileExistsError:
        pass
    if os.name != "posix":
        # Windows inherits ACLs from %LOCALAPPDATA%, which is already per-user.
        return
    try:
        info = os.stat(str(directory))
    except OSError:
        return
    getuid = getattr(os, "getuid", None)
    if callable(getuid) and info.st_uid != getuid():
        raise OSError(
            errno.EPERM,
            f"runtime state directory {directory} is owned by uid {info.st_uid}, not us",
        )
    # mkdir's mode is masked by umask, and an existing directory keeps whatever
    # mode it already had, so tighten explicitly rather than trusting either.
    if info.st_mode & 0o077:
        try:
            os.chmod(str(directory), 0o700)
        except OSError:
            # Cannot tighten it; callers treat an OSError here as "unknown",
            # which is the honest answer — not "nobody else is running".
            raise


# ---------------------------------------------------------------------------
#  Process start time (lets an observer close the pid-reuse window)
# ---------------------------------------------------------------------------

def _process_start_token(pid: int) -> str:
    """Return a cheap, self-describing process start token, or ``""``.

    Linux exposes the value directly; elsewhere we fall back to an empty token
    rather than shelling out, because the record already carries ``parent_pid``
    and the lock already proves liveness.  Consumers treat ``""`` as "not
    available", never as "matches".
    """
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                raw = handle.read().decode("utf-8", errors="replace")
            closing = raw.rfind(") ")
            if closing < 0:
                return ""
            fields = raw[closing + 2:].split()
            # starttime is field 22 of /proc/pid/stat, i.e. index 19 after the
            # comm field has been stripped.
            start_ticks = fields[19]
            boot_id = ""
            try:
                with open("/proc/sys/kernel/random/boot_id", "rb") as handle:
                    boot_id = handle.read().decode("ascii", errors="replace").strip()
            except OSError:
                boot_id = ""
            return f"{boot_id}:{start_ticks}" if boot_id else ""
        except (OSError, IndexError, ValueError):
            return ""
    return ""


def _executable_path() -> str:
    try:
        return os.path.abspath(sys.executable or "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
#  Handle
# ---------------------------------------------------------------------------

class SingleInstanceHandle:
    """The proof of uniqueness, held for the owning process's whole lifetime."""

    def __init__(self, fd: int, lock_file: Path, record_file: Path, record: dict):
        self._fd: Optional[int] = fd
        self._lock_file = lock_file
        self._record_file = record_file
        self._record = dict(record)
        self._publish_lock = threading.Lock()

    # -- introspection ----------------------------------------------------

    @property
    def lock_file(self) -> Path:
        return self._lock_file

    @property
    def record_file(self) -> Path:
        return self._record_file

    @property
    def held(self) -> bool:
        return self._fd is not None

    def record(self) -> dict:
        return dict(self._record)

    # -- mutation ---------------------------------------------------------

    def publish(self, **fields: Any) -> dict:
        """Merge ``fields`` into the record and rewrite it atomically."""
        with self._publish_lock:
            if self._fd is None:
                raise RuntimeError("single-instance lock is no longer held")
            self._record.update(fields)
            self._record["updated_at"] = _utc_now()
            snapshot = dict(self._record)
        _write_record_atomically(self._record_file, snapshot)
        return snapshot

    def release(self) -> None:
        """Drop the record and the lock. Idempotent."""
        global _active_handle

        with self._publish_lock:
            fd = self._fd
            self._fd = None
        if fd is None:
            return

        # Remove the record *before* dropping the lock: while the lock is still
        # held nobody else can be publishing, so this cannot delete a successor's
        # record. Doing it the other way round would race with the next winner.
        try:
            os.unlink(str(self._record_file))
        except OSError:
            pass

        _unlock_fd(fd)
        try:
            os.close(fd)
        except OSError:
            # The lock is already gone via _unlock_fd above, so a failed close
            # leaks nothing, and release() has to stay idempotent.
            pass

        with _state_lock:
            if _active_handle is self:
                _active_handle = None

    def __enter__(self) -> "SingleInstanceHandle":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.release()


# ---------------------------------------------------------------------------
#  Record IO
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_record_atomically(target: Path, payload: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".launcher-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(target))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            # Best-effort removal of the temp file; the write error re-raised
            # below is the one worth surfacing.
            pass
        raise


def _read_record(target: Path) -> Optional[dict]:
    try:
        with open(str(target), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def acquire_single_instance(
    *,
    instance_id: str,
    launch_id: str = "",
    ports: dict | None = None,
    internal_ports: dict | None = None,
    extra: dict | None = None,
    retries: int = 0,
    retry_interval: float = 0.25,
) -> Optional[SingleInstanceHandle]:
    """Take the single-instance lock and publish the starting record.

    Returns the handle on success and ``None`` when another live runtime holds
    the lock.  ``retries`` exists for the generation handoff during a storage
    restart, where the outgoing launcher releases the lock moments before its
    replacement asks for it.

    Failing to *reach* the lock file (permissions, read-only home) is not
    evidence that another instance is running, so it is reported by raising
    ``OSError``; the caller decides whether to fail open.
    """
    global _active_handle

    with _state_lock:
        if _active_handle is not None and _active_handle.held:
            return _active_handle

    lock_file = lock_path()
    record_file = record_path()
    fd = _open_lock_file(lock_file)

    attempt = 0
    while True:
        try:
            _try_lock_fd(fd)
            break
        except LockHeldByAnother:
            attempt += 1
            if attempt > max(0, int(retries)):
                try:
                    os.close(fd)
                except OSError:
                    # We never took the lock, so there is nothing to release; a
                    # failed close must not turn "somebody else won" into an
                    # exception.
                    pass
                return None
            time.sleep(max(0.0, float(retry_interval)))
        except OSError:
            # Could not consult the lock at all. Propagate so the caller can
            # treat it as unknown rather than as "another instance is running".
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    # The lock is ours now, so any record still on disk can only have been left
    # by a dead predecessor: release() deletes the record *before* dropping the
    # lock, so a live holder's record can never be visible to us here. Drop it
    # immediately rather than between here and the publish below, where an
    # observer that sees contention would read the predecessor's identity and
    # believe it belongs to the process it just lost to.
    try:
        os.unlink(str(record_file))
    except OSError:
        # Nothing to clear (the usual case), or the directory is not writable —
        # the publish below will report that failure on its own terms.
        pass

    pid = os.getpid()
    record = {
        "schema": RECORD_SCHEMA_VERSION,
        "state": STATE_STARTING,
        "app": "N.E.K.O",
        "instance_id": str(instance_id or ""),
        "launch_id": str(launch_id or ""),
        "pid": pid,
        "parent_pid": _safe_getppid(),
        "executable": _executable_path(),
        "start_token": _process_start_token(pid),
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "ports": dict(ports or {}),
        "internal_ports": dict(internal_ports or {}),
    }
    if extra:
        record.update({k: v for k, v in extra.items() if k not in ("pid", "schema")})

    # The lock file itself carries the pid so `cat` during triage is useful; the
    # authoritative payload lives in the sibling JSON.
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"{pid}\n".encode("ascii"))
    except OSError:
        # Purely a triage convenience. The lock proves uniqueness and the JSON
        # record is the authoritative payload, so failing to stamp the pid here
        # costs nothing that matters.
        pass

    handle = SingleInstanceHandle(fd, lock_file, record_file, record)
    try:
        _write_record_atomically(record_file, record)
    except OSError:
        # The lock is what guarantees uniqueness; an unwritable record only
        # costs observers their shortcut, so keep running.
        pass

    with _state_lock:
        _active_handle = handle
    return handle


def _safe_getppid() -> int:
    getppid = getattr(os, "getppid", None)
    if not callable(getppid):
        return 0
    try:
        return int(getppid())
    except Exception:
        return 0


def owner_status() -> tuple[str, Optional[dict]]:
    """Three-valued answer to "is a N.E.K.O runtime already running?".

    * ``("owned", record)``  — somebody holds the lock. ``record`` may be
      ``None`` if the holder has not published yet.
    * ``("free", None)``     — nobody holds the lock; any record on disk is
      stale and is ignored.
    * ``("unknown", None)``  — the lock could not be consulted at all.
    """
    with _state_lock:
        handle = _active_handle
    if handle is not None and handle.held:
        return OWNER_OWNED, handle.record()

    lock_file = lock_path()
    try:
        fd = _open_lock_file(lock_file)
    except OSError:
        return OWNER_UNKNOWN, None

    try:
        try:
            _try_lock_fd(fd)
        except LockHeldByAnother:
            return OWNER_OWNED, _read_record(record_path())
        except OSError:
            return OWNER_UNKNOWN, None
        # We took it, so nobody else holds it. Release immediately — this probe
        # must not become an accidental acquisition.
        _unlock_fd(fd)
        return OWNER_FREE, None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def read_owner_record() -> Optional[dict]:
    """Return the live owner's record, or ``None`` when there is no live owner."""
    status, record = owner_status()
    return record if status == OWNER_OWNED else None


def active_handle() -> Optional[SingleInstanceHandle]:
    with _state_lock:
        return _active_handle


def release_single_instance() -> None:
    """Release the process-wide handle, if this process holds one."""
    with _state_lock:
        handle = _active_handle
    if handle is not None:
        handle.release()


def drop_inherited_reference() -> None:
    """Forget an inherited lock handle inside a ``fork``ed child.

    A forked child inherits both the module state and the lock file descriptor.
    Left alone it could (a) keep the lock alive after the real holder is gone and
    (b) delete the record on its own exit path. Closing the child's descriptor
    does neither harm nor good to the holder: POSIX ``flock`` and Windows byte
    locks live on the open file *description*, which stays locked while the
    holder's own descriptor is open.
    """
    global _active_handle

    with _state_lock:
        handle = _active_handle
        _active_handle = None
    if handle is None:
        return
    fd = handle._fd
    handle._fd = None
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            # Dropping an inherited reference: the real holder still owns the
            # lock, and a failed close in this child changes nothing for it.
            pass
