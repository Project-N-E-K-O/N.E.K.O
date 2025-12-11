"""
插件Logger工具模块

为插件提供独立的文件日志功能，支持：
- 自动创建插件专属的日志目录
- 日志文件轮转（按大小）
- 自动清理旧日志文件（按数量）
- 可配置的日志级别和格式
"""
import functools
import logging
import sys
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler
from logging import StreamHandler
from datetime import datetime


class PluginFileLogger:
    """
    插件文件日志管理器
    
    为每个插件提供独立的文件日志功能，自动管理日志文件数量。
    日志会同时输出到文件和控制台（终端）。
    """
    
    # 默认配置
    DEFAULT_LOG_LEVEL = logging.INFO
    DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5MB per log file
    DEFAULT_BACKUP_COUNT = 10  # 保留10个备份文件（总共11个文件）
    DEFAULT_MAX_FILES = 20  # 最多保留20个日志文件（包括当前和备份）
    
    def __init__(
        self,
        plugin_id: str,
        plugin_dir: Path,
        log_level: int = DEFAULT_LOG_LEVEL,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
        max_files: int = DEFAULT_MAX_FILES,
        log_format: Optional[str] = None,
    ):
        """
        初始化插件文件日志管理器
        
        Args:
            plugin_id: 插件ID
            plugin_dir: 插件目录路径（通常是plugin.toml所在目录）
            log_level: 日志级别，默认INFO
            max_bytes: 单个日志文件最大大小（字节），默认5MB
            backup_count: 保留的备份文件数量，默认10个
            max_files: 最多保留的日志文件总数（包括当前和备份），默认20个
            log_format: 日志格式字符串，如果为None则使用默认格式
        """
        if max_files < 1:
            raise ValueError("max_files must be at least 1")
        if backup_count < 0:
            raise ValueError("backup_count must be non-negative")
        if max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.log_level = log_level
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.max_files = max_files
        
        # 日志目录：插件目录下的logs子目录
        self.log_dir = self.plugin_dir / "logs"
        
        # 确保日志目录存在
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 日志文件名：使用插件ID、日期和时间
        datetime_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_filename = f"{plugin_id}_{datetime_str}.log"
        self.log_file = self.log_dir / self.log_filename
        
        # 日志格式
        if log_format is None:
            log_format = (
                '%(asctime)s - [%(name)s] - %(levelname)s - '
                '%(filename)s:%(lineno)d - %(message)s'
            )
        self.log_format = log_format
        self.date_format = '%Y-%m-%d %H:%M:%S'
        
        # Logger实例（延迟创建）
        self._logger: Optional[logging.Logger] = None
        self._file_handler: Optional[RotatingFileHandler] = None
        self._console_handler: Optional[StreamHandler] = None
        
        # 清理旧日志
        self._cleanup_old_logs()
    
    def _cleanup_old_logs(self) -> None:
        """
        清理旧的日志文件，保持日志文件数量在限制内
        
        策略：
        1. 获取所有日志文件（按修改时间排序）
        2. 如果文件数量超过max_files，删除最旧的文件
        """
        try:
            # 获取所有日志文件（匹配插件ID的日志文件）
            log_files = list(self.log_dir.glob(f"{self.plugin_id}_*.log*"))
            
            if len(log_files) <= self.max_files:
                return
            
            # 按修改时间排序（最旧的在前）
            log_files.sort(key=lambda f: f.stat().st_mtime)
            
            # 删除最旧的文件
            files_to_delete = log_files[:-self.max_files]
            for log_file in files_to_delete:
                try:
                    log_file.unlink()
                    if self._logger:
                        self._logger.debug(f"Deleted old log file: {log_file.name}")
                except (OSError, PermissionError) as e:
                    # 如果logger还没创建，使用print
                    print(f"Warning: Failed to delete old log file {log_file}: {e}", file=sys.stderr)
        except (OSError, PermissionError) as e:
            print(f"Warning: Failed to cleanup old logs: {e}", file=sys.stderr)
    
    def setup(self, logger: Optional[logging.Logger] = None) -> logging.Logger:
        """
        设置文件日志handler和控制台handler
        
        日志会同时输出到：
        - 文件：插件的logs目录下的日志文件
        - 控制台：标准输出（终端）
        
        Args:
            logger: 要配置的logger实例，如果为None则创建新的logger
            
        Returns:
            配置好的logger实例（已添加文件handler和控制台handler）
        """
        # 如果已经设置过，直接返回
        if self._logger is not None and self._file_handler is not None:
            return self._logger
        
        # 获取或创建logger
        if logger is None:
            logger_name = f"plugin.{self.plugin_id}.file"
            self._logger = logging.getLogger(logger_name)
        else:
            self._logger = logger
        
        # 设置日志级别
        self._logger.setLevel(self.log_level)
        
        # 检查是否已经添加了文件handler（避免重复添加）
        file_handler_exists = False
        console_handler_exists = False
        for handler in self._logger.handlers:
            if isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(self.log_file):
                self._file_handler = handler
                file_handler_exists = True
            elif isinstance(handler, StreamHandler) and handler.stream == sys.stdout:
                console_handler_exists = True
                self._console_handler = handler
        
        # 如果两个handler都已存在，直接返回
        if file_handler_exists and console_handler_exists:
            return self._logger
        
        # 创建日志格式器
        formatter = logging.Formatter(self.log_format, self.date_format)
        
        # 添加控制台handler（如果不存在）
        if not console_handler_exists:
            try:
                console_handler = StreamHandler(sys.stdout)
                console_handler.setLevel(self.log_level)
                console_handler.setFormatter(formatter)
                self._logger.addHandler(console_handler)
                self._console_handler = console_handler
            except Exception as e:
                print(f"Warning: Failed to add console handler for plugin {self.plugin_id}: {e}", file=sys.stderr)
        
        # 创建文件handler（带轮转，如果不存在）
        if not file_handler_exists:
            try:
                self._file_handler = RotatingFileHandler(
                    self.log_file,
                    maxBytes=self.max_bytes,
                    backupCount=self.backup_count,
                    encoding='utf-8'
                )
                self._file_handler.setLevel(self.log_level)
                self._file_handler.setFormatter(formatter)
                self._logger.addHandler(self._file_handler)
            except Exception as e:
                print(f"Error: Failed to setup file logger for plugin {self.plugin_id}: {e}", file=sys.stderr)
                # 即使文件handler失败，也返回logger（可能还有控制台handler）
        
        # 记录初始化信息（仅输出到文件，不输出到控制台）
        if self._file_handler:
            # 直接写入文件handler，避免操作 handler 列表的竞态
            init_msg = (
                f"Plugin file logger initialized: {self.log_file}, "
                f"level={logging.getLevelName(self.log_level)}, "
                f"max_size={self.max_bytes / 1024 / 1024:.1f}MB, "
                f"backup_count={self.backup_count}, max_files={self.max_files}"
            )
            record = logging.LogRecord(
                name=self._logger.name,
                level=logging.INFO,
                pathname=__file__,
                lineno=0,
                msg=init_msg,
                args=(),
                exc_info=None,
            )
            self._file_handler.emit(record)
        
        return self._logger
    
    def get_logger(self) -> Optional[logging.Logger]:
        """
        获取配置好的logger实例
        
        Returns:
            logger实例，如果还未设置则返回None
        """
        return self._logger
    
    def get_log_file_path(self) -> Path:
        """
        获取当前日志文件路径
        
        Returns:
            日志文件路径
        """
        return self.log_file
    
    def get_log_directory(self) -> Path:
        """
        获取日志目录路径
        
        Returns:
            日志目录路径
        """
        return self.log_dir
    
    def cleanup(self) -> None:
        """
        清理资源（关闭handler等）
        """
        if self._file_handler:
            try:
                self._file_handler.close()
                if self._logger:
                    self._logger.removeHandler(self._file_handler)
            except Exception as e:
                # 清理失败不影响主流程，但记录一下方便调试
                if self._logger:
                    self._logger.debug(f"Failed to cleanup file handler: {e}")
            self._file_handler = None
        if self._console_handler and self._logger:
            try:
                self._logger.removeHandler(self._console_handler)
            except Exception as e:
                if self._logger:
                    self._logger.debug(f"Failed to remove console handler: {e}")
            self._console_handler = None


def enable_plugin_file_logging(
    plugin_id: str,
    plugin_dir: Path,
    logger: Optional[logging.Logger] = None,
    log_level: int = PluginFileLogger.DEFAULT_LOG_LEVEL,
    max_bytes: int = PluginFileLogger.DEFAULT_MAX_BYTES,
    backup_count: int = PluginFileLogger.DEFAULT_BACKUP_COUNT,
    max_files: int = PluginFileLogger.DEFAULT_MAX_FILES,
) -> logging.Logger:
    """
    便捷函数：为插件启用文件日志
    
    日志会同时输出到文件和控制台（终端）。
    
    Args:
        plugin_id: 插件ID
        plugin_dir: 插件目录路径（通常是plugin.toml所在目录）
        logger: 要配置的logger实例，如果为None则创建新的logger
        log_level: 日志级别，默认INFO
        max_bytes: 单个日志文件最大大小（字节），默认5MB
        backup_count: 保留的备份文件数量，默认10个
        max_files: 最多保留的日志文件总数，默认20个
        
    Returns:
        配置好的logger实例（已添加文件handler和控制台handler）
        
    使用示例:
        ```python
        from plugin.sdk.logger import enable_plugin_file_logging
        
        class MyPlugin(NekoPluginBase):
            def __init__(self, ctx):
                super().__init__(ctx)
                # 启用文件日志（同时输出到文件和控制台）
                self.file_logger = enable_plugin_file_logging(
                    plugin_id=self._plugin_id,
                    plugin_dir=ctx.config_path.parent,
                    logger=ctx.logger,
                    log_level=logging.DEBUG
                )
                # 使用file_logger记录日志，会同时显示在终端和保存到文件
                self.file_logger.info("Plugin initialized")
        ```
    """
    file_logger = PluginFileLogger(
        plugin_id=plugin_id,
        plugin_dir=plugin_dir,
        log_level=log_level,
        max_bytes=max_bytes,
        backup_count=backup_count,
        max_files=max_files,
    )
    return file_logger.setup(logger=logger)


def plugin_file_logger(
    log_level: int = PluginFileLogger.DEFAULT_LOG_LEVEL,
    max_bytes: int = PluginFileLogger.DEFAULT_MAX_BYTES,
    backup_count: int = PluginFileLogger.DEFAULT_BACKUP_COUNT,
    max_files: int = PluginFileLogger.DEFAULT_MAX_FILES,
):
    """
    装饰器：为插件类自动启用文件日志
    
    在插件初始化时自动设置文件日志，日志文件保存在插件的logs目录下。
    日志会同时输出到文件和控制台（终端）。
    
    Args:
        log_level: 日志级别，默认INFO
        max_bytes: 单个日志文件最大大小（字节），默认5MB
        backup_count: 保留的备份文件数量，默认10个
        max_files: 最多保留的日志文件总数，默认20个
        
    使用示例:
        ```python
        from plugin.sdk.logger import plugin_file_logger
        
        @plugin_file_logger(log_level=logging.DEBUG)
        class MyPlugin(NekoPluginBase):
            def __init__(self, ctx):
                super().__init__(ctx)
                # 文件日志已自动启用，可以通过 self.file_logger 访问
                # 日志会同时显示在终端和保存到文件
                self.file_logger.info("Plugin initialized")
        ```
    
    注意：
        装饰器会在插件实例上添加 `file_logger` 属性，指向配置好的logger。
        日志会同时输出到文件和控制台。
    """
    def decorator(cls):
        original_init = cls.__init__
        
        @functools.wraps(original_init)
        def new_init(self, ctx):
            # 调用原始初始化
            original_init(self, ctx)
            
            # 获取插件ID和目录
            plugin_id = getattr(self, '_plugin_id', getattr(ctx, 'plugin_id', 'unknown'))
            config_path = getattr(ctx, 'config_path', None)
            plugin_dir = config_path.parent if config_path else Path.cwd()
            
            # 启用文件日志
            file_logger = enable_plugin_file_logging(
                plugin_id=plugin_id,
                plugin_dir=plugin_dir,
                logger=getattr(ctx, 'logger', None),
                log_level=log_level,
                max_bytes=max_bytes,
                backup_count=backup_count,
                max_files=max_files,
            )
            
            # 将file_logger添加到实例
            self.file_logger = file_logger
            
            # 如果ctx中有logger，也更新它（可选）
            if hasattr(ctx, 'logger') and ctx.logger != file_logger:
                # 可以选择将file_logger的handler添加到ctx.logger
                # 或者保持两个logger独立
                pass
        
        cls.__init__ = new_init
        return cls
    
    return decorator


__all__ = [
    'PluginFileLogger',
    'enable_plugin_file_logging',
    'plugin_file_logger',
]

