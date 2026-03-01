"""
插件Logger工具模块

为插件提供独立的文件日志功能，支持：
- 自动创建插件专属的日志目录
- 日志文件轮转（按大小）
- 自动清理旧日志文件（按数量）
- 可配置的日志级别和格式

现在使用loguru作为日志后端
"""
import functools
import sys
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

from loguru import logger


class PluginFileLogger:
    """
    插件文件日志管理器
    
    为每个插件提供独立的文件日志功能，自动管理日志文件数量。
    日志会同时输出到文件和控制台（终端）。
    """
    
    # 默认配置
    DEFAULT_LOG_LEVEL = "INFO"
    DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5MB per log file
    DEFAULT_BACKUP_COUNT = 10  # 保留10个备份文件（总共11个文件）
    DEFAULT_MAX_FILES = 20  # 最多保留20个日志文件（包括当前和备份）
    
    def __init__(
        self,
        plugin_id: str,
        plugin_dir: Path,
        log_level: str = DEFAULT_LOG_LEVEL,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
        max_files: int = DEFAULT_MAX_FILES,
    ):
        """
        初始化插件文件日志管理器（使用loguru）
        
        Args:
            plugin_id: 插件ID
            plugin_dir: 插件目录路径（通常是plugin.toml所在目录）
            log_level: 日志级别（字符串："DEBUG", "INFO", "WARNING", "ERROR"），默认"INFO"
            max_bytes: 单个日志文件最大大小（字节），默认5MB
            backup_count: 保留的备份文件数量，默认10个
            max_files: 最多保留的日志文件总数（包括当前和备份），默认20个
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
        
        # 日志目录：优先使用项目根目录下的 log/plugins/{plugin_id} 目录
        # 如果不可用，则使用插件目录下的logs子目录作为降级方案
        try:
            # 尝试使用项目根目录下的 log/plugins/{plugin_id} 目录
            project_root = Path.cwd()
            log_dir = project_root / "log" / "plugins" / plugin_id
            log_dir.mkdir(parents=True, exist_ok=True)
            # 测试目录是否可写
            test_file = log_dir / ".test_write"
            try:
                test_file.write_text("test")
                test_file.unlink()
                self.log_dir = log_dir
            except (OSError, PermissionError):
                # 如果不可写，使用降级方案
                self.log_dir = self.plugin_dir / "logs"
                self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # 如果出现任何异常，使用降级方案
            self.log_dir = self.plugin_dir / "logs"
            self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 日志文件名：使用插件ID、日期和时间
        datetime_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_filename = f"{plugin_id}_{datetime_str}.log"
        self.log_file = self.log_dir / self.log_filename
        
        # Logger实例（延迟创建）
        self._logger: Optional[Any] = None  # loguru.Logger
        
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
    
    def setup(self, logger: Optional[Any] = None) -> Any:
        """
        设置文件日志handler和控制台handler（使用loguru）
        
        日志会同时输出到：
        - 文件：插件的logs目录下的日志文件
        - 控制台：标准输出（终端）
        
        Args:
            logger: 要配置的logger实例，如果为None则创建新的logger
            
        Returns:
            配置好的loguru logger实例（已添加文件handler和控制台handler）
        """
        # 如果已经设置过，直接返回
        if self._logger is not None:
            return self._logger
        
        # 获取或创建logger
        should_add_console = False
        if logger is None:
            # 创建新的loguru logger（绑定插件ID）
            self._logger = logger.bind(plugin_id=self.plugin_id)
            should_add_console = True
        else:
            self._logger = logger
            # 如果提供了外部 logger（通常来自 Host），假设它已经配置了控制台输出
            # 我们不再添加控制台 handler，以避免重复日志（Proc-xxx 和 Plugin-xxx 重复）
            should_add_console = False
        
        # 添加控制台输出（仅在需要时）
        if should_add_console:
            self._logger.add(
                sys.stdout,
                format=(
                    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                    "<level>{level: <8}</level> | "
                    f"[Plugin-{self.plugin_id}] "
                    "<level>{message}</level>"
                ),
                level=self.log_level,
                colorize=True,
            )
        
        # 添加文件输出（带轮转）
        self._logger.add(
            str(self.log_file),
            format=(
                "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
                f"[Plugin-{self.plugin_id}] "
                "{message}"
            ),
            level=self.log_level,
            rotation=self.max_bytes,
            retention=self.backup_count,
            encoding="utf-8",
        )
        
        # 记录初始化信息
        self._logger.info(
            f"Plugin file logger initialized: {self.log_file}, "
            f"level={self.log_level}, "
            f"max_size={self.max_bytes / 1024 / 1024:.1f}MB, "
            f"backup_count={self.backup_count}, max_files={self.max_files}"
        )
        
        return self._logger
    
    def get_logger(self) -> Optional[Any]:
        """
        获取配置好的logger实例
        
        Returns:
            loguru logger实例，如果还未设置则返回None
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
        清理资源（loguru会自动管理handler，这里主要是重置logger引用）
        """
        # loguru会自动管理handler，不需要手动清理
        # 但我们可以重置logger引用
        self._logger = None


def enable_plugin_file_logging(
    plugin_id: str,
    plugin_dir: Path,
    logger: Optional[Any] = None,
    log_level: str = PluginFileLogger.DEFAULT_LOG_LEVEL,
    max_bytes: int = PluginFileLogger.DEFAULT_MAX_BYTES,
    backup_count: int = PluginFileLogger.DEFAULT_BACKUP_COUNT,
    max_files: int = PluginFileLogger.DEFAULT_MAX_FILES,
) -> Any:
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
        配置好的loguru logger实例（已添加文件handler和控制台handler）
        
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
                    log_level="DEBUG"
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
    log_level: str = PluginFileLogger.DEFAULT_LOG_LEVEL,
    max_bytes: int = PluginFileLogger.DEFAULT_MAX_BYTES,
    backup_count: int = PluginFileLogger.DEFAULT_BACKUP_COUNT,
    max_files: int = PluginFileLogger.DEFAULT_MAX_FILES,
):
    """
    装饰器：为插件类自动启用文件日志
    
    在插件初始化时自动设置文件日志，日志文件保存在插件的logs目录下。
    日志会同时输出到文件和控制台（终端）。
    
    Args:
        log_level: 日志级别（字符串："DEBUG", "INFO", "WARNING", "ERROR"），默认"INFO"
        max_bytes: 单个日志文件最大大小（字节），默认5MB
        backup_count: 保留的备份文件数量，默认10个
        max_files: 最多保留的日志文件总数，默认20个
        
    使用示例:
        ```python
        from plugin.sdk.logger import plugin_file_logger
        
        @plugin_file_logger(log_level="DEBUG")
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

