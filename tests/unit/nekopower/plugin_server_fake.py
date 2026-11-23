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

"""An in-process plugin server for tests: the real ``/runs`` routes, fake plugins.

The run routes, the run service and the run manager (state machine, export
store, cancel propagation) are the production code. Only the last step is
replaced: instead of sending the call to a plugin child process,
``trigger_plugin`` runs a handler registered here. ``/plugins`` is served from
a catalog the test supplies, in the shape the real listing has.

Callers reach the app through ``FakePluginServer.client``, an
``httpx.AsyncClient`` on an ASGI transport, so nothing listens on a port.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import httpx

PLUGIN_SERVER_URL = "http://127.0.0.1:48916"

# (plugin_id, entry_id) -> coroutine function taking the call's args and
# returning the plugin's response envelope.
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def finish(data: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Return what a plugin's ``finish(...)`` puts on the wire."""
    from plugin.sdk.shared.core.finish import build_finish_envelope

    return dict(build_finish_envelope(data=data, **kwargs))


def failure(message: str, *, code: str = "PLUGIN_ERROR") -> dict[str, Any]:
    """Return the envelope of a plugin call that failed."""
    return {
        "success": False,
        "code": 1,
        "data": None,
        "message": message,
        "error": {"code": code, "message": message},
        "meta": {},
    }


class _FakeHost:
    def __init__(self, plugin_id: str, cancelled: list[tuple[str, str]]) -> None:
        self._plugin_id = plugin_id
        self._cancelled = cancelled

    def is_alive(self) -> bool:
        return True

    async def cancel_run(self, run_id: str) -> None:
        self._cancelled.append((self._plugin_id, run_id))


class FakePluginServer:
    """Real run routes over fake plugin entries. Build with ``install``."""

    def __init__(self) -> None:
        self.catalog: list[dict[str, Any]] = []
        self.handlers: dict[tuple[str, str], Handler] = {}
        # One item per plugin call: {"plugin_id", "entry_id", "args", "task_id"}.
        self.calls: list[dict[str, Any]] = []
        # (plugin_id, run_id) for every cancel that reached a plugin host.
        self.cancelled: list[tuple[str, str]] = []
        # Bodies of every POST /runs, as the route received them.
        self.run_requests: list[dict[str, Any]] = []
        self.plugins_requests = 0
        self.app: Any = None
        self._manager: Any = None

    # -- setup ------------------------------------------------------------

    @classmethod
    def install(cls, monkeypatch: Any) -> "FakePluginServer":
        """Create the app and route the run manager to this object's handlers."""
        from fastapi import FastAPI, Request

        from plugin.core.state import state
        from plugin.runs import manager
        from plugin.server.infrastructure.exceptions import register_exception_handlers
        from plugin.server.routes import runs as runs_routes

        server = cls()
        server._manager = manager

        async def trigger_plugin(*, plugin_id: str, entry_id: str, args: dict[str, Any], task_id: Any, client_host: Any) -> Any:
            del client_host
            server.calls.append(
                {"plugin_id": plugin_id, "entry_id": entry_id, "args": args, "task_id": task_id}
            )
            handler = server.handlers.get((plugin_id, entry_id))
            if handler is None:
                return SimpleNamespace(plugin_response=failure(f"no such entry {plugin_id}.{entry_id}"))
            return SimpleNamespace(plugin_response=await handler(args))

        monkeypatch.setattr(manager, "trigger_plugin", trigger_plugin)
        monkeypatch.setattr(manager, "_run_store", manager.InMemoryRunStore())
        monkeypatch.setattr(manager, "_export_store", manager.InMemoryExportStore())
        monkeypatch.setattr(manager, "_active_run_tasks", {})
        monkeypatch.setattr(manager, "_publish_bus_record", lambda **_kwargs: None)

        def hosts_snapshot(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                str(plugin["id"]): _FakeHost(str(plugin["id"]), server.cancelled)
                for plugin in server.catalog
            }

        monkeypatch.setattr(state, "get_plugin_hosts_snapshot_cached", hosts_snapshot)

        app = FastAPI()
        register_exception_handlers(app)

        @app.middleware("http")
        async def record_run_requests(request: Request, call_next: Any) -> Any:
            if request.method == "POST" and request.url.path == "/runs":
                body = await request.body()
                import json

                server.run_requests.append(json.loads(body))

                async def receive() -> dict[str, Any]:
                    return {"type": "http.request", "body": body, "more_body": False}

                request = Request(request.scope, receive)
            return await call_next(request)

        @app.get("/plugins")
        async def list_plugins() -> dict[str, Any]:
            server.plugins_requests += 1
            return {"plugins": server.catalog, "message": ""}

        app.include_router(runs_routes.router)
        server.app = app
        return server

    # -- access -----------------------------------------------------------

    def client(self, **kwargs: Any) -> httpx.AsyncClient:
        """Return a client whose requests look like loopback calls without an Origin."""
        kwargs.pop("proxy", None)
        kwargs.pop("trust_env", None)
        return _REAL_ASYNC_CLIENT(
            transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 50123)),
            **kwargs,
        )

    def add_plugin(self, plugin: dict[str, Any], **handlers: Handler) -> None:
        """Register a running plugin and the handlers of its entries."""
        self.catalog.append({"status": "running", **plugin})
        for entry_id, handler in handlers.items():
            self.handlers[(str(plugin["id"]), entry_id)] = handler

    def run(self, run_id: str) -> Any:
        return self._manager.get_run(run_id)

    def runs(self) -> list[Any]:
        return list(self._manager.list_runs())

    def report_progress(self, plugin_id: str, run_id: str, **patch: Any) -> None:
        """Do what a plugin's progress update does to its run."""
        self._manager.update_run_from_plugin(from_plugin=plugin_id, run_id=run_id, patch=patch)

    async def settle(self) -> None:
        """Let run tasks that are ready to finish do so."""
        for _ in range(5):
            await asyncio.sleep(0)


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def entry(
    entry_id: str,
    description: str = "",
    *,
    input_schema: dict[str, Any] | None = None,
    timeout: Any = None,
    metadata: dict[str, Any] | None = None,
    llm_result_fields: list[str] | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Return an entry descriptor in the shape ``GET /plugins`` lists."""
    return {
        "id": entry_id,
        "name": name or entry_id,
        "description": description,
        "event_key": f"plugin_entry:{entry_id}",
        "timeout": timeout,
        "input_schema": input_schema or {"type": "object", "properties": {}},
        "return_message": "",
        "llm_result_fields": list(llm_result_fields or []),
        "llm_result_schema": None,
        "metadata": dict(metadata or {}),
    }


def plugin(
    plugin_id: str,
    description: str = "",
    entries: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Return a plugin descriptor in the shape ``GET /plugins`` lists."""
    return {
        "id": plugin_id,
        "name": fields.pop("name", plugin_id),
        "description": description,
        "short_description": fields.pop("short_description", ""),
        "keywords": fields.pop("keywords", []),
        "passive": fields.pop("passive", False),
        "status": fields.pop("status", "running"),
        "entries": entries if entries is not None else [entry("run", "Default plugin entry")],
        **fields,
    }
