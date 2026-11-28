"""Read child output without waiting for inherited pipe handles to close."""

from __future__ import annotations

import codecs
import os
import selectors
import subprocess
import time
from collections.abc import Callable, Iterator

_CHUNK_BYTES = 65536
_POLL_SECONDS = 0.05


def _windows_pipe_reader(fd: int) -> tuple[Callable[[int], bytes | None], Callable[[], int]]:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    peek = kernel.PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD]
    peek.restype = wintypes.BOOL
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))

    def buffered_bytes() -> int:
        available = wintypes.DWORD()
        if not peek(handle, None, 0, None, ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error in (109, 232):  # broken pipe / closing pipe
                return 0
            raise ctypes.WinError(error)
        return available.value

    def read_ready(limit: int) -> bytes | None:
        available = buffered_bytes()
        if available:
            return os.read(fd, min(available, limit))
        time.sleep(_POLL_SECONDS)
        return None

    return read_ready, buffered_bytes


def _posix_buffered_bytes(fd: int) -> int:
    import array
    import fcntl
    import termios

    pending = array.array("i", [0])
    fcntl.ioctl(fd, termios.FIONREAD, pending, True)
    return pending[0]


def iter_process_output(
    process: subprocess.Popen[str], *, checkpoint: Callable[[], None],
) -> Iterator[str]:
    """Yield UTF-8 chunks while the child runs, draining ready bytes at exit.

    Only the direct command owns this protocol. A detached helper inheriting
    its stdout cannot keep the caller waiting after that command has exited.
    Polling also lets cancellation and deadlines retire a silent live child.
    """
    assert process.stdout is not None
    fd = process.stdout.fileno()
    selector = None
    if os.name == "nt":
        read_ready, buffered_bytes = _windows_pipe_reader(fd)
    else:
        selector = selectors.DefaultSelector()
        selector.register(fd, selectors.EVENT_READ)

        def read_ready(limit: int) -> bytes | None:
            if selector.select(_POLL_SECONDS):
                return os.read(fd, limit)
            return None

        buffered_bytes = lambda: _posix_buffered_bytes(fd)

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    remaining: int | None = None
    try:
        while True:
            checkpoint()
            if remaining is None and process.poll() is not None:
                # Freeze the ready-byte count once. Pipe capacity may exceed a
                # chunk, while later writes from helpers must not extend it.
                remaining = buffered_bytes()
                if not remaining:
                    break
            chunk = read_ready(_CHUNK_BYTES if remaining is None else min(_CHUNK_BYTES, remaining))
            if chunk is None:
                continue
            if not chunk:
                break
            yield decoder.decode(chunk)
            if remaining is not None:
                remaining -= len(chunk)
                if not remaining:
                    break
        tail = decoder.decode(b"", final=True)
        if tail:
            yield tail
    finally:
        if selector is not None:
            selector.close()
