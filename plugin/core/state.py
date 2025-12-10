"""
插件运行时状态模块

提供插件系统的全局运行时状态管理。
"""
import asyncio
import logging
import threading
from typing import Any, Dict

from plugin.sdk.events import EventHandler


EVENT_QUEUE_MAX = 1000
MESSAGE_QUEUE_MAX = 1000


class PluginRuntimeState:
    """插件运行时状态"""
    
    def __init__(self):
        self.plugins: Dict[str, Dict[str, Any]] = {}
        self.plugin_instances: Dict[str, Any] = {}
        self.event_handlers: Dict[str, EventHandler] = {}
        self.plugin_status: Dict[str, Dict[str, Any]] = {}
        self.plugin_hosts: Dict[str, Any] = {}
        self.plugin_status_lock = threading.Lock()
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
        self.message_queue: asyncio.Queue = asyncio.Queue(maxsize=MESSAGE_QUEUE_MAX)


# 全局状态实例
state = PluginRuntimeState()

