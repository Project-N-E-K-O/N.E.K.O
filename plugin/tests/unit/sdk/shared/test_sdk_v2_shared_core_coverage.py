from __future__ import annotations

import asyncio
import inspect
import types
from dataclasses import dataclass
from typing import Annotated

import pytest
from pydantic import BaseModel

from plugin.sdk.shared.core import base as core_base
from plugin.sdk.shared.core import bus_context as core_bus_context
from plugin.sdk.shared.core import config as core_config
from plugin.sdk.shared.core import decorators as core_decorators
from plugin.sdk.shared.core import finish as core_finish
from plugin.sdk.shared.core import plugins as core_plugins
from plugin.sdk.shared.core import result_contract as core_result_contract
from plugin.sdk.shared.core import router as core_router
from plugin.sdk.shared.logging import LogLevel


class _CtxOk:
    plugin_id = "demo"
    logger = None

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"data": {"config": {"feature": {"enabled": True}, "leaf": 1}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> dict[str, object]:
        return {"config": updates}

    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> dict[str, object]:
        return {"plugins": [{"plugin_id": "p"}, "skip"]}

    async def trigger_plugin_event(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        return {"success": True, "target": target_plugin_id, "event_type": event_type, "event_id": event_id, "params": params}


class _CtxNoApis:
    plugin_id = "demo"
    logger = None


class _CtxErrConfig(_CtxNoApis):
    async def get_own_config(self, timeout: float = 5.0) -> object:
        raise RuntimeError("boom")

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> object:
        raise RuntimeError("boom")


class _CtxBadQuery(_CtxNoApis):
    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> object:
        return "bad"


class _CtxBadPlugins(_CtxNoApis):
    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> object:
        return {"plugins": "bad"}


class _CtxQueryRaises(_CtxNoApis):
    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> object:
        raise TimeoutError("boom")


class _CtxCallRaises(_CtxNoApis):
    async def trigger_plugin_event(self, **kwargs: object) -> object:
        raise KeyError("boom")


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
    @core_decorators.plugin_entry(id="hello")
    async def hello(self) -> str:
        return "hello"


def test_finish_helpers_normalize_meta_and_structured_data() -> None:
    @dataclass
    class _Payload:
        count: int
        tags: tuple[str, ...]

    class _ModelDumpTypeError:
        def model_dump(self, *, mode: str | None = None):
            if mode is not None:
                raise TypeError("mode unsupported")
            return {"value": 1}

    class _DictOnly:
        def dict(self):
            return {"value": 2}

    envelope = core_finish.build_finish_envelope(
        data={
            "model": _ModelDumpTypeError(),
            "dict_only": _DictOnly(),
            "items": [1, (2, 3)],
            "payload": _Payload(count=4, tags=("x", "y")),
        },
        reply=False,
        trace_id="trace-1",
        meta={
            "source": "test",
            1: "ignored",
            "agent": {"include": True, 2: "ignored"},
        },
    )

    assert envelope["trace_id"] == "trace-1"
    assert envelope["data"] == {
        "model": {"value": 1},
        "dict_only": {"value": 2},
        "items": [1, [2, 3]],
        "payload": {"count": 4, "tags": ["x", "y"]},
    }
    assert envelope["meta"] == {
        "source": "test",
        "agent": {"include": True, "reply": False},
    }


def test_finish_normalize_meta_replaces_non_mapping_agent_meta() -> None:
    envelope = core_finish.build_finish_envelope(data=None, meta={"agent": "bad"})
    assert envelope["meta"] == {"agent": {"reply": True, "include": True}}


def test_core_base_collect_entries_covers_router_collect_entries_and_iter_handler_edges() -> None:
    base = _DemoPlugin(_CtxOk())

    async def with_meta() -> str:
        return "with-meta"

    setattr(
        with_meta,
        core_base.EVENT_META_ATTR,
        core_base.EventMeta(event_type="plugin_entry", id="with_meta", name="With Meta"),
    )

    async def plain_callable() -> str:
        return "plain"

    router_with_collect = _Router(name="collecting")

    def _collect_entries():
        return {
            "already_wrapped": core_base.EventHandler(
                meta=core_base.EventMeta(event_type="plugin_entry", id="already_wrapped", name="Already Wrapped"),
                handler=plain_callable,
            ),
            "with_meta": with_meta,
            "plain_callable": plain_callable,
        }

    router_with_collect.collect_entries = _collect_entries  # type: ignore[attr-defined]

    router_iter_edges = _Router(name="iter-edges")
    router_iter_edges._entries = {
        "skip_me": _RouteRecord(handler=123),
        "iter_meta": _RouteRecord(handler=with_meta),
    }

    base.include_router(router_with_collect)
    base.include_router(router_iter_edges)

    entries = base.collect_entries()

    assert "already_wrapped" in entries
    assert entries["already_wrapped"].meta.id == "already_wrapped"
    assert "with_meta" in entries
    assert entries["with_meta"].meta.id == "with_meta"
    assert "plain_callable" in entries
    assert entries["plain_callable"].meta.id == "plain_callable"
    assert "iter_meta" in entries
    assert entries["iter_meta"].meta.id == "with_meta"
    assert "skip_me" not in entries


def test_result_contract_remaining_helpers_and_validation_paths() -> None:
    class _ModelJsonSchemaOnly:
        @staticmethod
        def model_json_schema() -> dict[str, object]:
            return {"type": "object", "properties": {"title": {"type": "string"}}}

    class _SchemaOnly:
        @staticmethod
        def schema() -> dict[str, object]:
            return {"type": "object", "properties": {"value": {"type": "integer"}}}

    class _NoSchema:
        pass

    class _BrokenSchemaOnly:
        model_fields = ()

        @staticmethod
        def schema() -> dict[str, object]:
            raise TypeError("schema boom")

    class _FallbackField:
        def __init__(self, annotation: object, *, required: bool, default: object = ...) -> None:
            self.annotation = annotation
            self.default = default
            self._required = required

        def is_required(self) -> bool:
            return self._required

    class _BrokenSchemaModel:
        model_fields = {
            "title": _FallbackField(str, required=True),
            "count": _FallbackField(int, required=False, default=3),
        }

        @staticmethod
        def model_json_schema() -> dict[str, object]:
            raise ValueError("broken")

        @staticmethod
        def schema() -> dict[str, object]:
            raise TypeError("also broken")

    class _ParseOnly:
        @staticmethod
        def parse_obj(value: object) -> object:
            if not isinstance(value, dict) or "ok" not in value:
                raise ValueError("bad parse")
            return value

    class _ValidateOnly:
        @staticmethod
        def model_validate(value: object) -> object:
            if not isinstance(value, dict) or "ok" not in value:
                raise ValueError("bad validate")
            return value

    class _CtorOnly:
        def __init__(self, value: int) -> None:
            self.value = value

    class _MetaWithSchemaOnly:
        llm_result_fields = None
        llm_result_schema = {"type": "object", "required": ["title"]}
        llm_result_model = "bad"

    class _MetaWithFields:
        llm_result_fields = [" title ", "summary"]
        llm_result_schema = None
        llm_result_model = None

    assert core_result_contract.model_schema_from_type(_ModelJsonSchemaOnly) == {
        "type": "object",
        "properties": {"title": {"type": "string"}},
    }
    assert core_result_contract.model_schema_from_type(_SchemaOnly) == {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
    }
    assert core_result_contract.model_schema_from_type(_BrokenSchemaModel) == {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "count": {"type": "integer", "default": 3},
        },
        "required": ["title"],
    }
    assert core_result_contract.model_schema_from_type(_BrokenSchemaOnly) is None
    assert core_result_contract.model_schema_from_type(_NoSchema) is None
    assert core_result_contract.normalize_llm_result_fields(None) is None
    with pytest.raises(TypeError, match="sequence of field names"):
        core_result_contract.normalize_llm_result_fields("title")
    with pytest.raises(TypeError, match="only strings"):
        core_result_contract.normalize_llm_result_fields(["title", 1])
    assert core_result_contract.normalize_llm_result_fields(["title", " title ", "", "summary"]) == ["title", "summary"]

    assert core_result_contract.schema_from_fields([]) is None
    assert core_result_contract.schema_from_fields(["title"]) == {
        "type": "object",
        "properties": {"title": {}},
        "required": ["title"],
    }
    assert core_result_contract.fields_from_schema(None) is None
    assert core_result_contract.fields_from_schema({"required": "bad"}) is None
    assert core_result_contract.fields_from_schema({"required": ["title", 1, " summary "]}) == ["title", "summary"]

    direct_contract = core_result_contract.contract_from_meta(_MetaWithFields())
    assert direct_contract.fields == ("title", "summary")
    contract = core_result_contract.contract_from_meta(_MetaWithSchemaOnly())
    assert contract.fields == ("title",)
    assert contract.model is None

    assert core_result_contract._context_text(plugin_id="demo", entry_id="run") == " for demo.run"
    assert core_result_contract._context_text(plugin_id="demo", entry_id=None) == " for demo"
    assert core_result_contract._context_text(plugin_id=None, entry_id="run") == ""

    core_result_contract._validate_model_payload(_ValidateOnly, {"ok": True})
    core_result_contract._validate_model_payload(_ParseOnly, {"ok": True})
    core_result_contract._validate_model_payload(_CtorOnly, {"value": 3})
    core_result_contract._validate_model_payload(_CtorOnly, 4)

    disabled = core_result_contract.LlmResultContract()
    assert disabled.enabled is False
    assert core_result_contract.validate_reply_payload(disabled, "raw", export_type="text") == "raw"

    schema_only = core_result_contract.LlmResultContract(schema={"type": "object"})
    assert schema_only.enabled is True
    with pytest.raises(core_result_contract.LlmResultValidationError, match="must be an object"):
        core_result_contract.validate_reply_payload(schema_only, "bad", plugin_id="demo")
    assert core_result_contract.validate_reply_payload(schema_only, {"ok": True}) == {"ok": True}

    with pytest.raises(core_result_contract.LlmResultValidationError, match="must use JSON payloads"):
        core_result_contract.validate_reply_payload(
            core_result_contract.LlmResultContract(fields=("title",)),
            {"title": "ok"},
            export_type="text",
            plugin_id="demo",
            entry_id="run",
        )

    with pytest.raises(core_result_contract.LlmResultValidationError, match="must be an object containing"):
        core_result_contract.validate_reply_payload(
            core_result_contract.LlmResultContract(fields=("title",)),
            "bad",
        )

    with pytest.raises(core_result_contract.LlmResultValidationError, match="missing required llm_result fields"):
        core_result_contract.validate_reply_payload(
            core_result_contract.LlmResultContract(fields=("title", "summary")),
            {"title": "ok"},
        )
    assert core_result_contract.validate_reply_payload(
        core_result_contract.LlmResultContract(fields=("title",)),
        {"title": "ok"},
    ) == {"title": "ok"}

    with pytest.raises(core_result_contract.LlmResultValidationError, match="does not satisfy llm_result_model"):
        core_result_contract.validate_reply_payload(
            core_result_contract.LlmResultContract(model=_ParseOnly),
            {"bad": True},
            plugin_id="demo",
            entry_id="run",
        )
    assert core_result_contract.validate_reply_payload(
        core_result_contract.LlmResultContract(model=_ValidateOnly),
        {"ok": True},
    ) == {"ok": True}


def test_result_contract_helper_fallback_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RequiredRaises:
        annotation = str
        default = ...

        def is_required(self) -> bool:
            raise RuntimeError("boom")

    class _RequiredAttrOnly:
        annotation = bool
        default = ...
        required = True

    Undefined = type("Undefined", (), {})

    class _UndefinedDefault:
        annotation = float
        default = Undefined()

    class _LegacyField:
        def __init__(self, annotation: object, *, required: bool, default: object = ...) -> None:
            self.outer_type_ = annotation
            self.default = default
            self.required = required

    class _LegacySchemaModel:
        __fields__ = {
            "items": _LegacyField(list[int], required=True),
            "meta": _LegacyField(dict[str, object], required=False, default={"x": 1}),
            "flag": _LegacyField(bool | None, required=False, default=None),
            1: _LegacyField(str, required=True),
        }

    class _InvalidModelFieldsModel:
        model_fields = {
            1: _RequiredAttrOnly(),
        }

    class _ListTyped:
        pass

    annotated_schema = core_result_contract._schema_for_annotation(Annotated[int, "counter"])
    assert annotated_schema == {"type": "integer"}
    assert core_result_contract._schema_for_annotation(str | None) == {"type": ["string", "null"]}
    assert core_result_contract._schema_for_annotation(str | int) == {}
    monkeypatch.setitem(core_result_contract._PY_TYPE_TO_JSON, _ListTyped, ["custom"])
    assert core_result_contract._schema_for_annotation(_ListTyped | None) == {"type": ["custom", "null"]}
    assert core_result_contract._schema_for_annotation(list[str]) == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert core_result_contract._schema_for_annotation(dict[str, object]) == {"type": "object"}
    assert core_result_contract._schema_for_annotation(float) == {"type": "number"}

    assert core_result_contract._is_required_field(_RequiredRaises()) is False
    assert core_result_contract._is_required_field(_RequiredAttrOnly()) is True
    assert core_result_contract._is_required_field(_UndefinedDefault()) is True

    assert core_result_contract._field_annotation(_LegacyField(int, required=True)) is int
    assert core_result_contract._field_default(_LegacyField(int, required=True, default=7)) == 7

    assert core_result_contract._schema_from_declared_fields(_InvalidModelFieldsModel) is None
    assert core_result_contract._schema_from_declared_fields(_LegacySchemaModel) == {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "integer"}},
            "meta": {"type": "object", "default": {"x": 1}},
            "flag": {"type": ["boolean", "null"], "default": None},
        },
        "required": ["items"],
    }


def test_core_base_additional_logger_branches() -> None:
    base = _DemoPlugin(ctx=_CtxOk())

    assert base.logger_component() == "plugin.demo"
    assert base.logger_component("worker") == "plugin.demo.worker"
    assert base.get_logger("worker") is not None
    logger = base.setup_logger(level=LogLevel.INFO, suffix="worker")
    assert logger is not None
    assert base.sdk_logger is base.logger

    root_logger = base.setup_logger(level=None)
    assert root_logger is base.logger
    assert root_logger is base.sdk_logger

    with pytest.raises(ValueError, match="invalid log level"):
        base.setup_logger(level="wat")
    with pytest.raises(ValueError, match="invalid log_level"):
        base.enable_file_logging(log_level="wat")
    with pytest.raises(ValueError, match="max_bytes must be > 0"):
        base.enable_file_logging(max_bytes=0)
    with pytest.raises(ValueError, match="backup_count must be > 0"):
        base.enable_file_logging(backup_count=0)


def test_core_decorators_plugin_proxy_on_shared_module() -> None:
    sentinel = object()

    def fake_plugin_entry(**kwargs: object):
        assert kwargs == {"id": "x"}
        return sentinel

    original = core_decorators.plugin_entry
    core_decorators.plugin_entry = fake_plugin_entry  # type: ignore[assignment]
    try:
        assert core_decorators.plugin.entry(id="x") is sentinel
    finally:
        core_decorators.plugin_entry = original  # type: ignore[assignment]


def test_core_config_static_helpers() -> None:
    # Static helpers are now module-level functions, not class methods
    from plugin.sdk.shared.models.exceptions import ValidationError as CfgValidationError

    assert core_config.unwrap_config_payload({"data": {"config": {"x": 1}}}) == {"x": 1}
    assert core_config.unwrap_config_payload({"config": {"x": 1}}) == {"x": 1}
    with pytest.raises(CfgValidationError):
        core_config.unwrap_config_payload("x")
    with pytest.raises(CfgValidationError):
        core_config.unwrap_config_payload({"config": "x"})
    assert core_config.unwrap_config_payload({"x": 1}) == {"x": 1}

    data = {"a": {"b": 1}}
    assert core_config._get_by_path(data, "") == data
    assert core_config._get_by_path(data, "a.b") == 1
    with pytest.raises(CfgValidationError):
        core_config._get_by_path({"a": 1}, "a.b")
    with pytest.raises(CfgValidationError):
        core_config._get_by_path(data, "a.c")

    assert core_config._set_by_path({"a": 1}, "", {"x": 1}) == {"x": 1}
    with pytest.raises(CfgValidationError):
        core_config._set_by_path({"a": 1}, "", 1)
    assert core_config._set_by_path({"a": 1}, "a.b", 2)["a"]["b"] == 2


@pytest.mark.asyncio
async def test_core_config_error_paths() -> None:
    from plugin.sdk.shared.models.exceptions import ValidationError as _VE, TransportError as _TE
    _CfgError = (_VE, _TE)
    cfg_err = core_config.PluginConfig(_CtxErrConfig())
    with pytest.raises(_CfgError):
        await cfg_err.dump()
    with pytest.raises(_CfgError):
        await cfg_err.set("x", 1)
    with pytest.raises(_CfgError):
        await cfg_err.update({"x": 1})

    cfg_ok = core_config.PluginConfig(_CtxOk())
    with pytest.raises(_CfgError):
        await cfg_ok.dump(timeout=0)
    # get with missing path returns default (None)
    assert (await cfg_ok.get("missing", default=None)) is None
    assert (await cfg_ok.get("missing", default=1)) == 1
    with pytest.raises(_CfgError):
        await cfg_ok.set("", {"root": True})
    with pytest.raises(_CfgError):
        await cfg_ok.set("", 1)


@pytest.mark.asyncio
async def test_core_plugins_error_paths() -> None:
    plugins = core_plugins.Plugins(_CtxNoApis())
    assert (await plugins.list()).is_err()
    assert (await plugins.call(plugin_id="p", event_type="e", event_id="i")).is_err()
    assert (await plugins.list(timeout=0)).is_err()

    assert (await core_plugins.Plugins(_CtxBadQuery()).list()).is_err()
    assert (await core_plugins.Plugins(_CtxBadPlugins()).list()).is_err()
    assert (await core_plugins.Plugins(_CtxQueryRaises()).list()).is_err()
    assert (await core_plugins.Plugins(_CtxCallRaises()).call(plugin_id="p", event_type="e", event_id="i")).is_err()

    ok_plugins = core_plugins.Plugins(_CtxOk())
    listed = await ok_plugins.list()
    assert listed.is_ok()
    assert listed.unwrap() == [{"plugin_id": "p"}]
    assert (await ok_plugins.call_entry("badref")).is_err()
    assert (await ok_plugins.call_event("badref")).is_err()

    class _CtxMissing(_CtxOk):
        async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> dict[str, object]:
            return {"plugins": [{"plugin_id": "x"}]}

    assert (await core_plugins.Plugins(_CtxMissing()).require("p")).is_err()


@pytest.mark.asyncio
async def test_core_router_misc_paths() -> None:
    router = core_router.PluginRouter(prefix="p_", name="named")
    assert router.name() == "named"
    assert router._resolve_entry_id("p_run") == "p_run"
    router.set_prefix("x_")
    assert router._resolve_entry_id("run") == "x_run"
    assert router.iter_handlers() == {}
    assert (await router.add_entry("   ", lambda _payload: None)).is_err()
    assert (await router.add_entry("run", lambda _payload: None, input_schema={"type": "object"})).is_ok()
    assert (await router.add_entry("run", lambda _payload: None, replace=True)).is_ok()
    assert (await router.remove_entry("missing")).unwrap() is False


def test_core_router_binding_and_dependency_branches() -> None:
    router = core_router.PluginRouter(name="named")
    assert router.plugin_id == "named"
    assert router.file_logger is None
    with pytest.raises(core_router.PluginRouterError, match="not bound"):
        _ = router.main_plugin

    calls: list[dict[str, object]] = []

    class _Plugin:
        plugin_id = 123
        file_logger = object()
        shared_dep = "from-plugin"

        def report_status(self, status: dict[str, object]) -> None:
            calls.append(status)

    plugin = _Plugin()
    router._bind(plugin)

    assert router.plugin_id == "123"
    assert router.file_logger is plugin.file_logger
    assert router.main_plugin is plugin
    assert router.get_dependency("shared_dep") == "from-plugin"

    router.report_status({"ready": True})
    assert calls == [{"ready": True}]


def test_core_base_enable_file_logging_branch(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    base = _DemoPlugin(ctx=_CtxOk())
    calls: dict[str, object] = {}

    def _record_and_return_sink(**kwargs):
        calls["kwargs"] = kwargs
        return 123

    monkeypatch.setattr(core_base, "setup_plugin_file_logging", _record_and_return_sink)
    base._file_sink_id = 77

    logger = base.enable_file_logging(log_dir=tmp_path, max_bytes=10, backup_count=2)
    assert logger is base.file_logger
    assert calls["kwargs"]["previous_sink_id"] == 77
    assert base._file_sink_id == 123


@pytest.mark.asyncio
async def test_core_config_remaining_error_paths() -> None:
    from plugin.sdk.shared.models.exceptions import ValidationError as _VE2, TransportError as _TE2
    _CfgErr = (_VE2, _TE2)
    cfg = core_config.PluginConfig(_CtxOk())
    with pytest.raises(_CfgErr):
        await cfg.get("leaf", timeout=0)
    with pytest.raises(_CfgErr):
        await cfg.require("leaf", timeout=0)
    with pytest.raises(_CfgErr):
        await cfg.set("leaf", 1, timeout=0)
    with pytest.raises(_CfgErr):
        await cfg.update({"x": 1}, timeout=0)

    class _CtxUpdateBad(_CtxOk):
        async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> object:
            raise ValueError("bad")

    with pytest.raises(_CfgErr):
        await core_config.PluginConfig(_CtxUpdateBad()).set("x", 1)
    with pytest.raises(_CfgErr):
        await core_config.PluginConfig(_CtxUpdateBad()).update({"x": 1})

    class _CtxDumpBad(_CtxOk):
        async def get_own_config(self, timeout: float = 5.0) -> object:
            return "bad"

    cfg_bad = core_config.PluginConfig(_CtxDumpBad())
    with pytest.raises(_CfgErr):
        await cfg_bad.get("x")
    with pytest.raises(_CfgErr):
        await cfg_bad.require("x")
    with pytest.raises(_CfgErr):
        await cfg_bad.set("x", 1)


def test_sdk_bus_context_missing_namespaces_return_empty_lists() -> None:
    sdk_bus = core_bus_context.SdkBusContext(object(), host_ctx=object())

    messages = sdk_bus.messages.get(conversation_id="c1")
    events = sdk_bus.events.get(event_type="created")
    lifecycle = sdk_bus.lifecycle.get(stage="startup")
    conversations = sdk_bus.conversations.get(topic="demo")
    memory = sdk_bus.memory.get(bucket_id="bucket")

    assert isinstance(messages, core_bus_context.SdkBusList)
    assert isinstance(events, core_bus_context.SdkBusList)
    assert isinstance(lifecycle, core_bus_context.SdkBusList)
    assert isinstance(conversations, core_bus_context.SdkBusList)
    assert isinstance(memory, core_bus_context.SdkBusList)
    assert messages.count() == 0
    assert events.count() == 0
    assert lifecycle.count() == 0
    assert conversations.count() == 0
    assert memory.count() == 0


def test_sdk_bus_context_ensure_returns_safe_context_for_none() -> None:
    sdk_bus = core_bus_context.ensure_sdk_bus_context(None, host_ctx=object())

    assert isinstance(sdk_bus, core_bus_context.SdkBusContext)
    assert sdk_bus.messages.get(conversation_id="c1").count() == 0


def test_sdk_bus_context_memory_wrap_preserves_scalar_items() -> None:
    class _RawBus:
        class memory:
            @staticmethod
            def get(*, bucket_id: str, limit: int = 20, timeout: float = 5.0):
                return ["hello", 3, {"id": "m1", "rev": 2}]

    sdk_bus = core_bus_context.SdkBusContext(_RawBus(), host_ctx=object())
    memory = sdk_bus.memory.get(bucket_id="bucket")

    assert isinstance(memory, core_bus_context.SdkBusList)
    assert [item.payload for item in memory] == [{"value": "hello"}, {"value": 3}, {"id": "m1", "rev": 2}]


def test_sdk_bus_list_filter_combines_callable_and_kwargs() -> None:
    items = core_bus_context.SdkBusList(
        [
            core_bus_context.SdkBusMessageRecord(type="MESSAGE", source="demo", priority=0),
            core_bus_context.SdkBusMessageRecord(type="MESSAGE", source="demo", priority=1),
        ],
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )

    filtered = items.filter(lambda item: item.priority > 0, source="demo")

    assert filtered.count() == 1
    assert filtered[0].priority == 1


def test_sdk_bus_list_filter_rejects_non_callable_predicate() -> None:
    items = core_bus_context.SdkBusList(
        [core_bus_context.SdkBusMessageRecord(type="MESSAGE", source="demo", priority=1)],
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )

    with pytest.raises(TypeError, match="filter predicate must be callable"):
        items.filter("bad", source="demo")


def test_sdk_bus_list_local_filter_respects_strict_flag() -> None:
    items = core_bus_context.SdkBusList(
        [core_bus_context.SdkBusMessageRecord(type="MESSAGE", source="demo", priority=1)],
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )

    relaxed = items.filter(strict=False, priority_min="high")
    assert relaxed.count() == 0

    with pytest.raises(TypeError):
        items.filter(strict=True, priority_min="high")


def test_sdk_bus_list_filter_propagates_raw_type_error_when_signature_matches() -> None:
    class _RawList(list):
        def filter(self, *, strict: bool = True, source: str | None = None):
            raise TypeError("host boom")

    items = core_bus_context.SdkBusList.from_raw(
        _RawList([{"type": "MESSAGE", "source": "demo", "priority": 1}]),
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )

    with pytest.raises(TypeError, match="host boom"):
        items.filter(source="demo")


@pytest.mark.asyncio
async def test_sdk_bus_watcher_subscribe_waits_for_async_watcher_creation() -> None:
    holder: dict[str, object] = {}

    class _RawWatcher:
        def __init__(self) -> None:
            self.handlers: dict[str, list[object]] = {}

        def subscribe(self, *, on: str = "add"):
            def _decorator(fn):
                self.handlers.setdefault(on, []).append(fn)
                return fn

            return _decorator

    async def _build_watcher() -> object:
        await asyncio.sleep(0)
        watcher = _RawWatcher()
        holder["watcher"] = watcher
        return watcher

    watcher = core_bus_context.SdkBusWatcher(
        _build_watcher(),
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )
    seen: list[str] = []

    @watcher.subscribe(on="add")
    def _on_delta(delta: core_bus_context.SdkBusDelta[core_bus_context.SdkBusMessageRecord]) -> None:
        seen.append(delta.kind)

    raw: object | None = None
    for _ in range(5):
        await asyncio.sleep(0)
        raw = holder.get("watcher")
        if isinstance(raw, _RawWatcher) and raw.handlers.get("add"):
            break

    assert isinstance(raw, _RawWatcher)
    assert len(raw.handlers["add"]) == 1

    delta = type(
        "_Delta",
        (),
        {
            "kind": "add",
            "added": [{"type": "MESSAGE", "source": "demo"}],
            "changed": [],
            "removed": [],
            "current": [],
        },
    )()
    raw.handlers["add"][0](delta)
    assert seen == ["add"]


def test_sdk_bus_context_helper_and_record_utilities() -> None:
    assert core_bus_context._mapping_get({"name": "value"}, "name") == "value"
    assert core_bus_context._mapping_get(types.SimpleNamespace(name="attr"), "name") == "attr"
    assert core_bus_context._read_first(types.SimpleNamespace(primary=None, secondary="fallback"), "primary", "secondary") == "fallback"
    assert core_bus_context._as_dict({1: "x"}) == {"1": "x"}
    assert core_bus_context._as_dict(object()) == {}
    assert core_bus_context._as_str(12) == "12"
    assert core_bus_context._as_float(1.5) == 1.5
    assert core_bus_context._as_float("2.5") == 2.5
    assert core_bus_context._as_float("nan?") is None
    assert core_bus_context._as_float(True) is None
    assert core_bus_context._as_int(4) == 4
    assert core_bus_context._as_int("5") == 5
    assert core_bus_context._as_int("bad", default=9) == 9
    assert core_bus_context._as_int(None, default=7) == 7
    assert core_bus_context._iter_raw_items(None) == []

    class _Dumped:
        def dump_records(self) -> list[object]:
            return [{"id": "m1"}]

    class _FallbackIterable(list):
        def dump_records(self) -> object:
            return "not-a-list"

    assert core_bus_context._iter_raw_items(_Dumped()) == [{"id": "m1"}]
    assert core_bus_context._iter_raw_items(_FallbackIterable([1, 2])) == [1, 2]
    assert core_bus_context._iter_raw_items("not-iterable-here") == []
    assert core_bus_context._iter_raw_items(object()) == []

    message = core_bus_context.SdkBusMessageRecord.from_raw(
        {
            "type": "NOTICE",
            "time": "10.5",
            "plugin_id": 3,
            "source": "demo",
            "priority": "2",
            "content": 99,
            "metadata": {1: "meta"},
            "id": "msg-1",
            "description": "desc",
        }
    )
    assert message.dump()["metadata"] == {"1": "meta"}
    assert message.key() == "msg-1"
    assert message.version() == 10

    event = core_bus_context.SdkBusEventRecord.from_raw(
        {
            "event_type": "created",
            "received_at": 12,
            "trace_id": "evt-1",
            "entry_id": 7,
            "args": {"x": 1},
        }
    )
    assert event.dump()["args"] == {"x": 1}
    assert event.key() == "evt-1"
    assert event.version() == 12

    lifecycle = core_bus_context.SdkBusLifecycleRecord.from_raw(
        {
            "type": "startup",
            "at": "8",
            "trace_id": "life-1",
            "detail": {"phase": "boot"},
        }
    )
    assert lifecycle.dump()["detail"] == {"phase": "boot"}
    assert lifecycle.key() == "life-1"
    assert lifecycle.version() == 8

    conversation = core_bus_context.SdkBusConversationRecord.from_raw(
        {
            "message_type": "chat",
            "time": 3,
            "metadata": {"turn_type": "assistant", "lanlan_name": "Lan"},
            "messageCount": "4",
        }
    )
    assert conversation.dump()["lanlan_name"] == "Lan"
    assert conversation.key() == "Lan:3.0"
    assert conversation.version() == 3

    memory = core_bus_context.SdkBusMemoryRecord.from_raw({"id": "mem-1", "rev": "6"})
    assert memory.dump() == {"id": "mem-1", "rev": "6"}
    assert memory.key() == "mem-1"
    assert memory.version() == 6

    fallback_memory = core_bus_context.SdkBusMemoryRecord(payload={"value": "x"})
    assert fallback_memory.key().startswith("{'value': 'x'}")
    assert fallback_memory.version() is None


def test_sdk_bus_list_helper_paths_and_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RawList(list):
        def __init__(self, values: list[dict[str, object]]) -> None:
            super().__init__(values)
            self.watch_calls: list[tuple[object, str | None, float]] = []

        def explain(self) -> int:
            return 123

        def trace_tree_dump(self) -> str:
            return "branch"

        def filter(self, *, strict: bool = True, source: str | None = None):
            return _RawList([item for item in self if source is None or item.get("source") == source])

        def where_in(self, field: str, values: object):
            accepted = set(values)
            return _RawList([item for item in self if item.get(field) in accepted])

        def limit(self, size: int):
            return _RawList(list(self[:size]))

        def __add__(self, other: object):
            return _RawList([*self, *list(other)])

        def __and__(self, other: object):
            other_ids = {item.get("id") for item in list(other)}
            return _RawList([item for item in self if item.get("id") in other_ids])

        def watch(self, host_ctx: object, *, bus: str | None = None, debounce_ms: float = 0.0):
            self.watch_calls.append((host_ctx, bus, debounce_ms))
            return types.SimpleNamespace(start=lambda: None, stop=lambda: None)

    raw_items = _RawList(
        [
            {"type": "MESSAGE", "id": "m1", "source": "demo", "priority": 1},
            {"type": "MESSAGE", "id": "m2", "source": "other", "priority": 3},
        ]
    )
    items = core_bus_context.SdkBusList.from_raw(
        raw_items,
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )

    assert len(items) == 2
    assert items.size() == 2
    assert items.dump_records() == items.dump()
    assert items.explain() == "123"
    assert items.trace_tree_dump() == {"trace": "branch"}
    assert items.where(lambda item: item.priority > 1).count() == 1
    assert items.filter(lambda item: item.priority >= 1).count() == 2
    assert items.filter(source="demo").count() == 1
    assert items._local_filter({"strict": False, "source": "demo"}).count() == 1
    assert items._local_filter({"priority_min": 5}).count() == 0
    assert items._local_filter({"priority_max": 0}).count() == 0
    assert items._local_filter({"priority_max": 3, "source": "demo"}).count() == 1
    assert items._local_filter({"source": "missing"}).count() == 0
    assert items.where_in("source", ["demo"]).count() == 1
    assert items.limit(1).count() == 1

    ctx_wrapper = types.SimpleNamespace(_host_ctx="wrapped-host")
    watcher = items.watch(ctx_wrapper, bus="events", debounce_ms=12.5)
    assert isinstance(watcher, core_bus_context.SdkBusWatcher)
    assert raw_items.watch_calls == [("wrapped-host", "events", 12.5)]

    plain_items = core_bus_context.SdkBusList(
        ["plain-value"],
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )
    assert plain_items.dump() == [{"value": "plain-value"}]
    assert plain_items.explain() == "SdkBusList(namespace='messages', count=1)"
    assert plain_items.trace_tree_dump() == {"namespace": "messages", "count": 1}
    assert plain_items.where_in("source", ["demo"]).count() == 0
    assert plain_items.limit(5).count() == 1

    with pytest.raises(TypeError, match="watch\\(\\) is not available"):
        plain_items.watch()

    class _LocalOnlyRaw(list):
        def filter(self, source: str | None = None):
            return self

    local_only_items = core_bus_context.SdkBusList.from_raw(
        _LocalOnlyRaw([{"type": "MESSAGE", "id": "m3", "source": "demo", "priority": 2}]),
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )
    assert local_only_items.filter(priority_min=1).count() == 1

    assert core_bus_context.SdkBusList._raw_filter_accepts_kwargs(lambda **kwargs: None, {"source": "demo"}) is True

    def _raise_value_error(_: object) -> inspect.Signature:
        raise ValueError("boom")

    monkeypatch.setattr(core_bus_context.inspect, "signature", _raise_value_error)
    assert core_bus_context.SdkBusList._raw_filter_accepts_kwargs(lambda: None, {"source": "demo"}) is True


def test_sdk_bus_list_operator_paths_and_logging() -> None:
    messages: list[str] = []

    class _Logger:
        def debug(self, message: str) -> None:
            messages.append(message)

    class _BrokenLogger:
        def debug(self, message: str) -> None:
            raise RuntimeError(message)

    class _FailingRaw(list):
        def __add__(self, other: object):
            raise RuntimeError("add boom")

        def __and__(self, other: object):
            raise RuntimeError("and boom")

    good_left = core_bus_context.SdkBusList.from_raw(
        [
            {"type": "MESSAGE", "id": "m1", "source": "demo", "priority": 1},
            {"type": "MESSAGE", "id": "m2", "source": "demo", "priority": 2},
        ],
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )
    good_right = core_bus_context.SdkBusList.from_raw(
        [
            {"type": "MESSAGE", "id": "m2", "source": "demo", "priority": 2},
            {"type": "MESSAGE", "id": "m3", "source": "demo", "priority": 3},
        ],
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )
    assert [item.message_id for item in (good_left + good_right)] == ["m1", "m2", "m2", "m3"]
    assert [item.message_id for item in (good_left & good_right)] == ["m2"]

    left = core_bus_context.SdkBusList.from_raw(
        _FailingRaw(
            [
                {"type": "MESSAGE", "id": "m1", "source": "demo", "priority": 1},
                {"type": "MESSAGE", "id": "m2", "source": "demo", "priority": 2},
            ]
        ),
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=types.SimpleNamespace(logger=_Logger()),
    )
    right = core_bus_context.SdkBusList.from_raw(
        _FailingRaw(
            [
                {"type": "MESSAGE", "id": "m2", "source": "demo", "priority": 2},
                {"type": "MESSAGE", "id": "m3", "source": "demo", "priority": 3},
            ]
        ),
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=types.SimpleNamespace(logger=_Logger()),
    )
    assert [item.message_id for item in (left + right)] == ["m1", "m2", "m3"]
    assert [item.message_id for item in (left & right)] == ["m2"]
    assert any("__add__" in message for message in messages)
    assert any("__and__" in message for message in messages)

    obj = object()
    assert core_bus_context.SdkBusList._dedupe_key(obj) == str(obj)
    core_bus_context.SdkBusList([], namespace="messages", record_factory=core_bus_context.SdkBusMessageRecord, host_ctx=object())._log_fallback_error(
        "noop",
        RuntimeError("x"),
    )
    core_bus_context.SdkBusList(
        [],
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=types.SimpleNamespace(logger=_BrokenLogger()),
    )._log_fallback_error("noop", RuntimeError("x"))


def test_sdk_bus_watcher_sync_paths() -> None:
    calls: list[str] = []

    class _RawWatcher:
        async def start(self) -> None:
            calls.append("start")

        async def stop(self) -> None:
            calls.append("stop")

    async def _build_watcher() -> object:
        return _RawWatcher()

    watcher = core_bus_context.SdkBusWatcher(
        _build_watcher(),
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )
    assert isinstance(asyncio.run(watcher._await_raw_watcher()), _RawWatcher)

    idle = core_bus_context.SdkBusWatcher(
        object(),
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )
    idle._raw_watcher = None
    idle._raw_watcher_task = None
    assert asyncio.run(idle._await_raw_watcher()) is None

    watcher._run_async_call(object())

    async def _mark() -> None:
        calls.append("marked")

    watcher._run_async_call(_mark())
    watcher.start()
    watcher.stop()
    assert calls == ["marked", "start", "stop"]


@pytest.mark.asyncio
async def test_sdk_bus_watcher_async_subscribe_and_invoke_paths() -> None:
    events: list[str] = []

    class _ImmediateWatcher:
        def __init__(self) -> None:
            self.handler = None

        async def subscribe(self, *, on: str = "add"):
            async def _apply(fn):
                self.handler = fn

            return _apply

    immediate = _ImmediateWatcher()
    watcher = core_bus_context.SdkBusWatcher(
        immediate,
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )

    @watcher.subscribe(on="add")
    def _on_immediate(delta: core_bus_context.SdkBusDelta[core_bus_context.SdkBusMessageRecord]) -> None:
        events.append(delta.kind)

    await asyncio.sleep(0)
    assert callable(immediate.handler)
    immediate.handler(
        types.SimpleNamespace(
            kind="add",
            added=[{"type": "MESSAGE", "id": "m1"}],
            changed=[],
            removed=[],
            current=[],
        )
    )
    assert events == ["add"]

    class _SyncSubscribeWatcher:
        def __init__(self) -> None:
            self.handler = None

        def subscribe(self, *, on: str = "add"):
            def _apply(fn):
                self.handler = fn

                async def _done() -> None:
                    return None

                return _done()

            return _apply

    sync_subscribe = _SyncSubscribeWatcher()
    sync_watcher = core_bus_context.SdkBusWatcher(
        sync_subscribe,
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )

    @sync_watcher.subscribe(on="add")
    def _on_sync(delta: core_bus_context.SdkBusDelta[core_bus_context.SdkBusMessageRecord]) -> None:
        events.append(f"sync:{delta.kind}")

    await asyncio.sleep(0)
    assert callable(sync_subscribe.handler)
    sync_subscribe.handler(
        types.SimpleNamespace(
            kind="sync",
            added=[],
            changed=[],
            removed=[],
            current=[],
        )
    )
    assert "sync:sync" in events

    class _DeferredWatcher:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.handler = None

        async def start(self) -> None:
            self.calls.append("start")

        async def stop(self) -> None:
            self.calls.append("stop")

        async def subscribe(self, *, on: str = "add"):
            async def _apply(fn):
                self.handler = fn

            return _apply

    async def _build_deferred() -> object:
        await asyncio.sleep(0)
        return _DeferredWatcher()

    deferred = core_bus_context.SdkBusWatcher(
        _build_deferred(),
        namespace="messages",
        record_factory=core_bus_context.SdkBusMessageRecord,
        host_ctx=object(),
    )
    deferred.start()

    @deferred.subscribe(on="add")
    def _on_deferred(delta: core_bus_context.SdkBusDelta[core_bus_context.SdkBusMessageRecord]) -> None:
        events.append(f"deferred:{delta.kind}")

    await asyncio.sleep(0)
    raw = await deferred._await_raw_watcher()
    assert raw is not None
    await asyncio.sleep(0)
    assert isinstance(raw, _DeferredWatcher)
    assert raw.calls == ["start"]
    assert callable(raw.handler)
    raw.handler(
        types.SimpleNamespace(
            kind="change",
            added=[],
            changed=[{"type": "MESSAGE", "id": "m2"}],
            removed=["m1"],
            current=[{"type": "MESSAGE", "id": "m2"}],
        )
    )
    deferred.stop()
    await asyncio.sleep(0)
    assert raw.calls == ["start", "stop"]
    assert events[-1] == "deferred:change"


@pytest.mark.asyncio
async def test_sdk_bus_namespace_and_context_paths() -> None:
    err = core_bus_context.Err(RuntimeError("boom"))
    host_ctx = object()
    messages_bus = core_bus_context.SdkMessagesBus(object(), host_ctx=host_ctx)
    assert messages_bus._wrap_result(err) is err
    wrapped_ok = messages_bus._wrap_result(core_bus_context.Ok([{"type": "MESSAGE", "id": "m1"}]))
    assert isinstance(wrapped_ok, core_bus_context.Ok)
    assert isinstance(wrapped_ok.value, core_bus_context.SdkBusList)
    assert messages_bus._wrap_result({"raw": True}) == {"raw": True}

    class _AsyncEvents:
        async def get(self, **kwargs: object):
            return [{"event_type": "created", "id": "evt-1"}]

    events_bus = core_bus_context.SdkEventsBus(_AsyncEvents(), host_ctx=host_ctx)
    async_events = await events_bus.get(event_type="created")
    assert isinstance(async_events, core_bus_context.SdkBusList)
    assert async_events[0].event_id == "evt-1"

    class _RawConversations:
        def get_by_id(self, conversation_id: str, *, max_count: int = 10, timeout: float = 5.0):
            return [{"conversation_id": conversation_id, "lanlan_name": "Lan"}]

    class _AsyncConversations:
        async def get_by_id(self, conversation_id: str, *, max_count: int = 10, timeout: float = 5.0):
            return [{"conversation_id": conversation_id, "lanlan_name": "Lan"}]

    class _FallbackConversations:
        def get(self, **kwargs: object):
            return [{"conversation_id": kwargs["conversation_id"], "lanlan_name": "Lan"}]

    sync_conversations = core_bus_context.SdkConversationsBus(_RawConversations(), host_ctx=host_ctx)
    assert sync_conversations.get_by_id("c1")[0].conversation_id == "c1"

    async_conversations = core_bus_context.SdkConversationsBus(_AsyncConversations(), host_ctx=host_ctx)
    resolved_async = await async_conversations.get_by_id("c2")
    assert resolved_async[0].conversation_id == "c2"

    fallback_conversations = core_bus_context.SdkConversationsBus(_FallbackConversations(), host_ctx=host_ctx)
    assert fallback_conversations.get_by_id("c3")[0].conversation_id == "c3"

    memory_bus = core_bus_context.SdkMemoryBus(object(), host_ctx=host_ctx)
    assert memory_bus._wrap_result(err) is err
    wrapped_memory_ok = memory_bus._wrap_result(core_bus_context.Ok([{"id": "mem-1"}]))
    assert isinstance(wrapped_memory_ok, core_bus_context.Ok)
    assert isinstance(wrapped_memory_ok.value, core_bus_context.SdkBusList)
    assert memory_bus._wrap_result({"raw": True}) == {"raw": True}

    class _AsyncMemory:
        async def get(self, *, bucket_id: str, limit: int = 20, timeout: float = 5.0):
            return [{"id": bucket_id, "rev": 1}]

    async_memory = core_bus_context.SdkMemoryBus(_AsyncMemory(), host_ctx=host_ctx)
    resolved_memory = await async_memory.get(bucket_id="bucket")
    assert isinstance(resolved_memory, core_bus_context.SdkBusList)
    assert resolved_memory[0].payload["id"] == "bucket"

    sdk_bus = core_bus_context.SdkBusContext(
        types.SimpleNamespace(messages=object(), events=object(), lifecycle=object(), conversations=object(), memory=object()),
        host_ctx=host_ctx,
    )
    assert core_bus_context.ensure_sdk_bus_context(sdk_bus, host_ctx=host_ctx) is sdk_bus


@pytest.mark.asyncio
async def test_core_plugins_remaining_paths() -> None:
    ok_plugins = core_plugins.Plugins(_CtxOk())
    assert (await ok_plugins.call(plugin_id="p", event_type="e", event_id="i", timeout=0)).is_err()

    class _CtxListErr(_CtxNoApis):
        async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> object:
            raise ValueError("bad")

    assert (await core_plugins.Plugins(_CtxListErr()).require("p")).is_err()


@pytest.mark.asyncio
async def test_core_config_extended_async_methods() -> None:
    class _CtxProfiles(_CtxOk):
        async def get_own_base_config(self, timeout: float = 5.0) -> dict[str, object]:
            return {"config": {"base": True}}

        async def get_own_profiles_state(self, timeout: float = 5.0) -> dict[str, object]:
            return {"data": {"active": "dev", "files": {"dev": "profiles/dev.toml"}}}

        async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> dict[str, object]:
            return {"data": {"config": {"profile": profile_name}}}

        async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0) -> dict[str, object]:
            return {"config": {"effective": profile_name}}

    cfg = core_config.PluginConfig(_CtxProfiles())
    assert (await cfg.base_dump()) == {"base": True}
    assert (await cfg.profile_state()) == {"active": "dev", "files": {"dev": "profiles/dev.toml"}}
    assert (await cfg.profile_get("dev")) == {"profile": "dev"}
    dumped = await cfg.dump()
    assert dumped["feature"]["enabled"] is True
    assert (await cfg.profile_effective("dev")) == {"effective": "dev"}


@pytest.mark.asyncio
async def test_core_config_extended_async_method_error_paths() -> None:
    cfg = core_config.PluginConfig(_CtxNoApis())
    with pytest.raises(Exception):
        await cfg.base_dump()
    with pytest.raises(Exception):
        await cfg.profile_state()
    with pytest.raises(Exception):
        await cfg.profile_get("dev")
    with pytest.raises(Exception):
        await cfg.profile_effective("dev")

    with pytest.raises(Exception):
        await cfg.profile_get(" ")
    with pytest.raises(Exception):
        await cfg.profile_effective(" ")

    class _CtxBad(_CtxNoApis):
        async def get_own_base_config(self, timeout: float = 5.0):
            return "bad"
        async def get_own_profiles_state(self, timeout: float = 5.0):
            return "bad"
        async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
            return "bad"
        async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0):
            return "bad"

    bad = core_config.PluginConfig(_CtxBad())
    with pytest.raises(Exception):
        await bad.base_dump()
    with pytest.raises(Exception):
        await bad.profile_state()
    with pytest.raises(Exception):
        await bad.profile_get("dev")
    with pytest.raises(Exception):
        await bad.profile_effective("dev")

    class _CtxProfileNone(_CtxNoApis):
        async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
            return {"data": {"config": None}}

    assert (await core_config.PluginConfig(_CtxProfileNone()).profile_get("dev")) == {}

    class _CtxProfileBadCfg(_CtxNoApis):
        async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
            return {"data": {"config": "bad"}}

    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxProfileBadCfg()).profile_get("dev")


@pytest.mark.asyncio
async def test_core_config_extended_method_remaining_branches() -> None:
    cfg = core_config.PluginConfig(_CtxNoApis())
    with pytest.raises(Exception):
        await cfg.base_dump(timeout=0)
    with pytest.raises(Exception):
        await cfg.profile_state(timeout=0)
    with pytest.raises(Exception):
        await cfg.profile_get("dev", timeout=0)
    with pytest.raises(Exception):
        await cfg.profile_effective("dev", timeout=0)

    class _CtxRaise(_CtxNoApis):
        async def get_own_base_config(self, timeout: float = 5.0):
            raise RuntimeError("boom")
        async def get_own_profiles_state(self, timeout: float = 5.0):
            raise RuntimeError("boom")
        async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
            raise RuntimeError("boom")
        async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0):
            raise RuntimeError("boom")

    raised = core_config.PluginConfig(_CtxRaise())
    with pytest.raises(Exception):
        await raised.base_dump()
    with pytest.raises(Exception):
        await raised.profile_state()
    with pytest.raises(Exception):
        await raised.profile_get("dev")
    with pytest.raises(Exception):
        await raised.profile_effective("dev")

    class _CtxBadProfiles(_CtxNoApis):
        async def get_own_profiles_state(self, timeout: float = 5.0):
            return "bad"

    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxBadProfiles()).profile_state()

    class _CtxBadProfilePayload(_CtxNoApis):
        async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
            return "bad"

    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxBadProfilePayload()).profile_get("dev")

    with pytest.raises(Exception):
        await cfg.profile_get(" ")
    with pytest.raises(Exception):
        await cfg.profile_effective(" ")


class _CtxProfilesWrite(_CtxOk):
    async def get_own_base_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"config": {"feature": {"enabled": True}}}

    def __init__(self) -> None:
        self._profiles_state = {"config_profiles": {"active": "dev", "files": {"dev": {"path": "profiles/dev.toml"}, "prod": {"path": "profiles/prod.toml"}}}}
        self._profiles = {"dev": {"feature": {"enabled": True}}, "prod": {"feature": {"enabled": False}}}

    async def get_own_profiles_state(self, timeout: float = 5.0) -> dict[str, object]:
        return {"data": self._profiles_state}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> dict[str, object]:
        return {"data": {"config": self._profiles.get(profile_name, {})}}

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0) -> dict[str, object]:
        name = profile_name or self._profiles_state["config_profiles"]["active"]
        return {"config": self._profiles.get(name, {})}

    async def upsert_own_profile_config(self, profile_name: str, config: dict[str, object], *, make_active: bool = False, timeout: float = 10.0) -> dict[str, object]:
        self._profiles[profile_name] = dict(config)
        files = self._profiles_state.setdefault("config_profiles", {}).setdefault("files", {})
        files[profile_name] = {"path": f"profiles/{profile_name}.toml"}
        if make_active:
            self._profiles_state["config_profiles"]["active"] = profile_name
        return {"data": {"config": self._profiles[profile_name]}}

    async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0) -> dict[str, object]:
        removed = profile_name in self._profiles
        self._profiles.pop(profile_name, None)
        self._profiles_state["config_profiles"]["files"].pop(profile_name, None)
        if self._profiles_state["config_profiles"].get("active") == profile_name:
            self._profiles_state["config_profiles"]["active"] = None
        return {"removed": removed}

    async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0) -> dict[str, object]:
        self._profiles_state["config_profiles"]["active"] = profile_name
        return self._profiles_state


@pytest.mark.asyncio
async def test_core_config_new_template_views() -> None:
    cfg = core_config.PluginConfig(_CtxProfilesWrite())
    # PluginConfigBaseView and PluginConfigProfiles are now aliases to PluginConfig
    assert isinstance(cfg, core_config.PluginConfigBaseView)
    assert isinstance(cfg, core_config.PluginConfigProfiles)

    assert (await cfg.base_dump())["feature"]["enabled"] is True
    assert (await cfg.get_bool("feature.enabled")) is True
    state = await cfg.profile_state()
    assert state["config_profiles"]["active"] == "dev"
    assert (await cfg.profile_list()) == ["dev", "prod"]
    assert (await cfg.profile_active()) == "dev"
    assert (await cfg.profile_get("dev"))["feature"]["enabled"] is True
    effective_prod = await cfg.profile_effective("prod")
    assert effective_prod["feature"]["enabled"] is False
    assert (await cfg.get_bool("feature.enabled")) is True
    with pytest.raises(Exception):
        await cfg.get_int("feature.enabled")


@pytest.mark.asyncio
async def test_core_config_profile_write_paths() -> None:
    ctx = _CtxProfilesWrite()
    cfg = core_config.PluginConfig(ctx)
    created = await cfg.profile_create("qa", {"feature": {"enabled": True}})
    assert created["feature"]["enabled"] is True
    activated = await cfg.profile_activate("qa")
    assert activated is True
    await cfg.set("feature.flag", True)
    updated = await cfg.update({"feature": {"mode": "fast"}})
    assert updated["feature"]["mode"] == "fast"
    profile_updated = await cfg.profile_update("qa", {"feature": {"x": 1}})
    assert profile_updated["feature"]["x"] == 1
    deleted = await cfg.profile_delete("qa")
    assert deleted is True


@pytest.mark.asyncio
async def test_core_config_profile_error_paths() -> None:
    cfg = core_config.PluginConfig(_CtxProfilesWrite())
    with pytest.raises(Exception):
        await cfg.profile_get(" ")
    with pytest.raises(Exception):
        await cfg.profile_effective(" ")
    with pytest.raises(Exception):
        await cfg.profile_create(" ", {})

    class _NoProfileApis(_CtxNoApis):
        async def get_own_config(self, timeout: float = 5.0):
            return {"config": {"x": 1}}
        async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0):
            return {"config": updates}

    fallback = core_config.PluginConfig(_NoProfileApis())
    with pytest.raises(Exception):
        await fallback.set("x", 1)
    with pytest.raises(Exception):
        await fallback.update({"x": 1})

    class _NoActive(_CtxProfilesWrite):
        def __init__(self) -> None:
            super().__init__()
            self._profiles_state["config_profiles"]["active"] = None

    ensured = core_config.PluginConfig(_NoActive())
    assert (await ensured.profile_ensure_active("runtime", {"x": 1})) == "runtime"
    assert (await ensured.profile_active()) == "runtime"

    no_active = core_config.PluginConfig(_NoActive())
    with pytest.raises(Exception):
        await no_active.set("x", 1)
    with pytest.raises(Exception):
        await no_active.update({"x": 1})


@pytest.mark.asyncio
async def test_core_config_typed_getters_cover_string_list_none_and_error_paths() -> None:
    class _CtxTyped(_CtxNoApis):
        async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
            return {"config": {"name": "effective-demo", "items": ["x"], "maybe": None}}

        async def get_own_base_config(self, timeout: float = 5.0) -> dict[str, object]:
            return {"config": {"name": "demo", "items": [1, 2], "maybe": None}}

        async def get_own_profiles_state(self, timeout: float = 5.0) -> dict[str, object]:
            return {"data": {"config_profiles": {"active": "dev", "files": {"dev": {"path": "profiles/dev.toml"}}}}}

        async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> dict[str, object]:
            return {"data": {"config": {"name": "profile-demo", "items": ["x"], "maybe": None}}}

        async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0) -> dict[str, object]:
            return {"config": {"name": "effective-demo", "items": ["x"], "maybe": None}}

    cfg = core_config.PluginConfig(_CtxTyped())

    assert (await cfg.base_get("name")) == "demo"
    assert (await cfg.get_str("name")) == "effective-demo"
    assert (await cfg.get_str("maybe")) is None


@pytest.mark.asyncio
async def test_core_config_profiles_cover_remaining_branch_edges() -> None:
    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxProfilesWrite()).profile_ensure_active(" ")
    assert (await core_config.PluginConfig(_CtxProfilesWrite()).profile_ensure_active("dev")) == "dev"
    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxNoApis()).profile_ensure_active("dev")
    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxNoApis()).profile_delete("dev")
    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxNoApis()).profile_activate("dev")

    class _CtxExistsErr(_CtxNoApis):
        def __init__(self) -> None:
            self._calls = 0

        async def get_own_profiles_state(self, timeout: float = 5.0):
            self._calls += 1
            if self._calls == 1:
                return {"data": {"config_profiles": {"active": None, "files": {}}}}
            return "bad"

    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxExistsErr()).profile_ensure_active("dev")

    class _CtxCreateErr(_CtxProfilesWrite):
        def __init__(self) -> None:
            super().__init__()
            self._profiles_state["config_profiles"]["active"] = None
            self._profiles = {}

        async def upsert_own_profile_config(self, profile_name: str, config: dict[str, object], *, make_active: bool = False, timeout: float = 10.0) -> dict[str, object]:
            raise RuntimeError("create boom")

    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxCreateErr()).profile_ensure_active("runtime", {"x": 1})

    class _CtxActivateErr(_CtxProfilesWrite):
        def __init__(self) -> None:
            super().__init__()
            self._profiles_state["config_profiles"]["active"] = None

        async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0) -> dict[str, object]:
            raise RuntimeError("activate boom")

    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxActivateErr()).profile_ensure_active("dev")

    class _CtxActivateFalse(_CtxProfilesWrite):
        def __init__(self) -> None:
            super().__init__()
            self._profiles_state["config_profiles"]["active"] = None

        async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0) -> dict[str, object]:
            return {"config_profiles": {"active": None, "files": {"dev": {"path": "profiles/dev.toml"}}}}

    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxActivateFalse()).profile_ensure_active("dev")

    class _CtxActivateSuccess(_CtxProfilesWrite):
        def __init__(self) -> None:
            super().__init__()
            self._profiles_state["config_profiles"]["active"] = None

    assert (await core_config.PluginConfig(_CtxActivateSuccess()).profile_ensure_active("dev")) == "dev"

    class _CtxCurrentEffectiveErr(_CtxProfilesWrite):
        async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0) -> dict[str, object]:
            return "bad"  # type: ignore[return-value]

    with pytest.raises(Exception):
        await core_config.PluginConfig(_CtxCurrentEffectiveErr()).profile_effective("test")


def test_core_decorators_internal_helper_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Model:
        pass

    assert core_decorators._unwrap_model_annotation(Annotated[_Model, "model"]) is _Model
    assert core_decorators._unwrap_model_annotation(_Model | None) is _Model
    assert core_decorators._unwrap_model_annotation(int | str) is None
    assert core_decorators._schema_for_hint(Annotated[list[int], "items list"]) == {
        "description": "items list",
        "type": "array",
        "items": {"type": "integer"},
    }
    assert core_decorators._schema_for_hint(dict[str, int]) == {"type": "object"}

    def _variadic(*items: int, name: str, enabled: bool = False, **kwargs: object) -> None:
        return None

    schema = core_decorators._infer_schema_from_func(_variadic)
    assert schema == {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "enabled": {"type": "boolean", "default": False},
        },
        "required": ["name"],
    }

    monkeypatch.setattr(
        core_decorators,
        "get_type_hints",
        lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("boom")),
    )
    assert core_decorators._get_type_hints_safe(_variadic) == {}
    assert core_decorators._get_type_hints_for_owner_safe(_variadic, _Model) == {}


def test_core_decorators_schema_union_edge_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    original = core_decorators._schema_for_hint
    outer_union = object()
    inner_list_union = object()
    ambiguous_union = object()

    def _fake_get_origin(hint: object):
        if hint in {outer_union, inner_list_union, ambiguous_union}:
            return core_decorators.types.UnionType
        return None

    def _fake_get_args(hint: object):
        if hint is outer_union:
            return (inner_list_union, type(None))
        if hint is inner_list_union:
            return ()
        if hint is ambiguous_union:
            return (int, str)
        return ()

    def _fake_schema_for_hint(hint: object):
        if hint is inner_list_union:
            return {"type": ["integer"]}
        return original(hint)

    monkeypatch.setattr(core_decorators, "get_origin", _fake_get_origin)
    monkeypatch.setattr(core_decorators, "get_args", _fake_get_args)
    monkeypatch.setattr(core_decorators, "_schema_for_hint", _fake_schema_for_hint)

    assert original(outer_union) == {"type": ["integer", "null"]}
    assert original(ambiguous_union) == {}


def test_core_decorators_inference_finalize_and_validation_edges() -> None:
    def _plain() -> None:
        return None

    event_meta = core_decorators.EventMeta(event_type="evt", id="evt", persist=True)
    hook_meta = core_decorators.HookDecoratorMeta(target="x", timing="after", priority=1, condition="ready")
    wrapped_event = core_decorators._attach_event_meta(_plain, event_meta)
    wrapped_hook = core_decorators._attach_hook_meta(_plain, hook_meta)
    assert getattr(wrapped_event, core_decorators.PERSIST_ATTR) is True
    assert getattr(wrapped_hook, core_decorators.HOOK_META_ATTR).timing == "after"

    def _only_variadic(*items) -> None:
        return None

    class _ParamA:
        @staticmethod
        def model_json_schema() -> dict[str, object]:
            return {"type": "object", "properties": {"a": {"type": "string"}}}

    class _ParamB:
        @staticmethod
        def model_json_schema() -> dict[str, object]:
            return {"type": "object", "properties": {"b": {"type": "string"}}}

    class MissingType:
        pass

    def _multiple(a: _ParamA, b: _ParamB) -> None:
        return None

    def _untyped(value: "MissingType") -> None:
        return None

    assert core_decorators._infer_single_params_model_from_signature(
        core_decorators.inspect.signature(_only_variadic),
        {},
    ) == (None, None)
    assert core_decorators._infer_single_params_model_from_signature(
        core_decorators.inspect.signature(_multiple),
        {"a": _ParamA, "b": _ParamB},
    ) == (None, None)
    assert core_decorators._infer_single_params_model_from_signature(
        core_decorators.inspect.signature(_untyped),
        {},
    ) == (None, None)
    assert core_decorators._infer_llm_result_model_from_signature(
        core_decorators.inspect.signature(_plain),
        {},
    ) == (None, None)

    class _Owner:
        class Result:
            @staticmethod
            def model_json_schema() -> dict[str, object]:
                return {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}

        def run(self) -> "Result":
            return self.Result()

    inferred_model, inferred_schema = core_decorators._infer_llm_result_model_from_owner(_Owner.run, _Owner)
    assert inferred_model is _Owner.Result
    assert inferred_schema is not None
    assert inferred_schema["type"] == "object"

    with pytest.raises(ValueError, match="sequence of field names"):
        core_decorators.plugin_entry(llm_result_fields="bad")(_plain)  # type: ignore[arg-type]

    class _NoSchemaModel:
        pass

    with pytest.raises(ValueError, match="must provide model_json_schema"):
        core_decorators.plugin_entry(llm_result_model=_NoSchemaModel)(_plain)

    @core_decorators.neko_plugin
    class _Plugin:
        class Result(BaseModel):
            title: str
            summary: str

        @core_decorators.plugin_entry(_localns={})
        def run(self) -> "Result":
            return self.Result(title="demo", summary="ok")

    meta = getattr(_Plugin.run, core_decorators.EVENT_META_ATTR)
    assert meta.llm_result_model is _Plugin.Result
    assert meta.llm_result_fields == ["title", "summary"]
