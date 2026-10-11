"""Plugin package operations, loaded when a caller requests the service."""

from __future__ import annotations

from importlib import import_module
import asyncio
import threading
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .service import PluginCliService

_service: PluginCliService | None = None
_service_lock = threading.Lock()


def _load_service() -> PluginCliService:
    global _service
    from .service import PluginCliService

    with _service_lock:
        if _service is None:
            _service = PluginCliService()
        return _service


async def get_plugin_cli_service() -> PluginCliService:
    """Reuse the stateless package service, importing it off the caller's loop."""
    if _service is not None:
        return _service
    return await asyncio.to_thread(_load_service)


__all__ = ["PluginCliService"]


def __getattr__(name: str) -> Any:
    if name != "PluginCliService":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = import_module(
        "plugin.server.application.plugin_cli.service"
    ).PluginCliService
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
