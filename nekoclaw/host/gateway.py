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

"""The capability gateway: an MCP service for task agents, on the loopback address.

Task agents reach everything that is not a built-in tool through here (design
section 10), so a capability is implemented once whichever runtime runs the
task agent. Each commission gets a token of its own; a request without a known
token is refused, and the token stops working when the commission ends.

What the gateway serves:

* plugin entries: every entry visible to automatic routing, as one tool each.
  A call sends the existing ``/runs`` request through
  ``PluginRunner.run_entry``, the same code the task brain uses for a direct
  call, and stopping the commission cancels the run;
* MCP tools of the servers the user configured (through the ``mcp_adapter``
  plugin, see ``capabilities.py``);
* the built-in tools (keyboard and mouse, browser) while they are available.
  They come in groups and are never listed up front: their definitions would
  be sent with every request of every task agent, whether it touches a
  browser or not. The search tool's description carries one line about each
  group on offer, with the group's first call. A group is handed over, how
  it is used and then the definitions of its tools (within
  ``GATEWAY_TOOL_GROUP_MAX_TOKENS``), in one of three ways, and its tools
  are from then on called by name through ``capability_call``:
  ``capability_search`` with ``group`` hands it over by itself; the group's
  first call (going to an address, taking a screenshot, and nothing else
  the same tool can do) is
  carried out and, when it went well, answered with the group in front of
  its result, so that a task which starts that way spends no request on
  loading (one that failed is answered as any failed call, and the group is
  still to be handed over); any other call
  to a tool of a group this token has not been handed is not carried out
  and is answered with the group, so that nothing but that first step is
  done before the task agent has read how the tools are used, and it can
  call again at once. Read, not just sent: a model writes all the calls
  of one reply before any of their answers reaches it, so a group handed
  over by one of them is in hand only from the next reply on, and the
  calls after it in the same reply (but the first call) are not carried
  out either. The runtime names the reply with every call
  (``GATEWAY_REPLY_META_KEY``); a client that names none is taken to
  have read what it was sent. A call is one small step, carried out by the
  provider the host was given; stopping the commission stops it. The
  commission occupies the desktop or the browser from its first call
  (``leases.py``), and a result may carry pictures (screenshots) next to its
  text. A tool that hands back pictures is offered only to a task agent
  whose model can see them;
* two tools of its own, ``capability_search`` and ``capability_call``. Only
  the entries the task brain picked as candidates are listed up front, within
  ``GATEWAY_TOOL_LIST_MAX_TOKENS``; the rest are found and called through
  these two, so a long plugin list does not fill every request.

Permissions are decided here, at the gateway's entry, by the same
``PermissionGate`` the built-in tools use.

A plugin entry that answers ``needs_confirmation`` did not carry the call out:
it wants the user to confirm first. The task agent must not answer for the user,
so the gateway puts the plugin's question to the user itself (the commission
waits for the user meanwhile) and hands the task agent the plugin's reply
together with what the user said. A result that asks for a clarification, and
a deferred one, go back to the task agent with a note, as before.

Transport: MCP over HTTP POST with JSON answers (``initialize``, ``ping``,
``tools/list``, ``tools/call``). The listener is started when the first
task agent needs it, on a port chosen by the system, and never at startup.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nekoclaw.host.capabilities import KIND_BUILTIN, Capability, CapabilityRegistry
from nekoclaw.host.leases import BuiltinLeases
from nekoclaw.host.ledger import Commission, Ledger
from nekoclaw.host.permissions import PermissionGate
from nekoclaw.host.plugin_runs import (
    PluginRunner,
    describe_result,
    llm_result_fields,
    plugin_terminal_status,
)
from nekoclaw.host.selection import bm25_scores, find_entry

logger = logging.getLogger(__name__)

SERVER_NAME = "nekoclaw-gateway"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MCP_PATH = "/mcp"
SEARCH_TOOL = "capability_search"
CALL_TOOL = "capability_call"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
_START_TIMEOUT_SECONDS = 10.0

_SEARCH_TOOL_DEFINITION = {
    "name": SEARCH_TOOL,
    "description": (
        "Find further capabilities of the host (plugin entries and tools of connected services) "
        "by what they do. Returns their names, descriptions and argument schemas. Call one with "
        f"{CALL_TOOL}."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What the capability should do."}},
        "required": ["query"],
    },
}

_CALL_TOOL_DEFINITION = {
    "name": CALL_TOOL,
    "description": f"Call a capability found with {SEARCH_TOOL}, by its name, with arguments matching its schema.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": f"The name {SEARCH_TOOL} returned."},
            "arguments": {"type": "object", "description": "Arguments for the capability."},
        },
        "required": ["name"],
    },
}


# Puts a question to the user on behalf of a commission and returns the
# answer as ``{"text": ..., "choice": ...}``.
AskUser = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def wants_confirmation(run_data: Any) -> bool:
    """Return whether a plugin result asks for the user's confirmation (not a clarification)."""
    if not isinstance(run_data, dict) or bool(run_data.get("observation_only")):
        return False
    status = str(run_data.get("status") or "").strip().lower()
    return bool(run_data.get("needs_confirmation")) or status == "confirm_required"


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class Grant:
    """What one commission's token gives access to."""

    commission_id: str
    token: str
    # Plugins whose entries are listed up front.
    candidate_plugin_ids: tuple[str, ...] = ()
    # Whether the model of this commission's task agent can see pictures.
    supports_images: bool = False
    # The groups of built-in tools that were handed over through this token:
    # how they are used and their tools' definitions. A new token starts
    # without any, as the session it is for may no longer hold them.
    loaded_groups: set[str] = field(default_factory=set)
    # The model reply during which each of them was handed over, as the
    # client names its replies (``GATEWAY_REPLY_META_KEY``); nothing for a
    # group handed to a client that names none.
    handed_in: dict[str, int | str] = field(default_factory=dict)
    calls: int = 0
    in_flight: set[asyncio.Task[Any]] = field(default_factory=set)


def _text(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _reply_of(params: dict[str, Any]) -> int | str | None:
    """Return the model reply a ``tools/call`` says it was written in, if it names one."""
    from config.nekoclaw_settings import GATEWAY_REPLY_META_KEY

    meta = params.get("_meta")
    reply = meta.get(GATEWAY_REPLY_META_KEY) if isinstance(meta, dict) else None
    if isinstance(reply, bool) or not isinstance(reply, (int, str)):
        return None
    return reply


def _in_hand(grant: Grant, group: str, reply: int | str | None) -> bool:
    """Whether a call was written with a group in hand: handed over, and not in the reply the call is of."""
    if group not in grant.loaded_groups:
        return False
    return reply is None or grant.handed_in.get(group) != reply


def _hand_over(grant: Grant, group: str, reply: int | str | None) -> None:
    """Note that a group goes back with the answer to a call of ``reply``."""
    if group in grant.loaded_groups:
        # Handed over before (loaded a second time): when it first was stands.
        return
    grant.loaded_groups.add(group)
    if reply is not None:
        grant.handed_in[group] = reply


def _text_and_images(text: str, images: Any, *, error: bool = False) -> dict[str, Any]:
    """Return an MCP result of one text block followed by image blocks."""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for image in images:
        content.append({"type": "image", "data": image.data, "mimeType": image.mime_type})
    return {"content": content, "isError": error}


def _group_name(group: str) -> str:
    """Return a group of built-in tools by the name a model knows it by."""
    from config.prompts.prompts_nekoclaw_builtin import BUILTIN_GROUP_NAMES

    return BUILTIN_GROUP_NAMES.get(group, group)


def _groups_of(capabilities: list[Capability]) -> list[str]:
    """Return the groups of the built-in tools among ``capabilities``, in the order a model is told of them."""
    from config.prompts.prompts_nekoclaw_builtin import BUILTIN_GROUP_NAMES

    # Only a built-in tool has a group.
    present = {item.group for item in capabilities if item.group}
    return [group for group in BUILTIN_GROUP_NAMES if group in present]


def _first_call(group: str, capabilities: list[Capability]) -> tuple[Capability, dict[str, Any]] | None:
    """Return the tool a group is started with and what is written of that call, if the tool is among ``capabilities``."""
    from config.prompts.prompts_nekoclaw_builtin import BUILTIN_GROUP_FIRST_CALLS

    first = BUILTIN_GROUP_FIRST_CALLS.get(group)
    if first is None:
        return None
    for item in capabilities:
        if item.group == group and item.tool_name == first["name"]:
            return item, first
    return None


def _search_tool_definition(capabilities: list[Capability]) -> dict[str, Any]:
    """Return the search tool as offered to a task agent that can call ``capabilities``.

    Without a built-in tool among them it is the plain search tool, word
    for word. With some, its description ends in one line about each of
    their groups, with the group's first call, which is all a request
    carries of a group that was not loaded, and it takes ``group``.
    """
    groups = _groups_of(capabilities)
    if not groups:
        return _SEARCH_TOOL_DEFINITION
    from config.prompts.prompts_nekoclaw_builtin import (
        BUILTIN_GROUP_SUMMARIES,
        TOOL_GROUP_ARGUMENT,
        TOOL_GROUP_FIRST_CALL,
        TOOL_GROUPS_INTRO,
    )

    lines = [str(_SEARCH_TOOL_DEFINITION["description"]), TOOL_GROUPS_INTRO.format(call_tool=CALL_TOOL)]
    for group in groups:
        # Told only when the tool is there to be called.
        found = _first_call(group, capabilities)
        how = TOOL_GROUP_FIRST_CALL.format(name=found[0].tool_name, example=found[1]["example"]) if found else ""
        lines.append(f"- {_group_name(group)}: {BUILTIN_GROUP_SUMMARIES[group]}{how}")
    return {
        "name": SEARCH_TOOL,
        "description": "\n".join(lines),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What the capability should do."},
                "group": {
                    "type": "string",
                    "enum": [_group_name(group) for group in groups],
                    "description": TOOL_GROUP_ARGUMENT,
                },
            },
        },
    }


def _is_first_call(capability: Capability, arguments: dict[str, Any]) -> bool:
    """Whether a call is the first call of its group: the one carried out although the group was not loaded."""
    found = _first_call(capability.group or "", [capability])
    if found is None:
        return False
    first = found[1]
    # Only the step itself: the tool can do more under the same name.
    if any(arguments.get(key) not in allowed for key, allowed in first["arguments"].items()):
        return False
    return all(isinstance(arguments.get(key), str) and arguments[key].strip() for key in first["given"])


def _group_text(group: str, tools: list[Capability]) -> str:
    """Return what loading a group hands over: how it is used, then its tools' definitions."""
    from config.nekoclaw_settings import GATEWAY_TOOL_GROUP_MAX_TOKENS
    from config.prompts.prompts_nekoclaw_builtin import BUILTIN_GROUP_GUIDANCE, TOOL_GROUP_LOADED_HEADER
    from utils.tokenize import truncate_to_tokens

    parts = [
        TOOL_GROUP_LOADED_HEADER.format(name=_group_name(group), call_tool=CALL_TOOL),
        BUILTIN_GROUP_GUIDANCE[group],
        "\n".join(json.dumps(_definition(capability), ensure_ascii=False) for capability in tools),
    ]
    text = "\n\n".join(parts)
    cut = truncate_to_tokens(text, GATEWAY_TOOL_GROUP_MAX_TOKENS)
    if len(cut) < len(text):
        # Ours never are (a test keeps them inside the cap); said for a
        # provider whose definitions are larger.
        logger.warning("the tools of the group %s do not fit what loading a group may hand over; cut", group)
    return cut


def _definition(capability: Capability) -> dict[str, Any]:
    from config.nekoclaw_settings import GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS
    from utils.tokenize import truncate_to_tokens

    if capability.kind == KIND_BUILTIN:
        # Our own definitions, served as written: the name says what it is.
        return {
            "name": capability.tool_name,
            "description": capability.description,
            "inputSchema": capability.input_schema,
        }
    description = capability.description or capability.title
    return {
        "name": capability.tool_name,
        "description": truncate_to_tokens(f"{capability.title}. {description}", GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS),
        "inputSchema": capability.input_schema,
    }


class CapabilityGateway:
    """Serves capabilities to task agents. See the module docstring."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        runner: PluginRunner,
        permissions: PermissionGate,
        ledger: Ledger,
        ask_user: AskUser | None = None,
        leases: BuiltinLeases | None = None,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._permissions = permissions
        self._ledger = ledger
        self._ask_user = ask_user
        # Who occupies the desktop and the browser; without it nothing is
        # occupied and every call of a built-in tool goes straight through.
        self._leases = leases
        self._grants: dict[str, Grant] = {}  # by token
        self._by_commission: dict[str, Grant] = {}
        self._server: Any = None
        self._serve_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._port: int | None = None

    def set_ask_user(self, ask_user: AskUser | None) -> None:
        """Set how a plugin's request for confirmation is put to the user."""
        self._ask_user = ask_user

    # -- tokens -----------------------------------------------------------

    def issue(self, commission_id: str, *, candidate_plugin_ids: Any = (), supports_images: bool = False) -> str:
        """Return a new token for a commission, replacing any earlier one.

        ``supports_images`` says whether the model of the commission's task
        agent can see pictures; tools that need that are offered only then.
        """
        self.revoke(commission_id)
        grant = Grant(
            commission_id=commission_id,
            token=secrets.token_urlsafe(32),
            candidate_plugin_ids=tuple(str(item) for item in candidate_plugin_ids),
            supports_images=bool(supports_images),
        )
        self._grants[grant.token] = grant
        self._by_commission[commission_id] = grant
        return grant.token

    def images_refused(self, commission_id: str) -> None:
        """The model of a commission's task agent turned out not to take pictures.

        The tools that work from pictures are no longer offered to it nor
        carried out for it: it would go on clicking a screen it cannot see.
        """
        grant = self._by_commission.get(commission_id)
        if grant is not None:
            grant.supports_images = False

    def revoke(self, commission_id: str) -> None:
        """Make a commission's token useless and stop its calls that are still going."""
        grant = self._by_commission.pop(commission_id, None)
        if grant is None:
            return
        self._grants.pop(grant.token, None)
        for task in list(grant.in_flight):
            task.cancel()

    def _grant(self, token: str | None) -> Grant | None:
        if not token:
            return None
        for known, grant in self._grants.items():
            if hmac.compare_digest(known, token):
                return grant
        return None

    # -- listener ---------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._port is not None

    @property
    def port(self) -> int | None:
        """Return the port the gateway listens on, or None while it does not listen."""
        return self._port

    @property
    def url(self) -> str:
        if self._port is None:
            raise RuntimeError("the gateway is not listening")
        return f"http://127.0.0.1:{self._port}{MCP_PATH}"

    async def start(self) -> str:
        """Start listening if not yet, and return the endpoint URL."""
        async with self._start_lock:
            if self._port is not None:
                return self.url
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
                        error = task.exception()
                        raise RuntimeError(f"the gateway could not start: {error}")
                    if loop.time() > deadline:
                        raise RuntimeError("the gateway did not start in time")
                    await asyncio.sleep(0.01)
            except BaseException:
                # Not registered yet, so ``stop`` could never reach it: a
                # listener that did not start, or whose start was given up
                # on, is closed here.
                await _close_listener(server, task)
                raise
            sockets = [sock for listener in server.servers for sock in listener.sockets]
            self._port = int(sockets[0].getsockname()[1])
            self._server = server
            self._serve_task = task
            return self.url

    async def stop(self) -> None:
        """Stop listening and drop every token."""
        for commission_id in list(self._by_commission):
            self.revoke(commission_id)
        server, self._server = self._server, None
        task, self._serve_task = self._serve_task, None
        self._port = None
        await _close_listener(server, task)

    # -- HTTP -------------------------------------------------------------

    async def _asgi(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope["type"] != "http":
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}

        async def respond(status: int, body: Any = None, extra: dict[str, str] | None = None) -> None:
            payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            response_headers = [(b"content-length", str(len(payload)).encode())]
            if payload:
                response_headers.append((b"content-type", b"application/json"))
            for name, value in (extra or {}).items():
                response_headers.append((name.encode(), value.encode()))
            await send({"type": "http.response.start", "status": status, "headers": response_headers})
            await send({"type": "http.response.body", "body": payload})

        if scope.get("path") != MCP_PATH:
            await respond(404, {"error": "not found"})
            return
        if scope.get("method") != "POST":
            await respond(405, {"error": "use POST"}, {"allow": "POST"})
            return
        # A page in a browser cannot be allowed to drive this, even with a token.
        origin = headers.get("origin")
        if origin is not None and _origin_host(origin) not in _LOOPBACK_HOSTS:
            await respond(403, {"error": "forbidden origin"})
            return
        authorization = headers.get("authorization", "")
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
        grant = self._grant(token)
        if grant is None:
            await respond(401, {"error": "unknown or missing token"}, {"www-authenticate": "Bearer"})
            return

        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            await respond(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            return
        # The token may have been revoked or replaced while the body arrived;
        # such a request is not yet among the calls ``revoke`` stops.
        if self._grants.get(grant.token) is not grant:
            await respond(401, {"error": "unknown or missing token"}, {"www-authenticate": "Bearer"})
            return

        work = asyncio.ensure_future(self.handle(grant, payload))
        grant.in_flight.add(work)
        disconnect = asyncio.ensure_future(_wait_disconnect(receive))
        try:
            await asyncio.wait({work, disconnect}, return_when=asyncio.FIRST_COMPLETED)
            if not work.done():
                # The task agent gave up on this call (its run was stopped):
                # stop the work, which also cancels a plugin run under way.
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                return
            try:
                answer = work.result()
            except asyncio.CancelledError:
                await respond(499, {"error": "cancelled"})
                return
        finally:
            grant.in_flight.discard(work)
            disconnect.cancel()
            if not work.done():
                # The listener itself is going away.
                work.cancel()
        if answer is None:
            await respond(202)
        else:
            await respond(200, answer)

    # -- JSON-RPC ---------------------------------------------------------

    async def handle(self, grant: Grant, payload: Any) -> Any:
        """Answer one JSON-RPC message, or a list of them. None for notifications."""
        if isinstance(payload, list):
            answers = [answer for answer in [await self._handle_one(grant, item) for item in payload] if answer]
            return answers or None
        return await self._handle_one(grant, payload)

    async def _handle_one(self, grant: Grant, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        request_id = message.get("id")
        is_notification = "id" not in message
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        try:
            result = await self._dispatch(grant, message["method"], params)
        except JsonRpcError as exc:
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": exc.message}}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the caller, never raised into the server
            logger.exception("gateway request %s failed", message.get("method"))
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def _dispatch(self, grant: Grant, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            return {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
            }
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": await self.tool_definitions(grant)}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise JsonRpcError(-32602, "tools/call needs a tool name")
            arguments = params.get("arguments")
            return await self.call_tool(
                grant, name, arguments if isinstance(arguments, dict) else {}, reply=_reply_of(params)
            )
        raise JsonRpcError(-32601, f"unknown method {method}")

    # -- tools ------------------------------------------------------------

    async def tool_definitions(self, grant: Grant) -> list[dict[str, Any]]:
        """Return the tools one commission is offered up front.

        The entries of its candidate plugins as far as their budget reaches,
        and the search and call tools when anything is left that was not
        listed. The built-in tools are never listed: the search tool's
        description tells of their groups, and a group is handed over when
        it is first used.
        """
        from config.nekoclaw_settings import GATEWAY_TOOL_LIST_MAX_TOKENS
        from utils.tokenize import count_tokens

        callable_now = self._offered(grant, await self._registry.callable_by_task_agents())
        if not callable_now:
            return []
        candidates = set(grant.candidate_plugin_ids)
        listed: list[dict[str, Any]] = []
        used = 0
        for capability in callable_now:
            # Plugin entries, and MCP tools when the adapter was among the
            # candidates. (A built-in tool belongs to no plugin, so it is
            # never one of them.)
            if capability.plugin_id not in candidates:
                continue
            definition = _definition(capability)
            cost = count_tokens(json.dumps(definition, ensure_ascii=False))
            if used + cost > GATEWAY_TOOL_LIST_MAX_TOKENS:
                continue
            listed.append(definition)
            used += cost
        if len(listed) < len(callable_now):
            listed.extend([_search_tool_definition(callable_now), _CALL_TOOL_DEFINITION])
        return listed

    @staticmethod
    def _offered(grant: Grant, capabilities: list[Capability]) -> list[Capability]:
        """Leave out the tools this commission's model can make nothing of."""
        if grant.supports_images:
            return capabilities
        return [item for item in capabilities if not item.needs_images]

    async def call_tool(
        self, grant: Grant, name: str, arguments: dict[str, Any], *, reply: int | str | None = None
    ) -> dict[str, Any]:
        """Carry out one ``tools/call`` and return its MCP result.

        ``reply`` is the model reply the call was written in, as the client
        names it (``GATEWAY_REPLY_META_KEY``), ``None`` for a client that
        names none.
        """
        grant.calls += 1
        if name == SEARCH_TOOL:
            group = arguments.get("group")
            if isinstance(group, str) and group.strip():
                return await self._load_group(grant, group.strip(), reply)
            return _text(await self._search(grant, str(arguments.get("query") or "")))
        if name == CALL_TOOL:
            target = arguments.get("name")
            if not isinstance(target, str) or not target:
                return _text(f"{CALL_TOOL} needs the name of a capability", error=True)
            inner = arguments.get("arguments")
            name, arguments = target, inner if isinstance(inner, dict) else {}
        callable_now = await self._registry.callable_by_task_agents()
        capability = next((item for item in self._offered(grant, callable_now) if item.tool_name == name), None)
        if capability is None:
            if any(item.tool_name == name for item in callable_now):
                # Left out by ``_offered``: it works from pictures.
                return _text(
                    f"{name} works from pictures, and the model in use does not take pictures: it cannot be used "
                    "in this task. Go on without it, or report that the task needs a model that can see.",
                    error=True,
                )
            return _text(f"no capability named {name}; use {SEARCH_TOOL} to find one", error=True)
        commission = await self._ledger.get(grant.commission_id)
        if commission is None or not commission.active:
            return _text("this commission is no longer under way", error=True)
        group = capability.group
        if not group or _in_hand(grant, group, reply):
            return await self._call_capability(commission, capability, arguments)
        # A tool of a group the task agent did not have in hand when it
        # wrote this call: how the group is used has not been read, and but
        # for the group's first call, which the task agent was shown, the
        # arguments were written without the tool's definition. Either way
        # the group goes back with the answer, so the calls of the replies
        # after this one go through.
        from config.prompts.prompts_nekoclaw_builtin import (
            TOOL_GROUP_FIRST_RESULT,
            TOOL_GROUP_NOT_LOADED,
            TOOL_GROUP_NOT_READ,
        )

        first = _is_first_call(capability, arguments)
        if group in grant.loaded_groups:
            # Handed over by an earlier call of this same reply (the calls
            # of one reply are carried out one after the other): the model
            # wrote this call before that answer reached it. The first call
            # needs no reading; anything else waits for the next reply, and
            # the group is not sent again.
            if first:
                return await self._call_capability(commission, capability, arguments)
            return _text(TOOL_GROUP_NOT_READ.format(tool=name, name=_group_name(group)), error=True)
        handed_over = _group_text(group, [item for item in self._offered(grant, callable_now) if item.group == group])
        if not first:
            # Not carried out: nothing is asked of the user, nothing is occupied.
            _hand_over(grant, group, reply)
            refusal = TOOL_GROUP_NOT_LOADED.format(tool=name, name=_group_name(group))
            return _text(f"{refusal}\n\n{handed_over}", error=True)
        # The first call (going to an address, taking a screenshot) is
        # carried out like any call, the user asked first and the group
        # occupied. The group is put in front of what it returns, as
        # a block of its own: each is capped by itself, and the runtime
        # keeps them apart (``GatewayResult.parts``), so that when it cuts
        # old results down the group stays whole next to a long page.
        result = await self._call_capability(commission, capability, arguments)
        if result.get("isError"):
            # A first call that failed (declined, the browser occupied, a
            # page that would not load) comes back as any failed call does,
            # and the group is still to be handed over: a runtime keeps an
            # error as one text, in which the group would be cut together
            # with whatever the error says.
            return result
        # Only now, with the answer on its way: a call that was stopped
        # handed nothing over.
        _hand_over(grant, group, reply)
        opening = {"type": "text", "text": f"{handed_over}\n\n{TOOL_GROUP_FIRST_RESULT}"}
        return {**result, "content": [opening, *result["content"]]}

    async def _load_group(self, grant: Grant, asked: str, reply: int | str | None = None) -> dict[str, Any]:
        """Hand a group of built-in tools over: how it is used and its tools' definitions.

        ``asked`` is the group as a model names it (the name the search
        tool's description gives it; the host's own name for it is taken
        too). What is handed over are the tools this commission's model can
        make something of. ``reply`` is the model reply the load was
        written in (see ``call_tool``): the group is in hand from the next.
        """
        from config.prompts.prompts_nekoclaw_builtin import BUILTIN_GROUP_NAMES

        wanted = asked.lower()
        group = next((key for key, known in BUILTIN_GROUP_NAMES.items() if wanted in (key, known)), None)
        callable_now = await self._registry.callable_by_task_agents()
        if group is None:
            on_offer = ", ".join(_group_name(item) for item in _groups_of(self._offered(grant, callable_now)))
            return _text(f"no tool group named {asked}; the groups on offer: {on_offer or 'none'}", error=True)
        name = _group_name(group)
        tools = [item for item in callable_now if item.group == group]
        if not tools:
            # A group there is, but none of its tools can be called now.
            reason = next((item.unavailable_reason for item in self._registry.builtins() if item.group == group), "")
            return _text(
                f"The {name} tools are not available right now: {reason or 'switched off'}. "
                "Go on without them, or report that the task needs them.",
                error=True,
            )
        offered = self._offered(grant, tools)
        if not offered:
            return _text(
                f"The {name} tools work from pictures, and the model in use does not take pictures: they cannot "
                "be used in this task. Go on without them, or report that the task needs a model that can see.",
                error=True,
            )
        _hand_over(grant, group, reply)
        return _text(_group_text(group, offered))

    async def _search(self, grant: Grant, query: str) -> str:
        from config.nekoclaw_settings import GATEWAY_RESULT_MAX_TOKENS, GATEWAY_SEARCH_RESULTS
        from config.prompts.prompts_nekoclaw_builtin import BUILTIN_GROUP_SUMMARIES, TOOL_GROUP_POINTER
        from utils.tokenize import truncate_to_tokens

        capabilities = self._offered(grant, await self._registry.callable_by_task_agents())
        if not capabilities:
            return "No capabilities are available."
        # A built-in tool is not found on its own: its definition is of no
        # use without how its group is used, and the rest of the group is
        # needed with it. A group is searched as one thing, by all its tools
        # say, and a match names the group and how to load it.
        found: list[Capability | str] = [item for item in capabilities if not item.group]
        searched = [f"{item.title} {item.description} {item.tool_name}" for item in found]
        for group in _groups_of(capabilities):
            tools = [item for item in capabilities if item.group == group]
            found.append(group)
            said = [_group_name(group), BUILTIN_GROUP_SUMMARIES[group]]
            said.extend(f"{item.tool_name} {item.description}" for item in tools)
            searched.append(" ".join(said))
        ranked = sorted(zip(bm25_scores(query, searched), found, strict=True), key=lambda pair: pair[0], reverse=True)
        matches = [item for score, item in ranked if score > 0][:GATEWAY_SEARCH_RESULTS]
        if not matches:
            return f"No capability matches {query!r}."
        blocks = [
            TOOL_GROUP_POINTER.format(
                name=_group_name(item), summary=BUILTIN_GROUP_SUMMARIES[item], search_tool=SEARCH_TOOL
            )
            if isinstance(item, str)
            else json.dumps(_definition(item), ensure_ascii=False)
            for item in matches
        ]
        return truncate_to_tokens("\n".join(blocks), GATEWAY_RESULT_MAX_TOKENS)

    async def _call_capability(
        self,
        commission: Commission,
        capability: Capability,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        from config.nekoclaw_settings import GATEWAY_RESULT_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        decision = await self._permissions.check(
            commission.id,
            capability.permission,
            key=capability.permission_key,
            detail={"tool": capability.tool_name, "capability": capability.id, "title": capability.title},
        )
        if not decision.allow:
            await self._ledger.append_event(
                commission.id, "capability_denied", {"capability": capability.id, "reason": decision.reason}
            )
            return _text(decision.reason or "permission denied", error=True)

        if capability.kind == KIND_BUILTIN:
            return await self._call_builtin(commission, capability, arguments)

        async def run_created(run_id: str) -> None:
            await self._ledger.append_event(
                commission.id,
                "plugin_run",
                {"run_id": run_id, "plugin_id": capability.plugin_id, "entry_id": capability.entry_id, "via": "gateway"},
            )

        # The entry's result contract as it stands for this call: the plugin
        # list may no longer show the entry once the run is over.
        _plugin, entry = find_entry(await self._registry.plugins(), str(capability.plugin_id), capability.entry_id)
        outcome = await self._runner.run_entry(
            commission_id=commission.id,
            plugin_id=capability.plugin_id,
            entry_id=capability.entry_id,
            args=arguments,
            lanlan_name=commission.lanlan_name,
            conversation_id=commission.conversation_id,
            latest_user_request=commission.user_text,
            on_run_created=run_created,
        )
        text = describe_result(entry, outcome, lang=commission.lang)
        terminal = plugin_terminal_status(outcome.success, outcome.data)
        await self._ledger.append_event(
            commission.id,
            "plugin_run_ended",
            {"run_id": outcome.run_id, "status": outcome.status, "result": terminal, "via": "gateway"},
        )
        if terminal == "failed":
            return _text(truncate_to_tokens(text or str(outcome.error or "failed"), GATEWAY_RESULT_MAX_TOKENS), error=True)
        if entry is not None and llm_result_fields(entry) is None and outcome.data is not None:
            # The entry does not say which fields a model should read; a
            # task agent gets the data itself so it can go on working with it.
            # Without the entry nothing says the data may be shown.
            text = f"{text}\n{json.dumps(outcome.data, ensure_ascii=False, default=str)}"
        if terminal == "blocked":
            answer = await self._confirm_with_user(commission, capability, text) if wants_confirmation(outcome.data) else None
            if answer is not None:
                text = (
                    "[The plugin did not carry this out: it needs the user's confirmation first. The user was "
                    f"asked and answered: {json.dumps(answer, ensure_ascii=False)}. Only if that is a go-ahead, "
                    "call it again the way the plugin asks; otherwise leave it.]\n" + text
                )
            else:
                text = (
                    "[The plugin did not carry this out: it needs the user's confirmation or a clarification first.]\n"
                    + text
                )
        elif isinstance(outcome.data, dict) and outcome.data.get("deferred") is True:
            text = "[Accepted; the plugin completes this later on its own.]\n" + text
        return _text(truncate_to_tokens(text, GATEWAY_RESULT_MAX_TOKENS))

    async def _confirm_with_user(self, commission: Commission, capability: Capability, text: str) -> str | None:
        """Put a plugin's request for confirmation to the user; return the answer, or None if it cannot be asked.

        The commission waits for the user meanwhile. The task agent is not asked
        and cannot answer in the user's place.
        """
        from config.nekoclaw_settings import GATEWAY_CONFIRMATION_QUESTION_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        if self._ask_user is None:
            return None
        question = {
            "kind": "confirmation",
            "question": truncate_to_tokens(text or capability.title, GATEWAY_CONFIRMATION_QUESTION_MAX_TOKENS),
            "capability": capability.id,
            "title": capability.title,
        }
        try:
            answer = await self._ask_user(commission.id, question)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - it could not be asked; the task agent is told so
            logger.warning("a plugin's request for confirmation could not be put to the user: %s", type(exc).__name__)
            return None
        return str(answer.get("text") or "").strip()

    async def _call_builtin(
        self, commission: Commission, capability: Capability, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Carry out one call of a built-in tool for a commission.

        The commission occupies the tool's group first and keeps it until its
        run ends, it waits for the user or it is over (``leases.py``). The
        call itself is not written to the ledger: the task agent's own
        ``tool_start`` and ``tool_end`` events already show it, and a task
        of many small steps would double its rows.
        """
        from config.nekoclaw_settings import GATEWAY_RESULT_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens

        group = capability.group or ""
        if self._leases is not None and group:
            in_the_way = await self._leases.acquire(group, commission.id, title=commission.title)
            if in_the_way is not None:
                await self._ledger.append_event(
                    commission.id, "builtin_occupied", {"capability": capability.id, "by": in_the_way.team}
                )
                what = _group_name(group)
                return _text(
                    f"The {what} is in use by another task ({in_the_way.title or 'no title'}) and did not come "
                    "free in time. Nothing was done. Go on with what does not need it, try again later, or "
                    "report that you are waiting for it.",
                    error=True,
                )
            # The wait can be long; the commission may have been stopped or
            # have started waiting for the user meanwhile.
            current = await self._ledger.get(commission.id)
            if current is None or current.status != "working":
                self._leases.release(commission.id)
                return _text("this commission is no longer under way", error=True)
        try:
            outcome = await self._registry.run_builtin(capability, commission_id=commission.id, arguments=arguments)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the task agent is told and decides how to go on
            logger.exception("built-in tool %s failed", capability.id)
            return _text(f"{capability.tool_name} failed: {type(exc).__name__}", error=True)
        text = truncate_to_tokens(outcome.text or ("done" if outcome.success else "failed"), GATEWAY_RESULT_MAX_TOKENS)
        if not outcome.images:
            return _text(text, error=not outcome.success)
        grant = self._by_commission.get(commission.id)
        if grant is None or not grant.supports_images:
            # Not offered to such a task agent as a rule; reached through the
            # call tool it is told what it missed rather than sent a picture.
            return _text(f"{text}\n[A picture was returned, but this model cannot see pictures.]", error=not outcome.success)
        return _text_and_images(text, outcome.images, error=not outcome.success)


async def _close_listener(server: Any, task: "asyncio.Task[Any] | None") -> None:
    """Ask a listener to stop and wait for it, cancelling it if it takes too long."""
    if server is not None:
        server.should_exit = True
    if task is None:
        return
    try:
        # ``asyncio.wait`` never raises what the listener raised, so a cancel
        # seen here is always one of the caller waiting.
        await asyncio.wait({task}, timeout=5.0)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    except asyncio.CancelledError:
        # The caller goes away; the listener is closed without it, and kept
        # referenced until it has ended.
        task.cancel()
        _closing_listeners.add(task)
        task.add_done_callback(_forget_closing_listener)
        raise
    if not task.cancelled() and task.exception() is not None:
        logger.debug("gateway listener ended with an error", exc_info=task.exception())


# Listeners whose closing was left to run on its own after the caller went away.
_closing_listeners: set["asyncio.Task[Any]"] = set()


def _forget_closing_listener(task: "asyncio.Task[Any]") -> None:
    _closing_listeners.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.debug("gateway listener ended with an error", exc_info=task.exception())


def _origin_host(origin: str) -> str:
    rest = origin.split("://", 1)[-1]
    host = rest.split("/", 1)[0]
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


async def _wait_disconnect(receive: Callable[..., Any]) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
