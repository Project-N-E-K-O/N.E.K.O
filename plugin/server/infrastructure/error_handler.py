"""
错误处理工具模块

提供统一的错误处理函数，确保：
1. 只捕获预期的异常类型
2. 错误信息不泄露内部细节
3. 错误被正确记录和传播
"""
import asyncio
import os
from typing import Any, Callable, TypeVar, Optional
from functools import wraps

from fastapi import HTTPException
from loguru import logger
from plugin._types.exceptions import (
    PluginError,
    PluginNotFoundError,
    PluginNotRunningError,
    PluginTimeoutError,
    PluginExecutionError,
    PluginCommunicationError,
)

# 是否在开发模式（开发模式下可以返回更详细的错误信息）
DEBUG_MODE = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

# 预期的异常类型（可以安全地转换为 HTTPException）
EXPECTED_EXCEPTIONS = (
    PluginError,
    HTTPException,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    ConnectionError,
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
)

T = TypeVar('T')


def safe_error_message(error: Exception, context: str = "") -> str:
    """
    生成安全的错误消息，避免泄露内部细节
    
    Args:
        error: 异常对象
        context: 上下文信息
    
    Returns:
        安全的错误消息
    """
    if DEBUG_MODE:
        # 开发模式下返回详细错误信息
        return f"{context}: {str(error)}" if context else str(error)
    
    # 生产模式下返回通用错误消息
    if isinstance(error, PluginError):
        # 插件系统异常，返回用户友好的消息
        return str(error)
    elif isinstance(error, HTTPException):
        # HTTP异常，直接返回其消息
        return error.detail
    elif isinstance(error, (ValueError, TypeError, AttributeError, KeyError)):
        # 参数错误，返回通用消息
        return "Invalid request parameters"
    elif isinstance(error, (ConnectionError, OSError)):
        # 连接错误，返回通用消息
        return "Service temporarily unavailable"
    elif isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        # 超时错误，返回通用消息
        return "Request timeout"
    else:
        # 未知错误，返回通用消息
        return "An internal error occurred"


def handle_plugin_error(
    error: Exception,
    context: str,
    default_status_code: int = 500,
    log_level: str = "error"
) -> HTTPException:
    """
    处理插件相关错误，转换为 HTTPException
    
    Args:
        error: 异常对象
        context: 上下文信息（用于日志）
        default_status_code: 默认HTTP状态码
        log_level: 日志级别
    
    Returns:
        HTTPException
    """
    # 记录详细错误信息（包含堆栈跟踪）
    log_func = getattr(logger, log_level, logger.error)
    log_func(f"{context}: {error}", exc_info=True)
    
    # 如果是预期的异常，使用其状态码和消息
    if isinstance(error, HTTPException):
        return error
    elif isinstance(error, PluginNotFoundError):
        return HTTPException(status_code=404, detail=safe_error_message(error, context))
    elif isinstance(error, PluginNotRunningError):
        return HTTPException(status_code=503, detail=safe_error_message(error, context))
    elif isinstance(error, (PluginTimeoutError, TimeoutError, asyncio.TimeoutError)):
        return HTTPException(status_code=504, detail=safe_error_message(error, context))
    elif isinstance(error, (PluginExecutionError, PluginCommunicationError)):
        return HTTPException(status_code=500, detail=safe_error_message(error, context))
    elif isinstance(error, PluginError):
        return HTTPException(status_code=500, detail=safe_error_message(error, context))
    elif isinstance(error, (ValueError, TypeError, AttributeError, KeyError)):
        return HTTPException(status_code=400, detail=safe_error_message(error, context))
    elif isinstance(error, (ConnectionError, OSError)):
        return HTTPException(status_code=503, detail=safe_error_message(error, context))
    else:
        # 未知异常，返回通用错误
        return HTTPException(
            status_code=default_status_code,
            detail=safe_error_message(error, context)
        )


def error_handler(
    context: str,
    default_status_code: int = 500,
    log_level: str = "error",
    reraise_unexpected: bool = False
):
    """
    错误处理装饰器
    
    Args:
        context: 上下文信息（用于日志和错误消息）
        default_status_code: 默认HTTP状态码
        log_level: 日志级别
        reraise_unexpected: 是否重新抛出未预期的异常（用于调试）
    
    Example:
        @error_handler("Failed to get plugin status")
        async def get_plugin_status(...):
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                # HTTPException 直接传播
                raise
            except EXPECTED_EXCEPTIONS as e:
                # 预期的异常，转换为 HTTPException
                raise handle_plugin_error(e, context, default_status_code, log_level) from e
            except Exception as e:
                # 未预期的异常
                logger.exception(f"{context}: Unexpected error type: {type(e).__name__}")
                if reraise_unexpected:
                    raise
                raise handle_plugin_error(e, context, default_status_code, log_level) from e
        
        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return func(*args, **kwargs)
            except HTTPException:
                raise
            except EXPECTED_EXCEPTIONS as e:
                raise handle_plugin_error(e, context, default_status_code, log_level) from e
            except Exception as e:
                logger.exception(f"{context}: Unexpected error type: {type(e).__name__}")
                if reraise_unexpected:
                    raise
                raise handle_plugin_error(e, context, default_status_code, log_level) from e
        
        # 根据函数是否为协程函数选择包装器
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def safe_execute(
    func: Callable[..., T],
    context: str,
    default_return: Optional[T] = None,
    log_level: str = "warning"
) -> Optional[T]:
    """
    安全执行函数，捕获异常并返回默认值（用于非关键操作）
    
    Args:
        func: 要执行的函数
        context: 上下文信息
        default_return: 异常时的默认返回值
        log_level: 日志级别
    
    Returns:
        函数返回值或默认值
    """
    try:
        return func()
    except EXPECTED_EXCEPTIONS as e:
        log_func = getattr(logger, log_level, logger.warning)
        log_func(f"{context}: {e}", exc_info=True)
        return default_return
    except Exception as e:
        logger.exception(f"{context}: Unexpected error type: {type(e).__name__}")
        return default_return

