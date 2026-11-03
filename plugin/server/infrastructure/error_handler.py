"""
Error-handling utilities

Provide unified error handling to ensure:
1. Only expected exception types are caught
2. Error messages reveal no internal details
3. Errors are correctly recorded and propagated
"""
import asyncio
import os
from typing import Callable, TypeVar, cast
from functools import wraps

from fastapi import HTTPException
from plugin._types.exceptions import (
    PluginError,
    PluginNotFoundError,
    PluginNotRunningError,
    PluginTimeoutError,
    PluginExecutionError,
    PluginCommunicationError,
)
from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.error_handler")

# Whether development mode is active (permits more detailed error messages).
DEBUG_MODE = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

# Expected exception types (safe to convert to HTTPException).
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
UNEXPECTED_RUNTIME_EXCEPTIONS = (
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    TimeoutError,
    ImportError,
    ModuleNotFoundError,
    LookupError,
    AssertionError,
    NameError,
    ArithmeticError,
    UnicodeError,
)


def safe_error_message(error: Exception, context: str = "") -> str:
    """
    Generate a safe error message without exposing internal details.
    
    Args:
        error: Exception object
        context: Context information
    
    Returns:
        Safe error message
    """
    if DEBUG_MODE:
        # Return detailed errors in development mode.
        return f"{context}: {str(error)}" if context else str(error)
    
    # Return generic errors in production mode.
    if isinstance(error, PluginError):
        # Plugin-system exceptions return user-friendly messages.
        return str(error)
    elif isinstance(error, HTTPException):
        # HTTP exceptions return their messages directly.
        return error.detail
    elif isinstance(error, (ValueError, TypeError, AttributeError, KeyError)):
        # Argument errors return generic messages.
        return "Invalid request parameters"
    elif isinstance(error, (ConnectionError, OSError)):
        # Connection errors return generic messages.
        return "Service temporarily unavailable"
    elif isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        # Timeout errors return generic messages.
        return "Request timeout"
    else:
        # Unknown errors return generic messages.
        return "An internal error occurred"


def handle_plugin_error(
    error: Exception,
    context: str,
    default_status_code: int = 500,
    log_level: str = "error"
) -> HTTPException:
    """
    Handle plugin errors and convert them to HTTPException.
    
    Args:
        error: Exception object
        context: Context information for logging
        default_status_code: Default HTTP status code
        log_level: Logging level
    
    Returns:
        HTTPException
    """
    # Record detailed error information including the stack trace.
    log_func = getattr(logger, log_level, logger.error)
    log_func(f"{context}: {error}", exc_info=True)
    
    # Expected exceptions use their status codes and messages.
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
        # Unknown exceptions return a generic error.
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
    Error-handling decorator.
    
    Args:
        context: Context information for logging and error messages
        default_status_code: Default HTTP status code
        log_level: Logging level
        reraise_unexpected: Whether to reraise unexpected exceptions for debugging
    
    Example:
        @error_handler("Failed to get plugin status")
        async def get_plugin_status(...):
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args: object, **kwargs: object) -> T:
            try:
                return await func(*args, **kwargs)
            except HTTPException:
                # Propagate HTTPException directly.
                raise
            except EXPECTED_EXCEPTIONS as e:
                # Convert expected exceptions to HTTPException.
                raise handle_plugin_error(e, context, default_status_code, log_level) from e
            except UNEXPECTED_RUNTIME_EXCEPTIONS as e:
                # Unexpected exceptions.
                logger.exception(f"{context}: Unexpected error type: {type(e).__name__}")
                if reraise_unexpected:
                    raise
                raise handle_plugin_error(e, context, default_status_code, log_level) from e
            except BaseException as e:
                if isinstance(e, (SystemExit, KeyboardInterrupt, GeneratorExit)):
                    raise
                if not isinstance(e, Exception):
                    raise
                logger.exception(f"{context}: Unexpected base exception type: {type(e).__name__}")
                if reraise_unexpected:
                    raise
                raise handle_plugin_error(cast(Exception, e), context, default_status_code, log_level) from e
        
        @wraps(func)
        def sync_wrapper(*args: object, **kwargs: object) -> T:
            try:
                return func(*args, **kwargs)
            except HTTPException:
                raise
            except EXPECTED_EXCEPTIONS as e:
                raise handle_plugin_error(e, context, default_status_code, log_level) from e
            except UNEXPECTED_RUNTIME_EXCEPTIONS as e:
                logger.exception(f"{context}: Unexpected error type: {type(e).__name__}")
                if reraise_unexpected:
                    raise
                raise handle_plugin_error(e, context, default_status_code, log_level) from e
            except BaseException as e:
                if isinstance(e, (SystemExit, KeyboardInterrupt, GeneratorExit)):
                    raise
                if not isinstance(e, Exception):
                    raise
                logger.exception(f"{context}: Unexpected base exception type: {type(e).__name__}")
                if reraise_unexpected:
                    raise
                raise handle_plugin_error(cast(Exception, e), context, default_status_code, log_level) from e
        
        # Select the wrapper according to whether the function is a coroutine.
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def safe_execute(
    func: Callable[..., T],
    context: str,
    default_return: T | None = None,
    log_level: str = "warning"
) -> T | None:
    """
    Execute a function safely, catching exceptions and returning a default for noncritical operations.
    
    Args:
        func: Function to execute
        context: Context information
        default_return: Value to return on exception
        log_level: Logging level
    
    Returns:
        Function result or default value
    """
    try:
        return func()
    except EXPECTED_EXCEPTIONS as e:
        log_func = getattr(logger, log_level, logger.warning)
        log_func(f"{context}: {e}", exc_info=True)
        return default_return
    except UNEXPECTED_RUNTIME_EXCEPTIONS as e:
        logger.exception(f"{context}: Unexpected error type: {type(e).__name__}")
        return default_return
    except BaseException as e:
        if isinstance(e, (SystemExit, KeyboardInterrupt, GeneratorExit)):
            raise
        if not isinstance(e, Exception):
            raise
        logger.exception(f"{context}: Unexpected base exception type: {type(e).__name__}")
        return default_return
