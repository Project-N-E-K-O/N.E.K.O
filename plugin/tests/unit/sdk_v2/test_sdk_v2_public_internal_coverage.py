from __future__ import annotations

import importlib

import pytest

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
    with pytest.raises(NotImplementedError):
        pub_adapter_base.AdapterContext(adapter_id="a", config=pub_adapter_base.AdapterConfig(), logger=object())
    with pytest.raises(NotImplementedError):
        pub_adapter_base.AdapterBase(config=pub_adapter_base.AdapterConfig(), ctx=object())
    with pytest.raises(NotImplementedError):
        pub_adapter_gateway_core.AdapterGatewayCore(transport=object(), normalizer=object(), policy=object(), router=object(), invoker=object(), serializer=object())

    core = object.__new__(pub_adapter_gateway_core.AdapterGatewayCore)
    with pytest.raises(NotImplementedError):
        await core.start()
    with pytest.raises(NotImplementedError):
        await core.stop()
    with pytest.raises(NotImplementedError):
        await core.run_once()
    with pytest.raises(NotImplementedError):
        await core.handle_envelope(pub_adapter_gateway_models.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool", payload={}))

    defaults = [
        pub_adapter_gateway_defaults.DefaultRequestNormalizer(),
        pub_adapter_gateway_defaults.DefaultPolicyEngine(),
        pub_adapter_gateway_defaults.DefaultRouteEngine(),
        pub_adapter_gateway_defaults.DefaultResponseSerializer(),
        pub_adapter_gateway_defaults.CallablePluginInvoker(lambda _req, _dec: {}),
    ]
    req = pub_adapter_gateway_models.GatewayRequest(request_id="r", protocol="mcp", action=pub_adapter_gateway_models.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={})
    env = pub_adapter_gateway_models.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool", payload={})
    err = pub_adapter_gateway_models.GatewayError(code="E", message="e")
    route = pub_adapter_gateway_models.RouteDecision(mode=pub_adapter_gateway_models.RouteMode.SELF)

    with pytest.raises(NotImplementedError):
        await defaults[0].normalize(env)
    with pytest.raises(NotImplementedError):
        await defaults[1].authorize(req)
    with pytest.raises(NotImplementedError):
        await defaults[2].decide(req)
    with pytest.raises(NotImplementedError):
        await defaults[3].ok(req, {}, 1.0)
    with pytest.raises(NotImplementedError):
        await defaults[3].fail(req, err, 1.0)
    with pytest.raises(NotImplementedError):
        await defaults[4].invoke(req, route)

    neko = object.__new__(pub_adapter_neko.NekoAdapterPlugin)
    with pytest.raises(NotImplementedError):
        _ = neko.adapter_config
    with pytest.raises(NotImplementedError):
        _ = neko.adapter_context
    with pytest.raises(NotImplementedError):
        _ = neko.adapter_mode
    with pytest.raises(NotImplementedError):
        _ = neko.adapter_id
    with pytest.raises(NotImplementedError):
        await neko.adapter_startup()
    with pytest.raises(NotImplementedError):
        await neko.adapter_shutdown()
    with pytest.raises(NotImplementedError):
        await neko.register_adapter_tool_as_entry("n", object())
    with pytest.raises(NotImplementedError):
        await neko.unregister_adapter_tool_entry("n")
    with pytest.raises(NotImplementedError):
        neko.list_adapter_routes()


def test_public_adapter_gateway_contract_symbols() -> None:
    for name in pub_adapter_gateway_contracts.__all__:
        assert hasattr(pub_adapter_gateway_contracts, name)


def test_public_adapter_decorators_raise_directly() -> None:
    with pytest.raises(NotImplementedError):
        pub_adapter_decorators._not_impl()


def test_public_adapter_gateway_error_exception_sets_error() -> None:
    err = pub_adapter_gateway_models.GatewayError(code="E", message="e")
    exc = pub_adapter_gateway_models.GatewayErrorException(err)
    assert exc.error is err


@pytest.mark.asyncio
async def test_public_adapter_base_methods_raise() -> None:
    ctx = object.__new__(pub_adapter_base.AdapterContext)
    base = object.__new__(pub_adapter_base.AdapterBase)
    with pytest.raises(NotImplementedError):
        await ctx.call_plugin("p", "e", {})
    with pytest.raises(NotImplementedError):
        await ctx.broadcast_event("evt", {})
    with pytest.raises(NotImplementedError):
        await base.on_startup()
    with pytest.raises(NotImplementedError):
        await base.on_shutdown()
