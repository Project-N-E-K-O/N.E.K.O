# -*- coding: utf-8 -*-
"""Regression: SignalLoop's user_msgs_text → convert_to_messages wire.

PR #929 added ``_periodic_signal_extraction_loop._signal_check_one`` and
wrapped the list of message dicts with ``json.dumps()`` before handing it
to ``utils.llm_client.convert_to_messages``. The helper has only ever
accepted lists (PR #547), so the JSON-string input took the silent
``return []`` fallback. ``messages`` ended up empty, ``_format_conversation``
rendered an empty string, and the Stage-1 LLM prompt had
``======以下为对话======`` followed by nothing — LLM consistently returned
``[]``. PR #1346 later stripped the per-turn OFF-mode ``extract_facts``
fallback for ON-mode users, at which point new-fact growth froze entirely
once the Stage-2 ``signal_processed=False`` backlog drained (~2026-05-15).

These tests pin down the wire path so a future refactor can't reintroduce
the same mistake without it lighting up red immediately.
"""
from utils.llm_client import convert_to_messages


def test_signal_loop_wire_yields_human_messages_with_content():
    """Mirror SignalLoop's `_signal_check_one` message-building. The
    transformation must yield a list of BaseMessage with .content set —
    not an empty list."""
    user_msgs_text = ["主人喜欢吃鱼", "今天天气不错"]
    message_dicts = [
        {'type': 'human', 'data': {'content': m}}
        for m in user_msgs_text
    ]
    # convert_to_messages must receive a list directly — wrapping with
    # json.dumps() (the PR #929 mistake) silently returns [].
    messages = convert_to_messages(message_dicts)
    assert len(messages) == 2, "wire path dropped messages"
    assert messages[0].type == 'human'
    assert messages[0].content == "主人喜欢吃鱼"
    assert messages[1].content == "今天天气不错"


def test_convert_to_messages_silently_drops_str_input():
    """Document the existing helper contract: string inputs (the shape
    PR #929 accidentally produced) collapse to []. If a future refactor
    changes convert_to_messages to also accept str, this test will fail
    loudly — the author should then audit every call site to make sure
    nobody is relying on the silent-drop behaviour."""
    assert convert_to_messages("[]") == []
    assert convert_to_messages('[{"type": "human", "data": {"content": "x"}}]') == []
