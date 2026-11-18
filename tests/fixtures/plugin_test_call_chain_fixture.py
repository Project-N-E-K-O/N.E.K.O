"""Plugin used by the real-process call-chain tests (docs/design/plugin-call-chain.md)."""

from __future__ import annotations

import asyncio
import threading

from plugin.core.rpc import request_scope
from plugin.sdk.plugin import NekoPluginBase, PluginRouter, lifecycle, plugin_entry


def describe(ctx) -> dict:
    view = ctx.call_chain
    return {
        "callers": list(view.callers),
        "hops": [[hop.plugin_id, hop.event_type, hop.event_id] for hop in view.hops],
        "origin": [view.origin.kind, view.origin.ref],
        "trace_id": view.trace_id,
        "rights": sorted(view.rights),
        "rights_known": view.rights_known,
    }


class ChainRouter(PluginRouter):
    @plugin_entry(id="routed_chain")
    async def routed_chain(self, **_: object) -> dict:
        return describe(self.ctx)


class CallChainFixturePlugin(NekoPluginBase):
    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.include_router(ChainRouter())
        self._release = asyncio.Event()

    @property
    def host(self):
        # The process context behind the SDK facade (memory reads live there).
        return getattr(self.ctx, "_host_ctx", self.ctx)

    @lifecycle(id="startup")
    async def on_startup(self) -> None:
        async def dynamic_chain(**_: object) -> dict:
            return describe(self.ctx)

        self.register_dynamic_entry("dynamic_chain", dynamic_chain, name="Dynamic chain", timeout=5.0)

    @plugin_entry(id="chain")
    async def chain(self, **_: object) -> dict:
        return describe(self.ctx)

    @plugin_entry(id="call")
    async def call(self, target: str, params: dict | None = None) -> dict:
        result = await self.plugins.call_entry(target, params or {}, timeout=3)
        return {"ok": True, "value": result.value} if result.is_ok() else {"ok": False}

    @plugin_entry(id="read_memory")
    async def read_memory(self, secret: str = "q") -> str:
        await self.host.query_memory_async("lanlan", secret, timeout=3)
        return "SENTINEL-RESULT"

    @plugin_entry(id="push")
    async def push(self, secret: str = "SENTINEL-PUSH") -> bool:
        result = self.host.push_message(source="fixture", visibility=["chat"], ai_behavior="blind",
                                        parts=[{"type": "text", "text": secret}])
        return bool(result.get("submitted"))

    @plugin_entry(id="read_memory_in_bare_thread")
    async def read_memory_in_bare_thread(self) -> bool:
        thread = threading.Thread(target=lambda: self.host.query_memory_sync("lanlan", "q", timeout=3))
        thread.start()
        await asyncio.to_thread(thread.join)
        return True

    @plugin_entry(id="hold")
    async def hold(self) -> bool:
        await self._release.wait()
        self._release.clear()
        return True

    @plugin_entry(id="release")
    async def release(self) -> bool:
        self._release.set()
        return True

    @plugin_entry(id="forge")
    async def forge(self, parent: str, target: str) -> dict:
        """Claim someone else's delivery as parent, with a forged _ctx and trace."""
        with request_scope({"req_id": parent, "_rpc_trace_id": "forged-trace", "_rpc_depth": 7}):
            await self.host.query_memory_async("lanlan", "q", timeout=3)
            plugin_id, entry_id = target.split(":")
            return await self.host.trigger_plugin_event_async(
                target_plugin_id=plugin_id, event_type="plugin_entry", event_id=entry_id,
                params={"_ctx": {"run_id": "forged-run", "lanlan_name": "forged"}}, timeout=3,
            )

    @plugin_entry(id="fail")
    async def fail(self) -> None:
        raise RuntimeError("SENTINEL-ERROR")
