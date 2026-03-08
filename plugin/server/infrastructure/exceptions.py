"""
Server exception handlers for FastAPI.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from plugin._types.exceptions import (
    PluginError,
    PluginNotFoundError,
    PluginNotRunningError,
    PluginTimeoutError,
)
from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.exceptions")


def _error_response(status_code: int, payload: dict[str, object]) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


def register_exception_handlers(app: FastAPI) -> None:
    """Register plugin-specific exception handlers on FastAPI app."""

    @app.exception_handler(PluginError)
    async def plugin_error_handler(_: Request, exc: PluginError) -> JSONResponse:
        logger.warning(
            "Plugin error raised: type={}, detail={}",
            type(exc).__name__,
            str(exc),
        )
        return _error_response(
            500,
            {
                "error": "Plugin error",
                "detail": str(exc),
                "type": type(exc).__name__,
            },
        )

    @app.exception_handler(PluginNotFoundError)
    async def plugin_not_found_handler(_: Request, exc: PluginNotFoundError) -> JSONResponse:
        return _error_response(
            404,
            {
                "error": "Plugin not found",
                "detail": str(exc),
                "plugin_id": exc.plugin_id,
            },
        )

    @app.exception_handler(PluginNotRunningError)
    async def plugin_not_running_handler(_: Request, exc: PluginNotRunningError) -> JSONResponse:
        return _error_response(
            503,
            {
                "error": "Plugin not running",
                "detail": str(exc),
                "plugin_id": exc.plugin_id,
                "status": exc.status,
            },
        )

    @app.exception_handler(PluginTimeoutError)
    async def plugin_timeout_handler(_: Request, exc: PluginTimeoutError) -> JSONResponse:
        return _error_response(
            504,
            {
                "error": "Plugin timeout",
                "detail": str(exc),
                "plugin_id": exc.plugin_id,
                "entry_id": exc.entry_id,
                "timeout": exc.timeout,
            },
        )
