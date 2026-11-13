import io
from contextlib import closing
from types import SimpleNamespace
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_logic.voice_archive import VoiceArchive
from main_routers import voice_archive_router as api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'get_config_manager', lambda: SimpleNamespace(app_docs_dir=tmp_path))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app, client=('127.0.0.1', 50000), base_url='http://localhost') as client:
        yield client, VoiceArchive(tmp_path / 'voice_archive/archive.sqlite3')


def save(archive, sid, record, character='YUI', complete=True):
    base = dict(character=character, speech_id=sid, proof={'recordId': record})
    archive.write(dict(base, kind='audio', data=b'\x01\x00' * 480))
    archive.write(dict(base, kind='text', text='identical text', is_first=True))
    if complete:
        archive.write(dict(base, kind='done'))


def test_exact_identity_and_wav(client):
    c, archive = client
    save(archive, 'a', 'record-a')
    save(archive, 'b', 'record-b', complete=False)
    save(archive, 'c', 'record-a', character='Other')
    response = c.get('/api/voice-archive/message', params={'character': 'YUI', 'record_id': 'record-a'})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    clips = response.json()['clips']
    assert len(clips) == 1 and clips[0]['complete']
    audio = c.get(clips[0]['url'])
    assert audio.headers['content-type'] == 'audio/wav'
    with wave.open(io.BytesIO(audio.content)) as wav:
        assert wav.getframerate() == 48000
        assert wav.readframes(480) == b'\x01\x00' * 480
    live = c.get('/api/voice-archive/message', params={'character': 'YUI', 'speech_id': 'b'})
    assert live.json()['clips'][0]['complete'] is False
    missing = c.get('/api/voice-archive/message', params={'character': 'YUI', 'record_id': 'unknown'})
    assert missing.json() == {'clips': []}


def test_missing_database_does_not_create_it(client):
    c, archive = client
    assert c.get('/api/voice-archive/message?character=YUI&record_id=a').status_code == 404
    assert not archive.path.exists()


def test_cross_origin_and_remote_denied(client):
    c, _ = client
    assert c.get('/api/voice-archive/message?character=YUI&record_id=a',
                 headers={'Origin': 'https://evil.example', 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app, client=('192.0.2.1', 50000)) as remote:
        assert remote.get('/api/voice-archive/message?character=YUI&record_id=a').status_code == 403


def test_corrupt_archive_not_played(client):
    import sqlite3
    c, archive = client
    save(archive, 'a', 'r')
    url = c.get('/api/voice-archive/message?character=YUI&record_id=r').json()['clips'][0]['url']
    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("UPDATE chunks SET data=x'0000'")
    assert c.get(url).status_code == 409
