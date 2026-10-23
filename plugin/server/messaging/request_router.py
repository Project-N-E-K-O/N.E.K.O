"""Bind host operations and authenticated plugin connections to control RPC."""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Mapping

from plugin.core.rpc import (
    RpcClosedError,
    RpcDispatcher,
    encode_rpc_reply,
    log_rpc_failure,
    rpc_reply,
)
from plugin.core.state import state
from plugin.core.zmq_transport import pack_rpc_payload, unpack_rpc_payload
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.registry import build_request_handlers
from plugin.settings import PLUGIN_ZMQ_IPC_ENABLED, PLUGIN_ZMQ_IPC_ENDPOINT

logger = get_logger("server.messaging.request_router")


def _domain_error(exc: Exception) -> object | None:
    if isinstance(exc, ServerDomainError):
        return {"code": exc.code, "message": exc.message, "details": exc.details}
    return None


class PluginRouter:
    """Own routing lifetime; correlation and execution belong to the RPC runtime."""

    def __init__(self) -> None:
        self._handlers = build_request_handlers()
        self._dispatcher = RpcDispatcher(
            self._handlers, error_mapper=_domain_error, logger=logger
        )
        self._router_task: asyncio.Task | None = None
        self._zmq_task: asyncio.Task | None = None
        self._zmq_server = None
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        if self._router_task is not None and not self._router_task.done():
            return
        if self._router_task is not None:
            self._observe(self._router_task)
        self._shutdown_event = asyncio.Event()
        self._dispatcher = RpcDispatcher(
            self._handlers, error_mapper=_domain_error, logger=logger
        )
        self._router_task = asyncio.create_task(
            self._router_loop(), name="plugin-router-loop"
        )
        self._router_task.add_done_callback(self._observe)
        enabled = os.getenv(
            "NEKO_PLUGIN_ZMQ_IPC_ENABLED", str(PLUGIN_ZMQ_IPC_ENABLED)
        ).lower() in ("true", "1", "yes", "on")
        if enabled:
            from plugin.utils.zeromq_ipc import ZmqIpcServer

            try:
                self._zmq_server = ZmqIpcServer(
                    os.getenv("NEKO_PLUGIN_ZMQ_IPC_ENDPOINT", PLUGIN_ZMQ_IPC_ENDPOINT),
                    self._handle_zmq_request,
                )
                self._zmq_task = asyncio.create_task(
                    self._zmq_server.serve_forever(self._shutdown_event),
                    name="plugin-router-zmq",
                )
                self._zmq_task.add_done_callback(self._observe)
            except Exception:
                logger.exception("Failed to start plugin IPC compatibility listener")
        logger.info("Plugin router started")

    @staticmethod
    def _observe(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error(
                "Plugin router task exited: task={}",
                task.get_name(),
                exc_info=task.exception(),
            )

    async def stop(self) -> None:
        self._shutdown_event.set()
        tasks = [
            task for task in (self._router_task, self._zmq_task) if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._dispatcher.close()
        if self._zmq_server is not None:
            await self._zmq_server.aclose()
        self._router_task = self._zmq_task = self._zmq_server = None
        logger.info("Plugin router stopped")

    async def _handle_zmq_request(
        self, request: dict[str, object]
    ) -> dict[str, object]:
        return await self._dispatcher.dispatch(request)

    async def _router_loop(self) -> None:
        while not self._shutdown_event.is_set():
            request = await state.plugin_comm_queue.get()
            if not isinstance(request, Mapping):
                logger.warning("Discarding malformed plugin control request")
                continue
            if not self._dispatcher.submit(dict(request), self._send_response):
                try:
                    await asyncio.wait_for(
                        self._send_response(
                            rpc_reply(
                                request,
                                error={
                                    "code": "BUSY",
                                    "message": "Plugin RPC dispatcher is busy",
                                },
                            )
                        ),
                        timeout=0.25,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log_rpc_failure(
                        logger,
                        f"RPC overload reply failed: request_id={request.get('request_id')}",
                        exc,
                    )

    async def _send_response(self, response: dict[str, object]) -> None:
        plugin_id = str(response["to_plugin"])
        sender = state.get_downlink_sender(plugin_id)
        if not callable(sender):
            raise RpcClosedError(f"Plugin {plugin_id} downlink is unavailable")
        _, encoded = encode_rpc_reply(response, pack_rpc_payload)
        response = unpack_rpc_payload(encoded)
        result = sender(response)
        if inspect.isawaitable(result):
            await result


plugin_router = PluginRouter()
