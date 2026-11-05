from contextlib import closing
import json
import sqlite3

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401
from tests.chat_sync_legacy_ledger import insert_legacy_original

from services.chat_sync.ledger import ChatDomain, ChatOriginal, ChatSyncLedger


DOMAIN = ChatDomain("00000000-0000-4000-8000-000000000001", "a" * 32)

# Tables only earlier releases created. Their queue table also carried two
# extra columns behind a foreign key to chat_originals.
RETIRED_TABLES = ("chat_sources", "chat_lane_rotation", "chat_identity_backfills", "chat_oversized", "chat_cursors",
                  "chat_deleted_originals")
EARLIER_SCHEMA = """
    CREATE TABLE chat_sources (domain TEXT NOT NULL, database_id TEXT NOT NULL, generation TEXT NOT NULL,
        stream_id TEXT NOT NULL, next_seq INTEGER NOT NULL, PRIMARY KEY (domain, database_id, generation));
    CREATE TABLE chat_lane_rotation (domain TEXT PRIMARY KEY, new_batches INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE chat_identity_backfills (domain TEXT PRIMARY KEY);
    CREATE TABLE chat_oversized (domain TEXT NOT NULL, message_id TEXT NOT NULL, byte_length INTEGER NOT NULL,
        PRIMARY KEY (domain, message_id));
    CREATE TABLE chat_cursors (domain TEXT NOT NULL PRIMARY KEY, cursor INTEGER NOT NULL);
    CREATE TABLE chat_deleted_originals (domain TEXT NOT NULL, message_id TEXT NOT NULL, locator TEXT,
        original_hash TEXT, sha256 TEXT NOT NULL, PRIMARY KEY (domain, message_id), UNIQUE (domain, locator));
    DROP TABLE chat_pending;
    CREATE TABLE chat_pending (domain TEXT NOT NULL, message_id TEXT NOT NULL, queued_at_ms INTEGER,
        queue_lane TEXT NOT NULL DEFAULT 'new', PRIMARY KEY (domain, message_id),
        FOREIGN KEY (domain, message_id) REFERENCES chat_originals(domain, message_id));
"""


def _add_earlier_release_rows(conn):
    key = DOMAIN.key()
    conn.execute("INSERT INTO chat_sources VALUES (?,?,?,?,?)", (key, "db", "generation", "f" * 32, 3))
    conn.execute("INSERT INTO chat_lane_rotation VALUES (?,?)", (key, 1))
    conn.execute("INSERT INTO chat_identity_backfills VALUES (?)", (key,))
    conn.execute("INSERT INTO chat_oversized VALUES (?,?,?)", (key, "e" * 32, 9))
    conn.execute("INSERT INTO chat_cursors VALUES (?,?)", (key, 7))
    conn.execute("INSERT INTO chat_deleted_originals VALUES (?,?,?,?,?)",
                 (key, "d" * 32, '["db","generation","0"]', "0" * 64, "1" * 64))


@pytest.mark.parametrize("earlier_release", [False, True])
def test_deleted_bodies_are_erased_on_fresh_and_earlier_ledgers(tmp_path, earlier_release):
    """A fresh ledger has no retired tables; an earlier one keeps them and its live paths still work."""
    path = tmp_path / "sync.db"
    ChatSyncLedger(path)
    if earlier_release:
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(EARLIER_SCHEMA)
            _add_earlier_release_rows(conn)
    ledger = ChatSyncLedger(path)
    deleted = insert_legacy_original(path, DOMAIN, ChatOriginal("user", "deleted"), ("db", "generation", "1"))
    kept = insert_legacy_original(path, DOMAIN, ChatOriginal("assistant", "kept"), ("db", "generation", "2"))
    ledger.queue_deletion(DOMAIN, [deleted["messageId"]])
    # The legacy queue row references its original under enforced foreign keys.
    ledger.purge_deleted_originals(DOMAIN)
    assert [json.loads(raw)["messageId"] for raw in ledger.visible_originals(DOMAIN)] == [kept["messageId"]]
    assert ledger.deleted_message_ids(DOMAIN) == [deleted["messageId"]]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT message_id FROM chat_originals").fetchall() == [(kept["messageId"],)]
        assert conn.execute("SELECT message_id FROM chat_pending").fetchall() == [(kept["messageId"],)]
        # The deleted message keeps only its immutable identity.
        assert {row[0] for row in conn.execute("SELECT message_id FROM chat_origin_identities")} == {
            deleted["messageId"], kept["messageId"]}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if earlier_release:
            assert all(conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (1,) for table in RETIRED_TABLES)
        else:
            assert tables.isdisjoint(RETIRED_TABLES)


@pytest.mark.parametrize("other", [ChatDomain("00000000-0000-4000-8000-000000000002", "a" * 32),
                                   ChatDomain(DOMAIN.owner_id, "b" * 32)])
def test_domains_are_isolated(tmp_path, other):
    """Another owner or role never sees, hides or erases this domain's originals."""
    path = tmp_path / "sync.db"
    ledger = ChatSyncLedger(path)
    first = insert_legacy_original(path, DOMAIN, ChatOriginal("user", "A"), ("db", "generation", "1"))
    assert ledger.visible_originals(other) == []
    second = insert_legacy_original(path, other, ChatOriginal("user", "B"), ("db", "generation", "1"))
    assert [json.loads(raw)["messageId"] for raw in ledger.visible_originals(DOMAIN)] == [first["messageId"]]
    assert [json.loads(raw)["messageId"] for raw in ledger.visible_originals(other)] == [second["messageId"]]
    # The other domain deletes its own original and an ID that only this domain stores.
    ledger.queue_deletion(other, [first["messageId"], second["messageId"]])
    ledger.purge_deleted_originals(other)
    assert [json.loads(raw)["messageId"] for raw in ledger.visible_originals(DOMAIN)] == [first["messageId"]]
    assert ledger.visible_originals(other) == []
    assert ledger.deleted_message_ids(DOMAIN) == []
    assert sorted(ledger.deleted_message_ids(other)) == sorted([first["messageId"], second["messageId"]])
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT message_id FROM chat_originals").fetchall() == [(first["messageId"],)]


@pytest.mark.parametrize("role", ["system", "human", "tool"])
def test_non_chat_roles_rejected(role):
    with pytest.raises(ValueError, match="invalid_chat_original"):
        ChatOriginal(role, "memo")


@pytest.mark.parametrize("channel,scope", [("memory", "private-chat"), ("chat", "theater"), ("model", "private-chat")])
def test_unsupported_domains_rejected(channel, scope):
    with pytest.raises(ValueError, match="unsupported_sync_domain"):
        ChatDomain(DOMAIN.owner_id, "a" * 32, channel, scope)
