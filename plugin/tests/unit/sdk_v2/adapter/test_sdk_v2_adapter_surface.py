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


def test_adapter_decorators_raise() -> None:
    with pytest.raises(NotImplementedError):
        dec.on_adapter_event()
    with pytest.raises(NotImplementedError):
        dec.on_adapter_startup()
    with pytest.raises(NotImplementedError):
        dec.on_adapter_shutdown()


def test_adapter_decorator_return_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dec, "_not_impl", lambda *_args, **_kwargs: None)

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
    with pytest.raises(NotImplementedError):
        adapter.AdapterContext(adapter_id="a", config=adapter.AdapterConfig(), logger=object())
    with pytest.raises(NotImplementedError):
        adapter.AdapterBase(config=adapter.AdapterConfig(), ctx=object())
    with pytest.raises(NotImplementedError):
        adapter.AdapterGatewayCore(transport=object(), normalizer=object(), policy=object(), router=object(), invoker=object(), serializer=object())

    core = object.__new__(adapter.AdapterGatewayCore)
    with pytest.raises(NotImplementedError):
        await core.start()
    with pytest.raises(NotImplementedError):
        await core.stop()
    with pytest.raises(NotImplementedError):
        await core.run_once()
    with pytest.raises(NotImplementedError):
        await core.handle_envelope(gm.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool", payload={}))

    defaults = [
        adapter.DefaultRequestNormalizer(),
        adapter.DefaultPolicyEngine(),
        adapter.DefaultRouteEngine(),
        adapter.DefaultResponseSerializer(),
        adapter.CallablePluginInvoker(lambda _req, _dec: {}),
    ]

    with pytest.raises(NotImplementedError):
        await defaults[0].normalize(gm.ExternalEnvelope(protocol="mcp", connection_id="c", request_id="r", action="tool", payload={}))
    with pytest.raises(NotImplementedError):
        await defaults[1].authorize(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}))
    with pytest.raises(NotImplementedError):
        await defaults[2].decide(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}))
    with pytest.raises(NotImplementedError):
        await defaults[3].ok(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}), {}, 1.0)
    with pytest.raises(NotImplementedError):
        await defaults[3].fail(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}), gm.GatewayError(code="E", message="e"), 1.0)
    with pytest.raises(NotImplementedError):
        await defaults[4].invoke(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}), gm.RouteDecision(mode=gm.RouteMode.SELF))

    neko = object.__new__(adapter.NekoAdapterPlugin)
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


def test_adapter_runtime_common_exports() -> None:
    assert adapter.SDK_VERSION == "2.0.0a0"
    assert adapter.ok is not None
    assert adapter.fail is not None
    assert adapter.Result is not None
    assert adapter.ErrorCode is not None
