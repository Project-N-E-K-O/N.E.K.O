"""
插件 Logger 工具模块（stdlib 薄壳）

╔══════════════════════════════════════════════════════════════════════════╗
║                        ⚠⚠⚠  警  告  ⚠⚠⚠                                ║
║                                                                          ║
║   本模块过去版本：                                                      ║
║     - 用 Path.cwd() 推日志目录 → AppImage 打包后写入只读 squashfs 直接 ║
║       崩溃；                                                             ║
║     - 用 loguru 自创 sink → 和本体 RobustLoggerConfig 双轨制；         ║
║     - 一堆 fallback 把 PermissionError 吞成 silent fail。              ║
║                                                                          ║
║   现在：所有插件日志统一走 utils.logger_config.RobustLoggerConfig，    ║
║   通过 setup_logging(service_name=f"Plugin_{plugin_id}") 落到          ║
║   我的文档/N.E.K.O/logs/N.E.K.O_Plugin_{plugin_id}_*.log。            ║
║                                                                          ║
║   再有人在本模块（或仓库任何位置）：                                    ║
║     - 重新引入 loguru                                                   ║
║     - 用 cwd / __file__.parent 算日志目录                              ║
║     - 自创 FileHandler 绕过 RobustLoggerConfig                         ║
║   按维护者口径：**就把谁杀了。** lint 守门见 scripts/check_no_loguru.py。║
╚══════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Optional

from plugin.logging_config import PluginLoggerAdapter, get_logger


class PluginFileLogger:
    """插件文件日志管理器。

    历史接口保留。底层全部走 utils.logger_config.RobustLoggerConfig，
    日志目录由本体决定（默认 我的文档/N.E.K.O/logs/），不再由插件控制。
    """

    DEFAULT_LOG_LEVEL = "INFO"
    DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 兼容性常量；实际由 RobustLoggerConfig 决定
    DEFAULT_BACKUP_COUNT = 10
    DEFAULT_MAX_FILES = 20

    def __init__(
        self,
        plugin_id: str,
        plugin_dir: Path,
        log_level: str = DEFAULT_LOG_LEVEL,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
        max_files: int = DEFAULT_MAX_FILES,
    ):
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
        self._logger: Optional[PluginLoggerAdapter] = None

    def setup(self, logger_instance: Optional[Any] = None) -> PluginLoggerAdapter:
        """配置 logger（幂等）。

        若 logger_instance 由 host 传入，直接返回它（host 已经配置过文件 sink，
        本体 RobustLoggerConfig 也会同时落盘）；否则创建一个绑定 plugin_id 的
        adapter。
        """
        if self._logger is not None:
            return self._logger

        if isinstance(logger_instance, PluginLoggerAdapter):
            self._logger = logger_instance
        elif logger_instance is not None:
            # 外部传入的 stdlib logger / 其他 adapter — 用 plugin namespace 包一层
            self._logger = get_logger(f"plugin.{self.plugin_id}").bind(plugin_id=self.plugin_id)
        else:
            self._logger = get_logger(f"plugin.{self.plugin_id}").bind(plugin_id=self.plugin_id)
        return self._logger

    def get_logger(self) -> Optional[PluginLoggerAdapter]:
        return self._logger

    def get_log_file_path(self) -> Path:
        """返回当前进程的日志文件路径（由本体 RobustLoggerConfig 决定）。"""
        try:
            from utils.logger_config import RobustLoggerConfig
            cfg = RobustLoggerConfig(
                service_name=f"Plugin_{self.plugin_id}",
                log_subdir="plugin",
            )
            return Path(cfg.get_log_file_path())
        except Exception:
            return self.plugin_dir / "logs" / f"{self.plugin_id}.log"

    def get_log_directory(self) -> Path:
        try:
            from utils.logger_config import RobustLoggerConfig
            cfg = RobustLoggerConfig(
                service_name=f"Plugin_{self.plugin_id}",
                log_subdir="plugin",
            )
            return Path(cfg.get_log_directory_path())
        except Exception:
            return self.plugin_dir / "logs"

    def cleanup(self) -> None:
        self._logger = None


def enable_plugin_file_logging(
    plugin_id: str,
    plugin_dir: Path,
    logger: Optional[Any] = None,
    log_level: str = PluginFileLogger.DEFAULT_LOG_LEVEL,
    max_bytes: int = PluginFileLogger.DEFAULT_MAX_BYTES,
    backup_count: int = PluginFileLogger.DEFAULT_BACKUP_COUNT,
    max_files: int = PluginFileLogger.DEFAULT_MAX_FILES,
) -> PluginLoggerAdapter:
    """便捷函数：为插件返回一个绑定 plugin_id 的 logger。

    日志会自动落到本体的 plugin 日志文件（由 RobustLoggerConfig 管理路径）。
    """
    file_logger = PluginFileLogger(
        plugin_id=plugin_id,
        plugin_dir=plugin_dir,
        log_level=log_level,
        max_bytes=max_bytes,
        backup_count=backup_count,
        max_files=max_files,
    )
    return file_logger.setup(logger_instance=logger)


def plugin_file_logger(
    log_level: str = PluginFileLogger.DEFAULT_LOG_LEVEL,
    max_bytes: int = PluginFileLogger.DEFAULT_MAX_BYTES,
    backup_count: int = PluginFileLogger.DEFAULT_BACKUP_COUNT,
    max_files: int = PluginFileLogger.DEFAULT_MAX_FILES,
):
    """装饰器：给插件类自动挂 file_logger 属性。

    日志落盘位置由本体 RobustLoggerConfig 决定，插件无需关心。
    """
    def decorator(cls):
        original_init = cls.__init__

        @functools.wraps(original_init)
        def new_init(self, ctx):
            original_init(self, ctx)
            plugin_id = getattr(self, '_plugin_id', getattr(ctx, 'plugin_id', 'unknown'))
            config_path = getattr(ctx, 'config_path', None)
            plugin_dir = config_path.parent if config_path else Path.cwd()
            self.file_logger = enable_plugin_file_logging(
                plugin_id=plugin_id,
                plugin_dir=plugin_dir,
                logger=getattr(ctx, 'logger', None),
                log_level=log_level,
                max_bytes=max_bytes,
                backup_count=backup_count,
                max_files=max_files,
            )

        cls.__init__ = new_init
        return cls

    return decorator


__all__ = [
    'PluginFileLogger',
    'enable_plugin_file_logging',
    'plugin_file_logger',
]
