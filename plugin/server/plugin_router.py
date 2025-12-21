"""
插件间通信路由器

处理插件之间的通信请求，将请求路由到目标插件的 cmd_queue。
"""
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Empty
from typing import Dict, Any, Optional

from plugin.core.state import state
from plugin.api.exceptions import PluginNotFoundError, PluginExecutionError

logger = logging.getLogger("plugin.router")


class PluginRouter:
    """插件间通信路由器"""
    
    def __init__(self):
        self._router_task: Optional[asyncio.Task] = None
        self._shutdown_event: Optional[asyncio.Event] = None  # 延迟初始化，在 start() 中创建
        self._pending_requests: Dict[str, asyncio.Future] = {}
        # 创建共享的线程池执行器，用于在后台线程中执行阻塞的队列操作
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plugin-router")
    
    def _ensure_shutdown_event(self) -> asyncio.Event:
        """确保 shutdown_event 已创建（延迟初始化，避免在模块导入时创建）"""
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
        return self._shutdown_event
    
    async def start(self) -> None:
        """启动路由器任务"""
        if self._router_task is not None:
            logger.warning("Plugin router is already started")
            return
        
        # 确保 shutdown_event 已创建（延迟初始化）
        shutdown_event = self._ensure_shutdown_event()
        shutdown_event.clear()
        self._router_task = asyncio.create_task(self._router_loop())
        logger.info("Plugin router started")
    
    async def stop(self) -> None:
        """停止路由器任务"""
        if self._router_task is None:
            return
        
        # 确保 shutdown_event 已创建（延迟初始化）
        shutdown_event = self._ensure_shutdown_event()
        shutdown_event.set()
        try:
            await asyncio.wait_for(self._router_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Plugin router task did not stop in time")
            self._router_task.cancel()
        finally:
            self._router_task = None
            # 关闭线程池执行器
            self._executor.shutdown(wait=True)
            logger.info("Plugin router stopped")
    
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
            
            # multiprocessing.Queue.get() 是阻塞的，需要在线程中执行
            # 使用共享的执行器，避免每次调用都创建新的线程池
            try:
                request = await loop.run_in_executor(
                    self._executor,
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
        
        if request_type != "PLUGIN_TO_PLUGIN":
            logger.warning(f"Unknown request type: {request_type}")
            return
        
        from_plugin = request.get("from_plugin")
        to_plugin = request.get("to_plugin")
        event_type = request.get("event_type")
        event_id = request.get("event_id")
        args = request.get("args", {})
        request_id = request.get("request_id")
        timeout = request.get("timeout", 10.0)  # 增加默认超时时间以应对命令循环可能的延迟
        logger.info(
            f"[PluginRouter] Routing request: {from_plugin} -> {to_plugin}, "
            f"event={event_type}.{event_id}, req_id={request_id}"
        )
        
        # 获取目标插件的宿主
        host = state.plugin_hosts.get(to_plugin)
        if not host:
            error_msg = f"Plugin '{to_plugin}' not found"
            logger.error(f"[PluginRouter] {error_msg}")
            self._send_response(from_plugin, request_id, None, error_msg, timeout=timeout)
            return
        
        # 检查进程健康状态
        try:
            health = host.health_check()
            if not health.alive:
                error_msg = f"Plugin '{to_plugin}' process is not alive"
                logger.error(f"[PluginRouter] {error_msg}")
                self._send_response(from_plugin, request_id, None, error_msg, timeout=timeout)
                return
        except Exception as e:
            error_msg = f"Health check failed for plugin '{to_plugin}': {e}"
            logger.error(f"[PluginRouter] {error_msg}")
            self._send_response(from_plugin, request_id, None, error_msg, timeout=timeout)
            return
        
        # 触发目标插件的自定义事件
        try:
            result = await host.trigger_custom_event(
                event_type=event_type,
                event_id=event_id,
                args=args,
                timeout=timeout
            )
            self._send_response(from_plugin, request_id, result, None, timeout=timeout)
        except Exception as e:
            error_msg = str(e)
            logger.exception(f"[PluginRouter] Error triggering custom event: {e}")
            self._send_response(from_plugin, request_id, None, error_msg, timeout=timeout)
    
    def _send_response(self, to_plugin: str, request_id: str, result: Any, error: Optional[str], timeout: float = 10.0) -> None:
        """
        发送响应到源插件（使用响应映射，避免共享队列的竞态条件）
        
        Args:
            to_plugin: 目标插件ID
            request_id: 请求ID
            result: 响应结果
            error: 错误信息（如果有）
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

