"""
插件运行时状态模块

提供插件系统的全局运行时状态管理。
"""
import asyncio
import logging
import threading
from typing import Any, Dict, Optional

from plugin.sdk.events import EventHandler
from plugin.settings import EVENT_QUEUE_MAX, MESSAGE_QUEUE_MAX


class PluginRuntimeState:
    """插件运行时状态"""
    
    def __init__(self):
        self.plugins: Dict[str, Dict[str, Any]] = {}
        self.plugin_instances: Dict[str, Any] = {}
        self.event_handlers: Dict[str, EventHandler] = {}
        self.plugin_status: Dict[str, Dict[str, Any]] = {}
        self.plugin_hosts: Dict[str, Any] = {}
        self.plugin_status_lock = threading.Lock()
        self.plugins_lock = threading.Lock()  # 保护 plugins 字典的线程安全
        self.event_handlers_lock = threading.Lock()  # 保护 event_handlers 字典的线程安全
        self.plugin_hosts_lock = threading.Lock()  # 保护 plugin_hosts 字典的线程安全
        self._event_queue: Optional[asyncio.Queue] = None
        self._message_queue: Optional[asyncio.Queue] = None

    @property
    def event_queue(self) -> asyncio.Queue:
        if self._event_queue is None:
            self._event_queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
        return self._event_queue

    @property
    def message_queue(self) -> asyncio.Queue:
        if self._message_queue is None:
            self._message_queue = asyncio.Queue(maxsize=MESSAGE_QUEUE_MAX)
        return self._message_queue
    
    _plugin_comm_queue: Optional[Any] = None  # 插件间通信队列（multiprocessing.Queue）
    
    @property
    def plugin_comm_queue(self):
        """插件间通信队列（用于插件调用其他插件的 custom_event）"""
        if self._plugin_comm_queue is None:
            import multiprocessing
            # 使用 multiprocessing.Queue 因为需要跨进程
            self._plugin_comm_queue = multiprocessing.Queue()
        return self._plugin_comm_queue


# 全局状态实例
state = PluginRuntimeState()

