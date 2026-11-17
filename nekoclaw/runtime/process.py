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

"""The runtime in a child process (design section 8.8).

``SubprocessRuntime`` starts one child with ``multiprocessing``'s ``spawn``
start method, the same way the plugin host starts plugins, and talks to it over
a duplex pipe. Each pipe message is one JSON object: commands go down, events
come up (see ``nekoclaw.runtime.protocol``).

* The child is started by the first ``start`` command, never earlier.
* All sessions share the child's single event loop.
* Model credentials travel inside the ``start`` command. Nothing secret is put
  on the command line or in the environment.
* Host callbacks cross the pipe as ``host_request`` events answered by
  ``host_response`` commands.
* When every session has been idle for ``idle_exit_seconds`` the child closes
  its sessions and exits, which returns all of its memory. The next ``start``
  spawns a new child; a session resumes from its transcript.
* The child does not leave on its own. It asks with ``runtime_idle``, stating
  how many messages it has read, and keeps serving. The parent answers with a
  ``shutdown`` only if that is every message it has sent, and from then on
  sends this child nothing more: a ``start`` waits for the child to be gone and
  goes to a new one, any other command is rejected with an ``error`` event. So
  no command is ever written to a child that has stopped reading.
* If the child dies, every session it had gets an ``ended`` event with reason
  ``error``, and every ``start`` it had not answered yet gets an ``error``
  event.

Importing this module starts nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import os
import queue
import threading
import time
import uuid
from typing import Any

from pydantic import ValidationError

from nekoclaw.runtime import protocol
from nekoclaw.runtime.host import HostBridge, HostCallError, NoHost
from nekoclaw.runtime.protocol import EventSink

logger = logging.getLogger(__name__)

DEFAULT_IDLE_EXIT_SECONDS = 300.0
READY_TIMEOUT_SECONDS = 60.0
SHUTDOWN_TIMEOUT_SECONDS = 15.0
# How long the child itself waits for its sessions to close before it exits;
# shorter than the parent's wait above, so the exit is still announced.
_SESSIONS_CLOSE_SECONDS = 10.0
_IDLE_POLL_SECONDS = 0.25
_STOP = object()


class _PipeWriter:
    """Sends JSON messages over a pipe from a dedicated thread.

    Writing can block when the other side reads slowly; doing it on a thread
    keeps the event loop free. Messages keep their order.
    """

    def __init__(self, connection: Any, name: str) -> None:
        self._connection = connection
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread = threading.Thread(target=self._write_to_pipe, name=name, daemon=True)
        self._thread.start()

    def send(self, message: dict[str, Any]) -> None:
        self._queue.put(json.dumps(message, ensure_ascii=False).encode("utf-8"))

    def _write_to_pipe(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            try:
                self._connection.send_bytes(item)
            except (OSError, ValueError, EOFError):
                return

    def stop(self) -> None:
        """Stop after the queued messages without waiting for them."""
        self._queue.put(_STOP)

    def close(self, timeout: float = 5.0) -> None:
        """Stop and wait until the queued messages were written. Blocking."""
        self._queue.put(_STOP)
        self._thread.join(timeout)


def _read_pipe(connection: Any, loop: asyncio.AbstractEventLoop, deliver: Any) -> None:
    """Thread body: forward each pipe message to ``deliver`` on the loop; ``None`` at EOF."""
    while True:
        try:
            data = connection.recv_bytes()
        except (EOFError, OSError, ValueError):
            data = None
        try:
            loop.call_soon_threadsafe(deliver, data)
        except RuntimeError:
            return
        if data is None:
            return


# ---------------------------------------------------------------------------
# Child side
# ---------------------------------------------------------------------------


class _PipeHostBridge:
    """``HostBridge`` of the child: every call is forwarded to the parent."""

    def __init__(self, capabilities: frozenset[str], send: EventSink) -> None:
        self._capabilities = capabilities
        self._send = send
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    @property
    def pending(self) -> int:
        return len(self._pending)

    async def call(self, session_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name not in self._capabilities:
            raise HostCallError(f"host callback {name!r} is not available")
        request_id = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._send(
            protocol.dump(
                protocol.HostRequestEvent(
                    session_id=session_id, request_id=request_id, name=name, payload=payload
                )
            )
        )
        try:
            return await future
        except asyncio.CancelledError:
            self._send(
                protocol.dump(
                    protocol.HostRequestEvent(
                        session_id=session_id, request_id=request_id, name=name, cancel=True
                    )
                )
            )
            raise
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, response: protocol.HostResponseCommand) -> None:
        future = self._pending.get(response.request_id)
        if future is None or future.done():
            return
        if response.ok:
            future.set_result(dict(response.result or {}))
        else:
            future.set_exception(HostCallError(response.error or "the host callback failed"))


async def _serve(connection: Any, options: dict[str, Any]) -> None:
    from nekoclaw.runtime.core import RuntimeCore

    loop = asyncio.get_running_loop()
    writer = _PipeWriter(connection, "nekoclaw-runtime-events")
    inbox: asyncio.Queue[bytes | None] = asyncio.Queue()
    threading.Thread(
        target=_read_pipe,
        args=(connection, loop, inbox.put_nowait),
        name="nekoclaw-runtime-commands",
        daemon=True,
    ).start()

    bridge = _PipeHostBridge(frozenset(options.get("host_capabilities") or ()), writer.send)
    core = RuntimeCore(bridge, writer.send)
    idle_exit = float(options.get("idle_exit_seconds") or DEFAULT_IDLE_EXIT_SECONDS)
    tasks: set[asyncio.Task[None]] = set()
    idle_since: float | None = time.monotonic()
    # Messages read from the parent, valid or not; see RuntimeIdleEvent.
    received = 0
    reason = "shutdown"
    detail: str | None = None
    writer.send(protocol.dump(protocol.RuntimeReadyEvent(pid=os.getpid())))
    try:
        while True:
            try:
                data = await asyncio.wait_for(inbox.get(), _IDLE_POLL_SECONDS)
            except asyncio.TimeoutError:
                if tasks or bridge.pending or not core.idle:
                    idle_since = None
                elif idle_since is None:
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since >= idle_exit:
                    # Ask instead of leaving: a command may already be on its
                    # way, and a process that stopped reading would lose it.
                    # Ask again later if the parent does not agree.
                    writer.send(protocol.dump(protocol.RuntimeIdleEvent(received=received)))
                    idle_since = time.monotonic()
                continue
            if data is None:
                reason = "parent_gone"
                break
            received += 1
            idle_since = None
            try:
                command = protocol.parse_command(json.loads(data))
            except (ValueError, ValidationError) as exc:
                logger.warning("ignoring an invalid command: %s", exc)
                continue
            if isinstance(command, protocol.HostResponseCommand):
                bridge.resolve(command)
            elif isinstance(command, protocol.ShutdownCommand):
                reason = command.reason
                break
            else:
                # Starting or closing a session takes a while; the pump keeps reading.
                task = asyncio.ensure_future(core.handle(command))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
    except Exception as exc:  # noqa: BLE001 - reported to the parent before exiting
        logger.exception("the runtime loop failed")
        reason = "error"
        detail = f"{type(exc).__name__}: {exc}"
    finally:
        # Commands still under way are not cut short. A session in the middle
        # of closing has already left the core's table, so cancelling that
        # close would leave nobody to finish writing its transcript, and the
        # parent would see the session fail instead of close. The core waits
        # for sessions on their way in or out and then closes every session.
        try:
            await core.shutdown(timeout=_SESSIONS_CLOSE_SECONDS)
        except Exception:  # noqa: BLE001 - the process is exiting either way
            logger.exception("closing the sessions failed")
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.send(protocol.dump(protocol.RuntimeExitEvent(reason=reason, detail=detail)))  # type: ignore[arg-type]
        await asyncio.to_thread(writer.close)


def child_main(connection: Any, options: dict[str, Any]) -> None:
    """Entry point of the runtime process."""
    try:
        asyncio.run(_serve(connection, options))
    finally:
        try:
            connection.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Parent side
# ---------------------------------------------------------------------------


class SubprocessRuntime:
    """``AgentRuntime`` whose sessions live in a child process."""

    def __init__(
        self,
        on_event: EventSink,
        host: HostBridge | None = None,
        *,
        idle_exit_seconds: float = DEFAULT_IDLE_EXIT_SECONDS,
    ) -> None:
        self._on_event = on_event
        self._host: HostBridge = host if host is not None else NoHost()
        self._idle_exit_seconds = idle_exit_seconds
        self._lock = asyncio.Lock()
        self._process: Any = None
        self._connection: Any = None
        self._writer: _PipeWriter | None = None
        self._ready: asyncio.Future[int] | None = None
        self._exit_announced = False
        # Messages written to the current child; see RuntimeIdleEvent.
        self._sent = 0
        # The current child was told to exit: nothing more may be sent to it.
        self._retiring = False
        # Calls of ``shutdown`` at work right now; see RuntimeCore.
        self._shutdowns = 0
        self._sessions: set[str] = set()
        # Session id -> start commands the child has not answered yet.
        self._pending_starts: dict[str, int] = {}
        self._host_tasks: dict[str, asyncio.Task[None]] = {}

    # -- process --------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        """Process id of the running child, if there is one."""
        process = self._process
        return process.pid if process is not None and process.is_alive() else None

    @property
    def alive(self) -> bool:
        return self.pid is not None

    def _start_process(self) -> tuple[Any, Any]:
        """Blocking: create the pipe and start the child."""
        from utils import process_creation

        context = multiprocessing.get_context("spawn")
        parent_end, child_end = context.Pipe(duplex=True)
        process = context.Process(
            target=child_main,
            args=(
                child_end,
                {
                    "idle_exit_seconds": self._idle_exit_seconds,
                    "host_capabilities": sorted(self._host.capabilities),
                },
            ),
            name="nekoclaw-runtime",
            daemon=True,
        )
        try:
            process_creation.start(process)
        except BaseException:
            parent_end.close()
            child_end.close()
            raise
        child_end.close()
        return process, parent_end

    async def _wait_until_gone(self) -> None:
        """Wait for a child that was told to exit.

        Its sessions are closed and their transcripts written by then, so a
        session started next can resume from them.
        """
        process = self._process
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
        while self._connection is not None and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        if self._connection is not None:
            if process is not None and process.is_alive():
                logger.warning("the runtime process did not exit; terminating it")
                process.terminate()
            self._on_disconnect()
        if process is not None:
            await asyncio.to_thread(process.join, 5.0)

    async def _ensure_process(self) -> None:
        async with self._lock:
            if self._retiring:
                await self._wait_until_gone()
            if self._process is not None and self._process.is_alive() and self._writer is not None:
                return
            if self._connection is not None:
                # The previous child is gone but its closed pipe was not reported yet.
                self._on_disconnect()
            loop = asyncio.get_running_loop()
            process, connection = await asyncio.to_thread(self._start_process)
            self._process = process
            self._connection = connection
            self._writer = _PipeWriter(connection, "nekoclaw-runtime-commands")
            self._ready = loop.create_future()
            self._exit_announced = False
            self._retiring = False
            self._sent = 0
            threading.Thread(
                target=_read_pipe,
                args=(connection, loop, lambda data, owner=connection: self._on_pipe(owner, data)),
                name="nekoclaw-runtime-events",
                daemon=True,
            ).start()
            try:
                await asyncio.wait_for(asyncio.shield(self._ready), READY_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                process.terminate()
                raise RuntimeError("the runtime process did not become ready") from exc

    def _on_pipe(self, owner: Any, data: bytes | None) -> None:
        if owner is not self._connection:
            # A message from a child that has already been replaced.
            return
        if data is None:
            self._on_disconnect()
            return
        try:
            message = json.loads(data)
        except ValueError:
            logger.warning("ignoring an undecodable message from the runtime process")
            return
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "host_request":
            self._on_host_request(message)
            return
        if kind == "runtime_idle":
            self._on_idle_request(message)
            return
        if kind == "runtime_ready":
            if self._ready is not None and not self._ready.done():
                self._ready.set_result(int(message.get("pid") or 0))
        elif kind == "runtime_exit":
            self._exit_announced = True
        elif kind == "started":
            self._sessions.add(str(message.get("session_id")))
            self._start_answered(str(message.get("session_id")))
        elif kind == "error" and message.get("command") == "start":
            self._start_answered(str(message.get("session_id")))
        elif kind == "ended" and message.get("reason") == "closed":
            self._sessions.discard(str(message.get("session_id")))
        self._deliver(message)

    def _start_answered(self, session_id: str) -> None:
        left = self._pending_starts.get(session_id, 0) - 1
        if left > 0:
            self._pending_starts[session_id] = left
        else:
            self._pending_starts.pop(session_id, None)

    def _on_idle_request(self, message: dict[str, Any]) -> None:
        """Let an idle child exit, unless a message it has not read is on its way."""
        if self._retiring or message.get("received") != self._sent:
            # Declined: the child reads what is in the pipe and stays.
            return
        self._retire(protocol.ShutdownCommand(reason="idle"))

    def _retire(self, command: protocol.ShutdownCommand) -> None:
        """Tell the child to exit. Nothing is sent to it after this."""
        self._send(command)
        self._retiring = True

    def _deliver(self, message: dict[str, Any]) -> None:
        try:
            self._on_event(message)
        except Exception:  # noqa: BLE001 - a broken sink must not stop the pump
            logger.exception("the event sink failed")

    def _on_disconnect(self) -> None:
        writer, self._writer = self._writer, None
        connection, self._connection = self._connection, None
        if writer is not None:
            writer.stop()
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        for task in self._host_tasks.values():
            task.cancel()
        self._host_tasks.clear()
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(RuntimeError("the runtime process exited while starting"))
        self._retiring = False
        orphans, self._sessions = sorted(self._sessions), set()
        for session_id in orphans:
            self._deliver(
                protocol.dump(
                    protocol.EndedEvent(
                        session_id=session_id,
                        reason="error",
                        error=protocol.PROCESS_EXITED_ERROR,
                    )
                )
            )
        unanswered, self._pending_starts = sorted(self._pending_starts), {}
        for session_id in unanswered:
            self._deliver(
                protocol.dump(
                    protocol.ErrorEvent(
                        session_id=session_id,
                        command="start",
                        message="the runtime process exited before the session started",
                    )
                )
            )
        if not self._exit_announced:
            self._deliver(
                protocol.dump(
                    protocol.RuntimeExitEvent(
                        reason="error", detail="the runtime process exited unexpectedly"
                    )
                )
            )

    # -- host callbacks -------------------------------------------------------

    def _on_host_request(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("request_id"))
        if message.get("cancel"):
            task = self._host_tasks.pop(request_id, None)
            if task is not None:
                task.cancel()
            return
        task = asyncio.ensure_future(self._serve_host_request(request_id, message))
        self._host_tasks[request_id] = task
        task.add_done_callback(lambda _done: self._host_tasks.pop(request_id, None))

    async def _serve_host_request(self, request_id: str, message: dict[str, Any]) -> None:
        try:
            result = await self._host.call(
                str(message.get("session_id")),
                str(message.get("name")),
                dict(message.get("payload") or {}),
            )
            response = protocol.HostResponseCommand(request_id=request_id, ok=True, result=result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the failure is the callback's answer
            response = protocol.HostResponseCommand(
                request_id=request_id, ok=False, error=str(exc) or type(exc).__name__
            )
        self._send(response)

    # -- commands -------------------------------------------------------------

    def _send(self, command: Any) -> bool:
        writer = self._writer
        if writer is None:
            return False
        writer.send(protocol.dump(command))
        self._sent += 1
        return True

    def _send_or_reject(self, session_id: str, command: Any) -> bool:
        # A child that was told to exit is closing its sessions and no longer
        # reads commands, so it counts as gone already.
        if not self._retiring and self._send(command):
            return True
        self._deliver(
            protocol.dump(
                protocol.ErrorEvent(
                    session_id=session_id,
                    command=command.type,
                    message=(
                        "the runtime process is not running or is shutting down; "
                        "start the session again"
                    ),
                )
            )
        )
        return False

    async def start(self, spec: protocol.TaskAgentSpec) -> None:
        if self._shutdowns:
            # As in the in-process runtime: a session asked for while the
            # runtime is being shut down is refused. (An idle child that is
            # leaving is another matter; there the start waits for the next.)
            self._deliver(
                protocol.dump(
                    protocol.ErrorEvent(
                        session_id=spec.session_id,
                        command="start",
                        message="the runtime is shutting down",
                    )
                )
            )
            return
        try:
            await self._ensure_process()
        except Exception as exc:  # noqa: BLE001 - reported as a rejected command
            logger.exception("could not start the runtime process")
            self._deliver(
                protocol.dump(
                    protocol.ErrorEvent(
                        session_id=spec.session_id,
                        command="start",
                        message=f"the runtime process could not be started: {exc}",
                    )
                )
            )
            return
        if self._send_or_reject(spec.session_id, protocol.StartCommand(spec=spec)):
            self._pending_starts[spec.session_id] = self._pending_starts.get(spec.session_id, 0) + 1

    async def prompt(self, session_id: str, text: str) -> None:
        self._send_or_reject(session_id, protocol.PromptCommand(session_id=session_id, text=text))

    async def steer(self, session_id: str, text: str) -> None:
        self._send_or_reject(session_id, protocol.SteerCommand(session_id=session_id, text=text))

    async def follow_up(self, session_id: str, text: str) -> None:
        self._send_or_reject(session_id, protocol.FollowUpCommand(session_id=session_id, text=text))

    async def abort(self, session_id: str) -> None:
        self._send_or_reject(session_id, protocol.AbortCommand(session_id=session_id))

    async def close(self, session_id: str) -> None:
        self._send_or_reject(session_id, protocol.CloseCommand(session_id=session_id))

    async def shutdown(self) -> None:
        """Ask the child to close its sessions and exit; kill it if it does not.

        A ``start`` that arrives while this is at work is refused. Afterwards
        the runtime can be used again; the next ``start`` brings up a new child.
        """
        self._shutdowns += 1
        try:
            # The lock keeps this apart from a ``start`` that is already
            # bringing a child up.
            async with self._lock:
                process = self._process
                if process is None:
                    return
                if not self._retiring:
                    self._retire(protocol.ShutdownCommand())
                await asyncio.to_thread(process.join, SHUTDOWN_TIMEOUT_SECONDS)
                if process.is_alive():
                    logger.warning("the runtime process did not exit; terminating it")
                    process.terminate()
                    await asyncio.to_thread(process.join, 5.0)
                self._process = None
                # Let the reader thread report the closed pipe before returning.
                for _attempt in range(50):
                    if self._connection is None:
                        break
                    await asyncio.sleep(0.02)
                if self._connection is not None:
                    self._on_disconnect()
                self._retiring = False
        finally:
            self._shutdowns -= 1
