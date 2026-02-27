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
    # Hook 装饰器
    hook,
    before_entry,
    after_entry,
    around_entry,
    replace_entry,
    # 类型常量（用于 IDE 识别）
    PERSIST_ATTR,
    CHECKPOINT_ATTR,  # 向后兼容别名
    WORKER_MODE_ATTR,
    EntryKind,
)
from .hooks import HookMeta, HookHandler, HookTiming, HOOK_META_ATTR
from .hook_executor import HookExecutorMixin
from .call_chain import (
    CallChain,
    AsyncCallChain,
    CircularCallError,
    CallChainTooDeepError,
    get_call_chain,
    get_call_depth,
    is_in_call_chain,
)
from .base import NekoPluginBase, PluginMeta
from .router import PluginRouter, PluginRouterError
from .config import PluginConfig
from .plugins import Plugins
from .events import EventMeta, EventHandler, EVENT_META_ATTR
from .system_info import SystemInfo
from .memory import MemoryClient
from .types import PluginContextProtocol
from .store import PluginStore
from .database import PluginDatabase, PluginKVStore
from .state import PluginStatePersistence, StatePersistence, EXTENDED_TYPES

# Adapter 模块（可选导入，避免循环依赖）
try:
    from .adapter import (
        AdapterBase,
        AdapterConfig,
        AdapterContext,
        AdapterMode,
        AdapterMessage,
        AdapterResponse,
        AdapterGatewayCore,
        ExternalEnvelope,
        CallablePluginInvoker,
        DefaultPolicyEngine,
        DefaultRequestNormalizer,
        DefaultResponseSerializer,
        DefaultRouteEngine,
        GatewayAction,
        GatewayError,
        GatewayErrorException,
        GatewayRequest,
        GatewayResponse,
        LoggerLike,
        PluginInvoker,
        PolicyEngine,
        Protocol,
        RequestNormalizer,
        ResponseSerializer,
        RouteDecision,
        RouteEngine,
        RouteMode,
        TransportAdapter,
        on_adapter_event,
        on_adapter_startup,
        on_adapter_shutdown,
    )
    _ADAPTER_AVAILABLE = True
except ImportError:
    _ADAPTER_AVAILABLE = False

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
    
    # Hook 装饰器（插件内中间件 & 跨插件 Hook）
    "hook",             # Hook 装饰器
    "before_entry",     # before 类型 Hook 快捷方式
    "after_entry",      # after 类型 Hook 快捷方式
    "around_entry",     # around 类型 Hook 快捷方式
    "replace_entry",    # replace 类型 Hook 快捷方式
    "HookMeta",         # Hook 元数据
    "HookHandler",      # Hook 处理器
    "HookTiming",       # Hook 时机类型
    "HookExecutorMixin", # Hook 执行器 Mixin（供自定义类使用）
    "HOOK_META_ATTR",   # Hook 元数据属性名
    
    # 调用链追踪（防止循环调用和死锁）
    "CallChain",        # 同步调用链追踪器
    "AsyncCallChain",   # 异步调用链追踪器
    "CircularCallError", # 循环调用错误
    "CallChainTooDeepError", # 调用链过深错误
    "get_call_chain",   # 获取当前调用链
    "get_call_depth",   # 获取当前调用深度
    "is_in_call_chain", # 检查是否在调用链中
    
    # 基类和元数据
    "NekoPluginBase",   # 插件基类
    "PluginRouter",     # 插件路由器（模块化入口点，支持动态加载/卸载）
    "PluginRouterError", # 路由器错误
    "PluginMeta",       # 插件元数据
    "PluginConfig",     # 插件配置
    "Plugins",          # 插件间调用
    "EventMeta",        # 事件元数据
    "EventHandler",     # 事件处理器
    "SystemInfo",       # 系统信息
    "MemoryClient",     # 记忆客户端
    
    # 状态持久化
    "PluginStatePersistence", # 状态持久化管理器（推荐）
    "StatePersistence", # 向后兼容别名
    "EXTENDED_TYPES",   # 支持的扩展类型 (datetime, Enum, set, Path 等)
    
    # 类型定义和常量
    "PluginContextProtocol",
    "EntryKind",        # 入口类型: "service", "action", "hook", "custom", "lifecycle", "consumer", "timer"
    "PERSIST_ATTR",     # 持久化属性名
    "CHECKPOINT_ATTR",  # 向后兼容别名
    "WORKER_MODE_ATTR", # Worker 模式属性名
    "EVENT_META_ATTR",  # 事件元数据属性名
    
    # Adapter 相关（type="adapter" 插件使用）
    "AdapterBase",      # Adapter 基类
    "AdapterConfig",    # Adapter 配置
    "AdapterContext",   # Adapter 上下文
    "AdapterMode",      # Adapter 工作模式
    "AdapterMessage",   # Adapter 消息
    "AdapterResponse",  # Adapter 响应
    "AdapterGatewayCore", # Gateway Core 编排器
    "ExternalEnvelope", # 外部协议包
    "DefaultRequestNormalizer", # 默认请求规范化器
    "DefaultPolicyEngine", # 默认策略引擎
    "DefaultRouteEngine", # 默认路由引擎
    "DefaultResponseSerializer", # 默认响应序列化器
    "CallablePluginInvoker", # 可注入调用器
    "GatewayAction",    # Gateway 动作类型
    "GatewayRequest",   # Gateway 统一请求
    "GatewayResponse",  # Gateway 统一响应
    "GatewayError",     # Gateway 结构化错误
    "GatewayErrorException", # Gateway 错误异常
    "RouteDecision",    # Gateway 路由决策
    "RouteMode",        # Gateway 路由模式
    "LoggerLike",       # Gateway 日志接口
    "TransportAdapter", # 传输层接口
    "RequestNormalizer", # 规范化接口
    "PolicyEngine",     # 策略引擎接口
    "RouteEngine",      # 路由引擎接口
    "PluginInvoker",    # 插件调用接口
    "ResponseSerializer", # 响应序列化接口
    "Protocol",         # 协议类型枚举
    "on_adapter_event", # Adapter 事件装饰器
    "on_adapter_startup",   # Adapter 启动装饰器
    "on_adapter_shutdown",  # Adapter 关闭装饰器
]

# Hook 使用示例:
# from plugin.sdk import PluginRouter, hook, before_entry, after_entry, ok, fail
#
# class MyRouter(PluginRouter):
#     @hook(target="*", timing="before")
#     async def log_all(self, entry_id, params, **_):
#         self.logger.info(f"Calling {entry_id}")
#         return None  # 继续执行
#
#     @before_entry(target="save", priority=10)
#     async def validate(self, params, **_):
#         if not params.get("name"):
#             return fail(message="name required")  # 阻止执行
#         return None

# 便捷导入示例:
# from plugin.sdk import NekoPluginBase, neko_plugin, plugin_entry, lifecycle, ok
# from plugin.sdk import StatePersistence, EXTENDED_TYPES
