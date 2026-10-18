"""
Plugin-system exception definitions.

Provide explicit exception types instead of generic Exception handling.
"""


class PluginError(Exception):
    """Base plugin-system exception."""
    pass


class PluginNotFoundError(PluginError):
    """Plugin not found."""
    
    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id
        super().__init__(f"Plugin '{plugin_id}' not found")


class PluginNotRunningError(PluginError):
    """Plugin not running."""
    
    def __init__(self, plugin_id: str, status: str = "unknown"):
        self.plugin_id = plugin_id
        self.status = status
        super().__init__(f"Plugin '{plugin_id}' is not running (status: {status})")


class PluginTimeoutError(PluginError):
    """Plugin execution timed out."""
    
    def __init__(self, plugin_id: str, entry_id: str, timeout: float):
        self.plugin_id = plugin_id
        self.entry_id = entry_id
        self.timeout = timeout
        super().__init__(f"Plugin '{plugin_id}' entry '{entry_id}' timed out after {timeout}s")


class PluginExecutionError(PluginError):
    """Plugin execution error."""
    
    def __init__(self, plugin_id: str, entry_id: str, error: str):
        self.plugin_id = plugin_id
        self.entry_id = entry_id
        self.error = error
        super().__init__(f"Plugin '{plugin_id}' entry '{entry_id}' execution failed: {error}")


class PluginCommunicationError(PluginError):
    """Plugin interprocess communication error."""
    
    def __init__(self, plugin_id: str, message: str):
        self.plugin_id = plugin_id
        super().__init__(f"Communication error with plugin '{plugin_id}': {message}")


class PluginLoadError(PluginError):
    """Plugin loading error."""
    
    def __init__(self, plugin_id: str, reason: str):
        self.plugin_id = plugin_id
        self.reason = reason
        super().__init__(f"Failed to load plugin '{plugin_id}': {reason}")


class PluginImportError(PluginError):
    """Plugin import error."""
    
    def __init__(self, entry_point: str, reason: str):
        self.entry_point = entry_point
        self.reason = reason
        super().__init__(f"Failed to import plugin class '{entry_point}': {reason}")


class PluginLifecycleError(PluginError):
    """Plugin lifecycle event error."""
    
    def __init__(self, plugin_id: str, event_type: str, reason: str):
        self.plugin_id = plugin_id
        self.event_type = event_type
        self.reason = reason
        super().__init__(f"Plugin '{plugin_id}' lifecycle event '{event_type}' failed: {reason}")


class PluginTimerError(PluginError):
    """Plugin scheduled-task error."""
    
    def __init__(self, plugin_id: str, timer_id: str, reason: str):
        self.plugin_id = plugin_id
        self.timer_id = timer_id
        self.reason = reason
        super().__init__(f"Plugin '{plugin_id}' timer '{timer_id}' failed: {reason}")


class PluginEntryNotFoundError(PluginError):
    """Plugin entry point not found."""
    
    def __init__(self, plugin_id: str, entry_id: str):
        self.plugin_id = plugin_id
        self.entry_id = entry_id
        super().__init__(f"Entry '{entry_id}' not found in plugin '{plugin_id}'")


class PluginMetadataError(PluginError):
    """Plugin metadata error."""
    
    def __init__(self, plugin_id: str, reason: str):
        self.plugin_id = plugin_id
        self.reason = reason
        super().__init__(f"Plugin '{plugin_id}' metadata error: {reason}")


class PluginQueueError(PluginError):
    """Plugin queue-operation error."""
    
    def __init__(self, operation: str, reason: str):
        self.operation = operation
        self.reason = reason
        super().__init__(f"Queue operation '{operation}' failed: {reason}")
