from __future__ import annotations

import importlib

import pytest
import plugin.sdk_v2.adapter as adapter

from plugin.sdk_v2.public.adapter import base as pub_adapter_base
from plugin.sdk_v2.public.adapter import decorators as pub_adapter_decorators
from plugin.sdk_v2.public.adapter import gateway_contracts as pub_adapter_gateway_contracts
from plugin.sdk_v2.public.adapter import gateway_core as pub_adapter_gateway_core
from plugin.sdk_v2.public.adapter import gateway_defaults as pub_adapter_gateway_defaults
from plugin.sdk_v2.public.adapter import gateway_models as pub_adapter_gateway_models
from plugin.sdk_v2.public.adapter import neko_adapter as pub_adapter_neko
from plugin.sdk_v2.public.adapter import types as pub_adapter_types
from plugin.sdk_v2.public.extension import base as pub_extension_base
from plugin.sdk_v2.public.extension import decorators as pub_extension_decorators
from plugin.sdk_v2.public.extension import runtime as pub_extension_runtime
from plugin.sdk_v2.public.plugin import base as pub_plugin_base
from plugin.sdk_v2.public.plugin import decorators as pub_plugin_decorators
from plugin.sdk_v2.public.plugin import runtime as pub_plugin_runtime


def test_public_plugin_and_extension_modules_import_and_export() -> None:
    for mod in (
        importlib.reload(pub_plugin_base),
        importlib.reload(pub_plugin_decorators),
        importlib.reload(pub_plugin_runtime),
        importlib.reload(pub_extension_base),
        importlib.reload(pub_extension_decorators),
        importlib.reload(pub_extension_runtime),
    ):
        for name in mod.__all__:
            assert hasattr(mod, name)


def test_public_adapter_models_and_types_construct() -> None:
    env = pub_adapter_gateway_models.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool", payload={})
    req = pub_adapter_gateway_models.GatewayRequest(request_id="r", protocol="mcp", action=pub_adapter_gateway_models.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={})
    route = pub_adapter_gateway_models.RouteDecision(mode=pub_adapter_gateway_models.RouteMode.SELF)
    err = pub_adapter_gateway_models.GatewayError(code="E", message="e")
    resp = pub_adapter_gateway_models.GatewayResponse(request_id="r", success=False, error=err)
    msg = pub_adapter_types.AdapterMessage(id="1", protocol=pub_adapter_types.Protocol.CUSTOM, action="x", payload={})
    res = pub_adapter_types.AdapterResponse(request_id="1")
    rule = pub_adapter_types.RouteRule()
    assert env.protocol == "mcp"
    assert req.action == pub_adapter_gateway_models.GatewayAction.TOOL_CALL
    assert route.mode == pub_adapter_gateway_models.RouteMode.SELF
    assert resp.error is err
    assert msg.protocol == pub_adapter_types.Protocol.CUSTOM
    assert res.success is True
    assert rule.target == pub_adapter_types.RouteTarget.SELF


def test_public_adapter_decorators_return_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pub_adapter_decorators, "_not_impl", lambda *_a, **_k: None)

    def fn() -> str:
        return "x"

    assert pub_adapter_decorators.on_adapter_event()(fn) is fn
    assert pub_adapter_decorators.on_adapter_startup(priority=1)(fn) is fn
    assert pub_adapter_decorators.on_adapter_shutdown(priority=1)(fn) is fn
    assert pub_adapter_decorators.on_adapter_startup(fn, priority=1) is fn
    assert pub_adapter_decorators.on_adapter_shutdown(fn, priority=1) is fn
    assert pub_adapter_decorators.on_mcp_tool()(fn) is fn
    assert pub_adapter_decorators.on_mcp_resource()(fn) is fn
    assert pub_adapter_decorators.on_nonebot_message("group")(fn) is fn


@pytest.mark.asyncio
async def test_public_adapter_contract_methods_raise() -> None:
    class _Transport:
        protocol_name = "mcp"
        async def start(self):
            return adapter.Ok(None)
        async def stop(self):
            return adapter.Ok(None)
        async def recv(self):
            return adapter.Ok(pub_adapter_gateway_models.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={}))
        async def send(self, response):
            return adapter.Ok(None)

    ctx = pub_adapter_base.AdapterContext(adapter_id="a", config=pub_adapter_base.AdapterConfig(), logger=object())
    base = pub_adapter_base.AdapterBase(config=pub_adapter_base.AdapterConfig(), ctx=ctx)
    assert base.adapter_id == "a"
    core = pub_adapter_gateway_core.AdapterGatewayCore(transport=_Transport(), normalizer=pub_adapter_gateway_defaults.DefaultRequestNormalizer(), policy=pub_adapter_gateway_defaults.DefaultPolicyEngine(), router=pub_adapter_gateway_defaults.DefaultRouteEngine(), invoker=pub_adapter_gateway_defaults.CallablePluginInvoker(lambda _req, _dec: {}), serializer=pub_adapter_gateway_defaults.DefaultResponseSerializer())
    assert (await core.start()).is_ok()
    assert (await core.stop()).is_ok()
    assert (await core.run_once()).is_ok()
    assert (await core.handle_envelope(pub_adapter_gateway_models.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={}))).is_ok()

    defaults = [
        pub_adapter_gateway_defaults.DefaultRequestNormalizer(),
        pub_adapter_gateway_defaults.DefaultPolicyEngine(),
        pub_adapter_gateway_defaults.DefaultRouteEngine(),
        pub_adapter_gateway_defaults.DefaultResponseSerializer(),
        pub_adapter_gateway_defaults.CallablePluginInvoker(lambda _req, _dec: {}),
    ]
    req = pub_adapter_gateway_models.GatewayRequest(request_id="r", protocol="mcp", action=pub_adapter_gateway_models.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={})
    env = pub_adapter_gateway_models.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={})
    err = pub_adapter_gateway_models.GatewayError(code="E", message="e")
    route = pub_adapter_gateway_models.RouteDecision(mode=pub_adapter_gateway_models.RouteMode.SELF)
    assert (await defaults[0].normalize(env)).is_ok()
    assert (await defaults[1].authorize(req)).is_ok()
    assert (await defaults[2].decide(req)).is_ok()
    assert (await defaults[3].ok(req, {}, 1.0)).is_ok()
    assert (await defaults[3].fail(req, err, 1.0)).is_ok()
    assert (await defaults[4].invoke(req, route)).is_ok()

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

    neko = pub_adapter_neko.NekoAdapterPlugin(_Ctx())
    assert neko.adapter_id == "demo"
    assert neko.adapter_mode is not None
    assert (await neko.adapter_startup()).is_ok()
    assert (await neko.adapter_shutdown()).is_ok()
    assert (await neko.register_adapter_tool_as_entry("n", lambda: None)).is_ok()
    assert (await neko.unregister_adapter_tool_entry("n")).is_ok()
    assert neko.list_adapter_routes() == []


def test_public_adapter_gateway_contract_symbols() -> None:
    for name in pub_adapter_gateway_contracts.__all__:
        assert hasattr(pub_adapter_gateway_contracts, name)


def test_public_adapter_decorators_raise_directly() -> None:
    assert pub_adapter_decorators._not_impl() is None


def test_public_adapter_gateway_error_exception_sets_error() -> None:
    err = pub_adapter_gateway_models.GatewayError(code="E", message="e")
    exc = pub_adapter_gateway_models.GatewayErrorException(err)
    assert exc.error is err


@pytest.mark.asyncio
async def test_public_adapter_base_methods_raise() -> None:
    class _Ctx:
        async def trigger_plugin_event_async(self, **kwargs):
            return {"ok": True}
    ctx = pub_adapter_base.AdapterContext(adapter_id="a", config=pub_adapter_base.AdapterConfig(), logger=object(), plugin_ctx=_Ctx())
    assert (await ctx.call_plugin("p", "e", {})).is_ok()
    ctx.register_event_handler("evt", lambda payload: {"ok": True})
    assert (await ctx.broadcast_event("evt", {})).is_ok()
    base = pub_adapter_base.AdapterBase(config=pub_adapter_base.AdapterConfig(), ctx=ctx)
    assert base.adapter_id == "a"
    assert (await base.on_startup()).is_ok()
    assert (await base.on_shutdown()).is_ok()
