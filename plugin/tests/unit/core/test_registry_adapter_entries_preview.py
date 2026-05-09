from __future__ import annotations

from plugin.core import registry as module
from plugin.core.registry import _extract_entries_preview
from plugin.plugins.lifekit import LifeKitPlugin
from plugin.plugins.mcp_adapter import MCPAdapterPlugin


def test_mcp_adapter_extract_entries_preview_contains_static_entries() -> None:
    preview = _extract_entries_preview(
        "mcp_adapter",
        MCPAdapterPlugin,
        conf={},
        pdata={"entry": "plugin.plugins.mcp_adapter:MCPAdapterPlugin"},
    )

    ids = {item.get("id") for item in preview}
    assert "list_servers" in ids
    assert "gateway_invoke" in ids


def test_lifekit_extract_entries_preview_contains_router_entries() -> None:
    preview = _extract_entries_preview(
        "lifekit",
        LifeKitPlugin,
        conf={},
        pdata={"entry": "plugin.plugins.lifekit:LifeKitPlugin"},
    )

    ids = {item.get("id") for item in preview}
    assert {"trip_advice", "currency_convert", "unit_convert"} <= ids


def test_lifekit_scan_static_metadata_registers_router_entries() -> None:
    handlers_backup = dict(module.state.event_handlers)
    method_map_backup = dict(module.plugin_entry_method_map)
    try:
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
        module.plugin_entry_method_map.clear()

        module.scan_static_metadata("lifekit", LifeKitPlugin, conf={}, pdata={})

        assert "lifekit.trip_advice" in module.state.event_handlers
        assert "lifekit:plugin_entry:trip_advice" in module.state.event_handlers
        assert ("lifekit", "trip_advice") not in module.plugin_entry_method_map
    finally:
        with module.state.acquire_event_handlers_write_lock():
            module.state.event_handlers.clear()
            module.state.event_handlers.update(handlers_backup)
        module.plugin_entry_method_map.clear()
        module.plugin_entry_method_map.update(method_map_backup)
