"""Plugins used by the real-process control RPC contract tests."""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath

from pydantic import BaseModel

from plugin.sdk.plugin import NekoPluginBase, plugin_entry
from plugin.sdk.shared.models import Err


class Query(BaseModel):
    query: str


class RpcFixturePlugin(NekoPluginBase):
    @plugin_entry(id="start_background")
    async def start_background(self, target: str) -> str:
        self._background_release = asyncio.Event()
        self._background_result = None

        async def work():
            await self._background_release.wait()
            self._background_result = await self.call(target)

        self._background_task = asyncio.create_task(work())
        return "scheduled"

    @plugin_entry(id="release_background")
    async def release_background(self) -> None:
        self._background_release.set()

    @plugin_entry(id="background_result")
    async def background_result(self) -> dict:
        return {"ready": self._background_result is not None, "result": self._background_result}

    @plugin_entry(id="wire_value")
    async def wire_value(self, kind: str) -> object:
        if kind == "integer_keys":
            return {2024: 3}
        if kind == "path":
            return PurePosixPath("cache/image.png")
        return object()

    @plugin_entry(id="ping")
    async def ping(self, value: str = "ok", delay: float = 0) -> dict:
        self._pending_pings = getattr(self, "_pending_pings", 0) + 1
        try:
            await asyncio.sleep(delay)
            return {"value": value}
        finally:
            self._pending_pings -= 1

    @plugin_entry(id="active")
    async def active(self) -> int:
        return getattr(self, "_pending_pings", 0)

    @plugin_entry(id="typed", params=Query)
    async def typed(self, params: Query) -> dict:
        return {"query": params.query}

    @plugin_entry(id="reject")
    async def reject(self) -> object:
        return Err(ValueError("target rejected input"))

    @plugin_entry(id="call")
    async def call(
        self, target: str, params: dict | None = None, timeout: float = 1
    ) -> dict:
        result = await self.plugins.call_entry(target, params or {}, timeout=timeout)
        if result.is_ok():
            return {"ok": True, "value": result.value}
        return {"ok": False, "error": str(result.error), "code": result.error.code}

    @plugin_entry(id="relay")
    async def relay(self, target: str) -> object:
        return await self.plugins.call_entry(target, timeout=1)

    @plugin_entry(id="parallel")
    async def parallel(self, target: str) -> list:
        results = await asyncio.gather(
            *[
                self.plugins.call_entry(
                    target, {"value": str(i), "delay": (3 - i) * 0.01}, timeout=1
                )
                for i in range(4)
            ]
        )
        return [
            result.value if result.is_ok() else {"error": str(result.error)}
            for result in results
        ]

    @plugin_entry(id="config")
    async def config_entry(self) -> object:
        return await self.config.dump(timeout=1)
