"""``output_schema`` / ``strict_output`` reach every place entry metadata travels.

Normal, router and dynamic entries; the host's IPC registration; previews;
packaged metadata; ``GET /plugins``; the ``/runs`` error code; NekoClaw
capabilities and gateway tool definitions; ``neko-plugin validate``.
Entries that declare nothing keep their previous shape.
"""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import pytest
from pydantic import BaseModel

from plugin.sdk.plugin.base import NekoPluginBase
from plugin.sdk.plugin.decorators import plugin_entry
from plugin.sdk.shared.constants import EVENT_META_ATTR
from plugin.sdk.shared.core.router import PluginRouter

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}


class Reply(BaseModel):
    n: int


# ---------------------------------------------------------------------------
# Plugin side: router and dynamic entries
# ---------------------------------------------------------------------------


class _Router(PluginRouter):
    @plugin_entry(id="typed")
    async def typed(self) -> Reply:
        return Reply(n=1)

    @plugin_entry(id="explicit", output_schema=SCHEMA, strict_output=True)
    async def explicit(self):
        return {"n": 1}

    @plugin_entry(id="plain")
    async def plain(self):
        return {}


def test_router_entries_keep_the_output_contract():
    entries = _Router(prefix="r_").collect_entries()
    typed = entries["r_typed"].meta
    assert typed.output_schema == Reply.model_json_schema()
    assert typed.output_model is Reply
    explicit = entries["r_explicit"].meta
    assert explicit.output_schema == SCHEMA
    assert explicit.strict_output is True
    # Detached copy: the router may not alias the decorator's mapping.
    assert explicit.output_schema is not getattr(_Router.explicit, EVENT_META_ATTR).output_schema
    assert entries["r_plain"].meta.output_schema is None


async def test_router_add_entry_accepts_the_output_contract():
    router = PluginRouter(prefix="r_")

    async def handler():
        return {}

    await router.add_entry("dyn", handler, output_schema=SCHEMA)
    meta = router.collect_entries()["r_dyn"].meta
    assert meta.output_schema == SCHEMA


class _Queue:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def put_nowait(self, obj):
        self.items.append(obj)


class _Ctx:
    plugin_id = "demo"
    logger = None
    metadata: dict[str, object] = {}
    bus = None

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.message_queue = _Queue()
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }


class _Plugin(NekoPluginBase):
    pass


def _register_payload(ctx: _Ctx, entry_id: str) -> dict[str, object]:
    return next(
        item["meta"]
        for item in ctx.message_queue.items
        if item.get("type") == "ENTRY_UPDATE" and item.get("action") == "register" and item.get("entry_id") == entry_id
    )


def test_dynamic_entry_carries_the_output_contract_everywhere():
    ctx = _Ctx(Path(tempfile.mkdtemp()) / "plugin.toml")
    plugin = _Plugin(ctx)

    async def handler():
        return {"n": 1}

    plugin.register_dynamic_entry("dyn", handler, output_schema=SCHEMA)
    plugin.register_dynamic_entry("bare", handler)

    payload = _register_payload(ctx, "dyn")
    assert payload["output_schema"] == SCHEMA
    bare_payload = _register_payload(ctx, "bare")
    assert "output_schema" not in bare_payload
    assert "strict_output" not in bare_payload

    meta = plugin.collect_entries()["dyn"].meta
    assert meta.output_schema == SCHEMA

    listed = {item["id"]: item for item in plugin.list_entries()}
    assert listed["dyn"]["output_schema"] == SCHEMA
    assert "output_schema" not in listed["bare"]

    plugin.disable_entry("dyn")
    disabled = {item["id"]: item for item in plugin.list_entries(include_disabled=True)}
    assert disabled["dyn"]["output_schema"] == SCHEMA


def test_dynamic_entry_rejects_a_non_mapping_schema():
    plugin = _Plugin(_Ctx(Path(tempfile.mkdtemp()) / "plugin.toml"))
    with pytest.raises(TypeError, match="output_schema"):
        plugin.register_dynamic_entry("dyn", lambda: None, output_schema="object")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Host side: IPC registration, previews, packaged metadata, GET /plugins
# ---------------------------------------------------------------------------


class _Transport:
    async def send_command(self, msg):
        return None


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


async def test_host_ipc_registration_keeps_the_output_contract():
    from plugin.core.communication import PluginCommunicationResourceManager
    from plugin.core.state import state

    manager = PluginCommunicationResourceManager(plugin_id="demo", transport=_Transport(), logger=_Logger())
    handlers_backup = dict(state.event_handlers)
    cache_backup = copy.deepcopy(state._snapshot_cache)
    try:
        with state.acquire_event_handlers_write_lock():
            state.event_handlers.clear()
        state.invalidate_snapshot_cache("handlers")
        await manager._handle_entry_update({
            "type": "ENTRY_UPDATE",
            "action": "register",
            "plugin_id": "demo",
            "entry_id": "dyn",
            "meta": {"id": "dyn", "name": "Dyn", "output_schema": SCHEMA, "strict_output": True},
        })
        with state.acquire_event_handlers_read_lock():
            meta = state.event_handlers["demo.dyn"].meta
        assert meta.output_schema == SCHEMA
        assert meta.strict_output is True
    finally:
        with state.acquire_event_handlers_write_lock():
            state.event_handlers.clear()
            state.event_handlers.update(handlers_backup)
        with state._snapshot_cache_lock:
            state._snapshot_cache = cache_backup


class _PreviewPlugin:
    __routers__ = [_Router]

    @plugin_entry(id="typed_main")
    async def typed_main(self) -> Reply:
        return Reply(n=1)

    @plugin_entry(id="plain_main")
    async def plain_main(self):
        return {}


def test_previews_carry_the_output_contract_only_when_declared():
    from plugin.core.registry import _extract_entries_preview

    previews = {item["id"]: item for item in _extract_entries_preview("demo", _PreviewPlugin, {}, {})}
    assert previews["typed_main"]["output_schema"] == Reply.model_json_schema()
    assert "strict_output" not in previews["typed_main"]
    assert previews["explicit"]["output_schema"] == SCHEMA
    assert previews["explicit"]["strict_output"] is True
    assert "output_schema" not in previews["plain_main"]
    assert "strict_output" not in previews["plain_main"]


def test_packaged_handler_metadata_carries_the_output_contract():
    from plugin.server.application.plugins.metadata_scanner import _event_meta_payload

    payload = _event_meta_payload(getattr(_Router.explicit, EVENT_META_ATTR))
    assert payload["output_schema"] == SCHEMA
    assert payload["strict_output"] is True
    # The Pydantic model never leaves the plugin process.
    assert "output_model" not in _event_meta_payload(getattr(_Router.typed, EVENT_META_ATTR))
    # An entry that declares nothing keeps its previous packaged bytes.
    plain = _event_meta_payload(getattr(_Router.plain, EVENT_META_ATTR))
    assert "output_schema" not in plain
    assert "strict_output" not in plain


def test_get_plugins_entries_publish_the_output_contract():
    from plugin._types.events import EventHandler
    from plugin._types.events import EventMeta as HostEventMeta
    from plugin.server.application.plugins.query_service import (
        _append_entries_from_preview,
        _build_entries_from_handlers,
    )

    declared = HostEventMeta(event_type="plugin_entry", id="declared", name="Declared")
    declared.output_schema = SCHEMA  # type: ignore[attr-defined]
    declared.strict_output = True  # type: ignore[attr-defined]
    plain = HostEventMeta(event_type="plugin_entry", id="plain", name="Plain")
    entries, seen = _build_entries_from_handlers(
        plugin_id="demo",
        handlers_snapshot={
            "demo.declared": EventHandler(meta=declared, handler=lambda: None),
            "demo.plain": EventHandler(meta=plain, handler=lambda: None),
        },
    )
    by_id = {item["id"]: item for item in entries}
    assert by_id["declared"]["output_schema"] == SCHEMA
    assert by_id["declared"]["strict_output"] is True
    assert set(by_id["plain"]) == {
        "id", "name", "description", "event_key", "timeout", "input_schema",
        "return_message", "llm_result_fields", "llm_result_schema", "metadata",
    }

    from_preview: list[dict[str, object]] = []
    _append_entries_from_preview(
        plugin_id="demo",
        plugin_meta={"entries_preview": [{"id": "stopped", "output_schema": SCHEMA}]},
        entries=from_preview,
        seen=set(seen),
    )
    assert from_preview[0]["output_schema"] == SCHEMA
    assert "strict_output" not in from_preview[0]


# ---------------------------------------------------------------------------
# neko-plugin validate
# ---------------------------------------------------------------------------


def _plugin_dir(tmp_path: Path, decorator_args: str) -> Path:
    plugin_dir = tmp_path / "out_demo"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text(
        "\n".join([
            "[plugin]",
            'id = "out_demo"',
            'name = "Out Demo"',
            'version = "0.0.1"',
            'type = "plugin"',
            'entry = "plugin.plugins.out_demo:DemoPlugin"',
            "",
            "[plugin.sdk]",
            'recommended = ">=0.1.0,<0.2.0"',
            'supported = ">=0.1.0,<0.3.0"',
            "",
            "[[plugin.entries]]",
            'id = "run"',
            "strict_output = true",
            "",
        ]),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from plugin.sdk.plugin import plugin_entry\n\n\n"
        "class DemoPlugin:\n"
        f"    @plugin_entry(id='run', {decorator_args})\n"
        "    async def run(self):\n"
        "        return []\n",
        encoding="utf-8",
    )
    return plugin_dir


def _messages(plugin_dir: Path) -> list[str]:
    from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir

    return [message for _level, message in validate_plugin_dir(plugin_dir)]


def test_validate_rejects_a_non_literal_output_schema(tmp_path: Path):
    messages = _messages(_plugin_dir(tmp_path, "output_schema='array'"))
    assert any("output_schema in" in message and "dict literal" in message for message in messages)


def test_validate_says_an_explicit_output_schema_is_published_only(tmp_path: Path):
    from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir

    issues = validate_plugin_dir(_plugin_dir(tmp_path, "output_schema={'type': 'array'}"))
    hints = [(level, message) for level, message in issues if "output_schema in" in message]
    # Not an error, and no type='object' complaint: a hint that it is publish-only.
    assert [level for level, _message in hints] == ["warning"]
    assert "published only" in hints[0][1]
    # [[plugin.entries]] strict_output is a known field, ignored by the registry.
    strict = [message for _level, message in issues if "strict_output" in message]
    assert strict and all("not a recognized" not in message for message in strict)
    assert any("is ignored" in message for message in strict)


def test_validate_flags_non_table_mapping_fields(tmp_path: Path):
    plugin_dir = _plugin_dir(tmp_path, "output_schema={'type': 'array'}")
    toml_path = plugin_dir / "plugin.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8").replace("strict_output = true\n", 'output_schema = "typo"\n'),
        encoding="utf-8",
    )
    from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir

    flagged = [(level, message) for level, message in validate_plugin_dir(plugin_dir) if "output_schema must be a table" in message]
    assert flagged
    assert all(level == "warning" for level, _message in flagged)
    assert any("registry replaces" in message for _level, message in flagged)


def test_validate_keyed_table_type_hint_does_not_claim_a_replacement(tmp_path: Path):
    from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir

    plugin_dir = _plugin_dir(tmp_path, "output_schema={'type': 'array'}")
    toml_path = plugin_dir / "plugin.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8").replace(
            '[[plugin.entries]]\nid = "run"\nstrict_output = true\n', '[plugin.entries.run]\noutput_schema = "typo"\n'
        ),
        encoding="utf-8",
    )
    flagged = [(level, message) for level, message in validate_plugin_dir(plugin_dir) if "output_schema must be a table" in message]
    assert flagged and all(level == "warning" for level, _message in flagged)
    assert not any("registry replaces" in message for _level, message in flagged)


def test_manifest_overrides_of_the_output_contract_are_not_published():
    from plugin.core.registry import _normalized_entry_declaration

    declared = _normalized_entry_declaration(
        {"id": "run", "timeout": 5, "output_schema": {"type": "object"}, "strict_output": True}
    )
    # Published and enforced must agree: the check runs from the decorator.
    assert "output_schema" not in declared and "strict_output" not in declared
    assert declared["timeout"] == 5


def test_validate_gives_no_publish_hint_for_a_non_dict_output_schema(tmp_path: Path):
    messages = _messages(_plugin_dir(tmp_path, "output_schema='typo'"))
    assert any("dict literal" in message for message in messages)
    assert not any("published only" in message for message in messages)
