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

# ── Eager imports: used by virtually every plugin ──────────────────────
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
    plugin,
    hook,
    before_entry,
    after_entry,
    around_entry,
    replace_entry,
    PERSIST_ATTR,
    CHECKPOINT_ATTR,
    EntryKind,
)
from .hooks import HookMeta, HookHandler, HookTiming, HOOK_META_ATTR
from .hook_executor import HookExecutorMixin
from .base import NekoPluginBase, PluginMeta
from .router import PluginRouter, PluginRouterError
from .config import PluginConfig
from .plugins import Plugins
from .events import EventMeta, EventHandler, EVENT_META_ATTR

# ── Lazy imports: loaded on first access via __getattr__ ───────────────
# call_chain, system_info, memory, types, store, database, state, adapter

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # call_chain
    "CallChain":           (".call_chain", "CallChain"),
    "AsyncCallChain":      (".call_chain", "AsyncCallChain"),
    "CircularCallError":   (".call_chain", "CircularCallError"),
    "CallChainTooDeepError": (".call_chain", "CallChainTooDeepError"),
    "get_call_chain":      (".call_chain", "get_call_chain"),
    "get_call_depth":      (".call_chain", "get_call_depth"),
    "is_in_call_chain":    (".call_chain", "is_in_call_chain"),
    # system_info / memory / types
    "SystemInfo":          (".system_info", "SystemInfo"),
    "MemoryClient":        (".memory", "MemoryClient"),
    "PluginContextProtocol": (".types", "PluginContextProtocol"),
    # store / database / state
    "PluginStore":         (".store", "PluginStore"),
    "PluginDatabase":      (".database", "PluginDatabase"),
    "PluginKVStore":       (".database", "PluginKVStore"),
    "PluginStatePersistence": (".state", "PluginStatePersistence"),
    "StatePersistence":    (".state", "PluginStatePersistence"),
    "EXTENDED_TYPES":      (".state", "EXTENDED_TYPES"),
    # adapter
    "AdapterBase":         (".adapter", "AdapterBase"),
    "AdapterConfig":       (".adapter", "AdapterConfig"),
    "AdapterContext":      (".adapter", "AdapterContext"),
    "AdapterMode":         (".adapter", "AdapterMode"),
    "AdapterMessage":      (".adapter", "AdapterMessage"),
    "AdapterResponse":     (".adapter", "AdapterResponse"),
    "AdapterGatewayCore":  (".adapter", "AdapterGatewayCore"),
    "ExternalEnvelope":    (".adapter", "ExternalEnvelope"),
    "CallablePluginInvoker": (".adapter", "CallablePluginInvoker"),
    "DefaultPolicyEngine": (".adapter", "DefaultPolicyEngine"),
    "DefaultRequestNormalizer": (".adapter", "DefaultRequestNormalizer"),
    "DefaultResponseSerializer": (".adapter", "DefaultResponseSerializer"),
    "DefaultRouteEngine":  (".adapter", "DefaultRouteEngine"),
    "GatewayAction":       (".adapter", "GatewayAction"),
    "GatewayError":        (".adapter", "GatewayError"),
    "GatewayErrorException": (".adapter", "GatewayErrorException"),
    "GatewayRequest":      (".adapter", "GatewayRequest"),
    "GatewayResponse":     (".adapter", "GatewayResponse"),
    "LoggerLike":          (".adapter", "LoggerLike"),
    "PluginInvoker":       (".adapter", "PluginInvoker"),
    "PolicyEngine":        (".adapter", "PolicyEngine"),
    "Protocol":            (".adapter", "Protocol"),
    "RequestNormalizer":   (".adapter", "RequestNormalizer"),
    "ResponseSerializer":  (".adapter", "ResponseSerializer"),
    "RouteDecision":       (".adapter", "RouteDecision"),
    "RouteEngine":         (".adapter", "RouteEngine"),
    "RouteMode":           (".adapter", "RouteMode"),
    "TransportAdapter":    (".adapter", "TransportAdapter"),
    "on_adapter_event":    (".adapter", "on_adapter_event"),
    "on_adapter_startup":  (".adapter", "on_adapter_startup"),
    "on_adapter_shutdown": (".adapter", "on_adapter_shutdown"),
}


def __getattr__(name: str):
    spec = _LAZY_IMPORTS.get(name)
    if spec is not None:
        module_path, attr = spec
        import importlib
        try:
            mod = importlib.import_module(module_path, __package__)
        except ImportError:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
        val = getattr(mod, attr)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # 版本和错误码
    "SDK_VERSION",
    "ErrorCode",
    
    # 响应辅助函数
    "ok",
    "fail",
    
    # 装饰器
    "neko_plugin",
    "plugin_entry",
    "lifecycle",
    "on_event",
    "message",
    "timer_interval",
    "custom_event",
    "plugin",
    
    # Hook 装饰器
    "hook",
    "before_entry",
    "after_entry",
    "around_entry",
    "replace_entry",
    "HookMeta",
    "HookHandler",
    "HookTiming",
    "HookExecutorMixin",
    "HOOK_META_ATTR",
    
    # 调用链追踪
    "CallChain",
    "AsyncCallChain",
    "CircularCallError",
    "CallChainTooDeepError",
    "get_call_chain",
    "get_call_depth",
    "is_in_call_chain",
    
    # 基类和元数据
    "NekoPluginBase",
    "PluginRouter",
    "PluginRouterError",
    "PluginMeta",
    "PluginConfig",
    "Plugins",
    "EventMeta",
    "EventHandler",
    "SystemInfo",
    "MemoryClient",
    
    # 状态持久化
    "PluginStatePersistence",
    "StatePersistence",
    "EXTENDED_TYPES",
    
    # 类型定义和常量
    "PluginContextProtocol",
    "EntryKind",
    "PERSIST_ATTR",
    "CHECKPOINT_ATTR",
    "EVENT_META_ATTR",
    
    # Adapter 相关
    "AdapterBase",
    "AdapterConfig",
    "AdapterContext",
    "AdapterMode",
    "AdapterMessage",
    "AdapterResponse",
    "AdapterGatewayCore",
    "ExternalEnvelope",
    "DefaultRequestNormalizer",
    "DefaultPolicyEngine",
    "DefaultRouteEngine",
    "DefaultResponseSerializer",
    "CallablePluginInvoker",
    "GatewayAction",
    "GatewayRequest",
    "GatewayResponse",
    "GatewayError",
    "GatewayErrorException",
    "RouteDecision",
    "RouteMode",
    "LoggerLike",
    "TransportAdapter",
    "RequestNormalizer",
    "PolicyEngine",
    "RouteEngine",
    "PluginInvoker",
    "ResponseSerializer",
    "Protocol",
    "on_adapter_event",
    "on_adapter_startup",
    "on_adapter_shutdown",
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
