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

"""The capability gateway: an MCP service for familiars, on the loopback address.

Familiars reach everything that is not a built-in tool through here (design
section 10), so a capability is implemented once whichever runtime runs the
familiar. Each commission gets a token of its own; a request without a known
token is refused, and the token stops working when the commission ends.

What the gateway serves:

* plugin entries: every entry visible to automatic routing, as one tool each.
  A call sends the existing ``/runs`` request through
  ``PluginRunner.run_entry``, the same code the task brain uses for a direct
  call, and stopping the commission cancels the run;
* MCP tools of the servers the user configured (through the ``mcp_adapter``
  plugin, see ``capabilities.py``);
* two tools of its own, ``capability_search`` and ``capability_call``. Only
  the entries the task brain picked as candidates are listed up front, within
  ``GATEWAY_TOOL_LIST_MAX_TOKENS``; the rest are found and called through
  these two, so a long plugin list does not fill every request.

Permissions are decided here, at the gateway's entry, by the same
``PermissionGate`` the built-in tools use.

Transport: MCP over HTTP POST with JSON answers (``initialize``, ``ping``,
``tools/list``, ``tools/call``). The listener is started when the first
familiar needs it, on a port chosen by the system, and never at startup.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nekoclaw.host.capabilities import Capability, CapabilityRegistry
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
    calls: int = 0
    in_flight: set[asyncio.Task[Any]] = field(default_factory=set)


def _text(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _definition(capability: Capability) -> dict[str, Any]:
    from config.nekoclaw_settings import GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS
    from utils.tokenize import truncate_to_tokens

    description = capability.description or capability.title
    return {
        "name": capability.tool_name,
        "description": truncate_to_tokens(f"{capability.title}. {description}", GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS),
        "inputSchema": capability.input_schema,
    }


class CapabilityGateway:
    """Serves capabilities to familiars. See the module docstring."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        runner: PluginRunner,
        permissions: PermissionGate,
        ledger: Ledger,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._permissions = permissions
        self._ledger = ledger
        self._grants: dict[str, Grant] = {}  # by token
        self._by_commission: dict[str, Grant] = {}
        self._server: Any = None
        self._serve_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._port: int | None = None

    # -- tokens -----------------------------------------------------------

    def issue(self, commission_id: str, *, candidate_plugin_ids: Any = ()) -> str:
        """Return a new token for a commission, replacing any earlier one."""
        self.revoke(commission_id)
        grant = Grant(
            commission_id=commission_id,
            token=secrets.token_urlsafe(32),
            candidate_plugin_ids=tuple(str(item) for item in candidate_plugin_ids),
        )
        self._grants[grant.token] = grant
        self._by_commission[commission_id] = grant
        return grant.token

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
            while not server.started:
                if task.done():
                    error = task.exception()
                    raise RuntimeError(f"the gateway could not start: {error}")
                if loop.time() > deadline:
                    server.should_exit = True
                    raise RuntimeError("the gateway did not start in time")
                await asyncio.sleep(0.01)
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
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, 5.0)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except Exception:  # noqa: BLE001 - a failing listener is gone either way
                logger.debug("gateway listener ended with an error", exc_info=True)

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

        work = asyncio.ensure_future(self.handle(grant, payload))
        grant.in_flight.add(work)
        disconnect = asyncio.ensure_future(_wait_disconnect(receive))
        try:
            await asyncio.wait({work, disconnect}, return_when=asyncio.FIRST_COMPLETED)
            if not work.done():
                # The familiar gave up on this call (its run was stopped):
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
            return await self.call_tool(grant, name, arguments if isinstance(arguments, dict) else {})
        raise JsonRpcError(-32601, f"unknown method {method}")

    # -- tools ------------------------------------------------------------

    async def tool_definitions(self, grant: Grant) -> list[dict[str, Any]]:
        """Return the tools one commission is offered up front.

        The entries of its candidate plugins, as far as the token budget
        reaches, and the search and call tools when anything is left that was
        not listed.
        """
        from config.nekoclaw_settings import GATEWAY_TOOL_LIST_MAX_TOKENS
        from utils.tokenize import count_tokens

        callable_now = await self._registry.callable_by_familiars()
        if not callable_now:
            return []
        candidates = set(grant.candidate_plugin_ids)
        listed: list[dict[str, Any]] = []
        used = 0
        for capability in callable_now:
            # Plugin entries, and MCP tools when the adapter was among the candidates.
            if capability.plugin_id not in candidates:
                continue
            definition = _definition(capability)
            cost = count_tokens(json.dumps(definition, ensure_ascii=False))
            if used + cost > GATEWAY_TOOL_LIST_MAX_TOKENS:
                continue
            listed.append(definition)
            used += cost
        if len(listed) < len(callable_now):
            listed.extend([_SEARCH_TOOL_DEFINITION, _CALL_TOOL_DEFINITION])
        return listed

    async def call_tool(self, grant: Grant, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Carry out one ``tools/call`` and return its MCP result."""
        grant.calls += 1
        if name == SEARCH_TOOL:
            return _text(await self._search(str(arguments.get("query") or "")))
        if name == CALL_TOOL:
            target = arguments.get("name")
            if not isinstance(target, str) or not target:
                return _text(f"{CALL_TOOL} needs the name of a capability", error=True)
            inner = arguments.get("arguments")
            name, arguments = target, inner if isinstance(inner, dict) else {}
        capability = next(
            (item for item in await self._registry.callable_by_familiars() if item.tool_name == name), None
        )
        if capability is None:
            return _text(f"no capability named {name}; use {SEARCH_TOOL} to find one", error=True)
        commission = await self._ledger.get(grant.commission_id)
        if commission is None or not commission.active:
            return _text("this commission is no longer under way", error=True)
        return await self._call_capability(commission, capability, arguments)

    async def _search(self, query: str) -> str:
        from config.nekoclaw_settings import GATEWAY_RESULT_MAX_TOKENS, GATEWAY_SEARCH_RESULTS
        from utils.tokenize import truncate_to_tokens

        capabilities = await self._registry.callable_by_familiars()
        if not capabilities:
            return "No capabilities are available."
        scores = bm25_scores(query, [f"{item.title} {item.description} {item.tool_name}" for item in capabilities])
        ranked = sorted(zip(scores, capabilities, strict=True), key=lambda pair: pair[0], reverse=True)
        matches = [capability for score, capability in ranked if score > 0][:GATEWAY_SEARCH_RESULTS]
        if not matches:
            return f"No capability matches {query!r}."
        blocks = [json.dumps(_definition(capability), ensure_ascii=False) for capability in matches]
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

        async def run_created(run_id: str) -> None:
            await self._ledger.append_event(
                commission.id,
                "plugin_run",
                {"run_id": run_id, "plugin_id": capability.plugin_id, "entry_id": capability.entry_id, "via": "gateway"},
            )

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
        _plugin, entry = find_entry(await self._registry.plugins(), str(capability.plugin_id), capability.entry_id)
        text = describe_result(entry, outcome, lang=commission.lang)
        terminal = plugin_terminal_status(outcome.success, outcome.data)
        await self._ledger.append_event(
            commission.id,
            "plugin_run_ended",
            {"run_id": outcome.run_id, "status": outcome.status, "result": terminal, "via": "gateway"},
        )
        if terminal == "failed":
            return _text(truncate_to_tokens(text or str(outcome.error or "failed"), GATEWAY_RESULT_MAX_TOKENS), error=True)
        if llm_result_fields(entry) is None and outcome.data is not None:
            # The entry does not say which fields a model should read; a
            # familiar gets the data itself so it can go on working with it.
            text = f"{text}\n{json.dumps(outcome.data, ensure_ascii=False, default=str)}"
        if terminal == "blocked":
            text = (
                "[The plugin did not carry this out: it needs the user's confirmation or a clarification first.]\n"
                + text
            )
        elif isinstance(outcome.data, dict) and outcome.data.get("deferred") is True:
            text = "[Accepted; the plugin completes this later on its own.]\n" + text
        return _text(truncate_to_tokens(text, GATEWAY_RESULT_MAX_TOKENS))


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
