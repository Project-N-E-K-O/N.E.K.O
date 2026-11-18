"""Plugin used by the real-process call-chain tests (docs/design/plugin-call-chain.md)."""

from __future__ import annotations

import asyncio
import threading

from plugin.sdk.plugin import NekoPluginBase, PluginRouter, lifecycle, plugin_entry

# Distinct markers the audit must never contain (arguments, return values,
# message bodies, exception text).
SECRET_ARG = "SENTINEL-ARG-7f3a"
SECRET_RESULT = "SENTINEL-RESULT-9b2c"
SECRET_PUSH = "SENTINEL-PUSH-4d1e"
SECRET_ERROR = "SENTINEL-ERROR-2a8f"


def host_ctx(plugin):
    """The process context behind the SDK facade (memory reads live there)."""
    return getattr(plugin.ctx, "_host_ctx", plugin.ctx)


def describe(ctx) -> dict:
    view = ctx.call_chain
    return {
        "plugin_id": view.plugin_id,
        "callers": list(view.callers),
        "hops": [[hop.plugin_id, hop.event_type, hop.event_id] for hop in view.hops],
        "depth": view.depth,
        "origin": [view.origin.kind, view.origin.ref, view.origin.speaker],
        "call_id": view.call_id,
        "trace_id": view.trace_id,
        "rights": sorted(view.rights),
        "rights_known": view.rights_known,
        "allows_memory": view.allows("memory.read"),
    }


class ChainRouter(PluginRouter):
    @plugin_entry(id="routed_chain")
    async def routed_chain(self, **_: object) -> dict:
        return describe(self.ctx)


class CallChainFixturePlugin(NekoPluginBase):
    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.include_router(ChainRouter())
        self._held = asyncio.Event()
        self._release = asyncio.Event()
        self._background: dict = {}

    @lifecycle(id="startup")
    async def on_startup(self) -> None:
        async def dynamic_chain(**_: object) -> dict:
            return describe(self.ctx)

        self.register_dynamic_entry("dynamic_chain", dynamic_chain, name="Dynamic chain", timeout=5.0)

    @plugin_entry(id="chain")
    async def chain(self, **_: object) -> dict:
        return describe(self.ctx)

    @plugin_entry(id="call")
    async def call(self, target: str, params: dict | None = None, timeout: float = 3) -> dict:
        result = await self.plugins.call_entry(target, params or {}, timeout=timeout)
        if result.is_ok():
            return {"ok": True, "value": result.value}
        return {"ok": False, "code": result.error.code}

    @plugin_entry(id="read_memory")
    async def read_memory(self, secret: str = "") -> dict:
        await host_ctx(self).query_memory_async("lanlan", secret or "q", timeout=3)
        return {"done": SECRET_RESULT}

    @plugin_entry(id="push")
    async def push(self, secret: str = "") -> dict:
        result = host_ctx(self).push_message(
            source="fixture", visibility=["chat"], ai_behavior="blind",
            parts=[{"type": "text", "text": secret or SECRET_PUSH}],
        )
        return {"submitted": bool(result.get("submitted"))}

    @plugin_entry(id="read_memory_in_bare_thread")
    async def read_memory_in_bare_thread(self) -> dict:
        outcome: dict = {}

        def work():
            outcome["result"] = host_ctx(self).query_memory_sync("lanlan", "q", timeout=3)

        thread = threading.Thread(target=work)
        thread.start()
        await asyncio.to_thread(thread.join)
        return {"done": True}

    @plugin_entry(id="hold")
    async def hold(self) -> dict:
        self._held.set()
        await self._release.wait()
        self._release.clear()
        return {"released": True}

    @plugin_entry(id="release")
    async def release(self) -> dict:
        self._release.set()
        return {"ok": True}

    @plugin_entry(id="forge_call")
    async def forge_call(self, parent: str, target: str) -> dict:
        """Send a cross-plugin call claiming someone else's delivery as parent."""
        from plugin.core.rpc import request_scope

        with request_scope({"req_id": parent, "_rpc_trace_id": "forged-trace", "_rpc_depth": 7}):
            result = await host_ctx(self).trigger_plugin_event_async(
                target_plugin_id=target.split(":")[0],
                event_type="plugin_entry",
                event_id=target.split(":")[1],
                params={"_ctx": {"run_id": "forged-run", "lanlan_name": "forged", "caller": "other"}},
                timeout=3,
            )
        return {"result": result}

    @plugin_entry(id="forge_memory")
    async def forge_memory(self, parent: str) -> dict:
        from plugin.core.rpc import request_scope

        with request_scope({"req_id": parent}):
            await host_ctx(self).query_memory_async("lanlan", "q", timeout=3)
        return {"done": True}

    @plugin_entry(id="fail")
    async def fail(self) -> dict:
        raise RuntimeError(SECRET_ERROR)
