"""
资源管理模块：插件进程间通信资源管理器。

负责管理插件进程间的通信资源，包括队列、Future、后台任务等。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import Empty
from typing import Any, Dict, Optional

from multiprocessing import Queue


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
    
    def __post_init__(self):
        """初始化异步资源"""
        self._shutdown_event = asyncio.Event()
        # 为每个插件创建独立的线程池，避免阻塞
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"plugin-comm-{self.plugin_id}"
        )
    
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
    
    async def shutdown(self, timeout: float = 5.0) -> None:
        """
        关闭通信资源
        
        Args:
            timeout: 等待后台任务退出的超时时间
        """
        self.logger.debug(f"Shutting down communication resources for plugin {self.plugin_id}")
        
        # 停止结果消费和消息消费任务
        self._shutdown_event.set()
        
        if self._result_consumer_task and not self._result_consumer_task.done():
            try:
                await asyncio.wait_for(self._result_consumer_task, timeout=timeout)
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
                await asyncio.wait_for(self._message_consumer_task, timeout=timeout)
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
            self._executor.shutdown(wait=True, timeout=timeout)
            self._executor = None
        
        self.logger.debug(f"Communication resources for plugin {self.plugin_id} shutdown complete")
    
    def _cleanup_pending_futures(self) -> None:
        """清理所有待处理的 Future"""
        count = len(self._pending_futures)
        for req_id, future in self._pending_futures.items():
            if not future.done():
                future.cancel()
        self._pending_futures.clear()
        if count > 0:
            self.logger.debug(f"Cleaned up {count} pending futures for plugin {self.plugin_id}")
    
    async def trigger(self, entry_id: str, args: dict, timeout: float = 10.0) -> Any:
        """
        发送触发命令并等待结果
        
        Args:
            entry_id: 入口 ID
            args: 参数
            timeout: 超时时间（秒）
        
        Returns:
            插件返回的结果
        
        Raises:
            TimeoutError: 如果超时
            Exception: 如果插件执行出错
        """
        req_id = str(uuid.uuid4())
        future = asyncio.Future()
        self._pending_futures[req_id] = future
        
        try:
            # 发送命令
            self.cmd_queue.put({
                "type": "TRIGGER",
                "req_id": req_id,
                "entry_id": entry_id,
                "args": args
            })
            
            # 等待结果（带超时）
            try:
                result = await asyncio.wait_for(future, timeout=timeout)
                if result["success"]:
                    return result["data"]
                else:
                    raise Exception(result.get("error", "Unknown error"))
            except asyncio.TimeoutError:
                self.logger.error(
                    f"Plugin {self.plugin_id} entry {entry_id} timed out after {timeout}s"
                )
                raise TimeoutError(f"Plugin execution timed out after {timeout}s")
        finally:
            # 清理 Future（无论成功还是失败）
            self._pending_futures.pop(req_id, None)
    
    async def send_stop_command(self) -> None:
        """发送停止命令到插件进程"""
        try:
            self.cmd_queue.put({"type": "STOP"}, timeout=1.0)
            self.logger.debug(f"Sent STOP command to plugin {self.plugin_id}")
        except Exception as e:
            self.logger.warning(f"Failed to send STOP command to plugin {self.plugin_id}: {e}")
    
    async def _consume_results(self) -> None:
        """
        后台任务：持续消费结果队列
        
        这个任务会一直运行直到收到关闭信号
        """
        loop = asyncio.get_event_loop()
        
        while not self._shutdown_event.is_set():
            try:
                # 使用 executor 在后台线程中阻塞读取队列
                res = await loop.run_in_executor(
                    self._executor,
                    lambda: self.res_queue.get(timeout=1.0)
                )
                
                req_id = res.get("req_id")
                if not req_id:
                    self.logger.warning(f"Received result without req_id from plugin {self.plugin_id}")
                    continue
                
                future = self._pending_futures.pop(req_id, None)
                if future:
                    if not future.done():
                        if res.get("success"):
                            future.set_result(res)
                        else:
                            future.set_exception(Exception(res.get("error", "Unknown error")))
                else:
                    self.logger.warning(
                        f"Received result for unknown req_id {req_id} from plugin {self.plugin_id}"
                    )
                    
            except Empty:
                # 队列为空，继续等待
                continue
            except (OSError, RuntimeError) as e:
                # 系统级错误，记录并继续
                if not self._shutdown_event.is_set():
                    self.logger.error(f"System error consuming results for plugin {self.plugin_id}: {e}")
                await asyncio.sleep(0.1)
            except Exception as e:
                # 其他未知异常，记录详细信息
                if not self._shutdown_event.is_set():
                    self.logger.exception(f"Unexpected error consuming results for plugin {self.plugin_id}: {e}")
                # 短暂休眠避免 CPU 占用过高
                await asyncio.sleep(0.1)
    
    def get_status_messages(self, max_count: int = 100) -> list[Dict[str, Any]]:
        """
        从状态队列中获取消息（非阻塞）
        
        Args:
            max_count: 最多获取的消息数量
        
        Returns:
            状态消息列表
        """
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
        
        loop = asyncio.get_event_loop()
        
        while not self._shutdown_event.is_set():
            try:
                # 使用 executor 在后台线程中阻塞读取队列
                msg = await loop.run_in_executor(
                    self._executor,
                    lambda: self.message_queue.get(timeout=1.0)
                )
                
                # 转发消息到主进程的消息队列
                try:
                    if self._message_target_queue:
                        await self._message_target_queue.put(msg)
                        self.logger.debug(f"Forwarded message from plugin {self.plugin_id} to main queue")
                except asyncio.QueueFull:
                    self.logger.warning(f"Main message queue is full, dropping message from plugin {self.plugin_id}")
                except (AttributeError, RuntimeError) as e:
                    self.logger.error(f"Queue error forwarding message from plugin {self.plugin_id}: {e}")
                except Exception as e:
                    self.logger.exception(f"Unexpected error forwarding message from plugin {self.plugin_id}: {e}")
                    
            except Empty:
                # 队列为空，继续等待
                continue
            except (OSError, RuntimeError) as e:
                # 系统级错误
                if not self._shutdown_event.is_set():
                    self.logger.error(f"System error consuming messages for plugin {self.plugin_id}: {e}")
                await asyncio.sleep(0.1)
            except Exception as e:
                # 其他未知异常
                if not self._shutdown_event.is_set():
                    self.logger.exception(f"Unexpected error consuming messages for plugin {self.plugin_id}: {e}")
                # 短暂休眠避免 CPU 占用过高
                await asyncio.sleep(0.1)

