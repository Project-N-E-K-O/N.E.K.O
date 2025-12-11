"""
插件运行时状态模块

提供插件系统的全局运行时状态管理。
"""
import asyncio
import logging
import threading
from typing import Any, Dict

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
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
        self.message_queue: asyncio.Queue = asyncio.Queue(maxsize=MESSAGE_QUEUE_MAX)


# 全局状态实例
state = PluginRuntimeState()

