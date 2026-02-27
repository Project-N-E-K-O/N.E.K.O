"""
插件间通信路由器

处理插件之间的通信请求，将请求路由到目标插件的 cmd_queue。
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Empty
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
        self._shutdown_event: Optional[asyncio.Event] = None  # 延迟初始化，在 start() 中创建
        self._pending_requests: Dict[str, asyncio.Future] = {}
        # 创建共享的线程池执行器，用于在后台线程中执行阻塞的队列操作
        self._executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plugin-router")
        self._handlers = build_request_handlers()
    
    def _ensure_shutdown_event(self) -> asyncio.Event:
        """确保 shutdown_event 已创建（延迟初始化，避免在模块导入时创建）"""
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
        return self._shutdown_event

    def _ensure_executor(self) -> ThreadPoolExecutor:
        """确保 executor 可用（stop() 后会被 shutdown，需要在下次 start() 时重建）"""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plugin-router")
        return self._executor
    
    async def start(self) -> None:
        """启动路由器任务"""
        if self._router_task is not None:
            logger.warning("Plugin router is already started")
            return

        # 确保 executor 可用（允许 stop() 后再次 start()）
        self._ensure_executor()
        
        # 确保 shutdown_event 已创建（延迟初始化）
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
        """停止路由器任务"""
        if self._router_task is None:
            return
        
        # 确保 shutdown_event 已创建（延迟初始化）
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

        # 关闭线程池执行器
        if self._executor is not None:
            executor = self._executor
            try:
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    executor.shutdown(wait=False)
            except Exception:
                pass
            self._executor = None
        logger.info("Plugin router stopped")

    async def _handle_zmq_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a request coming from ZeroMQ IPC.

        Returns a response dict compatible with existing bus SDK expectations:
        {type,to_plugin,request_id,result,error}
        """
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
        except Exception as e:
            logger.exception("Error handling ZMQ request: %s", e)
            return {"type": "PLUGIN_TO_PLUGIN_RESPONSE", "to_plugin": from_plugin, "request_id": request_id, "result": None, "error": str(e)}

        if not out:
            # 理论上 handler 总是会通过 _send_response 填充 out；如果没有，则记录一条日志帮助排查
            try:
                logger.warning(
                    "[ZeroMQ IPC] no response generated for request_type=%s from=%s req_id=%s",
                    str(request_type),
                    str(from_plugin),
                    str(request_id),
                )
            except Exception:
                pass
            return {"type": "PLUGIN_TO_PLUGIN_RESPONSE", "to_plugin": from_plugin, "request_id": request_id, "result": None, "error": "no response"}
        return out
    
    async def _router_loop(self) -> None:
        """路由器主循环"""
        logger.info("Plugin router loop started")
        
        # 上次清理过期响应的时间
        last_cleanup_time = 0.0
        cleanup_interval = 30.0  # 每30秒清理一次过期响应
        
        # 确保 shutdown_event 已创建（延迟初始化）
        shutdown_event = self._ensure_shutdown_event()
        
        while not shutdown_event.is_set():
            try:
                # 定期清理过期的响应（防止响应映射无限增长）
                import time
                current_time = time.time()
                if current_time - last_cleanup_time >= cleanup_interval:
                    cleaned_count = state.cleanup_expired_responses()
                    if cleaned_count > 0:
                        logger.debug(f"[PluginRouter] Cleaned up {cleaned_count} expired responses")
                    last_cleanup_time = current_time
                
                # 从通信队列获取请求
                request = await asyncio.wait_for(
                    self._get_request_from_queue(),
                    timeout=1.0
                )
                
                if request is None:
                    continue
                
                # 处理请求
                await self._handle_request(request)
                
            except asyncio.TimeoutError:
                # 超时是正常的，继续循环
                continue
            except Exception as e:
                logger.exception(f"Error in plugin router loop: {e}")
                await asyncio.sleep(0.1)  # 避免快速循环
    
    async def _get_request_from_queue(self) -> Optional[Dict[str, Any]]:
        """从通信队列获取请求（非阻塞）"""
        try:
            # 使用 run_in_executor 在后台线程中执行阻塞操作
            loop = asyncio.get_running_loop()
            queue = state.plugin_comm_queue
            executor = self._ensure_executor()
            
            # multiprocessing.Queue.get() 是阻塞的，需要在线程中执行
            # 使用共享的执行器，避免每次调用都创建新的线程池
            try:
                request = await loop.run_in_executor(
                    executor,
                    lambda: queue.get(timeout=0.1)  # 短超时，避免阻塞太久
                )
                return request
            except Empty:
                return None
        except Exception as e:
            logger.debug(f"Error getting request from queue: {e}")
            return None
    
    async def _handle_request(self, request: Dict[str, Any]) -> None:
        """处理插件间通信请求"""
        request_type = request.get("type")

        handler = self._handlers.get(str(request_type))
        if handler is None:
            logger.warning(f"Unknown request type: {request_type}")
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
        """
        发送响应到源插件（使用响应映射，避免共享队列的竞态条件）
        
        Args:
            to_plugin: 目标插件ID
            request_id: 请求ID
            result: 响应结果
            error: 错误信息（字符串或结构化错误对象）
            timeout: 超时时间（秒），用于计算响应过期时间
        """
        response = {
            "type": "PLUGIN_TO_PLUGIN_RESPONSE",
            "to_plugin": to_plugin,
            "request_id": request_id,
            "result": result,
            "error": error,
        }
        
        try:
            q = None
            try:
                q = state.get_plugin_response_queue(to_plugin)
            except Exception:
                q = None
            if q is not None:
                try:
                    q.put(response, block=False)
                    return
                except Exception:
                    try:
                        q.put(response, block=True, timeout=0.05)
                        return
                    except Exception:
                        pass
            # 将响应存储在响应映射中，插件进程通过 request_id 直接查询
            # 这样可以避免共享队列的竞态条件问题
            # 同时设置过期时间，防止超时后的响应干扰后续请求
            state.set_plugin_response(request_id, response, timeout=timeout)
            logger.debug(
                f"[PluginRouter] Set response for plugin {to_plugin}, req_id={request_id}, "
                f"error={'yes' if error else 'no'}, timeout={timeout}s"
            )
        except Exception as e:
            logger.exception(f"Failed to set response for plugin {to_plugin}: {e}")


# 全局路由器实例
plugin_router = PluginRouter()

