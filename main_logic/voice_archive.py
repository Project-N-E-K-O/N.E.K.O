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
import time

log = logging.getLogger(__name__)
MAX_PENDING_EVENTS = 256
WRITER_IDLE_SECONDS = 2

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
                    # Provider fallback can change formats mid-speech. The
                    # producer, not the preceding chunk, owns this metadata.
                    fmt = event['format']
                    if fmt not in ('ogg', 'pcm_s16le_48000'):
                        raise ValueError('unknown voice archive audio format')
                    seq = db.execute('SELECT COALESCE(MAX(seq),-1)+1 FROM chunks WHERE clip_id=?', (clip_id,)).fetchone()[0]
                    db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
                               (clip_id, seq, fmt, data, hashlib.sha256(data).hexdigest(), event.get('gain', 1.0)))
                    # Any tail after a premature terminal makes completeness unconfirmed again.
                    db.execute('UPDATE clips SET stream_complete=0 WHERE id=?', (clip_id,))
                elif kind == 'done':
                    db.execute('UPDATE clips SET stream_complete=1 WHERE id=?', (clip_id,))
                elif kind == 'retry':
                    # The discarded generation shares the speech id with its
                    # retry; only its persisted chat identity may survive.
                    db.execute('DELETE FROM chunks WHERE clip_id=?', (clip_id,))
                    db.execute('DELETE FROM text_events WHERE clip_id=?', (clip_id,))
                    db.execute("UPDATE clips SET text='',metadata='{}',stream_complete=0,archive_error=0 "
                               'WHERE id=?', (clip_id,))
                else:
                    raise ValueError('unknown archive event')


def rename_character_voice_archive(app_docs_dir, old_name: str, new_name: str) -> bool:
    """Migrate the owner name while retaining immutable clip and chat record ids.

    This runs from the serialized character-rename transaction, off the event
    loop and after pending writes from the old character have drained.
    """
    path = Path(app_docs_dir) / 'voice_archive' / 'archive.sqlite3'
    if not path.is_file():
        return False
    with closing(sqlite3.connect(path, timeout=2)) as db:
        with db:
            changed = db.execute('UPDATE clips SET character=? WHERE character=?',
                                 (new_name, old_name)).rowcount
    return changed > 0


def _mark_incomplete(manager, speech_id):
    failed = getattr(manager, '_owner_voice_archive_failed', None)
    if failed is None:
        failed = manager._owner_voice_archive_failed = set()
    if len(failed) >= 256:
        failed.pop()
    failed.add(str(speech_id))


async def _write_queued_events(manager, queue):
    """Serialize optional disk work without holding a live transport lock."""
    cm = manager._config_manager
    archive = manager._owner_voice_archive
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=WRITER_IDLE_SECONDS)
            except asyncio.TimeoutError:
                # The producer never awaits, so this handoff is atomic on the
                # event loop. An idle worker must not retain a dead session.
                if queue.empty():
                    return
                continue
            sid = event['speech_id']
            try:
                from utils.voice_archive_settings import load_settings, recording_gate
                settings = await asyncio.to_thread(load_settings, cm)
                gate = recording_gate(cm)
                if (not settings['enabled'] or
                        (gate is not None and
                         (not gate[0] or event['produced_at_ns'] < gate[1]))):
                    # Enabling a recording cannot retroactively authorize
                    # speech produced while it was disabled, even if the
                    # writer was backlogged until after the POST committed.
                    _mark_incomplete(manager, sid)
                    continue
                if event['kind'] == 'text' and event.get('is_first'):
                    cfg = await asyncio.to_thread(cm.get_core_config)
                    event['metadata'] = {k: cfg.get(k) for k in ('ttsModelProvider', 'TTS_MODEL', 'TTS_VOICE_ID')}
                    event['metadata']['character_voice_id'] = event.pop('character_voice_id', None)
                event['archive_error'] = sid in getattr(manager, '_owner_voice_archive_failed', set())

                def write_event():
                    from utils.cloudsave_runtime import assert_cloudsave_writable
                    assert_cloudsave_writable(cm, operation='voice_archive_write', target=str(archive.path))
                    archive.write(event)

                await asyncio.to_thread(write_event)
                if event['kind'] == 'retry':
                    getattr(manager, '_owner_voice_archive_failed', set()).discard(sid)
            except asyncio.CancelledError:
                _mark_incomplete(manager, sid)
                raise
            except Exception:
                _mark_incomplete(manager, sid)
                log.warning('Voice archive write failed', exc_info=True)
            finally:
                queue.task_done()
    finally:
        if getattr(manager, '_owner_voice_archive_task', None) is asyncio.current_task():
            manager._owner_voice_archive_task = None


async def drain_voice_archive(manager):
    """Await queued commits at an explicit durability boundary (and in tests)."""
    queue = getattr(manager, '_owner_voice_archive_queue', None)
    if queue is not None:
        task = getattr(manager, '_owner_voice_archive_task', None)
        if not queue.empty() and (task is None or task.done()):
            manager._owner_voice_archive_task = asyncio.create_task(_write_queued_events(manager, queue))
        await queue.join()


def _enqueue_archive_event(manager, kind, speech_id, **values):
    """Enqueue a bounded, ordered archive event without awaiting disk or config IO.

    Snapshot turn identity before returning to live speech. A later text event
    fills the same row even when its first audio frame arrived earlier.
    """
    cm = getattr(manager, '_config_manager', None)
    if cm is None or not speech_id:
        return
    produced_at_ns = time.monotonic_ns()
    from utils.voice_archive_settings import recording_gate
    snapshot = recording_gate(cm)
    sid = str(speech_id)
    if snapshot is not None and not snapshot[0]:
        _mark_incomplete(manager, sid)
        return
    character = str(getattr(manager, 'lanlan_name', ''))
    if not character:
        _mark_incomplete(manager, sid)
        return
    event = dict(values, kind=kind, character=character, speech_id=sid,
                 produced_at_ns=produced_at_ns,
                 proof=dict(getattr(manager, '_chat_original_turns', {}).get(sid, {})))
    if kind == 'audio':
        data = event.get('data')
        if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
            return
        event['data'] = bytes(data)
        # Legacy untagged producers are PCM; only a tagged Ogg producer may
        # create subsequent Ogg pages without a recognizable OggS prefix.
        event.setdefault('format', 'ogg' if event['data'].startswith(b'OggS') else 'pcm_s16le_48000')
    if kind == 'text' and event.get('is_first'):
        event['character_voice_id'] = getattr(manager, 'voice_id', None)
    if not hasattr(manager, '_owner_voice_archive'):
        manager._owner_voice_archive = VoiceArchive(Path(cm.app_docs_dir) / 'voice_archive' / 'archive.sqlite3')
    queue = getattr(manager, '_owner_voice_archive_queue', None)
    if queue is None:
        queue = manager._owner_voice_archive_queue = asyncio.Queue(maxsize=MAX_PENDING_EVENTS)
    task = getattr(manager, '_owner_voice_archive_task', None)
    if task is None or task.done():
        manager._owner_voice_archive_task = asyncio.create_task(_write_queued_events(manager, queue))
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        _mark_incomplete(manager, sid)
        log.warning('Voice archive queue full; discarding optional %s event', kind)


async def archive_event(manager, kind, speech_id, **values):
    """Optional recording must never fail the live text or audio transport."""
    try:
        _enqueue_archive_event(manager, kind, speech_id, **values)
    except Exception:
        _mark_incomplete(manager, speech_id)
        log.warning('Voice archive enqueue failed', exc_info=True)
