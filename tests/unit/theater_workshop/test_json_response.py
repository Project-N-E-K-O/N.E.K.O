from __future__ import annotations

import json

import pytest

from theater_workshop.sdk.json_response import JSONResponseParser


@pytest.mark.parametrize("wrapper", ["plain", "fenced", "trailing_comma", "missing_comma"])
def test_json_repair_preserves_every_string_value(wrapper):
    parser = JSONResponseParser()
    expected = {"text": '记录原文：,}；,]；"称呼"；\\path\n下一行',
                "nested": ["[,}]", {"value": "原文, ]"}]}
    response = json.dumps(expected, ensure_ascii=False)
    if wrapper == "fenced":
        response = "```json\n" + response + "\n```"
    elif wrapper == "trailing_comma":
        response = response[:-1] + ",}"
    elif wrapper == "missing_comma":
        response = response.replace(', "nested":', ' "nested":')
    assert parser.parse_json_response(response) == expected


def test_json_repair_keeps_bounded_missing_delimiter_compatibility():
    assert JSONResponseParser().parse_json_response('{"a": {"b": 1 "c": 2} "d": 3}') == {
        "a": {"b": 1, "c": 2}, "d": 3,
    }


def test_json_repair_keeps_raw_control_characters_without_guessing_quotes():
    parser = JSONResponseParser()
    assert parser.parse_json_response('{"text":"第一行\n第二行",}') == {"text": "第一行\n第二行"}
    response = '{"text":"left "right" end"}'
    assert parser.parse_json_response(response).get("parse_error") is True


def test_json_repair_preserves_nested_raw_controls_and_bounded_attempts():
    parser = JSONResponseParser()
    expected = {"items": ["第一行\n第二行", {"text": 'tab\t原文,]；\\；"引号"'}]}
    response = json.dumps(expected, ensure_ascii=False).replace("\\n", "\n").replace("\\t", "\t")
    assert parser.parse_json_response(response[:-1] + ",}") == expected
    assert parser.parse_json_response('{"a":1 "b":2 "c":3 "d":4 "e":5}').get("parse_error") is True
