# -*- coding: utf-8 -*-
"""Thinking blocks of an Anthropic tool turn must be sent back unchanged.

Sonnet 5.5 (``between_tools``) / Opus 5.5 return ``thinking`` blocks next to
``tool_use`` blocks. Callers keep an OpenAI-shaped history that cannot hold
them, so ChatAnthropic remembers the original turn by tool_use id and restores
it when the current tool round is sent back.
"""
from __future__ import annotations

from collections import OrderedDict
from types import SimpleNamespace as NS

import pytest

import utils.llm_client.anthropic_client as anthropic_client_module
from utils.llm_client.anthropic_client import (
    ChatAnthropic,
    _normalize_messages_to_anthropic,
    _remember_tool_turn,
)

_TURN = [
    {"type": "thinking", "thinking": "", "signature": "sig-1"},
    {"type": "text", "text": "Checking the weather."},
    {"type": "tool_use", "id": "toolu_1", "name": "weather", "input": {"city": "Tokyo"}},
]


@pytest.fixture(autouse=True)
def _fresh_replay_cache(monkeypatch):
    monkeypatch.setattr(anthropic_client_module, "_tool_turn_replay", OrderedDict())


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


def test_current_tool_round_replays_original_blocks():
    _remember_tool_turn(_TURN)
    _system, messages = _normalize_messages_to_anthropic(_history())
    assert messages[1] == {"role": "assistant", "content": _TURN}
    assert messages[2]["content"][0]["tool_use_id"] == "toolu_1"


def test_turns_before_the_last_user_message_are_not_replayed():
    _remember_tool_turn(_TURN)
    history = _history() + [
        {"role": "assistant", "content": "It is sunny."},
        {"role": "user", "content": "thanks"},
    ]
    _system, messages = _normalize_messages_to_anthropic(history)
    assert all(b["type"] != "thinking" for b in messages[1]["content"])


def test_unknown_or_mismatched_tool_ids_keep_the_built_blocks():
    _remember_tool_turn(_TURN)
    _system, messages = _normalize_messages_to_anthropic(_history("toolu_other"))
    assert all(b["type"] != "thinking" for b in messages[1]["content"])


def test_turn_without_thinking_is_not_remembered():
    _remember_tool_turn(_TURN[1:])
    _system, messages = _normalize_messages_to_anthropic(_history())
    assert all(b["type"] != "thinking" for b in messages[1]["content"])


@pytest.mark.asyncio
async def test_astream_remembers_thinking_tool_turn(monkeypatch):
    events = [
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

    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def __aiter__(self):
            self._it = iter(events)
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _Fake:
        def __init__(self, **_kwargs):
            self.messages = NS(stream=lambda **_kw: _Stream())

        def close(self):
            pass

    class _FakeAsync(_Fake):
        async def close(self):
            pass

    monkeypatch.setattr(anthropic_client_module, "Anthropic", _Fake)
    monkeypatch.setattr(anthropic_client_module, "AsyncAnthropic", _FakeAsync)
    monkeypatch.setattr(anthropic_client_module, "_record_anthropic_token_usage", lambda *_a: None)

    client = ChatAnthropic(model="claude-sonnet-5-5", base_url="https://api.anthropic.com/v1", api_key="k")
    try:
        async for chunk in client.astream([{"role": "user", "content": "weather?"}]):
            if chunk.finish_reason:
                break  # consumers may stop at the finish signal
    finally:
        await client.aclose()

    _system, messages = _normalize_messages_to_anthropic(_history())
    assert messages[1]["content"] == _TURN
