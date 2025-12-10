"""
插件上下文模块

提供插件运行时上下文，包括状态更新和消息推送功能。
"""
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

