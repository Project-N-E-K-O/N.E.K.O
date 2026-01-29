"""
Plugin SDK 模块

提供插件开发工具包, 包括基类、事件系统和装饰器。

基本用法::

    from plugin.sdk import NekoPluginBase, neko_plugin, plugin_entry, lifecycle, ok
    
    @neko_plugin
    class MyPlugin(NekoPluginBase):
        __freezable__ = ["counter"]  # 需要持久化的属性
        __persist_mode__ = "auto"    # 自动保存状态
        
        @lifecycle(id="startup")
        def on_startup(self):
            self.counter = 0
        
        @lifecycle(id="freeze")
        def on_freeze(self):
            self.logger.info("插件即将冻结...")
        
        @lifecycle(id="unfreeze")
        def on_unfreeze(self):
            self.logger.info("插件从冻结状态恢复!")
        
        @plugin_entry(id="increment", persist=True)
        def increment(self, value: int = 1):
            self.counter += value
            return ok(data={"counter": self.counter})
"""

from .version import SDK_VERSION
from .errors import ErrorCode
from .responses import ok, fail
from .decorators import (
    neko_plugin,
    on_event,
    plugin_entry,
    lifecycle,
    message,
    timer_interval,
    custom_event,
    worker,
    plugin,
    # 类型常量（用于 IDE 识别）
    PERSIST_ATTR,
    CHECKPOINT_ATTR,  # 向后兼容别名
    WORKER_MODE_ATTR,
    EntryKind,
)
from .base import NekoPluginBase, PluginMeta
from .config import PluginConfig
from .plugins import Plugins
from .events import EventMeta, EventHandler, EVENT_META_ATTR
from .system_info import SystemInfo
from .memory import MemoryClient
from .types import PluginContextProtocol
from .state import StatePersistence, EXTENDED_TYPES

__all__ = [
    # 版本和错误码
    "SDK_VERSION",
    "ErrorCode",
    
    # 响应辅助函数
    "ok",
    "fail",
    
    # 装饰器
    "neko_plugin",      # 插件类装饰器
    "plugin_entry",     # 插件入口装饰器
    "lifecycle",        # 生命周期装饰器 (startup/shutdown/reload/freeze/unfreeze)
    "on_event",         # 通用事件装饰器
    "message",          # 消息事件装饰器
    "timer_interval",   # 定时任务装饰器
    "custom_event",     # 自定义事件装饰器
    "worker",           # Worker 模式装饰器
    "plugin",           # 插件装饰器命名空间
    
    # 基类和元数据
    "NekoPluginBase",   # 插件基类
    "PluginMeta",       # 插件元数据
    "PluginConfig",     # 插件配置
    "Plugins",          # 插件间调用
    "EventMeta",        # 事件元数据
    "EventHandler",     # 事件处理器
    "SystemInfo",       # 系统信息
    "MemoryClient",     # 记忆客户端
    
    # 状态持久化
    "StatePersistence", # 状态持久化管理器
    "EXTENDED_TYPES",   # 支持的扩展类型 (datetime, Enum, set, Path 等)
    
    # 类型定义和常量
    "PluginContextProtocol",
    "EntryKind",        # 入口类型: "service", "action", "hook", "custom", "lifecycle", "consumer", "timer"
    "PERSIST_ATTR",     # 持久化属性名
    "CHECKPOINT_ATTR",  # 向后兼容别名
    "WORKER_MODE_ATTR", # Worker 模式属性名
    "EVENT_META_ATTR",  # 事件元数据属性名
]

# 便捷导入示例:
# from plugin.sdk import NekoPluginBase, neko_plugin, plugin_entry, lifecycle, ok
# from plugin.sdk import StatePersistence, EXTENDED_TYPES
