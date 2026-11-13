"""Owner-only local audio archive. Never imported by memory/recall or sync uploads.

The immutable chat_original_source.recordId joins audio to time_indexed_original.
Audio is stored transactionally as original transport chunks, without transcoding.
No network calls, credentials, automatic pruning, or modifications to chat history.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY, character TEXT NOT NULL, speech_id TEXT NOT NULL,
    record_id TEXT, original_time TEXT, origin TEXT, text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    stream_complete INTEGER NOT NULL DEFAULT 0,
    archive_error INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}', UNIQUE(character, speech_id)
);
CREATE INDEX IF NOT EXISTS clips_record ON clips(record_id);
CREATE TABLE IF NOT EXISTS chunks (
    clip_id TEXT NOT NULL REFERENCES clips(id), seq INTEGER NOT NULL,
    format TEXT NOT NULL, data BLOB NOT NULL, sha256 TEXT NOT NULL,
    playback_gain REAL NOT NULL DEFAULT 1, PRIMARY KEY(clip_id, seq)
);
CREATE TABLE IF NOT EXISTS text_events (
    id INTEGER PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id),
    text TEXT NOT NULL, is_first INTEGER NOT NULL, created_at TEXT NOT NULL
);
PRAGMA user_version=1;
"""


class VoiceArchive:
    def __init__(self, path):
        self.path = Path(path)

    def write(self, event):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript(SCHEMA)
            self.path.chmod(0o600)
            now = datetime.now(timezone.utc).isoformat()
            character, sid = event['character'], event['speech_id']
            clip_id = hashlib.sha256((character + '\0' + sid).encode()).hexdigest()
            proof = event.get('proof') or {}
            with db:
                db.execute('INSERT OR IGNORE INTO clips(id,character,speech_id,created_at,updated_at) '
                           'VALUES (?,?,?,?,?)', (clip_id, character, sid, now, now))
                db.execute('UPDATE clips SET updated_at=? WHERE id=?', (now, clip_id))
                if event.get('archive_error'):
                    db.execute('UPDATE clips SET archive_error=1 WHERE id=?', (clip_id,))
                if proof.get('recordId'):
                    old = db.execute('SELECT record_id FROM clips WHERE id=?', (clip_id,)).fetchone()[0]
                    if old and old != proof['recordId']:
                        raise ValueError('voice archive identity conflict')
                    db.execute('UPDATE clips SET record_id=?,original_time=?,origin=? WHERE id=?',
                               (proof['recordId'], proof.get('originalTime'), proof.get('origin'), clip_id))
                if event.get('metadata'):
                    db.execute('UPDATE clips SET metadata=? WHERE id=?',
                               (json.dumps(event['metadata'], ensure_ascii=False), clip_id))
                kind = event['kind']
                if kind == 'text':
                    text = event['text']
                    first = bool(event.get('is_first'))
                    db.execute('INSERT INTO text_events(clip_id,text,is_first,created_at) VALUES(?,?,?,?)',
                               (clip_id, text, first, now))
                    db.execute('UPDATE clips SET text=CASE WHEN ? THEN ? ELSE text || ? END WHERE id=?',
                               (first, text, text, clip_id))
                elif kind == 'audio':
                    data = event['data']
                    # Same wire format as app-audio-playback: Ogg/Opus or mono PCM16/48kHz.
                    previous = db.execute('SELECT format FROM chunks WHERE clip_id=? ORDER BY seq DESC LIMIT 1',
                                          (clip_id,)).fetchone()
                    fmt = 'ogg' if data.startswith(b'OggS') else (previous[0] if previous else 'pcm_s16le_48000')
                    seq = db.execute('SELECT COALESCE(MAX(seq),-1)+1 FROM chunks WHERE clip_id=?', (clip_id,)).fetchone()[0]
                    db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
                               (clip_id, seq, fmt, data, hashlib.sha256(data).hexdigest(), event.get('gain', 1.0)))
                    # Any tail after a premature terminal makes completeness unconfirmed again.
                    db.execute('UPDATE clips SET stream_complete=0 WHERE id=?', (clip_id,))
                elif kind == 'done':
                    db.execute('UPDATE clips SET stream_complete=1 WHERE id=?', (clip_id,))
                else:
                    raise ValueError('unknown archive event')


def _mark_incomplete(manager, speech_id):
    failed = getattr(manager, '_owner_voice_archive_failed', None)
    if failed is None:
        failed = manager._owner_voice_archive_failed = set()
    if len(failed) >= 256:
        failed.pop()
    failed.add(str(speech_id))


async def archive_event(manager, kind, speech_id, **values):
    """Await durable writes off the event loop; archive errors never break speech.

Snapshot turn identity before yielding. Do not create a chat proof for an audio
callback: proactive/tool origins may not have been assigned yet. A later text
event fills the same speech-id row even when the provider emits audio first.
"""
    try:
        cm = getattr(manager, '_config_manager', None)
        if cm is None or not speech_id:
            return
        character = str(getattr(manager, 'lanlan_name', ''))
        # Capture identity before settings IO yields to another turn.
        proof = dict(getattr(manager, '_chat_original_turns', {}).get(str(speech_id), {}))
        from utils.voice_archive_settings import load_settings
        settings = await asyncio.to_thread(load_settings, cm)
        if not settings['enabled'] or not character:
            _mark_incomplete(manager, speech_id)
            return
        if not hasattr(manager, '_owner_voice_archive'):
            manager._owner_voice_archive = VoiceArchive(Path(cm.app_docs_dir) / 'voice_archive' / 'archive.sqlite3')
            manager._owner_voice_archive_lock = asyncio.Lock()
        archive = manager._owner_voice_archive
        event = dict(values, kind=kind, character=character, speech_id=str(speech_id), proof=proof)
        if kind == 'audio':
            if not isinstance(event.get('data'), (bytes, bytearray)) or not event['data']:
                return
            event['data'] = bytes(event['data'])
        if kind == 'text':
            cfg = await asyncio.to_thread(cm.get_core_config)
            event['metadata'] = {k: cfg.get(k) for k in ('ttsModelProvider', 'TTS_MODEL', 'TTS_VOICE_ID')}
            event['metadata']['character_voice_id'] = getattr(manager, 'voice_id', None)
        async with manager._owner_voice_archive_lock:
            event['archive_error'] = str(speech_id) in getattr(manager, '_owner_voice_archive_failed', set())
            # A cancellation must not release ordering while the thread is still committing.
            def write_event():
                from utils.cloudsave_runtime import assert_cloudsave_writable
                assert_cloudsave_writable(cm, operation='voice_archive_write', target=str(archive.path))
                archive.write(event)
            task = asyncio.create_task(asyncio.to_thread(write_event))
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
            task.result()
            if cancelled:
                raise asyncio.CancelledError
    except Exception:
        _mark_incomplete(manager, speech_id)
        log.warning('Voice archive write failed', exc_info=True)
