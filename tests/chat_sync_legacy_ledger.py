"""Recreate chat-sync ledger rows that only earlier releases wrote.

No production path adds local originals to the ledger any more, yet databases
written by earlier releases still hold them and every live reader must handle
them. Tests insert the exact rows such a release committed for one original:
its fixed payload and source alias, its legacy queue entry and its origin
identity.
"""

from contextlib import closing
import hashlib
import json
from uuid import uuid4

from services.chat_sync.protocol import encode_chat_original
from services.chat_sync.writer import store_connection


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def insert_legacy_original(path, domain, original, locator, *, message_id=None, origin=None):
    """Insert one legacy local original; the same locator returns the stored payload again."""
    key, alias, body = domain.key(), _encode(list(locator)), _encode(original.fields())
    with closing(store_connection(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute("SELECT original, payload FROM chat_originals WHERE domain=? AND locator=?",
                             (key, alias)).fetchone()
        if prior:
            assert prior[0] == body
            return json.loads(prior[1])
        stream, seq = origin if origin is not None else (uuid4().hex, 1)
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
        conn.execute("INSERT INTO chat_origin_identities VALUES (?, ?, ?, ?)",
                     (key, stream, seq, payload["messageId"]))
        conn.execute("INSERT INTO chat_originals VALUES (?, ?, ?, ?, ?, ?)",
                     (key, payload["messageId"], alias, body, encoded, hashlib.sha256(encoded).hexdigest()))
        conn.execute("INSERT INTO chat_pending (domain, message_id) VALUES (?, ?)", (key, payload["messageId"]))
        return payload
