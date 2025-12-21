"""
插件上下文模块

提供插件运行时上下文，包括状态更新和消息推送功能。
"""
import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI


@dataclass
class PluginContext:
    """插件运行时上下文"""
    plugin_id: str
    config_path: Path
    logger: Any  # logging.Logger
    status_queue: Any
    message_queue: Any = None  # 消息推送队列
    app: Optional[FastAPI] = None
    _plugin_comm_queue: Optional[Any] = None  # 插件间通信队列（主进程提供）

    def update_status(self, status: Dict[str, Any]) -> None:
        """
        子进程 / 插件内部调用：把原始 status 丢到主进程的队列里，由主进程统一整理。
        """
        try:
            payload = {
                "type": "STATUS_UPDATE",
                "plugin_id": self.plugin_id,
                "data": status,
                "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            self.status_queue.put_nowait(payload)
            # 这条日志爱要不要
            self.logger.info(f"Plugin {self.plugin_id} status updated: {payload}")
        except (AttributeError, RuntimeError) as e:
            # 队列操作错误
            self.logger.warning(f"Queue error updating status for plugin {self.plugin_id}: {e}")
        except Exception as e:
            # 其他未知异常
            self.logger.exception(f"Unexpected error updating status for plugin {self.plugin_id}: {e}")

    def push_message(
        self,
        source: str,
        message_type: str,
        description: str = "",
        priority: int = 0,
        content: Optional[str] = None,
        binary_data: Optional[bytes] = None,
        binary_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        子进程 / 插件内部调用：推送消息到主进程的消息队列。
        
        Args:
            source: 插件自己标明的来源
            message_type: 消息类型，可选值: "text", "url", "binary", "binary_url"
            description: 插件自己标明的描述
            priority: 插件自己设定的优先级，数字越大优先级越高
            content: 文本内容或URL（当message_type为text或url时）
            binary_data: 二进制数据（当message_type为binary时，仅用于小文件）
            binary_url: 二进制文件的URL（当message_type为binary_url时）
            metadata: 额外的元数据
        """
        if self.message_queue is None:
            self.logger.warning(f"Plugin {self.plugin_id} message_queue is not available, message dropped")
            return
        
        try:
            payload = {
                "type": "MESSAGE_PUSH",
                "plugin_id": self.plugin_id,
                "source": source,
                "description": description,
                "priority": priority,
                "message_type": message_type,
                "content": content,
                "binary_data": binary_data,
                "binary_url": binary_url,
                "metadata": metadata or {},
                "time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            self.message_queue.put_nowait(payload)
            self.logger.debug(f"Plugin {self.plugin_id} pushed message: {source} - {description}")
        except (AttributeError, RuntimeError) as e:
            # 队列操作错误
            self.logger.warning(f"Queue error pushing message for plugin {self.plugin_id}: {e}")
        except Exception as e:
            # 其他未知异常
            self.logger.exception(f"Unexpected error pushing message for plugin {self.plugin_id}: {e}")
    
    def trigger_plugin_event(
        self,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        args: Dict[str, Any],
        timeout: float = 5.0
    ) -> Dict[str, Any]:
        """
        触发其他插件的自定义事件（插件间通信）
        
        这是插件间功能复用的机制，使用 Queue 而不是 HTTP。
        处理流程和 plugin_entry 一样，在单线程的命令循环中执行。
        
        Args:
            target_plugin_id: 目标插件ID
            event_type: 自定义事件类型
            event_id: 事件ID
            args: 参数字典
            timeout: 超时时间（秒）
            
        Returns:
            事件处理器的返回结果
            
        Raises:
            RuntimeError: 如果通信队列不可用
            TimeoutError: 如果超时
            Exception: 如果事件执行失败
        """
        if self._plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {self.plugin_id}. "
                "This method can only be called from within a plugin process."
            )
        
        request_id = str(uuid.uuid4())
        request = {
            "type": "PLUGIN_TO_PLUGIN",
            "from_plugin": self.plugin_id,
            "to_plugin": target_plugin_id,
            "event_type": event_type,
            "event_id": event_id,
            "args": args,
            "request_id": request_id,
            "timeout": timeout,
        }
        
        # 发送请求到主进程的通信队列（multiprocessing.Queue，同步操作）
        try:
            self._plugin_comm_queue.put(request, timeout=timeout)
            self.logger.debug(
                f"[PluginContext] Sent plugin communication request: {self.plugin_id} -> {target_plugin_id}, "
                f"event={event_type}.{event_id}, req_id={request_id}"
            )
        except Exception as e:
            self.logger.error(f"Failed to send plugin communication request: {e}")
            raise RuntimeError(f"Failed to send plugin communication request: {e}") from e
        
        # 等待响应（同步等待，因为这是在插件进程的单线程中）
        # 主进程会将响应通过通信队列返回
        import time
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # 从通信队列获取响应（非阻塞）
                try:
                    response = self._plugin_comm_queue.get(timeout=0.1)
                except Exception:
                    # 队列为空或超时，继续等待
                    time.sleep(0.01)  # 避免 CPU 占用过高
                    continue
                
                # 检查是否是我们的响应
                if response.get("type") == "PLUGIN_TO_PLUGIN_RESPONSE":
                    # 检查目标插件和请求ID
                    if (response.get("to_plugin") == self.plugin_id and 
                        response.get("request_id") == request_id):
                        # 找到我们的响应
                        if response.get("error"):
                            error_msg = response.get("error")
                            self.logger.error(
                                f"[PluginContext] Plugin communication error: {error_msg}"
                            )
                            raise RuntimeError(error_msg)
                        else:
                            result = response.get("result")
                            self.logger.debug(
                                f"[PluginContext] Received plugin communication response: "
                                f"req_id={request_id}, result={result}"
                            )
                            return result
                    else:
                        # 不是我们的响应，需要放回队列
                        # 由于 multiprocessing.Queue 不支持放回，我们创建一个临时队列
                        # 或者重新放入队列（但可能顺序会乱）
                        # 暂时重新放入队列，让其他请求处理
                        try:
                            self._plugin_comm_queue.put(response, timeout=0.1)
                        except Exception:
                            # 如果放回失败，记录警告但继续
                            self.logger.warning(
                                f"[PluginContext] Failed to put back response for different request: "
                                f"to_plugin={response.get('to_plugin')}, req_id={response.get('request_id')}"
                            )
                else:
                    # 不是响应消息，可能是其他请求，重新放入队列
                    try:
                        self._plugin_comm_queue.put(response, timeout=0.1)
                    except Exception:
                        self.logger.warning(
                            f"[PluginContext] Failed to put back non-response message: {response.get('type')}"
                        )
                    
            except Exception as e:
                self.logger.exception(f"Error waiting for plugin communication response: {e}")
                raise
        
        # 超时
        raise TimeoutError(
            f"Plugin {target_plugin_id} event {event_type}.{event_id} timed out after {timeout}s"
        )

