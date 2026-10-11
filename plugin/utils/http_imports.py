"""Load the outbound HTTP backend on first use without blocking an event loop."""

from __future__ import annotations

import asyncio
from importlib import import_module
from types import ModuleType

_backend: ModuleType | None = None


def load_httpx() -> ModuleType:
    global _backend
    if _backend is None:
        imported = import_module("httpx")
        if _backend is None:
            _backend = imported
    return _backend


async def ensure_httpx() -> ModuleType:
    if _backend is not None:
        return _backend
    return await asyncio.to_thread(load_httpx)
