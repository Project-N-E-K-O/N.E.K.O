"""插件间通信路由器

从 ``state.plugin_comm_queue`` (asyncio.Queue) 读取请求，
执行对应 handler，再通过目标插件的 downlink sender 回传响应。
"""
import asyncio
import time
from typing import Dict, Any, Optional

from loguru import logger

from plugin.core.state import state
from plugin.server.requests.typing import ErrorPayload
from plugin.server.requests.registry import build_request_handlers
from plugin.settings import (
    PLUGIN_ZMQ_IPC_ENABLED,
    PLUGIN_ZMQ_IPC_ENDPOINT,
)


class PluginRouter:
    """插件间通信路由器"""

    def __init__(self):
        self._router_task: Optional[asyncio.Task] = None
        self._zmq_task: Optional[asyncio.Task] = None
        self._zmq_server: Any = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._handlers = build_request_handlers()

    def _ensure_shutdown_event(self) -> asyncio.Event:
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
        return self._shutdown_event

    async def start(self) -> None:
        if self._router_task is not None:
            logger.warning("Plugin router is already started")
            return

        shutdown_event = self._ensure_shutdown_event()
        shutdown_event.clear()
        self._router_task = asyncio.create_task(self._router_loop())

        if PLUGIN_ZMQ_IPC_ENABLED:
            try:
                from plugin.utils.zeromq_ipc import ZmqIpcServer
                self._zmq_server = ZmqIpcServer(
                    endpoint=PLUGIN_ZMQ_IPC_ENDPOINT,
                    request_handler=self._handle_zmq_request,
                )
                self._zmq_task = asyncio.create_task(self._zmq_server.serve_forever(shutdown_event))
                logger.info("ZeroMQ IPC server started at {}", PLUGIN_ZMQ_IPC_ENDPOINT)
            except Exception as e:
                self._zmq_server = None
                self._zmq_task = None
                logger.opt(exception=True).exception("Failed to start ZeroMQ IPC server: {}", e)
        logger.info("Plugin router started")

    async def stop(self) -> None:
        if self._router_task is None:
            return

        shutdown_event = self._ensure_shutdown_event()
        shutdown_event.set()
        try:
            self._router_task.cancel()
        except Exception:
            pass
        self._router_task = None

        if self._zmq_task is not None:
            try:
                self._zmq_task.cancel()
            except Exception:
                pass
            self._zmq_task = None
            if self._zmq_server is not None:
                try:
                    self._zmq_server.close()
                except Exception:
                    pass
                self._zmq_server = None

        logger.info("Plugin router stopped")

    # ── ZMQ IPC handler (unchanged) ──────────────────────────────

    async def _handle_zmq_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request_type = request.get("type")
        handler = self._handlers.get(str(request_type))
        from_plugin = request.get("from_plugin")
        request_id = request.get("request_id")

        if not isinstance(from_plugin, str) or not from_plugin:
            return {"type": "PLUGIN_TO_PLUGIN_RESPONSE", "to_plugin": "", "request_id": str(request_id or ""), "result": None, "error": "missing from_plugin"}
        if not isinstance(request_id, str) or not request_id:
            return {"type": "PLUGIN_TO_PLUGIN_RESPONSE", "to_plugin": from_plugin, "request_id": str(request_id or ""), "result": None, "error": "missing request_id"}
        if handler is None:
            return {"type": "PLUGIN_TO_PLUGIN_RESPONSE", "to_plugin": from_plugin, "request_id": request_id, "result": None, "error": f"unknown request type: {request_type}"}

        out: Dict[str, Any] = {}

        def _send_response(
            to_plugin: str,
            request_id: str,
            result: Any,
            error: Optional[ErrorPayload],
            timeout: float = 10.0,
        ) -> None:
            out.update({
                "type": "PLUGIN_TO_PLUGIN_RESPONSE",
                "to_plugin": to_plugin,
                "request_id": request_id,
                "result": result,
                "error": error,
            })

        try:
            await handler(request, _send_response)
        except Exception as e:
            logger.exception("Error handling ZMQ request: %s", e)
            return {"type": "PLUGIN_TO_PLUGIN_RESPONSE", "to_plugin": from_plugin, "request_id": request_id, "result": None, "error": str(e)}

        if not out:
            logger.warning("[ZMQ IPC] no response for type=%s from=%s req_id=%s", request_type, from_plugin, request_id)
            return {"type": "PLUGIN_TO_PLUGIN_RESPONSE", "to_plugin": from_plugin, "request_id": request_id, "result": None, "error": "no response"}
        return out

    # ── main router loop ─────────────────────────────────────────

    async def _router_loop(self) -> None:
        logger.info("Plugin router loop started")
        last_cleanup_time = 0.0
        cleanup_interval = 30.0
        shutdown_event = self._ensure_shutdown_event()
        comm_queue = state.plugin_comm_queue

        while not shutdown_event.is_set():
            try:
                current_time = time.time()
                if current_time - last_cleanup_time >= cleanup_interval:
                    cleaned = state.cleanup_expired_responses()
                    if cleaned > 0:
                        logger.debug("[PluginRouter] Cleaned {} expired responses", cleaned)
                    last_cleanup_time = current_time

                try:
                    request = await asyncio.wait_for(comm_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if request is None:
                    continue

                await self._handle_request(request)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in plugin router loop: {}", e)
                await asyncio.sleep(0.1)

    async def _handle_request(self, request: Dict[str, Any]) -> None:
        request_type = request.get("type")
        handler = self._handlers.get(str(request_type))
        if handler is None:
            logger.warning("Unknown request type: {}", request_type)
            return
        await handler(request, self._send_response)

    def _send_response(
        self,
        to_plugin: str,
        request_id: str,
        result: Any,
        error: Optional[ErrorPayload],
        timeout: float = 10.0,
    ) -> None:
        """Route response back to the originating plugin via its downlink."""
        response = {
            "type": "PLUGIN_TO_PLUGIN_RESPONSE",
            "to_plugin": to_plugin,
            "request_id": request_id,
            "result": result,
            "error": error,
        }

        # Try the ZMQ downlink first
        sender = state.get_downlink_sender(to_plugin)
        if sender is not None:
            try:
                asyncio.ensure_future(sender(response))
                return
            except Exception:
                pass

        # Fallback: store in the shared response map (for state.get_plugin_response)
        try:
            state.set_plugin_response(request_id, response, timeout=timeout)
            logger.debug(
                "[PluginRouter] Set response for plugin {}, req_id={}, error={}",
                to_plugin, request_id, "yes" if error else "no",
            )
        except Exception as e:
            logger.exception("Failed to set response for plugin {}: {}", to_plugin, e)


# 全局路由器实例
plugin_router = PluginRouter()
