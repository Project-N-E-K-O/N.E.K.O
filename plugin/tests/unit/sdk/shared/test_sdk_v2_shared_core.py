from __future__ import annotations

from dataclasses import dataclass, fields

import pytest
from pydantic import BaseModel

from plugin.sdk.shared import core
from plugin.sdk.shared.core import base as core_base
from plugin.sdk.shared.core import config as core_config
from plugin.sdk.shared.core.context import SdkContext
from plugin.sdk.shared.models.exceptions import TransportError, ValidationError
from plugin.sdk.shared.core import decorators as core_decorators
from plugin.sdk.shared.core import events as core_events
from plugin.sdk.shared.core import hook_executor as core_hook_executor
from plugin.sdk.shared.core import hooks as core_hooks
from plugin.sdk.shared.core import plugins as core_plugins
from plugin.sdk.shared.core import result_contract as core_result_contract
from plugin.sdk.shared.core import router as core_router
from plugin.sdk.shared.core import types as core_types
from plugin.sdk.shared.models.exceptions import CapabilityUnavailableError, TransportError


def test_core_exports_exist() -> None:
    for name in core.__all__:
        assert hasattr(core, name)


def test_core_types_and_dataclasses() -> None:
    pref = core_types.PluginRef(plugin_id="p")
    eref = core_types.EntryRef(plugin_id="p", entry_id="e")
    vref = core_types.EventRef(plugin_id="p", event_type="entry", event_id="e")
    assert pref.plugin_id == "p"
    assert eref.entry_id == "e"
    assert vref.event_type == "entry"

    meta = core_events.EventMeta(event_type="entry", id="run")
    handler = core_events.EventHandler(meta=meta, handler=lambda: None)
    hmeta = core_hooks.HookMeta()
    pmeta = core_base.PluginMeta(id="p", name="Plugin")

    assert handler.meta.id == "run"
    assert hmeta.timing == "before"
    assert pmeta.sdk_conflicts == []
    assert [item.name for item in fields(core_base.PluginMeta)] == [
        "id",
        "name",
        "version",
        "sdk_version",
        "description",
        "short_description",
        "keywords",
        "passive",
        "sdk_recommended",
        "sdk_supported",
        "sdk_untested",
        "sdk_conflicts",
    ]


def test_core_constants() -> None:
    assert core_base.NEKO_PLUGIN_META_ATTR == "__neko_plugin_meta__"
    assert core_base.NEKO_PLUGIN_TAG == "__neko_plugin__"
    assert core_events.EVENT_META_ATTR == "__neko_event_meta__"
    assert core_hooks.HOOK_META_ATTR == "__neko_hook_meta__"
    assert core_decorators.PERSIST_ATTR == "_neko_persist"
    assert not hasattr(core_decorators, "CHECKPOINT_ATTR")
    assert not hasattr(core_decorators, "EventDecoratorMeta")


class _CoreCtx:
    plugin_id = "demo"

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"config": {"feature": {"enabled": True}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> dict[str, object]:
        return {"config": updates}

    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> dict[str, object]:
        return {"plugins": [{"plugin_id": "p", "name": "Plugin"}]}

    async def trigger_plugin_event(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        return {"success": True, "target_plugin_id": target_plugin_id, "event_type": event_type, "event_id": event_id, "params": params}


@dataclass(slots=True)
class _RouteRecord:
    handler: object


class _Router:
    def __init__(self, name: str = "router") -> None:
        self._name = name
        self._prefix = ""
        self._entries: dict[str, _RouteRecord] = {}

    def name(self) -> str:
        return self._name

    def set_prefix(self, prefix: str) -> None:
        self._prefix = prefix

    def iter_handlers(self) -> dict[str, object]:
        return {entry_id: record.handler for entry_id, record in self._entries.items()}


class _DemoPlugin(core_base.NekoPluginBase):
    @property
    def input_schema(self) -> dict[str, str]:
        return {"type": "object"}

    @core_decorators.plugin_entry(id="hello")
    async def hello(self) -> str:
        return "hello"

    async def plain(self) -> str:
        return "plain"


@pytest.mark.asyncio
async def test_sdk_context_forwards_update_own_config() -> None:
    ctx = SdkContext(_CoreCtx())

    payload = await ctx.update_own_config({"mcp_servers": {"fetch": {"url": "https://example.com"}}})

    assert payload == {"config": {"mcp_servers": {"fetch": {"url": "https://example.com"}}}}


def test_core_base_and_hook_classes() -> None:
    base = _DemoPlugin(ctx=_CoreCtx())
    assert base.ctx.plugin_id == "demo"
    assert base.config is not None
    assert base.plugins is not None
    assert base.get_input_schema() == {"type": "object"}

    router = _Router(name="r1")
    base.include_router(router, prefix="x_")
    assert router._prefix == "x_"
    assert base.exclude_router("r1") is True
    assert base.exclude_router("r1") is False

    async def from_router() -> str:
        return "router"

    router2 = _Router(name="r2")
    router2._entries = {"routed": _RouteRecord(handler=from_router)}
    base.include_router(router2)
    entries = base.collect_entries()
    assert "hello" in entries
    assert "routed" in entries
    assert callable(entries["hello"].handler)
    assert callable(entries["routed"].handler)
    assert "plain" not in entries

    logger = base.enable_file_logging(log_level="DEBUG")
    assert logger is base.file_logger

    hooks = object.__new__(core_hook_executor.HookExecutorMixin)
    with pytest.raises(NotImplementedError):
        hooks.__init_hook_executor__()


@pytest.mark.asyncio
async def test_hook_executor_methods_raise() -> None:
    hooks = object.__new__(core_hook_executor.HookExecutorMixin)
    with pytest.raises(NotImplementedError):
        await hooks.run_before_hooks("x", {})
    with pytest.raises(NotImplementedError):
        await hooks.run_after_hooks("x", {}, result=None)
    with pytest.raises(NotImplementedError):
        await hooks.run_around_hooks("x", {}, call_next=lambda: None)


@pytest.mark.asyncio
async def test_core_config_plugins_router_behaviors() -> None:
    ctx = _CoreCtx()
    cfg = core_config.PluginConfig(ctx)
    plugins = core_plugins.Plugins(ctx)
    router = core_router.PluginRouter(prefix="x_")

    # Config now raises exceptions instead of returning Result
    dumped = await cfg.dump()
    assert dumped["feature"]["enabled"] is True

    got = await cfg.get("feature.enabled")
    assert got is True

    with pytest.raises((ValidationError, TransportError)):
        await cfg.require("feature.missing")

    with pytest.raises((ValidationError, TransportError)):
        await cfg.set("feature.flag", True)

    with pytest.raises((ValidationError, TransportError)):
        await cfg.update({"a": 1})

    listed = await plugins.list()
    assert listed.is_ok()
    assert listed.unwrap()[0]["plugin_id"] == "p"

    called_entry = await plugins.call_entry("p:run", {"x": 1})
    assert called_entry.is_ok()
    assert called_entry.unwrap()["event_type"] == "plugin_entry"

    called_event = await plugins.call_event("p:event:run", {"x": 1})
    assert called_event.is_ok()
    assert called_event.unwrap()["event_type"] == "event"

    required = await plugins.require("p")
    assert required.is_ok()
    missing_plugin = await plugins.require("missing")
    assert missing_plugin.is_err()

    add_ok = await router.add_entry("run", lambda _payload: None)
    assert add_ok.is_ok()
    add_dup = await router.add_entry("run", lambda _payload: None)
    assert add_dup.is_err()
    entries = await router.list_entries()
    assert entries.is_ok()
    assert entries.unwrap()[0].id == "x_run"
    removed = await router.remove_entry("run")
    assert removed.is_ok()
    assert removed.unwrap() is True


def test_core_error_types() -> None:
    assert isinstance(core_config.PluginConfigError("e"), RuntimeError)
    assert isinstance(core_config.ConfigPathError("e"), RuntimeError)
    assert isinstance(core_config.ConfigValidationError("e"), RuntimeError)
    assert isinstance(core_plugins.PluginCallError("e"), RuntimeError)
    assert isinstance(core_plugins.InvalidEntryRefError("e"), RuntimeError)
    assert isinstance(core_plugins.InvalidEventRefError("e"), RuntimeError)
    assert isinstance(core_router.PluginRouterError("e"), RuntimeError)
    assert isinstance(core_router.EntryConflictError("e"), RuntimeError)

    call_error = core_plugins.PluginCallError(
        "boom",
        op_name="plugins.call_entry_json",
        entry_ref="demo:run",
        plugin_id="demo",
        timeout=5.0,
    )
    assert call_error.context["op_name"] == "plugins.call_entry_json"
    assert call_error.context["entry_ref"] == "demo:run"
    assert call_error.context["plugin_id"] == "demo"
    assert call_error.context["timeout"] == 5.0

    config_error = core_config.PluginConfigError(
        "config failed",
        op_name="get_own_config",
        getter_name="get_own_config",
        profile_name="dev",
        timeout=3.0,
    )
    assert config_error.context["op_name"] == "get_own_config"
    assert config_error.context["getter_name"] == "get_own_config"
    assert config_error.context["profile_name"] == "dev"
    assert config_error.context["timeout"] == 3.0

    transport_error = TransportError(
        "transport failed",
        op_name="message_plane.request",
        capability="plugin_ctx.message_plane_request",
        topic="demo.topic",
        timeout=6.0,
    )
    assert transport_error.context["op_name"] == "message_plane.request"
    assert transport_error.context["capability"] == "plugin_ctx.message_plane_request"
    assert transport_error.context["topic"] == "demo.topic"
    assert transport_error.context["timeout"] == 6.0

    capability_error = CapabilityUnavailableError(
        "missing",
        op_name="memory.query",
        capability="plugin_ctx.query_memory",
        bucket_id="bucket-1",
        timeout=2.0,
    )
    assert capability_error.context["op_name"] == "memory.query"
    assert capability_error.context["capability"] == "plugin_ctx.query_memory"
    assert capability_error.context["bucket_id"] == "bucket-1"
    assert capability_error.context["timeout"] == 2.0


def test_core_plugin_ref_parsers() -> None:
    entry_ref = core_plugins.parse_entry_ref("p:e")
    assert entry_ref.plugin_id == "p"
    assert entry_ref.entry_id == "e"

    event_ref = core_plugins.parse_event_ref("p:entry:run")
    assert event_ref.plugin_id == "p"
    assert event_ref.event_type == "entry"
    assert event_ref.event_id == "run"

    with pytest.raises(core_plugins.InvalidEntryRefError) as entry_error:
        core_plugins.parse_entry_ref("invalid")
    assert entry_error.value.context["entry_ref"] == "invalid"
    assert entry_error.value.context["op_name"] == "plugins.parse_entry_ref"
    with pytest.raises(core_plugins.InvalidEntryRefError):
        core_plugins.parse_entry_ref("p:")

    with pytest.raises(core_plugins.InvalidEventRefError) as event_error:
        core_plugins.parse_event_ref("invalid")
    assert event_error.value.context["event_ref"] == "invalid"
    assert event_error.value.context["op_name"] == "plugins.parse_event_ref"
    with pytest.raises(core_plugins.InvalidEventRefError):
        core_plugins.parse_event_ref("p::run")


def test_core_decorators_behaviors() -> None:
    with pytest.raises(ValueError, match="event_type must be non-empty"):
        core_decorators.on_event(event_type=" ")

    @core_decorators.on_event(event_type="evt", id="a1")
    def fn() -> None:
        return None

    meta = getattr(fn, core_decorators.EVENT_META_ATTR)
    assert meta.id == "a1"
    assert meta.kind == "action"
    assert meta.model_validate is True

    with pytest.raises(ValueError, match="entry id must be non-empty"):
        core_decorators.plugin_entry(id=" ")(lambda: None)

    with pytest.raises(ValueError, match="seconds must be > 0"):
        core_decorators.timer_interval(id="t", seconds=0)

    with pytest.raises(ValueError, match="timing must be one of"):
        core_decorators.hook(timing="x")

    @core_decorators.timer_interval(id="tick", seconds=5)
    def on_tick() -> None:
        return None

    timer_meta = getattr(on_tick, core_decorators.EVENT_META_ATTR)
    assert timer_meta.extra["mode"] == "interval"
    assert timer_meta.extra["seconds"] == 5
    assert timer_meta.metadata["seconds"] == 5

    @core_decorators.custom_event(event_type="audit", id="manual_audit", trigger_method="manual")
    def on_audit() -> None:
        return None

    custom_meta = getattr(on_audit, core_decorators.EVENT_META_ATTR)
    assert custom_meta.event_type == "audit"
    assert custom_meta.kind == "custom"
    assert custom_meta.extra["trigger_method"] == "manual"
    assert custom_meta.metadata["trigger_method"] == "manual"


def test_core_plugin_entry_auto_infers_and_accepts_model_schema() -> None:
    class _ParamsModel:
        @staticmethod
        def model_json_schema() -> dict[str, object]:
            return {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }

    @core_decorators.plugin_entry()
    def inferred(name: str = "world", retries: int = 1) -> None:
        return None

    inferred_meta = getattr(inferred, core_decorators.EVENT_META_ATTR)
    assert inferred_meta.input_schema == {
        "type": "object",
        "properties": {
            "name": {"type": "string", "default": "world"},
            "retries": {"type": "integer", "default": 1},
        },
    }

    @core_decorators.plugin_entry(params=_ParamsModel)
    def modeled() -> None:
        return None

    modeled_meta = getattr(modeled, core_decorators.EVENT_META_ATTR)
    assert modeled_meta.params is _ParamsModel
    assert modeled_meta.input_schema == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }


def test_core_plugin_entry_requires_keyword_arguments() -> None:
    with pytest.raises(TypeError):
        core_decorators.plugin_entry("hello")


def test_core_plugin_entry_rejects_input_schema_and_params_together() -> None:
    class _ParamsModel:
        @staticmethod
        def model_json_schema() -> dict[str, object]:
            return {"type": "object"}

    with pytest.raises(ValueError, match="mutually exclusive"):
        core_decorators.plugin_entry(input_schema={"type": "object"}, params=_ParamsModel)


def test_core_plugin_entry_supports_llm_result_contracts() -> None:
    @core_decorators.plugin_entry(llm_result_fields=["title", "summary", "title"])
    def by_fields() -> None:
        return None

    by_fields_meta = getattr(by_fields, core_decorators.EVENT_META_ATTR)
    assert by_fields_meta.llm_result_fields == ["title", "summary"]
    assert by_fields_meta.llm_result_schema == {
        "type": "object",
        "properties": {
            "title": {},
            "summary": {},
        },
        "required": ["title", "summary"],
    }

    class _ResultModel(BaseModel):
        title: str
        summary: str

    @core_decorators.plugin_entry(llm_result_model=_ResultModel)
    def by_model() -> None:
        return None

    by_model_meta = getattr(by_model, core_decorators.EVENT_META_ATTR)
    assert by_model_meta.llm_result_model is _ResultModel
    assert by_model_meta.llm_result_fields == ["title", "summary"]
    assert by_model_meta.llm_result_schema is not None


def test_result_contract_uses_required_fields_only() -> None:
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": [" title ", "summary", ""],
    }
    assert core_result_contract.fields_from_schema(schema) == ["title", "summary"]
    assert core_result_contract.fields_from_schema(
        {
            "type": "object",
            "properties": {"title": {"type": "string"}},
        },
    ) is None


def test_result_contract_schema_without_required_does_not_force_optional_fields() -> None:
    class _Meta:
        llm_result_fields = None
        llm_result_schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
            },
        }
        llm_result_model = None

    contract = core_result_contract.contract_from_meta(_Meta())
    assert contract.fields == ()
    assert core_result_contract.validate_reply_payload(
        contract,
        {"title": "demo"},
        export_type="json",
    ) == {"title": "demo"}


def test_result_contract_normalizes_explicit_llm_result_fields() -> None:
    class _Meta:
        llm_result_fields = (" title ", "summary", "title")
        llm_result_schema = None
        llm_result_model = None

    contract = core_result_contract.contract_from_meta(_Meta())
    assert contract.fields == ("title", "summary")


def test_core_plugin_entry_auto_infers_params_and_result_models_from_annotations() -> None:
    class _ParamsModel(BaseModel):
        query: str
        count: int = 5

    class _ResultModel(BaseModel):
        title: str
        summary: str

    @core_decorators.plugin_entry()
    async def search(params: _ParamsModel) -> _ResultModel:
        return _ResultModel(title="demo", summary=params.query)

    meta = getattr(search, core_decorators.EVENT_META_ATTR)
    assert meta.params is _ParamsModel
    assert meta.input_schema is not None
    assert meta.input_schema["properties"]["query"]["type"] == "string"
    assert meta.llm_result_model is _ResultModel
    assert meta.llm_result_fields == ["title", "summary"]


def test_core_plugin_entry_rejects_multiple_llm_result_contract_forms() -> None:
    class _ResultModel(BaseModel):
        title: str

    with pytest.raises(ValueError, match="mutually exclusive"):
        core_decorators.plugin_entry(llm_result_fields=["title"], llm_result_model=_ResultModel)


def test_core_shortcuts_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def fake_hook(*, target: str = "*", timing: str = "before", priority: int = 0, condition: str | None = None):
        called.append(f"{timing}:{target}:{priority}:{condition}")

        def decorator(fn):
            return fn

        return decorator

    monkeypatch.setattr(core_decorators, "hook", fake_hook)

    def fn() -> str:
        return "x"

    assert core_decorators.before_entry(priority=1, condition="a")(fn) is fn
    assert core_decorators.after_entry(priority=2, condition="b")(fn) is fn
    assert core_decorators.around_entry(priority=3, condition="c")(fn) is fn
    assert core_decorators.replace_entry(priority=4, condition="d")(fn) is fn

    assert called == [
        "before:*:1:a",
        "after:*:2:b",
        "around:*:3:c",
        "replace:*:4:d",
    ]


def test_router_convenience_accessors() -> None:
    router = core_router.PluginRouter(prefix="x_", tags=["a"], name="r")
    assert router.prefix == "x_"
    router.prefix = "y_"
    assert router.prefix == "y_"
    assert router.tags == ["a"]
    assert router.is_bound is False
    assert router.entry_ids == []

    base = core_base.NekoPluginBase(_CoreCtx())
    base.include_router(router)
    assert router.is_bound is True
    assert router.plugin_id == "demo"
    assert router.ctx is base.ctx
    assert router.config is base.config
    assert router.plugins is base.plugins
    assert router.logger is base.logger
    assert router.store is base.store
    assert router.db is base.db
    assert router.get_plugin_attr("ctx") is base.ctx
    assert router.has_plugin_attr("config") is True
    assert router.get_dependency("plugins") is base.plugins
    assert router.collect_entries() == {}
    assert router.on_mount() is None
    assert router.on_unmount() is None
    assert base.exclude_router(router) is True
    assert router.is_bound is False
