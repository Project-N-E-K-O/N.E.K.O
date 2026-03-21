from __future__ import annotations

from plugin.server.application.plugins.query_service import _append_entries_from_preview


def test_append_entries_from_preview_merges_with_existing_entries() -> None:
    entries = [
        {"id": "dynamic_tool", "name": "Dynamic Tool"},
    ]
    seen = {("plugin_entry", "dynamic_tool")}

    _append_entries_from_preview(
        plugin_id="mcp_adapter",
        plugin_meta={
            "entries_preview": [
                {"id": "list_servers", "name": "List Servers"},
                {"id": "dynamic_tool", "name": "Dynamic Tool Preview"},
            ]
        },
        entries=entries,
        seen=seen,
    )

    ids = [item["id"] for item in entries]
    assert ids == ["dynamic_tool", "list_servers"]
