from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from plugin.core.registry import _extract_entries_preview
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
