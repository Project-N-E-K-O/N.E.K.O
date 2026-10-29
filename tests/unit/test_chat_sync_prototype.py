"""Disposable-database coverage for the v0 recovery client and local deletion markers, without runtime chat."""

import asyncio
from contextlib import closing
from dataclasses import FrozenInstanceError
import json
import sqlite3

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from services.chat_sync.ledger import ChatDomain, ChatOriginal, ChatSyncLedger, SourceLocator
from services.chat_sync.protocol import decode_chat_original
from services.chat_sync.prototype import PrototypeChatSyncClient


def test_recovery_client_checks_exact_coverage_and_fixed_deletion_fence(tmp_path):
    """Copied receipts cannot stand in for an authenticated metadata lookup."""
    async def scenario():
        ledger = ChatSyncLedger(tmp_path / "recovery.db")
        records = [{"messageId": "b" * 32, "sha256": "a" * 64}]
        requests = []

        async def request(method, path, body):
            requests.append((method, path, json.loads(body) if body else None))
            domain = {"ownerId": DOMAIN.owner_id, "characterSyncId": DOMAIN.character_uid}
            if method == "POST":
                return {**domain, "cursor": 7, "results": [{"messageId": "b" * 32, "state": "missing"}]}
            return {**domain, "through": 7, "cursor": 7, "hasMore": False, "deletedMessageIds": ["b" * 32]}

        client = PrototypeChatSyncClient(ledger, DOMAIN, request)
        check = await client.check_recovery(records)
        assert check["results"][0]["state"] == "missing"
        page = await client.recovery_deletions(after=2, through=7)
        assert page["deletedMessageIds"] == ["b" * 32]
        assert requests[0][2] == {"records": records}
        assert requests[1][1].endswith("/recovery/deletions?after=2&limit=200&through=7")
        with closing(sqlite3.connect(ledger.path)) as conn:
            assert conn.execute("SELECT count(*) FROM chat_cursors").fetchone() == (0,)
        assert ledger.deleted_message_ids(DOMAIN) == []
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"ownerId": "00000000-0000-4000-8000-000000000002"}, {"characterSyncId": "b" * 32},
    {"cursor": True}, {"results": []}, {"results": [{"messageId": "c" * 32, "state": "received"}]},
    {"results": [{"messageId": "b" * 32, "state": "accepted"}]}, {"extra": True},
])
def test_recovery_client_rejects_unbound_or_incomplete_coverage(tmp_path, change):
    """Validate the complete owner/role/manifest before exposing any decision."""
    async def scenario():
        async def request(*_args):
            return {"ownerId": DOMAIN.owner_id, "characterSyncId": DOMAIN.character_uid,
                    "cursor": 7, "results": [{"messageId": "b" * 32, "state": "received"}], **change}
        client = PrototypeChatSyncClient(ChatSyncLedger(tmp_path / "recovery.db"), DOMAIN, request)
        with pytest.raises(ValueError, match="invalid_chat_recovery"):
            await client.check_recovery([{"messageId": "b" * 32, "sha256": "a" * 64}])
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"through": 8}, {"cursor": 8}, {"cursor": 3}, {"hasMore": True}, {"deletedMessageIds": ["bad"]},
    {"cursor": 3, "hasMore": True}, {"cursor": 2, "through": 7, "hasMore": True},
])
def test_recovery_client_rejects_changed_or_incomplete_fence(tmp_path, change):
    """No recovery decision can silently skip the remainder of a deletion page."""
    async def scenario():
        async def request(*_args):
            return {"ownerId": DOMAIN.owner_id, "characterSyncId": DOMAIN.character_uid,
                    "through": 7, "cursor": 7, "hasMore": False, "deletedMessageIds": [], **change}
        client = PrototypeChatSyncClient(ChatSyncLedger(tmp_path / "recovery.db"), DOMAIN, request)
        with pytest.raises(ValueError, match="invalid_chat_recovery"):
            await client.recovery_deletions(after=2, through=7)
    asyncio.run(scenario())


DOMAIN = ChatDomain("00000000-0000-4000-8000-000000000001", "a" * 32)
OTHER = ChatDomain(DOMAIN.owner_id, "b" * 32)


def capture(ledger, index, text="same text", domain=DOMAIN):
    """Register only synthetic sources using the already tested capture API."""
    return ledger.capture(domain, SourceLocator("synthetic-db", "generation", str(index)), ChatOriginal("user", text))


def deletion_rows(ledger):
    """Read the fixed marker outbox and tombstones without a sync pass."""
    with closing(sqlite3.connect(ledger.path)) as conn:
        return (conn.execute("SELECT domain, deletion_id, payload FROM chat_deletions ORDER BY rowid").fetchall(),
                conn.execute("SELECT domain, message_id FROM chat_tombstones ORDER BY rowid").fetchall())


def test_local_delete_hides_immediately_and_keeps_later_capture(tmp_path):
    """A deletion changes visibility at once and leaves a later capture pending."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    first = capture(ledger, 1)
    deletion = ledger.queue_deletion(DOMAIN, [first["messageId"], "f" * 32])
    assert ledger.visible_originals(DOMAIN) == []
    assert ledger.pending(DOMAIN) == []
    second = capture(ledger, 2)
    restarted = ChatSyncLedger(ledger.path)
    markers, tombstones = deletion_rows(restarted)
    assert markers == [(DOMAIN.key(), deletion.deletion_id, deletion.payload)]
    assert tombstones == [(DOMAIN.key(), first["messageId"]), (DOMAIN.key(), "f" * 32)]
    assert restarted.deleted_message_ids(OTHER) == []
    with pytest.raises(FrozenInstanceError):
        deletion.deletion_id = "e" * 32
    assert [decode_chat_original(raw)["messageId"] for raw, _ in restarted.pending(DOMAIN)] == [second["messageId"]]


@pytest.mark.parametrize("ids", [[], ["f" * 32, "f" * 32], ["F" * 32], ["f" * 32 + "\n"], [None]])
def test_deletion_rejects_ambiguous_id_lists_without_mutation(tmp_path, ids):
    """Only a nonempty explicit set of unique lowercase IDs can be queued."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    with pytest.raises(ValueError, match="invalid_message_ids"):
        ledger.queue_deletion(DOMAIN, ids)
    assert deletion_rows(ledger) == ([], [])


def test_deletion_transaction_failure_rolls_back_hidden_marker(tmp_path):
    """Outbox registration and immediate local hiding either both commit or neither."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    capture(ledger, 1)
    before = ledger.visible_originals(DOMAIN)
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("CREATE TRIGGER fail_delete BEFORE INSERT ON chat_deletions BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        ledger.queue_deletion(DOMAIN, [decode_chat_original(before[0])["messageId"]])
    assert ledger.visible_originals(DOMAIN) == before
    assert deletion_rows(ledger) == ([], [])
