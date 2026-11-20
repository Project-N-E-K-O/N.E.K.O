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
import os
from pathlib import Path
import sqlite3
import tempfile

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


def _purge_intent_path(app_docs_dir, character: str) -> Path:
    key = hashlib.sha256(str(character).encode("utf-8")).hexdigest()
    return Path(app_docs_dir) / "voice_archive" / "purge-intents" / f"{key}.json"


def has_pending_character_voice_archive_purge(app_docs_dir, character: str) -> bool:
    """Fail closed while a deleted identity's archive purge is pending."""
    path = _purge_intent_path(app_docs_dir, character)
    return path.exists() or path.is_symlink()


def _read_purge_intent(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("character"), str)
        or not payload["character"]
        or payload.get("state") not in {"prepared", "committed"}
    ):
        raise ValueError("invalid voice archive purge intent")
    return payload


def write_character_voice_archive_purge_intent(
    app_docs_dir,
    character: str,
    *,
    character_uid: str | None = None,
    character_id: str | None = None,
) -> Path:
    """Atomically fence a character name before its config deletion commits."""
    if not isinstance(character, str) or not character:
        raise ValueError("voice archive purge requires a character name")
    path = _purge_intent_path(app_docs_dir, character)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "version": 1,
        "character": character,
        "character_uid": str(character_uid or "").strip() or None,
        "character_id": str(character_id or "").strip() or None,
        "state": "prepared",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if path.exists() or path.is_symlink():
        existing = _read_purge_intent(path)
        if existing["character"] != character:
            raise RuntimeError("voice archive purge intent identity mismatch")
        for field in ("character_uid", "character_id"):
            old_value, new_value = existing.get(field), payload.get(field)
            if old_value and new_value and old_value != new_value:
                raise RuntimeError("another identity's voice archive purge is still pending")
            if old_value:
                payload[field] = old_value
        if existing.get("state") == "committed":
            payload["state"] = "committed"
            payload["created_at"] = existing.get("created_at") or payload["created_at"]
    _write_purge_intent_payload(path, payload)
    return path


def _write_purge_intent_payload(path: Path, payload: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".purge-", suffix=".tmp", dir=path.parent)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Atomic replace is the required durability boundary; directory
            # fsync is best-effort on platforms that do not support it.
            pass
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def commit_character_voice_archive_purge_intent(
    app_docs_dir,
    character: str,
    *,
    character_uid: str | None = None,
    character_id: str | None = None,
) -> Path:
    """Mark a prepared intent committed after characters.json is durable."""
    path = write_character_voice_archive_purge_intent(
        app_docs_dir, character, character_uid=character_uid, character_id=character_id,
    )
    payload = _read_purge_intent(path)
    payload["state"] = "committed"
    _write_purge_intent_payload(path, payload)
    return path


def discard_character_voice_archive_purge_intent(
    app_docs_dir,
    character: str,
    *,
    character_uid: str | None = None,
    character_id: str | None = None,
) -> bool:
    """Remove only an uncommitted intent for the same character identity."""
    path = _purge_intent_path(app_docs_dir, character)
    if not path.exists() and not path.is_symlink():
        return True
    payload = _read_purge_intent(path)
    if payload.get("state") == "committed":
        return False
    for field, expected in (("character_uid", character_uid), ("character_id", character_id)):
        actual = payload.get(field)
        if actual and expected and actual != expected:
            return False
    path.unlink(missing_ok=True)
    return True


class VoiceArchive:
    def __init__(self, path):
        self.path = Path(path)

    def write(self, event):
        if has_pending_character_voice_archive_purge(self.path.parent.parent, event["character"]):
            raise RuntimeError("voice archive purge is pending for this character")
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
    if (
        has_pending_character_voice_archive_purge(app_docs_dir, old_name)
        or has_pending_character_voice_archive_purge(app_docs_dir, new_name)
    ):
        raise RuntimeError("cannot rename a character into or out of a pending voice archive purge")
    path = Path(app_docs_dir) / 'voice_archive' / 'archive.sqlite3'
    if not path.is_file():
        return False
    with closing(sqlite3.connect(path, timeout=2)) as db:
        with db:
            changed = db.execute('UPDATE clips SET character=? WHERE character=?',
                                 (new_name, old_name)).rowcount
    return changed > 0


def delete_character_voice_archive(
    app_docs_dir,
    character: str,
    *,
    character_uid: str | None = None,
    character_id: str | None = None,
) -> int:
    """Durably fence, then securely purge one deleted character's voice data.

    The intent survives SQLite failures and is retried at startup. All replay
    and history joins reject a character while this marker exists, so a new
    character with the same name cannot see old rows before the purge finishes.
    """
    intent_path = commit_character_voice_archive_purge_intent(
        app_docs_dir, character, character_uid=character_uid, character_id=character_id,
    )
    path = Path(app_docs_dir) / "voice_archive" / "archive.sqlite3"
    if not path.is_file():
        intent_path.unlink(missing_ok=True)
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
    # Clear the fence only after the row deletes and WAL checkpoint completed.
    intent_path.unlink(missing_ok=True)
    return deleted


def retry_pending_voice_archive_purges(config_manager) -> dict:
    """Resume committed archive purges; discard prepared intents for live identities."""
    app_docs_dir = config_manager.app_docs_dir
    directory = Path(app_docs_dir) / "voice_archive" / "purge-intents"
    summary = {"purged": [], "discarded": [], "pending": [], "errors": []}
    if not directory.is_dir():
        return summary
    try:
        # Never interpret ConfigManager's default-profile fallback as proof
        # that the deleted identity is absent. A missing, malformed or
        # unpersisted characters.json must leave the purge fenced for retry.
        characters_data = config_manager.load_characters(require_authoritative=True)
        catgirls = characters_data.get("猫娘", {}) if isinstance(characters_data, dict) else {}
        if not isinstance(catgirls, dict):
            catgirls = {}
    except Exception as exc:
        summary["errors"].append(str(exc))
        log.warning("Voice archive purge recovery cannot read characters.json: %s", exc)
        return summary

    from utils.config_manager import get_character_uid, get_reserved

    for intent_path in sorted(directory.glob("*.json")):
        try:
            intent = _read_purge_intent(intent_path)
            character = intent["character"]
            entry = catgirls.get(character)
            if intent["state"] == "prepared" and entry is not None:
                current_uid = str(get_character_uid(entry) or "").strip() or None
                current_id = str(get_reserved(entry, "character_id", default="") or "").strip() or None
                same_uid = not intent.get("character_uid") or current_uid == intent["character_uid"]
                same_id = not intent.get("character_id") or current_id == intent["character_id"]
                if same_uid and same_id:
                    intent_path.unlink(missing_ok=True)
                    summary["discarded"].append(character)
                    continue
            deleted = delete_character_voice_archive(
                app_docs_dir,
                character,
                character_uid=intent.get("character_uid"),
                character_id=intent.get("character_id"),
            )
            summary["purged"].append({"character": character, "clips": deleted})
        except Exception as exc:
            summary["pending"].append(str(intent_path))
            summary["errors"].append(str(exc))
            log.warning("Voice archive purge recovery remains pending: %s", intent_path.name, exc_info=True)
    return summary

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
                        # A newer retry can replace this reset while SQLite is
                        # committing it in the worker thread. Never pop that
                        # newer control event with the older completion.
                        if pending_resets.get(reset_sid) is reset_event:
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
        if sid not in generations and len(generations) >= 256:
            # Retain only a bounded set of retried speech IDs. If an extremely
            # old retry owner is evicted, suppress its late events rather than
            # allowing them to create a second recording generation.
            expired_sid = next(iter(generations))
            generations.pop(expired_sid, None)
            getattr(manager, '_owner_voice_archive_failed_generations', {}).pop(expired_sid, None)
            getattr(manager, '_owner_voice_archive_retry_resets', {}).pop(expired_sid, None)
            _reject_unconsented_speech(manager, expired_sid)
        attempt_generation += 1
        # Keep the generation counter after done so a later retry of the same
        # speech ID cannot reuse a generation and clear a newer partial marker.
        generations.pop(sid, None)
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
