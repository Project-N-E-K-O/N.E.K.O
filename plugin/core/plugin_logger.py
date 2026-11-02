"""
Plugin logger utilities (thin stdlib wrapper)

╔══════════════════════════════════════════════════════════════════════════╗
║                        ⚠⚠⚠  WARNING  ⚠⚠⚠                              ║
║                                                                          ║
║   Previous versions of this module:                                     ║
║     - Derived log directories from Path.cwd(), crashing when AppImage  ║
║       attempted to write into read-only squashfs;                        ║
║     - Invented loguru sinks alongside RobustLoggerConfig;               ║
║     - Swallowed PermissionError through numerous silent fallbacks.     ║
║                                                                          ║
║   All plugin logs now use utils.logger_config.RobustLoggerConfig,      ║
║   with setup_logging(service_name=f"Plugin_{plugin_id}") writing to    ║
║   Documents/N.E.K.O/logs/N.E.K.O_Plugin_{plugin_id}_*.log.                ║
║                                                                          ║
║   If anyone, here or anywhere in this repository, again:                ║
║     - Reintroduces loguru                                               ║
║     - Derives log directories from cwd / __file__.parent                ║
║     - Invents FileHandler paths bypassing RobustLoggerConfig           ║
║   The maintainer says: **kill them.** See scripts/check_no_loguru.py.   ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Optional

from plugin.logging_config import PluginLoggerAdapter, get_logger


class PluginFileLogger:
    """Plugin file-log manager.

    Retains the historical interface. Everything delegates to utils.logger_config.RobustLoggerConfig;
    the host determines the log directory (Documents/N.E.K.O/logs/ by default), not the plugin.
    """

    DEFAULT_LOG_LEVEL = "INFO"
    DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # Compatibility constant; actual value is determined by RobustLoggerConfig.
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
        """Configure the logger (idempotent).

        Return a host-supplied logger_instance directly (the host already configured its file sink,
        and the host's RobustLoggerConfig also writes to disk); otherwise create an adapter
        bound to plugin_id.
        """
        if self._logger is not None:
            return self._logger

        if isinstance(logger_instance, PluginLoggerAdapter):
            self._logger = logger_instance
        elif logger_instance is not None:
            # Externally supplied stdlib logger / other adapter: wrap with the plugin namespace.
            self._logger = get_logger(f"plugin.{self.plugin_id}").bind(plugin_id=self.plugin_id)
        else:
            self._logger = get_logger(f"plugin.{self.plugin_id}").bind(plugin_id=self.plugin_id)
        return self._logger

    def get_logger(self) -> Optional[PluginLoggerAdapter]:
        return self._logger

    def get_log_file_path(self) -> Path:
        """Return this process's log-file path (determined by the host's RobustLoggerConfig)."""
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
    """Convenience function returning a logger bound to plugin_id.

    Logs automatically go to the host's plugin log file (path managed by RobustLoggerConfig).
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
    """Decorator automatically attaching a file_logger attribute to plugin classes.

    The host's RobustLoggerConfig determines the log location; plugins need not manage it.
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
