import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import chat_history_router, voice_archive_router


@pytest.fixture
def client(tmp_path, monkeypatch):
    memory = tmp_path / 'memory' / 'Alice'
    memory.mkdir(parents=True)
    with sqlite3.connect(memory / 'time_indexed.db') as db:
        db.execute('CREATE TABLE time_indexed_original(id INTEGER PRIMARY KEY,session_id TEXT,message TEXT,timestamp TEXT)')
        db.execute('CREATE INDEX timestamps ON time_indexed_original(timestamp)')
        for i in range(1, 66):
            payload = {'type': 'ai', 'data': {'content': f'message {i}', 'metadata': {'chat_original_source': {'recordId': f'record-{i}'}}}}
            # ID order deliberately differs from chronological order, with ties.
            stamp = '2026-11-02 12:00:00' if i <= 32 else '2026-11-01 12:00:00'
            db.execute('INSERT INTO time_indexed_original VALUES(?,?,?,?)', (i, 'session', json.dumps(payload), stamp))
    voice = tmp_path / 'voice_archive'
    voice.mkdir()
    with sqlite3.connect(voice / 'archive.sqlite3') as db:
        db.executescript('CREATE TABLE clips(id TEXT PRIMARY KEY,character TEXT,record_id TEXT,speech_id TEXT,created_at TEXT,stream_complete INTEGER,archive_error INTEGER); CREATE INDEX records ON clips(record_id); CREATE TABLE chunks(clip_id TEXT,seq INTEGER,format TEXT,data BLOB,sha256 TEXT,PRIMARY KEY(clip_id,seq));')
        for i in range(1, 66):
            clip = hashlib.sha256(str(i).encode()).hexdigest()
            # Row 65 has a same-ID recording for another character only.
            db.execute('INSERT INTO clips VALUES(?,?,?,?,?,?,?)', (clip, 'Bob' if i == 65 else 'Alice', f'record-{i}', str(i), '', 1, 0))
            if i != 64:  # A text-only archive event is not a playable recording.
                raw = b'\0\0' * 480
                db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)', (clip, 0, 'pcm_s16le_48000', raw, hashlib.sha256(raw).hexdigest()))
    class Config(SimpleNamespace):
        async def aload_characters(self):
            return {'猫娘': {'Alice': {}}}
    config = Config(memory_dir=tmp_path / 'memory', app_docs_dir=tmp_path)
    monkeypatch.setattr(chat_history_router, 'get_config_manager', lambda: config)
    monkeypatch.setattr(voice_archive_router, 'get_config_manager', lambda: config)
    app = FastAPI()
    app.include_router(chat_history_router.router)
    app.include_router(voice_archive_router.router)
    with TestClient(app, client=('127.0.0.1', 50000), base_url='http://localhost') as client:
        yield client, tmp_path


def test_history_exposes_only_exact_playable_voice_and_serves_original_audio(client):
    web, _ = client
    entries = web.get('/api/chat-history/messages', params={'character': 'Alice'}).json()['messages']
    assert 'voice' not in entries[0]  # other character, same durable ID
    assert 'voice' not in entries[1]  # no audio chunks
    assert entries[2]['voice'] == {'recordId': 'record-63'}
    manifest = web.get('/api/voice-archive/message', params={'character': 'Alice', 'record_id': 'record-63'}).json()
    audio = web.get(manifest['clips'][0]['url'])
    assert audio.status_code == 200 and audio.content.startswith(b'RIFF')
    assert audio.headers['cache-control'] == 'no-store'


def test_playback_pages_follow_time_then_id_and_keep_date_search_scope(client):
    web, _ = client
    params = {'character': 'Alice'}
    ids = []
    while True:
        response = web.get('/api/chat-history/playback', params=params)
        assert response.status_code == 200
        page = response.json()
        assert len(page['messages']) <= 30
        ids.extend(row['id'] for row in page['messages'])
        if page['nextCursor'] is None:
            break
        params.update(cursor=page['nextCursor'], after_time=page['nextTime'])
    assert ids == list(range(33, 64)) + list(range(1, 33))
    scoped = web.get('/api/chat-history/playback', params={'character': 'Alice', 'day': '2026-11-02', 'q': 'message 1'}).json()
    assert [m['id'] for m in scoped['messages']] == [1, *range(10, 20)]
    assert web.get('/api/chat-history/playback', params={'character': 'Alice', 'cursor': 30}).status_code == 422
    assert web.get('/api/chat-history/playback', params={'character': 'Unknown'}).status_code == 404


def test_no_voice_archive_stops_without_scanning_every_page(client):
    web, root = client
    (root / 'voice_archive/archive.sqlite3').rename(root / 'voice_archive/retained.sqlite3')
    page = web.get('/api/chat-history/playback', params={'character': 'Alice'}).json()
    assert page['messages'] == [] and page['nextCursor'] is None
    assert web.get('/api/chat-history/messages', params={'character': 'Alice'}).json()['messages']


def test_unavailable_voice_archive_does_not_hide_text_or_claim_success(client):
    web, root = client
    (root / 'voice_archive/archive.sqlite3').write_bytes(b'broken')
    assert web.get('/api/chat-history/messages', params={'character': 'Alice'}).json()['messages']
    assert web.get('/api/chat-history/playback', params={'character': 'Alice'}).status_code == 503
