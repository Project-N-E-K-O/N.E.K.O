"""
插件间通信路由器

处理插件之间的通信请求，将请求路由到目标插件的 cmd_queue。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Full
from typing import Protocol, cast

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.requests.registry import build_request_handlers
from plugin.settings import PLUGIN_ZMQ_IPC_ENABLED, PLUGIN_ZMQ_IPC_ENDPOINT

logger = get_logger("server.plugin_router")

JsonObject = dict[str, object]

try:
    from zmq.error import ZMQError
except (ImportError, ModuleNotFoundError):
    class ZMQError(RuntimeError):
        """Fallback error type when pyzmq is unavailable."""


class _RequestHandler(Protocol):
    async def __call__(self, request: JsonObject, send_response: "_SendResponse") -> None: ...


class _SendResponse(Protocol):
    def __call__(
        self,
        to_plugin: str,
        request_id: str,
        result: object,
        error: object | None,
        timeout: float = 10.0,
    ) -> None: ...


class _ResponseQueue(Protocol):
    def put(self, item: JsonObject, block: bool = True, timeout: float | None = None) -> None: ...


class _ZmqIpcServerContract(Protocol):
    async def serve_forever(self, shutdown_event: asyncio.Event) -> None: ...

    def close(self) -> None: ...


def _normalize_mapping(raw: object) -> JsonObject | None:
    if not isinstance(raw, Mapping):
        return None

    normalized: JsonObject = {}
    for key, value in raw.items():
        if isinstance(key, str):
            normalized[key] = value
    return normalized


class PluginRouter:
    """插件间通信路由器"""

    def __init__(self) -> None:
        self._router_task: asyncio.Task[None] | None = None
        self._zmq_task: asyncio.Task[None] | None = None
        self._zmq_server: _ZmqIpcServerContract | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plugin-router")
        self._handlers: dict[str, _RequestHandler] = cast(dict[str, _RequestHandler], build_request_handlers())

    def _ensure_shutdown_event(self) -> asyncio.Event:
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
        return self._shutdown_event

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plugin-router")
        return self._executor

    async def _cancel_task(self, task: asyncio.Task[None], *, task_name: str) -> None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logger.debug("plugin router task cancelled: {}", task_name)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            logger.warning(
                "plugin router task stop failed: task={}, err_type={}, err={}",
                task_name,
                type(exc).__name__,
                str(exc),
            )

    async def start(self) -> None:
        """启动路由器任务"""
        if self._router_task is not None:
            logger.warning("Plugin router is already started")
            return

        self._ensure_executor()

        shutdown_event = self._ensure_shutdown_event()
        shutdown_event.clear()
        self._router_task = asyncio.create_task(self._router_loop(), name="plugin-router-loop")

        if PLUGIN_ZMQ_IPC_ENABLED:
            try:
                from plugin.utils.zeromq_ipc import ZmqIpcServer

                self._zmq_server = cast(
                    _ZmqIpcServerContract,
                    ZmqIpcServer(
                        endpoint=PLUGIN_ZMQ_IPC_ENDPOINT,
                        request_handler=self._handle_zmq_request,
                    ),
                )
                self._zmq_task = asyncio.create_task(
                    self._zmq_server.serve_forever(shutdown_event),
                    name="plugin-router-zmq-server",
                )
                logger.info("ZeroMQ IPC server started at {}", PLUGIN_ZMQ_IPC_ENDPOINT)
            except (ImportError, ModuleNotFoundError, RuntimeError, ValueError, TypeError, OSError, ZMQError) as exc:
                self._zmq_server = None
                self._zmq_task = None
                logger.error(
                    "Failed to start ZeroMQ IPC server: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )

        logger.info("Plugin router started")

    async def stop(self) -> None:
        """停止路由器任务"""
        if self._router_task is None:
            return

        shutdown_event = self._ensure_shutdown_event()
        shutdown_event.set()

        router_task = self._router_task
        self._router_task = None
        await self._cancel_task(router_task, task_name="router")

        if self._zmq_task is not None:
            zmq_task = self._zmq_task
            self._zmq_task = None
            await self._cancel_task(zmq_task, task_name="zmq")

        if self._zmq_server is not None:
            try:
                self._zmq_server.close()
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                logger.warning(
                    "failed to close ZeroMQ IPC server: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )
            self._zmq_server = None

        if self._executor is not None:
            executor = self._executor
            self._executor = None
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)
            except (RuntimeError, ValueError, OSError) as exc:
                logger.warning(
                    "failed to shutdown plugin router executor: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )

        logger.info("Plugin router stopped")

    async def _handle_zmq_request(self, request: JsonObject) -> JsonObject:
        """Handle a request coming from ZeroMQ IPC."""
        request_type = request.get("type")
        handler = self._handlers.get(str(request_type))
        from_plugin = request.get("from_plugin")
        request_id = request.get("request_id")

        if not isinstance(from_plugin, str) or not from_plugin:
            return {
                "type": "PLUGIN_TO_PLUGIN_RESPONSE",
                "to_plugin": "",
                "request_id": str(request_id or ""),
                "result": None,
                "error": "missing from_plugin",
            }

        if not isinstance(request_id, str) or not request_id:
            return {
                "type": "PLUGIN_TO_PLUGIN_RESPONSE",
                "to_plugin": from_plugin,
                "request_id": str(request_id or ""),
                "result": None,
                "error": "missing request_id",
            }

        if handler is None:
            return {
                "type": "PLUGIN_TO_PLUGIN_RESPONSE",
                "to_plugin": from_plugin,
                "request_id": request_id,
                "result": None,
                "error": f"unknown request type: {request_type}",
            }

        out: JsonObject = {}

        def _send_response(
            to_plugin: str,
            request_id: str,
            result: object,
            error: object | None,
            timeout: float = 10.0,
        ) -> None:
            _ = timeout
            out.update(
                {
                    "type": "PLUGIN_TO_PLUGIN_RESPONSE",
                    "to_plugin": to_plugin,
                    "request_id": request_id,
                    "result": result,
                    "error": error,
                }
            )

        try:
            await handler(request, _send_response)
        except (RuntimeError, ValueError, TypeError, KeyError, OSError, TimeoutError, AttributeError) as exc:
            logger.error(
                "Error handling ZMQ request: req_type={}, from_plugin={}, req_id={}, err_type={}, err={}",
                str(request_type),
                from_plugin,
                request_id,
                type(exc).__name__,
                str(exc),
            )
            return {
                "type": "PLUGIN_TO_PLUGIN_RESPONSE",
                "to_plugin": from_plugin,
                "request_id": request_id,
                "result": None,
                "error": str(exc),
            }

        if not out:
            logger.warning(
                "ZeroMQ IPC request has no response: req_type={}, from_plugin={}, req_id={}",
                str(request_type),
                from_plugin,
                request_id,
            )
            return {
                "type": "PLUGIN_TO_PLUGIN_RESPONSE",
                "to_plugin": from_plugin,
                "request_id": request_id,
                "result": None,
                "error": "no response",
            }

        return out

    async def _router_loop(self) -> None:
        """路由器主循环"""
        logger.info("Plugin router loop started")

        last_cleanup_time = 0.0
        cleanup_interval = 30.0
        shutdown_event = self._ensure_shutdown_event()

        while not shutdown_event.is_set():
            try:
                current_time = time.time()
                if current_time - last_cleanup_time >= cleanup_interval:
                    cleaned_count = int(state.cleanup_expired_responses())
                    if cleaned_count > 0:
                        logger.debug("PluginRouter cleaned expired responses: {}", cleaned_count)
                    last_cleanup_time = current_time

                request = await asyncio.wait_for(self._get_request_from_queue(), timeout=1.0)
                if request is None:
                    continue

                await self._handle_request(request)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except (RuntimeError, ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
                logger.warning(
                    "Error in plugin router loop: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )
                await asyncio.sleep(0.1)

    async def _get_request_from_queue(self) -> JsonObject | None:
        """从通信队列获取请求（非阻塞）"""
        queue_obj = state.plugin_comm_queue
        if queue_obj is None:
            return None

        loop = asyncio.get_running_loop()
        executor = self._ensure_executor()

        def _read_request() -> object:
            return queue_obj.get(timeout=0.1)

        try:
            request_obj = await loop.run_in_executor(executor, _read_request)
        except Empty:
            return None
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as exc:
            logger.debug(
                "Error getting request from queue: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            return None

        return _normalize_mapping(request_obj)

    async def _handle_request(self, request: JsonObject) -> None:
        """处理插件间通信请求"""
        request_type = request.get("type")
        handler = self._handlers.get(str(request_type))
        if handler is None:
            logger.warning("Unknown request type: {}", str(request_type))
            return

        await handler(request, self._send_response)

    def _send_response(
        self,
        to_plugin: str,
        request_id: str,
        result: object,
        error: object | None,
        timeout: float = 10.0,
    ) -> None:
        """发送响应到源插件。"""
        response: JsonObject = {
            "type": "PLUGIN_TO_PLUGIN_RESPONSE",
            "to_plugin": to_plugin,
            "request_id": request_id,
            "result": result,
            "error": error,
        }

        queue_obj: _ResponseQueue | None = None
        try:
            queue_obj = cast(_ResponseQueue | None, state.get_plugin_response_queue(to_plugin))
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError) as exc:
            logger.debug(
                "failed to get plugin response queue: plugin_id={}, err_type={}, err={}",
                to_plugin,
                type(exc).__name__,
                str(exc),
            )

        if queue_obj is not None:
            try:
                queue_obj.put(response, block=False)
                return
            except Full:
                logger.debug("plugin response queue is full, retry with timeout: plugin_id={}", to_plugin)
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError):
                logger.debug("non-blocking response queue put failed: plugin_id={}", to_plugin)

            try:
                queue_obj.put(response, block=True, timeout=0.05)
                return
            except Full:
                logger.debug("blocking response queue put timed out: plugin_id={}", to_plugin)
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError):
                logger.debug("blocking response queue put failed: plugin_id={}", to_plugin)

        try:
            state.set_plugin_response(request_id, response, timeout=timeout)
            logger.debug(
                "PluginRouter set response for plugin={}, req_id={}, has_error={}, timeout={}s",
                to_plugin,
                request_id,
                "yes" if error is not None else "no",
                timeout,
            )
        except (RuntimeError, ValueError, TypeError, OSError, AttributeError, KeyError) as exc:
            logger.error(
                "Failed to set response for plugin {}: err_type={}, err={}",
                to_plugin,
                type(exc).__name__,
                str(exc),
            )


plugin_router = PluginRouter()
