"""
插件系统异常定义

提供明确的异常类型，替代通用的 Exception 捕获。
"""


class PluginError(Exception):
    """插件系统基础异常"""
    pass


class PluginNotFoundError(PluginError):
    """插件未找到"""
    
    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id
        super().__init__(f"Plugin '{plugin_id}' not found")


class PluginNotRunningError(PluginError):
    """插件未运行"""
    
    def __init__(self, plugin_id: str, status: str = "unknown"):
        self.plugin_id = plugin_id
        self.status = status
        super().__init__(f"Plugin '{plugin_id}' is not running (status: {status})")


class PluginTimeoutError(PluginError):
    """插件执行超时"""
    
    def __init__(self, plugin_id: str, entry_id: str, timeout: float):
        self.plugin_id = plugin_id
        self.entry_id = entry_id
        self.timeout = timeout
        super().__init__(f"Plugin '{plugin_id}' entry '{entry_id}' timed out after {timeout}s")


class PluginExecutionError(PluginError):
    """插件执行错误"""
    
    def __init__(self, plugin_id: str, entry_id: str, error: str):
        self.plugin_id = plugin_id
        self.entry_id = entry_id
        self.error = error
        super().__init__(f"Plugin '{plugin_id}' entry '{entry_id}' execution failed: {error}")


class PluginCommunicationError(PluginError):
    """插件进程间通信错误"""
    
    def __init__(self, plugin_id: str, message: str):
        self.plugin_id = plugin_id
        super().__init__(f"Communication error with plugin '{plugin_id}': {message}")


class PluginLoadError(PluginError):
    """插件加载错误"""
    
    def __init__(self, plugin_id: str, reason: str):
        self.plugin_id = plugin_id
        self.reason = reason
        super().__init__(f"Failed to load plugin '{plugin_id}': {reason}")


class PluginImportError(PluginError):
    """插件导入错误"""
    
    def __init__(self, entry_point: str, reason: str):
        self.entry_point = entry_point
        self.reason = reason
        super().__init__(f"Failed to import plugin class '{entry_point}': {reason}")


class PluginLifecycleError(PluginError):
    """插件生命周期事件错误"""
    
    def __init__(self, plugin_id: str, event_type: str, reason: str):
        self.plugin_id = plugin_id
        self.event_type = event_type
        self.reason = reason
        super().__init__(f"Plugin '{plugin_id}' lifecycle event '{event_type}' failed: {reason}")


class PluginTimerError(PluginError):
    """插件定时任务错误"""
    
    def __init__(self, plugin_id: str, timer_id: str, reason: str):
        self.plugin_id = plugin_id
        self.timer_id = timer_id
        self.reason = reason
        super().__init__(f"Plugin '{plugin_id}' timer '{timer_id}' failed: {reason}")


class PluginEntryNotFoundError(PluginError):
    """插件入口未找到"""
    
    def __init__(self, plugin_id: str, entry_id: str):
        self.plugin_id = plugin_id
        self.entry_id = entry_id
        super().__init__(f"Entry '{entry_id}' not found in plugin '{plugin_id}'")


class PluginMetadataError(PluginError):
    """插件元数据错误"""
    
    def __init__(self, plugin_id: str, reason: str):
        self.plugin_id = plugin_id
        self.reason = reason
        super().__init__(f"Plugin '{plugin_id}' metadata error: {reason}")


class PluginQueueError(PluginError):
    """插件队列操作错误"""
    
    def __init__(self, operation: str, reason: str):
        self.operation = operation
        self.reason = reason
        super().__init__(f"Queue operation '{operation}' failed: {reason}")
