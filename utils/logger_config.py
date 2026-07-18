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
    
    # 默认配置
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
        self.service_name = service_name  # 服务名称用于文件名区分
        self.log_level = log_level or self.DEFAULT_LOG_LEVEL
        self.max_bytes = max_bytes or self.DEFAULT_MAX_BYTES
        self.backup_count = backup_count or self.DEFAULT_BACKUP_COUNT
        self.retention_days = retention_days or self.DEFAULT_LOG_RETENTION_DAYS
        self.log_subdir = log_subdir

        # 获取日志目录（先拿到基目录，再按 log_subdir 路由到子目录）
        self.log_dir = self._get_log_directory()
        if log_subdir:
            # 不让调用方传带 "/" 的路径，避免意外逃出基目录。
            safe = str(log_subdir).strip().strip("/\\")
            if safe:
                self.log_dir = self.log_dir / safe
        
        # 日志文件名：如果有service_name则包含，否则只用app_name
        if self.service_name:
            log_filename = f"{self.app_name}_{self.service_name}_{datetime.now().strftime('%Y%m%d')}.log"
        else:
            log_filename = f"{self.app_name}_{datetime.now().strftime('%Y%m%d')}.log"
        self.log_file = self.log_dir / log_filename
        
        # 确保日志目录存在
        self._ensure_log_directory()
        
        # 清理旧日志
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
        # 尝试1: 使用当前存储根目录。老日志不迁移；新日志跟随新根目录。
        try:
            selected_root = _get_selected_storage_root_from_env()
            if selected_root is not None:
                log_dir = selected_root / "logs"
                if self._test_directory_writable(log_dir):
                    return log_dir
        except Exception as e:
            print(f"Warning: Failed to use selected storage log directory: {e}", file=sys.stderr)

        # 尝试2: 使用用户文档目录（兼容旧版本和非 launcher 直接运行）
        try:
            docs_dir = self._get_documents_directory()
            # 使用配置的应用名称目录
            log_dir = docs_dir / self.app_name / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use Documents directory: {e}", file=sys.stderr)
        
        # 尝试2: 使用应用程序所在目录
        try:
            app_dir = _get_writable_application_directory()
            log_dir = app_dir / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use application directory: {e}", file=sys.stderr)
        
        # 尝试3: 使用系统用户数据目录
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
        
        # 尝试4: 使用用户主目录
        try:
            log_dir = Path.home() / f".{self.app_name}" / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use home directory: {e}", file=sys.stderr)
        
        # 尝试5: 使用临时目录（最后的降级选项）
        try:
            import tempfile
            log_dir = Path(tempfile.gettempdir()) / self.app_name / "logs"
            if self._test_directory_writable(log_dir):
                return log_dir
        except Exception as e:
            print(f"Warning: Failed to use temp directory: {e}", file=sys.stderr)
        
        # 如果所有方法都失败，返回当前目录
        print("Warning: All log directory attempts failed, using application directory", file=sys.stderr)
        return _get_writable_application_directory() / "logs"
    
    def _get_documents_directory(self):
        """Get the system's user documents directory (via system APIs)"""
        if sys.platform == "win32":
            # Windows: 使用系统API获取真正的"我的文档"路径
            try:
                import ctypes
                from ctypes import windll, wintypes
                
                # 使用SHGetFolderPath获取我的文档路径
                CSIDL_PERSONAL = 5  # My Documents
                SHGFP_TYPE_CURRENT = 0
                
                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                windll.shell32.SHGetFolderPathW(None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf)
                docs_dir = Path(buf.value)
                
                if docs_dir.exists():
                    return docs_dir
            except Exception as e:
                print(f"Warning: Failed to get Documents path via API: {e}", file=sys.stderr)
            
            # 降级：尝试从注册表读取
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                )
                docs_dir = Path(winreg.QueryValueEx(key, "Personal")[0])
                winreg.CloseKey(key)
                
                # 展开环境变量
                docs_dir = Path(os.path.expandvars(str(docs_dir)))
                if docs_dir.exists():
                    return docs_dir
            except Exception as e:
                print(f"Warning: Failed to get Documents path from registry: {e}", file=sys.stderr)
            
            # 最后的降级
            docs_dir = Path.home() / "Documents"
            if not docs_dir.exists():
                docs_dir = Path.home() / "文档"
            return docs_dir
        
        elif sys.platform == "darwin":
            # macOS
            return Path.home() / "Documents"
        else:
            # Linux: 尝试使用XDG
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
            # 分步创建目录，避免parents=True在打包后可能出现的问题
            # 收集所有需要创建的父目录
            dirs_to_create = []
            current = directory
            while current and not current.exists():
                dirs_to_create.append(current)
                current = current.parent
            
            # 从最顶层开始创建目录
            for dir_path in reversed(dirs_to_create):
                if not dir_path.exists():
                    dir_path.mkdir(exist_ok=True)
            
            # 尝试创建一个测试文件
            test_file = directory / ".write_test"
            test_file.write_text("test", encoding="utf-8")
            test_file.unlink()
            return True
        except Exception:
            return False
    
    def _ensure_log_directory(self):
        """Ensure the log directory exists"""
        try:
            # 分步创建目录，避免parents=True在打包后可能出现的问题
            dirs_to_create = []
            current = self.log_dir
            while current and not current.exists():
                dirs_to_create.append(current)
                current = current.parent
            
            # 从最顶层开始创建目录
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
        # 创建或获取logger。默认使用服务专属logger，避免落到root。
        if not logger_name:
            if self.service_name:
                logger_name = f"{self.app_name}.{self.service_name}"
            else:
                logger_name = self.app_name
        logger = logging.getLogger(logger_name)
        logger.setLevel(self.log_level)
        # 不向root传播，避免被外部handler劫持到错误文件。
        logger.propagate = False
        # 幂等重建：清理当前logger已有handler，避免重复写入。
        if logger.handlers:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
        
        # 日志格式
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        date_format = '%Y-%m-%d %H:%M:%S'
        formatter = logging.Formatter(log_format, date_format)
        
        # 控制台默认钳到 INFO：DEBUG 量太大会瞬间淹没终端，落盘即可。
        # 想让 console 也吐 DEBUG（极少数本地排障场景），设 NEKO_LOG_CONSOLE_LEVEL=DEBUG。
        console_level = self._resolve_console_level()

        # 1. 控制台Handler
        try:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(console_level)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        except Exception as e:
            print(f"Warning: Failed to add console handler: {e}", file=sys.stderr)
        
        # 2. 文件Handler（带轮转）
        try:
            # 使用RotatingFileHandler进行按大小轮转
            file_handler = RotatingFileHandler(
                self.log_file,
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
                encoding='utf-8'
            )
            # 主日志钳到 INFO+：DEBUG 走单独的 dev 文件，不污染用户统一日志。
            file_handler.setLevel(max(self.log_level, logging.INFO))
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Error: Failed to add file handler: {e}", file=sys.stderr)
            # 文件handler失败不应该阻止应用运行

        # 2b. Dev-only DEBUG Handler：仅源码运行时启用，落到源码 ``logs/`` 下，
        # 只收 DEBUG 一级（INFO+ 已经在主日志里）。frozen 时不挂——AppImage
        # squashfs 只读，且打包后用户不需要 dev 调试日志。
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

        # 3. 错误日志Handler（单独记录ERROR及以上级别）
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
        # 如果在异常上下文中且未明确指定exc_info，自动设置为True
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
    # 使用带命名空间的 logger 名（如 N.E.K.O.Agent），
    # 避免与第三方库的同名 logger 冲突（browser_use 内部有名为 "Agent" 的 logger）。
    base_logger = config.setup_logger()
    
    # 为 APP_NAME 父 logger 挂载 handler，使跨服务共享模块（utils, config 等）
    # 的日志也能写入文件。共享模块使用 get_module_logger(__name__) 创建如
    # N.E.K.O.utils.xxx 的 logger，向上传播到此父 logger 后被捕获。
    _ensure_shared_parent_logger(config, base_logger)
    
    # 包装为增强logger
    logger = EnhancedLogger(base_logger)
    
    # 记录日志配置信息（子进程静默模式下跳过）
    if not silent:
        service_info = f"{service_name}" if service_name else config.app_name
        logger.info(f"=== {service_info} 日志系统已初始化 ===")
        logger.info(f"日志目录: {config.get_log_directory_path()}")
        logger.info(f"日志级别: {logging.getLevelName(config.log_level)}")
        logger.info("=" * 50)
    
    return logger, config


# =============================================================================
# 统一的速率限制日志过滤器
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
    
    DEFAULT_RATE_LIMIT_INTERVAL = 15.0  # 默认15秒
    
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
        
        # 记录每个端点的上次日志时间
        self._last_log_times = {}
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        Filter a log record
        
        Returns:
            bool: True to show the log, False to suppress it
        """
        import time
        
        # WARNING 和 ERROR 级别的日志始终显示
        if record.levelno > logging.INFO:
            return True
        
        msg = record.getMessage()
        
        # 检查完全抑制的端点
        for endpoint in self.suppressed_endpoints:
            if endpoint in msg:
                return False
        
        # 检查速率限制的端点
        current_time = time.time()
        for endpoint in self.rate_limited_endpoints:
            if endpoint in msg:
                last_time = self._last_log_times.get(endpoint, 0)
                if current_time - last_time >= self.rate_limit_interval:
                    self._last_log_times[endpoint] = current_time
                    # 添加速率限制提示
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
# 预定义的过滤器配置
# =============================================================================

# Main Server 的端点配置
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

# Agent Server 的端点配置
AGENT_SERVER_SUPPRESSED_ENDPOINTS = [
    "/computer_use/availability",
    "/mcp/availability",
]

AGENT_SERVER_RATE_LIMITED_ENDPOINTS = [
    "/tasks",
]

# HTTPX 客户端的抑制配置
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
    # 2026-04-16: doutub.com 域名易主挂黑产，停用
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

# HTTPX 客户端的速率限制配置（每 N 秒显示一次）
HTTPX_RATE_LIMITED_PATTERNS = [
    "/mcp",  # MCP 相关请求日志限流
    "/tasks",  # 任务状态轮询请求限流
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


# 导出主要接口
__all__ = [
    'RobustLoggerConfig', 
    'EnhancedLogger', 
    'setup_logging',
    'get_module_logger',
    # 速率限制相关
    'RateLimitedEndpointFilter',
    'ThrottledLogger',
    # 预定义配置
    'MAIN_SERVER_SUPPRESSED_ENDPOINTS',
    'MAIN_SERVER_RATE_LIMITED_ENDPOINTS',
    'AGENT_SERVER_SUPPRESSED_ENDPOINTS',
    'AGENT_SERVER_RATE_LIMITED_ENDPOINTS',
    'HTTPX_SUPPRESSED_PATTERNS',
    'HTTPX_RATE_LIMITED_PATTERNS',
    # 工厂函数
    'create_main_server_filter',
    'create_agent_server_filter',
    'create_httpx_filter',
]


if __name__ == "__main__":
    # 测试代码
    logger, config = setup_logging("TestApp")
    
    logger.debug("这是一条debug消息")
    logger.info("这是一条info消息")
    logger.warning("这是一条warning消息")
    logger.error("这是一条error消息")
    
    print(f"\n日志已保存到: {config.get_log_file_path()}")
