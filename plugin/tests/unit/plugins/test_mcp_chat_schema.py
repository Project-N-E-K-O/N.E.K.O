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
