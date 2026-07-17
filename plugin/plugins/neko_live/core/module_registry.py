"""Feature-module registry for Neko Roast."""

from __future__ import annotations

from typing import Any, Protocol

from .module_registry_lifecycle import (
    setup_all_modules,
    teardown_all_modules,
    toggle_module,
)
from .module_registry_snapshot import ModuleRecord, module_snapshot


class InteractionModule(Protocol):
    id: str
    title: str
    version: str
    enabled: bool
    domain: str

    async def setup(self, ctx: Any) -> None:
        raise NotImplementedError

    async def teardown(self) -> None:
        raise NotImplementedError

    async def on_enable(self, ctx: Any) -> None:
        raise NotImplementedError

    async def on_disable(self) -> None:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        raise NotImplementedError

    def config_schema(self) -> list[dict[str, Any]]:
        raise NotImplementedError


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, InteractionModule] = {}
        self._degraded: dict[str, str] = {}

    def register(self, module: InteractionModule) -> None:
        if module.id in self._modules:
            raise ValueError(f"duplicate module id: {module.id}")
        self._modules[module.id] = module

    async def setup_all(self, ctx: Any) -> None:
        await setup_all_modules(self._modules, self._degraded, ctx)

    async def teardown_all(self, ctx: Any = None) -> None:
        await teardown_all_modules(self._modules, ctx)

    async def enable(self, module_id: str, ctx: Any) -> bool:
        return await toggle_module(self._modules, self._degraded, module_id, True, ctx)

    async def disable(self, module_id: str, ctx: Any) -> bool:
        return await toggle_module(self._modules, self._degraded, module_id, False, ctx)

    def get(self, module_id: str) -> InteractionModule:
        return self._modules[module_id]

    def is_degraded(self, module_id: str) -> bool:
        return module_id in self._degraded

    def snapshot(self) -> list[dict[str, Any]]:
        return module_snapshot(self._modules, self._degraded)


__all__ = [
    "InteractionModule",
    "ModuleRecord",
    "ModuleRegistry",
]
