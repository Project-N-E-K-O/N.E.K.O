# -*- coding: utf-8 -*-
"""
鲁棒的日志配置模块
适用于exe封装后的应用，支持：
- 自动选择合适的日志目录（用户数据目录）
- 日志轮转（按大小和时间）
- 自动清理旧日志
- 降级策略（当无法写入时的备用方案）
- 跨平台支持
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta

from config import APP_NAME


class RobustLoggerConfig:
    """鲁棒的日志配置类"""
    
    # 默认配置
    DEFAULT_LOG_LEVEL = logging.INFO
    DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10MB per log file
    DEFAULT_BACKUP_COUNT = 5  # Keep 5 backup files
    DEFAULT_LOG_RETENTION_DAYS = 30  # Keep logs for 30 days
    
    def __init__(self, app_name=None, service_name=None, log_level=None, max_bytes=None, 
                 backup_count=None, retention_days=None):
        """
        初始化日志配置
        
        Args:
            app_name: 应用名称，用于创建日志目录，默认使用配置中的 APP_NAME
            service_name: 服务名称，用于区分不同服务的日志文件（如Main、Memory、Agent）
            log_level: 日志级别
            max_bytes: 单个日志文件的最大大小
            backup_count: 保留的备份文件数量
            retention_days: 日志保留天数
        """
        self.app_name = app_name if app_name is not None else APP_NAME
        self.service_name = service_name  # 服务名称用于文件名区分
        self.log_level = log_level or self.DEFAULT_LOG_LEVEL
        self.max_bytes = max_bytes or self.DEFAULT_MAX_BYTES
        self.backup_count = backup_count or self.DEFAULT_BACKUP_COUNT
        self.retention_days = retention_days or self.DEFAULT_LOG_RETENTION_DAYS
        
        # 获取日志目录
        self.log_dir = self._get_log_directory()
        
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
        获取合适的日志目录
        优先级：
        1. 用户文档目录/{APP_NAME}/logs（我的文档，默认首选）
        2. 应用程序所在目录/logs
        3. 用户数据目录（AppData等）
        4. 用户主目录
        5. 临时目录（最后的降级选项）
        
        Returns:
            Path: 日志目录路径
        """
        # 尝试1: 使用用户文档目录（我的文档，默认首选！）
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
            # 对于exe打包的应用，使用exe所在目录
            if getattr(sys, 'frozen', False):
                # 如果是打包后的exe
                app_dir = Path(sys.executable).parent
            else:
                # 如果是脚本运行，使用项目根目录
                app_dir = Path.cwd()
            
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
        print(f"Warning: All log directory attempts failed, using current directory", file=sys.stderr)
        return Path.cwd() / "logs"
    
    def _get_documents_directory(self):
        """获取系统的用户文档目录（使用系统API）"""
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
        测试目录是否可写
        
        Args:
            directory: 要测试的目录
            
        Returns:
            bool: 是否可写
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
            test_file.write_text("test")
            test_file.unlink()
            return True
        except Exception:
            return False
    
    def _ensure_log_directory(self):
        """确保日志目录存在"""
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
        """清理超过保留期的旧日志文件"""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.retention_days)
            
            for log_file in self.log_dir.glob(f"{self.app_name}_*.log*"):
                try:
                    # 获取文件的修改时间
                    file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                    
                    # 如果文件太旧，删除它
                    if file_mtime < cutoff_date:
                        log_file.unlink()
                        print(f"Cleaned up old log file: {log_file.name}")
                except Exception as e:
                    print(f"Warning: Failed to clean up log file {log_file}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Failed to cleanup old logs: {e}", file=sys.stderr)
    
    def get_log_file_path(self):
        """获取日志文件路径"""
        return str(self.log_file)
    
    def get_log_directory_path(self):
        """获取日志目录路径"""
        return str(self.log_dir)
    
    def setup_logger(self, logger_name=None):
        """
        配置并返回logger实例
        
        Args:
            logger_name: logger的名称，如果为None则返回root logger
            
        Returns:
            logging.Logger: 配置好的logger实例
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
        
        # 1. 控制台Handler
        try:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.log_level)
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
            file_handler.setLevel(self.log_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Error: Failed to add file handler: {e}", file=sys.stderr)
            # 文件handler失败不应该阻止应用运行
        
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
    """增强的Logger包装器，自动处理traceback"""
    
    def __init__(self, logger):
        self._logger = logger
    
    def __getattr__(self, name):
        """代理所有其他方法到原始logger"""
        return getattr(self._logger, name)
    
    def error(self, msg, *args, exc_info=None, **kwargs):
        """
        增强的error方法，自动包含traceback
        
        Args:
            msg: 错误消息
            exc_info: 是否包含异常信息，默认True（自动检测）
            *args, **kwargs: 传递给原始logger.error的其他参数
        """
        # 如果在异常上下文中且未明确指定exc_info，自动设置为True
        if exc_info is None:
            import sys
            exc_info = sys.exc_info()[0] is not None
        
        self._logger.error(msg, *args, exc_info=exc_info, **kwargs)
    
    def exception(self, msg, *args, **kwargs):
        """异常记录方法（始终包含traceback）"""
        self._logger.exception(msg, *args, **kwargs)


def setup_logging(app_name=None, service_name=None, log_level=None, silent=False):
    """
    便捷函数：设置日志配置
    
    Args:
        app_name: 应用名称，默认使用配置中的 APP_NAME（用于确定日志目录）
        service_name: 服务名称，用于区分不同服务的日志文件（如Main、Memory、Agent）
        log_level: 日志级别
        silent: 静默模式，不打印初始化消息（用于子进程避免重复输出）
        
    Returns:
        tuple: (增强的logger实例, 日志配置对象)
        
    注意：
        返回的logger会自动在error()调用时包含traceback（如果在异常上下文中）
        也可以使用logger.exception()来明确记录异常信息
    """
    config = RobustLoggerConfig(app_name=app_name, service_name=service_name, log_level=log_level)
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
    统一的速率限制日志过滤器
    
    支持两种模式：
    1. 完全抑制：某些端点的日志完全不显示
    2. 速率限制：某些端点的日志每 N 秒只显示一次
    
    使用示例：
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
        初始化过滤器
        
        Args:
            suppressed_endpoints: 完全抑制的端点列表（日志完全不显示）
            rate_limited_endpoints: 速率限制的端点列表（每 N 秒显示一次）
            rate_limit_interval: 速率限制间隔（秒），默认15秒
            rate_limit_message: 速率限制提示消息，默认 "(此日志每{N}秒显示一次)"
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
        过滤日志记录
        
        Returns:
            bool: True 表示显示日志，False 表示抑制日志
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
        重置计时器
        
        Args:
            endpoint: 要重置的端点，如果为 None 则重置所有
        """
        if endpoint:
            self._last_log_times.pop(endpoint, None)
        else:
            self._last_log_times.clear()


class ThrottledLogger:
    """
    带速率限制的日志记录器包装器
    
    用于业务逻辑中需要速率限制日志的场景
    
    使用示例:
        throttled = ThrottledLogger(logger, interval=15.0)
        throttled.info("mcp_check", "MCP availability check result: ready")  # 每15秒只记录一次
    """
    
    def __init__(self, logger, interval: float = 15.0):
        """
        初始化速率限制日志记录器
        
        Args:
            logger: 原始 logger 实例
            interval: 速率限制间隔（秒）
        """
        self._logger = logger
        self._interval = interval
        self._last_log_times = {}
    
    def _should_log(self, key: str) -> bool:
        """检查是否应该记录日志"""
        import time
        current_time = time.time()
        last_time = self._last_log_times.get(key, 0)
        if current_time - last_time >= self._interval:
            self._last_log_times[key] = current_time
            return True
        return False
    
    def _format_message(self, msg: str) -> str:
        """格式化消息，添加速率限制提示"""
        return f"{msg} (此日志每{int(self._interval)}秒显示一次)"
    
    def debug(self, key: str, msg: str, *args, **kwargs):
        """速率限制的 debug 日志"""
        if self._should_log(key):
            self._logger.debug(self._format_message(msg), *args, **kwargs)
    
    def info(self, key: str, msg: str, *args, **kwargs):
        """速率限制的 info 日志"""
        if self._should_log(key):
            self._logger.info(self._format_message(msg), *args, **kwargs)
    
    def warning(self, key: str, msg: str, *args, **kwargs):
        """速率限制的 warning 日志"""
        if self._should_log(key):
            self._logger.warning(self._format_message(msg), *args, **kwargs)
    
    def error(self, key: str, msg: str, *args, **kwargs):
        """速率限制的 error 日志"""
        if self._should_log(key):
            self._logger.error(self._format_message(msg), *args, **kwargs)
    
    def reset(self, key: str = None):
        """重置计时器"""
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
]

# HTTPX 客户端的速率限制配置（每 N 秒显示一次）
HTTPX_RATE_LIMITED_PATTERNS = [
    "/mcp",  # MCP 相关请求日志限流
    "/tasks",  # 任务状态轮询请求限流
]


def create_main_server_filter() -> RateLimitedEndpointFilter:
    """创建 Main Server 的日志过滤器"""
    return RateLimitedEndpointFilter(
        suppressed_endpoints=MAIN_SERVER_SUPPRESSED_ENDPOINTS,
        rate_limited_endpoints=MAIN_SERVER_RATE_LIMITED_ENDPOINTS,
        rate_limit_interval=15.0
    )


def create_agent_server_filter() -> RateLimitedEndpointFilter:
    """创建 Agent Server 的日志过滤器"""
    return RateLimitedEndpointFilter(
        suppressed_endpoints=AGENT_SERVER_SUPPRESSED_ENDPOINTS,
        rate_limited_endpoints=AGENT_SERVER_RATE_LIMITED_ENDPOINTS,
        rate_limit_interval=15.0
    )


def create_httpx_filter() -> RateLimitedEndpointFilter:
    """创建 HTTPX 客户端的日志过滤器"""
    return RateLimitedEndpointFilter(
        suppressed_endpoints=HTTPX_SUPPRESSED_PATTERNS,
        rate_limited_endpoints=HTTPX_RATE_LIMITED_PATTERNS,
        rate_limit_interval=15.0
    )


def _ensure_shared_parent_logger(config, service_logger):
    """为 APP_NAME 父 logger 挂载与服务 logger 相同的 handler。

    每个进程只配置一次（幂等）。这样 get_module_logger(__name__)（不带 service_name）
    创建的共享 logger（如 N.E.K.O.utils.xxx）也能正确写入日志文件。
    """
    app_logger = logging.getLogger(config.app_name)
    if app_logger.handlers:
        return
    app_logger.setLevel(service_logger.level)
    app_logger.propagate = False
    for handler in service_logger.handlers:
        app_logger.addHandler(handler)


def get_module_logger(module_name: str, service_name: str = None) -> logging.Logger:
    """获取绑定到指定服务日志文件的模块级 logger。

    通过 Python logging 的层级传播机制，子 logger 自动继承父 logger 的
    file handler，无需为每个模块单独配置。

    Args:
        module_name: 模块名，通常传 __name__。
        service_name: 所属服务名（如 "Main", "Agent", "Memory"）。
                      如果为 None，创建共享 logger（挂在 APP_NAME 下）。

    Examples:
        # 属于 Main 服务的模块
        logger = get_module_logger(__name__, "Main")   # → N.E.K.O.Main.main_logic.core

        # 跨服务共享的工具模块
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

