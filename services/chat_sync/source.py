"""Durable new private-chat originals, independent of memory projections and Steam.

Raw text and a pending-ledger marker commit together. Recovery reuses persisted
source/database identities and never scans legacy text, row numbers or timestamps.

Chat history includes visible Agent/tool-assisted replies. Their producer origin
remains local provenance; the wire carries inert text under the same character,
consent, identity and deletion rules as other conversation records.
"""

import asyncio
from contextlib import closing, nullcontext
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from services.chat_sync.writer import store_connection
import time
from uuid import uuid4
import weakref

from services.chat_sync.ledger import ChatDomain, ChatOriginal, SourceLocator
from services.chat_sync.protocol import MAX_ORIGIN_SEQ, decode_chat_original, encode_chat_original
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
                required = {'source_identity', 'source_originals', 'source_unbound', 'source_local_replies',
                            'source_origin_streams', 'source_history_tasks', 'source_history_members',
                            'source_recovery_candidates', 'source_recovery_roles', 'source_recovery_delete_ids',
                            'source_recovery_delete_tasks', 'source_recovery_delete_members', 'source_role_reassignments'}
                migrations = {'source_originals': {'queued_at_ms', 'history_task_id', 'origin_stream_id', 'origin_seq'},
                              'source_unbound': {'origin_stream_id', 'origin_seq', 'verified_owner', 'deletion_pending'},
                              'source_history_tasks': {'device_id'},
                              'source_history_members': {'original_hash', 'origin_stream_id', 'origin_seq'}}
                if required <= tables and all(columns <= {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                                               for table, columns in migrations.items()):
                    return
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS source_identity (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    database_id TEXT NOT NULL, generation TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_originals (
                    record_id TEXT PRIMARY KEY, owner TEXT NOT NULL, uid TEXT NOT NULL,
                    original TEXT, original_hash TEXT NOT NULL,
                    pending INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0,
                    queued_at_ms INTEGER);
                CREATE TABLE IF NOT EXISTS source_unbound (
                    record_id TEXT PRIMARY KEY, uid TEXT NOT NULL, scope TEXT NOT NULL,
                    original TEXT, original_hash TEXT NOT NULL, queued_at_ms INTEGER NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS source_local_replies (
                    record_id TEXT PRIMARY KEY, uid TEXT NOT NULL, origin TEXT NOT NULL,
                    original TEXT, original_hash TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS source_origin_streams (
                    uid TEXT PRIMARY KEY, stream_id TEXT NOT NULL UNIQUE, next_seq INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS source_role_reassignments (
                    old_uid TEXT PRIMARY KEY, owner TEXT NOT NULL, new_uid TEXT NOT NULL UNIQUE);
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
            if "history_task_id" not in columns:
                connection.execute("ALTER TABLE source_originals ADD COLUMN history_task_id TEXT")
            # Missing historical pins remain unknown; never backfill them from
            # the current device or raw payload during an additive migration.
            task_columns = {row[1] for row in connection.execute("PRAGMA table_info(source_history_tasks)")}
            if "device_id" not in task_columns:
                connection.execute("ALTER TABLE source_history_tasks ADD COLUMN device_id TEXT")
            member_columns = {row[1] for row in connection.execute("PRAGMA table_info(source_history_members)")}
            if "original_hash" not in member_columns:
                connection.execute("ALTER TABLE source_history_members ADD COLUMN original_hash TEXT")
            # Only new raw commits allocate trustworthy order. Missing legacy
            # source metadata stays NULL rather than borrowing row/time order.
            for table in ("source_originals", "source_unbound", "source_history_members"):
                origin_columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for name, kind in (("origin_stream_id", "TEXT"), ("origin_seq", "INTEGER")):
                    if name not in origin_columns:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            unbound_columns = {row[1] for row in connection.execute("PRAGMA table_info(source_unbound)")}
            if "verified_owner" not in unbound_columns:
                connection.execute("ALTER TABLE source_unbound ADD COLUMN verified_owner TEXT")
            if "deletion_pending" not in unbound_columns:
                connection.execute("ALTER TABLE source_unbound ADD COLUMN deletion_pending INTEGER NOT NULL DEFAULT 0")
                # Retained proven deletion IDs predate this outbox flag. Their
                # raw bytes stay erased while first binding can deliver markers.
                connection.execute("UPDATE source_unbound SET deletion_pending=1 WHERE deleted=1")
            connection.execute("INSERT OR IGNORE INTO source_identity VALUES (1,?,?)", (uuid4().hex, uuid4().hex))
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

    @staticmethod
    def _require_current_uid(connection, uid, owner=None):
        """A late producer cannot recreate source authority for a retired cloud identity."""
        if connection.execute("SELECT 1 FROM source_role_reassignments WHERE old_uid=?", (uid,)).fetchone():
            raise ValueError("chat_source_role_reassigned")
        previous = connection.execute("SELECT owner FROM source_role_reassignments WHERE new_uid=?", (uid,)).fetchone()
        if owner is not None and previous and previous["owner"] != owner:
            raise ValueError("chat_sync_owner_mismatch")

    def reassignment(self, old_uid):
        """Return the fixed local transfer so config-write crash recovery reuses the same UID."""
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT owner,old_uid,new_uid FROM source_role_reassignments WHERE old_uid=?", (old_uid,)).fetchone()
        return None if row is None else {"ownerId": row["owner"], "oldUid": row["old_uid"], "newUid": row["new_uid"]}

    def reassign_role(self, owner, old_uid, new_uid, ledger):
        """Keep local chat when an explicit save creates a new identity after cloud deletion.

        The caller first pauses the retired binding and holds its writer/session commit guard.
        This one source transaction is replayable before a separate config-file save. It never
        changes the retired ledger, sends requests, approves history, or moves deletion tasks.
        Only source-proven and validated downloaded originals can enter a later history grant;
        unknown legacy source order and tool replies retain their local-only classification.
        """
        old_domain = ChatDomain(owner, old_uid)
        ChatDomain(owner, new_uid)
        if old_uid == new_uid:
            raise ValueError("chat_sync_binding_conflict")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT owner,new_uid FROM source_role_reassignments WHERE old_uid=?", (old_uid,)).fetchone()
            if previous:
                if previous["owner"] != owner:
                    raise ValueError("chat_sync_owner_mismatch")
                if previous["new_uid"] != new_uid:
                    raise ValueError("chat_sync_binding_conflict")
                return new_uid
            self._require_current_uid(connection, old_uid, owner)
            for table in ("source_originals", "source_unbound", "source_local_replies", "source_origin_streams"):
                if connection.execute(f"SELECT 1 FROM {table} WHERE uid=? LIMIT 1", (new_uid,)).fetchone():
                    raise ValueError("chat_sync_binding_conflict")
            if connection.execute("SELECT 1 FROM source_role_reassignments WHERE new_uid=? OR old_uid=?", (new_uid, new_uid)).fetchone():
                raise ValueError("chat_sync_binding_conflict")
            rows = connection.execute("SELECT * FROM source_originals WHERE uid=?", (old_uid,)).fetchall()
            unbound = connection.execute("SELECT * FROM source_unbound WHERE uid=?", (old_uid,)).fetchall()
            if any(row["owner"] != owner for row in rows) or any(
                    row["verified_owner"] not in (None, owner) for row in unbound):
                raise ValueError("chat_sync_owner_mismatch")
            records = {row["record_id"]: dict(row) for row in unbound}
            for row in rows:
                existing = records.get(row["record_id"])
                if existing and existing["original_hash"] != row["original_hash"]:
                    raise ValueError("chat_source_integrity_error")
                # A local deletion fence wins over a stale source/ledger copy.
                if existing and existing["deleted"]:
                    continue
                records[row["record_id"]] = {**dict(row), "scope": "private-chat-original-v1"}
            deleted = set(ledger.deleted_message_ids(old_domain))
            for payload in ledger.visible_originals(old_domain):
                record = decode_chat_original(payload)
                if record["ownerId"] != owner or record["characterSyncId"] != old_uid:
                    raise ValueError("chat_source_integrity_error")
                identity = record["messageId"]
                raw = ChatOriginal(record["role"], record["text"], record["originalTime"], record["timezone"],
                    record["conversationId"], record["replyToMessageId"])
                original = json.dumps(asdict(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                if identity in records:
                    saved = records[identity]
                    if not saved["deleted"] and saved["original"] is not None and saved["original"] != original:
                        raise ValueError("chat_source_integrity_error")
                    if not saved["deleted"] and saved["origin_stream_id"] is not None and (
                            saved["origin_stream_id"], saved["origin_seq"]) != (record["originStreamId"], record["originSeq"]):
                        raise ValueError("chat_source_integrity_error")
                    continue
                if identity in deleted:
                    continue
                if connection.execute("SELECT 1 FROM source_local_replies WHERE record_id=? UNION ALL "
                    "SELECT 1 FROM source_unbound WHERE record_id=? AND uid!=? UNION ALL "
                    "SELECT 1 FROM source_originals WHERE record_id=? AND uid!=?",
                    (identity, identity, old_uid, identity, old_uid)).fetchone():
                    raise ValueError("chat_source_identity_conflict")
                records[identity] = {"record_id": identity, "original": original,
                    "original_hash": hashlib.sha256(original.encode()).hexdigest(), "deleted": 0,
                    "scope": "private-chat-original-v1", "queued_at_ms": time.time_ns() // 1_000_000,
                    "origin_stream_id": record["originStreamId"], "origin_seq": record["originSeq"]}
            for identity, row in records.items():
                erased = row["deleted"] or identity in deleted
                original = None if erased else row["original"]
                if original is not None:
                    if hashlib.sha256(original.encode()).hexdigest() != row["original_hash"]:
                        raise ValueError("chat_source_integrity_error")
                    ChatOriginal(**json.loads(original))
                # Re-enrollment requires the next explicit combined consent; pending old
                # bodies become unbound, and old tombstones never become new deletion jobs.
                connection.execute("INSERT INTO source_unbound "
                    "(record_id,uid,scope,original,original_hash,queued_at_ms,deleted,origin_stream_id,origin_seq,verified_owner,deletion_pending) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,0) ON CONFLICT(record_id) DO UPDATE SET "
                    "uid=excluded.uid,scope=excluded.scope,original=excluded.original,original_hash=excluded.original_hash,"
                    "queued_at_ms=excluded.queued_at_ms,deleted=excluded.deleted,origin_stream_id=excluded.origin_stream_id,"
                    "origin_seq=excluded.origin_seq,verified_owner=excluded.verified_owner,deletion_pending=0",
                    (identity, new_uid, row["scope"], original, row["original_hash"], row["queued_at_ms"] or 0,
                     int(erased), row["origin_stream_id"], row["origin_seq"], owner))
            connection.execute("DELETE FROM source_originals WHERE uid=? AND owner=?", (old_uid, owner))
            connection.execute("UPDATE source_local_replies SET uid=? WHERE uid=?", (new_uid, old_uid))
            connection.execute("UPDATE source_origin_streams SET uid=? WHERE uid=?", (new_uid, old_uid))
            connection.execute("UPDATE source_history_tasks SET status='cancelled' WHERE uid=?", (old_uid,))
            connection.execute("INSERT INTO source_role_reassignments VALUES (?,?,?)", (old_uid, owner, new_uid))
        return new_uid

    def read_role_originals(self, owner, uid, ledger=None):
        """Restore transferred local chat while a new cloud grant is pending or rejected.

        This is a local display projection, never an ACK or an upload authorization. The
        original owner remains pinned, so signing into another account cannot publish it.
        """
        ChatDomain(owner, uid)
        with closing(self._connect()) as connection:
            mapping = connection.execute("SELECT owner FROM source_role_reassignments WHERE new_uid=?", (uid,)).fetchone()
            if mapping is None:
                return []
            if mapping["owner"] != owner:
                raise ValueError("chat_sync_owner_mismatch")
            rows = connection.execute("SELECT record_id,uid,scope,original,original_hash,queued_at_ms,deleted,"
                "origin_stream_id,origin_seq,verified_owner,deletion_pending FROM source_unbound WHERE uid=? AND deleted=0 AND original IS NOT NULL "
                "UNION ALL SELECT record_id,uid,'private-chat-original-v1',original,original_hash,queued_at_ms,deleted,"
                "origin_stream_id,origin_seq,owner,0 FROM source_originals WHERE uid=? AND owner=? AND deleted=0 AND original IS NOT NULL",
                (uid, uid, owner)).fetchall()
            hidden = {row[0] for row in connection.execute("SELECT record_id FROM source_unbound WHERE uid=? AND deleted=1 "
                "UNION SELECT record_id FROM source_originals WHERE uid=? AND owner=? AND deleted=1", (uid, uid, owner))}
            ancestors, seen = [], {uid}
            previous_uid = uid
            while ledger is not None:
                previous = connection.execute("SELECT owner,old_uid FROM source_role_reassignments WHERE new_uid=?", (previous_uid,)).fetchone()
                if previous is None:
                    break
                if previous["owner"] != owner or previous["old_uid"] in seen:
                    raise ValueError("chat_source_integrity_error")
                previous_uid = previous["old_uid"]
                ancestors.append(previous_uid)
                seen.add(previous_uid)
        result = {}
        for row in rows:
            if row["verified_owner"] != owner:
                raise ValueError("chat_sync_owner_mismatch")
            if hashlib.sha256(row["original"].encode()).hexdigest() != row["original_hash"]:
                raise ValueError("chat_source_integrity_error")
            if row["scope"] != "private-chat-original-v1" or row["origin_stream_id"] is None or row["origin_seq"] is None:
                continue
            original = ChatOriginal(**json.loads(row["original"]))
            record = {"schemaVersion": 1, "ownerId": owner, "characterSyncId": uid, "channel": "chat", "scope": "private-chat",
                "messageId": row["record_id"], "originStreamId": row["origin_stream_id"], "originSeq": row["origin_seq"], **original.fields()}
            encode_chat_original(record)
            result[row["record_id"]] = record
        if ledger is not None:
            # Legacy source order is deliberately not reconstructed for upload. Its old
            # canonical ledger may still supply a local-only view across repeated UID changes.
            # Read every predecessor fence first: a later local delete wins over all old views.
            for identity in [uid, *ancestors]:
                hidden.update(ledger.deleted_message_ids(ChatDomain(owner, identity)))
            for ancestor in ancestors:
                for payload in ledger.visible_originals(ChatDomain(owner, ancestor)):
                    record = decode_chat_original(payload)
                    if record["ownerId"] != owner or record["characterSyncId"] != ancestor:
                        raise ValueError("chat_source_integrity_error")
                    result.setdefault(record["messageId"], {**record, "characterSyncId": uid})
        return [record for identity, record in result.items() if identity not in hidden]

    def local_history(self, uid, owner=None):
        """Read fixed local originals, including history not yet approved for upload."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT record_id,original,original_hash,origin_stream_id,origin_seq FROM source_unbound WHERE uid=? "
                "AND deleted=0 AND original IS NOT NULL AND (verified_owner IS NULL OR verified_owner=?) "
                "UNION ALL SELECT record_id,original,original_hash,origin_stream_id,origin_seq FROM source_originals "
                "WHERE uid=? AND owner=? AND deleted=0 AND original IS NOT NULL",
                (uid, owner, uid, owner)).fetchall()
        records = []
        for identity, raw, digest, stream, seq in rows:
            if hashlib.sha256(raw.encode()).hexdigest() != digest:
                raise ValueError("chat_source_integrity_error")
            records.append({"messageId": identity, "characterSyncId": uid, **ChatOriginal(**json.loads(raw)).fields(),
                            "originStreamId": stream, "originSeq": seq})
        return records

    def stored_ids(self, uid):
        """Include erased IDs so reading the archive cannot restore a deleted record."""
        with closing(self._connect()) as connection:
            return {row[0] for row in connection.execute(
                "SELECT record_id FROM source_unbound WHERE uid=? UNION SELECT record_id FROM source_originals WHERE uid=? "
                "UNION SELECT record_id FROM source_local_replies WHERE uid=?", (uid, uid, uid))}

    def commit_unbound(self, uid, record_id, original: ChatOriginal):
        """Keep producer-proven raw bytes without assigning an account or upload task."""
        self.commit_history(uid, [(record_id, original, None)])

    def commit_history(self, uid, records):
        """Import archive records in one durable transaction, preserving producer fences."""
        from utils.config_manager.reserved_schema import is_valid_character_uid
        if not is_valid_character_uid(uid):
            raise ValueError("invalid_chat_source_identity")
        committed = []
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_current_uid(connection, uid)
            for record_id, original, origin in records:
                if (not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id)
                        or origin is not None and (origin not in ("agent", "tool") or original.role != "assistant")):
                    raise ValueError("invalid_chat_source_identity")
                payload = json.dumps(asdict(original), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                if origin is not None:
                    old = connection.execute("SELECT uid,origin,original_hash FROM source_local_replies WHERE record_id=?", (record_id,)).fetchone()
                    if old:
                        if tuple(old) != (uid, origin, digest):
                            raise ValueError("chat_source_identity_conflict")
                        continue
                    for table in ("source_originals", "source_unbound"):
                        if connection.execute(f"SELECT 1 FROM {table} WHERE record_id=?", (record_id,)).fetchone():
                            raise ValueError("chat_source_identity_conflict")
                    connection.execute("INSERT INTO source_local_replies (record_id,uid,origin,original,original_hash) VALUES (?,?,?,?,?)",
                                       (record_id, uid, origin, payload, digest))
                else:
                    if connection.execute("SELECT 1 FROM source_local_replies WHERE record_id=?", (record_id,)).fetchone():
                        raise ValueError("chat_source_identity_conflict")
                    old = connection.execute("SELECT uid,original_hash FROM source_unbound WHERE record_id=?", (record_id,)).fetchone()
                    bound = connection.execute("SELECT uid,original_hash FROM source_originals WHERE record_id=?", (record_id,)).fetchone()
                    if old or bound:
                        if tuple(old or bound) != (uid, digest):
                            raise ValueError("chat_source_identity_conflict")
                        continue
                    stream, seq = self._allocate_origin(connection, uid)
                    connection.execute("INSERT INTO source_unbound "
                                       "(record_id,uid,scope,original,original_hash,queued_at_ms,deleted,origin_stream_id,origin_seq,verified_owner) "
                                       "VALUES (?,?,?,?,?,?,0,?,?,?)",
                                       (record_id, uid, "private-chat-original-v1", payload, digest, time.time_ns() // 1_000_000, stream, seq,
                                        (connection.execute("SELECT owner FROM source_role_reassignments WHERE new_uid=?", (uid,)).fetchone() or [None])[0]))
                committed.append((record_id, origin))
        for record_id, origin in committed:
            if origin is None:
                get_trace().emit("source_committed", character_uid=uid, message_id=record_id, local_message_id=record_id)
            else:
                get_trace().emit("local_reply_committed", character_uid=uid, message_id=record_id, source_origin=origin)

    def commit(self, owner, uid, record_id, original: ChatOriginal):
        """Atomically save immutable raw bytes and the recoverable pending marker."""
        ChatDomain(owner, uid)
        if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id):
            raise ValueError("invalid_chat_source_identity")
        payload = json.dumps(asdict(original), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_current_uid(connection, uid, owner)
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
            # Save actual arrival alongside raw bytes and pending=1. Recovery or
            # an idempotent producer retry must never restart the waiting clock.
            stream, seq = self._allocate_origin(connection, uid)
            connection.execute(
                "INSERT INTO source_originals "
                "(record_id,owner,uid,original,original_hash,pending,deleted,queued_at_ms,origin_stream_id,origin_seq) VALUES (?,?,?,?,?,1,0,?,?,?)",
                (record_id, owner, uid, payload, digest, time.time_ns() // 1_000_000, stream, seq),
            )
        get_trace().emit("source_committed", character_uid=uid, message_id=record_id, local_message_id=record_id)

    def commit_local_reply(self, uid, record_id, original: ChatOriginal, origin):
        """Retain tool provenance before enrolling its text in the shared chat source."""
        if origin not in ("agent", "tool") or original.role != "assistant":
            raise ValueError("invalid_chat_source_identity")
        self.commit_history(uid, [(record_id, original, origin)])

    def clear_local_replies(self, uid, message_ids=None):
        """Erase local-only bodies and keep fixed identities against late retries."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            known = {row[0] for row in connection.execute("SELECT record_id FROM source_local_replies WHERE uid=?", (uid,))}
            identities = list(known) if message_ids is None else list(dict.fromkeys(identity for identity in message_ids if identity in known))
            connection.executemany("UPDATE source_local_replies SET original=NULL,deleted=1 WHERE uid=? AND record_id=? AND deleted=0",
                                   [(uid, identity) for identity in identities])
        return identities

    def include_local_replies(self, uid, *, owner=None, message_ids=None, hidden=()):
        """Reuse fixed tool-reply IDs; old history stays unbound until its normal approval."""
        hidden = set(hidden)
        selected = set(message_ids) if message_ids is not None else None
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_current_uid(connection, uid, owner)
            rows = connection.execute("SELECT * FROM source_local_replies WHERE uid=? AND deleted=0 ORDER BY rowid", (uid,))
            for row in rows:
                identity = row["record_id"]
                if identity in hidden or (selected is not None and identity not in selected):
                    continue
                existing = connection.execute("SELECT uid,original_hash FROM source_unbound WHERE record_id=? UNION ALL "
                    "SELECT uid,original_hash FROM source_originals WHERE record_id=?", (identity, identity)).fetchone()
                if existing:
                    if tuple(existing) != (uid, row["original_hash"]):
                        raise ValueError("chat_source_identity_conflict")
                    continue
                if hashlib.sha256(row["original"].encode("utf-8")).hexdigest() != row["original_hash"]:
                    raise ValueError("chat_source_integrity_error")
                stream, seq = self._allocate_origin(connection, uid)
                if owner is None:
                    verified = (connection.execute("SELECT owner FROM source_role_reassignments WHERE new_uid=?", (uid,)).fetchone() or [None])[0]
                    connection.execute("INSERT INTO source_unbound "
                        "(record_id,uid,scope,original,original_hash,queued_at_ms,origin_stream_id,origin_seq,verified_owner) "
                        "VALUES (?,?,?,?,?,?,?,?,?)", (identity, uid, "private-chat-original-v1", row["original"],
                        row["original_hash"], time.time_ns() // 1_000_000, stream, seq, verified))
                else:
                    connection.execute("INSERT INTO source_originals "
                        "(record_id,owner,uid,original,original_hash,pending,queued_at_ms,origin_stream_id,origin_seq) "
                        "VALUES (?,?,?,?,?,1,?,?,?)", (identity, owner, uid, row["original"], row["original_hash"],
                        time.time_ns() // 1_000_000, stream, seq))

    @staticmethod
    def local_reply_ids(path, uid, *, live_only=False):
        """Freeze local-only identities without opening a writer or reading bodies."""
        path = Path(path)
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='source_local_replies'").fetchone():
                return []
            query = ("SELECT record_id FROM source_local_replies l WHERE uid=? AND NOT EXISTS "
                     "(SELECT 1 FROM source_unbound u WHERE u.record_id=l.record_id) AND NOT EXISTS "
                     "(SELECT 1 FROM source_originals o WHERE o.record_id=l.record_id)")
            if live_only:
                query += " AND deleted=0"
            return [row[0] for row in connection.execute(query + " ORDER BY rowid", (uid,))]

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

    def pending(self, maximum, bindings=None):
        """Return only explicitly proven pending originals in durable insertion order."""
        with closing(self._connect()) as connection:
            identity = connection.execute("SELECT * FROM source_identity WHERE singleton=1").fetchone()
            eligible = ""
            parameters = []
            if bindings is not None:
                if not bindings:
                    return []
                eligible = " AND (" + " OR ".join("(owner=? AND uid=?)" for _ in bindings) + ")"
                parameters = [value for binding in bindings for value in (binding["owner_id"], binding["character_uid"])]
            rows = connection.execute("SELECT * FROM source_originals WHERE pending=1" + eligible
                                      + " ORDER BY rowid LIMIT ?", (*parameters, maximum)).fetchall()
            return [(dict(row), SourceLocator(identity["database_id"], identity["generation"], row["record_id"])) for row in rows]

    def captured(self, record_id):
        """Clear pending only after the ledger has durably accepted this exact identity."""
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE source_originals SET pending=0 WHERE record_id=?", (record_id,))

    def known_ids(self, owner, uid):
        """Include pending originals in a clear boundary without inferring by clock."""
        with closing(self._connect()) as connection:
            return list(dict.fromkeys([row[0] for row in connection.execute(
                "SELECT record_id FROM source_originals WHERE owner=? AND uid=? ORDER BY rowid", (owner, uid),
            )] + [row[0] for row in connection.execute(
                "SELECT record_id FROM source_unbound WHERE uid=? AND deleted=0 ORDER BY rowid", (uid,),
            )]))

    def pending_deletions(self, uid, maximum):
        """Page proven retained IDs without requiring approval to upload bodies."""
        with closing(self._connect()) as connection:
            return [row[0] for row in connection.execute(
                "SELECT record_id FROM source_unbound WHERE uid=? AND deleted=1 AND deletion_pending=1 "
                "AND scope='private-chat-original-v1' ORDER BY rowid LIMIT ?", (uid, maximum),
            )]

    def deletions_captured(self, uid, message_ids):
        """Clear source outbox flags only after permanent ledger fences commit."""
        with closing(self._connect()) as connection, connection:
            connection.executemany("UPDATE source_unbound SET deletion_pending=0 WHERE uid=? AND record_id=? AND deleted=1",
                                   [(uid, identity) for identity in message_ids])

    def deleted_ids(self, uid, message_ids):
        """Find proven local deletion fences for only the incoming page's IDs."""
        if not message_ids:
            return []
        with closing(self._connect()) as connection:
            return [row[0] for row in connection.execute(
                "SELECT record_id FROM source_unbound WHERE uid=? AND deleted=1 "
                "AND scope='private-chat-original-v1' AND record_id IN ("
                + ",".join("?" for _ in message_ids) + ")", (uid, *message_ids),
            )]

    def purge(self, owner, uid, message_ids):
        """Remove readable originals while retaining immutable deletion identities."""
        if not message_ids:
            return
        from services.chat_sync.recovery import purge_outage_originals
        # Outage copies live outside this database and may be the only readable copy.
        purge_outage_originals(self.path.parent, uid, message_ids)
        with closing(self._connect()) as connection, connection:
            dirty = None
            for offset in range(0, len(message_ids), 200):
                page = message_ids[offset:offset + 200]
                placeholders = ','.join('?' for _ in page)
                dirty = connection.execute(
                    "SELECT 1 FROM source_originals WHERE owner=? AND uid=? AND (original IS NOT NULL OR pending=1 OR deleted=0) "
                    "AND record_id IN (" + placeholders + ") UNION ALL "
                    "SELECT 1 FROM source_unbound WHERE uid=? AND deleted=0 AND record_id IN (" + placeholders + ") UNION ALL "
                    "SELECT 1 FROM source_recovery_candidates WHERE uid=? AND (original IS NOT NULL OR payload IS NOT NULL) "
                    "AND record_id IN (" + placeholders + ") LIMIT 1",
                    (owner, uid, *page, uid, *page, uid, *page),
                ).fetchone()
                if dirty is not None:
                    break
            if dirty is None:
                return
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "UPDATE source_originals SET original=NULL,pending=0,deleted=1 "
                "WHERE owner=? AND uid=? AND record_id=?",
                [(owner, uid, identity) for identity in message_ids],
            )
            # The same source writer cancels fixed members before another page
            # can transfer their bytes into the authorized recovery outbox.
            connection.executemany("UPDATE source_unbound SET original=NULL,"
                                   "deletion_pending=CASE WHEN deleted=0 THEN 1 ELSE deletion_pending END,deleted=1 WHERE uid=? AND record_id=?",
                                   [(uid, identity) for identity in message_ids])
            # Recovery candidates keep their deletion identity, never the readable body.
            connection.executemany("UPDATE source_recovery_candidates SET original=NULL,payload=NULL,deleted=1 "
                                   "WHERE uid=? AND record_id=?", [(uid, identity) for identity in message_ids])
            connection.executemany("UPDATE source_local_replies SET original=NULL,deleted=1 WHERE uid=? AND record_id=?",
                                   [(uid, identity) for identity in message_ids])
            connection.executemany(
                "UPDATE source_history_members SET status='cancelled' WHERE record_id=? AND task_id IN "
                "(SELECT task_id FROM source_history_tasks WHERE uid=?)",
                [(identity, uid) for identity in message_ids],
            )

    @staticmethod
    def metadata(task):
        """Expose fixed consent/progress counts without any raw body or session pin."""
        if task is None:
            return None
        return {"taskId": task["task_id"], "characterSyncId": task["uid"],
                "localName": task["local_name"], "cloudName": task["cloud_name"],
                "count": task["count"], "unverifiedCount": None, "status": task["status"],
                "registeredCount": task["registered"], "cancelledCount": task["cancelled"]}

    def task(self, uid, task_id=None):
        """Read one role-scoped fixed task and durable per-member progress."""
        with closing(self._connect()) as connection:
            query = "SELECT * FROM source_history_tasks WHERE uid=?"
            args = [uid]
            if task_id is not None:
                query += " AND task_id=?"
                args.append(task_id)
            row = connection.execute(query + " ORDER BY rowid DESC LIMIT 1", args).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["registered"] = connection.execute(
                "SELECT count(*) FROM source_history_members WHERE task_id=? AND status='registered'", (result["task_id"],),
            ).fetchone()[0]
            result["cancelled"] = connection.execute(
                "SELECT count(*) FROM source_history_members WHERE task_id=? AND status='cancelled'", (result["task_id"],),
            ).fetchone()[0]
            return result

    def approved_tasks(self, uid):
        """Find unfinished approved snapshots independently of ledger queue size."""
        with closing(self._connect()) as connection:
            identities = [row[0] for row in connection.execute(
                "SELECT task_id FROM source_history_tasks WHERE uid=? AND status='approved' ORDER BY rowid", (uid,),
            )]
        return [self.task(uid, identity) for identity in identities]

    def awaiting_registration(self, uid):
        """Count all approved waiting members even when a newer preview is open."""
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT count(*) FROM source_history_members m JOIN source_history_tasks t ON t.task_id=m.task_id "
                "JOIN source_unbound u ON u.record_id=m.record_id WHERE t.uid=? AND t.status='approved' "
                "AND m.status='waiting' AND u.deleted=0", (uid,),
            ).fetchone()[0]

    def prepare_history(self, binding, service_url, fingerprint, local_name, cloud_name, *, device_id, after_origin_seq=0,
                        through_origin_seq=None, supersede_preview=True):
        """Freeze only trusted unbound source IDs in one SQLite snapshot."""
        if through_origin_seq is not None and (type(through_origin_seq) is not int or through_origin_seq < 0):
            raise ValueError("chat_sync_history_changed")
        uid, task_id = binding["character_uid"], uuid4().hex
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            identity = connection.execute("SELECT * FROM source_identity WHERE singleton=1").fetchone()
            # A fresh preview supersedes only unapproved consent. Approved
            # snapshots retain their exact members and can resume independently.
            # A background snapshot consumes already saved combined consent;
            # it must not invalidate a manual preview currently on screen.
            if supersede_preview:
                connection.execute("UPDATE source_history_tasks SET status='cancelled' WHERE uid=? AND status='preview'", (uid,))
            connection.execute("INSERT INTO source_history_tasks "
                               "(task_id,uid,binding,service_url,fingerprint,database_id,generation,local_name,cloud_name,status,count,device_id) "
                               "VALUES (?,?,?,?,?,?,?,?,?,'preview',0,?)",
                               (task_id, uid, json.dumps(binding, sort_keys=True), service_url, fingerprint,
                                identity["database_id"], identity["generation"], local_name, cloud_name, device_id))
            connection.execute(
                "INSERT INTO source_history_members(task_id,record_id,ordinal,original_hash,origin_stream_id,origin_seq) "
                "SELECT ?,u.record_id,u.origin_seq,u.original_hash,u.origin_stream_id,u.origin_seq FROM source_unbound u WHERE u.uid=? AND u.deleted=0 "
                "AND u.scope='private-chat-original-v1' AND u.original IS NOT NULL "
                "AND u.origin_stream_id IS NOT NULL AND u.origin_seq IS NOT NULL "
                "AND u.origin_seq>? "
                + ("AND u.origin_seq<=? " if through_origin_seq is not None else "") +
                "AND (u.verified_owner IS NULL OR u.verified_owner=?) "
                "AND NOT EXISTS (SELECT 1 FROM source_originals o WHERE o.record_id=u.record_id) "
                "AND NOT EXISTS (SELECT 1 FROM source_history_members m JOIN source_history_tasks t "
                "ON t.task_id=m.task_id WHERE m.record_id=u.record_id AND t.status IN ('approved','registered'))",
                (task_id, uid, after_origin_seq, *((through_origin_seq,) if through_origin_seq is not None else ()), binding["owner_id"]),
            )
            connection.execute("UPDATE source_history_tasks SET count=(SELECT count(*) FROM source_history_members WHERE task_id=?) WHERE task_id=?",
                               (task_id, task_id))
        return self.task(uid, task_id)

    def cancel_preview(self, uid, task_id):
        """Cancel only the caller's unapproved snapshot, retaining raw sources and committed grants."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE source_history_members SET status='cancelled' WHERE task_id=? AND status='waiting' "
                               "AND EXISTS (SELECT 1 FROM source_history_tasks WHERE uid=? AND task_id=? AND status='preview')",
                               (task_id, uid, task_id))
            connection.execute("UPDATE source_history_tasks SET status='cancelled' WHERE uid=? AND task_id=? AND status='preview'",
                               (uid, task_id))

    def approve_history(self, uid, task_id):
        """Approve the persisted snapshot once without reading newer source rows."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute("SELECT * FROM source_history_tasks WHERE uid=? AND task_id=?", (uid, task_id)).fetchone()
            identity = connection.execute("SELECT * FROM source_identity WHERE singleton=1").fetchone()
            if task is None or (task["database_id"], task["generation"]) != (identity["database_id"], identity["generation"]):
                raise ValueError("chat_sync_history_changed")
            if task["status"] == "preview":
                # A manual preview can coexist with a background combined
                # grant. Already covered IDs retain their first registration;
                # confirming the older preview cannot register them twice.
                connection.execute(
                    "UPDATE source_history_members SET status='cancelled' WHERE task_id=? AND status='waiting' "
                    "AND (EXISTS (SELECT 1 FROM source_originals o WHERE o.record_id=source_history_members.record_id) "
                    "OR EXISTS (SELECT 1 FROM source_history_members m JOIN source_history_tasks t ON t.task_id=m.task_id "
                    "WHERE m.record_id=source_history_members.record_id AND m.task_id!=? AND t.status IN ('approved','registered')))",
                    (task_id, task_id),
                )
            connection.execute("UPDATE source_history_tasks SET status='approved' WHERE uid=? AND task_id=? AND status='preview'",
                               (uid, task_id))
            # Empty and fully covered snapshots are complete without leaving an
            # approved job that can never consume a registration page.
            connection.execute("UPDATE source_history_tasks SET status='registered' WHERE uid=? AND task_id=? AND status='approved' "
                               "AND NOT EXISTS (SELECT 1 FROM source_history_members WHERE task_id=? AND status='waiting')",
                               (uid, task_id, task_id))
        return self.task(uid, task_id)

    def advance_history(self, task, binding, *, maximum=200):
        """Transfer one approved page and its progress atomically into raw recovery."""
        if type(maximum) is not int or not 1 <= maximum <= 200:
            raise ValueError("invalid_chat_source_recovery_limit")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            identity = connection.execute("SELECT * FROM source_identity WHERE singleton=1").fetchone()
            if (identity["database_id"], identity["generation"]) != (task["database_id"], task["generation"]):
                raise ValueError("chat_sync_history_changed")
            current = connection.execute("SELECT status FROM source_history_tasks WHERE task_id=?", (task["task_id"],)).fetchone()
            if current is None or current[0] != "approved":
                return 0
            rows = connection.execute(
                "SELECT u.*,m.ordinal,m.original_hash AS fixed_hash,m.origin_stream_id AS fixed_stream,m.origin_seq AS fixed_seq "
                "FROM source_history_members m JOIN source_unbound u ON u.record_id=m.record_id "
                "WHERE m.task_id=? AND m.status='waiting' ORDER BY m.ordinal LIMIT ?", (task["task_id"], maximum),
            ).fetchall()
            for row in rows:
                if row["deleted"] or row["original"] is None:
                    raise ValueError("chat_source_integrity_error")
                if row["uid"] != binding["character_uid"] or row["scope"] != "private-chat-original-v1":
                    raise ValueError("chat_source_integrity_error")
                if row["fixed_hash"] != row["original_hash"] or hashlib.sha256(row["original"].encode("utf-8")).hexdigest() != row["original_hash"]:
                    raise ValueError("chat_source_integrity_error")
                if (row["fixed_stream"], row["fixed_seq"]) != (row["origin_stream_id"], row["origin_seq"]):
                    raise ValueError("chat_source_integrity_error")
                connection.execute(
                    "INSERT INTO source_originals(record_id,owner,uid,original,original_hash,pending,deleted,queued_at_ms,history_task_id,origin_stream_id,origin_seq) "
                    "VALUES (?,?,?,?,?,1,0,?,?,?,?)",
                    (row["record_id"], binding["owner_id"], row["uid"], row["original"], row["original_hash"], row["queued_at_ms"], task["task_id"],
                     row["origin_stream_id"], row["origin_seq"]),
                )
                connection.execute("UPDATE source_unbound SET original=NULL WHERE record_id=?", (row["record_id"],))
                connection.execute("UPDATE source_history_members SET status='registered' WHERE task_id=? AND record_id=?",
                                   (task["task_id"], row["record_id"]))
            if not connection.execute("SELECT 1 FROM source_history_members WHERE task_id=? AND status='waiting' LIMIT 1", (task["task_id"],)).fetchone():
                connection.execute("UPDATE source_history_tasks SET status='registered' WHERE task_id=?", (task["task_id"],))
            return len(rows)


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


async def local_reply_ids(runtime, uid):
    """Snapshot only this role's local-only deletion boundary, without cloud work."""
    return await asyncio.to_thread(ChatOriginalSource.local_reply_ids, _source_path(runtime), uid)


async def clear_local_replies(runtime, uid, message_ids=None):
    """Local-only clearing never creates a remote deletion marker."""
    identities = await local_reply_ids(runtime, uid) if message_ids is None else message_ids
    if not identities:
        return []
    path = _source_path(runtime)
    known = set(await local_reply_ids(runtime, uid))
    identities = list(dict.fromkeys(identity for identity in identities if identity in known))
    live = set(await asyncio.to_thread(ChatOriginalSource.local_reply_ids, path, uid, live_only=True))
    if not live.intersection(identities):
        # Retrying a completed local clear still hides delayed bubbles, without
        # making canonical outage clearing depend on another writer commit.
        return identities
    source = await asyncio.to_thread(ChatOriginalSource, path)
    return await asyncio.to_thread(source.clear_local_replies, uid, identities)


def register_source_deletions(ledger, source, domain, identities):
    """Commit permanent marker authority before acknowledging the source outbox."""
    known = set(ledger.deleted_message_ids(domain, identities))
    missing = [identity for identity in identities if identity not in known]
    if missing:
        ledger.queue_deletions(domain, missing)
    # A prior queued or acknowledged marker already owns the permanent
    # tombstone. Recovery never invents another request after that fence.
    source.deletions_captured(domain.character_uid, identities)


def _recover_deletions(runtime, source, maximum):
    """Transfer one marker page per bound role; permanent tombstones close crash gaps."""
    lock = getattr(runtime, "_commit_lock", None)
    with lock if lock is not None else nullcontext():
        for binding in runtime.state.bindings():
            uid = binding["character_uid"]
            identities = source.pending_deletions(uid, min(maximum, 200))
            if not identities:
                continue
            domain = ChatDomain(binding["owner_id"], uid)
            register_source_deletions(runtime.ledger, source, domain, identities)


async def recover_sources(runtime, *, max_records=200):
    """Replay proven originals directly into the fixed owner's idempotent ledger."""
    if type(max_records) is not int or max_records <= 0:
        raise ValueError("invalid_chat_source_recovery_limit")
    path = _source_path(runtime)
    if not await asyncio.to_thread(path.exists):
        return 0
    source = await asyncio.to_thread(ChatOriginalSource, path)
    # A prior delete may have committed before its source cleanup was interrupted.
    bindings = await asyncio.to_thread(runtime.state.bindings)
    for binding in bindings:
        domain = ChatDomain(binding["owner_id"], binding["character_uid"])
        deleted = await asyncio.to_thread(runtime.ledger.deleted_message_ids, domain)
        await asyncio.to_thread(source.purge, domain.owner_id, domain.character_uid, deleted)
    await asyncio.to_thread(_recover_deletions, runtime, source, max_records)
    recovered = 0
    for row, locator in await asyncio.to_thread(source.pending, max_records, bindings):
        binding = await asyncio.to_thread(runtime.state.binding, row["uid"])
        if not binding or binding["owner_id"] != row["owner"]:
            continue
        # A readable body must still match the immutable bytes committed with its ID.
        if hashlib.sha256(row['original'].encode('utf-8')).hexdigest() != row['original_hash']:
            raise ValueError('chat_source_integrity_error')
        original = ChatOriginal(**json.loads(row["original"]))
        await asyncio.to_thread(runtime.ledger.capture, ChatDomain(row["owner"], row["uid"]), locator,
                                original, message_id=row["record_id"], queued_at_ms=row["queued_at_ms"],
                                queue_lane="history" if row["history_task_id"] else "new",
                                proven_source_origin=None if row["origin_stream_id"] is None and row["origin_seq"] is None
                                else (row["origin_stream_id"], row["origin_seq"]))
        await asyncio.to_thread(source.captured, row["record_id"])
        recovered += 1
    return recovered


async def known_source_ids(runtime, character_uid):
    """Let clear include all locally accepted raw originals, including pending ones."""
    path = _source_path(runtime)
    binding = await asyncio.to_thread(runtime.state.binding, character_uid)
    if not await asyncio.to_thread(path.exists):
        return []
    source = await asyncio.to_thread(ChatOriginalSource, path)
    return await asyncio.to_thread(source.known_ids, binding["owner_id"] if binding else None, character_uid)


async def purge_source_originals(runtime, character_uid, message_ids):
    """Follow the committed ledger tombstone with owner-scoped source cleanup."""
    path = _source_path(runtime)
    binding = await asyncio.to_thread(runtime.state.binding, character_uid)
    if await asyncio.to_thread(path.exists):
        source = await asyncio.to_thread(ChatOriginalSource, path)
        await asyncio.to_thread(source.purge, binding["owner_id"] if binding else None, character_uid, message_ids)


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
    except ValueError as error:
        if str(error) != "chat_source_role_reassigned":
            raise
        # A producer can hold the old config across the source/config-file commit gap.
        # Follow only the fixed local lineage, retaining raw text without recreating a
        # retired cloud identity. A newer Off is observed under the same commit gate.
        def retain_reassigned():
            with runtime._commit_lock:
                target, owner, seen = uid, None, set()
                while mapping := source.reassignment(target):
                    if target in seen or owner not in (None, mapping["ownerId"]):
                        raise ValueError("chat_source_integrity_error")
                    seen.add(target)
                    owner, target = mapping["ownerId"], mapping["newUid"]
                current = runtime.state.binding(target)
                if current and current["owner_id"] != owner:
                    raise ValueError("chat_sync_owner_mismatch")
                if current and current["enabled"] and runtime.state.choices().get(target, True):
                    # This is the existing producer grant, never a newly created consent.
                    source.commit(owner, target, record_id, original)
                else:
                    source.commit_unbound(target, record_id, original)
        await asyncio.to_thread(retain_reassigned)
        runtime.wake_scheduler()
        return record_id
    # Keep the original durable before the model starts; ledger replay belongs
    # to the owned scheduler, so the producer never waits for backlog processing.
    # This first transaction is the crash-recovery barrier. Scheduling an unsaved
    # body in the background would lose accepted originals on process termination.
    runtime.wake_scheduler()
    return record_id


async def save_local_reply(lanlan_name, text, proof, *, config_manager=None, character_uid=None):
    """Persist tool text with truthful provenance and reuse the existing chat grant."""
    from services.chat_sync.runtime import get_desktop_chat_sync_runtime
    from utils.config_manager import get_config_manager

    manager = config_manager or get_config_manager()
    uid = character_uid or await character_uid_for(lanlan_name, manager)
    if not uid:
        return None
    runtime = await get_desktop_chat_sync_runtime(manager)
    source = await asyncio.to_thread(ChatOriginalSource, _source_path(runtime))
    original = ChatOriginal("assistant", text, original_time=proof["originalTime"], timezone="UTC")
    target = uid
    try:
        await asyncio.to_thread(source.commit_local_reply, uid, proof["recordId"], original, proof["origin"])
    except ValueError as error:
        if str(error) != "chat_source_role_reassigned":
            raise
        # Follow only the fixed local lineage; a transfer cannot create a cloud grant.
        def retain_local():
            with runtime._commit_lock:
                target, seen = uid, set()
                while mapping := source.reassignment(target):
                    if target in seen:
                        raise ValueError("chat_source_integrity_error")
                    seen.add(target)
                    target = mapping["newUid"]
                source.commit_local_reply(target, proof["recordId"], original, proof["origin"])
                return target
        target = await asyncio.to_thread(retain_local)
    from services.chat_sync.recovery import role_pending
    binding = await asyncio.to_thread(runtime.state.binding, target)
    if binding:
        owner = None if await asyncio.to_thread(role_pending, runtime, target) else binding["owner_id"]
        await asyncio.to_thread(source.include_local_replies, target, owner=owner, message_ids=[proof["recordId"]])
        runtime.wake_scheduler()
    return proof["recordId"]


def mark_turn_origin(manager, turn_id, origin):
    """Bind an explicit origin to one speech identity rather than a global current flag."""
    # Preserve provenance independently of future upload policy. Later tool
    # evidence travels separately; emitted identity/proof stays fixed (MSYNC-004).
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


def mark_tool_turn(manager, turn_id):
    """Queue tool evidence for one fixed reply without mutating emitted proof."""
    queue = getattr(manager, "sync_message_queue", None)
    if not turn_id or queue is None:
        return
    proof = mark_turn_origin(manager, turn_id, "tool")
    # This internal event precedes the tool await, including failures or a
    # session close with no post-tool text. It never becomes chat content.
    queue.put({"type": "system", "data": "chat original tool", "chat_original_id": proof["recordId"]})


# Gemini keeps streaming transcript of a reply for up to three seconds after
# its turn terminal; the realtime client treats that text as the same reply.
# Sealing waits slightly longer so the saved original holds the whole reply.
LATE_TEXT_GRACE_SECONDS = 3.5

# Live buffers, so a clear can first save replies still waiting out their grace.
_LIVE_BUFFERS = weakref.WeakSet()


def pending_original_ids():
    """Keep archive readers from enrolling a reply before its producer seals the tail."""
    return {identity for buffer in list(_LIVE_BUFFERS) for identity in buffer.records}


async def flush_terminated_originals(character_uid):
    """Save one character's completed replies before a fixed deletion snapshot."""
    for buffer in list(_LIVE_BUFFERS):
        await buffer.flush_terminated(character_uid=character_uid)


class AssistantOriginalBuffer:
    """Collect emitted text per stable message ID; persist only at final boundaries."""

    # Sealed IDs remembered so text arriving after the grace is dropped, not conflicting.
    _SEALED_LIMIT = 64

    def __init__(self, *, tail_grace=0.0):
        """Start without inferred identities for legacy or system messages."""
        self.records = {}
        self._traced_exclusions = set()
        self._tool_records = set()
        self._tail_grace = tail_grace
        # Record ID -> pending deferred seal after its terminal.
        self._deferred = {}
        self._sealed = {}
        _LIVE_BUFFERS.add(self)

    def mark_tool(self, record_id):
        """Retain late tool evidence even when ordinary chunks arrived first."""
        if isinstance(record_id, str) and re.fullmatch(r"[0-9a-f]{32}", record_id):
            self._tool_records.add(record_id)
            self._trace_exclusion({"recordId": record_id, "origin": "tool"}, "nonchat_origin")

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
        if not isinstance(proof, dict) or proof.get("origin") not in ("chat", "proactive", "agent", "tool"):
            self._trace_exclusion(proof, "nonchat_origin" if isinstance(proof, dict)
                                  and proof.get("origin") in ("agent", "avatar", "system", "tool", "mirror") else "unknown_origin")
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
                self._tool_records.discard(record_id)
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
                self._tool_records.discard(identity)
                continue
            if "terminal" not in entry:
                entry["terminal"] = {"name": lanlan_name, "uid": None, "excluded": excluded}
            # A terminated reply keeps its own terminal's decision and pinned UID.
            await self._seal(identity)
        if record_id is None:
            self._tool_records.intersection_update(self.records)
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
            # Tool provenance remains truthful while its text uses the same sync grant.
            proof = entry["proof"]
            if identity in self._tool_records and proof["origin"] in ("chat", "proactive"):
                proof = {**proof, "origin": "tool"}
            local_only = proof["origin"] in ("agent", "tool")
            # A UID pinned at the terminal survives a rename before this seal.
            pinned = {"character_uid": terminal["uid"]} if terminal["uid"] else {}
            try:
                if local_only:
                    saved = await save_local_reply(lanlan_name, "".join(entry["chunks"]), proof, **pinned)
                else:
                    saved = await save_original(lanlan_name, "assistant", "".join(entry["chunks"]), record_id=identity,
                                                original_time=entry["proof"]["originalTime"], **pinned)
            except Exception:
                get_trace().emit("assistant_source_finished", message_id=identity, source_origin=proof["origin"],
                                 source_reason="local_save_failed" if local_only else "save_failed", outcome="error")
                print("[chat-sync] original_save_failed")
                # A failed save stays retryable under the same identity.
                self._sealed.pop(identity, None)
                return
            get_trace().emit("assistant_source_finished", message_id=identity, source_origin=proof["origin"],
                             source_reason=("local_saved" if saved else "local_save_unavailable") if local_only
                             else ("saved" if saved else "save_unavailable"), outcome="success" if saved else "error")
        else:
            get_trace().emit("assistant_source_finished", message_id=identity, source_origin=entry["proof"]["origin"],
                             source_reason="terminal_excluded", outcome="success")
        self.records.pop(identity, None)
        self._tool_records.discard(identity)
        while len(self._sealed) > self._SEALED_LIMIT:
            self._sealed.pop(next(iter(self._sealed)))

    async def flush_terminated(self, *, character_uid=None):
        """Seal replies already past their terminal now, e.g. before shutdown or a clear."""
        for identity in [key for key, entry in self.records.items() if "terminal" in entry
                         and character_uid in (None, entry["terminal"]["uid"])]:
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
        self._tool_records.intersection_update(self.records)
