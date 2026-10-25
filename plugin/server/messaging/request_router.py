"""Bind host operations and authenticated plugin connections to control RPC."""

from __future__ import annotations

import asyncio
import inspect
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

logger = get_logger("server.messaging.request_router")

# Matches the dispatcher's in-flight limit; beyond it callers fall back to their deadline.
_MAX_PENDING_BUSY_REPLIES = 256


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
        self._busy_replies: set[asyncio.Task] = set()
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
        if self._router_task is not None:
            self._router_task.cancel()
            await asyncio.gather(self._router_task, return_exceptions=True)
        busy_replies = tuple(self._busy_replies)
        for task in busy_replies:
            task.cancel()
        await asyncio.gather(*busy_replies, return_exceptions=True)
        await self._dispatcher.close()
        self._router_task = None
        logger.info("Plugin router stopped")

    async def _router_loop(self) -> None:
        while not self._shutdown_event.is_set():
            request = await state.plugin_comm_queue.get()
            if not isinstance(request, Mapping):
                logger.warning("Discarding malformed plugin control request")
                continue
            if not self._dispatcher.submit(dict(request), self._send_response):
                self._reply_busy(request)

    def _reply_busy(self, request: Mapping[str, object]) -> None:
        # Overload replies must not hold the shared receive loop: a blocked
        # downlink would otherwise delay every plugin's requests and cancels.
        request_id = request.get("request_id")
        if len(self._busy_replies) >= _MAX_PENDING_BUSY_REPLIES:
            logger.warning(
                "RPC overload reply dropped: request_id={}, pending={}",
                request_id,
                len(self._busy_replies),
            )
            return
        task = asyncio.create_task(self._send_busy(request))
        self._busy_replies.add(task)
        task.add_done_callback(self._busy_replies.discard)

    async def _send_busy(self, request: Mapping[str, object]) -> None:
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
