"""Durable new private-chat originals, independent of memory projections and Steam.

Raw text, its arrival time and its origin order commit together. Recovery reuses
the persisted message identities and never scans legacy text, row numbers or timestamps.

Assistant replies are kept only when their turn proof names a chat or proactive
origin; the wire carries inert text under the same character, consent, identity
and deletion rules as other conversation records.
"""

import asyncio
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from services.chat_sync.writer import store_connection
import time
from uuid import uuid4

from services.chat_sync.ledger import ChatDomain, ChatOriginal
from services.chat_sync.protocol import MAX_ORIGIN_SEQ
from services.chat_sync.trace import get_trace


def is_chat_input_source(source):
    """Recognize actual ordinary composer tags, excluding synthetic drop prompts."""
    return str(source or '').strip() in ('', 'react-chat-window', 'legacy-text-button')


class ChatOriginalSource:
    """Keep an independently recoverable local original and its fixed owner."""

    def __init__(self, path: Path):
        """Initialize only the dedicated original WAL, without touching old history."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with closing(self._connect()) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                required = {'source_originals', 'source_unbound', 'source_local_replies',
                            'source_origin_streams', 'source_history_tasks', 'source_history_members',
                            'source_recovery_candidates', 'source_recovery_roles', 'source_recovery_delete_ids',
                            'source_recovery_delete_tasks', 'source_recovery_delete_members'}
                migrations = {'source_originals': {'queued_at_ms', 'origin_stream_id', 'origin_seq'},
                              'source_unbound': {'origin_stream_id', 'origin_seq', 'deletion_pending'}}
                if required <= tables and all(columns <= {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                                               for table, columns in migrations.items()):
                    return
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            # Older versions wrote history backfill tasks that name the local and
            # cloud roles. Nothing reads them now, but retiring a role still erases
            # them, so fresh stores keep both history tables for that cleanup.
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS source_originals (
                    record_id TEXT PRIMARY KEY, owner TEXT NOT NULL, uid TEXT NOT NULL,
                    original TEXT, original_hash TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0, queued_at_ms INTEGER);
                CREATE TABLE IF NOT EXISTS source_unbound (
                    record_id TEXT PRIMARY KEY, uid TEXT NOT NULL, scope TEXT NOT NULL,
                    original TEXT, original_hash TEXT NOT NULL, queued_at_ms INTEGER NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS source_local_replies (
                    record_id TEXT PRIMARY KEY, uid TEXT NOT NULL, origin TEXT NOT NULL,
                    original TEXT, original_hash TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS source_origin_streams (
                    uid TEXT PRIMARY KEY, stream_id TEXT NOT NULL UNIQUE, next_seq INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS source_history_tasks (
                    task_id TEXT PRIMARY KEY, uid TEXT NOT NULL, binding TEXT NOT NULL,
                    service_url TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    database_id TEXT NOT NULL, generation TEXT NOT NULL,
                    local_name TEXT NOT NULL, cloud_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'preview', count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS source_history_members (
                    task_id TEXT NOT NULL, record_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'waiting', PRIMARY KEY(task_id,record_id));
            """)
            from services.chat_sync.recovery import SCHEMA
            connection.executescript(SCHEMA)
            # Legacy source arrivals are unknown. Migration must not reset their
            # age, and concurrent producers must agree on this additive schema.
            connection.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(source_originals)")}
            if "queued_at_ms" not in columns:
                connection.execute("ALTER TABLE source_originals ADD COLUMN queued_at_ms INTEGER")
            # Only new raw commits allocate trustworthy order. Missing legacy
            # source metadata stays NULL rather than borrowing row/time order.
            for table in ("source_originals", "source_unbound"):
                origin_columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for name, kind in (("origin_stream_id", "TEXT"), ("origin_seq", "INTEGER")):
                    if name not in origin_columns:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            unbound_columns = {row[1] for row in connection.execute("PRAGMA table_info(source_unbound)")}
            if "deletion_pending" not in unbound_columns:
                connection.execute("ALTER TABLE source_unbound ADD COLUMN deletion_pending INTEGER NOT NULL DEFAULT 0")
                # Retained proven deletion IDs predate this outbox flag. Their
                # raw bytes stay erased while first binding can deliver markers.
                connection.execute("UPDATE source_unbound SET deletion_pending=1 WHERE deleted=1")
            connection.commit()

    def _connect(self):
        """Use durable short transactions and always close per-operation handles."""
        connection = store_connection(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA secure_delete=ON")
        return connection

    @staticmethod
    def _allocate_origin(connection, uid):
        """Allocate actual save order inside the same transaction as raw bytes."""
        connection.execute("INSERT OR IGNORE INTO source_origin_streams VALUES (?,?,1)", (uid, uuid4().hex))
        stream, seq = connection.execute("SELECT stream_id,next_seq FROM source_origin_streams WHERE uid=?", (uid,)).fetchone()
        if not isinstance(stream, str) or not re.fullmatch(r"[0-9a-f]{32}", stream) or type(seq) is not int or seq <= 0:
            raise ValueError("invalid_chat_source_origin")
        # Exhaustion keeps its existing public contract and cannot silently
        # replace the stream or wrap source order after the final safe integer.
        if seq > MAX_ORIGIN_SEQ:
            raise ValueError("source_sequence_exhausted")
        connection.execute("UPDATE source_origin_streams SET next_seq=next_seq+1 WHERE uid=?", (uid,))
        return stream, seq

    def stored_ids(self, uid):
        """Include erased IDs so reading the archive cannot restore a deleted record."""
        with closing(self._connect()) as connection:
            return {row[0] for row in connection.execute(
                "SELECT record_id FROM source_unbound WHERE uid=? UNION SELECT record_id FROM source_originals WHERE uid=? "
                "UNION SELECT record_id FROM source_local_replies WHERE uid=?", (uid, uid, uid))}

    def commit_unbound(self, uid, record_id, original: ChatOriginal):
        """Keep producer-proven raw bytes without assigning an account or upload task."""
        from utils.config_manager.reserved_schema import is_valid_character_uid
        if not is_valid_character_uid(uid):
            raise ValueError("invalid_chat_source_identity")
        if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id):
            raise ValueError("invalid_chat_source_identity")
        payload = json.dumps(asdict(original), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM source_local_replies WHERE record_id=?", (record_id,)).fetchone():
                raise ValueError("chat_source_identity_conflict")
            old = connection.execute("SELECT uid,original_hash FROM source_unbound WHERE record_id=?", (record_id,)).fetchone()
            bound = connection.execute("SELECT uid,original_hash FROM source_originals WHERE record_id=?", (record_id,)).fetchone()
            if old or bound:
                if tuple(old or bound) != (uid, digest):
                    raise ValueError("chat_source_identity_conflict")
                return
            stream, seq = self._allocate_origin(connection, uid)
            connection.execute("INSERT INTO source_unbound "
                               "(record_id,uid,scope,original,original_hash,queued_at_ms,deleted,origin_stream_id,origin_seq) "
                               "VALUES (?,?,?,?,?,?,0,?,?)",
                               (record_id, uid, "private-chat-original-v1", payload, digest, time.time_ns() // 1_000_000, stream, seq))
        get_trace().emit("source_committed", character_uid=uid, message_id=record_id, local_message_id=record_id)

    def commit(self, owner, uid, record_id, original: ChatOriginal):
        """Atomically save immutable raw bytes under the role's fixed owner."""
        ChatDomain(owner, uid)
        if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id):
            raise ValueError("invalid_chat_source_identity")
        payload = json.dumps(asdict(original), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM source_local_replies WHERE record_id=?", (record_id,)).fetchone():
                raise ValueError("chat_source_identity_conflict")
            old = connection.execute("SELECT * FROM source_originals WHERE record_id=?", (record_id,)).fetchone()
            if old:
                if (old["owner"], old["uid"], old["original_hash"]) != (owner, uid, digest):
                    raise ValueError("chat_source_identity_conflict")
                return
            # A late producer retry cannot turn a previously unbound original
            # into newly authorized chat merely because consent now exists.
            unbound = connection.execute("SELECT uid,original_hash FROM source_unbound WHERE record_id=?", (record_id,)).fetchone()
            if unbound:
                if tuple(unbound) != (uid, digest):
                    raise ValueError("chat_source_identity_conflict")
                return
            # Save actual arrival alongside raw bytes. Recovery or an
            # idempotent producer retry must never restart the waiting clock.
            stream, seq = self._allocate_origin(connection, uid)
            connection.execute(
                "INSERT INTO source_originals "
                "(record_id,owner,uid,original,original_hash,deleted,queued_at_ms,origin_stream_id,origin_seq) VALUES (?,?,?,?,?,0,?,?,?)",
                (record_id, owner, uid, payload, digest, time.time_ns() // 1_000_000, stream, seq),
            )
        get_trace().emit("source_committed", character_uid=uid, message_id=record_id, local_message_id=record_id)

    @staticmethod
    def read_local_replies(path, uid):
        """Read records not yet enrolled in the shared chat history."""
        path = Path(path)
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='source_local_replies'").fetchone():
                return []
            rows = connection.execute("SELECT record_id,origin,original,original_hash FROM source_local_replies l WHERE uid=? AND deleted=0 "
                "AND NOT EXISTS (SELECT 1 FROM source_unbound u WHERE u.record_id=l.record_id) "
                "AND NOT EXISTS (SELECT 1 FROM source_originals o WHERE o.record_id=l.record_id) ORDER BY rowid", (uid,)).fetchall()
        replies = []
        for identity, origin, payload, digest in rows:
            if (origin not in ("agent", "tool") or not re.fullmatch(r"[0-9a-f]{32}", identity)
                    or hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest):
                raise ValueError("chat_source_integrity_error")
            original = ChatOriginal(**json.loads(payload))
            if original.role != "assistant":
                raise ValueError("chat_source_integrity_error")
            replies.append({"messageId": identity, "characterSyncId": uid, "role": original.role,
                            "text": original.text, "originalTime": original.original_time})
        return replies


def _source_path(runtime):
    """Keep source recovery beside sync state, outside Steam-managed memory trees."""
    return Path(runtime.state.path).parent / "sources.sqlite3"


async def local_replies(runtime, uid):
    """Restore local-only replies without auth, capture, backfill or cloud I/O."""
    from services.chat_sync.writer import WriterQuarantined
    try:
        await asyncio.to_thread(runtime.writer.check)
    except WriterQuarantined:
        if not await asyncio.to_thread(runtime.writer.local_visibility_trusted):
            return []
    return await asyncio.to_thread(ChatOriginalSource.read_local_replies, _source_path(runtime), uid)


async def character_uid_for(lanlan_name, config_manager=None):
    """Resolve the saved UID behind a character name at this moment."""
    from utils.config_manager import get_config_manager
    from utils.config_manager.reserved_schema import get_character_uid

    manager = config_manager or get_config_manager()
    data = await manager.aload_characters()
    return get_character_uid(data.get("猫娘", {}).get(lanlan_name, {}))


def _saved_capture_consent(directory, uid):
    """Read existing local capture consent without admitting a quarantined writer."""
    try:
        path = (Path(directory) / "state.sqlite3").resolve()
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            # Match normal capture eligibility, including retained paused bindings.
            # This grants only local outage retention, never writer or cloud authority.
            return bool(conn.execute(
                "SELECT EXISTS(SELECT 1 FROM sync_bindings WHERE character_uid=?) OR "
                "EXISTS(SELECT 1 FROM sync_choices WHERE character_uid=? AND enabled=1)",
                (uid, uid),
            ).fetchone()[0])
    except (OSError, sqlite3.Error):
        # Read-only mode cannot create a missing store or turn unknown state into On.
        return False


async def save_original(lanlan_name, role, text, *, record_id=None, original_time=None, config_manager=None,
                        character_uid=None):
    """Commit a new accepted original before best-effort ledger delivery, without HTTP."""
    from services.chat_sync.runtime import get_desktop_chat_sync_runtime
    from utils.config_manager import get_config_manager

    manager = config_manager or get_config_manager()
    # A UID pinned at the reply's terminal survives a later rename.
    uid = character_uid or await character_uid_for(lanlan_name, manager)
    if not uid:
        return None
    record_id = record_id or uuid4().hex
    original = ChatOriginal(role, text, original_time=original_time or str(time.time_ns() // 1_000_000), timezone="UTC")
    from services.chat_sync.writer import WriterQuarantined
    from services.chat_sync.recovery import role_pending, save_outage
    try:
        runtime = await get_desktop_chat_sync_runtime(manager)
        binding = await asyncio.to_thread(runtime.state.binding, uid)
        if binding is None and not (await asyncio.to_thread(runtime.state.choices)).get(uid, False):
            # Unconfigured roles keep their normal local memory; no hidden raw
            # history is captured before the user saves an explicit sync choice.
            return None
        source = await asyncio.to_thread(ChatOriginalSource, _source_path(runtime))
        if binding is None or await asyncio.to_thread(role_pending, runtime, uid):
            # Rebinding alone does not approve outage/old originals. A later
            # explicit Save/On including history may approve this trusted lane;
            # recovery deletion markers still need their separate confirmation.
            await asyncio.to_thread(source.commit_unbound, uid, record_id, original)
            return record_id
        await asyncio.to_thread(source.commit, binding["owner_id"], uid, record_id, original)
    except WriterQuarantined:
        directory = runtime.state.path.parent if 'runtime' in locals() else Path(manager.app_docs_dir) / 'chat_sync'
        if not await asyncio.to_thread(_saved_capture_consent, directory, uid):
            return None
        try:
            await asyncio.to_thread(save_outage, directory, uid, record_id, original)
        except (OSError, sqlite3.Error, ValueError):
            # The primary conversation already owns its saved message. Optional
            # sync failure never reports that the user's local save failed.
            print("[chat-sync] local_original_sync_unavailable")
            return None
        return record_id
    # Keep the original durable before the model starts; ledger replay belongs
    # to the owned scheduler, so the producer never waits for backlog processing.
    # This first transaction is the crash-recovery barrier. Scheduling an unsaved
    # body in the background would lose accepted originals on process termination.
    runtime.wake_scheduler()
    return record_id


def mark_turn_origin(manager, turn_id, origin):
    """Bind an explicit origin to one speech identity rather than a global current flag."""
    # Preserve provenance independently of future upload policy; emitted
    # identity/proof stays fixed (MSYNC-004).
    turns = getattr(manager, "_chat_original_turns", None)
    if turns is None:
        turns = manager._chat_original_turns = {}
    key = str(turn_id or "")
    if key not in turns:
        turns[key] = {"recordId": uuid4().hex, "originalTime": str(time.time_ns() // 1_000_000), "origin": origin}
        get_trace().emit("assistant_source_selected", message_id=turns[key]["recordId"], source_origin=origin)
    # Retain a bounded identity map; ordinary turns are never keyed by text/time.
    while len(turns) > 256:
        turns.pop(next(iter(turns)))
    return dict(turns[key])


# Gemini keeps streaming transcript of a reply for up to three seconds after
# its turn terminal; the realtime client treats that text as the same reply.
# Sealing waits slightly longer so the saved original holds the whole reply.
LATE_TEXT_GRACE_SECONDS = 3.5


class AssistantOriginalBuffer:
    """Collect emitted text per stable message ID; persist only at final boundaries."""

    # Sealed IDs remembered so text arriving after the grace is dropped, not conflicting.
    _SEALED_LIMIT = 64

    def __init__(self, *, tail_grace=0.0):
        """Start without inferred identities for legacy or system messages."""
        self.records = {}
        self._traced_exclusions = set()
        self._tail_grace = tail_grace
        # Record ID -> pending deferred seal after its terminal.
        self._deferred = {}
        self._sealed = {}

    def _trace_exclusion(self, proof, reason):
        """Observe each excluded identity once, without retaining any text."""
        trace = get_trace()
        if not trace.enabled:
            return
        proof = proof if isinstance(proof, dict) else {}
        identity = proof.get("recordId")
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
            identity = None
        key = (identity, reason)
        if key not in self._traced_exclusions:
            self._traced_exclusions.add(key)
            trace.emit("assistant_source_excluded", message_id=identity,
                       source_origin=proof.get("origin"), source_reason=reason)

    def append(self, message):
        """Copy server-created source proof and preserve exact visible chunk bytes."""
        # Every mirror is excluded, independently of its existing memory policy.
        metadata = message.get('metadata')
        proof = message.get("chat_original_source")
        if isinstance(metadata, dict) and isinstance(metadata.get('mirror'), dict):
            self._trace_exclusion(proof, "mirror")
            return
        if not isinstance(proof, dict) or proof.get("origin") not in ("chat", "proactive"):
            self._trace_exclusion(proof, "nonchat_origin" if isinstance(proof, dict)
                                  and proof.get("origin") in ("avatar", "system") else "unknown_origin")
            return
        record_id = proof.get("recordId")
        if record_id in self._sealed:
            # The saved original is immutable; reusing its ID would only conflict.
            self._trace_exclusion(proof, "late_after_seal")
            return
        if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id):
            self._trace_exclusion(proof, "invalid_identity")
            return
        text = message.get("text")
        if not isinstance(text, str):
            self._trace_exclusion(proof, "invalid_text")
            return
        entry = self.records.setdefault(record_id, {"proof": dict(proof), "chunks": []})
        if entry["proof"] != proof:
            raise ValueError("chat_source_identity_conflict")
        entry["chunks"].append(text)
        if record_id in self._deferred:
            # Late transcript of a terminated reply restarts its grace.
            self._defer(record_id)

    def _defer(self, record_id):
        previous = self._deferred.pop(record_id, None)
        if previous is not None:
            previous.cancel()
        self._deferred[record_id] = asyncio.create_task(self._seal_later(record_id))

    async def _seal_later(self, record_id):
        await asyncio.sleep(self._tail_grace)
        self._deferred.pop(record_id, None)
        if record_id in self.records:
            await self._seal(record_id)

    async def finish(self, lanlan_name, record_id=None, *, excluded=False):
        """Persist only the matching completed original, never normalized projections."""
        if record_id is not None and self._tail_grace > 0:
            if record_id not in self.records:
                return
            previous = self.records[record_id].get("terminal")
            try:
                uid = previous["uid"] if previous else await character_uid_for(lanlan_name)
            except Exception:
                uid = None
            entry = self.records.get(record_id)
            if entry is None:
                return
            # A repeated terminal can only make the reply stricter.
            entry["terminal"] = {"name": lanlan_name, "uid": uid,
                                 "excluded": excluded or bool(previous and previous["excluded"])}
            self._defer(record_id)
            return
        ids = [record_id] if record_id else list(self.records)
        for identity in ids:
            pending = self._deferred.pop(identity, None)
            if pending is not None:
                pending.cancel()
            entry = self.records.get(identity)
            if not entry:
                continue
            if "terminal" not in entry:
                entry["terminal"] = {"name": lanlan_name, "uid": None, "excluded": excluded}
            # A terminated reply keeps its own terminal's decision and pinned UID.
            await self._seal(identity)
        self._traced_exclusions = {key for key in self._traced_exclusions
                                   if record_id is not None and key[0] not in (None, record_id)}

    async def _seal(self, identity):
        entry = self.records[identity]
        terminal = entry["terminal"]
        lanlan_name, excluded = terminal["name"], terminal["excluded"]
        # Close the identity before awaiting: a chunk arriving mid-save is
        # dropped visibly instead of joining an entry that is about to vanish.
        self._sealed[identity] = None
        if not excluded:
            proof = entry["proof"]
            # A UID pinned at the terminal survives a rename before this seal.
            pinned = {"character_uid": terminal["uid"]} if terminal["uid"] else {}
            try:
                saved = await save_original(lanlan_name, "assistant", "".join(entry["chunks"]), record_id=identity,
                                            original_time=proof["originalTime"], **pinned)
            except Exception:
                get_trace().emit("assistant_source_finished", message_id=identity, source_origin=proof["origin"],
                                 source_reason="save_failed", outcome="error")
                print("[chat-sync] original_save_failed")
                # A failed save stays retryable under the same identity.
                self._sealed.pop(identity, None)
                return
            get_trace().emit("assistant_source_finished", message_id=identity, source_origin=proof["origin"],
                             source_reason="saved" if saved else "save_unavailable", outcome="success" if saved else "error")
        else:
            get_trace().emit("assistant_source_finished", message_id=identity, source_origin=entry["proof"]["origin"],
                             source_reason="terminal_excluded", outcome="success")
        self.records.pop(identity, None)
        while len(self._sealed) > self._SEALED_LIMIT:
            self._sealed.pop(next(iter(self._sealed)))

    async def flush_terminated(self):
        """Seal replies already past their terminal now, e.g. before shutdown."""
        for identity in [key for key, entry in self.records.items() if "terminal" in entry]:
            pending = self._deferred.pop(identity, None)
            if pending is not None:
                pending.cancel()
            if identity in self.records:
                await self._seal(identity)

    def discard(self):
        """Drop temporary output when the existing persistence path discards it.

        A reply already past its terminal is complete and keeps its pending seal.
        """
        self.records = {identity: entry for identity, entry in self.records.items() if "terminal" in entry}
        self._traced_exclusions.clear()
