"""Local chat-sync identity, tombstone and recovery-record store.

The opt-in runtime adapter supplies the fixed account binding. The ledger path
must be outside Steam-restored memory directories. Async callers must offload
these synchronous SQLite operations with asyncio.to_thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from services.chat_sync.writer import store_connection
from uuid import uuid4

from services.chat_sync.protocol import decode_chat_original, validate_sync_domain


class IdentityConflict(ValueError):
    """A stable identity was presented with different immutable content."""


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


def _message_ids(value: object) -> tuple[str, ...]:
    """Validate explicit IDs without expanding a clear operation by device time."""
    if not isinstance(value, list) or not value:
        raise ValueError("invalid_message_ids")
    if any(
        not isinstance(item, str) or len(item) != 32
        or any(char not in "0123456789abcdef" for char in item)
        for item in value
    ) or len(set(value)) != len(value):
        raise ValueError("invalid_message_ids")
    return tuple(value)


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
    """Encode SQLite domain keys and fixed deletion/batch bodies canonically."""
    # Chat original payloads use encode_chat_original instead.
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
    """Local originals, tombstones and recovery records, independent of models.

    Construction does not authorize an owner or enable sync. Authorization and
    verified binding remain the responsibility of the runtime adapter.
    """

    def __init__(self, path: Path):
        """Create only ledger tables; never initialize or migrate user memory."""
        self.path = path
        with closing(self._connect()) as conn:
            # Databases written by earlier releases may also hold retired tables
            # (chat_sources, chat_cursors, chat_oversized, ...). Their rows carry
            # no chat text, nothing reads them, and they are left in place.
            # chat_pending only holds rows queued by earlier releases; nothing
            # adds to it, but its rows are erased together with their originals.
            conn.executescript("""
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
            """)

    def _connect(self) -> sqlite3.Connection:
        """Give each operation its own connection and durable SQLite commits."""
        conn = store_connection(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = FULL")
        # Reclaimed current database pages must not retain readable deleted
        # text. Backups, snapshots and storage-medium erasure are separate work.
        conn.execute("PRAGMA secure_delete = ON")
        return conn

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

    def deleted_message_ids(self, domain: ChatDomain) -> list[str]:
        """Expose permanent IDs in the order their tombstones were written."""
        with closing(self._connect()) as conn:
            return [row[0] for row in conn.execute(
                "SELECT message_id FROM chat_tombstones WHERE domain=? ORDER BY rowid", (domain.key(),),
            )]

    def purge_deleted_originals(self, domain: ChatDomain) -> None:
        """Reclaim current raw copies after durable tombstones, retaining identity.

        A legacy sealed body containing a deleted record is dropped rather
        than rewritten; nothing seals new bodies.
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
                "SELECT o.message_id,o.payload,o.sha256 FROM chat_originals o "
                "JOIN chat_tombstones t ON t.domain=o.domain AND t.message_id=o.message_id WHERE o.domain=? "
                "UNION ALL SELECT d.message_id,d.payload,NULL FROM chat_downloaded d "
                "JOIN chat_tombstones t ON t.domain=d.domain AND t.message_id=d.message_id WHERE d.domain=?",
                (key, key),
            ).fetchall()
            for message_id, payload, digest in rows:
                digest = digest or hashlib.sha256(payload).hexdigest()
                _validate_batch_member(domain, message_id, payload, digest)
                _remember_origin_identity(conn, domain, decode_chat_original(payload))
            sealed = conn.execute(
                "SELECT batch_id,sealed_bytes,sha256 FROM chat_batches WHERE domain=?", (key,),
            ).fetchone()
            if sealed:
                batch = _read_sealed_batch(domain, sealed)
                deleted = {row[0] for row in conn.execute("SELECT message_id FROM chat_tombstones WHERE domain=?", (key,))}
                if any(member[0] in deleted for member in batch.members):
                    conn.execute("DELETE FROM chat_batches WHERE domain=?", (key,))
            # Legacy chat_pending rows reference chat_originals; erase them first.
            for table in ("chat_pending", "chat_originals", "chat_downloaded"):
                conn.execute(f"DELETE FROM {table} WHERE domain=? AND message_id IN "
                             "(SELECT message_id FROM chat_tombstones WHERE domain=?)", (key, key))
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
