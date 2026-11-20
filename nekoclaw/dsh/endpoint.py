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

"""The driver's listener on the loopback address, for its dsh processes.

One listener, two services:

* ``/model/v1/chat/completions`` - the model endpoint dsh is configured
  with (``forward.py``). Guarded by a token per dsh process, which that
  process is given in its environment as the "key" of its provider and
  which stops working when the process is gone.
* ``/tools/mcp`` - the host's own tools as an MCP service
  (``host_tools.py``). Guarded by a token per session, which says whose
  call it is and stops working when the session is closed.

It listens on the loopback address only, on a port the system chooses, and
is started by the first session that needs it, never at startup. Every
request has to carry a token; the tokens are made here, kept in memory and
written to no file. A request that carries an ``Origin`` header is refused
whatever else it carries: a page in a browser cannot make a cross-site
request without one, and dsh sends none.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from nekoclaw.dsh.forward import ModelForwarder, respond_json
from nekoclaw.dsh.host_tools import ON_ANSWERED, Answered, HostTools

logger = logging.getLogger(__name__)

MODEL_PREFIX = "/model/v1"
MODEL_PATH = f"{MODEL_PREFIX}/chat/completions"
TOOLS_PATH = "/tools/mcp"
_START_TIMEOUT_SECONDS = 10.0

Send = Callable[[dict[str, Any]], Awaitable[None]]
Receive = Callable[[], Awaitable[dict[str, Any]]]


def _bearer(headers: dict[str, str]) -> str | None:
    authorization = headers.get("authorization", "")
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else None


async def _read_body(receive: Receive) -> bytes | None:
    """Read a request body; None when the client went away first."""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        chunks.append(message.get("body", b""))
        if not message.get("more_body"):
            return b"".join(chunks)


async def _wait_disconnect(receive: Receive) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return


class LocalEndpoint:
    """Serves the model endpoint and the host's tools to the driver's dsh processes."""

    def __init__(self, *, forwarder: ModelForwarder, tools: HostTools) -> None:
        self._forwarder = forwarder
        self._tools = tools
        # The "keys" the dsh processes send with their model requests.
        self._process_tokens: set[str] = set()
        self._session_tokens: dict[str, str] = {}
        self._by_session: dict[str, str] = {}
        self._port: int | None = None
        self._server: Any = None
        self._serve_task: asyncio.Task[Any] | None = None
        self._start_lock = asyncio.Lock()

    # -- tokens -----------------------------------------------------------

    def issue_process_token(self) -> str:
        """Return a new token for the model requests of one dsh process."""
        token = secrets.token_urlsafe(32)
        self._process_tokens.add(token)
        return token

    def revoke_process_token(self, token: str) -> None:
        self._process_tokens.discard(token)

    def _process_of(self, token: str | None) -> str | None:
        """Return the process whose token this is (the token as it was issued), or None."""
        if token is None:
            return None
        offered = token.encode("utf-8")
        for known in self._process_tokens:
            if hmac.compare_digest(offered, known.encode("utf-8")):
                return known
        return None

    def _session_of(self, token: str | None) -> str | None:
        """Return the session a token is for, compared the way a process's token is."""
        if token is None:
            return None
        offered = token.encode("utf-8")
        for known, session_id in self._session_tokens.items():
            if hmac.compare_digest(offered, known.encode("utf-8")):
                return session_id
        return None

    def issue(self, session_id: str) -> str:
        """Return a new token for a session's calls of the host's tools; an older one stops working."""
        self.revoke(session_id)
        token = secrets.token_urlsafe(32)
        self._session_tokens[token] = session_id
        self._by_session[session_id] = token
        return token

    def revoke(self, session_id: str) -> None:
        token = self._by_session.pop(session_id, None)
        if token is not None:
            self._session_tokens.pop(token, None)

    # -- listener ---------------------------------------------------------

    @property
    def port(self) -> int | None:
        return self._port

    @property
    def model_url(self) -> str:
        """Return the base address dsh's provider is configured with."""
        if self._port is None:
            raise RuntimeError("the endpoint is not listening")
        return f"http://127.0.0.1:{self._port}{MODEL_PREFIX}"

    @property
    def tools_url(self) -> str:
        if self._port is None:
            raise RuntimeError("the endpoint is not listening")
        return f"http://127.0.0.1:{self._port}{TOOLS_PATH}"

    async def start(self) -> None:
        """Start listening if not yet."""
        async with self._start_lock:
            if self._port is not None:
                return
            import uvicorn

            config = uvicorn.Config(
                self._asgi,
                host="127.0.0.1",
                port=0,
                log_config=None,
                access_log=False,
                lifespan="off",
                interface="asgi3",
                proxy_headers=False,
            )
            server = uvicorn.Server(config)

            @contextlib.contextmanager
            def no_signals() -> Any:
                yield

            # The process's signal handlers belong to the servers that own the process.
            server.capture_signals = no_signals  # type: ignore[method-assign]
            task = asyncio.ensure_future(server.serve())
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _START_TIMEOUT_SECONDS
            try:
                while not server.started:
                    if task.done():
                        raise RuntimeError(f"the dsh endpoint could not start: {task.exception()}")
                    if loop.time() > deadline:
                        raise RuntimeError("the dsh endpoint did not start in time")
                    await asyncio.sleep(0.01)
                sockets = [sock for listener in server.servers for sock in listener.sockets]
                if not sockets:
                    raise RuntimeError("the dsh endpoint listens nowhere")
                port = int(sockets[0].getsockname()[1])
            except BaseException:
                await _close_listener(server, task)
                raise
            self._port = port
            self._server = server
            self._serve_task = task

    async def stop(self) -> None:
        """Stop listening and drop every token."""
        self._process_tokens.clear()
        self._session_tokens.clear()
        self._by_session.clear()
        server, self._server = self._server, None
        task, self._serve_task = self._serve_task, None
        self._port = None
        await _close_listener(server, task)

    # -- HTTP -------------------------------------------------------------

    async def _asgi(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        path = scope.get("path")
        if path not in (MODEL_PATH, TOOLS_PATH):
            await respond_json(send, 404, {"error": "not found"})
            return
        if scope.get("method") != "POST":
            await send(
                {
                    "type": "http.response.start",
                    "status": 405,
                    "headers": [(b"allow", b"POST"), (b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return
        if "origin" in headers:
            # Sent by a page in a browser, from wherever; never by dsh.
            await respond_json(send, 403, {"error": "requests with an Origin are not served"})
            return
        token = _bearer(headers)
        if path == MODEL_PATH:
            process = self._process_of(token)
            if process is None:
                await respond_json(send, 401, {"error": {"message": "unknown or missing token", "type": "invalid_api_key"}})
                return
            body = await _read_body(receive)
            if body is not None:
                # A model name stands for a session of the process that asks, and of no other.
                await self._forwarder.serve(process, body, receive, send)
            return
        session_id = self._session_of(token)
        if session_id is None:
            await respond_json(send, 401, {"error": "unknown or missing token"})
            return
        body = await _read_body(receive)
        if body is None:
            return
        if self._session_of(token) != session_id:
            # Revoked while the body came in: the session was closed, and one
            # of the same id may have its tools now.
            await respond_json(send, 401, {"error": "unknown or missing token"})
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            await respond_json(send, 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            return
        # What the session is offered as of the look at the token, nothing
        # awaited in between: a session closed and opened again under the
        # same id before the call is carried out is not this one.
        # What the work hands dsh that must not be lost with an answer that
        # never gets there (the texts a ``wait_agent`` takes): whoever
        # handed it over is told below whether the answer was sent to a dsh
        # that was still listening.
        told = Answered()
        marked = ON_ANSWERED.set(told)
        try:
            work = asyncio.ensure_future(self._tools.handle(session_id, payload, offer=self._tools.offer(session_id)))
        finally:
            ON_ANSWERED.reset(marked)
        gone = asyncio.ensure_future(_wait_disconnect(receive))
        delivered = False
        try:
            try:
                await asyncio.wait({work, gone}, return_when=asyncio.FIRST_COMPLETED)
                if not work.done():
                    # dsh gave this call up: its turn was stopped, or the call took
                    # longer than dsh waits. The work is stopped with it.
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                    return
                try:
                    answer = work.result()
                except asyncio.CancelledError:
                    return
                # A dsh that hung up as the work finished may be known to the
                # server and not yet to the watcher: the watcher is given the
                # turns of the loop it takes to hear of it, before the answer
                # is written and looked at below.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                if answer is None:
                    await send({"type": "http.response.start", "status": 202, "headers": [(b"content-length", b"0")]})
                    await send({"type": "http.response.body", "body": b""})
                else:
                    await respond_json(send, 200, answer)
                # Looked at as the last of the answer is handed to the
                # server, nothing awaited since. A server that finds the
                # connection gone writes nothing and says nothing, so that
                # the write went through does not show that it was sent;
                # what shows is the watcher, which the server tells before
                # it lets a write go on. (One that is told only because the
                # answer is complete hears of it a turn of the loop later.)
                # It is not proof: a dsh that hangs up in the last turn of
                # the loop before this look is known to the server and not
                # yet to the watcher, and nothing here can tell. So this
                # says that the answer was written to a dsh that was still
                # listening as far as could be seen, and no more.
                delivered = not gone.done()
            finally:
                gone.cancel()
                if not work.done():
                    work.cancel()
        finally:
            # Whoever handed dsh something with this answer is told whether
            # it got there, the last to hand over first. And whoever hands
            # something over from here on (the work of a call dsh gave up
            # may still be at it) is told the same as it does.
            telling, told.outcome = list(reversed(told)), delivered
            for tell in telling:
                try:
                    tell(delivered)
                except Exception:  # noqa: BLE001 - the others are still told
                    logger.exception("telling whether an answer reached dsh failed")


async def _close_listener(server: Any, task: "asyncio.Task[Any] | None") -> None:
    """Ask a listener to stop and wait for it, cancelling it if it takes too long."""
    if server is not None:
        server.should_exit = True
    if task is None:
        return
    await asyncio.wait({task}, timeout=5.0)
    if not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    elif not task.cancelled() and task.exception() is not None:
        logger.debug("the dsh endpoint's listener ended with an error", exc_info=task.exception())
