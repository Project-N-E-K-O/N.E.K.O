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

"""The host's own tools, served to dsh sessions as tools of an MCP service.

In the built-in runtime ``report``, ``note_lesson``, ``web_search`` and the
subagent tools are tools of the runtime that call back into the host
(``nekoclaw.runtime.tools.host_tools``). dsh has no place for them, but it
calls the tools of any service a session is handed, so the driver serves
them: the same definitions and the same implementations, each call going to
the same host callback. dsh shows them to the model as ``mcp__host__<name>``.

``web_fetch`` is served the same way, though it calls nothing of the host
and dsh has a tool of that name: dsh's reaches every address, the loopback
interface and private networks included, where the built-in runtime's
(``nekoclaw.runtime.tools.web``) reaches public addresses only. dsh's is
switched off (``profile.build_patch``) and the built-in runtime's is the one
a task agent has, in either runtime.

What differs from the built-in runtime, and why:

* ``ask_user_question`` is not offered. dsh ends a call of such a tool after
  a minute and cannot be told otherwise, and a person answers when they
  answer. A question is a ``waiting`` report here; the host hands the answer
  to the session as its next work.
* ``wait_agent`` hands over what arrived as its result. dsh takes no text in
  the middle of a turn, so what a subagent's result or the user would add
  waits for the turn to end; a session that waits for it gets it at once.
  It waits for less than dsh's minute at a time.
* Every other call that takes longer than dsh allows is given up and
  reported as failed.

Transport as in the host's gateway: HTTP POST with JSON answers,
``initialize``, ``ping``, ``tools/list`` and ``tools/call``.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nekoclaw.runtime.host import HostBridge
from nekoclaw.runtime.loop.tools import AgentTool
from nekoclaw.runtime.tools.base import SessionInbox, SpillStore, ToolCallInfo, ToolContext, ToolError, define_tool

logger = logging.getLogger(__name__)

SERVER_NAME = "nekoclaw-host-tools"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
# Left out on dsh; see the module docstring.
NOT_OFFERED = frozenset({"ask_user_question"})
WAIT_TOOL = "wait_agent"

# Returns the texts that arrived for a session, waiting up to so many seconds
# for the first; an empty list when none came. What it returns counts as
# handed to the session.
TakeInput = Callable[[float], Awaitable[list[str]]]

# Whom to tell whether the answer to the request under way reached dsh: set
# by whoever serves the request (``LocalEndpoint``), added to by a call that
# hands dsh something it must not lose (``wait_agent``'s texts). Each is
# called once the answer is written, with True, or with False where it did
# not get there. Calls run in tasks of their own, which see the same list.
ON_ANSWERED: contextvars.ContextVar[list[Callable[[bool], None]] | None] = contextvars.ContextVar(
    "nekoclaw_dsh_on_answered", default=None
)


class Answered(list):  # type: ignore[type-arg]
    """Those to tell whether an answer reached dsh (``ON_ANSWERED``); one that joins late is told at once.

    The work of a call may go on after dsh gave the call up (it is not
    cut off in the middle of what it writes), and hand something over
    then: whoever serves the request has told the others by that time.
    ``outcome`` is what they were told; with it set, one that is added is
    told the same on the spot, and is not left waiting for word that was
    given already.
    """

    outcome: bool | None = None

    def append(self, tell: Callable[[bool], None]) -> None:
        if self.outcome is None:
            super().append(tell)
        else:
            tell(self.outcome)


class _Cancelled:
    """Cancellation token of one call: set when dsh gives the call up."""

    def __init__(self) -> None:
        self.cancelled = False

    def is_cancelled(self) -> bool:
        return self.cancelled


@dataclass(slots=True)
class _Offer:
    """What one session is offered, and its calls under way."""

    tools: dict[str, AgentTool] = field(default_factory=dict)
    running: dict[asyncio.Task[Any], _Cancelled] = field(default_factory=dict)
    # The tasks that wait for the host on behalf of its calls; see ``_SessionHost``.
    asking: set[asyncio.Task[Any]] = field(default_factory=set)
    closed: bool = False


class _SessionHost:
    """The host as one session's tools reach it: what they ask of it is stopped with the session.

    A tool's call of the host runs in a task of its own (``run_cancellable``),
    which the call's own cancellation reaches only on its way out. One
    woken already (its answer in) would take a step more meanwhile, after
    the session closed: ``HostTools.close`` cancels these tasks themselves.
    """

    def __init__(self, host: HostBridge, offer: _Offer) -> None:
        self._host = host
        self._offer = offer

    @property
    def capabilities(self) -> frozenset[str]:
        return self._host.capabilities

    async def call(self, session_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._offer.closed:
            raise ToolError("the session that made this call was closed")
        task = asyncio.current_task()
        if task is not None:
            self._offer.asking.add(task)
        try:
            return await self._host.call(session_id, name, payload)
        finally:
            if task is not None:
                self._offer.asking.discard(task)


# ``handle``/``call`` without an offer: what the session is offered when they run.
_NOW: Any = object()


def _definition(tool: AgentTool) -> dict[str, Any]:
    return {"name": tool.name, "description": tool.description, "inputSchema": dict(tool.parameters)}


def _result(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


class HostTools:
    """Builds and carries out the host's tools for the sessions of a dsh driver."""

    def __init__(self, host: HostBridge) -> None:
        self._host = host
        self._offers: dict[str, _Offer] = {}

    def open(
        self,
        session_id: str,
        *,
        workdir: str,
        session_dir: str,
        top_level: bool,
        disabled: tuple[str, ...],
        take_input: TakeInput,
        emit_report: Callable[[dict[str, Any]], None],
    ) -> list[str]:
        """Build a session's tools and return their names, as the built-in runtime names them."""
        from nekoclaw.runtime.host import offers_subagents
        from nekoclaw.runtime.protocol import RuntimeLimits
        from nekoclaw.runtime.tools import tool_schemas
        from nekoclaw.runtime.tools.host_tools import (
            WAIT_AGENT_DEFAULT_MS,
            WAIT_AGENT_MAX_MS,
            WAIT_AGENT_MIN_MS,
            build_host_tools,
        )
        from nekoclaw.runtime.tools.web import build_web_tools

        schemas = tool_schemas()
        offer = _Offer()
        context = ToolContext(
            session_id=session_id,
            workdir=Path(workdir),
            spill=SpillStore(Path(session_dir) / "outputs"),
            host=_SessionHost(self._host, offer),
            limits=RuntimeLimits(),
            supports_images=False,
            platform=sys.platform,
            # Only ``wait_agent`` reads it, and that tool is replaced below.
            inbox=SessionInbox(),
            jobs=None,  # type: ignore[arg-type]  # the tools built here use none of the three
            todos=None,  # type: ignore[arg-type]
            goals=None,  # type: ignore[arg-type]
            emit_report=emit_report,
            top_level=top_level,
        )
        built = [*build_host_tools(context, schemas), *build_web_tools(context, schemas)]
        tools = {tool.name: tool for tool in built}

        if offers_subagents(self._host.capabilities):

            async def wait_agent(args: dict[str, Any], call: ToolCallInfo) -> str:
                from config.nekoclaw_settings import DSH_HOST_TOOL_SECONDS

                del call
                timeout_ms = int(args.get("timeout_ms", WAIT_AGENT_DEFAULT_MS))
                if not WAIT_AGENT_MIN_MS <= timeout_ms <= WAIT_AGENT_MAX_MS:
                    raise ToolError(f"timeout_ms must be from {WAIT_AGENT_MIN_MS} through {WAIT_AGENT_MAX_MS}")
                # Inside the time dsh gives a call; a longer wait is asked for again.
                arrived = await take_input(min(timeout_ms / 1000, DSH_HOST_TOOL_SECONDS - 5.0))
                state = json.dumps({"timedOut": not arrived}, separators=(",", ":"))
                return "\n\n".join([state, *arrived])

            tools[WAIT_TOOL] = define_tool(schemas[WAIT_TOOL], wait_agent)

        hidden = NOT_OFFERED | set(disabled)
        offer.tools = {name: tool for name, tool in sorted(tools.items()) if name not in hidden}
        self._offers[session_id] = offer
        return list(offer.tools)

    def close(self, session_id: str) -> None:
        """Take a session's tools back, and stop its calls under way (with what they asked of the host)."""
        offer = self._offers.pop(session_id, None)
        if offer is None:
            return
        offer.closed = True
        for work, token in list(offer.running.items()):
            token.cancelled = True
            work.cancel()
        for asking in list(offer.asking):
            asking.cancel()

    def names(self, session_id: str) -> list[str]:
        offer = self._offers.get(session_id)
        return list(offer.tools) if offer is not None else []

    def offer(self, session_id: str) -> _Offer | None:
        """Return what a session is offered right now, for ``handle`` to keep to; see there."""
        return self._offers.get(session_id)

    # -- JSON-RPC ---------------------------------------------------------

    async def handle(self, session_id: str, payload: Any, *, offer: _Offer | None = _NOW) -> Any:
        """Answer one JSON-RPC message of a session, or a list of them. None for notifications.

        ``offer`` is what the session was offered when its token was looked
        at (``offer()``), by default what it is offered now. Its tools are
        carried out only while they are still the session's: one closed
        meanwhile, and opened again under the same id, is another session.
        """
        if offer is _NOW:
            offer = self._offers.get(session_id)
        if isinstance(payload, list):
            answers = [
                answer for answer in [await self._handle_one(session_id, offer, item) for item in payload] if answer
            ]
            return answers or None
        return await self._handle_one(session_id, offer, payload)

    async def _handle_one(self, session_id: str, offer: _Offer | None, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        is_notification = "id" not in message
        request_id = message.get("id")
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        method = message["method"]
        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            result: Any = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [_definition(tool) for tool in offer.tools.values()] if offer is not None else []}
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments")
            if is_notification and name == WAIT_TOOL:
                # Asked for with no id: no answer goes back, so what the wait
                # handed over would go nowhere. Not carried out: the texts
                # stay for a wait that can be answered.
                return None
            result = await self.call(
                session_id,
                str(name or ""),
                arguments if isinstance(arguments, dict) else {},
                call_id=str(request_id),
                offer=offer,
            )
        else:
            if is_notification:
                return None
            # Also what dsh's client gets for the probe of a newer protocol
            # it sends first; it then goes on with ``initialize``.
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method {method}"}}
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def call(
        self, session_id: str, name: str, arguments: dict[str, Any], *, call_id: str = "", offer: _Offer | None = _NOW
    ) -> dict[str, Any]:
        """Carry out one tool call and return its MCP result; see ``handle`` for ``offer``."""
        from config.nekoclaw_settings import DSH_HOST_TOOL_SECONDS
        from config.prompts.prompts_nekoclaw_dsh import DSH_TOOL_TOOK_TOO_LONG

        current = self._offers.get(session_id)
        if offer is _NOW:
            offer = current
        elif offer is not current:
            return _result("the session that made this call was closed", error=True)
        tool = offer.tools.get(name) if offer is not None else None
        if tool is None:
            return _result(f"no tool named {name}", error=True)
        token = _Cancelled()

        async def carried_out() -> Any:
            # Looked at again where the tool starts, nothing awaited in between:
            # the session may have been closed and opened again since.
            if offer is not None and self._offers.get(session_id) is not offer:
                raise ToolError("the session that made this call was closed")
            return await tool.execute(call_id or name, arguments, token, None)

        work = asyncio.ensure_future(carried_out())
        if offer is not None:
            offer.running[work] = token
        work.add_done_callback(lambda done: offer.running.pop(done, None) if offer is not None else None)
        try:
            # Shielded: when the wait is given up below, the work is told to
            # stop through its token and then cancelled, in that order.
            outcome = await asyncio.wait_for(asyncio.shield(work), DSH_HOST_TOOL_SECONDS)
        except asyncio.TimeoutError:
            token.cancelled = True
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            return _result(DSH_TOOL_TOOK_TOO_LONG, error=True)
        except asyncio.CancelledError:
            token.cancelled = True
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                # dsh gave the call up (its turn was stopped): so does the host.
                raise
            # The session was closed under it (``close``).
            return _result("the session that made this call was closed", error=True)
        except ToolError as exc:
            return _result(str(exc), error=True)
        except Exception as exc:  # noqa: BLE001 - reported to the model, never raised into the listener
            logger.exception("the host tool %s failed", name)
            return _result(f"{name} failed: {type(exc).__name__}: {exc}", error=True)
        text = "".join(str(getattr(part, "text", "") or "") for part in outcome.content)
        return _result(text or "(empty result)")
