"""Exercise SDK calls from lifecycle hooks over the real plugin transport."""

from __future__ import annotations

import asyncio

from plugin.sdk.plugin import NekoPluginBase, lifecycle, plugin_entry


class LifecycleRpcPlugin(NekoPluginBase):
    __freezable__ = ["restored_count"]

    def __init__(self, ctx):
        super().__init__(ctx)
        self.phase = ctx._effective_config["lifecycle_probe"]["phase"]
        self.restored_count = 0

    async def _observations(self):
        return (await self.store.get("observations", {})).unwrap()

    async def _observe(self, phase):
        records = await self.bus.memory.get(bucket_id="lifecycle_rpc", timeout=1)
        observations = await self._observations()
        observations[phase] = len(records)
        (await self.store.set("observations", observations)).unwrap()

    @lifecycle(id="config_change")
    async def config_change(self, **kwargs):
        if self.phase == "config_change":
            await self._observe("config_change")

    @lifecycle(id="freeze")
    async def freeze(self):
        if self.phase == "freeze":
            await self._observe("freeze")

    @lifecycle(id="unfreeze")
    async def unfreeze(self):
        if self.phase == "unfreeze":
            await self._observe("unfreeze")
            self.restored_count += 1

    @lifecycle(id="shutdown")
    async def shutdown(self):
        if self.phase == "shutdown":
            await self._observe("shutdown")
        elif self.phase == "blocked_shutdown":
            await asyncio.Event().wait()

    @plugin_entry(id="snapshot")
    async def snapshot(self):
        return {
            "observations": await self._observations(),
            "restored_count": self.restored_count,
        }
