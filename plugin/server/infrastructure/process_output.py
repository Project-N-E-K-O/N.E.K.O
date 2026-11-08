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


def _windows_pipe_reader(fd: int) -> Callable[[], bytes | None]:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    peek = kernel.PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD]
    peek.restype = wintypes.BOOL
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))

    def read_ready() -> bytes | None:
        available = wintypes.DWORD()
        if not peek(handle, None, 0, None, ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error in (109, 232):  # broken pipe / closing pipe
                return b""
            raise ctypes.WinError(error)
        if available.value:
            return os.read(fd, min(available.value, _CHUNK_BYTES))
        time.sleep(_POLL_SECONDS)
        return None

    return read_ready


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
        read_ready = _windows_pipe_reader(fd)
    else:
        selector = selectors.DefaultSelector()
        selector.register(fd, selectors.EVENT_READ)

        def read_ready() -> bytes | None:
            if selector.select(_POLL_SECONDS):
                return os.read(fd, _CHUNK_BYTES)
            return None

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while True:
            checkpoint()
            exited = process.poll() is not None
            chunk = read_ready()
            if chunk is None:
                if exited:
                    break
                # If the child exited during this wait, peek again after the
                # next poll observes exit so its final bytes are not lost.
                continue
            if not chunk:
                break
            yield decoder.decode(chunk)
            # Drain at most one bounded chunk after observing exit. A helper
            # that writes continuously must not extend the command lifetime.
            if exited:
                break
        tail = decoder.decode(b"", final=True)
        if tail:
            yield tail
    finally:
        if selector is not None:
            selector.close()
