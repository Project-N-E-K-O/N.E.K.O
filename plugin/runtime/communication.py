"""
插件进程间通信资源管理器

负责管理插件进程间的通信资源,包括队列、Future、后台任务等。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import Empty
from typing import Any, Dict, Optional

from multiprocessing import Queue

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
from plugin.api.exceptions import PluginExecutionError
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
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("plugin.communication"))
    
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
            await asyncio.to_thread(self.message_queue.put, _SHUTDOWN_SENTINEL, True, QUEUE_GET_TIMEOUT)
        except Exception:
            # shutdown 需要尽力而为：即使唤醒失败也继续走超时等待/取消逻辑
            pass

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
        
        # 关键日志：记录发送触发命令
        self.logger.info(
            "[CommManager] Sending TRIGGER command: plugin_id=%s, entry_id=%s, req_id=%s",
            self.plugin_id,
            entry_id,
            req_id,
        )
        # 详细参数信息使用 DEBUG
        self.logger.debug(
            "[CommManager] Args: type=%s, keys=%s, content=%s",
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
            "[CommManager] TRIGGER message: %s",
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
            "[CommManager] Sending TRIGGER_CUSTOM command: plugin_id=%s, event_type=%s, event_id=%s, req_id=%s",
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
                        if res.get("success"):
                            future.set_result(res)
                        else:
                            future.set_exception(Exception(res.get("error", "Unknown error")))
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
            except Exception as e:
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
                
                # 转发消息到主进程的消息队列
                try:
                    if self._message_target_queue:
                        # shutdown 期间不要在主进程队列上阻塞等待，否则可能导致插件 shutdown 卡住。
                        if shutdown_event.is_set():
                            continue
                        if isinstance(msg, dict) and not msg.get("_bus_stored"):
                            try:
                                from plugin.core.state import state

                                msg = dict(msg)
                                if not isinstance(msg.get("message_id"), str) or not msg.get("message_id"):
                                    msg["message_id"] = str(uuid.uuid4())
                                if not isinstance(msg.get("time"), str) or not msg.get("time"):
                                    msg["time"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                                msg["_bus_stored"] = True
                                state.append_message_record(msg)
                            except Exception:
                                self.logger.debug(
                                    "Failed to store message to bus for plugin %s",
                                    self.plugin_id,
                                    exc_info=True,
                                )

                        try:
                            # 尽量快速转发；如果主进程已进入 shutdown 或队列不再消费，避免无限阻塞。
                            await asyncio.wait_for(self._message_target_queue.put(msg), timeout=0.05)
                        except asyncio.TimeoutError:
                            # 主队列可能被阻塞/不再消费，直接丢弃以保证 shutdown 及时。
                            continue
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
                                    continue

                                # 输出上一条日志的重复统计（如果有）
                                if last_key is not None and self._last_forward_log_repeat_count > 0:
                                    self.logger.info(
                                        "[MESSAGE FORWARD] (suppressed %d duplicate messages for Plugin: %s | Source: %s | Priority: %s | Description: %s)",
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
                except asyncio.QueueFull:
                    self.logger.warning(
                        f"Main message queue is full, dropping message from plugin {self.plugin_id}"
                    )
                except (AttributeError, RuntimeError) as e:
                    self.logger.error(f"Queue error forwarding message from plugin {self.plugin_id}: {e}")
                except Exception as e:
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
            except Exception as e:
                # 其他未知异常
                if not shutdown_event.is_set():
                    self.logger.exception(f"Unexpected error consuming messages for plugin {self.plugin_id}")
                # 短暂休眠避免 CPU 占用过高
                await asyncio.sleep(MESSAGE_CONSUMER_SLEEP_INTERVAL)

