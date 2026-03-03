from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from .types import PluginContextProtocol


class PluginCallError(RuntimeError):
    """插件调用错误
    
    当插件间通信失败时抛出此异常,例如:
    - 目标插件不存在
    - 事件ID无效
    - 调用超时
    - 参数格式错误
    """
    pass


def _parse_entry_ref(ref: str) -> Tuple[str, str]:
    # "plugin_id:entry_id"
    parts = ref.split(":")
    if len(parts) != 2:
        raise PluginCallError(f"Invalid entry ref '{ref}', expected 'plugin_id:entry_id'")
    plugin_id, entry_id = parts
    if not plugin_id or not entry_id:
        raise PluginCallError(f"Invalid entry ref '{ref}'")
    return plugin_id, entry_id


def _parse_event_ref(ref: str) -> Tuple[str, str, str]:
    # "plugin_id:event_type:event_id"
    parts = ref.split(":")
    if len(parts) != 3:
        raise PluginCallError(f"Invalid event ref '{ref}', expected 'plugin_id:event_type:event_id'")
    plugin_id, event_type, event_id = parts
    if not plugin_id or not event_type or not event_id:
        raise PluginCallError(f"Invalid event ref '{ref}'")
    return plugin_id, event_type, event_id


@dataclass
class Plugins:
    """插件管理器
    
    提供插件间通信和查询功能。通过self.plugins访问此类的实例。
    
    Attributes:
        ctx: 插件上下文
    
    Example:
        >>> # 在插件中使用
        >>> plugins_info = self.plugins.list()
        >>> result = self.plugins.call_entry("other_plugin:action", {"param": "value"})
    """
    ctx: "PluginContextProtocol"

    def list(self, filters: Optional[Dict[str, Any]] = None, *, timeout: float = 5.0) -> Dict[str, Any]:
        """查询插件列表
        
        获取系统中所有插件的信息,可以通过filters参数过滤。
        
        Args:
            filters: 过滤条件字典,例如 {"status": "running", "include_events": True}
            timeout: 超时时间(秒)
        
        Returns:
            包含插件列表的字典,格式为:
            {
                "plugins": [
                    {"plugin_id": "...", "name": "...", "status": "...", ...},
                    ...
                ]
            }
        
        Raises:
            PluginCallError: 如果ctx.query_plugins不可用
            TimeoutError: 如果查询超时
        
        Example:
            >>> plugins = self.plugins.list()
            >>> running_plugins = self.plugins.list(filters={"status": "running"})
        """
        if not hasattr(self.ctx, "query_plugins"):
            raise PluginCallError("ctx.query_plugins is not available")
        return self.ctx.query_plugins(filters or {}, timeout=timeout)

    def call_entry(self, ref: str, params: Dict[str, Any], *, timeout: float = 10.0) -> Any:
        """调用其他插件的entry
        
        通过"plugin_id:entry_id"格式的引用调用其他插件的plugin_entry。
        
        Args:
            ref: 插件entry引用,格式为"plugin_id:entry_id"
            params: 传递给entry的参数字典
            timeout: 超时时间(秒)
        
        Returns:
            目标entry的返回结果
        
        Raises:
            PluginCallError: 如果引用格式无效或调用失败
            TimeoutError: 如果调用超时
        
        Example:
            >>> result = self.plugins.call_entry(
            ...     "data_processor:process",
            ...     {"data": [1, 2, 3]}
            ... )
        """
        plugin_id, entry_id = _parse_entry_ref(ref)
        return self.call(plugin_id=plugin_id, event_type="plugin_entry", event_id=entry_id, params=params, timeout=timeout)

    def call_event(self, ref: str, params: Dict[str, Any], *, timeout: float = 10.0) -> Any:
        """调用其他插件的自定义事件
        
        通过"plugin_id:event_type:event_id"格式的引用调用其他插件的自定义事件。
        
        Args:
            ref: 事件引用,格式为"plugin_id:event_type:event_id"
            params: 传递给事件处理器的参数字典
            timeout: 超时时间(秒)
        
        Returns:
            事件处理器的返回结果
        
        Raises:
            PluginCallError: 如果引用格式无效或调用失败
            TimeoutError: 如果调用超时
        
        Example:
            >>> result = self.plugins.call_event(
            ...     "notifier:custom_event:send_notification",
            ...     {"message": "Hello"}
            ... )
        """
        plugin_id, event_type, event_id = _parse_event_ref(ref)
        return self.call(plugin_id=plugin_id, event_type=event_type, event_id=event_id, params=params, timeout=timeout)

    def call(self, *, plugin_id: str, event_type: str, event_id: str, params: Dict[str, Any], timeout: float = 10.0) -> Any:
        """通用插件事件调用方法
        
        底层方法,用于调用其他插件的任意事件。通常建议使用call_entry()或call_event()。
        
        Args:
            plugin_id: 目标插件ID
            event_type: 事件类型("plugin_entry", "lifecycle", "message", "timer"等)
            event_id: 事件ID
            params: 参数字典
            timeout: 超时时间(秒)
        
        Returns:
            事件处理器的返回结果
        
        Raises:
            PluginCallError: 如果ctx.trigger_plugin_event不可用
            RuntimeError: 如果调用失败
            TimeoutError: 如果调用超时
        
        Example:
            >>> result = self.plugins.call(
            ...     plugin_id="other_plugin",
            ...     event_type="plugin_entry",
            ...     event_id="action",
            ...     params={"param": "value"}
            ... )
        """
        if not hasattr(self.ctx, "trigger_plugin_event"):
            raise PluginCallError("ctx.trigger_plugin_event is not available")
        return self.ctx.trigger_plugin_event(
            target_plugin_id=plugin_id,
            event_type=event_type,
            event_id=event_id,
            params=params,
            timeout=timeout,
        )

    def require(self, plugin_id: str, *, timeout: float = 5.0) -> None:
        """检查必需的插件是否存在
        
        验证指定的插件是否已加载。如果插件不存在,抛出PluginCallError异常。
        用于声明插件依赖关系。
        
        Args:
            plugin_id: 必需的插件ID
            timeout: 超时时间(秒)
        
        Raises:
            PluginCallError: 如果必需的插件不存在
            TimeoutError: 如果查询超时
        
        Example:
            >>> def __init__(self, ctx):
            ...     super().__init__(ctx)
            ...     # 声明依赖
            ...     self.plugins.require("data_processor")
            ...     self.plugins.require("notifier")
        """
        info = self.list({"include_events": False}, timeout=timeout)
        plugins = info.get("plugins", []) if isinstance(info, dict) else []
        if not any(isinstance(p, dict) and p.get("plugin_id") == plugin_id for p in plugins):
            raise PluginCallError(f"Required plugin '{plugin_id}' not found")
