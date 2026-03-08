from __future__ import annotations

from dataclasses import dataclass, fields

from plugin.sdk_v2.plugin.base import (
    NEKO_PLUGIN_META_ATTR,
    NEKO_PLUGIN_TAG,
    NekoPluginBase,
    PluginMeta,
)
from plugin.sdk_v2.plugin.decorators import plugin_entry


class _Ctx:
    plugin_id = "demo"

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
        return {"config": {"feature": {"enabled": True}}}

    async def update_own_config(self, updates: dict[str, object], timeout: float = 10.0) -> dict[str, object]:
        return {"config": updates}

    async def query_plugins_async(self, filters: dict[str, object], timeout: float = 5.0) -> dict[str, object]:
        return {"plugins": [{"plugin_id": "demo", "name": "Demo"}]}

    async def trigger_plugin_event_async(
        self,
        *,
        target_plugin_id: str,
        event_type: str,
        event_id: str,
        params: dict[str, object],
        timeout: float,
    ) -> dict[str, object]:
        return {"target_plugin_id": target_plugin_id, "event_type": event_type, "event_id": event_id, "params": params}


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
    input_schema = {"type": "object"}

    @plugin_entry(id="hello")
    async def hello(self) -> str:
        return "hello"

    async def plain(self) -> str:
        return "plain"


def test_base_constants_and_meta_defaults() -> None:
    assert NEKO_PLUGIN_META_ATTR == "__neko_plugin_meta__"
    assert NEKO_PLUGIN_TAG == "__neko_plugin__"

    meta = PluginMeta(id="p", name="Plugin")
    assert meta.version == "0.0.0"
    assert meta.sdk_version == "2.0.0a0"
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
    assert base.store is None
    assert base.db is None
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

    base.include_router(router, prefix="pre_")
    assert router._prefix == "pre_"
    assert base.exclude_router("r1") is True
    assert base.exclude_router("r1") is False

    base.include_router(router)
    assert base.exclude_router(router) is True
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
    assert callable(entries["hello"])
    assert callable(entries["routed"])
    assert "plain" not in entries


def test_enable_file_logging_sets_file_logger_attribute() -> None:
    base = _DemoPlugin(ctx=_Ctx())
    logger = base.enable_file_logging(log_level="DEBUG")
    assert logger is getattr(base, "file_logger")
