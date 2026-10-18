"""Local chat-sync identity and pending-record foundation.

Callers must verify private-chat provenance before capture; role labels alone
are not proof. The opt-in runtime adapter supplies the fixed account binding.
The ledger path must be outside Steam-restored memory directories. Async callers
must offload these synchronous SQLite operations with asyncio.to_thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from services.chat_sync.writer import store_connection
import time
from uuid import uuid4

from services.chat_sync.protocol import (
    MAX_ORIGIN_SEQ,
    decode_chat_original,
    encode_chat_original,
    validate_sync_domain,
)


# Omitted timestamps mean a new local arrival; explicit None preserves a legacy
# source's unknown arrival clock through recovery without inventing a date.
_CAPTURE_TIME_NOW = object()

# The largest original one automatic batch can carry. A larger original can
# never be uploaded, so it stays on this device instead of blocking the queue.
MAX_SYNC_ORIGINAL_BYTES = 256 * 1024


class IdentityConflict(ValueError):
    """A stable identity was presented with different immutable content."""


class OriginalNeedsFragmentation(ValueError):
    """The oldest pending original cannot fit the caller's current byte budget."""

    def __init__(self, message_id: str, byte_length: int):
        """Report the original identity and size without including its text."""
        super().__init__("original_requires_fragmentation")
        self.message_id = message_id
        self.byte_length = byte_length


@dataclass(frozen=True)
class SealedChatBatch:
    """An immutable local snapshot, not a compressed or HTTP wire envelope.

    Members contain (message ID, canonical original bytes, original SHA-256).
    sealed_bytes includes the full domain and batch ID; sha256 covers those
    internal bytes. No acknowledgement or delivery status is implied.
    """

    batch_id: str
    members: tuple[tuple[str, bytes, str], ...]
    sealed_bytes: bytes
    sha256: str


@dataclass(frozen=True)
class SealedChatDeletion:
    """A fixed prototype deletion request retained until its exact receipt arrives."""

    deletion_id: str
    message_ids: tuple[str, ...]
    payload: bytes


def _message_ids(value: object, *, allow_empty: bool = False) -> tuple[str, ...]:
    """Validate explicit IDs without expanding a clear operation by device time."""
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError("invalid_message_ids")
    if any(
        not isinstance(item, str) or len(item) != 32
        or any(char not in "0123456789abcdef" for char in item)
        for item in value
    ) or len(set(value)) != len(value):
        raise ValueError("invalid_message_ids")
    return tuple(value)


def _receipt(receipt: object, id_field: str) -> tuple[str, tuple[str, ...]]:
    """Accept a complete prototype receipt without treating its cursor as downloaded."""
    try:
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {id_field, "messageIds", "cursor"}
            or type(receipt["cursor"]) is not int or receipt["cursor"] < 0
        ):
            raise ValueError
        request_id = _message_ids([receipt[id_field]])[0]
        return request_id, _message_ids(receipt["messageIds"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid_chat_receipt") from None


def _read_deletion(row: tuple) -> SealedChatDeletion:
    """Verify fixed deletion bytes before returning them for a retry."""
    deletion_id, payload, digest = row
    try:
        _message_ids([deletion_id])
        body = json.loads(payload)
        if (
            not isinstance(payload, bytes) or not isinstance(body, dict)
            or set(body) != {"messageIds"}
            or _encode(body).encode("utf-8") != payload
            or hashlib.sha256(payload).hexdigest() != digest
        ):
            raise ValueError
        return SealedChatDeletion(deletion_id, _message_ids(body["messageIds"]), payload)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise ValueError("invalid_chat_deletion") from None


def _identifier(value: str) -> None:
    """Reject ambiguous identities instead of normalizing a persisted key."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("invalid_identity")


@dataclass(frozen=True)
class ChatDomain:
    """Complete isolation key; model resources and character names are not keys."""

    owner_id: str
    character_uid: str
    channel: str = "chat"
    scope: str = "private-chat"

    def __post_init__(self):
        """Allow only the persisted desktop UID and the first-release chat scope."""
        validate_sync_domain(self.owner_id, self.character_uid, self.channel, self.scope)

    def key(self) -> str:
        """Use a JSON tuple so separators inside identifiers cannot collide."""
        return _encode([self.owner_id, self.character_uid, self.channel, self.scope])


@dataclass(frozen=True)
class SourceLocator:
    """Persisted source identity, storage generation and record locator.

    database_id and generation must come from verified durable source metadata,
    never inferred from a path, row number, timestamp or matching text.
    """

    database_id: str
    generation: str
    record_id: str

    def __post_init__(self):
        """Require all source identity parts before a local alias can be created."""
        for value in (self.database_id, self.generation, self.record_id):
            _identifier(value)

    def key(self) -> str:
        """Keep the storage generation in the alias when row IDs are reused."""
        return _encode([self.database_id, self.generation, self.record_id])


@dataclass(frozen=True)
class ChatOriginal:
    """Verified original fields only; no derived context or attachment paths."""

    role: str
    text: str
    original_time: str | None = None
    timezone: str | None = None
    conversation_id: str | None = None
    reply_to_message_id: str | None = None

    def __post_init__(self):
        """Validate without trimming text or inventing missing time information."""
        if self.role not in ("user", "assistant") or not isinstance(self.text, str):
            raise ValueError("invalid_chat_original")
        # Reject unpaired surrogates instead of silently changing original text.
        self.text.encode("utf-8")
        for value in (self.original_time, self.timezone, self.conversation_id, self.reply_to_message_id):
            if value is not None:
                # Preserve occurrence metadata verbatim; it is not a local key.
                if not isinstance(value, str) or not value:
                    raise ValueError("invalid_chat_original")
                value.encode("utf-8")
        if self.timezone is not None and self.original_time is None:
            raise ValueError("timezone_without_time")

    def fields(self) -> dict:
        """Explicitly whitelist immutable original fields in the v1 payload."""
        return {
            "role": self.role,
            "text": self.text,
            "originalTime": self.original_time,
            "timezone": self.timezone,
            "conversationId": self.conversation_id,
            "replyToMessageId": self.reply_to_message_id,
        }


def _encode(value: object) -> str:
    """Encode internal SQLite keys and original-field comparison values."""
    # Wire payloads use encode_chat_original; local locators are not wire data.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate_batch_member(domain: ChatDomain, message_id: str, payload: bytes, digest: str) -> None:
    """Reject corrupt or cross-domain originals before freezing or returning bytes."""
    try:
        record = decode_chat_original(payload)
        if (
            record["messageId"] != message_id
            or record["ownerId"] != domain.owner_id
            or record["characterSyncId"] != domain.character_uid
            or record["channel"] != domain.channel
            or record["scope"] != domain.scope
            or hashlib.sha256(payload).hexdigest() != digest
        ):
            raise ValueError("invalid_chat_batch")
    except (TypeError, ValueError, RecursionError):
        # Malformed local JSON can exceed the shared decoder's recursion limit.
        raise ValueError("invalid_chat_batch") from None


def _read_sealed_batch(domain: ChatDomain, row: tuple) -> SealedChatBatch:
    """Validate the persisted snapshot without rebuilding it from current pending rows."""
    batch_id, sealed_bytes, digest = row
    try:
        if not isinstance(sealed_bytes, bytes) or hashlib.sha256(sealed_bytes).hexdigest() != digest:
            raise ValueError("invalid_chat_batch")
        body = json.loads(sealed_bytes)
        if (
            not isinstance(body, dict) or set(body) != {"batchId", "domain", "members"}
            or body["batchId"] != batch_id
            or not isinstance(batch_id, str) or len(batch_id) != 32
            or any(char not in "0123456789abcdef" for char in batch_id)
            or body["domain"] != json.loads(domain.key())
            or not isinstance(body["members"], list) or not 1 <= len(body["members"]) <= 200
            or _encode(body).encode("utf-8") != sealed_bytes
        ):
            raise ValueError("invalid_chat_batch")
        members = []
        for member in body["members"]:
            if not isinstance(member, list) or len(member) != 3 or not all(isinstance(item, str) for item in member):
                raise ValueError("invalid_chat_batch")
            message_id, original, original_digest = member
            payload = original.encode("utf-8")
            _validate_batch_member(domain, message_id, payload, original_digest)
            members.append((message_id, payload, original_digest))
        if len({member[0] for member in members}) != len(members):
            raise ValueError("invalid_chat_batch")
        return SealedChatBatch(batch_id, tuple(members), sealed_bytes, digest)
    except (KeyError, TypeError, ValueError, RecursionError):
        # Corrupt snapshots keep the same safe failure contract as bad members.
        raise ValueError("invalid_chat_batch") from None


def _remember_origin_identity(conn: sqlite3.Connection, domain: ChatDomain, record: dict) -> None:
    """Keep both directions of an immutable prototype source-sequence identity."""
    identity = (record["originStreamId"], record["originSeq"], record["messageId"])
    prior = conn.execute(
        "SELECT origin_stream_id, origin_seq, message_id FROM chat_origin_identities WHERE domain=? "
        "AND ((origin_stream_id=? AND origin_seq=?) OR message_id=?)",
        (domain.key(), *identity),
    ).fetchall()
    if any(tuple(item) != identity for item in prior):
        raise IdentityConflict("origin_identity_conflict")
    conn.execute("INSERT OR IGNORE INTO chat_origin_identities VALUES (?, ?, ?, ?)", (domain.key(), *identity))


class ChatSyncLedger:
    """Atomic original + alias + pending registration, independent of models.

    Construction does not authorize an owner or enable sync. Authorization and
    verified binding remain the responsibility of the runtime adapter.
    """

    def __init__(self, path: Path):
        """Create only ledger tables; never initialize or migrate user memory."""
        self.path = path
        with closing(self._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS chat_sources (
                    domain TEXT NOT NULL,
                    database_id TEXT NOT NULL,
                    generation TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    next_seq INTEGER NOT NULL,
                    PRIMARY KEY (domain, database_id, generation)
                );
                CREATE TABLE IF NOT EXISTS chat_originals (
                    domain TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    original TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    sha256 TEXT NOT NULL,
                    PRIMARY KEY (domain, message_id),
                    UNIQUE (domain, locator)
                );
                CREATE TABLE IF NOT EXISTS chat_pending (
                    domain TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    queued_at_ms INTEGER,
                    PRIMARY KEY (domain, message_id),
                    FOREIGN KEY (domain, message_id)
                        REFERENCES chat_originals(domain, message_id)
                );
                CREATE TABLE IF NOT EXISTS chat_batches (
                    domain TEXT NOT NULL PRIMARY KEY,
                    batch_id TEXT NOT NULL UNIQUE,
                    sealed_bytes BLOB NOT NULL,
                    sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_lane_rotation (
                    domain TEXT PRIMARY KEY, new_batches INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS chat_identity_backfills (domain TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS chat_oversized (
                    domain TEXT NOT NULL, message_id TEXT NOT NULL, byte_length INTEGER NOT NULL,
                    PRIMARY KEY (domain, message_id));
                CREATE TABLE IF NOT EXISTS chat_downloaded (
                    domain TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    PRIMARY KEY (domain, message_id)
                );
                CREATE TABLE IF NOT EXISTS chat_tombstones (
                    domain TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    PRIMARY KEY (domain, message_id)
                );
                CREATE TABLE IF NOT EXISTS chat_cursors (
                    domain TEXT NOT NULL PRIMARY KEY,
                    cursor INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_deletions (
                    domain TEXT NOT NULL,
                    deletion_id TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    sha256 TEXT NOT NULL,
                    PRIMARY KEY (domain, deletion_id)
                );
                CREATE TABLE IF NOT EXISTS chat_recovery_deletion_pages (
                    domain TEXT NOT NULL, task_id TEXT NOT NULL, page_hash TEXT NOT NULL,
                    deletion_id TEXT NOT NULL UNIQUE, payload BLOB NOT NULL, acked INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(domain,task_id,page_hash));
                CREATE TABLE IF NOT EXISTS chat_origin_identities (
                    domain TEXT NOT NULL,
                    origin_stream_id TEXT NOT NULL,
                    origin_seq INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    PRIMARY KEY (domain, origin_stream_id, origin_seq),
                    UNIQUE (domain, message_id)
                );
                CREATE TABLE IF NOT EXISTS chat_deleted_originals (
                    domain TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    locator TEXT,
                    original_hash TEXT,
                    sha256 TEXT NOT NULL,
                    PRIMARY KEY (domain, message_id),
                    UNIQUE (domain, locator)
                );
            """)
            # Serialize the additive migration across concurrent runtime handles.
            # Existing pending rows intentionally retain NULL instead of "now".
            conn.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(chat_pending)")}
            if "queued_at_ms" not in columns:
                conn.execute("ALTER TABLE chat_pending ADD COLUMN queued_at_ms INTEGER")
            # Existing accepted outbox rows remain live-chat work. A history
            # lane is assigned only by an explicitly approved source transfer.
            if "queue_lane" not in columns:
                conn.execute("ALTER TABLE chat_pending ADD COLUMN queue_lane TEXT NOT NULL DEFAULT 'new'")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        """Give each operation its own connection and durable SQLite commits."""
        conn = store_connection(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = FULL")
        # Reclaimed current database pages must not retain readable deleted
        # text. Backups, snapshots and storage-medium erasure are separate work.
        conn.execute("PRAGMA secure_delete = ON")
        return conn

    def capture(
        self, domain: ChatDomain, source: SourceLocator, original: ChatOriginal,
        *, message_id: str | None = None, queued_at_ms: int | None | object = _CAPTURE_TIME_NOW,
        queue_lane: str = "new",
        proven_source_origin: tuple[str, int] | None = None,
    ) -> dict:
        """Capture one verified original; retry returns the same persisted ID.

        The caller must recover any gap between its source commit and capture
        by scanning verified source identities. This transaction alone does not
        establish atomicity with the existing time-index database.
        """
        # A verified new source may supply its backend-generated record UID so
        # the live bubble and later download keep one immutable message ID.
        if message_id is not None:
            _message_ids([message_id])
        if queue_lane not in ("new", "history"):
            raise ValueError("invalid_chat_queue_lane")
        if proven_source_origin is not None:
            # Only a producer's durable raw transaction may supply source order.
            # Validate it before aliases/retries; never infer it from timestamps.
            try:
                if not isinstance(proven_source_origin, tuple) or len(proven_source_origin) != 2:
                    raise ValueError
                _message_ids([proven_source_origin[0]])
                if type(proven_source_origin[1]) is not int or not 1 <= proven_source_origin[1] <= MAX_ORIGIN_SEQ:
                    raise ValueError
            except ValueError:
                raise ValueError("invalid_chat_source_origin") from None
        if queued_at_ms is not _CAPTURE_TIME_NOW and queued_at_ms is not None and (
            type(queued_at_ms) is not int or not 0 <= queued_at_ms <= 2 ** 63 - 1
        ):
            raise ValueError("invalid_chat_queue_time")
        key, locator, body = domain.key(), source.key(), _encode(original.fields())
        conn = self._connect()
        try:
            # Serialize identity lookup and sequence allocation across processes.
            conn.execute("BEGIN IMMEDIATE")
            retired = conn.execute(
                "SELECT message_id, original_hash FROM chat_deleted_originals WHERE domain=? AND locator=?",
                (key, locator),
            ).fetchone()
            if retired:
                if retired[1] != hashlib.sha256(body.encode("utf-8")).hexdigest():
                    raise IdentityConflict("source_content_conflict")
                if message_id is not None and retired[0] != message_id:
                    raise IdentityConflict("source_identity_conflict")
                raise ValueError("chat_original_deleted")
            if message_id is not None and conn.execute(
                "SELECT 1 FROM chat_tombstones WHERE domain=? AND message_id=?", (key, message_id),
            ).fetchone():
                raise ValueError("chat_original_deleted")
            prior = conn.execute(
                "SELECT original, payload FROM chat_originals WHERE domain=? AND locator=?",
                (key, locator),
            ).fetchone()
            if prior:
                # An existing source alias never creates another pending task.
                if prior[0] != body:
                    raise IdentityConflict("source_content_conflict")
                if message_id is not None and json.loads(prior[1])["messageId"] != message_id:
                    raise IdentityConflict("source_identity_conflict")
                if proven_source_origin is not None and (
                    json.loads(prior[1])["originStreamId"], json.loads(prior[1])["originSeq"]
                ) != proven_source_origin:
                    raise IdentityConflict("source_identity_conflict")
                return json.loads(prior[1])
            if proven_source_origin is None:
                # Existing authorized source rows retain their original ledger
                # allocation; an additive migration never rewrites wire identity.
                conn.execute("INSERT OR IGNORE INTO chat_sources VALUES (?, ?, ?, ?, 1)",
                             (key, source.database_id, source.generation, uuid4().hex))
                stream, seq = conn.execute(
                    "SELECT stream_id, next_seq FROM chat_sources WHERE domain=? AND database_id=? AND generation=?",
                    (key, source.database_id, source.generation),
                ).fetchone()
            else:
                stream, seq = proven_source_origin
            # Never emit a sequence that the mobile JavaScript runtime rounds.
            # Exhaustion requires a new verified source stream, not wraparound.
            if seq > MAX_ORIGIN_SEQ:
                raise ValueError("source_sequence_exhausted")
            payload = {
                "schemaVersion": 1,
                "ownerId": domain.owner_id,
                "characterSyncId": domain.character_uid,
                "channel": domain.channel,
                "scope": domain.scope,
                "messageId": message_id if message_id is not None else uuid4().hex,
                "originStreamId": stream,
                "originSeq": seq,
                **original.fields(),
            }
            encoded = encode_chat_original(payload)
            # Prototype identity registration shares capture's original commit;
            # it does not alter source selection or sequence allocation.
            _remember_origin_identity(conn, domain, payload)
            # Original bytes, source alias, pending record and sequence advance
            # commit together. Closing an uncommitted connection rolls them back.
            conn.execute(
                "INSERT INTO chat_originals VALUES (?, ?, ?, ?, ?, ?)",
                (key, payload["messageId"], locator, body, encoded, hashlib.sha256(encoded).hexdigest()),
            )
            # Arrival time belongs to the atomic outbox commit, never to mutable
            # display time. Source-WAL recovery supplies its original saved clock.
            arrived = time.time_ns() // 1_000_000 if queued_at_ms is _CAPTURE_TIME_NOW else queued_at_ms
            conn.execute("INSERT INTO chat_pending (domain,message_id,queued_at_ms,queue_lane) VALUES (?, ?, ?, ?)",
                         (key, payload["messageId"], arrived, queue_lane))
            if proven_source_origin is None:
                conn.execute("UPDATE chat_sources SET next_seq=next_seq+1 WHERE domain=? AND database_id=? AND generation=?",
                             (key, source.database_id, source.generation))
            conn.commit()
            return payload
        finally:
            conn.close()

    def pending(self, domain: ChatDomain, *, limit: int = 200) -> list[tuple[bytes, str]]:
        """Return bounded fixed payload bytes and hashes, without marking ack."""
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_page_size")
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT o.payload, o.sha256 FROM chat_originals o JOIN chat_pending p "
                "ON o.domain=p.domain AND o.message_id=p.message_id "
                "WHERE o.domain=? AND NOT EXISTS (SELECT 1 FROM chat_tombstones t "
                "WHERE t.domain=o.domain AND t.message_id=o.message_id) ORDER BY o.rowid LIMIT ?",
                (domain.key(), limit),
            ).fetchall()
        finally:
            conn.close()

    def seal_batch(
        self, domain: ChatDomain, *, byte_budget: int, limit: int = 200,
    ) -> SealedChatBatch | None:
        """Freeze one pending prefix atomically, or return the existing fixed batch.

        The budget counts canonical original UTF-8 bytes only, excluding this
        internal envelope. It is not a compressed/request/fragment size limit.
        Pending rows remain intact until an exact prototype acknowledgement.
        A previously sealed snapshot is never rewritten by later tombstones.
        """
        if type(byte_budget) is not int or byte_budget <= 0:
            raise ValueError("invalid_batch_byte_budget")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid_page_size")
        conn = self._connect()
        try:
            # This writer lock prevents concurrent sealers from choosing twice
            # and excludes captures until the complete snapshot commits.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT batch_id, sealed_bytes, sha256 FROM chat_batches WHERE domain=?",
                (domain.key(),),
            ).fetchone()
            if row:
                return _read_sealed_batch(domain, row)
            # Persist a small bounded rotation at sealing, where retries retain
            # their old batch. New chat leads; every fourth opportunity grants
            # an eligible history prefix even under continuous new arrivals.
            lanes = {item[0] for item in conn.execute("SELECT DISTINCT queue_lane FROM chat_pending WHERE domain=?", (domain.key(),))}
            streak_row = conn.execute("SELECT new_batches FROM chat_lane_rotation WHERE domain=?", (domain.key(),)).fetchone()
            streak = streak_row[0] if streak_row else 0
            lane = "history" if "history" in lanes and ("new" not in lanes or streak >= 3) else "new"
            # Fetch at most 200 small metadata rows before reading original
            # bytes; an unconsumed SQLite cursor can retain a lock on errors.
            candidates = conn.execute(
                "SELECT o.message_id, length(o.payload), o.sha256 FROM chat_originals o "
                "JOIN chat_pending p ON o.domain=p.domain AND o.message_id=p.message_id "
                "WHERE o.domain=? AND p.queue_lane=? AND NOT EXISTS (SELECT 1 FROM chat_tombstones t "
                "WHERE t.domain=o.domain AND t.message_id=o.message_id) ORDER BY o.rowid LIMIT ?",
                (domain.key(), lane, limit),
            ).fetchall()
            members, used_bytes, oversized = [], 0, []
            for message_id, byte_length, original_digest in candidates:
                if byte_length > MAX_SYNC_ORIGINAL_BYTES:
                    oversized.append((domain.key(), message_id, byte_length))
                    continue
                if used_bytes + byte_length > byte_budget:
                    if not members:
                        raise OriginalNeedsFragmentation(message_id, byte_length)
                    # Preserve a contiguous prefix; never skip a large original.
                    break
                payload = conn.execute(
                    "SELECT payload FROM chat_originals WHERE domain=? AND message_id=?",
                    (domain.key(), message_id),
                ).fetchone()[0]
                _validate_batch_member(domain, message_id, payload, original_digest)
                members.append([message_id, payload.decode("utf-8"), original_digest])
                used_bytes += byte_length
            if oversized:
                # Keep the local original and its identity; only its upload task ends.
                conn.executemany("INSERT OR IGNORE INTO chat_oversized VALUES (?, ?, ?)", oversized)
                conn.executemany("DELETE FROM chat_pending WHERE domain=? AND message_id=?",
                                 [item[:2] for item in oversized])
            if not members:
                if oversized:
                    conn.commit()
                return None
            batch_id = uuid4().hex
            sealed_bytes = _encode({
                "batchId": batch_id, "domain": json.loads(domain.key()), "members": members,
            }).encode("utf-8")
            digest = hashlib.sha256(sealed_bytes).hexdigest()
            batch = _read_sealed_batch(domain, (batch_id, sealed_bytes, digest))
            conn.execute(
                "INSERT INTO chat_batches VALUES (?, ?, ?, ?)",
                (domain.key(), batch_id, sealed_bytes, digest),
            )
            conn.execute("INSERT INTO chat_lane_rotation VALUES (?,?) ON CONFLICT(domain) DO UPDATE SET new_batches=excluded.new_batches",
                         (domain.key(), 0 if lane == "history" else min(streak + 1, 3)))
            conn.commit()
            return batch
        finally:
            # A failed insert, validation or process interruption leaves no
            # completed snapshot and never consumes pending originals.
            conn.close()

    def queue_deletion(self, domain: ChatDomain, message_ids: list[str]) -> SealedChatDeletion:
        """Hide explicit IDs and persist their immutable deletion request atomically.

        Unknown IDs are allowed so a deletion can reach the server before an
        old upload. A clear operation must pass a fixed known-ID snapshot here.
        """
        ids = _message_ids(message_ids)
        if len(ids) > 200:
            raise ValueError("invalid_message_ids")
        return self.queue_deletions(domain, message_ids)[0]

    def queue_deletions(self, domain: ChatDomain, message_ids: list[str]) -> tuple[SealedChatDeletion, ...]:
        """Hide one fixed boundary and seal <=200-ID requests in one transaction.

        Large local clears retain every marker atomically. Restart/network retry
        reads the persisted IDs and bytes rather than recomputing the boundary.
        """
        ids = _message_ids(message_ids)
        requests = []
        for offset in range(0, len(ids), 200):
            members = ids[offset:offset + 200]
            payload = _encode({"messageIds": list(members)}).encode("utf-8")
            requests.append(SealedChatDeletion(uuid4().hex, members, payload))
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany("INSERT OR IGNORE INTO chat_tombstones VALUES (?, ?)",
                             [(domain.key(), item) for item in ids])
            conn.executemany("INSERT INTO chat_deletions VALUES (?, ?, ?, ?)", [
                (domain.key(), item.deletion_id, item.payload, hashlib.sha256(item.payload).hexdigest())
                for item in requests
            ])
            conn.commit()
        return tuple(requests)

    def queue_recovery_deletions(self, domain: ChatDomain, task_id: str, message_ids: list[str]) -> None:
        """Retain a renewed page's fixed request and ACK across the source progress gap."""
        _message_ids([task_id])
        ids = _message_ids(message_ids)
        if len(ids) > 200:
            raise ValueError("invalid_message_ids")
        payload = _encode({"messageIds": list(ids)}).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO chat_recovery_deletion_pages VALUES (?,?,?,?,?,0)",
                         (domain.key(), task_id, digest, uuid4().hex, payload))
            row = conn.execute("SELECT deletion_id,payload,acked FROM chat_recovery_deletion_pages "
                               "WHERE domain=? AND task_id=? AND page_hash=?", (domain.key(), task_id, digest)).fetchone()
            if row[1] != payload:
                raise IdentityConflict("original_content_conflict")
            conn.executemany("INSERT OR IGNORE INTO chat_tombstones VALUES (?,?)", [(domain.key(), value) for value in ids])
            if not row[2]:
                conn.execute("INSERT OR IGNORE INTO chat_deletions VALUES (?,?,?,?)", (domain.key(), row[0], payload, digest))

    def deleted_message_ids(self, domain: ChatDomain, message_ids: list[str] | None = None) -> list[str]:
        """Expose permanent IDs, optionally checking only a bounded recovery page."""
        ids = _message_ids(message_ids, allow_empty=True) if message_ids is not None else None
        if ids == ():
            return []
        with closing(self._connect()) as conn:
            if ids is not None:
                return [row[0] for row in conn.execute(
                    "SELECT message_id FROM chat_tombstones WHERE domain=? AND message_id IN ("
                    + ",".join("?" for _ in ids) + ")", (domain.key(), *ids),
                )]
            return [row[0] for row in conn.execute(
                "SELECT message_id FROM chat_tombstones WHERE domain=? ORDER BY rowid", (domain.key(),),
            )]

    def purge_deleted_originals(self, domain: ChatDomain) -> None:
        """Reclaim current raw copies after durable tombstones, retaining identity.

        A sealed body containing a deleted record is cancelled rather than
        rewritten. Its other members remain pending for a new fixed snapshot.
        This repeatable cleanup is for the dedicated sync ledger only.
        """
        key = domain.key()
        with closing(self._connect()) as conn:
            dirty = any(conn.execute(
                f"SELECT 1 FROM {table} p JOIN chat_tombstones t ON t.domain=p.domain AND t.message_id=p.message_id "
                "WHERE p.domain=? LIMIT 1", (key,),
            ).fetchone() for table in ('chat_pending', 'chat_originals', 'chat_downloaded'))
            if not dirty and not conn.execute(
                "SELECT 1 FROM chat_batches WHERE domain=? AND EXISTS(SELECT 1 FROM chat_tombstones WHERE domain=?)", (key, key),
            ).fetchone():
                return
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT o.message_id,o.locator,o.original,o.payload,o.sha256 FROM chat_originals o "
                "JOIN chat_tombstones t ON t.domain=o.domain AND t.message_id=o.message_id WHERE o.domain=? "
                "UNION ALL SELECT d.message_id,NULL,NULL,d.payload,NULL FROM chat_downloaded d "
                "JOIN chat_tombstones t ON t.domain=d.domain AND t.message_id=d.message_id WHERE d.domain=?",
                (key, key),
            ).fetchall()
            for message_id, locator, original, payload, digest in rows:
                digest = digest or hashlib.sha256(payload).hexdigest()
                _validate_batch_member(domain, message_id, payload, digest)
                _remember_origin_identity(conn, domain, decode_chat_original(payload))
                conn.execute("INSERT OR IGNORE INTO chat_deleted_originals VALUES (?, ?, ?, ?, ?)", (
                    key, message_id, locator,
                    hashlib.sha256(original.encode("utf-8")).hexdigest() if original is not None else None,
                    digest,
                ))
            sealed = conn.execute(
                "SELECT batch_id,sealed_bytes,sha256 FROM chat_batches WHERE domain=?", (key,),
            ).fetchone()
            if sealed:
                batch = _read_sealed_batch(domain, sealed)
                deleted = {row[0] for row in conn.execute("SELECT message_id FROM chat_tombstones WHERE domain=?", (key,))}
                if any(member[0] in deleted for member in batch.members):
                    conn.execute("DELETE FROM chat_batches WHERE domain=?", (key,))
            for table in ("chat_pending", "chat_originals", "chat_downloaded", "chat_oversized"):
                conn.execute(f"DELETE FROM {table} WHERE domain=? AND message_id IN "
                             "(SELECT message_id FROM chat_tombstones WHERE domain=?)", (key, key))
            conn.commit()

    def pending_deletions(self, domain: ChatDomain) -> list[SealedChatDeletion]:
        """Return the finite current deletion snapshot in insertion order."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT deletion_id, payload, sha256 FROM chat_deletions WHERE domain=? ORDER BY rowid",
                (domain.key(),),
            ).fetchall()
        return [_read_deletion(row) for row in rows]

    def ack_deletion(self, domain: ChatDomain, receipt: dict) -> None:
        """Consume only an exactly acknowledged request; retain every tombstone."""
        deletion_id, ids = _receipt(receipt, "deletionId")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT deletion_id, payload, sha256 FROM chat_deletions WHERE domain=? AND deletion_id=?",
                (domain.key(), deletion_id),
            ).fetchone()
            if row is None or _read_deletion(row).message_ids != ids:
                raise ValueError("invalid_chat_receipt")
            conn.execute("DELETE FROM chat_deletions WHERE domain=? AND deletion_id=?", (domain.key(), deletion_id))
            conn.execute("UPDATE chat_recovery_deletion_pages SET acked=1 WHERE domain=? AND deletion_id=?", (domain.key(), deletion_id))
            conn.commit()

    def ack_batch(self, domain: ChatDomain, receipt: dict) -> None:
        """Acknowledge the fixed members only, leaving later captures pending."""
        batch_id, ids = _receipt(receipt, "batchId")
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT batch_id, sealed_bytes, sha256 FROM chat_batches WHERE domain=?", (domain.key(),),
            ).fetchone()
            batch = _read_sealed_batch(domain, row) if row is not None else None
            if batch is None or batch.batch_id != batch_id or tuple(item[0] for item in batch.members) != ids:
                raise ValueError("invalid_chat_receipt")
            conn.executemany("DELETE FROM chat_pending WHERE domain=? AND message_id=?",
                             [(domain.key(), item) for item in ids])
            conn.execute("DELETE FROM chat_batches WHERE domain=? AND batch_id=?", (domain.key(), batch_id))
            conn.commit()

    def cursor(self, domain: ChatDomain) -> int:
        """Return only the cursor of a completely merged downloaded page."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT cursor FROM chat_cursors WHERE domain=?", (domain.key(),)).fetchone()
        return row[0] if row is not None else 0

    def stats(self, domain: ChatDomain) -> dict:
        """Count durable remaining work without claiming one batch finishes sync."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            originals = conn.execute(
                "SELECT COUNT(*) FROM chat_pending p WHERE p.domain=? AND NOT EXISTS "
                "(SELECT 1 FROM chat_tombstones t WHERE t.domain=p.domain AND t.message_id=p.message_id)",
                (domain.key(),),
            ).fetchone()[0]
            deletions = conn.execute("SELECT COUNT(*) FROM chat_deletions WHERE domain=?", (domain.key(),)).fetchone()[0]
            cursor = conn.execute("SELECT cursor FROM chat_cursors WHERE domain=?", (domain.key(),)).fetchone()
            sealed = conn.execute("SELECT 1 FROM chat_batches WHERE domain=?", (domain.key(),)).fetchone()
            oversized = conn.execute(
                "SELECT COUNT(*) FROM chat_oversized s WHERE s.domain=? AND NOT EXISTS "
                "(SELECT 1 FROM chat_tombstones t WHERE t.domain=s.domain AND t.message_id=s.message_id)",
                (domain.key(),),
            ).fetchone()[0]
            return {"pendingOriginals": originals, "pendingDeletions": deletions, "cursor": cursor[0] if cursor else 0,
                    "hasSealedBatch": sealed is not None, "localOnlyOriginals": oversized}

    def queue_state(self, domain: ChatDomain) -> dict:
        """Read all live pending arrivals and raw UTF-8 sizes in one snapshot.

        Unknown legacy arrival times remain NULL and are reported separately;
        the scheduler decides eligibility without backfilling an invented clock.
        """
        key = domain.key()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN")
            count, byte_length, oldest, latest, legacy = conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(o.payload)),0),MIN(p.queued_at_ms),"
                "MAX(p.queued_at_ms),COALESCE(SUM(p.queued_at_ms IS NULL),0) "
                "FROM chat_pending p JOIN chat_originals o "
                "ON o.domain=p.domain AND o.message_id=p.message_id "
                "WHERE p.domain=? AND NOT EXISTS (SELECT 1 FROM chat_tombstones t "
                "WHERE t.domain=p.domain AND t.message_id=p.message_id)", (key,),
            ).fetchone()
            deletions = conn.execute("SELECT COUNT(*) FROM chat_deletions WHERE domain=?", (key,)).fetchone()[0]
            sealed = conn.execute("SELECT 1 FROM chat_batches WHERE domain=?", (key,)).fetchone()
            return {
                "pendingOriginals": count, "pendingBytes": byte_length,
                "oldestEnqueuedAt": oldest, "lastEnqueuedAt": latest,
                "legacyPending": bool(legacy), "hasSealedBatch": sealed is not None,
                "pendingDeletions": deletions,
            }

    def apply_changes(self, domain: ChatDomain, page: dict, *, after: int) -> None:
        """Merge one complete prototype page and its cursor in one transaction.

        Downloads never call capture, allocate a source alias, or register
        pending uploads. Tombstones survive old pages and dominate visibility.
        """
        if (
            type(after) is not int or after < 0 or not isinstance(page, dict)
            or set(page) != {"cursor", "hasMore", "originals", "deletedMessageIds"}
            or type(page["cursor"]) is not int or page["cursor"] < after
            or type(page["hasMore"]) is not bool
            or (page["hasMore"] and page["cursor"] <= after)
            or not isinstance(page["originals"], list)
        ):
            raise ValueError("invalid_chat_changes")
        deleted_ids = _message_ids(page["deletedMessageIds"], allow_empty=True)
        originals, origins, records = {}, {}, []
        for original in page["originals"]:
            try:
                if not isinstance(original, str):
                    raise ValueError
                payload = original.encode("utf-8")
                record = decode_chat_original(payload)
                message_id = record["messageId"]
                _validate_batch_member(domain, message_id, payload, hashlib.sha256(payload).hexdigest())
            except (TypeError, ValueError, RecursionError):
                raise ValueError("invalid_chat_changes") from None
            if message_id in originals and originals[message_id] != payload:
                raise IdentityConflict("original_content_conflict")
            origin = (record["originStreamId"], record["originSeq"])
            if origin in origins and origins[origin] != message_id:
                raise IdentityConflict("origin_identity_conflict")
            originals[message_id] = payload
            origins[origin] = message_id
            records.append(record)
        key = domain.key()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT cursor FROM chat_cursors WHERE domain=?", (key,)).fetchone()
            if (row[0] if row is not None else 0) != after:
                raise ValueError("chat_cursor_conflict")
            # Check every immutable ID before applying deletions or advancing.
            for message_id, payload in originals.items():
                retired = conn.execute(
                    "SELECT sha256 FROM chat_deleted_originals WHERE domain=? AND message_id=?", (key, message_id),
                ).fetchone()
                if retired and retired[0] != hashlib.sha256(payload).hexdigest():
                    raise IdentityConflict("original_content_conflict")
                prior = conn.execute(
                    "SELECT payload FROM chat_originals WHERE domain=? AND message_id=? UNION ALL "
                    "SELECT payload FROM chat_downloaded WHERE domain=? AND message_id=?",
                    (key, message_id, key, message_id),
                ).fetchall()
                if any(item[0] != payload for item in prior):
                    raise IdentityConflict("original_content_conflict")
            # Only this feature's raw-original tables are backfilled. Verify
            # existing records before registering immutable identities; never
            # scan or migrate the old memory/time-index projection databases.
            # Every later write registers its own identity, so this runs once.
            if conn.execute("SELECT 1 FROM chat_identity_backfills WHERE domain=?", (key,)).fetchone() is None:
                prior_records = conn.execute(
                    "SELECT message_id, payload, sha256 FROM chat_originals WHERE domain=? UNION ALL "
                    "SELECT message_id, payload, NULL FROM chat_downloaded WHERE domain=?", (key, key),
                ).fetchall()
                for message_id, payload, digest in prior_records:
                    _validate_batch_member(domain, message_id, payload,
                                           digest if digest is not None else hashlib.sha256(payload).hexdigest())
                    _remember_origin_identity(conn, domain, decode_chat_original(payload))
                conn.execute("INSERT INTO chat_identity_backfills VALUES (?)", (key,))
            # Remember identities even when their originals are hidden below.
            # Deletion never removes this mapping, so an old source sequence
            # cannot return under a fresh ID after its body was discarded.
            for record in records:
                _remember_origin_identity(conn, domain, record)
            conn.executemany("INSERT OR IGNORE INTO chat_tombstones VALUES (?, ?)",
                             [(key, item) for item in deleted_ids])
            for message_id, payload in originals.items():
                hidden = conn.execute(
                    "SELECT 1 FROM chat_tombstones WHERE domain=? AND message_id=?", (key, message_id),
                ).fetchone()
                if hidden is None:
                    conn.execute("INSERT OR IGNORE INTO chat_downloaded VALUES (?, ?, ?)", (key, message_id, payload))
            conn.execute(
                "INSERT INTO chat_cursors VALUES (?, ?) ON CONFLICT(domain) DO UPDATE SET cursor=excluded.cursor",
                (key, page["cursor"]),
            )
            conn.commit()

    def visible_originals(self, domain: ChatDomain) -> list[bytes]:
        """Expose the stored local/downloaded union without reviving tombstones."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT message_id, payload FROM chat_originals WHERE domain=? UNION "
                "SELECT message_id, payload FROM chat_downloaded WHERE domain=?", (domain.key(), domain.key()),
            ).fetchall()
            hidden = {row[0] for row in conn.execute(
                "SELECT message_id FROM chat_tombstones WHERE domain=?", (domain.key(),),
            )}
        return [payload for message_id, payload in rows if message_id not in hidden]
