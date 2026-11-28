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

"""A connection to a browser in the Chrome DevTools Protocol.

One WebSocket to the browser's debugging endpoint. Commands are JSON messages
with an id; the answer carries the same id. Everything else the browser sends
is an event. Pages are reached through "flat" sessions on the same socket: a
command names the session it is for, and an event names the session it came
from.

Events are handed to listeners in the order they arrive, from the task that
reads the socket, so a listener must not block; it records what happened and
whoever waits for it is woken.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 10.0
# The code a browser answers with when the session a command names is gone.
SESSION_GONE_CODE = -32001
# A message this long (the tree of a large page, a screenshot) is parsed in a
# worker thread: parsing megabytes would hold the event loop up.
PARSE_IN_THREAD_BYTES = 256 * 1024
_PIPE_READ_CHUNK_BYTES = 64 * 1024

# Called with ``(method, params, session_id)`` for every event.
EventListener = Callable[[str, dict[str, Any], "str | None"], None]


def _win32_pipe_api() -> Any:
    import ctypes
    import ctypes.wintypes as wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.CancelIoEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class PipeTransport:
    """NUL-delimited JSON transport over Chromium's ``--remote-debugging-pipe`` handles.

    Implements the same minimal async interface (``send``, ``__aiter__``, ``close``)
    as a WebSocket client so ``CdpConnection`` drives both identically.
    """

    def __init__(
        self,
        read_handle: int,
        write_handle: int,
        *,
        is_win32_handle: bool | None = None,
        initial_buffer: bytes = b"",
    ) -> None:
        self._read_handle: int | None = int(read_handle)
        self._write_handle: int | None = int(write_handle)
        self._is_win32 = (sys.platform == "win32") if is_win32_handle is None else bool(is_win32_handle)
        self._initial_buffer = bytes(initial_buffer)
        self._closed = False
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[str | None] | None = None
        self._reader_thread: threading.Thread | None = None
        self._wakeup_r: int | None = None
        self._wakeup_w: int | None = None
        if not self._is_win32:
            self._wakeup_r, self._wakeup_w = os.pipe()
            if hasattr(os, "set_blocking"):
                try:
                    os.set_blocking(self._write_handle, False)
                except OSError:
                    pass

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._reader_thread is not None:
            return
        self._loop = loop
        self._queue = asyncio.Queue()
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name="nekopower-cdp-pipe-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def _enqueue(self, item: str | None) -> None:
        loop = self._loop
        queue = self._queue
        if loop is None or queue is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            pass

    def _emit_from_buffer(self, buf: bytearray) -> None:
        while True:
            nul = buf.find(0)
            if nul == -1:
                break
            raw = bytes(buf[:nul])
            del buf[: nul + 1]
            if raw:
                self._enqueue(raw.decode("utf-8", errors="replace"))

    def _read_loop(self) -> None:
        buf = bytearray(self._initial_buffer)
        self._initial_buffer = b""
        try:
            self._emit_from_buffer(buf)
            if self._is_win32:
                self._read_loop_win32(buf)
            else:
                self._read_loop_posix(buf)
        except Exception:  # noqa: BLE001 - pipe ended or was closed
            logger.debug("CDP pipe reader ended", exc_info=True)
        finally:
            self._close_read_handle()
            self._enqueue(None)

    def _read_loop_win32(self, buf: bytearray) -> None:
        import ctypes
        import ctypes.wintypes as wintypes

        kernel32 = _win32_pipe_api()
        chunk = ctypes.create_string_buffer(_PIPE_READ_CHUNK_BYTES)
        nread = wintypes.DWORD()
        while not self._closed:
            handle = self._read_handle
            if handle is None:
                break
            ok = kernel32.ReadFile(handle, chunk, _PIPE_READ_CHUNK_BYTES, ctypes.byref(nread), None)
            if not ok or nread.value == 0:
                break
            buf.extend(chunk.raw[: nread.value])
            self._emit_from_buffer(buf)

    def _read_loop_posix(self, buf: bytearray) -> None:
        import selectors

        rfd = self._read_handle
        wfd = self._wakeup_r
        if rfd is None:
            return
        try:
            with selectors.DefaultSelector() as sel:
                sel.register(rfd, selectors.EVENT_READ, "read")
                if wfd is not None:
                    sel.register(wfd, selectors.EVENT_READ, "wake")
                while not self._closed:
                    try:
                        events = sel.select()
                    except (OSError, ValueError):
                        break
                    tags = {key.data for key, _ in events}
                    if "wake" in tags:
                        break
                    if "read" not in tags:
                        continue
                    try:
                        data = os.read(rfd, _PIPE_READ_CHUNK_BYTES)
                    except OSError:
                        break
                    if not data:
                        break
                    buf.extend(data)
                    self._emit_from_buffer(buf)
        except (OSError, ValueError):
            return

    def write_sync(self, payload: bytes) -> None:
        """Write raw bytes to the browser pipe. Blocking; interruptible on close; thread-safe."""
        with self._write_lock:
            if self._closed or self._write_handle is None:
                raise BrokenPipeError("the browser pipe is closed")
            if self._is_win32:
                import ctypes
                import ctypes.wintypes as wintypes

                kernel32 = _win32_pipe_api()
                view = memoryview(payload)
                offset = 0
                written = wintypes.DWORD()
                while offset < len(view):
                    if self._closed or self._write_handle is None:
                        raise BrokenPipeError("the browser pipe is closed")
                    part = bytes(view[offset:])
                    ok = kernel32.WriteFile(
                        self._write_handle, part, len(part), ctypes.byref(written), None
                    )
                    if not ok or written.value == 0:
                        raise BrokenPipeError(f"pipe write failed (error {ctypes.get_last_error()})")
                    offset += written.value
            else:
                import selectors

                view = memoryview(payload)
                offset = 0
                handle = self._write_handle
                wfd = self._wakeup_r
                try:
                    with selectors.DefaultSelector() as sel:
                        sel.register(handle, selectors.EVENT_WRITE, "write")
                        if wfd is not None:
                            sel.register(wfd, selectors.EVENT_READ, "wake")
                        while offset < len(view):
                            if self._closed or self._write_handle is None:
                                raise BrokenPipeError("the browser pipe is closed")
                            try:
                                events = sel.select(0.2)
                            except (OSError, ValueError) as exc:
                                raise BrokenPipeError("the browser pipe is closed") from exc
                            tags = {key.data for key, _ in events}
                            if self._closed or "wake" in tags:
                                raise BrokenPipeError("the browser pipe is closed")
                            if "write" not in tags:
                                continue
                            try:
                                n = os.write(handle, view[offset:])
                            except BlockingIOError:
                                continue
                            if n <= 0:
                                raise BrokenPipeError("pipe write returned 0")
                            offset += n
                except (OSError, ValueError) as exc:
                    if isinstance(exc, BrokenPipeError):
                        raise
                    raise BrokenPipeError("the browser pipe is closed") from exc

    async def send(self, text: str) -> None:
        payload = text.encode("utf-8") + b"\x00"
        await asyncio.to_thread(self.write_sync, payload)

    def __aiter__(self) -> "PipeTransport":
        return self

    async def __anext__(self) -> str:
        if self._queue is None:
            self.start(asyncio.get_running_loop())
        assert self._queue is not None
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    def _cancel_pending_io(self) -> None:
        """Interrupt any blocked pipe read or write without waiting for locks."""
        if self._is_win32:
            api = _win32_pipe_api()
            for handle in (self._write_handle, self._read_handle):
                if handle is not None:
                    try:
                        api.CancelIoEx(handle, None)
                    except Exception:  # noqa: BLE001 - best effort cancellation
                        pass
        elif self._wakeup_w is not None:
            try:
                os.write(self._wakeup_w, b"\x00")
            except OSError:
                pass

    def _close_write_handle(self, *, timeout: float = 1.5) -> None:
        acquired = self._write_lock.acquire(timeout=timeout)
        if not acquired:
            # A stalled write did not yield in time; re-issue cancellation and avoid deadlocking cleanup.
            self._cancel_pending_io()
            return
        try:
            handle, self._write_handle = self._write_handle, None
            if handle is None:
                return
            try:
                if self._is_win32:
                    _win32_pipe_api().CloseHandle(handle)
                else:
                    os.close(handle)
            except OSError:
                pass
        finally:
            self._write_lock.release()

    def _close_read_handle(self) -> None:
        with self._close_lock:
            handle, self._read_handle = self._read_handle, None
            wr, self._wakeup_r = self._wakeup_r, None
            ww, self._wakeup_w = self._wakeup_w, None
        for fd in (wr, ww):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if handle is not None:
            try:
                if self._is_win32:
                    _win32_pipe_api().CloseHandle(handle)
                else:
                    os.close(handle)
            except OSError:
                pass

    def close_sync(self) -> None:
        """Close both ends of the pipe and stop the reader thread. Blocking; idempotent."""
        self._closed = True
        self._cancel_pending_io()
        self._close_write_handle(timeout=1.5)
        thread = self._reader_thread
        if thread is not None and thread.is_alive():
            self._cancel_pending_io()
            thread.join(timeout=1.5)
        if thread is None or not thread.is_alive():
            self._close_read_handle()
        self._enqueue(None)

    async def close(self) -> None:
        await asyncio.to_thread(self.close_sync)


class CdpError(Exception):
    """The browser answered a command with an error."""

    def __init__(self, method: str, code: int, message: str) -> None:
        super().__init__(f"{method}: {message}")
        self.method = method
        self.code = code
        self.message = message


class CdpClosed(Exception):
    """The connection to the browser is gone."""


class CdpTimeout(Exception):
    """The browser did not answer a command in time."""


class CdpConnection:
    """One open connection. Create it with ``connect``."""

    def __init__(self, socket: Any) -> None:
        self._socket = socket
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        # command id -> the session it was sent to, for the commands sent to one.
        self._pending_sessions: dict[int, str] = {}
        self._listeners: list[EventListener] = []
        self._closed = False
        self._close_listeners: list[Callable[[], None]] = []
        self._reader = asyncio.ensure_future(self._read())

    @classmethod
    async def connect(cls, url: str, *, open_timeout: float = CONNECT_TIMEOUT_SECONDS) -> "CdpConnection":
        """Open a connection to a debugging endpoint (``ws://127.0.0.1:port/devtools/browser/...``)."""
        from websockets.asyncio.client import connect

        socket = await connect(
            url,
            # Screenshots and large pages come back as single messages.
            max_size=None,
            open_timeout=open_timeout,
            # The endpoint is on this computer: no proxy, and no keepalive
            # pings, which a browser paused in a dialog answers late.
            proxy=None,
            ping_interval=None,
            compression=None,
        )
        return cls(socket)

    @classmethod
    async def connect_pipe(cls, transport: PipeTransport) -> "CdpConnection":
        """Wrap an already-open ``PipeTransport`` from ``--remote-debugging-pipe``."""
        transport.start(asyncio.get_running_loop())
        return cls(transport)

    @property
    def closed(self) -> bool:
        return self._closed

    def on_event(self, listener: EventListener) -> Callable[[], None]:
        """Call ``listener`` for every event; returns a callable that removes it."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def on_close(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` once when the connection is gone."""
        if self._closed:
            listener()
        else:
            self._close_listeners.append(listener)

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Send one command and return its result.

        Raises ``CdpError`` when the browser refuses it, ``CdpTimeout`` when
        no answer comes in time and ``CdpClosed`` when the connection is gone.
        """
        if self._closed:
            raise CdpClosed("the connection to the browser is closed")
        self._next_id += 1
        command_id = self._next_id
        message: dict[str, Any] = {"id": command_id, "method": method}
        if params:
            message["params"] = params
        if session_id:
            message["sessionId"] = session_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        if session_id:
            self._pending_sessions[command_id] = session_id
        # One time for the whole command. Sending waits while the socket's
        # buffer is full, which it stays for as long as the browser reads
        # nothing: that wait counts, or a browser that has stopped would be
        # waited for without end.
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        sending = asyncio.timeout(timeout)
        try:
            try:
                async with sending:
                    await self._socket.send(json.dumps(message, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001 - whatever the socket raises, the connection is unusable
                if sending.expired():
                    # What was written stays in the buffer and reaches the
                    # browser once it reads again, like a command it was
                    # sent and did not answer in time.
                    raise CdpTimeout(f"{method} could not be sent within {timeout:.0f}s") from exc
                self._gone()
                raise CdpClosed(f"the connection to the browser broke: {type(exc).__name__}") from exc
            try:
                answer = await asyncio.wait_for(future, None if deadline is None else max(0.0, deadline - loop.time()))
            except asyncio.TimeoutError as exc:
                raise CdpTimeout(f"{method} got no answer within {timeout:.0f}s") from exc
        finally:
            self._pending.pop(command_id, None)
            self._pending_sessions.pop(command_id, None)
        error = answer.get("error")
        if error:
            raise CdpError(method, int(error.get("code") or 0), str(error.get("message") or "error"))
        result = answer.get("result")
        return result if isinstance(result, dict) else {}

    def fail_session(self, session_id: str) -> None:
        """Fail the commands still waiting for an answer from a session that is gone.

        Once the page of a session has closed the browser no longer answers
        what was sent to it; without this those commands would wait out
        their time. They fail with ``CdpError`` (``SESSION_GONE_CODE``).
        """
        for command_id, owner in list(self._pending_sessions.items()):
            if owner != session_id:
                continue
            future = self._pending.get(command_id)
            if future is not None and not future.done():
                future.set_result({"error": {"code": SESSION_GONE_CODE, "message": "Session with given id not found."}})

    async def _read(self) -> None:
        try:
            async for raw in self._socket:
                try:
                    if len(raw) >= PARSE_IN_THREAD_BYTES:
                        # Awaited before the next message is read, so the order stays.
                        message = await asyncio.to_thread(json.loads, raw)
                    else:
                        message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                command_id = message.get("id")
                if isinstance(command_id, int):
                    future = self._pending.get(command_id)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                method = message.get("method")
                if not isinstance(method, str):
                    continue
                params = message.get("params")
                session_id = message.get("sessionId")
                for listener in list(self._listeners):
                    try:
                        listener(method, params if isinstance(params, dict) else {}, session_id)
                    except Exception:  # noqa: BLE001 - one listener must not stop the others
                        logger.exception("a browser event listener failed on %s", method)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the socket ended; everyone waiting is told
            logger.debug("the browser connection ended: %s", type(exc).__name__)
        finally:
            self._gone()

    def _gone(self) -> None:
        if self._closed:
            return
        self._closed = True
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(CdpClosed("the connection to the browser is closed"))
        self._pending.clear()
        self._pending_sessions.clear()
        listeners, self._close_listeners = self._close_listeners, []
        for listener in listeners:
            try:
                listener()
            except Exception:  # noqa: BLE001 - closing goes on
                logger.exception("a close listener failed")

    async def close(self) -> None:
        """Close the connection. Commands still waiting fail with ``CdpClosed``."""
        self._gone()
        reader = self._reader
        if not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        try:
            await asyncio.wait_for(self._socket.close(), 3.0)
        except Exception:  # noqa: BLE001 - it is gone either way
            logger.debug("closing the browser socket failed", exc_info=True)
