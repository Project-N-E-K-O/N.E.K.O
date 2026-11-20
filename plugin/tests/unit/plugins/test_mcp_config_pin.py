"""The reconnect path must pin the config it connects with.

``connect_server`` reads a fresh ``config.dump()`` snapshot, while the tool risk
class is read from ``_servers_config``. They only agreed at process start, so a
``tool_risk`` edited and followed by a reconnect kept the old class -- a server
moved from ``read`` to ``external`` still registered its tools as ``read``, which
in enforce mode is the difference between a confirm and a silent allow.
"""
import asyncio
from types import SimpleNamespace

from plugin.plugins.mcp_adapter import MCPAdapterPlugin


def _stub(servers):
    return SimpleNamespace(
        _servers_config=servers,
        _servers_config_lock=asyncio.Lock(),
        ctx=SimpleNamespace(logger=SimpleNamespace(info=lambda *a, **k: None)),
    )


def test_a_reconnect_replaces_the_cached_entry_with_what_it_connects_with():
    stub = _stub({"fs": {"url": "http://old", "tool_risk": "read"}})
    ok = asyncio.run(MCPAdapterPlugin._pin_connected_server_config(
        stub, "fs", {"url": "http://new", "tool_risk": "external"},
    ))
    assert ok is True
    assert stub._servers_config["fs"] == {"url": "http://new", "tool_risk": "external"}
    # And the reader now sees the new class.
    assert MCPAdapterPlugin._server_tool_risk(stub, "fs") == "external"


def test_a_server_removed_while_waiting_is_not_resurrected():
    stub = _stub({})
    ok = asyncio.run(MCPAdapterPlugin._pin_connected_server_config(
        stub, "gone", {"url": "http://new"},
    ))
    assert ok is False
    assert "gone" not in stub._servers_config


def test_the_pinned_copy_is_not_the_caller_snapshot():
    """A later edit of the caller's dict must not move the risk under the connection."""
    snapshot = {"url": "http://new", "tool_risk": "external"}
    stub = _stub({"fs": {"url": "http://old", "tool_risk": "read"}})
    asyncio.run(MCPAdapterPlugin._pin_connected_server_config(stub, "fs", snapshot))
    snapshot["tool_risk"] = "read"
    assert MCPAdapterPlugin._server_tool_risk(stub, "fs") == "external"
