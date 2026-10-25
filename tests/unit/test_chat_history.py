"""Complete chat records are independent of reviewed recent memory."""

import json
import sqlite3

import pytest

from memory.chat_history import read_chat_history


UID = "a" * 32


def archive(path, rows):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE time_indexed_original(id INTEGER PRIMARY KEY,session_id TEXT,message TEXT,timestamp DATETIME)")
        conn.executemany("INSERT INTO time_indexed_original VALUES(?,?,?,?)", [
            (index + 1, "synthetic-session", json.dumps(row), "2026-10-20 12:43:15.123456")
            for index, row in enumerate(rows)])


def test_complete_archive_keeps_identical_records_and_never_reads_reviewed_memory(tmp_path):
    path = tmp_path / "time_indexed.db"
    archive(path, [{"type": "human", "data": {"content": "same"}}] * 2 + [
        {"type": "ai", "data": {"content": [{"type": "text", "text": "[20261020 Tue 12:43]original"}]}},
        {"type": "system", "data": {"content": "compressed memo"}},
        {"type": "human", "data": {"content": "camera scaffold", "metadata": {"sources": ["avatar-drop"]}}},
        {"type": "ai", "data": {"content": "theater", "metadata": {"source": "theater_numeric_v2"}}}])
    records = read_chat_history(path, UID)
    assert [row["text"] for row in records] == ["same", "same", "original"]
    assert len({row["messageId"] for row in records}) == 3
    (tmp_path / "recent.json").write_text('[{"type":"system","data":{"content":"reviewed"}}]')
    assert read_chat_history(path, UID) == records
    assert {row["messageId"] for row in read_chat_history(path, "b" * 32)}.isdisjoint(row["messageId"] for row in records)


def test_archive_reuses_producer_ids_and_keeps_tool_provenance(tmp_path):
    path = tmp_path / "time_indexed.db"
    proof = {"recordId": "1" * 32, "originalTime": "1234567890123", "origin": "tool"}
    archive(path, [{"type": "ai", "data": {"content": "tool reply", "metadata": {"chat_original_source": proof}}}])
    row, = read_chat_history(path, UID)
    assert row["messageId"] == proof["recordId"] and row["origin"] == "tool"
    assert row["originalTime"] == proof["originalTime"]
    assert read_chat_history(tmp_path / "missing.db", UID) == []
    assert not (tmp_path / "missing.db").exists()


def test_actual_memory_projection_preserves_ids_outside_model_messages(tmp_path):
    from main_logic.cross_server import _assistant_history_message, _append_user_input_cache_to_history
    from utils.llm_client import convert_to_messages, messages_to_dict

    user = {"recordId": "1" * 32, "originalTime": "1234567890123", "origin": "chat"}
    reply = {"recordId": "2" * 32, "originalTime": "1234567890124", "origin": "chat"}
    projected = []
    _append_user_input_cache_to_history(projected, 'user', set(), user)
    projected.append(_assistant_history_message('[20261020 Tue 12:43]reply', reply))
    messages = convert_to_messages(projected)
    assert all('metadata' not in message.to_openai() for message in messages)
    path = tmp_path / 'time_indexed.db'
    archive(path, messages_to_dict(messages))
    records = read_chat_history(path, UID)
    assert [row['messageId'] for row in records] == [user['recordId'], reply['recordId']]
    assert [row['text'] for row in records] == ['user', 'reply']


@pytest.mark.parametrize('broken', ['not json', 'null', '[]', '{"type":"ai","data":[]}',
    '{"type":"ai","data":{"metadata":[1]}}', '{"type":"ai","data":{"metadata":{"chat_original_source":[1]}}}'])
def test_bad_archive_rows_do_not_hide_valid_neighbors(tmp_path, broken):
    path = tmp_path / 'time_indexed.db'
    archive(path, [{'type': 'human', 'data': {'content': 'before'}}, {}, {'type': 'ai', 'data': {'content': 'after'}}])
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE time_indexed_original SET message=? WHERE id=2', (broken,))
    assert [row['text'] for row in read_chat_history(path, UID)] == ['before', 'after']


def test_invalid_archive_time_stays_unknown_and_conflicting_identity_is_excluded(tmp_path):
    path = tmp_path / 'time_indexed.db'
    def row(text):
        return {'type': 'ai', 'data': {'content': text, 'metadata': {'chat_original_source': {'recordId': '1' * 32}}}}
    archive(path, [row('first'), {'type': 'human', 'data': {'content': 'retained'}}, row('changed'), row('first')])
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE time_indexed_original SET timestamp='invalid' WHERE id=2")
    records = read_chat_history(path, UID)
    assert len(records) == 1 and records[0]['text'] == 'retained'
    assert records[0]['originalTime'] is None and records[0]['timezone'] is None


def test_synthetic_drop_prompts_are_excluded_without_losing_voice_chat_or_tools(tmp_path):
    path = tmp_path / 'time_indexed.db'
    archive(path, [{'type': 'human', 'data': {'content': 'auto', 'metadata': {'sources': ['avatar-drop']}}},
        {'type': 'human', 'data': {'content': 'voice', 'metadata': {'sources': ['voice']}}},
        {'type': 'ai', 'data': {'content': 'tool', 'metadata': {'chat_original_source': {
            'recordId': '2' * 32, 'origin': 'tool'}}}}])
    assert [record['text'] for record in read_chat_history(path, UID)] == ['voice', 'tool']


@pytest.mark.parametrize('original_time', [1234567890123, '', False, {}, [], None])
@pytest.mark.parametrize('saved_at', ['2026-10-20 12:43:15.123456', 'invalid'])
def test_invalid_proof_time_uses_archive_time_or_remains_unknown(tmp_path, original_time, saved_at):
    path = tmp_path / 'time_indexed.db'
    archive(path, [{'type': 'human', 'data': {'content': 'kept', 'metadata': {
        'chat_original_source': {'recordId': '1' * 32, 'originalTime': original_time, 'origin': 'tool'}}}}])
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE time_indexed_original SET timestamp=?', (saved_at,))
    row, = read_chat_history(path, UID)
    assert row['origin'] not in ('tool', 'agent')
    if saved_at == 'invalid':
        assert row['originalTime'] is None and row['timezone'] is None
    else:
        assert isinstance(row['originalTime'], str) and row['originalTime'].isdigit()
        assert row['timezone'] == 'UTC'
