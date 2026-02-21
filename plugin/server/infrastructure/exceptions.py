"""
异常处理中间件
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

from plugin._types.exceptions import (
    PluginError,
    PluginNotFoundError,
    PluginNotRunningError,
    PluginTimeoutError,
)


def register_exception_handlers(app):
    """注册异常处理中间件"""
    
    @app.exception_handler(PluginError)
    async def plugin_error_handler(request: Request, exc: PluginError):
        """统一处理插件系统异常"""
        logger.warning(f"Plugin error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Plugin error",
                "detail": str(exc),
                "type": exc.__class__.__name__
            }
        )

    @app.exception_handler(PluginNotFoundError)
    async def plugin_not_found_handler(request: Request, exc: PluginNotFoundError):
        """处理插件未找到异常"""
        return JSONResponse(
            status_code=404,
            content={
                "error": "Plugin not found",
                "detail": str(exc),
                "plugin_id": exc.plugin_id
            }
        )

    @app.exception_handler(PluginNotRunningError)
    async def plugin_not_running_handler(request: Request, exc: PluginNotRunningError):
        """处理插件未运行异常"""
        return JSONResponse(
            status_code=503,
            content={
                "error": "Plugin not running",
                "detail": str(exc),
                "plugin_id": exc.plugin_id,
                "status": exc.status
            }
        )

    @app.exception_handler(PluginTimeoutError)
    async def plugin_timeout_handler(request: Request, exc: PluginTimeoutError):
        """处理插件超时异常"""
        return JSONResponse(
            status_code=504,
            content={
                "error": "Plugin timeout",
                "detail": str(exc),
                "plugin_id": exc.plugin_id,
                "entry_id": exc.entry_id,
                "timeout": exc.timeout
            }
        )

