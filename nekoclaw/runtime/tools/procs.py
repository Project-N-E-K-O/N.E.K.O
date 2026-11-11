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

"""Shell processes whose whole process tree can be stopped.

A command started here is put in a Job Object on Windows and in a new session
(its own process group) on POSIX, so stopping it also stops whatever it
started. The process is created suspended on Windows and resumed only after it
joined the job, which leaves no window in which a child could escape.

Pipes are read by threads rather than through the event loop: that works on
every loop implementation the host may run and keeps file writes for spilled
output off the loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from utils import process_creation

logger = logging.getLogger(__name__)

# How long output is still collected after the shell itself exited: a detached
# grandchild may keep the pipe open indefinitely.
_DRAIN_GRACE_SECONDS = 1.0
# POSIX: time between asking the group to terminate and killing it.
KILL_GRACE_SECONDS = 3.0
MAX_SPILL_BYTES = 64 * 1024 * 1024
_READ_CHUNK = 65536

_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_SUSPEND_RESUME = 0x0800


def _incomplete_suffix(data: bytes) -> int:
    """Return how many trailing bytes belong to an unfinished UTF-8 character."""
    for back in range(1, min(4, len(data)) + 1):
        byte = data[-back]
        if byte & 0xC0 == 0x80:
            continue
        if byte & 0x80 == 0:
            return 0
        if byte & 0xE0 == 0xC0:
            needed = 2
        elif byte & 0xF0 == 0xE0:
            needed = 3
        elif byte & 0xF8 == 0xF0:
            needed = 4
        else:
            return 0
        return back if back < needed else 0
    return 0


class OutputBuffer:
    """Tail of one output stream, addressed by absolute byte offsets.

    At most ``max_bytes`` are kept in memory. From the moment older bytes
    would be dropped, the complete stream is also written to a spill file so
    nothing is lost. Safe to feed from a reader thread while the loop reads.
    """

    def __init__(
        self,
        max_bytes: int,
        reserve_spill: Callable[[], Path | None],
        max_spill_bytes: int = MAX_SPILL_BYTES,
    ) -> None:
        self._max_bytes = max_bytes
        self._reserve_spill = reserve_spill
        self._max_spill_bytes = max_spill_bytes
        self._lock = threading.Lock()
        self._tail = bytearray()
        self._start = 0
        self._closed = False
        self._spill_file = None
        self._spill_written = 0
        self.total = 0
        self.spill_path: Path | None = None

    def feed(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            if self._spill_file is None and self.spill_path is None:
                if len(self._tail) + len(data) > self._max_bytes:
                    self._open_spill()
            self._write_spill(data)
            self._tail += data
            self.total += len(data)
            overflow = len(self._tail) - self._max_bytes
            if overflow > 0:
                del self._tail[:overflow]
                self._start += overflow

    def _open_spill(self) -> None:
        path = self._reserve_spill()
        if path is None:
            return
        try:
            self._spill_file = open(path, "wb")
        except OSError:
            self._spill_file = None
            return
        self.spill_path = path
        # Nothing has been dropped yet, so the tail is the whole stream so far.
        self._write_spill(bytes(self._tail))

    def _write_spill(self, data: bytes) -> None:
        if self._spill_file is None:
            return
        room = self._max_spill_bytes - self._spill_written
        if room <= 0:
            return
        try:
            self._spill_file.write(data[:room])
            self._spill_written += min(len(data), room)
        except OSError:
            self._close_spill()

    def _close_spill(self) -> None:
        if self._spill_file is not None:
            try:
                self._spill_file.close()
            except OSError:
                pass
            self._spill_file = None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._close_spill()

    def flush(self) -> None:
        with self._lock:
            if self._spill_file is not None:
                try:
                    self._spill_file.flush()
                except OSError:
                    pass

    def read_from(self, offset: int) -> tuple[str, int, bool]:
        """Return ``(text, next_offset, lossy)`` for everything from ``offset`` on.

        ``lossy`` is true when bytes at or after ``offset`` already left
        memory. While the stream is open, a trailing partial character is held
        back for the next read.
        """
        with self._lock:
            lossy = offset < self._start
            begin = max(offset, self._start)
            chunk = bytes(self._tail[begin - self._start:])
            closed = self._closed
        if lossy:
            # The cut may fall inside a character: drop its remaining bytes.
            skip = 0
            while skip < min(3, len(chunk)) and chunk[skip] & 0xC0 == 0x80:
                skip += 1
            chunk = chunk[skip:]
            begin += skip
        if not closed:
            held = _incomplete_suffix(chunk)
            if held:
                chunk = chunk[:-held]
        text = chunk.decode("utf-8", errors="replace").replace("\r\n", "\n")
        return text, begin + len(chunk), lossy


# ---------------------------------------------------------------------------
# Windows Job Object
# ---------------------------------------------------------------------------


class _WindowsJob:
    """A Job Object used only to terminate a command's process tree on request.

    It is not marked kill-on-close: a program the command launched on purpose
    (an editor, a browser) must outlive the shell that started it.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        self._kernel32 = kernel32
        self._ntdll = ntdll
        self._handle = kernel32.CreateJobObjectW(None, None)
        self._lock = threading.Lock()
        self.assigned = False

    def adopt_and_resume(self, pid: int) -> None:
        """Put the suspended process ``pid`` in the job, then let it run.

        Raises ``OSError`` when the process cannot be resumed; a failure to
        join the job is tolerated and leaves ``assigned`` false.
        """
        access = _PROCESS_TERMINATE | _PROCESS_SET_QUOTA | _PROCESS_SUSPEND_RESUME
        process = self._kernel32.OpenProcess(access, False, pid)
        if not process:
            raise OSError(self._ctypes.get_last_error(), "OpenProcess failed")
        try:
            if self._handle:
                self.assigned = bool(self._kernel32.AssignProcessToJobObject(self._handle, process))
            status = self._ntdll.NtResumeProcess(process)
            if status != 0:
                raise OSError(status, "NtResumeProcess failed")
        finally:
            self._kernel32.CloseHandle(process)

    def terminate(self) -> bool:
        with self._lock:
            if not self._handle or not self.assigned:
                return False
            return bool(self._kernel32.TerminateJobObject(self._handle, 1))

    def close(self) -> None:
        with self._lock:
            if self._handle:
                self._kernel32.CloseHandle(self._handle)
                self._handle = None


def _taskkill_tree(pid: int) -> None:
    """Fallback tree termination for a process that is in no job of ours."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------------------
# ShellProcess
# ---------------------------------------------------------------------------


class ShellProcess:
    """One running command with collected output and tree termination."""

    def __init__(
        self,
        popen: subprocess.Popen[bytes],
        stdout: OutputBuffer,
        stderr: OutputBuffer,
        job: _WindowsJob | None,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._popen = popen
        self._job = job
        self._loop = loop
        self._kill_lock = threading.Lock()
        self._kill_requested = False
        self.pid = popen.pid
        self.stdout = stdout
        self.stderr = stderr
        self.returncode: int | None = None
        self.finished = asyncio.Event()
        # Set whenever new output arrived; consumers clear it before waiting.
        self.activity = asyncio.Event()
        self._readers = [
            threading.Thread(
                target=self._pump, args=(popen.stdout, stdout), name="nekoclaw-stdout", daemon=True
            ),
            threading.Thread(
                target=self._pump, args=(popen.stderr, stderr), name="nekoclaw-stderr", daemon=True
            ),
        ]
        for reader in self._readers:
            reader.start()
        threading.Thread(target=self._reap, name="nekoclaw-reap", daemon=True).start()

    # -- threads ------------------------------------------------------------

    def _notify(self, event: asyncio.Event) -> None:
        try:
            self._loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            # The loop is closed: nobody is left to notify.
            pass

    def _pump(self, pipe: object, buffer: OutputBuffer) -> None:
        try:
            fileno = pipe.fileno()  # type: ignore[attr-defined]
            while True:
                data = os.read(fileno, _READ_CHUNK)
                if not data:
                    break
                buffer.feed(data)
                self._notify(self.activity)
        except (OSError, ValueError):
            pass

    def _reap(self) -> None:
        code = self._popen.wait()
        deadline = time.monotonic() + _DRAIN_GRACE_SECONDS
        for reader in self._readers:
            reader.join(max(0.0, deadline - time.monotonic()))
        for buffer in (self.stdout, self.stderr):
            buffer.close()
        for pipe in (self._popen.stdout, self._popen.stderr):
            try:
                if pipe is not None and not any(reader.is_alive() for reader in self._readers):
                    pipe.close()
            except OSError:
                pass
        if self._job is not None:
            self._job.close()
        self.returncode = code
        self._notify(self.activity)
        self._notify(self.finished)

    # -- state --------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.returncode is None

    @property
    def kill_requested(self) -> bool:
        return self._kill_requested

    @property
    def signal_name(self) -> str | None:
        """Name of the signal that ended the process (POSIX only)."""
        code = self.returncode
        if code is None or code >= 0 or sys.platform == "win32":
            return None
        try:
            return signal.Signals(-code).name
        except ValueError:
            return f"SIG{-code}"

    async def wait(self) -> None:
        await self.finished.wait()

    def kill(self) -> bool:
        """Stop the command and everything it started. Returns False if already done."""
        with self._kill_lock:
            if self.returncode is not None or self._kill_requested:
                return False
            self._kill_requested = True
        if sys.platform == "win32":
            if self._job is None or not self._job.terminate():
                threading.Thread(
                    target=_taskkill_tree, args=(self.pid,), name="nekoclaw-taskkill", daemon=True
                ).start()
            return True
        self._signal_group(signal.SIGTERM)
        escalate = threading.Timer(KILL_GRACE_SECONDS, self._signal_group, args=(signal.SIGKILL,))
        escalate.daemon = True
        escalate.start()
        return True

    def _signal_group(self, signum: int) -> None:
        if self.returncode is not None and signum != signal.SIGKILL:
            return
        try:
            os.killpg(self.pid, signum)  # type: ignore[attr-defined]
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _spawn_blocking(
    argv: Sequence[str],
    cwd: str,
    env: Mapping[str, str],
    stdout: OutputBuffer,
    stderr: OutputBuffer,
    loop: asyncio.AbstractEventLoop,
) -> ShellProcess:
    job: _WindowsJob | None = None
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        job = _WindowsJob()
        kwargs["creationflags"] = _CREATE_SUSPENDED | _CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        popen = process_creation.popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(env),
            **kwargs,
        )
    except BaseException:
        if job is not None:
            job.close()
        raise
    if job is not None:
        try:
            job.adopt_and_resume(popen.pid)
        except OSError:
            popen.kill()
            popen.wait()
            job.close()
            raise
    return ShellProcess(popen, stdout, stderr, job, loop)


async def spawn(
    argv: Sequence[str],
    *,
    cwd: str,
    env: Mapping[str, str],
    stdout: OutputBuffer,
    stderr: OutputBuffer,
) -> ShellProcess:
    """Start ``argv`` with no stdin and both output streams collected."""
    loop = asyncio.get_running_loop()
    return await asyncio.to_thread(_spawn_blocking, argv, cwd, env, stdout, stderr, loop)
