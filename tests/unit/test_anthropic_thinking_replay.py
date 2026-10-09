# -*- coding: utf-8 -*-
"""Thinking blocks of an Anthropic tool turn must be sent back unchanged.

Sonnet 5.5 (``between_tools``) / Opus 5.5 return ``thinking`` blocks next to
``tool_use`` blocks. Callers keep an OpenAI-shaped history that cannot hold
them, so ChatAnthropic remembers the original turn by tool_use id and restores
it when the current tool round is sent back with the same system and tools.
"""
from __future__ import annotations

from collections import OrderedDict
from types import SimpleNamespace as NS

import pytest

import utils.llm_client.anthropic_client as anthropic_client_module
from utils.llm_client.anthropic_client import ChatAnthropic, _remember_tool_turn

_TOOLS = [{
    "type": "function",
    "function": {
        "name": "weather",
        "description": "Look up the weather.",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    },
}]

_TURN = [
    {"type": "thinking", "thinking": "", "signature": "sig-1"},
    {"type": "text", "text": "Checking the weather."},
    {"type": "tool_use", "id": "toolu_1", "name": "weather", "input": {"city": "Tokyo"}},
]


class _Stream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def __aiter__(self):
        self._it = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(anthropic_client_module, "_tool_turn_replay", OrderedDict())
    events_box: dict = {"events": []}

    class _Fake:
        def __init__(self, **_kwargs):
            self.messages = NS(stream=lambda **_kw: _Stream(events_box["events"]))

        def close(self):
            pass

    class _FakeAsync(_Fake):
        async def close(self):
            pass

    monkeypatch.setattr(anthropic_client_module, "Anthropic", _Fake)
    monkeypatch.setattr(anthropic_client_module, "AsyncAnthropic", _FakeAsync)
    monkeypatch.setattr(anthropic_client_module, "_record_anthropic_token_usage", lambda *_a: None)
    llm = ChatAnthropic(
        model="claude-sonnet-5-5",
        base_url="https://api.anthropic.com/v1",
        api_key="k",
        tools=_TOOLS,
    )
    llm._events_box = events_box
    return llm


def _history(tool_call_id="toolu_1"):
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "weather?"},
        {
            "role": "assistant",
            "content": "Checking the weather.",
            "tool_calls": [{
                "id": tool_call_id,
                "type": "function",
                "function": {"name": "weather", "arguments": "{\"city\": \"Tokyo\"}"},
            }],
        },
        {"role": "tool", "tool_call_id": tool_call_id, "content": "sunny"},
    ]


def _first_request_key(client, history=None):
    """Context key of the request that produced the tool turn (everything before it)."""
    request = (history or _history())[:2]
    payload = client._build_payload_for_call(request, {})
    return anthropic_client_module._replay_context_key(payload, payload["messages"])


def _remember_for(client, history=None):
    _remember_tool_turn(_TURN, _first_request_key(client, history))


def _assistant_content(client, history, **overrides):
    payload = client._build_payload_for_call(history, overrides)
    return [m for m in payload["messages"] if m["role"] == "assistant"][0]["content"]


def test_current_tool_round_replays_original_blocks(client):
    _remember_for(client)
    assert _assistant_content(client, _history()) == _TURN


def test_request_without_the_original_tools_does_not_replay(client):
    # Forced-finalize drops tools; replaying would fail the preserved-thinking prefix check.
    _remember_for(client)
    content = _assistant_content(client, _history(), tools=None)
    assert all(b["type"] != "thinking" for b in content)


def test_changed_system_prompt_does_not_replay(client):
    _remember_for(client)
    history = _history()
    history[0] = {"role": "system", "content": "other"}
    assert all(b["type"] != "thinking" for b in _assistant_content(client, history))


def test_turns_before_the_last_user_message_are_not_replayed(client):
    _remember_for(client)
    history = _history() + [
        {"role": "assistant", "content": "It is sunny."},
        {"role": "user", "content": "thanks"},
    ]
    assert all(b["type"] != "thinking" for b in _assistant_content(client, history))


def test_unknown_tool_ids_keep_the_built_blocks(client):
    _remember_for(client)
    content = _assistant_content(client, _history("toolu_other"))
    assert all(b["type"] != "thinking" for b in content)


def test_changed_tool_arguments_do_not_replay(client):
    _remember_for(client)
    history = _history()
    history[2]["tool_calls"][0]["function"]["arguments"] = "{\"city\": \"Osaka\"}"
    assert all(b["type"] != "thinking" for b in _assistant_content(client, history))


def test_sanitized_assistant_text_still_replays(client):
    _remember_for(client)
    history = _history()
    history[2]["content"] = "Checking"
    assert _assistant_content(client, history) == _TURN


@pytest.mark.asyncio
async def test_astream_skips_turns_that_did_not_stop_for_tool_use(client):
    client._events_box["events"] = [
        NS(type="content_block_start", index=0, content_block=NS(type="thinking", thinking="", signature="s")),
        NS(type="content_block_start", index=1, content_block=NS(type="tool_use", id="toolu_1", name="weather", input={"city": "Tokyo"})),
        NS(type="message_delta", delta=NS(stop_reason="max_tokens", usage=None), usage=None),
    ]
    try:
        async for _chunk in client.astream([{"role": "user", "content": "weather?"}]):
            pass
    finally:
        await client.aclose()
    assert not anthropic_client_module._tool_turn_replay


def test_tool_result_with_image_still_replays(client):
    # Tool-result images arrive as an adjacent user message and merge with the tool_result.
    _remember_for(client)
    history = _history() + [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
            {"type": "text", "text": "[tool image]"},
        ],
    }]
    assert _assistant_content(client, history) == _TURN


def test_rewritten_earlier_history_does_not_replay(client):
    _remember_for(client)
    history = _history()
    history[1] = {"role": "user", "content": "weather? (trimmed)"}
    assert all(b["type"] != "thinking" for b in _assistant_content(client, history))


def test_lookup_refreshes_the_entry(client, monkeypatch):
    monkeypatch.setattr(anthropic_client_module, "_TOOL_TURN_REPLAY_MAX", 2)
    key = _first_request_key(client)
    _remember_tool_turn(_TURN, key)
    _remember_tool_turn([_TURN[0], {**_TURN[2], "id": "toolu_2"}], key)
    _assistant_content(client, _history())  # hit on toolu_1 makes toolu_2 the oldest
    _remember_tool_turn([_TURN[0], {**_TURN[2], "id": "toolu_3"}], key)
    assert list(anthropic_client_module._tool_turn_replay) == ["toolu_1", "toolu_3"]


def test_turn_without_thinking_is_not_remembered(client):
    _remember_tool_turn(_TURN[1:], _first_request_key(client))
    assert all(b["type"] != "thinking" for b in _assistant_content(client, _history()))


@pytest.mark.asyncio
async def test_astream_remembers_thinking_tool_turn(client):
    client._events_box["events"] = [
        NS(type="message_start", message=NS(usage=None)),
        NS(type="content_block_start", index=0, content_block=NS(type="thinking", thinking="", signature="")),
        NS(type="content_block_delta", index=0, delta=NS(type="thinking_delta", thinking="")),
        NS(type="content_block_delta", index=0, delta=NS(type="signature_delta", signature="sig-1")),
        NS(type="content_block_stop", index=0),
        NS(type="content_block_start", index=1, content_block=NS(type="text", text="")),
        NS(type="content_block_delta", index=1, delta=NS(type="text_delta", text="Checking the weather.")),
        NS(type="content_block_stop", index=1),
        NS(type="content_block_start", index=2, content_block=NS(type="tool_use", id="toolu_1", name="weather", input={})),
        NS(type="content_block_delta", index=2, delta=NS(type="input_json_delta", partial_json="{\"city\": ")),
        NS(type="content_block_delta", index=2, delta=NS(type="input_json_delta", partial_json="\"Tokyo\"}")),
        NS(type="content_block_stop", index=2),
        NS(type="message_delta", delta=NS(stop_reason="tool_use", usage=None), usage=None),
        NS(type="message_stop", message=None),
    ]
    first_request = [{"role": "system", "content": "sys"}, {"role": "user", "content": "weather?"}]
    try:
        async for chunk in client.astream(first_request):
            if chunk.finish_reason:
                break  # consumers may stop at the finish signal
        assert _assistant_content(client, _history()) == _TURN
    finally:
        await client.aclose()
