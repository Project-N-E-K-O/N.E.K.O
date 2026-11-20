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


def delete_character_voice_archive(app_docs_dir, character: str) -> int:
    """Purge one deleted character's audio and duplicated text, not other owners.

    Called only once the character deletion has committed. ``secure_delete``
    overwrites removed SQLite cells rather than retaining their old payloads
    in reusable pages, and a checkpoint releases the old WAL pages.
    """
    path = Path(app_docs_dir) / 'voice_archive' / 'archive.sqlite3'
    if not path.is_file():
        return 0
    with closing(sqlite3.connect(path, timeout=2)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA secure_delete=ON')
        with db:
            for table in ('chunks', 'text_events'):
                db.execute(f'DELETE FROM {table} WHERE clip_id IN '
                           '(SELECT id FROM clips WHERE character=?)', (character,))
            deleted = db.execute('DELETE FROM clips WHERE character=?', (character,)).rowcount
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return deleted


def _mark_incomplete(manager, speech_id, attempt_generation=None):
    sid = str(speech_id)
    failed = getattr(manager, '_owner_voice_archive_failed', None)
    if failed is None:
        failed = manager._owner_voice_archive_failed = set()
    if len(failed) >= 256 and sid not in failed:
        failed.pop()
    failed.add(sid)

    generations = getattr(manager, '_owner_voice_archive_attempt_generations', {})
    if attempt_generation is None:
        attempt_generation = generations.get(sid, 0)
    failed_generations = getattr(manager, '_owner_voice_archive_failed_generations', None)
    if failed_generations is None:
        failed_generations = manager._owner_voice_archive_failed_generations = {}
    if len(failed_generations) >= 256 and sid not in failed_generations:
        failed_generations.pop(next(iter(failed_generations)))
    failed_generations[sid] = max(
        int(attempt_generation or 0), failed_generations.get(sid, -1),
    )


def _clear_discarded_attempt_failure(manager, speech_id, attempt_generation):
    sid = str(speech_id)
    failed_generations = getattr(manager, '_owner_voice_archive_failed_generations', {})
    failed_generation = failed_generations.get(sid)
    # A failure in the new attempt must survive a reset that only discards
    # an older attempt; otherwise a missing new chunk could be marked complete.
    if failed_generation is None or failed_generation < int(attempt_generation or 0):
        getattr(manager, '_owner_voice_archive_failed', set()).discard(sid)
        failed_generations.pop(sid, None)


def _reject_unconsented_speech(manager, speech_id):
    """Never archive the tail of a speech whose start was not authorized.

    Besides consent, this prevents an Ogg/Opus tail from being replayed after
    its bootstrap headers were deliberately excluded on mid-speech opt-in.
    """
    rejected = getattr(manager, '_owner_voice_archive_unconsented', None)
    if rejected is None:
        rejected = manager._owner_voice_archive_unconsented = set()
    if len(rejected) >= 256:
        rejected.pop()
    rejected.add(str(speech_id))
    _mark_incomplete(manager, speech_id)


async def _write_queued_events(manager, queue):
    """Serialize optional disk work without holding a live transport lock."""
    cm = manager._config_manager
    archive = manager._owner_voice_archive

    async def persist(event):
        sid = event['speech_id']

        def write_event():
            from utils.voice_archive_settings import write_if_recording_authorized
            from utils.cloudsave_runtime import assert_cloudsave_writable

            def write():
                if event['kind'] == 'text' and event.get('is_first'):
                    cfg = cm.get_core_config()
                    event['metadata'] = {
                        k: cfg.get(k) for k in ('ttsModelProvider', 'TTS_MODEL', 'TTS_VOICE_ID')
                    }
                    event['metadata']['character_voice_id'] = event.pop('character_voice_id', None)
                event['archive_error'] = sid in getattr(manager, '_owner_voice_archive_failed', set())
                assert_cloudsave_writable(cm, operation='voice_archive_write', target=str(archive.path))
                archive.write(event)

            return write_if_recording_authorized(cm, event['recording_generation'], write)

        return await asyncio.to_thread(write_event)

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
                # A retry is a control boundary, not optional audio payload.
                # When the bounded audio queue was full, its reset is retained
                # here and committed before any more queued chunks are handled.
                pending_resets = getattr(manager, '_owner_voice_archive_retry_resets', {})
                for reset_sid, reset_event in list(pending_resets.items()):
                    if reset_sid in getattr(manager, '_owner_voice_archive_unconsented', set()):
                        pending_resets.pop(reset_sid, None)
                        continue
                    try:
                        if not await persist(reset_event):
                            _reject_unconsented_speech(manager, reset_sid)
                        else:
                            _clear_discarded_attempt_failure(
                                manager, reset_sid, reset_event.get('attempt_generation', 0),
                            )
                    except asyncio.CancelledError:
                        _mark_incomplete(manager, reset_sid)
                        raise
                    except Exception:
                        _reject_unconsented_speech(manager, reset_sid)
                        log.warning('Voice archive retry reset failed', exc_info=True)
                    finally:
                        pending_resets.pop(reset_sid, None)

                if sid in getattr(manager, '_owner_voice_archive_unconsented', set()):
                    continue
                generations = getattr(manager, '_owner_voice_archive_attempt_generations', {})
                if event.get('attempt_generation', 0) < generations.get(sid, 0):
                    # This event belongs to an attempt discarded by a later retry.
                    continue
                if not await persist(event):
                    _reject_unconsented_speech(manager, sid)
                    continue
                if event['kind'] == 'retry':
                    _clear_discarded_attempt_failure(
                        manager, sid, event.get('attempt_generation', 0),
                    )
                elif event['kind'] == 'done':
                    if generations.get(sid, 0) == event.get('attempt_generation', 0):
                        generations.pop(sid, None)
            except asyncio.CancelledError:
                _mark_incomplete(manager, sid, event.get('attempt_generation', 0))
                raise
            except Exception:
                _mark_incomplete(manager, sid, event.get('attempt_generation', 0))
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


async def retire_voice_archive(manager):
    """Stop new events before draining a retiring character or application."""
    manager._owner_voice_archive_retired = True
    await drain_voice_archive(manager)


def _enqueue_archive_event(manager, kind, speech_id, **values):
    """Enqueue a bounded, ordered archive event without awaiting disk or config IO.

    Snapshot turn identity before returning to live speech. A later text event
    fills the same row even when its first audio frame arrived earlier.
    """
    cm = getattr(manager, '_config_manager', None)
    if cm is None or not speech_id:
        return
    from utils.voice_archive_settings import recording_gate
    snapshot = recording_gate(cm)
    sid = str(speech_id)
    if getattr(manager, '_owner_voice_archive_retired', False):
        _mark_incomplete(manager, sid)
        return
    if sid in getattr(manager, '_owner_voice_archive_unconsented', set()):
        return
    if snapshot is not None and not snapshot[0]:
        _reject_unconsented_speech(manager, sid)
        return
    character = str(getattr(manager, 'lanlan_name', ''))
    if not character:
        _mark_incomplete(manager, sid)
        return
    generations = getattr(manager, '_owner_voice_archive_attempt_generations', None)
    if generations is None:
        generations = manager._owner_voice_archive_attempt_generations = {}
    attempt_generation = generations.get(sid, 0)
    if kind == 'retry':
        attempt_generation += 1
        generations[sid] = attempt_generation
    event = dict(values, kind=kind, character=character, speech_id=sid,
                 recording_generation=snapshot[1] if snapshot is not None else 0,
                 attempt_generation=attempt_generation,
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
        if kind == 'retry':
            # Keep the reset outside the bounded media queue. The writer checks
            # this map before its next queued event, then skips every stale
            # pre-retry chunk by attempt_generation.
            pending = getattr(manager, '_owner_voice_archive_retry_resets', None)
            if pending is None:
                pending = manager._owner_voice_archive_retry_resets = {}
            pending[sid] = event
            log.warning('Voice archive queue full; deferring retry reset for speech %s', sid)
        else:
            _mark_incomplete(manager, sid, attempt_generation)
            log.warning('Voice archive queue full; discarding optional %s event', kind)


async def archive_event(manager, kind, speech_id, **values):
    """Optional recording must never fail the live text or audio transport."""
    try:
        _enqueue_archive_event(manager, kind, speech_id, **values)
    except Exception:
        _mark_incomplete(manager, speech_id)
        log.warning('Voice archive enqueue failed', exc_info=True)
