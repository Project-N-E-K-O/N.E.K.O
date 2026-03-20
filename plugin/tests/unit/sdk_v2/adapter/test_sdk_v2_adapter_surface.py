from __future__ import annotations

from pathlib import Path
import re

import pytest

import plugin.sdk_v2.adapter as adapter
from plugin.sdk_v2.adapter import decorators as dec
from plugin.sdk_v2.adapter import gateway_models as gm


class _MockPluginCtx:
    plugin_id = "demo"
    logger = None

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": True, "name": "data.db"}},
            "plugin_state": {"backend": "file"},
        }

    async def get_own_config(self, timeout: float = 5.0):
        return {"config": {}}

    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0):
        return {"plugins": []}

    async def trigger_plugin_event(self, **kwargs):
        return {"success": True}


@pytest.fixture
def mock_plugin_ctx(tmp_path: Path) -> _MockPluginCtx:
    return _MockPluginCtx(tmp_path / "plugin.toml")


def test_adapter_exports_exist() -> None:
    expected_exports = {
        "AdapterBase",
        "AdapterConfig",
        "AdapterContext",
        "AdapterGatewayCore",
        "AdapterMode",
        "AdapterResponse",
        "NekoAdapterPlugin",
        "RouteRule",
        "RouteTarget",
    }

    assert expected_exports.issubset(set(adapter.__all__))
    for name in expected_exports:
        assert hasattr(adapter, name)


def test_adapter_models_construct() -> None:
    incoming = gm.ExternalRequest(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={})
    req = gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={})
    route = gm.RouteDecision(mode=gm.RouteMode.SELF)
    err = gm.GatewayError(code="E", message="e")
    resp = gm.GatewayResponse(request_id="r", success=False, error=err)
    assert incoming.protocol == "mcp"
    assert req.action == gm.GatewayAction.TOOL_CALL
    assert route.mode == gm.RouteMode.SELF
    assert resp.error is err
    assert isinstance(gm.GatewayErrorException(err), RuntimeError)


def test_adapter_config_from_dict_defaults_invalid_priority() -> None:
    config = adapter.AdapterConfig.from_dict(
        {
            "priority": "high",
            "routes": [{"protocol": "mcp", "action": "tool_call", "ignored": "x"}],
        }
    )

    assert config.priority == 0
    assert len(config.routes) == 1
    assert config.routes[0].protocol == "mcp"


def test_adapter_config_drops_invalid_route_target_and_priority() -> None:
    config = adapter.AdapterConfig.from_dict(
        {
            "routes": [
                {"protocol": "mcp", "action": "tool_call", "target": "plugin", "priority": "2"},
                {"protocol": "http", "action": "invoke", "target": "wat"},
                {"protocol": "ws", "action": "invoke", "priority": "high"},
            ],
        }
    )

    assert len(config.routes) == 1
    assert config.routes[0].target == adapter.RouteTarget.PLUGIN
    assert config.routes[0].priority == 2


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


def test_adapter_event_meta_matches_prefix_actions() -> None:
    wildcard = dec.AdapterEventMeta(protocol="nonebot", action="message.*", pattern=None, priority=0)
    exact = dec.AdapterEventMeta(protocol="nonebot", action="message.group", pattern=None, priority=0)

    assert wildcard.matches(protocol="nonebot", action="message.group") is True
    assert wildcard.matches(protocol="nonebot", action="message.private") is True
    assert wildcard.matches(protocol="nonebot", action="notice.group") is False
    assert exact.matches(protocol="nonebot", action="message.group") is True
    assert exact.matches(protocol="nonebot", action="message.private") is False


@pytest.mark.asyncio
async def test_adapter_context_prefers_call_plugin_entry() -> None:
    calls: list[str] = []

    class _Ctx:
        async def call_plugin_entry(self, *, target_plugin_id: str, entry_id: str, params: dict[str, object], timeout: float = 30.0):
            calls.append(f"entry:{target_plugin_id}:{entry_id}")
            return {"success": True, "params": params}

        async def trigger_plugin_event(self, **kwargs):
            calls.append("trigger")
            return {"success": False}

    ctx = adapter.AdapterContext(adapter_id="a", config=adapter.AdapterConfig(), logger=object(), plugin_ctx=_Ctx())
    result = await ctx.call_plugin("plugin", "entry", {"x": 1})

    assert result.is_ok()
    assert calls == ["entry:plugin:entry"]


@pytest.mark.asyncio
async def test_adapter_gateway_defaults_reject_unknown_action_and_oversized_params() -> None:
    normalizer = adapter.DefaultRequestNormalizer()
    unsupported = await normalizer.normalize(
        gm.ExternalRequest(protocol="mcp", connection_id="c", request_id="r", action="unknown_action", payload={})
    )
    assert unsupported.is_err()

    policy = adapter.DefaultPolicyEngine(max_params_bytes=8)
    request = gm.GatewayRequest(
        request_id="r",
        protocol="mcp",
        action=gm.GatewayAction.TOOL_CALL,
        source_app="a",
        trace_id="t",
        params={"value": "123456789"},
    )
    denied = await policy.authorize(request)
    assert denied.is_err()

    not_serializable = gm.GatewayRequest(
        request_id="r",
        protocol="mcp",
        action=gm.GatewayAction.TOOL_CALL,
        source_app="a",
        trace_id="t",
        params={"value": object()},
    )
    serialization_error = await policy.authorize(not_serializable)
    assert serialization_error.is_err()
    assert isinstance(serialization_error.error, adapter.AuthorizationError)

    class _NestedBad:
        def __str__(self) -> str:
            return "nested-bad"

        __repr__ = __str__

    response = await adapter.DefaultResponseSerializer().build_success_response(
        gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}),
        {"value": _NestedBad()},
        1.0,
    )
    assert response.is_ok()
    assert "nested-bad" in str(response.unwrap().data)


@pytest.mark.asyncio
async def test_adapter_default_implementations(mock_plugin_ctx: _MockPluginCtx) -> None:
    ctx = adapter.AdapterContext(adapter_id="a", config=adapter.AdapterConfig(), logger=object())
    assert ctx.adapter_id == "a"
    base = adapter.AdapterBase(config=adapter.AdapterConfig(), adapter_ctx=ctx)
    assert base.adapter_id == "a"

    class _Transport:
        protocol_name = "mcp"
        async def start(self):
            return adapter.Ok(None)
        async def stop(self):
            return adapter.Ok(None)
        async def recv(self):
            return adapter.Ok(gm.ExternalRequest(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={}))
        async def send(self, response):
            return adapter.Ok(None)

    core = adapter.AdapterGatewayCore(
        transport=_Transport(),
        normalizer=adapter.DefaultRequestNormalizer(),
        policy=adapter.DefaultPolicyEngine(),
        router=adapter.DefaultRouteEngine(),
        invoker=adapter.CallablePluginInvoker(lambda _req, _dec: {}),
        serializer=adapter.DefaultResponseSerializer(),
    )
    assert (await core.start()).is_ok()
    assert (await core.run_once()).is_ok()
    assert (await core.stop()).is_ok()
    assert (await core.handle_request(gm.ExternalRequest(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={}))).is_ok()

    defaults = [
        adapter.DefaultRequestNormalizer(),
        adapter.DefaultPolicyEngine(),
        adapter.DefaultRouteEngine(),
        adapter.DefaultResponseSerializer(),
        adapter.CallablePluginInvoker(lambda _req, _dec: {}),
    ]

    assert (await defaults[0].normalize(gm.ExternalRequest(protocol="mcp", connection_id="c", request_id="r", action="tool_call", payload={}))).is_ok()
    assert (await defaults[1].authorize(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}))).is_ok()
    assert (await defaults[2].decide(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}))).is_ok()
    assert (await defaults[3].build_success_response(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}), {}, 1.0)).is_ok()
    assert (await defaults[3].build_error_response(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}), gm.GatewayError(code="E", message="e"), 1.0)).is_ok()
    assert (await defaults[4].invoke(gm.GatewayRequest(request_id="r", protocol="mcp", action=gm.GatewayAction.TOOL_CALL, source_app="a", trace_id="t", params={}), gm.RouteDecision(mode=gm.RouteMode.SELF))).is_ok()

    neko = adapter.NekoAdapterPlugin(mock_plugin_ctx)
    assert neko.adapter_id == "demo"
    assert neko.adapter_config.mode == adapter.AdapterMode.HYBRID
    assert neko.adapter_mode is not None
    assert (await neko.adapter_startup()).is_ok()
    assert (await neko.adapter_shutdown()).is_ok()
    assert (await neko.register_adapter_tool_as_entry("n", lambda: None)).is_ok()
    assert (await neko.unregister_adapter_tool_entry("n")).is_ok()
    assert neko.list_adapter_routes() == []


def test_adapter_runtime_common_exports() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", adapter.SDK_VERSION)
    assert adapter.Result is not None
    assert adapter.ErrorCode is not None


@pytest.mark.asyncio
async def test_adapter_base_methods_ok() -> None:
    class _Ctx2:
        async def trigger_plugin_event(self, **kwargs):
            return {"success": True}
    ctx = adapter.AdapterContext(adapter_id="a", config=adapter.AdapterConfig(), logger=object(), plugin_ctx=_Ctx2())
    assert (await ctx.call_plugin("p", "e", {})).is_ok()
    ctx.register_event_handler("evt", lambda payload: {"success": True})
    assert (await ctx.broadcast_event("evt", {})).is_ok()
    config = adapter.AdapterConfig()
    base = adapter.AdapterBase(config=config, adapter_ctx=ctx)
    assert base.mode == config.mode
    assert (await base.on_message({"hello": "world"})).unwrap() == {"hello": "world"}
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
    assert hasattr(adapter.DefaultResponseSerializer, "build_success_response")
    assert hasattr(adapter.CallablePluginInvoker, "invoke")
    assert hasattr(adapter.NekoAdapterPlugin, "adapter_config")
    assert hasattr(adapter.NekoAdapterPlugin, "register_adapter_tool_as_entry")


@pytest.mark.asyncio
async def test_adapter_tool_resource_and_route_methods() -> None:
    class _Ctx2:
        async def trigger_plugin_event(self, **kwargs):
            return {"success": True}

    routes: list[adapter.RouteRule] = []
    config = adapter.AdapterConfig(routes=routes)
    ctx = adapter.AdapterContext(adapter_id="a", config=config, logger=object(), plugin_ctx=_Ctx2())
    base = adapter.AdapterBase(config=config, adapter_ctx=ctx)
    assert base.config is not config
    assert base.register_tool("tool", object()) is True
    assert base.register_resource("res", object()) is True
    assert base.get_tool("tool") is not None
    assert base.get_resource("res") is not None
    assert base.list_tools() == ["tool"]
    assert base.list_resources() == ["res"]
    rule = adapter.RouteRule(protocol="mcp", action="tool_call")
    assert base.add_route(rule) is True
    assert base.add_route({"protocol": "http", "action": "invoke", "unknown": "skip"}) is True
    assert routes == []
    assert config.routes is routes
    assert base.list_routes()[0].protocol == rule.protocol
    assert base.list_routes()[0].action == rule.action
    assert base.list_routes()[1].protocol == "http"
    assert base.unregister_tool("tool") is not None
    assert base.unregister_resource("res") is not None
    assert base.list_tools() == []
    assert base.list_resources() == []
    assert (await base.forward_to_plugin("p", "e", {})).is_ok()
    ctx.register_event_handler("evt", lambda payload: {"success": True})
    assert (await base.broadcast("evt", {})).is_ok()


def test_adapter_context_get_event_handlers_filters_by_event_type() -> None:
    ctx = adapter.AdapterContext(adapter_id="a", config=adapter.AdapterConfig(), logger=object(), plugin_ctx=None)
    def first(payload):
        return {"ok": 1}

    def second(payload):
        return {"ok": 2}
    ctx.register_event_handler("evt", first, protocol="mcp", action="evt")
    ctx.register_event_handler("other", second, protocol="mcp", action="evt")

    handlers = ctx.get_event_handlers("evt", protocol="mcp")

    assert handlers == [first]


@pytest.mark.asyncio
async def test_neko_adapter_extra_methods(mock_plugin_ctx: _MockPluginCtx) -> None:
    neko = adapter.NekoAdapterPlugin(mock_plugin_ctx)
    assert neko.register_adapter_tool("tool", object()) is True
    assert neko.register_adapter_resource("res", object()) is True
    assert neko.get_adapter_tool("tool") is not None
    assert neko.get_adapter_resource("res") is not None
    assert neko.list_adapter_tools() == ["tool"]
    assert neko.list_adapter_resources() == ["res"]
    rule = adapter.RouteRule(protocol="mcp", action="tool_call", target=adapter.RouteTarget.PLUGIN, plugin_id="p", entry_id="e", priority=1)
    assert neko.add_adapter_route(rule) is True
    matched = neko.find_matching_route("mcp", "tool_call")
    assert matched is not None
    assert matched.protocol == rule.protocol
    assert matched.action == rule.action
    assert matched.target == rule.target
    assert matched.plugin_id == rule.plugin_id
    assert matched.entry_id == rule.entry_id
    assert matched.priority == rule.priority
    assert neko.add_adapter_route({"protocol": "http", "action": "invoke", "target": adapter.RouteTarget.SELF, "priority": 2}) is True
    normalized = next(item for item in neko.list_adapter_routes() if item.protocol == "http")
    assert hasattr(normalized, "protocol")
    assert hasattr(normalized, "action")
    assert hasattr(normalized, "target")
    assert normalized.action == "invoke"
    wildcard = adapter.RouteRule(
        protocol="nonebot",
        action="*",
        pattern="message.*",
        target=adapter.RouteTarget.BROADCAST,
        priority=3,
    )
    assert neko.add_adapter_route(wildcard) is True
    wildcard_match = neko.find_matching_route("nonebot", "message.group")
    assert wildcard_match is not None
    assert wildcard_match.pattern == "message.*"
    assert (await neko.forward_to_plugin("p", "e", {})).is_ok()
    assert (await neko.handle_adapter_message("mcp", "tool_call", {})).is_ok()


@pytest.mark.asyncio
async def test_neko_adapter_discovers_decorated_members(mock_plugin_ctx: _MockPluginCtx) -> None:
    lifecycle_calls: list[str] = []

    class _DecoratedAdapter(adapter.NekoAdapterPlugin):
        @dec.on_mcp_tool("echo")
        async def echo_tool(self, payload: dict[str, object] | None = None):
            return payload or {}

        @dec.on_mcp_resource("docs")
        async def docs_resource(self, payload: dict[str, object] | None = None):
            return payload or {}

        @dec.on_nonebot_message()
        async def handle_nonebot(self, payload: dict[str, object] | None = None):
            return payload or {}

        @dec.on_adapter_startup(priority=2)
        async def boot(self):
            lifecycle_calls.append("startup")

        @dec.on_adapter_shutdown(priority=1)
        async def stop_adapter(self):
            lifecycle_calls.append("shutdown")

    neko = _DecoratedAdapter(mock_plugin_ctx)
    assert neko.list_adapter_tools() == ["echo"]
    assert neko.list_adapter_resources() == ["docs"]
    handlers = neko.adapter_context.get_event_handlers("message.group", protocol="nonebot")
    assert len(handlers) == 1
    assert getattr(handlers[0], "__name__", "") == "handle_nonebot"
    assert (await neko.adapter_startup()).is_ok()
    assert (await neko.adapter_shutdown()).is_ok()
    assert lifecycle_calls == ["startup", "shutdown"]


def test_neko_adapter_discovery_skips_unrelated_descriptors(mock_plugin_ctx: _MockPluginCtx) -> None:
    class _DescriptorAdapter(adapter.NekoAdapterPlugin):
        @property
        def dangerous(self):
            raise AssertionError("descriptor should not be accessed")

        @dec.on_adapter_startup()
        async def boot(self):
            return None

    plugin = _DescriptorAdapter(mock_plugin_ctx)
    assert plugin.adapter_id == "demo"


@pytest.mark.asyncio
async def test_neko_adapter_register_tool_as_entry_propagates_errors(mock_plugin_ctx: _MockPluginCtx) -> None:
    class _BadDynamicEntryAdapter(adapter.NekoAdapterPlugin):
        async def register_dynamic_entry(self, *args, **kwargs):
            raise RuntimeError("boom")

    class _ErrDynamicEntryAdapter(adapter.NekoAdapterPlugin):
        async def register_dynamic_entry(self, *args, **kwargs):
            return adapter.Err(adapter.TransportError("bad", op_name="adapter.dynamic_entry"))

    class _NoneDynamicEntryAdapter(adapter.NekoAdapterPlugin):
        async def register_dynamic_entry(self, *args, **kwargs):
            return None

    class _FalseDynamicEntryAdapter(adapter.NekoAdapterPlugin):
        async def register_dynamic_entry(self, *args, **kwargs):
            return False

    class _UnsupportedDynamicEntryAdapter(adapter.NekoAdapterPlugin):
        async def register_dynamic_entry(self, *args, **kwargs):
            return object()

    bad_adapter = _BadDynamicEntryAdapter(mock_plugin_ctx)
    raised = await bad_adapter.register_adapter_tool_as_entry("tool", lambda: None)
    assert raised.is_err()
    assert isinstance(raised.error, adapter.TransportError)
    assert bad_adapter.get_adapter_tool("tool") is None

    err_adapter = _ErrDynamicEntryAdapter(mock_plugin_ctx)
    returned = await err_adapter.register_adapter_tool_as_entry("tool", lambda: None)
    assert returned.is_err()
    assert isinstance(returned.error, adapter.TransportError)
    assert err_adapter.get_adapter_tool("tool") is None

    none_adapter = _NoneDynamicEntryAdapter(mock_plugin_ctx)
    registered = await none_adapter.register_adapter_tool_as_entry("tool", lambda: None)
    assert registered.unwrap() is True
    assert none_adapter.get_adapter_tool("tool") is not None

    false_adapter = _FalseDynamicEntryAdapter(mock_plugin_ctx)
    rejected = await false_adapter.register_adapter_tool_as_entry("tool", lambda: None)
    assert rejected.is_err()
    assert isinstance(rejected.error, adapter.TransportError)
    assert false_adapter.get_adapter_tool("tool") is None

    unsupported_adapter = _UnsupportedDynamicEntryAdapter(mock_plugin_ctx)
    unsupported = await unsupported_adapter.register_adapter_tool_as_entry("tool", lambda: None)
    assert unsupported.is_err()
    assert isinstance(unsupported.error, adapter.TransportError)
    assert "unsupported result" in str(unsupported.error)
    assert unsupported_adapter.get_adapter_tool("tool") is None


@pytest.mark.asyncio
async def test_neko_adapter_register_tool_as_entry_validates_name_and_local_registration(mock_plugin_ctx: _MockPluginCtx) -> None:
    class _RegisterRejectingAdapter(adapter.NekoAdapterPlugin):
        def register_adapter_tool(self, name: str, handler: object) -> bool:
            return False

    neko = adapter.NekoAdapterPlugin(mock_plugin_ctx)
    invalid = await neko.register_adapter_tool_as_entry("", lambda: None)
    assert invalid.is_err()
    assert isinstance(invalid.error, adapter.InvalidArgumentError)

    rejected = await _RegisterRejectingAdapter(mock_plugin_ctx).register_adapter_tool_as_entry("tool", lambda: None)
    assert rejected.is_err()
    assert isinstance(rejected.error, adapter.InvalidArgumentError)


@pytest.mark.asyncio
async def test_neko_adapter_lifecycle_normalizes_exception_and_err_results(mock_plugin_ctx: _MockPluginCtx) -> None:
    class _StartupSdkErrorAdapter(adapter.NekoAdapterPlugin):
        @dec.on_adapter_startup()
        async def boot(self):
            raise adapter.InvalidArgumentError("bad-startup")

    class _ShutdownRuntimeErrorAdapter(adapter.NekoAdapterPlugin):
        @dec.on_adapter_shutdown()
        async def stop(self):
            raise RuntimeError("boom")

    class _StartupErrSdkAdapter(adapter.NekoAdapterPlugin):
        @dec.on_adapter_startup()
        async def boot(self):
            return adapter.Err(adapter.InvalidArgumentError("bad-result"))

    class _ShutdownErrRuntimeAdapter(adapter.NekoAdapterPlugin):
        @dec.on_adapter_shutdown()
        async def stop(self):
            return adapter.Err(RuntimeError("wrapped-boom"))

    startup_sdk_error = await _StartupSdkErrorAdapter(mock_plugin_ctx).adapter_startup()
    assert startup_sdk_error.is_err()
    assert isinstance(startup_sdk_error.error, adapter.InvalidArgumentError)

    shutdown_runtime_error = await _ShutdownRuntimeErrorAdapter(mock_plugin_ctx).adapter_shutdown()
    assert shutdown_runtime_error.is_err()
    assert isinstance(shutdown_runtime_error.error, adapter.TransportError)
    assert "boom" in str(shutdown_runtime_error.error)

    startup_err_sdk = await _StartupErrSdkAdapter(mock_plugin_ctx).adapter_startup()
    assert startup_err_sdk.is_err()
    assert isinstance(startup_err_sdk.error, adapter.InvalidArgumentError)

    shutdown_err_runtime = await _ShutdownErrRuntimeAdapter(mock_plugin_ctx).adapter_shutdown()
    assert shutdown_err_runtime.is_err()
    assert isinstance(shutdown_err_runtime.error, adapter.TransportError)
    assert "wrapped-boom" in str(shutdown_err_runtime.error)


@pytest.mark.asyncio
async def test_neko_adapter_handle_message_routes_cover_none_broadcast_and_self(mock_plugin_ctx: _MockPluginCtx) -> None:
    neko = adapter.NekoAdapterPlugin(mock_plugin_ctx)

    no_route = await neko.handle_adapter_message("mcp", "missing", {"x": 1})
    assert no_route.unwrap() is None

    broadcast_rule = adapter.RouteRule(
        protocol="nonebot",
        action="message.group",
        target=adapter.RouteTarget.BROADCAST,
    )
    assert neko.add_adapter_route(broadcast_rule) is True
    neko.adapter_context.register_event_handler(
        "message.group",
        lambda payload: {"seen": payload["x"]},
        protocol="nonebot",
        action="message.group",
    )
    broadcast = await neko.handle_adapter_message("nonebot", "message.group", {"x": 2})
    assert broadcast.unwrap() == {"responses": [{"seen": 2}]}

    erroring = adapter.NekoAdapterPlugin(mock_plugin_ctx)
    assert erroring.add_adapter_route(broadcast_rule) is True

    def _raise_on_broadcast(payload):
        raise RuntimeError("broadcast-failed")

    erroring.adapter_context.register_event_handler(
        "message.group",
        _raise_on_broadcast,
        protocol="nonebot",
        action="message.group",
    )
    failed = await erroring.handle_adapter_message("nonebot", "message.group", {"x": 3})
    assert failed.is_err()
    assert isinstance(failed.error, adapter.TransportError)

    self_route = adapter.NekoAdapterPlugin(mock_plugin_ctx)
    assert self_route.add_adapter_route(
        adapter.RouteRule(protocol="http", action="echo", target=adapter.RouteTarget.SELF)
    ) is True
    echoed = await self_route.handle_adapter_message("http", "echo", {"ok": True})
    assert echoed.unwrap() == {"ok": True}


@pytest.mark.asyncio
async def test_neko_adapter_unregister_tool_entry_preserves_local_tool_on_remote_failure(mock_plugin_ctx: _MockPluginCtx) -> None:
    class _BadUnregisterAdapter(adapter.NekoAdapterPlugin):
        async def unregister_dynamic_entry(self, entry_id: str):
            return adapter.Err(adapter.TransportError("boom", op_name="adapter.dynamic_entry.unregister"))

    class _FalseUnregisterAdapter(adapter.NekoAdapterPlugin):
        async def unregister_dynamic_entry(self, entry_id: str):
            return False

    plugin = _BadUnregisterAdapter(mock_plugin_ctx)
    plugin.register_adapter_tool("tool", object())

    removed = await plugin.unregister_adapter_tool_entry("tool")

    assert removed.is_err()
    assert plugin.get_adapter_tool("tool") is not None

    false_plugin = _FalseUnregisterAdapter(mock_plugin_ctx)
    false_plugin.register_adapter_tool("tool", object())
    failed = await false_plugin.unregister_adapter_tool_entry("tool")
    assert failed.is_err()
    assert false_plugin.get_adapter_tool("tool") is not None


@pytest.mark.asyncio
async def test_neko_adapter_unregister_tool_entry_normalizes_runtime_errors(mock_plugin_ctx: _MockPluginCtx) -> None:
    class _RuntimeUnregisterAdapter(adapter.NekoAdapterPlugin):
        async def unregister_dynamic_entry(self, entry_id: str):
            raise RuntimeError("remote-boom")

    plugin = _RuntimeUnregisterAdapter(mock_plugin_ctx)
    plugin.register_adapter_tool("tool", object())

    removed = await plugin.unregister_adapter_tool_entry("tool")

    assert removed.is_err()
    assert isinstance(removed.error, adapter.TransportError)
    assert plugin.get_adapter_tool("tool") is not None


@pytest.mark.asyncio
async def test_neko_adapter_unregister_tool_entry_clears_local_tool_on_remote_success(mock_plugin_ctx: _MockPluginCtx) -> None:
    class _OkUnregisterAdapter(adapter.NekoAdapterPlugin):
        async def unregister_dynamic_entry(self, entry_id: str):
            return adapter.Ok(False)

    class _NoneUnregisterAdapter(adapter.NekoAdapterPlugin):
        async def unregister_dynamic_entry(self, entry_id: str):
            return None

    plugin = _OkUnregisterAdapter(mock_plugin_ctx)
    plugin.register_adapter_tool("tool", object())

    removed = await plugin.unregister_adapter_tool_entry("tool")

    assert removed.unwrap() is True
    assert plugin.get_adapter_tool("tool") is None

    none_plugin = _NoneUnregisterAdapter(mock_plugin_ctx)
    none_plugin.register_adapter_tool("tool", object())
    none_removed = await none_plugin.unregister_adapter_tool_entry("tool")
    assert none_removed.unwrap() is True
    assert none_plugin.get_adapter_tool("tool") is None
