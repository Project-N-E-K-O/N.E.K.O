"""Every hand-built entry projection carries contract fields it does not shape.

Entry metadata is copied into several explicit dicts (IPC registration,
``list_entries``, registry previews, ``GET /plugins`` entries) and one
EventMeta rebuild (router prefix resolution). A field added to
``ENTRY_CONTRACT_FIELDS`` or to ``EventMeta`` must reach all of them without
anyone editing each copy; these tests add a probe field and check it arrives.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from plugin._types import entry_metadata
from plugin._types.entry_metadata import (
    ENTRY_CONTRACT_BOOL_FIELDS,
    ENTRY_CONTRACT_FIELDS,
    ENTRY_CONTRACT_MAPPING_FIELDS,
    unhandled_entry_contract_fields,
)

_PROBE = "zz_probe_field"


@pytest.fixture
def probe_field(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(
        entry_metadata, "ENTRY_CONTRACT_FIELDS", (*ENTRY_CONTRACT_FIELDS, _PROBE)
    )
    return _PROBE


def _meta(**extra: object) -> SimpleNamespace:
    base = {
        "event_type": "plugin_entry",
        "id": "probe",
        "name": "Probe",
        "description": "",
        "input_schema": {"type": "object", "properties": {}},
        "kind": "action",
        "auto_start": False,
        "persist": None,
        "model_validate": True,
        "timeout": None,
        "llm_result_fields": None,
        "llm_result_schema": None,
        "metadata": {},
        "extra": {},
        "quick_action": False,
    }
    base.update(extra)
    return SimpleNamespace(**base)


def test_projection_shaped_fields_name_real_contract_fields() -> None:
    # A typo here would silently carry the real field a second time.
    from plugin._types.entry_metadata import ENTRY_PROJECTION_SHAPED_FIELDS

    assert ENTRY_PROJECTION_SHAPED_FIELDS <= set(ENTRY_CONTRACT_FIELDS)
    # New (opt-in) contract fields are carried, never shaped by hand.
    assert not ENTRY_PROJECTION_SHAPED_FIELDS & {"output_schema", "strict_output"}


def test_field_shape_tuples_name_real_contract_fields() -> None:
    assert set(ENTRY_CONTRACT_BOOL_FIELDS) <= set(ENTRY_CONTRACT_FIELDS)
    assert set(ENTRY_CONTRACT_MAPPING_FIELDS) <= set(ENTRY_CONTRACT_FIELDS)
    assert not set(ENTRY_CONTRACT_BOOL_FIELDS) & set(ENTRY_CONTRACT_MAPPING_FIELDS)


def test_unset_values_are_not_carried(probe_field: str) -> None:
    assert unhandled_entry_contract_fields(_meta(**{probe_field: None}), ()).get(probe_field, "absent") == "absent"
    assert unhandled_entry_contract_fields(_meta(**{probe_field: False}), ()).get(probe_field, "absent") == "absent"
    assert unhandled_entry_contract_fields(_meta(), ()).get(probe_field, "absent") == "absent"


def test_mapping_values_are_copied(probe_field: str) -> None:
    value = {"type": "object"}
    carried = unhandled_entry_contract_fields(_meta(**{probe_field: value}), ())
    assert carried[probe_field] == value
    assert carried[probe_field] is not value


def test_dynamic_register_payload_carries_new_field(probe_field: str) -> None:
    from plugin.sdk.plugin.base import NekoPluginBase

    sent: list[dict[str, object]] = []
    fake = SimpleNamespace(plugin_id="demo", _notify_host_comm=sent.append)
    NekoPluginBase._notify_dynamic_entry_registered(
        fake, "probe", _meta(**{probe_field: {"type": "object"}})  # type: ignore[arg-type]
    )
    assert sent[0]["meta"][probe_field] == {"type": "object"}  # type: ignore[index]


def test_dynamic_register_payload_unchanged_without_new_fields(probe_field: str) -> None:
    from plugin.sdk.plugin.base import NekoPluginBase

    sent: list[dict[str, object]] = []
    fake = SimpleNamespace(plugin_id="demo", _notify_host_comm=sent.append)
    NekoPluginBase._notify_dynamic_entry_registered(fake, "probe", _meta())  # type: ignore[arg-type]
    assert set(sent[0]["meta"]) == {  # type: ignore[arg-type]
        "id", "name", "description", "input_schema", "kind", "auto_start",
        "enabled", "metadata", "model_validate",
    }


def test_list_entries_item_carries_new_field(probe_field: str) -> None:
    from plugin.sdk.plugin.base import _entry_listing

    item = _entry_listing("probe", _meta(**{probe_field: 3}), enabled=True, dynamic=False)
    assert item[probe_field] == 3
    assert _entry_listing("probe", _meta(), enabled=True, dynamic=False).get(probe_field, "absent") == "absent"


def test_registry_preview_carries_new_field(probe_field: str) -> None:
    from plugin.core.registry import _entry_preview

    preview = _entry_preview("demo", "probe", _meta(**{probe_field: "x"}))
    assert preview[probe_field] == "x"


def test_registry_config_skeleton_matches_previous_shape() -> None:
    from plugin.core.registry import _entry_preview

    assert _entry_preview("demo", "cfg", None) == {
        "id": "cfg",
        "name": "",
        "description": "",
        "event_key": "demo.cfg",
        "input_schema": {},
        "return_message": "",
        "event_type": "plugin_entry",
        "kind": "action",
        "auto_start": False,
        "timeout": None,
        "model_validate": True,
        "llm_result_schema": {},
        "metadata": {},
    }


def test_plugins_listing_carries_new_field_from_both_sources(probe_field: str) -> None:
    from plugin.server.application.plugins.query_service import (
        _append_entries_from_preview,
        _build_entries_from_handlers,
    )

    handler = SimpleNamespace(meta=_meta(**{probe_field: {"k": 1}}), handler=None)
    entries, seen = _build_entries_from_handlers(
        plugin_id="demo", handlers_snapshot={"demo.probe": handler}
    )
    assert entries[0][probe_field] == {"k": 1}

    preview_entries: list[dict[str, object]] = []
    _append_entries_from_preview(
        plugin_id="demo",
        plugin_meta={"entries_preview": [{"id": "other", probe_field: {"k": 2}}]},
        entries=preview_entries,
        seen=set(),
    )
    assert preview_entries[0][probe_field] == {"k": 2}


def test_router_resolution_keeps_every_event_meta_field() -> None:
    from plugin.sdk.shared.core.events import EventMeta, QuickActionConfig
    from plugin.sdk.shared.core.router import PluginRouter

    meta = EventMeta(
        event_type="plugin_entry",
        id="probe",
        name="Probe",
        description="d",
        input_schema={"type": "object"},
        kind="service",
        auto_start=True,
        persist=True,
        params=dict,
        model_validate=False,
        timeout=3.0,
        llm_result_fields=["a"],
        llm_result_schema={"type": "object", "required": ["a"]},
        llm_result_model=dict,
        quick_action=True,
        quick_action_config=QuickActionConfig(icon="i", priority=2),
        extra={"x": 1},
        metadata={"y": 2},
        output_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        output_model=dict,
        strict_output=True,
    )
    router = PluginRouter(prefix="p_")
    router._decorated_entries.append(("probe", meta, lambda: None))
    resolved = router._resolved_entries()["p_probe"].meta
    for field in dataclasses.fields(EventMeta):
        if field.name == "id":
            continue
        assert getattr(resolved, field.name) == getattr(meta, field.name), field.name
    assert resolved.id == "p_probe"
    assert resolved.metadata is not meta.metadata
    assert resolved.input_schema is not meta.input_schema
    assert resolved.output_schema is not meta.output_schema


def test_list_entries_projection_detaches_nested_schemas():
    from plugin.sdk.plugin.base import _entry_listing
    from plugin.sdk.shared.core.events import EventMeta

    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    meta = EventMeta(event_type="plugin_entry", id="e", output_schema=schema)
    item = _entry_listing("e", meta, enabled=True, dynamic=False)
    item["output_schema"]["properties"]["count"]["type"] = "string"
    assert schema["properties"]["count"]["type"] == "integer"


def _cyclic() -> dict[str, object]:
    schema: dict[str, object] = {"type": "object"}
    schema["self"] = schema
    return schema


@pytest.mark.parametrize(
    "broken_schema",
    [
        pytest.param({1: "not a string key"}, id="non-string-key"),
        pytest.param({"properties": {("a", "b"): {}}}, id="nested-tuple-key"),
        pytest.param(_cyclic(), id="cycle"),
        pytest.param({"type": "number", "maximum": float("inf")}, id="infinity"),
    ],
)
def test_malformed_output_schema_drops_only_itself_from_get_plugins(broken_schema):
    from plugin._types.events import EventHandler
    from plugin._types.events import EventMeta as HostEventMeta
    from plugin.server.application.plugins.query_service import _build_entries_from_handlers

    broken = HostEventMeta(event_type="plugin_entry", id="broken", name="Broken")
    broken.output_schema = broken_schema  # type: ignore[attr-defined]
    fine = HostEventMeta(event_type="plugin_entry", id="fine", name="Fine")
    fine.output_schema = {"type": "object"}  # type: ignore[attr-defined]
    entries, _seen = _build_entries_from_handlers(
        plugin_id="demo",
        handlers_snapshot={
            "demo.broken": EventHandler(meta=broken, handler=lambda: None),
            "demo.fine": EventHandler(meta=fine, handler=lambda: None),
        },
    )
    by_id = {item["id"]: item for item in entries}
    assert "output_schema" not in by_id["broken"]
    assert by_id["fine"]["output_schema"] == {"type": "object"}


def test_projection_detaches_nested_levels_around_an_uncopyable_default():
    from types import MappingProxyType

    from plugin.sdk.plugin.base import _entry_listing
    from plugin.sdk.shared.core.events import EventMeta

    schema = {"type": "object", "properties": {"count": {"type": "integer", "default": MappingProxyType({"a": 1})}}}
    meta = EventMeta(event_type="plugin_entry", id="e", output_schema=schema)
    item = _entry_listing("e", meta, enabled=True, dynamic=False)
    item["output_schema"]["properties"]["count"]["type"] = "string"
    assert schema["properties"]["count"]["type"] == "integer"
