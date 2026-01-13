"""
插件基类模块

提供插件开发的基础类和接口。
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, Any, List
from .events import EventHandler, EventMeta, EVENT_META_ATTR
from .config import PluginConfig
from .plugins import Plugins
from .version import SDK_VERSION
from plugin.settings import (
    NEKO_PLUGIN_META_ATTR, 
    NEKO_PLUGIN_TAG,
    PLUGIN_LOG_LEVEL,
    PLUGIN_LOG_MAX_BYTES,
    PLUGIN_LOG_BACKUP_COUNT,
    PLUGIN_LOG_MAX_FILES,
)

if TYPE_CHECKING:
    from plugin.core.context import PluginContext


@dataclass
class PluginMeta:
    """插件元数据（SDK 内部使用）"""
    id: str
    name: str
    version: str = "0.1.0"
    sdk_version: str = SDK_VERSION
    sdk_recommended: Optional[str] = None
    sdk_supported: Optional[str] = None
    sdk_untested: Optional[str] = None
    sdk_conflicts: List[str] = field(default_factory=list)
    description: str = ""


class NekoPluginBase:
    """插件都继承这个基类."""
    
    def __init__(self, ctx: "PluginContext"):
        """
        Initialize the plugin base with the given plugin context and construct common plugin helpers.
        
        Parameters:
            ctx (PluginContext): The plugin's execution context providing runtime services and metadata.
                Stored on the instance as `self.ctx`; `plugin_id` is derived from it (defaults to "unknown")
                and used to construct `self.config` (PluginConfig) and `self.plugins` (Plugins).
        """
        self.ctx: "PluginContext" = ctx
        self._plugin_id = getattr(ctx, "plugin_id", "unknown")
        self.config = PluginConfig(ctx)
        self.plugins = Plugins(ctx)

    def get_input_schema(self) -> Dict[str, Any]:
        """
        Retrieve the plugin input schema from the instance.
        
        Returns:
            A dictionary of the input schema defined on the instance (`self.input_schema`) or an empty dict if no schema is set.
        """
        schema = getattr(self, "input_schema", None)
        return schema or {}

    def collect_entries(self) -> Dict[str, EventHandler]:
        """
        默认实现：扫描自身方法，把带入口标记的都收集起来。
        （注意：这是插件内部调用的，不是服务器在外面乱扫全模块）
        """
        entries: Dict[str, EventHandler] = {}
        for attr_name in dir(self):
            value = getattr(self, attr_name)
            if not callable(value):
                continue
            meta: EventMeta | None = getattr(value, EVENT_META_ATTR, None)
            if meta:
                if meta.id in entries:
                    logger = getattr(self, "ctx", None)
                    if logger:
                        logger = getattr(logger, "logger", None)
                    if logger:
                        logger.warning(f"Duplicate entry id '{meta.id}' in plugin {self._plugin_id}")
                entries[meta.id] = EventHandler(meta=meta, handler=value)
        return entries
    
    def report_status(self, status: Dict[str, Any]) -> None:
        """
        Report plugin status to the host.
        
        If the plugin context supports status updates, sends the provided status payload to the host. If the context does not support updates, attempts to issue a warning via the context's logger.
        
        Parameters:
            status (Dict[str, Any]): Arbitrary status data to report to the host.
        """
        if hasattr(self.ctx, "update_status"):
            # 这里只传原始 status，由 Context 负责打包成队列消息
            self.ctx.update_status(status)
        else:
            logger = getattr(self.ctx, "logger", None)
            if logger:
                logger.warning(
                    f"Plugin {self._plugin_id} tried to report status but ctx.update_status is missing."
                )
    
    def enable_file_logging(
        self,
        log_level: Optional[str] = None,
        max_bytes: Optional[int] = None,
        backup_count: Optional[int] = None,
        max_files: Optional[int] = None,
    ) -> Any:
        """
        Enable per-plugin file logging and console output, creating rotated log files under the plugin's logs directory.
        
        Parameters:
            log_level (Optional[str]): Log level as a string (e.g., "DEBUG", "INFO", "WARNING", "ERROR"). Defaults to PLUGIN_LOG_LEVEL when None.
            max_bytes (Optional[int]): Maximum size in bytes of a single log file before rotation. Defaults to PLUGIN_LOG_MAX_BYTES when None.
            backup_count (Optional[int]): Number of backup files to keep per rotation. Defaults to PLUGIN_LOG_BACKUP_COUNT when None.
            max_files (Optional[int]): Maximum total number of retained log files; older files beyond this limit will be removed. Defaults to PLUGIN_LOG_MAX_FILES when None.
        
        Returns:
            Any: A configured loguru logger instance with both file and console handlers attached.
        """
        # 延迟导入，避免循环依赖
        from .logger import enable_plugin_file_logging
        
        # 获取插件目录（config_path的父目录）
        config_path = getattr(self.ctx, "config_path", None)
        plugin_dir = config_path.parent if config_path else Path.cwd()
        
        # 使用配置中的默认值
        log_level = log_level if log_level is not None else PLUGIN_LOG_LEVEL
        max_bytes = max_bytes if max_bytes is not None else PLUGIN_LOG_MAX_BYTES
        backup_count = backup_count if backup_count is not None else PLUGIN_LOG_BACKUP_COUNT
        max_files = max_files if max_files is not None else PLUGIN_LOG_MAX_FILES
        
        # 启用文件日志
        file_logger = enable_plugin_file_logging(
            plugin_id=self._plugin_id,
            plugin_dir=plugin_dir,
            logger=getattr(self.ctx, "logger", None),
            log_level=log_level,
            max_bytes=max_bytes,
            backup_count=backup_count,
            max_files=max_files,
        )
        
        # 将file_logger保存到实例，方便访问
        self.file_logger = file_logger
        
        return file_logger