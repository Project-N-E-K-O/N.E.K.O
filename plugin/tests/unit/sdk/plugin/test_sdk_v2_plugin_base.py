from __future__ import annotations

import importlib
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path
from typing import ClassVar

import pytest
import plugin.sdk.plugin as plugin_api

from plugin.sdk.plugin import runtime
from plugin.sdk.plugin.base import (
    DEFAULT_PLUGIN_VERSION,
    NEKO_PLUGIN_META_ATTR,
    NEKO_PLUGIN_TAG,
    EventMeta,
    NekoPluginBase,
    PluginMeta,
)
from plugin.sdk.plugin.decorators import plugin_entry
from plugin.sdk.shared.constants import SDK_VERSION


class _Ctx:
    plugin_id = "demo"
    logger = None

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or Path(tempfile.gettempdir()) / "demo" / "plugin.toml"
        self.metadata = {"role": "demo"}
        self.bus = {"messages": "bus"}
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": True, "name": "data.db"}},
            "plugin_state": {"backend": "file"},
        }
        self.run_updates: list[dict[str, object]] = []
        self.exports: list[dict[str, object]] = []
        self.pushed_messages: list[dict[str, object]] = []

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"config": {"feature": {"enabled": True}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> dict[str, object]:
        return {"config": updates}

    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> dict[str, object]:
        return {"plugins": [{"plugin_id": "demo", "name": "Demo"}]}

    async def trigger_plugin_event(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        return {"target_plugin_id": target_plugin_id, "event_type": event_type, "event_id": event_id, "params": params}

    async def get_system_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"config": {"server": {"mode": "test"}}}

    async def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0) -> dict[str, object]:
        return {"bucket_id": bucket_id, "query": query}

    async def run_update_async(self, **kwargs: object) -> dict[str, object]:
        self.run_updates.append(dict(kwargs))
        return {"ok": True}

    async def export_push_async(self, **kwargs: object) -> dict[str, object]:
        self.exports.append(dict(kwargs))
        return {"ok": True}

    def push_message(self, **kwargs: object) -> dict[str, object]:
        self.pushed_messages.append(dict(kwargs))
        return {"ok": True}


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


class _DemoPlugin(NekoPluginBase):
    input_schema: ClassVar[dict[str, str]] = {"type": "object"}

    @plugin_entry(id="hello")
    async def hello(self) -> str:
        return "hello"

    async def plain(self) -> str:
        return "plain"


class _RichPlugin(NekoPluginBase):
    @plugin_entry(
        id="typed",
        description="typed entry",
        timeout=12.0,
        metadata={"group": "demo"},
    )
    async def typed(self, name: str = "world", enabled: bool = True) -> str:
        return name if enabled else "disabled"


@pytest.fixture(scope="module")
def plugin_api_module():
    return importlib.reload(plugin_api)


def test_base_constants_and_meta_defaults() -> None:
    assert NEKO_PLUGIN_META_ATTR == "__neko_plugin_meta__"
    assert NEKO_PLUGIN_TAG == "__neko_plugin__"

    meta = PluginMeta(id="p", name="Plugin")
    assert meta.version == DEFAULT_PLUGIN_VERSION
    assert meta.sdk_version == SDK_VERSION
    assert meta.sdk_recommended is None
    assert meta.sdk_conflicts == []


def test_plugin_meta_conflicts_default_factory_isolated() -> None:
    a = PluginMeta(id="a", name="A")
    b = PluginMeta(id="b", name="B")
    a.sdk_conflicts.append("x")
    assert b.sdk_conflicts == []


def test_plugin_meta_fields_shape() -> None:
    names = [f.name for f in fields(PluginMeta)]
    assert names == [
        "id",
        "name",
        "version",
        "sdk_version",
        "description",
        "sdk_recommended",
        "sdk_supported",
        "sdk_untested",
        "sdk_conflicts",
    ]


def test_neko_plugin_base_class_defaults() -> None:
    assert NekoPluginBase.__freezable__ == []
    assert NekoPluginBase.__persist_mode__ == "off"


def test_neko_plugin_base_init_wires_ctx_config_plugins() -> None:
    base = _DemoPlugin(ctx=_Ctx())
    assert base.ctx.plugin_id == "demo"
    assert base.store is not None
    assert base.db is not None
    assert base.state is not None
    assert isinstance(base._routers, list)
    assert base.config is not None
    assert base.plugins is not None


def test_get_input_schema_returns_dict_or_empty() -> None:
    base = _DemoPlugin(ctx=_Ctx())
    assert base.get_input_schema() == {"type": "object"}

    class _NoSchema(NekoPluginBase):
        pass

    no_schema = _NoSchema(ctx=_Ctx())
    assert no_schema.get_input_schema() == {}


def test_include_exclude_router_with_prefix_and_name() -> None:
    base = _DemoPlugin(ctx=_Ctx())
    router = _Router(name="r1")
    seen: list[str] = []

    def _bind(plugin) -> None:
        seen.append(f"bind:{plugin.plugin_id}")

    def _unbind() -> None:
        seen.append("unbind")

    router._bind = _bind  # type: ignore[attr-defined]
    router._unbind = _unbind  # type: ignore[attr-defined]

    base.include_router(router, prefix="pre_")
    assert router._prefix == "pre_"
    assert seen.count("bind:demo") == 1
    assert base.exclude_router("r1") is True
    assert seen.count("unbind") == 1
    assert base.exclude_router("r1") is False

    base.include_router(router)
    assert seen.count("bind:demo") == 2
    assert base.exclude_router(router) is True
    assert seen.count("unbind") == 2
    assert base.exclude_router(router) is False


def test_collect_entries_merges_method_entries_and_router_entries() -> None:
    base = _DemoPlugin(ctx=_Ctx())

    async def from_router() -> str:
        return "router"

    router = _Router(name="r")
    router._entries = {"routed": _RouteRecord(handler=from_router)}
    base.include_router(router)
    entries = base.collect_entries()
    assert "hello" in entries
    assert "routed" in entries
    assert callable(entries["hello"].handler)
    assert callable(entries["routed"].handler)
    assert "plain" not in entries


def test_enable_file_logging_sets_file_logger_attribute() -> None:
    base = _DemoPlugin(ctx=_Ctx())
    logger = base.enable_file_logging(log_level="DEBUG")
    assert logger is base.file_logger


def test_logger_helpers_are_wired() -> None:
    base = _DemoPlugin(ctx=_Ctx())
    assert base.logger is not None
    assert base.sdk_logger is base.logger
    assert base.logger_component() == "plugin.demo"
    assert base.logger_component("worker") == "plugin.demo.worker"

    child = base.get_logger("worker")
    assert child is not None

    configured = base.setup_logger(level="INFO", suffix="worker")
    assert configured is not None


def test_enable_file_logging_keeps_default_logger_synced() -> None:
    base = _DemoPlugin(ctx=_Ctx())
    logger = base.enable_file_logging(log_level="DEBUG")
    assert logger is base.file_logger
    assert logger is base.logger
    assert logger is base.sdk_logger


def test_plugin_base_convenience_accessors(tmp_path) -> None:
    config_path = tmp_path / "demo" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    base = _DemoPlugin(ctx=_Ctx(config_path=config_path))
    assert base.plugin_id == "demo"
    assert base.metadata == {"role": "demo"}
    assert base.bus is not None
    assert base.bus.messages.get().count() == 0
    assert base.config_dir == tmp_path / "demo"
    assert base.data_path() == tmp_path / "demo" / "data"
    assert base.data_path("cache", "x.json") == tmp_path / "demo" / "data" / "cache" / "x.json"


def test_plugin_base_runtime_facades_are_lazy_and_cached() -> None:
    base = _DemoPlugin(ctx=_Ctx())

    memory_1 = base.memory
    memory_2 = base.memory
    system_info_1 = base.system_info
    system_info_2 = base.system_info

    assert memory_1 is memory_2
    assert system_info_1 is system_info_2
    assert isinstance(memory_1, runtime.MemoryClient)
    assert isinstance(system_info_1, runtime.SystemInfo)


def test_plugin_base_router_and_static_ui_convenience(tmp_path) -> None:
    ctx = _Ctx(config_path=tmp_path / "plugin.toml")
    ctx.config_path.parent.mkdir(parents=True, exist_ok=True)
    (ctx.config_path.parent / "static").mkdir()
    (ctx.config_path.parent / "static" / "index.html").write_text("ready")

    base = _DemoPlugin(ctx=ctx)
    router = _Router(name="r2")
    base.include_router(router, prefix="api_")
    assert base.get_router("r2") is router
    assert base.list_routers() == ["r2"]
    assert base.register_static_ui() is True
    assert base.get_static_ui_config()["plugin_id"] == "demo"
    assert _DemoPlugin(ctx=_Ctx(config_path=tmp_path / "missing" / "plugin.toml")).register_static_ui() is False



@pytest.mark.asyncio
async def test_plugin_base_dynamic_entry_and_status_helpers() -> None:
    class _CtxWithStatus(_Ctx):
        def __init__(self) -> None:
            super().__init__()
            self.status = None

        def update_status(self, status: dict[str, object]) -> None:
            self.status = status

    base = _DemoPlugin(ctx=_CtxWithStatus())

    async def dyn_handler() -> str:
        return "dyn"

    assert base.register_dynamic_entry("dyn", dyn_handler, name="Dyn") is True
    assert base.is_entry_enabled("dyn") is True
    assert any(item["id"] == "dyn" for item in base.list_entries())
    assert len([item for item in base.list_entries(include_disabled=True) if item["id"] == "dyn"]) == 1
    meta = base._dynamic_entries["dyn"]["meta"]
    assert meta.kind == "action"
    assert meta.metadata == {"dynamic": True, "enabled": True}
    assert base.disable_entry("dyn") is True
    assert base.is_entry_enabled("dyn") is False
    assert all(item["id"] != "dyn" for item in base.list_entries())
    assert any(item["id"] == "dyn" for item in base.list_entries(include_disabled=True))
    assert base.enable_entry("dyn") is True
    assert base.unregister_dynamic_entry("dyn") is True
    assert base.enable_entry("dyn") is False
    assert base.disable_entry("dyn") is False
    assert base.is_entry_enabled("dyn") is None
    base.report_status({"success": True})
    assert base._host_ctx.status == {"success": True}


@pytest.mark.asyncio
async def test_plugin_base_runtime_shortcuts_delegate_to_ctx() -> None:
    ctx = _Ctx()
    base = _DemoPlugin(ctx=ctx)

    run_result = await base.run_update(progress=0.5, stage="working")
    export_result = await base.export_push(export_type="text", text="hello", label="demo")
    finish_result = await base.finish(data={"ok": True}, reply=False, message="done")
    push_result = base.push_message(source="demo", message_type="text", content="payload")

    assert run_result == {"ok": True}
    assert ctx.run_updates == [{"progress": 0.5, "stage": "working", "timeout": 5.0, "run_id": None, "message": None, "step": None, "step_total": None, "eta_seconds": None, "metrics": None}]
    assert export_result == {"ok": True}
    assert ctx.exports[0]["export_type"] == "text"
    assert ctx.exports[0]["text"] == "hello"
    assert push_result == {"ok": True}
    assert ctx.pushed_messages[0]["source"] == "demo"
    assert finish_result["success"] is True
    assert finish_result["message"] == "done"
    assert finish_result["meta"]["agent"]["reply"] is False


@pytest.mark.asyncio
async def test_register_dynamic_entry_rejects_non_callable_handler() -> None:
    base = _DemoPlugin(ctx=_Ctx())

    with pytest.raises(TypeError, match="handler must be callable"):
        base.register_dynamic_entry("dyn", object())


@pytest.mark.asyncio
async def test_register_dynamic_entry_rejects_duplicate_ids() -> None:
    base = _DemoPlugin(ctx=_Ctx())

    async def dyn_handler() -> str:
        return "dyn"

    with pytest.raises(runtime.EntryConflictError, match="duplicate entry id"):
        base.register_dynamic_entry("hello", dyn_handler)

    assert base.register_dynamic_entry("dyn", dyn_handler) is True
    with pytest.raises(runtime.EntryConflictError, match="duplicate entry id"):
        base.register_dynamic_entry("dyn", dyn_handler)


def test_list_entries_exposes_richer_metadata() -> None:
    plugin = _RichPlugin(ctx=_Ctx())
    item = next(entry for entry in plugin.list_entries() if entry["id"] == "typed")
    assert item["description"] == "typed entry"
    assert item["event_type"] == "plugin_entry"
    assert item["kind"] == "action"
    assert item["timeout"] == 12.0
    assert item["model_validate"] is True
    assert item["metadata"] == {"group": "demo"}
    assert item["input_schema"] == {
        "type": "object",
        "properties": {
            "name": {"type": "string", "default": "world"},
            "enabled": {"type": "boolean", "default": True},
        },
    }


def test_collect_entries_does_not_evaluate_non_entry_properties() -> None:
    class _PropertyPlugin(NekoPluginBase):
        def __init__(self, ctx) -> None:
            super().__init__(ctx)
            self.property_hits = 0

        @property
        def expensive(self) -> str:
            self.property_hits += 1
            raise AssertionError("property should not be evaluated")

        @plugin_entry(id="safe")
        async def safe(self) -> str:
            return "ok"

    plugin = _PropertyPlugin(ctx=_Ctx())

    entries = plugin.collect_entries()

    assert "safe" in entries
    assert plugin.property_hits == 0


def test_list_entries_uses_dynamic_enabled_state_without_rechecking() -> None:
    plugin = _RichPlugin(ctx=_Ctx())

    async def dyn_handler() -> str:
        return "dyn"

    meta = EventMeta(
        event_type="plugin_entry",
        id="dyn_disabled",
        name="Dyn Disabled",
        metadata={"dynamic": True, "enabled": False},
    )
    plugin._dynamic_entries["dyn_disabled"] = {
        "meta": meta,
        "handler": dyn_handler,
        "enabled": False,
    }
    plugin.is_entry_enabled = lambda entry_id: (_ for _ in ()).throw(AssertionError("should not be called"))  # type: ignore[method-assign]

    visible_entries = plugin.list_entries()
    assert all(entry["id"] != "dyn_disabled" for entry in visible_entries)

    all_entries = plugin.list_entries(include_disabled=True)
    dyn_entry = next(entry for entry in all_entries if entry["id"] == "dyn_disabled")
    assert dyn_entry["enabled"] is False
    assert dyn_entry["dynamic"] is True


def test_plugin_init_reexports_declared_symbols(plugin_api_module) -> None:
    mod = plugin_api_module
    for name in mod.__all__:
        assert hasattr(mod, name)

    assert "_name" not in vars(mod)


def test_plugin_init_all_contains_expected_symbols(plugin_api_module) -> None:
    mod = plugin_api_module
    required = {
        "NekoPluginBase",
        "PluginMeta",
        "neko_plugin",
        "plugin_entry",
        "plugin",
        "PluginConfig",
        "Plugins",
        "PluginRouter",
        "Result",
        "Ok",
        "Err",
    }
    assert required.issubset(set(mod.__all__))
    assert len(mod.__all__) == len(set(mod.__all__))
    # Internal symbols must NOT leak to plugin surface
    assert "HostBusProtocol" not in mod.__all__
    assert "SdkContext" not in mod.__all__
    assert "ensure_sdk_context" not in mod.__all__
    assert "CallChain" not in mod.__all__
    assert "HookExecutorMixin" not in mod.__all__
    assert "EXTENDED_TYPES" not in mod.__all__
