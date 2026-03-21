from __future__ import annotations

import asyncio

from plugin.sdk.plugin import NekoPluginBase, lifecycle


class DynamicEntryFixturePlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def on_startup(self) -> None:
        async def _late_entry(**_: object) -> dict[str, object]:
            return {"ok": True, "source": "dynamic"}

        async def _slow_entry(**_: object) -> dict[str, object]:
            await asyncio.sleep(0.2)
            return {"ok": True, "source": "slow"}

        self.register_dynamic_entry(
            "late_entry",
            _late_entry,
            name="Late Entry",
            timeout=1.0,
        )
        self.register_dynamic_entry(
            "slow_entry",
            _slow_entry,
            name="Slow Entry",
            timeout=0.05,
        )
