"""Keep bounded field semantics in the active plugin selection catalog."""

from __future__ import annotations

import copy
import json

import pytest

from config import AGENT_PLUGIN_FIELD_DESCRIPTION_MAX_TOKENS
from nekoclaw.host.selection import build_desc_lines
from utils.tokenize import count_tokens, truncate_to_tokens

pytestmark = pytest.mark.unit


def _render(properties, *, required=()):
    catalog = [{
        "id": "search", "description": "Search tools", "entries": [{
            "id": "run", "input_schema": {
                "type": "object", "properties": properties, "required": list(required),
            },
        }],
    }]
    original = copy.deepcopy(catalog)
    lines = build_desc_lines(catalog)
    assert catalog == original
    assert len(lines) == 1
    return lines[0]


def test_schema_descriptions_distinguish_resolved_queries_from_raw_messages():
    query = "Standalone search terms for the requested subject."
    message = "Keep the user's original message verbatim."
    line = _render({
        "query": {"type": "string", "description": query},
        "message": {"type": "string", "description": message},
    }, required=["query"])

    assert f"query:string description={json.dumps(query)}" in line
    assert f"message:string description={json.dumps(message)}" in line
    assert "required=['query']" in line


@pytest.mark.parametrize("description", [
    "Preserve the original user wording without rewriting or translating it. " * 100,
    "根据当前请求的对象生成独立搜索词，保留用户最后的更正。" * 100,
], ids=["english", "chinese"])
def test_long_property_descriptions_are_limited_to_eighty_tokens(description):
    assert AGENT_PLUGIN_FIELD_DESCRIPTION_MAX_TOKENS == 80
    line = _render({"query": {"type": "string", "description": description}})

    assert " description=" in line
    rendered, _ = json.JSONDecoder().raw_decode(line.split(" description=", 1)[1])
    assert rendered == truncate_to_tokens(description.strip(), 80)
    assert count_tokens(rendered) <= 80
    assert description not in line


def test_property_descriptions_quote_newlines_backslashes_and_unicode():
    description = '  Keep "quoted"\npath C:\\Temp and 日本語 exactly.\tDo not summarize.  '
    line = _render({"message": {"type": "string", "description": description}})

    escaped = json.dumps(description.strip(), ensure_ascii=False)
    assert f"description={escaped}" in line
    assert "\n" not in line and "\t" not in line
    assert "日本語" in line
    rendered, _ = json.JSONDecoder().raw_decode(line.split(" description=", 1)[1])
    assert rendered == description.strip()


@pytest.mark.parametrize("description", [None, "", " \n\t ", 0, False, [], {}, ["text"]])
def test_empty_or_nonstring_descriptions_do_not_change_the_field(description):
    line = _render({"query": {"type": "string", "description": description}})
    assert line == "- search: Search tools | entries: [run args(query:string)]"


@pytest.mark.parametrize("field_count", [8, 9])
@pytest.mark.parametrize("enum_count", [12, 13])
def test_descriptions_keep_eight_field_and_twelve_enum_boundaries(field_count, enum_count):
    properties = {
        f"field{index}": {"type": "string", "description": f"Meaning of field {index}"}
        for index in range(field_count)
    }
    properties["field0"]["enum"] = [f"v{index}" for index in range(enum_count)]
    line = _render(properties, required=["field0"])

    shown = "|".join(f"v{index}" for index in range(12))
    suffix = "|... +1 more" if enum_count == 13 else ""
    assert f'field0:string enum=[{shown}{suffix}] description="Meaning of field 0"' in line
    assert line.count(" description=") == 8
    assert 'field7:string description="Meaning of field 7"' in line
    assert "field8" not in line and "Meaning of field 8" not in line
    assert "v12" not in line
    assert "required=['field0']" in line


def test_nonmapping_field_definitions_keep_the_existing_any_type():
    line = _render({"empty": {}, "null": None, "text": "string", "list": []})
    assert line == "- search: Search tools | entries: [run args(empty:any, null:any, text:any, list:any)]"
