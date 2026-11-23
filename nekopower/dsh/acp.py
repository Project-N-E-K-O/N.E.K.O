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

"""A connection to an ACP agent running as a child process.

ACP (Agent Client Protocol) is JSON-RPC 2.0 over the agent's standard input
and output, one JSON object per line. This module is the client side and
knows nothing of dsh: it starts the process, sends requests and
notifications, and hands what the agent sends to its owner.

* Messages the agent sends are handled on the event loop in the order they
  arrived. A notification is handed over at once; a request is answered by a
  task of its own, started after every earlier message was handed over, so
  whatever the agent said before it asked is known when the answer is made.
* Reading and writing the pipes is done on threads: a pipe can block, and
  the event loop of the host may be one that cannot watch a child's pipes.
* When the agent's output ends, every request still waiting fails with
  ``AcpClosed`` and the owner is told once.

Nothing secret is put on the command line by this module; the environment is
whatever the caller passes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from utils.asyncio_executor import to_thread_through_cancellation

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603
_CREATE_NO_WINDOW = 0x08000000
_STOP = object()
_CLOSE_INPUT = object()
# How long killing an agent's process group waits for ``Popen``'s lock; see ``_end_group``.
_GROUP_LOCK_SECONDS = 5.0

NotificationHandler = Callable[[str, dict[str, Any]], None]
RequestHandler = Callable[[str, dict[str, Any]], Awaitable["dict[str, Any] | None"]]


class AcpError(RuntimeError):
    """The agent answered a request with an error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(self.detail)

    @property
    def detail(self) -> str:
        """Return the error as one line, with the details the agent attached."""
        details = self.data.get("details") if isinstance(self.data, dict) else self.data
        if isinstance(details, str) and details.strip() and details.strip() not in self.message:
            return f"{self.message}: {details.strip()}"
        return self.message


class AcpClosed(AcpError):
    """The agent process is gone, or was never started."""

    def __init__(self, message: str = "the agent process is not running") -> None:
        super().__init__(INTERNAL_ERROR, message)


class AcpConnection:
    """One agent process and the JSON-RPC conversation with it."""

    def __init__(
        self,
        *,
        on_notification: NotificationHandler,
        on_request: RequestHandler,
        on_stderr: Callable[[str], None] | None = None,
        on_closed: Callable[[], None] | None = None,
        stderr_lines_kept: int = 40,
    ) -> None:
        self._on_notification = on_notification
        self._on_request = on_request
        self._on_stderr = on_stderr
        self._on_closed = on_closed
        self._process: subprocess.Popen[bytes] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._outbox: queue.Queue[Any] = queue.Queue()
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 0
        self._closed = False
        self._serving: set[asyncio.Task[None]] = set()
        # The last lines of the agent's standard error, for a failure report.
        self.stderr_tail: deque[str] = deque(maxlen=stderr_lines_kept)

    # -- process ----------------------------------------------------------

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None and _poll(process) is None else None

    @property
    def alive(self) -> bool:
        return self.pid is not None and not self._closed

    @property
    def returncode(self) -> int | None:
        process = self._process
        return _poll(process) if process is not None else None

    async def open(self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str]) -> None:
        """Start the agent process. Raises ``OSError`` when it cannot be started."""
        if self._process is not None:
            raise RuntimeError("the connection was opened before")
        self._loop = loop = asyncio.get_running_loop()
        spawning = loop.run_in_executor(None, self._spawn, list(argv), cwd, dict(env))
        try:
            self._process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            # The worker cannot be stopped and may start the agent all the
            # same: it is waited for, and what it started is ended here
            # rather than left running with nobody to end it.
            while not spawning.done():
                try:
                    await asyncio.shield(spawning)
                except asyncio.CancelledError:
                    continue
                except BaseException:  # noqa: BLE001 - the cancellation below is what propagates
                    break
            if spawning.done() and not spawning.cancelled() and spawning.exception() is None:
                await to_thread_through_cancellation(_discard, spawning.result())
            raise
        threading.Thread(target=self._read_output, args=(loop,), name="acp-agent-output", daemon=True).start()
        threading.Thread(target=self._read_errors, args=(loop,), name="acp-agent-errors", daemon=True).start()
        threading.Thread(target=self._write_input, name="acp-agent-input", daemon=True).start()
        if sys.platform != "win32":
            # Not left to the end of its output: what it started may hold that open.
            threading.Thread(target=_end_group_once_gone, args=(self._process,), name="acp-agent-group", daemon=True).start()

    @staticmethod
    def _spawn(argv: list[str], cwd: str, env: dict[str, str]) -> "subprocess.Popen[bytes]":
        """Blocking: create the process with its three pipes."""
        from utils import process_creation

        options: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": cwd,
            "env": env,
        }
        if sys.platform == "win32":
            # No console window of its own; the commands it runs inherit that.
            options["creationflags"] = _CREATE_NO_WINDOW
        else:
            # A group of its own: it is ended together with what it started.
            options["start_new_session"] = True
        return process_creation.popen(argv, **options)

    # -- threads ----------------------------------------------------------

    def _read_output(self, loop: asyncio.AbstractEventLoop) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            for line in process.stdout:
                if not line.strip():
                    continue
                try:
                    loop.call_soon_threadsafe(self._on_line, line)
                except RuntimeError:
                    return
        except (OSError, ValueError):
            # The pipe was closed under the reader: the end is told below all the same.
            pass
        try:
            loop.call_soon_threadsafe(self._on_end)
        except RuntimeError:
            # The event loop is closed: nobody is left to tell.
            pass

    def _read_errors(self, loop: asyncio.AbstractEventLoop) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        try:
            for raw in process.stderr:
                text = raw.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue
                try:
                    loop.call_soon_threadsafe(self._on_error_line, text)
                except RuntimeError:
                    return
        except (OSError, ValueError):
            # The pipe was closed under the reader; the end is told by the output's reader.
            pass

    def _write_input(self) -> None:
        process = self._process
        assert process is not None and process.stdin is not None
        stream = process.stdin
        while True:
            item = self._outbox.get()
            if item is _STOP:
                return
            try:
                if item is _CLOSE_INPUT:
                    stream.close()
                    return
                stream.write(item)
                stream.flush()
            except (OSError, ValueError):
                return

    # -- incoming ---------------------------------------------------------

    def _on_error_line(self, text: str) -> None:
        self.stderr_tail.append(text)
        if self._on_stderr is not None:
            try:
                self._on_stderr(text)
            except Exception:  # noqa: BLE001 - a broken listener must not stop the reader
                logger.exception("handling a line of the agent's standard error failed")

    def _on_line(self, line: bytes) -> None:
        try:
            message = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.warning("ignoring a line from the agent that is not JSON")
            return
        if not isinstance(message, dict):
            return
        method = message.get("method")
        if isinstance(method, str):
            params = message.get("params")
            params = params if isinstance(params, dict) else {}
            if "id" in message:
                task = asyncio.ensure_future(self._serve(message["id"], method, params))
                self._serving.add(task)
                task.add_done_callback(self._serving.discard)
            else:
                try:
                    self._on_notification(method, params)
                except Exception:  # noqa: BLE001 - one bad notification must not stop the rest
                    logger.exception("handling the agent's notification %s failed", method)
            return
        future = self._pending.pop(message.get("id"), None) if isinstance(message.get("id"), int) else None
        if future is None or future.done():
            return
        error = message.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            future.set_exception(
                AcpError(
                    code if isinstance(code, int) else INTERNAL_ERROR,
                    str(error.get("message") or "the agent reported an error"),
                    error.get("data"),
                )
            )
        else:
            future.set_result(message.get("result"))

    async def _serve(self, request_id: Any, method: str, params: dict[str, Any]) -> None:
        try:
            result = await self._on_request(method, params)
        except asyncio.CancelledError:
            raise
        except AcpError as exc:
            self._send({"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": {"code": exc.code, "message": exc.message}})
            return
        except Exception as exc:  # noqa: BLE001 - reported to the agent, never raised into the loop
            logger.exception("answering the agent's request %s failed", method)
            self._send(
                {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": request_id,
                    "error": {"code": INTERNAL_ERROR, "message": f"{type(exc).__name__}: {exc}"},
                }
            )
            return
        self._send({"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result if result is not None else {}})

    def _on_end(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._outbox.put(_STOP)
        for task in list(self._serving):
            task.cancel()
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(AcpClosed("the agent process exited"))
        if self._on_closed is not None:
            try:
                self._on_closed()
            except Exception:  # noqa: BLE001 - the owner's business
                logger.exception("handling the end of the agent process failed")

    # -- outgoing ---------------------------------------------------------

    def _send(self, message: dict[str, Any]) -> None:
        if self._closed or self._process is None:
            return
        self._outbox.put(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a notification; dropped when the agent is gone."""
        self._send({"jsonrpc": JSONRPC_VERSION, "method": method, "params": params})

    async def request(self, method: str, params: dict[str, Any], *, timeout: float | None = None) -> Any:
        """Send a request and return its result.

        Raises ``AcpError`` when the agent answers with an error, ``AcpClosed``
        when it is gone, and ``asyncio.TimeoutError`` when ``timeout`` runs
        out; an answer that comes later is dropped.
        """
        if self._closed or self._process is None or self._loop is None:
            raise AcpClosed
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[Any] = self._loop.create_future()
        self._pending[request_id] = future
        self._send({"jsonrpc": JSONRPC_VERSION, "id": request_id, "method": method, "params": params})
        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

    # -- closing ----------------------------------------------------------

    async def close(self, grace: float) -> None:
        """End the agent process: close its input, wait ``grace`` seconds, then kill it.

        An agent whose input ends closes its sessions and exits by itself.
        """
        process = self._process
        if process is None:
            return
        if _poll(process) is None:
            self._outbox.put(_CLOSE_INPUT)
            exited = await asyncio.to_thread(_wait, process, grace)
            if not exited:
                logger.warning("the agent process did not exit; killing it")
                await asyncio.to_thread(kill_tree, process)
                await asyncio.to_thread(_wait, process, 5.0)
        else:
            self._outbox.put(_STOP)
        # The reader thread reports the end; make sure it is seen before returning.
        for _attempt in range(100):
            if self._closed:
                break
            await asyncio.sleep(0.02)
        if not self._closed:
            self._on_end()

    def kill(self) -> None:
        """Kill the process at once, with what it started. Blocking."""
        process = self._process
        if process is not None and _poll(process) is None:
            kill_tree(process)


def _wait(process: "subprocess.Popen[bytes]", seconds: float) -> bool:
    """Blocking: wait for a process; return whether it exited. On POSIX its exit is collected by ``_poll``."""
    if sys.platform == "win32":
        try:
            process.wait(seconds)
        except subprocess.TimeoutExpired:
            return False
        return True
    deadline = time.monotonic() + seconds
    delay = 0.005
    while _poll(process) is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, 0.05)
    return True


def _discard(process: "subprocess.Popen[bytes]") -> None:
    """Blocking: end a process nobody is going to talk to, and let go of its pipes."""
    kill_tree(process)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                # Closed already, or broken: either way it is let go of.
                pass
    _wait(process, 5.0)


def _poll(process: "subprocess.Popen[bytes]") -> int | None:
    """``process.poll()``; on POSIX through ``_collect``, the one way the agent's exit is collected here."""
    if sys.platform == "win32":
        return process.poll()
    _collect(process)
    return process.returncode


def _collect(process: "subprocess.Popen[bytes]") -> None:
    """POSIX: when the process exited and its exit is not collected yet, kill what is left in its group, then collect it.

    The agent was started with ``start_new_session``: its group's id is its
    process id, which is its own until its exit is collected. So the group
    is killed before that, never after: by then the id may be another
    process's. Done under the lock ``Popen`` collects under, so that
    nothing collects it in between; a caller that finds the lock taken
    leaves it to whoever holds it.
    """
    lock = getattr(process, "_waitpid_lock", None)
    if lock is not None and not lock.acquire(blocking=False):
        return
    try:
        if process.returncode is not None or not _exited(process):
            return
        _kill_group(process)
        try:
            _pid, status = os.waitpid(process.pid, 0)
        except ChildProcessError:
            # Collected by something else after all: nothing to tell.
            return
        process.returncode = os.waitstatus_to_exitcode(status)
    finally:
        if lock is not None:
            lock.release()


def _exited(process: "subprocess.Popen[bytes]") -> bool:
    """POSIX: whether a process exited and its exit is not collected yet (a zombie)."""
    if hasattr(os, "waitid"):
        try:
            return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return False
    # No ``waitid`` (macOS before Python 3.13): the system's process table says.
    import psutil

    try:
        return psutil.Process(process.pid).status() == psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _end_group_once_gone(process: "subprocess.Popen[bytes]") -> None:
    """Blocking, POSIX, a thread of its own: once the agent exited, kill what it left in its group and collect its exit.

    Its end is waited for without collecting it (``WNOWAIT``); where the
    system has no way to do that it is looked for four times a second.
    Whoever notices the end first does the same (``_collect``).
    """
    if hasattr(os, "waitid"):
        try:
            os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        except OSError:
            # Collected already, which ends the group first.
            return
    while _poll(process) is None:
        time.sleep(0.01 if hasattr(os, "waitid") else 0.25)


def _kill_group(process: "subprocess.Popen[bytes]") -> None:
    """POSIX: send the process group a process leads SIGKILL. Only while its exit is not collected; see ``_collect``."""
    import signal

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        # ProcessLookupError: nothing is left of the group.
        pass


def _end_group(process: "subprocess.Popen[bytes]") -> None:
    """Blocking, POSIX: kill the process group of a process that is not collected yet, with whatever is in it.

    The lock is waited for a while only: ``Popen.wait()`` without a time
    limit holds it until the process ends, and the kill that would end it
    comes after this. Not got, the group is left to that kill.
    """
    lock = getattr(process, "_waitpid_lock", None)
    if lock is not None and not lock.acquire(timeout=_GROUP_LOCK_SECONDS):
        logger.warning("the agent process is being waited for elsewhere; its process group is not killed")
        return
    try:
        if process.returncode is None:
            _kill_group(process)
    finally:
        if lock is not None:
            lock.release()


def kill_tree(process: "subprocess.Popen[bytes]") -> None:
    """Kill a process and the processes it started. Blocking."""
    if _poll(process) is not None:
        return
    if sys.platform == "win32":
        taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        try:
            subprocess.run(  # noqa: S603 - a fixed system tool and a process id
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            # No tree to kill after all, or no taskkill: the process itself is killed below.
            pass
    else:
        _end_group(process)
    try:
        process.kill()
    except OSError:
        # It exited meanwhile.
        pass
