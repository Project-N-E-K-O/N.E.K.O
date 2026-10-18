# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Robust logging configuration module
For exe-packaged applications, supporting:
- automatic selection of a suitable log directory (user data directory)
- log rotation (by size and time)
- automatic cleanup of old logs
- degradation strategy (fallbacks when writing is impossible)
- cross-platform support

╔══════════════════════════════════════════════════════════════════════════╗
║                        ⚠⚠⚠  WARNING  ⚠⚠⚠                                ║
║                                                                          ║
║   This module is the ONLY logging backend allowed in this repo. Every    ║
║   Python process (main_*, agent_*, memory_*, user_plugin_server, each    ║
║   Plugin subprocess) must go through setup_logging(service_name=...).    ║
║                                                                          ║
║   Strictly forbidden:                                                    ║
║     1. introducing third-party logging libs like loguru / structlog /    ║
║        logbook;                                                          ║
║     2. computing the log dir from cwd / __file__.parent (AppImage        ║
║        squashfs is read-only);                                           ║
║     3. creating your own FileHandler to bypass RobustLoggerConfig;       ║
║     4. writing plugin logs anywhere else.                                ║
║                                                                          ║
║   Whoever messes this up again will — in the maintainer's words — be     ║
║   killed.                                                                ║
║   Lint gate: scripts/check_no_loguru.py (CI: .github/workflows/          ║
║   analyze.yml).                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta

from config import APP_NAME

NEKO_STORAGE_SELECTED_ROOT_ENV = "NEKO_STORAGE_SELECTED_ROOT"


def _get_application_root() -> Path:
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def _get_writable_application_directory() -> Path:
    """Return a writable path suitable as the base directory for log files."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _get_application_root()


def _get_selected_storage_root_from_env() -> Path | None:
    raw_root = str(os.environ.get(NEKO_STORAGE_SELECTED_ROOT_ENV) or "").strip()
    if not raw_root:
        return None

    try:
        selected_root = Path(raw_root).expanduser()
    except Exception:
        return None

    if not selected_root.is_absolute():
        return None
    return selected_root


class RobustLoggerConfig:
    """Robust logging configuration class"""
    
    # Default configuration.
    DEFAULT_LOG_LEVEL = logging.INFO
    DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10MB per log file
    DEFAULT_BACKUP_COUNT = 5  # Keep 5 backup files
    DEFAULT_LOG_RETENTION_DAYS = 30  # Keep logs for 30 days
    
    def __init__(self, app_name=None, service_name=None, log_level=None, max_bytes=None,
                 backup_count=None, retention_days=None, log_subdir=None):
        """
        Initialize logging configuration

        Args:
            app_name: application name, used to create the log directory; defaults to APP_NAME from config
            service_name: service name, distinguishing log files of different services (e.g. Main, Memory, Agent)
            log_level: log level
            max_bytes: maximum size of a single log file
            backup_count: number of backup files to keep
            retention_days: days to keep logs
            log_subdir: which subdirectory under the base dir logs land in (e.g. "plugin").
                Default None = write directly to the base dir ``<docs>/N.E.K.O/logs/``.
                Passing ``"plugin"`` routes logs to ``<docs>/N.E.K.O/logs/plugin/``, used
                to tuck the many plugin subprocess logs out of the top level into one
                subdirectory, keeping them apart from host-process logs like
                PluginServer / Main / Memory / Agent.
        """
        self.app_name = app_name if app_name is not None else APP_NAME
        self.service_name = service_name  # Service name distinguishes filenames.
        self.log_level = log_level or self.DEFAULT_LOG_LEVEL
        self.max_bytes = max_bytes or self.DEFAULT_MAX_BYTES
        self.backup_count = backup_count or self.DEFAULT_BACKUP_COUNT
        self.retention_days = retention_days or self.DEFAULT_LOG_RETENTION_DAYS
        self.log_subdir = log_subdir

        # Get the base log directory, then route to the log_subdir subdirectory.
        self.log_dir = self._get_log_directory()
        if log_subdir:
            # Reject caller paths containing "/" to prevent accidental escape from the base directory.
            safe = str(log_subdir).strip().strip("/\\")
            if safe:
                self.log_dir = self.log_dir / safe
        
        # Log filename includes service_name when present; otherwise use only app_name.
        if self.service_name:
            log_filename = f"{self.app_name}_{self.service_name}_{datetime.now().strftime('%Y%m%d')}.log"
        else:
            log_filename = f"{self.app_name}_{datetime.now().strftime('%Y%m%d')}.log"
        self.log_file = self.log_dir / log_filename
        
        # Ensure the log directory exists.
        self._ensure_log_directory()
        
        # Clean up old logs.
        self._cleanup_old_logs()
    
    def _get_log_directory(self):
        """
        Get a suitable log directory
        Priority:
        1. selected runtime storage directory/logs (injected by the launcher via environment variables)
        2. user documents directory/{APP_NAME}/logs (compatible with old versions and direct runs)
        3. application directory/logs
        4. user data directory (AppData etc.)
        5. user home directory
        6. temp directory (last-resort fallback)
        
        Returns:
            Path: log directory path
        """
        # Attempt 1: current storage root. Do not migrate old logs; new logs follow the new root.
        try:
            selected_root = _get_selected_storage_root_from_env()
            if selected_root is not None:
                log_dir = selected_root / "logs"
                if self._test_directory_writable(log_dir):
                    return log_dir
        except Exception as e:
            print(f"Warning: Failed to use selected storage log directory: {e}", file=sys.stderr)

        # Attempt 2: user Documents directory (compatible with older versions and direct non-launcher runs).
        try:
            docs_dir = self._get_documents_directory()
            # Use the configured application-name directory.
            log_dir = docs_dir / self.app_name / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use Documents directory: {e}", file=sys.stderr)
        
        # Attempt 2: application directory.
        try:
            app_dir = _get_writable_application_directory()
            log_dir = app_dir / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use application directory: {e}", file=sys.stderr)
        
        # Attempt 3: system user-data directory.
        try:
            if sys.platform == "win32":
                # Windows: %APPDATA%\AppName\logs
                base_dir = os.getenv('APPDATA')
                if base_dir:
                    log_dir = Path(base_dir) / self.app_name / "logs"
                    if self._test_directory_writable(log_dir):
                        return log_dir
            elif sys.platform == "darwin":
                # macOS: ~/Library/Application Support/AppName/logs
                base_dir = Path.home() / "Library" / "Application Support"
                log_dir = base_dir / self.app_name / "logs"
                if self._test_directory_writable(log_dir):
                    return log_dir
            else:
                # Linux: ~/.local/share/AppName/logs
                xdg_data_home = os.getenv('XDG_DATA_HOME')
                if xdg_data_home:
                    log_dir = Path(xdg_data_home) / self.app_name / "logs"
                else:
                    log_dir = Path.home() / ".local" / "share" / self.app_name / "logs"
                if self._test_directory_writable(log_dir):
                    return log_dir
        except Exception as e:
            print(f"Warning: Failed to get system data directory: {e}", file=sys.stderr)
        
        # Attempt 4: user home directory.
        try:
            log_dir = Path.home() / f".{self.app_name}" / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use home directory: {e}", file=sys.stderr)
        
        # Attempt 5: temporary directory (last-resort fallback).
        try:
            import tempfile
            log_dir = Path(tempfile.gettempdir()) / self.app_name / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use temp directory: {e}", file=sys.stderr)
        
        # If every method fails, return the current directory.
        print("Warning: All log directory attempts failed, using application directory", file=sys.stderr)
        return _get_writable_application_directory() / "logs"
    
    def _get_documents_directory(self):
        """Get the system's user documents directory (via system APIs)"""
        if sys.platform == "win32":
            # Windows: use the system API to find the actual Documents path.
            try:
                import ctypes
                from ctypes import windll, wintypes
                
                # Use SHGetFolderPath to get the Documents path.
                CSIDL_PERSONAL = 5  # My Documents
                SHGFP_TYPE_CURRENT = 0
                
                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
                docs_dir = Path(buf.value)
                
                if docs_dir.exists():
                    return docs_dir
            except Exception as e:
                print(f"Warning: Failed to get Documents path via API: {e}", file=sys.stderr)
            
            # Fallback: try reading the registry.
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                )
                docs_dir = Path(winreg.QueryValueEx(key, "Personal")[0])
                winreg.CloseKey(key)
                
                # Expand environment variables.
                docs_dir = Path(os.path.expandvars(str(docs_dir)))
                if docs_dir.exists():
                    return docs_dir
            except Exception as e:
                print(f"Warning: Failed to get Documents path from registry: {e}", file=sys.stderr)
            
            # Final fallback.
            docs_dir = Path.home() / "Documents"
            if not docs_dir.exists():
                docs_dir = Path.home() / "文档"
            return docs_dir
        
        elif sys.platform == "darwin":
            # macOS
            return Path.home() / "Documents"
        else:
            # Linux: try XDG.
            xdg_docs = os.getenv('XDG_DOCUMENTS_DIR')
            if xdg_docs:
                return Path(xdg_docs)
            return Path.home() / "Documents"
    
    def _test_directory_writable(self, directory):
        """
        Test whether a directory is writable
        
        Args:
            directory: directory to test
            
        Returns:
            bool: whether it is writable
        """
        try:
            # Create directories incrementally to avoid possible packaged-app problems with parents=True.
            # Collect all parent directories needing creation.
            dirs_to_create = []
            current = directory
            while current and not current.exists():
                dirs_to_create.append(current)
                current = current.parent
            
            # Create directories from the top down.
            for dir_path in reversed(dirs_to_create):
                if not dir_path.exists():
                    dir_path.mkdir(exist_ok=True)
            
            # Try creating a test file.
            test_file = directory / ".write_test"
            test_file.write_text("test", encoding="utf-8")
            test_file.unlink()
            return True
        except Exception:
            return False
    
    def _ensure_log_directory(self):
        """Ensure the log directory exists"""
        try:
            # Create directories incrementally to avoid possible packaged-app problems with parents=True.
            dirs_to_create = []
            current = self.log_dir
            while current and not current.exists():
                dirs_to_create.append(current)
                current = current.parent
            
            # Create directories from the top down.
            for dir_path in reversed(dirs_to_create):
                if not dir_path.exists():
                    dir_path.mkdir(exist_ok=True)
        except Exception as e:
            print(f"Error: Failed to create log directory: {e}", file=sys.stderr)
            raise
    
    def _cleanup_old_logs(self):
        """Clean up old log files past the retention period.

        Besides the main log directory, source-mode runs also sweep the dev DEBUG
        directory (``<repo>/logs/``), so daily-rolled ``*_debug_YYYYMMDD.log`` files
        don't pile up forever.
        """
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        dirs_to_scan = [self.log_dir]
        if not getattr(sys, "frozen", False):
            try:
                dev_dir = _get_application_root() / "logs"
                if dev_dir.exists() and dev_dir.resolve() != self.log_dir.resolve():
                    dirs_to_scan.append(dev_dir)
            except Exception as e:
                print(f"Warning: Failed to resolve dev debug dir for cleanup: {e}", file=sys.stderr)

        for scan_dir in dirs_to_scan:
            try:
                for log_file in scan_dir.glob(f"{self.app_name}_*.log*"):
                    try:
                        file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                        if file_mtime < cutoff_date:
                            log_file.unlink()
                            print(f"Cleaned up old log file: {log_file.name}")
                    except Exception as e:
                        print(f"Warning: Failed to clean up log file {log_file}: {e}", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Failed to cleanup old logs in {scan_dir}: {e}", file=sys.stderr)
    
    def _resolve_console_level(self) -> int:
        """Decide the console handler's level.

        Default: max(log_level, INFO) — even with DEBUG enabled overall, the console
        only shows INFO+; DEBUG goes to files. Override with NEKO_LOG_CONSOLE_LEVEL=DEBUG/INFO/...
        """
        override = (os.environ.get("NEKO_LOG_CONSOLE_LEVEL") or "").strip().upper()
        if override:
            level = logging.getLevelName(override)
            if isinstance(level, int):
                return level
        return max(self.log_level, logging.INFO)

    def get_log_file_path(self):
        """Get the log file path"""
        return str(self.log_file)
    
    def get_log_directory_path(self):
        """Get the log directory path"""
        return str(self.log_dir)
    
    def setup_logger(self, logger_name=None):
        """
        Configure and return a logger instance
        
        Args:
            logger_name: name of the logger; returns the root logger when None
            
        Returns:
            logging.Logger: the configured logger instance
        """
        # Create or retrieve the logger; default to a service-specific logger rather than root.
        if not logger_name:
            if self.service_name:
                logger_name = f"{self.app_name}.{self.service_name}"
            else:
                logger_name = self.app_name
        logger = logging.getLogger(logger_name)
        logger.setLevel(self.log_level)
        # Disable propagation to root so external handlers cannot redirect logs to the wrong file.
        logger.propagate = False
        # Idempotent rebuild: remove existing handlers from this logger to avoid duplicate writes.
        if logger.handlers:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
        
        # Log format.
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'
        formatter = logging.Formatter(log_format, date_format)
        
        # Console defaults to INFO: DEBUG floods terminals instantly; disk logging suffices.
        # Set NEKO_LOG_CONSOLE_LEVEL=DEBUG for rare local troubleshooting requiring console DEBUG.
        console_level = self._resolve_console_level()

        # 1. Console handler.
        try:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(console_level)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        except Exception as e:
            print(f"Warning: Failed to add console handler: {e}", file=sys.stderr)
        
        # 2. Rotating file handler.
        try:
            # Use RotatingFileHandler for size-based rotation.
            file_handler = RotatingFileHandler(
                self.log_file,
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
                encoding='utf-8'
            )
            # Main log accepts INFO+; DEBUG goes to a separate dev file to keep the unified user log clean.
            file_handler.setLevel(max(self.log_level, logging.INFO))
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Error: Failed to add file handler: {e}", file=sys.stderr)
            # File-handler failures must not stop the application.

        # 2b. Dev-only DEBUG handler: enable only for source runs, writing under source logs/,
        # accepting only DEBUG (INFO+ already goes to the main log). Do not attach when frozen:
        # AppImage squashfs is read-only, and packaged users do not need developer debug logs.
        if not getattr(sys, "frozen", False) and self.log_level <= logging.DEBUG:
            try:
                dev_debug_dir = _get_application_root() / "logs"
                dev_debug_dir.mkdir(parents=True, exist_ok=True)
                if self.service_name:
                    debug_filename = f"{self.app_name}_{self.service_name}_debug_{datetime.now().strftime('%Y%m%d')}.log"
                else:
                    debug_filename = f"{self.app_name}_debug_{datetime.now().strftime('%Y%m%d')}.log"
                debug_handler = RotatingFileHandler(
                    dev_debug_dir / debug_filename,
                    maxBytes=self.max_bytes,
                    backupCount=self.backup_count,
                    encoding='utf-8',
                    delay=True,
                )
                debug_handler.setLevel(logging.DEBUG)
                debug_handler.addFilter(lambda r: r.levelno < logging.INFO)
                debug_handler.setFormatter(formatter)
                logger.addHandler(debug_handler)
            except Exception as e:
                print(f"Warning: Failed to add dev debug handler: {e}", file=sys.stderr)

        # 3. Error-log handler (separate ERROR-and-above output).
        try:
            if self.service_name:
                error_filename = f"{self.app_name}_{self.service_name}_error.log"
            else:
                error_filename = f"{self.app_name}_error.log"
            error_log_file = self.log_dir / error_filename
            error_handler = RotatingFileHandler(
                error_log_file,
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
                encoding='utf-8'
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            logger.addHandler(error_handler)
        except Exception as e:
            print(f"Warning: Failed to add error handler: {e}", file=sys.stderr)
        
        return logger


class EnhancedLogger:
    """Enhanced logger wrapper that handles tracebacks automatically"""
    
    def __init__(self, logger):
        self._logger = logger
    
    def __getattr__(self, name):
        """Proxy all other methods to the original logger"""
        return getattr(self._logger, name)
    
    def error(self, msg, *args, exc_info=None, **kwargs):
        """
        Enhanced error method that automatically includes the traceback
        
        Args:
            msg: error message
            exc_info: whether to include exception info, default True (auto-detect)
            *args, **kwargs: other arguments passed through to the original logger.error
        """
        # In exception context, automatically set exc_info=True unless explicitly supplied.
        if exc_info is None:
            import sys
            exc_info = sys.exc_info()[0] is not None
        
        self._logger.error(msg, *args, exc_info=exc_info, **kwargs)
    
    def exception(self, msg, *args, **kwargs):
        """Exception logging method (always includes the traceback)"""
        self._logger.exception(msg, *args, **kwargs)


def setup_logging(app_name=None, service_name=None, log_level=None, silent=False,
                  log_subdir=None):
    """
    Convenience function: set up logging configuration

    Args:
        app_name: application name, defaults to APP_NAME from config (determines the log directory)
        service_name: service name, distinguishing log files of different services (e.g. Main, Memory, Agent)
        log_level: log level
        silent: silent mode, no init message printed (for subprocesses, avoiding duplicate output)
        log_subdir: log subdirectory. Plugin subprocesses pass ``"plugin"`` to tuck
            ``N.E.K.O_Plugin_<id>_*.log`` under ``logs/plugin/``, keeping them apart
            from host-process logs like PluginServer / Main / Memory / Agent.
            Default ``None`` = keep the old behavior, writing to the ``logs/`` base directory.

    Returns:
        tuple: (enhanced logger instance, logging config object)

    Note:
        The returned logger automatically includes the traceback on error() calls (when in an exception context)
        logger.exception() can also be used to record exception info explicitly
    """
    config = RobustLoggerConfig(
        app_name=app_name,
        service_name=service_name,
        log_level=log_level,
        log_subdir=log_subdir,
    )
    # Use namespaced logger names (e.g. N.E.K.O.Agent)
    # to avoid collisions with third-party loggers (browser_use internally has an "Agent" logger).
    base_logger = config.setup_logger()
    
    # Attach handlers to the APP_NAME parent logger so cross-service shared modules (utils, config, etc.)
    # also write to files. Shared modules use get_module_logger(__name__) for loggers such as
    # N.E.K.O.utils.xxx, whose propagation reaches this parent and is captured.
    _ensure_shared_parent_logger(config, base_logger)
    
    # Wrap as an enhanced logger.
    logger = EnhancedLogger(base_logger)
    
    # Record logging configuration (skip in subprocess silent mode).
    if not silent:
        service_info = f"{service_name}" if service_name else config.app_name
        logger.info(f"=== {service_info} 日志系统已初始化 ===")
        logger.info(f"日志目录: {config.get_log_directory_path()}")
        logger.info(f"日志级别: {logging.getLevelName(config.log_level)}")
        logger.info("=" * 50)
    
    return logger, config


# =============================================================================
# Unified rate-limiting log filter.
# =============================================================================

class RateLimitedEndpointFilter(logging.Filter):
    """
    Unified rate-limited log filter
    
    Two modes are supported:
    1. full suppression: logs for certain endpoints are never shown
    2. rate limiting: logs for certain endpoints are shown only once every N seconds
    
    Usage example:
        filter = RateLimitedEndpointFilter(
            suppressed_endpoints=["/health", "/ping"],
            rate_limited_endpoints=["/api/tasks", "/status"],
            rate_limit_interval=15.0
        )
        logging.getLogger("uvicorn.access").addFilter(filter)
    """
    
    DEFAULT_RATE_LIMIT_INTERVAL = 15.0  # Default: 15 seconds.
    
    def __init__(self, 
                 suppressed_endpoints: list = None,
                 rate_limited_endpoints: list = None,
                 rate_limit_interval: float = None,
                 rate_limit_message: str = None):
        """
        Initialize the filter
        
        Args:
            suppressed_endpoints: endpoints to suppress entirely (logs never shown)
            rate_limited_endpoints: endpoints to rate-limit (shown once every N seconds)
            rate_limit_interval: rate-limit interval in seconds, default 15
            rate_limit_message: rate-limit hint message, default "(this log is shown every {N} seconds)"
        """
        super().__init__()
        self.suppressed_endpoints = suppressed_endpoints or []
        self.rate_limited_endpoints = rate_limited_endpoints or []
        self.rate_limit_interval = rate_limit_interval or self.DEFAULT_RATE_LIMIT_INTERVAL
        self.rate_limit_message = rate_limit_message or f"(此日志每{int(self.rate_limit_interval)}秒显示一次)"
        
        # Track each endpoint's previous log time.
        self._last_log_times = {}
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        Filter a log record
        
        Returns:
            bool: True to show the log, False to suppress it
        """
        import time
        
        # Always display WARNING and ERROR logs.
        if record.levelno > logging.INFO:
            return True
        
        msg = record.getMessage()
        
        # Check fully suppressed endpoints.
        for endpoint in self.suppressed_endpoints:
            if endpoint in msg:
                return False
        
        # Check rate-limited endpoints.
        current_time = time.time()
        for endpoint in self.rate_limited_endpoints:
            if endpoint in msg:
                last_time = self._last_log_times.get(endpoint, 0)
                if current_time - last_time >= self.rate_limit_interval:
                    self._last_log_times[endpoint] = current_time
                    # Add a rate-limit notice.
                    record.msg = f"{record.msg} {self.rate_limit_message}"
                    return True
                else:
                    return False
        
        return True
    
    def reset_timer(self, endpoint: str = None):
        """
        Reset timers
        
        Args:
            endpoint: endpoint to reset; resets all when None
        """
        if endpoint:
            self._last_log_times.pop(endpoint, None)
        else:
            self._last_log_times.clear()


class ThrottledLogger:
    """
    Rate-limited logger wrapper
    
    For business-logic scenarios that need rate-limited logging
    
    Usage example:
        throttled = ThrottledLogger(logger, interval=15.0)
        throttled.info("mcp_check", "MCP availability check result: ready")  # logged once every 15s
    """
    
    def __init__(self, logger, interval: float = 15.0):
        """
        Initialize the rate-limited logger
        
        Args:
            logger: original logger instance
            interval: rate-limit interval in seconds
        """
        self._logger = logger
        self._interval = interval
        self._last_log_times = {}
    
    def _should_log(self, key: str) -> bool:
        """Check whether the log should be recorded"""
        import time
        current_time = time.time()
        last_time = self._last_log_times.get(key, 0)
        if current_time - last_time >= self._interval:
            self._last_log_times[key] = current_time
            return True
        return False
    
    def _format_message(self, msg: str) -> str:
        """Format the message, appending the rate-limit hint"""
        return f"{msg} (此日志每{int(self._interval)}秒显示一次)"
    
    def debug(self, key: str, msg: str, *args, **kwargs):
        """Rate-limited debug log"""
        if self._should_log(key):
            self._logger.debug(self._format_message(msg), *args, **kwargs)
    
    def info(self, key: str, msg: str, *args, **kwargs):
        """Rate-limited info log"""
        if self._should_log(key):
            self._logger.info(self._format_message(msg), *args, **kwargs)
    
    def warning(self, key: str, msg: str, *args, **kwargs):
        """Rate-limited warning log"""
        if self._should_log(key):
            self._logger.warning(self._format_message(msg), *args, **kwargs)
    
    def error(self, key: str, msg: str, *args, **kwargs):
        """Rate-limited error log"""
        if self._should_log(key):
            self._logger.error(self._format_message(msg), *args, **kwargs)
    
    def reset(self, key: str = None):
        """Reset timers"""
        if key:
            self._last_log_times.pop(key, None)
        else:
            self._last_log_times.clear()


# =============================================================================
# Predefined filter configurations.
# =============================================================================

# Main Server endpoint configuration.
MAIN_SERVER_SUPPRESSED_ENDPOINTS = [
    "/api/characters/current_catgirl",
    "/api/agent/computer_use/availability",
    "/api/agent/mcp/availability",
    "/api/steam/update-playtime",
    # OAuth callbacks carry short-lived code/state in the query string.  The
    # route handles them, but the raw request target must never reach access logs.
    "/oauth/callback",
    "/api/card-drop/oauth/callback",
    "/api/card-drop/steam-callback",
]

MAIN_SERVER_RATE_LIMITED_ENDPOINTS = [
]

# Agent Server endpoint configuration.
AGENT_SERVER_SUPPRESSED_ENDPOINTS = [
    "/computer_use/availability",
    "/mcp/availability",
]

AGENT_SERVER_RATE_LIMITED_ENDPOINTS = [
    "/tasks",
]

# HTTPX client suppression configuration.
HTTPX_SUPPRESSED_PATTERNS = [
    "/computer_use/availability",
    "/mcp/availability",
    # Crawler domains — music (music_crawlers.py)
    "music.163.com",
    "soundcloud.com",
    "itunes.apple.com",
    "musopen.org",
    "freemusicarchive.org",
    "bandcamp.com",
    # Crawler domains — memes (meme_fetcher.py)
    "imgflip.com",
    # 2026-04-16: doutub.com changed ownership and hosts illicit services; disable it.
    # "doutub.com",
    "fabiaoqing.com",
    "doutupk.com",
    # Crawler domains — web scraper (web_scraper.py)
    "bilibili.com",
    "reddit.com",
    "weibo.com",
    "weibo.cn",
    "twitter.com",
    "google.com/search",
    "baidu.com",
    "douyin.com",
    "kuaishou.com",
    "trends24.in",
    "getdaytrends.com",
]

# HTTPX client rate limits (display once every N seconds).
HTTPX_RATE_LIMITED_PATTERNS = [
    "/mcp",  # Rate-limit MCP request logs.
    "/tasks",  # Rate-limit task-status polling requests.
]


def create_main_server_filter() -> RateLimitedEndpointFilter:
    """Create the Main Server log filter"""
    return RateLimitedEndpointFilter(
        suppressed_endpoints=MAIN_SERVER_SUPPRESSED_ENDPOINTS,
        rate_limited_endpoints=MAIN_SERVER_RATE_LIMITED_ENDPOINTS,
        rate_limit_interval=15.0
    )


def create_agent_server_filter() -> RateLimitedEndpointFilter:
    """Create the Agent Server log filter"""
    return RateLimitedEndpointFilter(
        suppressed_endpoints=AGENT_SERVER_SUPPRESSED_ENDPOINTS,
        rate_limited_endpoints=AGENT_SERVER_RATE_LIMITED_ENDPOINTS,
        rate_limit_interval=15.0
    )


def create_httpx_filter() -> RateLimitedEndpointFilter:
    """Create the HTTPX client log filter"""
    return RateLimitedEndpointFilter(
        suppressed_endpoints=HTTPX_SUPPRESSED_PATTERNS,
        rate_limited_endpoints=HTTPX_RATE_LIMITED_PATTERNS,
        rate_limit_interval=15.0
    )


def _ensure_shared_parent_logger(config, service_logger):
    """Attach the same handlers as the service logger to the APP_NAME parent logger.

    Configured only once per process (idempotent). This way shared loggers created by
    get_module_logger(__name__) (without service_name), like N.E.K.O.utils.xxx, also
    write to the log files correctly.
    """
    app_logger = logging.getLogger(config.app_name)
    if app_logger.handlers:
        return
    app_logger.setLevel(service_logger.level)
    app_logger.propagate = False
    for handler in service_logger.handlers:
        app_logger.addHandler(handler)


def get_module_logger(module_name: str, service_name: str = None) -> logging.Logger:
    """Get a module-level logger bound to the given service's log file.

    Via Python logging's hierarchical propagation, child loggers automatically inherit
    the parent logger's file handler — no per-module configuration needed.

    Args:
        module_name: module name, usually __name__.
        service_name: owning service name (e.g. "Main", "Agent", "Memory").
                      When None, creates a shared logger (under APP_NAME).

    Examples:
        # a module belonging to the Main service
        logger = get_module_logger(__name__, "Main")   # → N.E.K.O.Main.main_logic.core

        # a utility module shared across services
        logger = get_module_logger(__name__)            # → N.E.K.O.utils.config_manager
    """
    if service_name:
        return logging.getLogger(f"{APP_NAME}.{service_name}.{module_name}")
    return logging.getLogger(f"{APP_NAME}.{module_name}")


# Export primary interfaces.
__all__ = [
    'RobustLoggerConfig', 
    'EnhancedLogger', 
    'setup_logging',
    'get_module_logger',
    # Rate limiting.
    'RateLimitedEndpointFilter',
    'ThrottledLogger',
    # Predefined configurations.
    'MAIN_SERVER_SUPPRESSED_ENDPOINTS',
    'MAIN_SERVER_RATE_LIMITED_ENDPOINTS',
    'AGENT_SERVER_SUPPRESSED_ENDPOINTS',
    'AGENT_SERVER_RATE_LIMITED_ENDPOINTS',
    'HTTPX_SUPPRESSED_PATTERNS',
    'HTTPX_RATE_LIMITED_PATTERNS',
    # Factory functions.
    'create_main_server_filter',
    'create_agent_server_filter',
    'create_httpx_filter',
]


if __name__ == "__main__":
    # Test code.
    logger, config = setup_logging("TestApp")
    
    logger.debug("这是一条debug消息")
    logger.info("这是一条info消息")
    logger.warning("这是一条warning消息")
    logger.error("这是一条error消息")
    
    print(f"\n日志已保存到: {config.get_log_file_path()}")
