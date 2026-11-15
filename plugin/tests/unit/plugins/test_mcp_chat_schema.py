"""Provider contract tests for MCP chat injection."""
import copy
import re
from types import SimpleNamespace

import pytest
from google.genai import types

from plugin.plugins.mcp_adapter import MCPAdapterPlugin
from plugin.plugins.mcp_adapter.chat_schema import portable_chat_schema


def test_mcp_schema_references_do_not_break_other_gemini_tools():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"path": {"type": "string", "description": "File path"}},
        "type": "object",
        "properties": {"path": {"$ref": "#/$defs/path"}},
        "required": ["path"],
    }
    original = copy.deepcopy(schema)
    portable = portable_chat_schema(schema)
    tool = types.Tool(function_declarations=[
        {"name": "mcp_fs_read", "parameters": portable},
        {"name": "minecraft_task", "parameters": {"type": "object", "properties": {}}},
    ])
    assert len(tool.function_declarations) == 2
    assert portable["properties"]["path"]["type"] == "string"
    assert schema == original


@pytest.mark.parametrize("schema", [
    {"$ref": "https://example.com/schema"},
    {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"},
    {"$ref": "#/$defs/missing"},
    {"oneOf": [{"type": "string"}, {"type": "integer"}]},
    {"type": "integer", "enum": [1, 2]},
])
def test_unrepresentable_schema_is_rejected(schema):
    with pytest.raises(ValueError):
        portable_chat_schema(schema)


def test_dotted_names_are_portable_and_collisions_get_suffixes():
    plugin = object.__new__(MCPAdapterPlugin)
    plugin._llm_tools = {}
    first = plugin._alloc_llm_tool_name("mcp_github.search_fs.read_file")
    second = plugin._alloc_llm_tool_name("mcp_github_search_fs_read_file", frozenset({first}))
    assert first != second
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", first)
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", second)


@pytest.mark.asyncio
async def test_unsafe_schema_never_reaches_shared_tool_registry():
    plugin = object.__new__(MCPAdapterPlugin)
    plugin._chat_tools = {}
    plugin._pending_chat_tools = {}
    plugin._llm_tools = {}
    plugin.ctx = SimpleNamespace(logger=SimpleNamespace(warning=lambda *args: None))

    async def foreign_names():
        return frozenset()

    plugin._fetch_foreign_llm_tool_names = foreign_names
    registrations = []
    plugin.register_llm_tool = lambda **kwargs: registrations.append(kwargs)
    result = await plugin._register_chat_tool_local_locked(
        tool_id="mcp_fs_read", server_name="fs", tool_name="read", description="read",
        schema={"$ref": "https://example.com/schema"},
    )
    assert result is False
    assert registrations == []
    assert plugin._pending_chat_tools == {}


def _adapter_for_risk(servers_config):
    """An adapter with just enough state for the risk lookup.

    Deliberately narrow. The injection path that consumes this value needs the gateway
    and a live tool list, and pretending otherwise would test the harness, not the rule.
    The rule under test is one line at the registration site: the pin if there is one,
    otherwise `external`.
    """
    plugin = object.__new__(MCPAdapterPlugin)
    plugin._servers_config = servers_config
    warnings = []
    plugin.ctx = SimpleNamespace(logger=SimpleNamespace(warning=lambda *a: warnings.append(a)))
    return plugin, warnings


def test_a_pinned_server_risk_is_read_from_the_startup_config_copy():
    """The pin lives in ``_servers_config``, not in ``self.ctx.config``.

    ``self.ctx.config`` does not exist on a plugin, and the broad except that used to
    wrap it turned every pin into a silent miss: the value fell through to the blanket
    default while the comment promised an override. A dead knob that looks configured is
    worse than no knob, so this reads the same object the server loop reads.
    """
    plugin, warnings = _adapter_for_risk({
        "fs": {"tool_risk": "Read", "enabled": True},
        "payments": {"enabled": True},
        "typo": {"tool_risk": "readonly"},
    })
    assert plugin._server_tool_risk("fs") == "read"
    assert plugin._server_tool_risk("payments") == ""
    assert plugin._server_tool_risk("typo") == ""
    assert len(warnings) == 1, "an ignored pin has to be said out loud"
    assert "readonly" in str(warnings[0])

    # The registration rule, stated once: a pin wins, anything else is external.
    assert str(plugin._server_tool_risk("fs") or "external") == "read"
    assert str(plugin._server_tool_risk("payments") or "external") == "external"


def test_a_missing_server_entry_never_invents_a_class():
    plugin, warnings = _adapter_for_risk({})
    assert plugin._server_tool_risk("absent") == ""
    assert warnings == []
    assert str(plugin._server_tool_risk("absent") or "external") == "external"
