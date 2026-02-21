"""
统一日志配置模块

提供插件系统的统一日志配置，基于 loguru。

环境变量:
    NEKO_LOG_LEVEL: 全局日志级别 (TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL)
    NEKO_LOG_CONSOLE: 是否输出到控制台 (true/false)
    NEKO_LOG_FILE: 是否输出到文件 (true/false)
    NEKO_LOG_JSON: 是否输出 JSON 格式 (true/false)
    NEKO_LOG_DIR: 日志目录路径

Usage:
    from plugin.logging_config import get_logger, setup_logging
    
    # 获取组件 logger
    logger = get_logger("server.lifecycle")
    logger.info("Server started", port=8000)
    
    # 结构化日志
    logger.info("Request processed", method="GET", path="/api", duration=0.5)
"""
from __future__ import annotations

import os
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from loguru import logger as _loguru_logger


class LogLevel(str, Enum):
    """日志级别枚举"""
    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


def _get_log_level() -> LogLevel:
    """获取全局日志级别"""
    level_str = os.getenv("NEKO_LOG_LEVEL", "INFO").upper()
    try:
        return LogLevel(level_str)
    except ValueError:
        return LogLevel.INFO


def _get_component_level(component: str) -> Optional[LogLevel]:
    """获取组件特定的日志级别"""
    env_name = f"NEKO_LOG_LEVEL_{component.upper().replace('.', '_')}"
    level_str = os.getenv(env_name)
    if level_str:
        try:
            return LogLevel(level_str.upper())
        except ValueError:
            pass
    return None


# 全局配置
LOG_LEVEL = _get_log_level()
LOG_DIR = Path(os.getenv("NEKO_LOG_DIR", "log"))
LOG_CONSOLE = _get_bool_env("NEKO_LOG_CONSOLE", True)
LOG_FILE = _get_bool_env("NEKO_LOG_FILE", True)
LOG_JSON = _get_bool_env("NEKO_LOG_JSON", False)
LOG_MAX_SIZE = os.getenv("NEKO_LOG_MAX_SIZE", "10 MB")
LOG_RETENTION = os.getenv("NEKO_LOG_RETENTION", "7 days")
LOG_COMPRESSION = os.getenv("NEKO_LOG_COMPRESSION", "gz")

# 日志格式（统一格式，所有组件使用）
# 控制台格式（带颜色，使用 extra[component]）
FORMAT_CONSOLE = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{extra[component]: <20}</cyan> | "
    "<level>{message}</level>"
)
# 文件格式（无颜色，使用 extra[component]）
FORMAT_FILE = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{extra[component]: <20} | {message}"
)

# 简化格式（用于不需要 component 的场景）
FORMAT_CONSOLE_SIMPLE = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<level>{message}</level>"
)
FORMAT_FILE_SIMPLE = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}"
)

# 插件进程格式（带进程标识）
def get_plugin_format_console(plugin_id: str) -> str:
    """获取插件进程的控制台日志格式"""
    return (
        f"<green>{{time:YYYY-MM-DD HH:mm:ss}}</green> | "
        f"<level>{{level: <8}}</level> | "
        f"<cyan>[{plugin_id}]</cyan> | "
        f"<level>{{message}}</level>"
    )

def get_plugin_format_file(plugin_id: str) -> str:
    """获取插件进程的文件日志格式"""
    return (
        f"{{time:YYYY-MM-DD HH:mm:ss.SSS}} | {{level: <8}} | "
        f"[{plugin_id}] | {{message}}"
    )

# 敏感信息过滤模式
REDACT_PATTERNS = [
    re.compile(r'(password|passwd|pwd)["\']?\s*[:=]\s*["\']?[^"\'\s]+', re.I),
    re.compile(r'(token|api_key|apikey|secret)["\']?\s*[:=]\s*["\']?[^"\'\s]+', re.I),
    re.compile(r'(authorization|auth)["\']?\s*[:=]\s*["\']?[^"\'\s]+', re.I),
]

# 是否已配置默认 logger
_default_configured = False


def configure_default_logger(level: str = "INFO") -> None:
    """配置默认的 loguru logger 格式
    
    在主进程启动时调用一次，统一所有使用 `from loguru import logger` 的模块的日志格式。
    
    Args:
        level: 日志级别，默认 INFO
    
    Usage:
        # 在 user_plugin_server.py 启动时调用
        from plugin.logging_config import configure_default_logger
        configure_default_logger()
    """
    global _default_configured
    if _default_configured:
        return
    
    _loguru_logger.remove()
    _loguru_logger.add(
        sys.stdout,
        format=FORMAT_CONSOLE_SIMPLE,
        level=level,
        colorize=True,
    )
    _default_configured = True

# 已配置的组件集合
_configured_components: set[str] = set()
_configured_console_roots: set[str] = set()  # 按 root 组件去重，防止 console handler 重复
_setup_lock = None
try:
    import threading
    _setup_lock = threading.Lock()
except ImportError:
    pass


def _redact_sensitive(message: str) -> str:
    """过滤敏感信息"""
    for pattern in REDACT_PATTERNS:
        message = pattern.sub(r'\1=***REDACTED***', message)
    return message


def _format_with_extra(record: dict) -> str:
    """格式化日志记录，包含额外字段"""
    extra = record.get("extra", {})
    extra_fields = {k: v for k, v in extra.items() if k != "component"}
    if extra_fields:
        extra_str = " | " + " ".join(f"{k}={v}" for k, v in extra_fields.items())
        return record["message"] + extra_str
    return record["message"]


def setup_logging(
    component: str = "main",
    level: Optional[LogLevel] = None,
    force: bool = False,
) -> None:
    """配置组件的日志输出
    
    Args:
        component: 组件名称
        level: 日志级别，None 使用全局或组件特定级别
        force: 是否强制重新配置
    """
    if _setup_lock:
        with _setup_lock:
            _setup_logging_impl(component, level, force)
    else:
        _setup_logging_impl(component, level, force)


def _setup_logging_impl(
    component: str,
    level: Optional[LogLevel],
    force: bool,
) -> None:
    """实际的日志配置实现"""
    if component in _configured_components and not force:
        return
    
    # 确定日志级别：参数 > 组件环境变量 > 全局
    if level is None:
        level = _get_component_level(component) or LOG_LEVEL
    
    # 首次配置时移除默认 handler
    if not _configured_components:
        _loguru_logger.remove()
    
    # 控制台输出（按 root 组件去重，防止重复 handler）
    if LOG_CONSOLE:
        root = component.split(".")[0]
        if root not in _configured_console_roots:
            _loguru_logger.add(
                sys.stdout,
                format=FORMAT_CONSOLE,
                level=level.value,
                colorize=True,
                filter=lambda record, r=root: record["extra"].get("component", "").startswith(r),
            )
            _configured_console_roots.add(root)
    
    # 文件输出
    if LOG_FILE:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / f"{component.replace('.', '_')}.log"
        _loguru_logger.add(
            str(log_file),
            format=FORMAT_FILE,
            level=level.value,
            rotation=LOG_MAX_SIZE,
            retention=LOG_RETENTION,
            compression=LOG_COMPRESSION,
            encoding="utf-8",
            filter=lambda record, c=component: record["extra"].get("component", "") == c,
        )
    
    # JSON 输出
    if LOG_JSON:
        json_file = LOG_DIR / f"{component.replace('.', '_')}.json"
        _loguru_logger.add(
            str(json_file),
            serialize=True,
            level=level.value,
            rotation=LOG_MAX_SIZE,
            retention=LOG_RETENTION,
            filter=lambda record, c=component: record["extra"].get("component", "") == c,
        )
    
    _configured_components.add(component)


def get_logger(component: str) -> Any:
    """获取带组件标识的 logger
    
    Args:
        component: 组件名称，如 "server.lifecycle", "runtime.host", "plugin.xxx"
    
    Returns:
        绑定了组件名称的 loguru logger
    
    Usage:
        logger = get_logger("server.lifecycle")
        logger.info("Server started")
        logger.error("Failed to start", error=str(e))
    """
    # 自动配置组件
    if component not in _configured_components:
        setup_logging(component)
    
    return _loguru_logger.bind(component=component)


def intercept_standard_logging() -> None:
    """拦截标准库 logging，重定向到 loguru
    
    用于兼容使用标准库 logging 的第三方库。
    """
    import logging
    
    class InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                level = _loguru_logger.level(record.levelname).name
            except ValueError:
                level = record.levelno
            
            frame, depth = sys._getframe(6), 6
            while frame and frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1
            
            _loguru_logger.opt(depth=depth, exception=record.exc_info).log(
                level, record.getMessage()
            )
    
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    
    # 拦截常见的第三方库日志
    for name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "sqlalchemy"]:
        logging.getLogger(name).handlers = [InterceptHandler()]


def format_log_text(value: Any, max_len: Optional[int] = None, wrap: Optional[int] = None) -> str:
    """格式化日志文本，支持截断和换行
    
    Args:
        value: 要格式化的值
        max_len: 最大长度，默认从环境变量 NEKO_PLUGIN_LOG_CONTENT_MAX 读取
        wrap: 换行宽度，默认从环境变量 NEKO_PLUGIN_LOG_WRAP 读取
    
    Returns:
        格式化后的字符串
    """
    s = "" if value is None else str(value)
    
    if max_len is None:
        try:
            max_len = int(os.getenv("NEKO_PLUGIN_LOG_CONTENT_MAX", "200"))
        except (ValueError, TypeError):
            max_len = 200
    if max_len <= 0:
        max_len = 200
    
    truncated = False
    if len(s) > max_len:
        s = s[:max_len]
        truncated = True
    
    if wrap is None:
        try:
            wrap = int(os.getenv("NEKO_PLUGIN_LOG_WRAP", "0"))
        except (ValueError, TypeError):
            wrap = 0
    
    if wrap and wrap > 0:
        s = "\n".join(s[i:i + wrap] for i in range(0, len(s), wrap))
    
    if truncated:
        s = s + "...(truncated)"
    
    return s


# 导出
__all__ = [
    "LogLevel",
    "LOG_LEVEL",
    "LOG_DIR",
    "get_logger",
    "setup_logging",
    "configure_default_logger",
    "intercept_standard_logging",
    "format_log_text",
    # 日志格式常量
    "FORMAT_CONSOLE",
    "FORMAT_FILE",
    "FORMAT_CONSOLE_SIMPLE",
    "FORMAT_FILE_SIMPLE",
    "get_plugin_format_console",
    "get_plugin_format_file",
]
