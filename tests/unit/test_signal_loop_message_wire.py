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
"""  # noqa: DOCSTRING_CJK
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


def test_extraction_preserves_original_sync_identity_and_time_after_restore():
    import json
    from datetime import datetime
    from app.memory_server.rows import _extract_role_tagged_messages_from_rows
    rows = [(datetime(2026, 10, 26), 'restored-session', json.dumps({'type': 'human', 'data': {
        'content': 'original text', 'metadata': {'recent_memory_id': 'a'*32, 'recent_memory_time': '1000'},
        'additional_kwargs': {'memory_source_id': 'forged', 'memory_source_created_at': 999999}}}))]
    messages = convert_to_messages(_extract_role_tagged_messages_from_rows(rows))
    assert messages[0].content == 'original text'
    assert messages[0].additional_kwargs == {'memory_source_id': 'sync:'+'a'*32, 'memory_source_created_at': 1000}


def test_restored_unknown_source_time_is_not_replaced_with_delivery_time():
    import json
    from datetime import datetime
    from app.memory_server.rows import _extract_role_tagged_messages_from_rows
    row = (datetime(2026, 10, 26), 'session', json.dumps({'type': 'human', 'data': {
        'content': 'legacy evidence', 'metadata': {'recent_memory_id': 'a'*32, 'recent_memory_time': None}}}))
    source = convert_to_messages(_extract_role_tagged_messages_from_rows([row]))[0].additional_kwargs
    assert source == {'memory_source_id': 'sync:'+'a'*32}


def test_extraction_and_original_lookup_share_iso_source_time():
    import json
    from datetime import datetime
    from app.memory_server.rows import _extract_role_tagged_messages_from_rows
    row = (datetime(2026, 10, 26), 'session', json.dumps({'type': 'human', 'data': {
        'content': 'original confirmation', 'metadata': {'chat_original_source': {
            'recordId': 'a'*32, 'originalTime': '2026-10-01T12:00:00+08:00'}},
        'additional_kwargs': {'memory_source_id': 'forged', 'memory_source_created_at': 999999}}}))
    source = convert_to_messages(_extract_role_tagged_messages_from_rows([row]))[0].additional_kwargs
    assert source == {'memory_source_id': 'sync:'+'a'*32, 'memory_source_created_at': 1790827200000}


def test_legacy_source_identity_stays_compatible_and_unreadable_time_stays_unknown():
    import json
    from app.memory_server.rows import _extract_role_tagged_messages_from_rows
    stored = {'type': 'human', 'data': {'content': 'legacy evidence'}}
    messages = convert_to_messages(_extract_role_tagged_messages_from_rows([
        ('2026-10-01 00:00:00.000000', 'session-legacy', json.dumps(stored))]))
    assert messages[0].additional_kwargs['memory_source_id'] == (
        'desktop:c10773a7e321da86cbd33175587eec9a22bcedcd4bc1773056b283195d46572a')
    messages = convert_to_messages(_extract_role_tagged_messages_from_rows([
        ('unreadable time', 'session-legacy', json.dumps(stored))]))
    assert 'memory_source_created_at' not in messages[0].additional_kwargs


def test_explicit_unknown_original_time_is_not_replaced_with_database_epoch_zero():
    import json
    from app.memory_server.rows import _extract_role_tagged_messages_from_rows
    for metadata in [
        {'recent_memory_id': 'a'*32, 'recent_memory_time': None},
        {'chat_original_source': {'recordId': 'a'*32, 'originalTime': None}},
    ]:
        stored = {'type': 'human', 'data': {'content': 'unknown original time', 'metadata': metadata}}
        source = convert_to_messages(_extract_role_tagged_messages_from_rows([(0, 'session', json.dumps(stored))]))[0]
        assert source.additional_kwargs == {'memory_source_id': 'sync:' + 'a'*32}
