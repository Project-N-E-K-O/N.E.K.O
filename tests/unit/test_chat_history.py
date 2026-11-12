import json
import sqlite3

import pytest
from services.chat_history.store import browse, HistoryUnavailable


@pytest.fixture
def archive(tmp_path):
    directory = tmp_path / 'Alice'
    directory.mkdir()
    with sqlite3.connect(directory / 'time_indexed.db') as db:
        db.execute('CREATE TABLE time_indexed_original(id INTEGER PRIMARY KEY, session_id TEXT, message TEXT, timestamp TEXT)')
        for i in range(1, 67):
            data = {'type': 'human' if i % 2 else 'ai', 'data': {'content': f'Hello 音乐 {i}', 'metadata': {}}}
            if i == 15:
                data['data']['metadata']['subject_id'] = 'private-other'
            db.execute('INSERT INTO time_indexed_original VALUES(?,?,?,?)', (i, 'one' if i < 40 else 'two', json.dumps(data), '2026-11-01 12:00:00' if i < 40 else '2026-11-02 13:00:00'))
    return tmp_path


def test_pages_no_gaps_or_duplicates(archive):
    ids, cursor = [], 0
    while True:
        page = browse(archive, 'Alice', cursor=cursor, limit=7)
        ids.extend(m['id'] for m in page['messages'])
        if not page['nextCursor']:
            break
        cursor = page['nextCursor']
    assert ids == [i for i in range(66, 0, -1) if i != 15]


def test_filters_literal_search_and_date(archive):
    page = browse(archive, 'Alice', day='2026-11-01', query='HELLO 音乐', role='user', order='asc', limit=100)
    assert [m['id'] for m in page['messages']] == [i for i in range(1, 40, 2) if i != 15]
    assert not browse(archive, 'Alice', query='%')['messages']
    assert not browse(archive, 'Alice', query="' OR 1=1 --")['messages']


def test_context_is_session_scoped(archive):
    page = browse(archive, 'Alice', anchor=39)
    assert page['messages'][-1]['id'] == 39
    assert 15 not in [m['id'] for m in page['messages']]
    with pytest.raises(LookupError):
        browse(archive, 'Alice', anchor=15)


def test_missing_corrupt_and_paths(archive):
    assert browse(archive, 'Missing')['available'] is False
    for name in ('../Alice', '.', '..', '/Alice'):
        with pytest.raises(ValueError):
            browse(archive, name)
    (archive / 'Alias').symlink_to(archive / 'Alice')
    with pytest.raises(ValueError):
        browse(archive, 'Alias')
    (archive / 'Alice' / 'time_indexed.db').write_bytes(b'invalid')
    with pytest.raises(HistoryUnavailable):
        browse(archive, 'Alice')


def test_scan_budget_can_continue(archive):
    with sqlite3.connect(archive / 'Alice' / 'time_indexed.db') as db:
        db.executemany('INSERT INTO time_indexed_original VALUES(?,?,?,?)', [(i, 'one', '{}', '2026-11-03') for i in range(67, 5100)])
    first = browse(archive, 'Alice', query='Hello')
    assert first['messages'] == [] and first['nextCursor']
    assert browse(archive, 'Alice', cursor=first['nextCursor'], query='Hello')['messages']


def test_date_cursor_uses_index_and_closes_connections(archive, monkeypatch):
    with sqlite3.connect(archive / 'Alice' / 'time_indexed.db') as db:
        db.execute('CREATE INDEX idx_time_indexed_original_timestamp ON time_indexed_original(timestamp)')
        db.executemany('INSERT INTO time_indexed_original VALUES(?,?,?,?)',
                       [(i, 'one', '{}', '2026-11-03') for i in range(67, 20067)])
    connections = []
    class TrackedConnection(sqlite3.Connection):
        closed = False
        steps = 0
        def set_progress_handler(self, callback, interval):
            def tracked():
                self.steps += 1
                return callback()
            return super().set_progress_handler(tracked, interval)
        def close(self):
            self.closed = True
            return super().close()
    connect = sqlite3.connect
    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs, factory=TrackedConnection)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', tracked_connect)
    ids, cursor = [], 0
    while True:
        page = browse(archive, 'Alice', day='2026-11-01', cursor=cursor, limit=7)
        ids.extend(m['id'] for m in page['messages'])
        cursor = page['nextCursor']
        if cursor is None:
            break
    assert ids == [i for i in range(39, 0, -1) if i != 15]
    assert not browse(archive, 'Alice', day='2025-01-01')['messages']
    assert not browse(archive, 'Alice', day='9999-12-31')['messages']
    with pytest.raises(LookupError):
        browse(archive, 'Alice', anchor=15)
    assert all(c.closed for c in connections)
    assert sum(c.steps for c in connections) < 50  # No scan of the 20,000 unrelated rows.


def test_deeply_nested_json_does_not_block_following_rows(archive):
    with sqlite3.connect(archive / 'Alice' / 'time_indexed.db') as db:
        db.execute('UPDATE time_indexed_original SET message=? WHERE id=66', ('[' * 2000 + '0' + ']' * 2000,))
    assert browse(archive, 'Alice', limit=1)['messages'][0]['id'] == 65


def test_router_validation_and_offloaded_read(archive, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from main_routers import chat_history_router as module
    class Config:
        memory_dir = archive
        async def aload_characters(self):
            return {'猫娘': {'Alice': {}}}
    monkeypatch.setattr(module, 'get_config_manager', lambda: Config())
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[module.local_access] = lambda: None
    with TestClient(app) as client:
        assert client.get('/api/chat-history/messages?character=Alice&limit=0').status_code == 422
        assert client.get('/api/chat-history/messages?character=Alice&day=oops').status_code == 422
        assert client.get('/api/chat-history/messages?character=Other').status_code == 404
        assert client.get('/api/chat-history/messages?character=Alice&anchor=15').status_code == 404
        assert client.get('/api/chat-history/messages?character=Alice&q=音乐').json()['messages']
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        assert client.get('/api/chat-history/characters').status_code == 403
