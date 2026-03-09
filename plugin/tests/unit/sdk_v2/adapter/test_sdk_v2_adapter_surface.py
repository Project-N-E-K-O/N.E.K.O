from __future__ import annotations

import pytest

import plugin.sdk_v2.adapter as adapter
from plugin.sdk_v2.adapter import decorators as dec
from plugin.sdk_v2.adapter import gateway_models as gm


def test_adapter_exports_exist() -> None:
    for name in adapter.__all__:
        assert hasattr(adapter, name)


def test_adapter_models_construct() -> None:
    env = gm.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={})
    req = gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={})
    route = gm.RouteDecision(mode=gm.RouteMode.SELF)
    err = gm.GatewayError(code="E", message="e")
    resp = gm.GatewayResponse(request_id="r", success=False, error=err)
    assert env.protocol == "mcp"
    assert req.action == gm.GatewayAction.TOOL_CALL
    assert route.mode == gm.RouteMode.SELF
    assert resp.error is err
    assert isinstance(gm.GatewayErrorException(err), RuntimeError)


def test_adapter_decorators_construct() -> None:
    def fn() -> str:
        return "x"
    wrapped = dec.on_adapter_event()(fn)
    assert wrapped is fn
    assert getattr(fn, dec.ADAPTER_EVENT_META).protocol == "*"
    assert dec.on_adapter_startup()(fn) is fn
    assert dec.on_adapter_shutdown()(fn) is fn


def test_adapter_decorator_return_paths() -> None:
    def fn() -> str:
        return "x"

    assert dec.on_adapter_event()(fn) is fn
    assert dec.on_adapter_startup(priority=1)(fn) is fn
    assert dec.on_adapter_shutdown(priority=1)(fn) is fn
    assert dec.on_adapter_startup(fn, priority=1) is fn
    assert dec.on_adapter_shutdown(fn, priority=1) is fn

    assert dec.on_mcp_tool()(fn) is fn
    assert dec.on_mcp_resource()(fn) is fn
    assert dec.on_nonebot_message("group")(fn) is fn


@pytest.mark.asyncio
async def test_adapter_not_implemented_methods() -> None:
    ctx = adapter.AdapterContext(adapter_id="a", config=adapter.AdapterConfig(), logger=object())
    assert ctx.adapter_id == "a"
    base = adapter.AdapterBase(config=adapter.AdapterConfig(), ctx=ctx)
    assert base.adapter_id == "a"
    pass

    class _Transport:
        protocol_name = "mcp"
        async def start(self):
            return adapter.Ok(None)
        async def stop(self):
            return adapter.Ok(None)
        async def recv(self):
            return adapter.Ok(gm.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={}))
        async def send(self, response):
            return adapter.Ok(None)

    core = adapter.AdapterGatewayCore(transport=_Transport(), normalizer=adapter.DefaultRequestNormalizer(), policy=adapter.DefaultPolicyEngine(), router=adapter.DefaultRouteEngine(), invoker=adapter.CallablePluginInvoker(lambda _req, _dec: {}), serializer=adapter.DefaultResponseSerializer())
    assert (await core.start()).is_ok()
    assert (await core.run_once()).is_ok()
    assert (await core.stop()).is_ok()
    assert (await core.handle_envelope(gm.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={}))).is_ok()

    defaults = [
        adapter.DefaultRequestNormalizer(),
        adapter.DefaultPolicyEngine(),
        adapter.DefaultRouteEngine(),
        adapter.DefaultResponseSerializer(),
        adapter.CallablePluginInvoker(lambda _req, _dec: {}),
    ]

    assert (await defaults[0].normalize(gm.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={}))).is_ok()
    assert (await defaults[1].authorize(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}))).is_ok()
    assert (await defaults[2].decide(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}))).is_ok()
    assert (await defaults[3].ok(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}), {}, 1.0)).is_ok()
    assert (await defaults[3].fail(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}), gm.GatewayError(code="E", message="e"), 1.0)).is_ok()
    assert (await defaults[4].invoke(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}), gm.RouteDecision(mode=gm.RouteMode.SELF))).is_ok()

    class _Ctx:
        plugin_id = "demo"
        logger = None
        config_path = __import__("pathlib").Path("/tmp/demo/plugin.toml")
        _effective_config = {"plugin": {"store": {"enabled": True}, "database": {"enabled": True, "name": "data.db"}}, "plugin_state": {"backend": "file"}}
        async def get_own_config(self, timeout: float = 5.0):
            return {"config": {}}
        async def query_plugins_async(self, filters: dict[str, object], timeout: float = 5.0):
            return {"plugins": []}
        async def trigger_plugin_event_async(self, **kwargs):
            return {"ok": True}
    neko = adapter.NekoAdapterPlugin(_Ctx())
    assert neko.adapter_id == "demo"
    assert neko.adapter_mode is not None
    assert (await neko.adapter_startup()).is_ok()
    assert (await neko.adapter_shutdown()).is_ok()
    assert (await neko.register_adapter_tool_as_entry("n", lambda: None)).is_ok()
    assert (await neko.unregister_adapter_tool_entry("n")).is_ok()
    assert neko.list_adapter_routes() == []


def test_adapter_runtime_common_exports() -> None:
    assert adapter.SDK_VERSION == "0.1.0"
    assert adapter.ok is not None
    assert adapter.fail is not None
    assert adapter.Result is not None
    assert adapter.ErrorCode is not None


@pytest.mark.asyncio
async def test_adapter_base_methods_raise() -> None:
    class _Ctx2:
        async def trigger_plugin_event_async(self, **kwargs):
            return {"ok": True}
    ctx = adapter.AdapterContext(adapter_id="a", config=adapter.AdapterConfig(), logger=object(), plugin_ctx=_Ctx2())
    assert (await ctx.call_plugin("p", "e", {})).is_ok()
    ctx.register_event_handler("evt", lambda payload: {"ok": True})
    assert (await ctx.broadcast_event("evt", {})).is_ok()
    base = adapter.AdapterBase(config=adapter.AdapterConfig(), ctx=ctx)
    assert (await base.on_startup()).is_ok()
    assert (await base.on_shutdown()).is_ok()


def test_adapter_facade_methods_are_visible() -> None:
    assert hasattr(adapter.AdapterConfig, "from_dict")
    assert hasattr(adapter.AdapterContext, "register_event_handler")
    assert hasattr(adapter.AdapterContext, "get_event_handlers")
    assert hasattr(adapter.AdapterContext, "call_plugin")
    assert hasattr(adapter.AdapterContext, "broadcast_event")
    assert hasattr(adapter.AdapterBase, "adapter_id")
    assert hasattr(adapter.AdapterBase, "mode")
    assert hasattr(adapter.AdapterBase, "on_message")
    assert hasattr(adapter.AdapterGatewayCore, "start")
    assert hasattr(adapter.DefaultRequestNormalizer, "normalize")
    assert hasattr(adapter.DefaultPolicyEngine, "authorize")
    assert hasattr(adapter.DefaultRouteEngine, "decide")
    assert hasattr(adapter.DefaultResponseSerializer, "ok")
    assert hasattr(adapter.CallablePluginInvoker, "invoke")
    assert hasattr(adapter.NekoAdapterPlugin, "adapter_config")
    assert hasattr(adapter.NekoAdapterPlugin, "register_adapter_tool_as_entry")
