"""Read recent context through the public SDK from async and worker entries."""

import asyncio

from plugin.sdk.plugin import NekoPluginBase, plugin_entry


class UserContextPlugin(NekoPluginBase):
    def _read_worker(self):
        return [
            record.dump()
            for record in self.bus.memory.get(
                bucket_id="context-test", limit=1, timeout=2
            )
        ]

    @plugin_entry(id="read")
    async def read(self):
        records = await self.bus.memory.get(
            bucket_id="context-test", limit=1, timeout=2
        )
        return {
            "async": [record.dump() for record in records],
            "worker": await asyncio.to_thread(self._read_worker),
        }
