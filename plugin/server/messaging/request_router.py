"""Bind host operations and authenticated plugin connections to control RPC."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Mapping

from plugin.core.rpc import (
    RpcClosedError,
    RpcDispatcher,
    encode_rpc_reply,
    log_rpc_failure,
    rpc_reply,
)
from plugin.core.state import state
from plugin.core.control_ingress import ControlIngress
from plugin.core.zmq_transport import pack_rpc_payload, unpack_rpc_payload
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.registry import build_request_handlers
from plugin.settings import PluginRpcQuotas, get_plugin_rpc_quotas

logger = get_logger("server.messaging.request_router")

# Reply delivery has its own bounded task budget.
_MAX_PENDING_BUSY_REPLIES = 256
_RECEIVE_BATCH_SIZE = 32


def _domain_error(exc: Exception) -> object | None:
    if isinstance(exc, ServerDomainError):
        return {"code": exc.code, "message": exc.message, "details": exc.details}
    return None


def _plugin_call_target(request: Mapping[str, object]) -> str | None:
    if request.get("type") != "PLUGIN_TO_PLUGIN":
        return None
    target = request.get("to_plugin")
    if not isinstance(target, str) or not target.strip():
        return None
    return target.strip()


def _new_dispatcher(handlers, quotas: PluginRpcQuotas) -> RpcDispatcher:
    return RpcDispatcher(
        handlers,
        max_in_flight_per_owner=quotas.max_in_flight_per_plugin,
        target_of=_plugin_call_target,
        max_in_flight_per_target=quotas.max_in_flight_per_target_plugin,
        error_mapper=_domain_error,
        logger=logger,
    )


class PluginRouter:
    """Own routing lifetime; correlation and execution belong to the RPC runtime."""

    def __init__(self) -> None:
        self._handlers = build_request_handlers()
        self._dispatcher = _new_dispatcher(self._handlers, get_plugin_rpc_quotas())
        self._router_task: asyncio.Task | None = None
        self._busy_replies: set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()
        self._start_lock = threading.Lock()
        self._stop_task = None
        self._ingress = None
        self._queue = None

    async def start(self) -> None:
        while True:
            with self._start_lock:
                if self._router_task is None:
                    self._start_local()
                    return
                if not self._router_task.done():
                    if self._stop_task is not None and not self._stop_task.done():
                        raise RpcClosedError("Plugin router is stopping")
                    return
                lease = self._ingress
            # Another caller may recover before this cleanup runs. Stop only
            # the failed generation, then inspect the current one again.
            await self._stop_ingress(lease)

    def _start_local(self) -> None:
        if self._stop_task is not None and not self._stop_task.done():
            raise RpcClosedError("Plugin router is stopping")
        self._shutdown_event = asyncio.Event()
        quotas = get_plugin_rpc_quotas()
        self._dispatcher = _new_dispatcher(self._handlers, quotas)
        self._stop_task = None
        self._queue = state.plugin_comm_queue
        self._ingress = ControlIngress(
            self._queue,
            self._dispatch_control,
            max_queued_per_connection=quotas.max_queued_per_plugin,
            logger=logger,
        )
        state.plugin_control_ingress = self._ingress
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
        lease = self._ingress
        await self._stop_ingress(lease)

    async def _stop_ingress(self, lease: ControlIngress | None) -> None:
        if lease is None or lease is not self._ingress:
            return
        # The ingress owns its loop for its entire lifetime. Reading another
        # router field here could pair an old loop with a restarted ingress.
        owner = lease.loop
        if owner is asyncio.get_running_loop():
            await self._stop_on_owner(lease)
        else:
            if owner.is_closed():
                with self._start_lock:
                    if lease is not self._ingress:
                        return
                    if self._router_task is not None and self._router_task.done():
                        # asyncio.run may have cancelled the old generation
                        # and closed its loop without calling our stop(). With
                        # no owner left to schedule cleanup on, retire its
                        # references under the generation lock before restart.
                        lease.close()
                        if state.plugin_control_ingress is lease:
                            state.plugin_control_ingress = None
                            state._plugin_comm_queue = None
                        self._router_task = None
                        self._ingress = None
                        self._queue = None
                        self._stop_task = None
                        self._busy_replies.clear()
                        return
            if owner.is_closed() or not owner.is_running():
                if lease is not self._ingress:
                    return
                raise RpcClosedError("Plugin router owner stopped before cleanup")
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self._stop_on_owner(lease), owner))

    async def _stop_on_owner(self, lease) -> None:
        if lease is not self._ingress:
            return
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop_local())
        await asyncio.shield(self._stop_task)

    async def _stop_local(self) -> None:
        if self._ingress is not None:
            self._ingress.close()
            if state.plugin_control_ingress is self._ingress:
                state.plugin_control_ingress = None
                state._plugin_comm_queue = None
        self._shutdown_event.set()
        if self._router_task is not None:
            self._router_task.cancel()
            await asyncio.gather(self._router_task, return_exceptions=True)
        busy_replies = tuple(self._busy_replies)
        for task in busy_replies:
            task.cancel()
        await asyncio.gather(*busy_replies, return_exceptions=True)
        await self._dispatcher.close()
        with self._start_lock:
            self._router_task = None
            self._ingress = None
            self._queue = None
        logger.info("Plugin router stopped")

    async def _router_loop(self) -> None:
        received = 0
        while not self._shutdown_event.is_set():
            request = await self._queue.get()
            received += 1
            if received >= _RECEIVE_BATCH_SIZE:
                received = 0
                # A nonempty Queue.get() never yields. Give reply tasks time
                # to deliver and release slots before draining another batch.
                await asyncio.sleep(0)
                # Reserve room for the next batch even when a healthy send
                # takes more than one loop turn. Each send is already bounded
                # by _send_busy's timeout; never grow the reply task budget.
                while len(self._busy_replies) > _MAX_PENDING_BUSY_REPLIES - _RECEIVE_BATCH_SIZE:
                    await asyncio.wait(
                        tuple(self._busy_replies), return_when=asyncio.FIRST_COMPLETED
                    )
            if not isinstance(request, Mapping):
                logger.warning("Discarding malformed plugin control request")
                continue
            if not self._ingress.take(request):
                continue
            if not self._dispatcher.submit(dict(request), self._send_response):
                self._reply_busy(request)

    def _dispatch_control(self, request: dict) -> None:
        self._dispatcher.submit(request, self._send_response)

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
        # The reply keeps its request's queue share, so one plugin's pending
        # replies stay within its own quota and cannot use up the shared budget.
        ingress = self._ingress
        if ingress is not None and ingress.retain(request):
            task.add_done_callback(lambda _task: ingress.release(request))

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
