"""
插件进程间通信资源管理器

负责管理插件进程间的通信资源,包括队列、Future、后台任务等。
"""
from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from plugin.utils.time_utils import now_iso
from queue import Empty, Queue
from typing import Any, ClassVar, Dict, Optional

from loguru import logger

from plugin.settings import (
    COMMUNICATION_THREAD_POOL_MAX_WORKERS,
    PLUGIN_TRIGGER_TIMEOUT,
    PLUGIN_SHUTDOWN_TIMEOUT,
    QUEUE_GET_TIMEOUT,
    MESSAGE_CONSUMER_SLEEP_INTERVAL,
    RESULT_CONSUMER_SLEEP_INTERVAL,
    PLUGIN_LOG_MESSAGE_FORWARD,
    PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS,
)
from plugin._types.exceptions import PluginExecutionError
from plugin.utils.logging import format_log_text as _format_log_text


_SHUTDOWN_SENTINEL: Dict[str, Any] = {"_type": "__shutdown__"}


@dataclass
class PluginCommunicationResourceManager:
    """
    插件进程间通信资源管理器
    
    负责管理：
    - 命令队列、结果队列、状态队列、消息队列
    - 待处理请求的 Future 管理
    - 结果消费后台任务
    - 消息消费后台任务
    - 通信超时和清理
    """
    plugin_id: str
    cmd_queue: Queue
    res_queue: Queue
    status_queue: Queue
    message_queue: Queue
    logger: Any = field(default_factory=lambda: logger.bind(component="communication"))
    
    # 异步相关资源
    _pending_futures: Dict[str, asyncio.Future] = field(default_factory=dict)
    _result_consumer_task: Optional[asyncio.Task] = None
    _message_consumer_task: Optional[asyncio.Task] = None
    _shutdown_event: Optional[asyncio.Event] = None
    _executor: Optional[ThreadPoolExecutor] = None
    _message_target_queue: Optional[asyncio.Queue] = None  # 主进程的消息队列
    _background_tasks: set[asyncio.Task] = field(default_factory=set)
    _last_forward_log_key: Optional[tuple] = field(default=None, init=False, repr=False)
    _last_forward_log_time: float = field(default=0.0, init=False, repr=False)
    _last_forward_log_repeat_count: int = field(default=0, init=False, repr=False)
    
    def __post_init__(self):
        """初始化异步资源"""
        # 延迟到实际使用时再创建，避免在错误的事件循环中创建
        # 为每个插件创建独立的线程池，避免阻塞
        self._executor = ThreadPoolExecutor(
            max_workers=COMMUNICATION_THREAD_POOL_MAX_WORKERS,
            thread_name_prefix=f"plugin-comm-{self.plugin_id}"
        )
    
    def _ensure_shutdown_event(self) -> None:
        """确保 shutdown_event 已创建（延迟初始化）"""
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
    
    async def start(self, message_target_queue: Optional[asyncio.Queue] = None) -> None:
        """
        启动结果消费和消息消费后台任务
        
        Args:
            message_target_queue: 主进程的消息队列，用于接收插件推送的消息
        """
        self._message_target_queue = message_target_queue
        if self._result_consumer_task is None or self._result_consumer_task.done():
            self._result_consumer_task = asyncio.create_task(self._consume_results())
            self.logger.debug(f"Started result consumer for plugin {self.plugin_id}")
        if self._message_consumer_task is None or self._message_consumer_task.done():
            self._message_consumer_task = asyncio.create_task(self._consume_messages())
            self.logger.debug(f"Started message consumer for plugin {self.plugin_id}")
    
    async def shutdown(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        """
        关闭通信资源
        
        Args:
            timeout: 等待后台任务退出的超时时间
        """
        self.logger.debug(f"Shutting down communication resources for plugin {self.plugin_id}")
        
        # 停止结果消费和消息消费任务
        self._ensure_shutdown_event()
        shutdown_event = self._shutdown_event
        if shutdown_event is not None:
            shutdown_event.set()

        # 主动唤醒阻塞在 multiprocessing.Queue.get 的后台线程，避免 shutdown 卡在 queue.get 超时上。
        # 注意：put 也可能阻塞，因此放到 executor 里并带超时。
        try:
            await asyncio.to_thread(self.res_queue.put, _SHUTDOWN_SENTINEL, True, QUEUE_GET_TIMEOUT)
        except Exception as e:
            # shutdown 需要尽力而为：即使唤醒失败也继续走超时等待/取消逻辑
            self.logger.debug(
                "Failed to awake res_queue during shutdown for plugin {}: {}",
                self.plugin_id, e
            )
        try:
            await asyncio.to_thread(self.message_queue.put, _SHUTDOWN_SENTINEL, True, QUEUE_GET_TIMEOUT)
        except Exception as e:
            self.logger.debug(
                "Failed to awake message_queue during shutdown for plugin {}: {}",
                self.plugin_id, e
            )

        # 给消费者一个很短的“自然退出”窗口，避免 shutdown 被拖慢。
        # 之后直接 cancel，以确保在 timeout 内尽快结束。
        graceful_wait = min(0.5, float(timeout)) if timeout is not None else 0.5
        
        if self._result_consumer_task and not self._result_consumer_task.done():
            try:
                await asyncio.wait_for(self._result_consumer_task, timeout=graceful_wait)
            except asyncio.TimeoutError:
                self.logger.warning(
                    f"Result consumer for plugin {self.plugin_id} didn't stop in time, cancelling"
                )
                self._result_consumer_task.cancel()
                try:
                    await self._result_consumer_task
                except asyncio.CancelledError:
                    pass
        
        if self._message_consumer_task and not self._message_consumer_task.done():
            try:
                await asyncio.wait_for(self._message_consumer_task, timeout=graceful_wait)
            except asyncio.TimeoutError:
                self.logger.warning(
                    f"Message consumer for plugin {self.plugin_id} didn't stop in time, cancelling"
                )
                self._message_consumer_task.cancel()
                try:
                    await self._message_consumer_task
                except asyncio.CancelledError:
                    pass
        
        # 清理所有待处理的 Future
        self._cleanup_pending_futures()
        
        # 回收后台清理任务，避免 loop 关闭时遗留 pending 任务
        if self._background_tasks:
            for task in list(self._background_tasks):
                task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        
        # 关闭线程池
        if self._executor:
            # 必须等待线程退出, 否则非 daemon 线程会阻止主进程退出.
            # 这里投递到 executor 的 queue.get/put 都带超时(QUEUE_GET_TIMEOUT), 因此可在可控时间内退出.
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        
        self.logger.debug(f"Communication resources for plugin {self.plugin_id} shutdown complete")
    
    def get_pending_requests_count(self) -> int:
        """
        获取待处理请求数量(公共方法)
        
        Returns:
            待处理的请求数量
        """
        return len(self._pending_futures)
    
    def _cleanup_pending_futures(self) -> None:
        """清理所有待处理的 Future"""
        count = len(self._pending_futures)
        for _req_id, future in self._pending_futures.items():
            if not future.done():
                future.cancel()
        self._pending_futures.clear()
        if count > 0:
            self.logger.debug(f"Cleaned up {count} pending futures for plugin {self.plugin_id}")

    async def _send_command_and_wait(
        self,
        req_id: str,
        msg: dict,
        timeout: float,
        error_context: str
    ) -> Any:
        """
        通用的命令发送和等待逻辑
        """
        future = asyncio.Future()
        self._pending_futures[req_id] = future

        # multiprocessing.Queue.put 可能在子进程异常/管道阻塞时卡住。
        # 这里必须避免在事件循环线程中执行阻塞 put。
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                lambda: self.cmd_queue.put(msg, timeout=QUEUE_GET_TIMEOUT),
            )
        except Exception as e:
            self._pending_futures.pop(req_id, None)
            raise RuntimeError(
                f"Failed to send command to plugin {self.plugin_id} ({error_context}): {e}"
            ) from e
        
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if result["success"]:
                return result["data"]
            else:
                raise PluginExecutionError(self.plugin_id, error_context, result.get("error", "Unknown error"))
        except asyncio.TimeoutError:
            self.logger.error(
                f"Plugin {self.plugin_id} {error_context} timed out after {timeout}s, req_id={req_id}"
            )
            # 超时后不立即清理 Future, 给响应一些时间到达
            # 延迟清理, 避免响应到达时找不到 Future
            async def cleanup_after_delay():
                await asyncio.sleep(2.0)  # 给响应2秒时间到达
                if req_id in self._pending_futures:
                    future = self._pending_futures.get(req_id)
                    if future and future.done():
                        self.logger.debug(
                            f"Cleaning up completed Future for req_id={req_id} after timeout"
                        )
                    self._pending_futures.pop(req_id, None)
            
            cleanup_task = asyncio.create_task(cleanup_after_delay())
            self._background_tasks.add(cleanup_task)
            cleanup_task.add_done_callback(self._background_tasks.discard)
            
            raise TimeoutError("%s execution timed out after %ss" % (error_context, timeout)) from None

    async def trigger(self, entry_id: str, args: dict, timeout: float = PLUGIN_TRIGGER_TIMEOUT) -> Any:
        """
        发送触发命令并等待结果
        
        Args:
            entry_id: 入口 ID
            args: 参数
            timeout: 超时时间(秒)
        
        Returns:
            插件返回的结果
        
        Raises:
            TimeoutError: 如果超时
            Exception: 如果插件执行出错
        """
        req_id = str(uuid.uuid4())
        
        self.logger.debug(
            "[CommManager] Sending TRIGGER command: plugin_id={}, entry_id={}, req_id={}",
            self.plugin_id,
            entry_id,
            req_id,
        )
        # 详细参数信息使用 DEBUG
        self.logger.debug(
            "[CommManager] Args: type={}, keys={}, content={}",
            type(args),
            list(args.keys()) if isinstance(args, dict) else "N/A",
            args,
        )
        
        # 构建命令消息
        trigger_msg = {
            "type": "TRIGGER",
            "req_id": req_id,
            "entry_id": entry_id,
            "args": args
        }
        self.logger.debug(
            "[CommManager] TRIGGER message: {}",
            trigger_msg,
        )
        
        # 发送命令并等待结果
        return await self._send_command_and_wait(req_id, trigger_msg, timeout, f"entry {entry_id}")
    
    async def trigger_custom_event(
        self, 
        event_type: str, 
        event_id: str, 
        args: dict, 
        timeout: float = PLUGIN_TRIGGER_TIMEOUT
    ) -> Any:
        """
        触发自定义事件执行
        
        Args:
            event_type: 自定义事件类型(例如 "file_change", "user_action")
            event_id: 事件ID
            args: 参数字典
            timeout: 超时时间(秒)
        
        Returns:
            事件处理器返回的结果
        
        Raises:
            TimeoutError: 如果超时
            PluginExecutionError: 如果事件执行出错
        """
        req_id = str(uuid.uuid4())
        
        self.logger.info(
            "[CommManager] Sending TRIGGER_CUSTOM command: plugin_id={}, event_type={}, event_id={}, req_id={}",
            self.plugin_id,
            event_type,
            event_id,
            req_id,
        )
        
        # 构建命令消息
        trigger_msg = {
            "type": "TRIGGER_CUSTOM",
            "req_id": req_id,
            "event_type": event_type,
            "event_id": event_id,
            "args": args
        }
        
        # 发送命令并等待结果
        return await self._send_command_and_wait(req_id, trigger_msg, timeout, f"custom event {event_type}.{event_id}")

    async def send_freeze_command(self, timeout: float = PLUGIN_TRIGGER_TIMEOUT) -> Dict[str, Any]:
        """
        发送冻结命令到插件进程
        
        Args:
            timeout: 超时时间(秒)
        
        Returns:
            冻结结果字典，包含 success, data, error 键
        """
        req_id = str(uuid.uuid4())
        
        self.logger.info(
            "[CommManager] Sending FREEZE command: plugin_id={}, req_id={}",
            self.plugin_id,
            req_id,
        )
        
        freeze_msg = {
            "type": "FREEZE",
            "req_id": req_id,
        }

        try:
            result = await self._send_command_and_wait(req_id, freeze_msg, timeout, "freeze")
        except Exception as e:
            self.logger.warning(
                "[CommManager] FREEZE command failed: plugin_id={}, req_id={}, error={}",
                self.plugin_id,
                req_id,
                e,
            )
            return {"success": False, "data": None, "error": str(e)}
        
        # 规范化返回格式，确保包含 success, data, error 键
        if not isinstance(result, dict):
            return {"success": True, "data": result, "error": None}
        
        # 如果结果已经有 success 键，直接返回
        if "success" in result:
            return result
        
        # 否则包装为标准格式
        if "error" in result:
            return {"success": False, "data": result.get("data"), "error": result.get("error")}
        
        return {"success": True, "data": result, "error": None}

    async def send_cancel_run(self, run_id: str) -> None:
        """Send a CANCEL_RUN command to the plugin process (fire-and-forget).

        The child process will set the cancel_event for the given run_id,
        causing the running entry to be cancelled if it supports cancellation.
        """
        msg = {"type": "CANCEL_RUN", "run_id": str(run_id)}
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                lambda: self.cmd_queue.put(msg, timeout=QUEUE_GET_TIMEOUT),
            )
            self.logger.debug("Sent CANCEL_RUN for run_id={} to plugin {}", run_id, self.plugin_id)
        except Exception as e:
            self.logger.warning("Failed to send CANCEL_RUN to plugin {}: {}", self.plugin_id, e)

    async def push_bus_change(self, *, sub_id: str, bus: str, op: str, delta: Dict[str, Any] | None = None) -> None:
        """Push a bus change notification to plugin process.

        This is an internal channel for watcher delivery. It is fire-and-forget and does not wait for a response.
        """
        msg = {
            "type": "BUS_CHANGE",
            "sub_id": str(sub_id),
            "bus": str(bus),
            "op": str(op),
            "delta": dict(delta or {}),
        }

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                lambda: self.cmd_queue.put(msg, timeout=QUEUE_GET_TIMEOUT),
            )
        except Exception as e:
            raise RuntimeError(f"Failed to push BUS_CHANGE to plugin {self.plugin_id}: {e}") from e
    
    async def _handle_entry_update(self, msg: Dict[str, Any]) -> None:
        """处理动态 entry 更新消息
        
        Args:
            msg: ENTRY_UPDATE 消息，包含 action, entry_id, plugin_id, meta
        """
        try:
            from plugin.core.state import state
            from plugin.sdk.events import EventMeta, EventHandler
            
            action = msg.get("action")
            entry_id = msg.get("entry_id")
            plugin_id = self.plugin_id
            incoming_plugin_id = msg.get("plugin_id")
            if incoming_plugin_id and incoming_plugin_id != self.plugin_id:
                self.logger.warning(
                    "ENTRY_UPDATE plugin_id mismatch ignored: expected=%s, got=%s",
                    self.plugin_id,
                    incoming_plugin_id,
                )
                return
            meta_dict = msg.get("meta")
            
            if not entry_id:
                self.logger.warning(f"ENTRY_UPDATE message missing entry_id: {msg}")
                return
            
            self.logger.info(f"Processing ENTRY_UPDATE: action={action}, entry_id={entry_id}, plugin_id={plugin_id}")
            
            if action == "register":
                # 注册新的动态 entry
                if not meta_dict:
                    self.logger.warning(f"ENTRY_UPDATE register missing meta: {msg}")
                    return
                
                # 创建 EventMeta
                event_meta = EventMeta(
                    event_type="plugin_entry",
                    id=meta_dict.get("id", entry_id),
                    name=meta_dict.get("name", entry_id),
                    description=meta_dict.get("description", ""),
                    input_schema=meta_dict.get("input_schema"),
                    kind=meta_dict.get("kind", "action"),
                    auto_start=meta_dict.get("auto_start", False),
                    enabled=meta_dict.get("enabled", True),
                    dynamic=True,
                    metadata={"_dynamic": True, "_registered_via_ipc": True},
                )
                
                # 创建代理 handler（实际调用会路由到插件进程）
                # 使用默认参数捕获 entry_id，避免闭包变量被后续调用覆盖
                async def dynamic_handler(_entry_id=entry_id, **kwargs):
                    # 这个 handler 会被主进程调用，然后路由到插件进程
                    return await self.trigger(_entry_id, kwargs)
                
                event_handler = EventHandler(meta=event_meta, handler=dynamic_handler)
                
                # 注册到 state.event_handlers
                with state.acquire_event_handlers_write_lock():
                    state.event_handlers[f"{plugin_id}.{entry_id}"] = event_handler
                    state.event_handlers[f"{plugin_id}:plugin_entry:{entry_id}"] = event_handler
                
                self.logger.info(f"Dynamic entry '{entry_id}' registered for plugin {plugin_id}")
                
            elif action == "unregister":
                # 注销动态 entry
                with state.acquire_event_handlers_write_lock():
                    state.event_handlers.pop(f"{plugin_id}.{entry_id}", None)
                    state.event_handlers.pop(f"{plugin_id}:plugin_entry:{entry_id}", None)
                
                self.logger.info(f"Dynamic entry '{entry_id}' unregistered for plugin {plugin_id}")
                
            elif action == "enable":
                # 启用 entry
                with state.acquire_event_handlers_write_lock():
                    handler = state.event_handlers.get(f"{plugin_id}.{entry_id}")
                    if handler and hasattr(handler.meta, "enabled"):
                        handler.meta.enabled = True
                
                self.logger.info(f"Entry '{entry_id}' enabled for plugin {plugin_id}")
                
            elif action == "disable":
                # 禁用 entry
                with state.acquire_event_handlers_write_lock():
                    handler = state.event_handlers.get(f"{plugin_id}.{entry_id}")
                    if handler and hasattr(handler.meta, "enabled"):
                        handler.meta.enabled = False
                
                self.logger.info(f"Entry '{entry_id}' disabled for plugin {plugin_id}")
                
            else:
                self.logger.warning(f"Unknown ENTRY_UPDATE action: {action}")
                
        except Exception:
            self.logger.exception("Failed to handle ENTRY_UPDATE")
    
    async def _handle_static_ui_register(self, msg: Dict[str, Any]) -> None:
        """处理静态 UI 注册消息
        
        Args:
            msg: STATIC_UI_REGISTER 消息，包含 plugin_id, config
        """
        try:
            from plugin.core.state import state
            
            plugin_id = self.plugin_id
            incoming_plugin_id = msg.get("plugin_id")
            if incoming_plugin_id and incoming_plugin_id != self.plugin_id:
                self.logger.warning(
                    "STATIC_UI_REGISTER plugin_id mismatch ignored: expected=%s, got=%s",
                    self.plugin_id,
                    incoming_plugin_id,
                )
                return
            config = msg.get("config")
            
            if not config:
                self.logger.warning(f"STATIC_UI_REGISTER message missing config: {msg}")
                return
            
            self.logger.info(f"Processing STATIC_UI_REGISTER: plugin_id={plugin_id}")
            
            # 更新 state.plugins 中的静态 UI 配置
            with state.acquire_plugins_write_lock():
                plugin_meta = state.plugins.get(plugin_id)
                if isinstance(plugin_meta, dict):
                    plugin_meta["static_ui_config"] = config
                    state.plugins[plugin_id] = plugin_meta
                    self.logger.info(f"Static UI registered for plugin {plugin_id}: {config.get('directory')}")
                else:
                    self.logger.warning(f"Plugin {plugin_id} not found in state.plugins")
                    
        except Exception:
            self.logger.exception("Failed to handle STATIC_UI_REGISTER")
    
    async def send_stop_command(self) -> None:
        """发送停止命令到插件进程"""
        try:
            # cmd_queue 可能被高频消息挤满；并且本类内部 executor 可能被 queue.get 占满。
            # STOP 投递用 asyncio.to_thread（默认线程池），避免被 self._executor 饥饿。
            sent = False
            for _ in range(10):
                try:
                    await asyncio.to_thread(self.cmd_queue.put, {"type": "STOP"}, True, 0.2)
                    sent = True
                    break
                except Exception:
                    await asyncio.sleep(0)

            if not sent:
                await asyncio.to_thread(self.cmd_queue.put, {"type": "STOP"}, True, QUEUE_GET_TIMEOUT)

            self.logger.debug(f"Sent STOP command to plugin {self.plugin_id}")
        except Exception as e:
            self.logger.warning(f"Failed to send STOP command to plugin {self.plugin_id}: {e}")
    
    async def _consume_results(self) -> None:
        """
        后台任务：持续消费结果队列
        
        这个任务会一直运行直到收到关闭信号
        """
        self._ensure_shutdown_event()
        shutdown_event = self._shutdown_event
        if shutdown_event is None:
            return
        loop = asyncio.get_running_loop()
        
        while not shutdown_event.is_set():
            try:
                # 使用 executor 在后台线程中阻塞读取队列
                # QUEUE_GET_TIMEOUT 是 1.0 秒，超时后会继续循环
                res = await loop.run_in_executor(
                    self._executor,
                    lambda: self.res_queue.get(timeout=QUEUE_GET_TIMEOUT)
                )
                # 收到响应后立即处理，不延迟

                if isinstance(res, dict) and res.get("_type") == "__shutdown__":
                    break
                
                req_id = res.get("req_id")
                if not req_id:
                    self.logger.warning(f"Received result without req_id from plugin {self.plugin_id}")
                    continue
                
                # 记录收到响应的时间
                self.logger.debug(
                    f"Received result for req_id {req_id} from plugin {self.plugin_id}, "
                    f"success={res.get('success')}"
                )
                
                future = self._pending_futures.get(req_id)
                if future:
                    if not future.done():
                        # Future 还未完成，设置结果
                        self.logger.debug(
                            f"Setting result for req_id {req_id}, Future is not done yet"
                        )
                        # 始终回传结果，让调用方统一处理成功/失败
                        future.set_result(res)
                        # 设置结果后，从字典中移除
                        self._pending_futures.pop(req_id, None)
                        self.logger.debug(f"Result set and Future removed for req_id {req_id}")
                    else:
                        # Future 已经完成（可能因为超时），忽略延迟到达的响应
                        self.logger.warning(
                            f"Received delayed result for req_id {req_id} from plugin {self.plugin_id}, "
                            f"but Future is already done (likely timed out). Ignoring."
                        )
                        # 清理已完成的 Future
                        self._pending_futures.pop(req_id, None)
                else:
                    self.logger.warning(
                        f"Received result for unknown req_id {req_id} from plugin {self.plugin_id}. "
                        f"Available req_ids: {list(self._pending_futures.keys())[:5]}"
                    )
                    
            except Empty:
                # 队列为空，继续等待
                continue
            except asyncio.CancelledError:
                break
            except (OSError, RuntimeError) as e:
                # 系统级错误，记录并继续
                if not shutdown_event.is_set():
                    self.logger.error(f"System error consuming results for plugin {self.plugin_id}: {e}")
                await asyncio.sleep(RESULT_CONSUMER_SLEEP_INTERVAL)
            except Exception:
                # 其他未知异常，记录详细信息
                if not shutdown_event.is_set():
                    self.logger.exception(f"Unexpected error consuming results for plugin {self.plugin_id}")
                # 短暂休眠避免 CPU 占用过高
                await asyncio.sleep(RESULT_CONSUMER_SLEEP_INTERVAL)
    
    def get_status_messages(self, max_count: int | None = None) -> list[Dict[str, Any]]:
        """
        从状态队列中获取消息（非阻塞）
        
        Args:
            max_count: 最多获取的消息数量（None 时使用默认值）
        
        Returns:
            状态消息列表
        """
        from plugin.settings import STATUS_MESSAGE_DEFAULT_MAX_COUNT
        if max_count is None:
            max_count = STATUS_MESSAGE_DEFAULT_MAX_COUNT
        messages = []
        count = 0
        while count < max_count:
            try:
                msg = self.status_queue.get_nowait()
                messages.append(msg)
                count += 1
            except Empty:
                break
        return messages
    
    # 特殊消息类型路由表：type → handler method name
    # 匹配的消息由对应 handler 直接处理，不转发到主进程队列。
    # 新增消息类型只需添加一行映射。
    _MESSAGE_ROUTING: ClassVar[Dict[str, str]] = {
        "ENTRY_UPDATE": "_handle_entry_update",
        "STATIC_UI_REGISTER": "_handle_static_ui_register",
    }

    async def _forward_message(self, msg: Dict[str, Any]) -> None:
        """Store message to bus, forward to main queue, and log with dedup."""
        if not self._message_target_queue:
            return

        if isinstance(msg, dict) and not msg.get("_bus_stored"):
            try:
                from plugin.core.state import state

                msg = dict(msg)
                if not isinstance(msg.get("message_id"), str) or not msg.get("message_id"):
                    msg["message_id"] = str(uuid.uuid4())
                if not isinstance(msg.get("time"), str) or not msg.get("time"):
                    msg["time"] = now_iso()
                msg["_bus_stored"] = True
                state.append_message_record(msg)
            except Exception:
                self.logger.debug(
                    "Failed to store message to bus for plugin {}",
                    self.plugin_id,
                    exc_info=True,
                )

        try:
            # 尽量快速转发；如果主进程已进入 shutdown 或队列不再消费，避免无限阻塞。
            await asyncio.wait_for(self._message_target_queue.put(msg), timeout=0.05)
        except asyncio.TimeoutError:
            # 主队列可能被阻塞/不再消费，直接丢弃以保证 shutdown 及时。
            return

        if PLUGIN_LOG_MESSAGE_FORWARD:
            log_content = _format_log_text(msg.get("content", ""))
            window = PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS
            if window and window > 0:
                now_ts = time.monotonic()
                key = (
                    self.plugin_id,
                    msg.get("source", "unknown"),
                    msg.get("priority", 0),
                    msg.get("description", ""),
                    log_content,
                )
                last_key = self._last_forward_log_key
                last_ts = self._last_forward_log_time
                if (
                    last_key == key
                    and last_ts > 0.0
                    and (now_ts - last_ts) <= window
                ):
                    # 在去重时间窗口内的重复日志，累加计数并跳过，避免刷屏和性能损耗
                    self._last_forward_log_repeat_count += 1
                    return

                # 输出上一条日志的重复统计（如果有）
                if last_key is not None and self._last_forward_log_repeat_count > 0:
                    self.logger.info(
                        "[MESSAGE FORWARD] (suppressed {} duplicate messages for Plugin: {} | Source: {} | Priority: {} | Description: {})",
                        self._last_forward_log_repeat_count,
                        last_key[0],
                        last_key[1],
                        last_key[2],
                        last_key[3],
                    )

                # 切换到当前日志 key，重置计数
                self._last_forward_log_key = key
                self._last_forward_log_time = now_ts
                self._last_forward_log_repeat_count = 0

            self.logger.info(
                f"[MESSAGE FORWARD] Plugin: {self.plugin_id} | "
                f"Source: {msg.get('source', 'unknown')} | "
                f"Priority: {msg.get('priority', 0)} | "
                f"Description: {msg.get('description', '')} | "
                f"Content: {log_content}"
            )

    async def _consume_messages(self) -> None:
        """
        后台任务：持续消费消息队列
        
        将插件推送的消息转发到主进程的消息队列
        """
        if self._message_target_queue is None:
            self.logger.warning(f"Message target queue not set for plugin {self.plugin_id}, message consumer will not work")
            return
        
        self._ensure_shutdown_event()
        shutdown_event = self._shutdown_event
        if shutdown_event is None:
            return
        loop = asyncio.get_running_loop()
        
        while not shutdown_event.is_set():
            try:
                # 使用 executor 在后台线程中阻塞读取队列
                msg = await loop.run_in_executor(
                    self._executor,
                    lambda: self.message_queue.get(timeout=QUEUE_GET_TIMEOUT)
                )

                if isinstance(msg, dict) and msg.get("_type") == "__shutdown__":
                    break
                
                # 路由特殊消息类型
                if isinstance(msg, dict):
                    handler_name = self._MESSAGE_ROUTING.get(msg.get("type", ""))
                    if handler_name:
                        await getattr(self, handler_name)(msg)
                        continue
                
                # shutdown 期间不要在主进程队列上阻塞等待
                if shutdown_event.is_set():
                    continue

                # 转发普通消息到主进程
                try:
                    await self._forward_message(msg)
                except asyncio.QueueFull:
                    self.logger.warning(
                        f"Main message queue is full, dropping message from plugin {self.plugin_id}"
                    )
                except (AttributeError, RuntimeError) as e:
                    self.logger.error(f"Queue error forwarding message from plugin {self.plugin_id}: {e}")
                except Exception:
                    self.logger.exception(
                        f"Unexpected error forwarding message from plugin {self.plugin_id}"
                    )
            except Empty:
                # 队列为空，继续等待
                continue
            except asyncio.CancelledError:
                break
            except (OSError, RuntimeError) as e:
                # 系统级错误
                if not shutdown_event.is_set():
                    self.logger.error(f"System error consuming messages for plugin {self.plugin_id}: {e}")
                await asyncio.sleep(MESSAGE_CONSUMER_SLEEP_INTERVAL)
            except Exception:
                # 其他未知异常
                if not shutdown_event.is_set():
                    self.logger.exception(f"Unexpected error consuming messages for plugin {self.plugin_id}")
                # 短暂休眠避免 CPU 占用过高
                await asyncio.sleep(MESSAGE_CONSUMER_SLEEP_INTERVAL)

