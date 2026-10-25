"""Disposable-database coverage for the internal v0 loop, without runtime chat."""

import asyncio
from dataclasses import FrozenInstanceError
import json
import sqlite3

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from services.chat_sync.ledger import (
    ChatDomain,
    ChatOriginal,
    ChatSyncLedger,
    IdentityConflict,
    SourceLocator,
)
from services.chat_sync.protocol import decode_chat_original, encode_chat_original
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
        assert ledger.cursor(DOMAIN) == 0
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


def page(cursor, originals=(), deleted=(), has_more=False):
    """Build the temporary changes contract with canonical original strings."""
    return {
        "cursor": cursor, "hasMore": has_more,
        "originals": [encode_chat_original(record).decode("utf-8") for record in originals],
        "deletedMessageIds": list(deleted),
    }


def batch_receipt(batch):
    """Acknowledge exactly the sealed member order, without a download claim."""
    return {"batchId": batch.batch_id, "messageIds": [item[0] for item in batch.members], "cursor": 100}


def deletion_receipt(deletion):
    """Return the matching fixed deletion request identity and ID list."""
    return {"deletionId": deletion.deletion_id, "messageIds": list(deletion.message_ids), "cursor": 100}


def test_pull_only_does_not_seal_or_send_pending_originals_or_markers(tmp_path):
    """A scheduled download check cannot bypass body or deletion admission."""
    async def scenario():
        ledger = ChatSyncLedger(tmp_path / "sync.db")
        capture(ledger, 1)
        ledger.queue_deletions(DOMAIN, ["f" * 32])
        requests = []

        async def request(method, path, body):
            requests.append((method, path, body))
            return page(1)

        client = PrototypeChatSyncClient(ledger, DOMAIN, request)
        receipt = await client.step(byte_budget=10000, max_pull_pages=1,
                                    upload=False, send_deletions=False)
        assert receipt["uploaded"] == receipt["deletions"] == 0
        assert receipt["pullPages"] == 1
        assert len(requests) == 1 and requests[0][0] == "GET"
        assert ledger.stats(DOMAIN) == {"pendingOriginals": 1, "pendingDeletions": 1,
                                       "cursor": 1, "hasSealedBatch": False,
                                       "localOnlyOriginals": 0}
    asyncio.run(scenario())


def test_download_is_durable_deduplicates_ids_and_never_registers_uploads(tmp_path):
    """Equal text with distinct IDs survives while exact replays remain one record."""
    source = ChatSyncLedger(tmp_path / "source.db")
    first, second = capture(source, 1), capture(source, 2)
    target = ChatSyncLedger(tmp_path / "target.db")
    target.apply_changes(DOMAIN, page(2, [first, second, first]), after=0)
    restarted = ChatSyncLedger(target.path)
    assert {decode_chat_original(raw)["messageId"] for raw in restarted.visible_originals(DOMAIN)} == {
        first["messageId"], second["messageId"],
    }
    assert restarted.pending(DOMAIN) == []
    assert restarted.cursor(DOMAIN) == 2
    assert restarted.cursor(OTHER) == 0
    assert restarted.visible_originals(OTHER) == []
    with sqlite3.connect(target.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chat_originals").fetchone()[0] == 0


@pytest.mark.parametrize("local", [False, True])
def test_conflicting_page_rolls_back_all_members_deletions_and_cursor(tmp_path, local):
    """One immutable-ID conflict prevents every other mutation in the same page."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    source = ledger if local else ChatSyncLedger(tmp_path / "source.db")
    first = capture(source, 1)
    second = capture(source, 2)
    if not local:
        ledger.apply_changes(DOMAIN, page(1, [first]), after=0)
    before = ledger.visible_originals(DOMAIN)
    after = ledger.cursor(DOMAIN)
    with pytest.raises(IdentityConflict, match="original_content_conflict"):
        ledger.apply_changes(DOMAIN, page(3, [second, {**first, "text": "changed"}], [first["messageId"]]), after=after)
    assert ledger.cursor(DOMAIN) == after
    assert ledger.visible_originals(DOMAIN) == before
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_tombstones").fetchone()[0] == 0


@pytest.mark.parametrize("existing", ["same-page", "downloaded", "captured", "deleted"])
def test_origin_sequence_conflict_rolls_back_complete_page(tmp_path, existing):
    """One source sequence cannot acquire a second message ID, even after deletion."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    source = ledger if existing == "captured" else ChatSyncLedger(tmp_path / "source.db")
    first = capture(source, 1)
    unrelated = capture(source, 2)
    if existing in {"downloaded", "deleted"}:
        deleted = [first["messageId"]] if existing == "deleted" else []
        ledger.apply_changes(DOMAIN, page(1, [first], deleted), after=0)
        ledger = ChatSyncLedger(ledger.path)
    before = ledger.visible_originals(DOMAIN)
    before_pending = ledger.pending(DOMAIN)
    after = ledger.cursor(DOMAIN)
    with sqlite3.connect(ledger.path) as conn:
        before_tombstones = conn.execute("SELECT * FROM chat_tombstones").fetchall()
        before_identities = conn.execute("SELECT * FROM chat_origin_identities").fetchall()
    conflict = {**first, "messageId": "f" * 32, "text": "different identity at the same source sequence"}
    originals = [first, conflict] if existing == "same-page" else [unrelated, conflict]
    with pytest.raises(IdentityConflict, match="origin_identity_conflict"):
        ledger.apply_changes(DOMAIN, page(after + 1, originals, [unrelated["messageId"]]), after=after)
    assert ledger.visible_originals(DOMAIN) == before
    assert ledger.pending(DOMAIN) == before_pending
    assert ledger.cursor(DOMAIN) == after
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT * FROM chat_tombstones").fetchall() == before_tombstones
        assert conn.execute("SELECT * FROM chat_origin_identities").fetchall() == before_identities


@pytest.mark.parametrize("local", [False, True])
def test_old_temporary_records_are_validated_before_identity_backfill(tmp_path, local):
    """Earlier prototype rows cannot bypass identity checks after opening the new schema."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    source = ledger if local else ChatSyncLedger(tmp_path / "source.db")
    first = capture(source, 1)
    if not local:
        ledger.apply_changes(DOMAIN, page(1, [first]), after=0)
    after = ledger.cursor(DOMAIN)
    with sqlite3.connect(ledger.path) as conn:
        # Reproduce a temporary ledger from before identity registration existed.
        conn.execute("DELETE FROM chat_origin_identities")
        conn.execute("DELETE FROM chat_identity_backfills")
    conflict = {**first, "messageId": "f" * 32}
    with pytest.raises(IdentityConflict, match="origin_identity_conflict"):
        ledger.apply_changes(DOMAIN, page(after + 1, [conflict], [first["messageId"]]), after=after)
    assert ledger.cursor(DOMAIN) == after
    assert ledger.visible_originals(DOMAIN) == [encode_chat_original(first)]
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_origin_identities").fetchone()[0] == 0
    ledger.apply_changes(DOMAIN, page(after + 1, [first]), after=after)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT origin_stream_id, origin_seq, message_id FROM chat_origin_identities").fetchone() == (
            first["originStreamId"], first["originSeq"], first["messageId"],
        )


def test_download_tombstone_wins_same_page_and_old_page_after_restart(tmp_path):
    """An old original or backup-like replay cannot revive a deleted identity."""
    source = ChatSyncLedger(tmp_path / "source.db")
    first = capture(source, 1)
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    ledger.apply_changes(DOMAIN, page(1, [first], [first["messageId"]]), after=0)
    restarted = ChatSyncLedger(ledger.path)
    restarted.apply_changes(DOMAIN, page(2, [first]), after=1)
    assert restarted.visible_originals(DOMAIN) == []
    assert restarted.pending(DOMAIN) == []
    assert restarted.cursor(DOMAIN) == 2


def test_local_delete_hides_immediately_but_preserves_fixed_batch_and_later_capture(tmp_path):
    """A deletion changes visibility, never the bytes of an already sealed retry."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    first = capture(ledger, 1)
    batch = ledger.seal_batch(DOMAIN, byte_budget=4096)
    deletion = ledger.queue_deletion(DOMAIN, [first["messageId"], "f" * 32])
    assert ledger.visible_originals(DOMAIN) == []
    assert ledger.pending(DOMAIN) == []
    assert ledger.seal_batch(DOMAIN, byte_budget=1) == batch
    second = capture(ledger, 2)
    restarted = ChatSyncLedger(ledger.path)
    assert restarted.pending_deletions(DOMAIN) == [deletion]
    assert restarted.pending_deletions(OTHER) == []
    with pytest.raises(FrozenInstanceError):
        deletion.deletion_id = "e" * 32
    restarted.ack_deletion(DOMAIN, deletion_receipt(deletion))
    restarted.ack_batch(DOMAIN, batch_receipt(batch))
    assert restarted.cursor(DOMAIN) == 0
    assert [decode_chat_original(raw)["messageId"] for raw, _ in restarted.pending(DOMAIN)] == [second["messageId"]]
    assert restarted.seal_batch(DOMAIN, byte_budget=4096).members[0][0] == second["messageId"]


@pytest.mark.parametrize("mutation", ["id", "order", "missing", "extra", "cursor", "field"])
def test_wrong_batch_receipt_never_clears_fixed_or_later_pending(tmp_path, mutation):
    """Only the exact fixed ID list, order and batch ID authorize acknowledgement."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    capture(ledger, 1)
    capture(ledger, 2)
    batch = ledger.seal_batch(DOMAIN, byte_budget=4096)
    capture(ledger, 3)
    receipt = batch_receipt(batch)
    if mutation == "id":
        receipt["batchId"] = "e" * 32
    elif mutation == "order":
        receipt["messageIds"].reverse()
    elif mutation == "missing":
        receipt["messageIds"].pop()
    elif mutation == "extra":
        receipt["messageIds"].append("e" * 32)
    elif mutation == "cursor":
        receipt["cursor"] = True
    else:
        receipt["unexpected"] = True
    with pytest.raises(ValueError, match="invalid_chat_receipt"):
        ledger.ack_batch(DOMAIN, receipt)
    assert len(ledger.pending(DOMAIN)) == 3
    assert ledger.seal_batch(DOMAIN, byte_budget=1) == batch


def test_wrong_deletion_receipt_keeps_outbox_and_all_tombstones(tmp_path):
    """A partial receipt does not consume a fixed multi-ID deletion request."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    deletion = ledger.queue_deletion(DOMAIN, ["e" * 32, "f" * 32])
    receipt = deletion_receipt(deletion)
    receipt["messageIds"].pop()
    with pytest.raises(ValueError, match="invalid_chat_receipt"):
        ledger.ack_deletion(DOMAIN, receipt)
    assert ledger.pending_deletions(DOMAIN) == [deletion]
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_tombstones").fetchone()[0] == 2


@pytest.mark.parametrize("ids", [[], ["f" * 32, "f" * 32], ["F" * 32], ["f" * 32 + "\n"], [None]])
def test_deletion_rejects_ambiguous_id_lists_without_mutation(tmp_path, ids):
    """Only a nonempty explicit set of unique lowercase IDs can be queued."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    with pytest.raises(ValueError, match="invalid_message_ids"):
        ledger.queue_deletion(DOMAIN, ids)
    assert ledger.pending_deletions(DOMAIN) == []


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
    assert ledger.pending_deletions(DOMAIN) == []


@pytest.mark.parametrize("mutation", ["backward", "stalled", "foreign", "noncanonical", "type"])
def test_bad_changes_page_cannot_partially_merge(tmp_path, mutation):
    """Malformed namespace, bytes or progress leave the complete page uncommitted."""
    source = ChatSyncLedger(tmp_path / "source.db")
    first = capture(source, 1)
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    value = page(1, [first])
    if mutation == "backward":
        value["cursor"] = -1
    elif mutation == "stalled":
        value.update(cursor=0, hasMore=True)
    elif mutation == "foreign":
        value = page(1, [{**first, "characterSyncId": OTHER.character_uid}])
    elif mutation == "noncanonical":
        value["originals"][0] += " "
    else:
        value["hasMore"] = 1
    with pytest.raises(ValueError, match="invalid_chat_changes"):
        ledger.apply_changes(DOMAIN, value, after=0)
    assert ledger.cursor(DOMAIN) == 0
    assert ledger.visible_originals(DOMAIN) == []


def test_stale_download_writer_cannot_advance_another_page(tmp_path):
    """A concurrently advanced cursor requires the caller to fetch its page again."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    ledger.apply_changes(DOMAIN, page(1), after=0)
    with pytest.raises(ValueError, match="chat_cursor_conflict"):
        ledger.apply_changes(DOMAIN, page(2), after=0)
    assert ledger.cursor(DOMAIN) == 1


def test_client_sends_deletion_before_fixed_body_and_only_pull_advances_cursor(tmp_path):
    """The injected transport sees fixed canonical bodies and deletion-first ordering."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    first = capture(ledger, 1)
    batch = ledger.seal_batch(DOMAIN, byte_budget=4096)
    deletion = ledger.queue_deletion(DOMAIN, [first["messageId"]])
    calls = []

    async def request(method, path, body):
        """Return synthetic receipts without any network or account lookup."""
        calls.append((method, path, body))
        if method == "POST":
            assert body == deletion.payload
            return deletion_receipt(deletion)
        if method == "PUT":
            assert body == json.dumps({"originals": [batch.members[0][1].decode()]}, ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":")).encode()
            capture(ledger, 2, "late capture")
            return batch_receipt(batch)
        assert path.endswith("after=0&limit=200")
        return page(1, [first], [first["messageId"]])

    client = PrototypeChatSyncClient(ledger, DOMAIN, request)
    result = asyncio.run(client.step(byte_budget=4096, max_pull_pages=2))
    assert [call[0] for call in calls] == ["POST", "PUT", "GET"]
    assert result == {"deletions": 1, "uploaded": 1, "pullPages": 1, "cursor": 1, "hasMore": False}
    assert len(ledger.pending(DOMAIN)) == 1
    assert len(ledger.visible_originals(DOMAIN)) == 1


def test_client_lost_response_retries_identical_batch_bytes_after_restart(tmp_path):
    """A server-side commit with a lost reply never changes the local retry envelope."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    capture(ledger, 1)
    calls = []

    async def lost_response(method, path, body):
        """Simulate a response lost after the remote request reached its server."""
        calls.append((method, path, body))
        raise OSError("synthetic_response_lost")

    with pytest.raises(OSError, match="synthetic_response_lost"):
        asyncio.run(PrototypeChatSyncClient(ledger, DOMAIN, lost_response).step(byte_budget=4096, max_pull_pages=1))
    capture(ledger, 2)
    restarted = ChatSyncLedger(ledger.path)

    async def retry(method, path, body):
        """Confirm the exact previous request before acknowledging its members."""
        if method == "PUT":
            assert (method, path, body) == calls[0]
            return batch_receipt(restarted.seal_batch(DOMAIN, byte_budget=1))
        return page(0)

    asyncio.run(PrototypeChatSyncClient(restarted, DOMAIN, retry).step(byte_budget=1, max_pull_pages=1))
    assert len(restarted.pending(DOMAIN)) == 1


def test_client_failed_deletion_prevents_original_upload(tmp_path):
    """A marker failure retains the fixed request and stops body traffic for this pass."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    capture(ledger, 1)
    deletion = ledger.queue_deletion(DOMAIN, ["f" * 32])
    calls = []

    async def request(method, path, body):
        """Fail only the expected marker call, never contact an external service."""
        calls.append((method, path, body))
        raise OSError("synthetic_unavailable")

    with pytest.raises(OSError, match="synthetic_unavailable"):
        asyncio.run(PrototypeChatSyncClient(ledger, DOMAIN, request).step(byte_budget=4096, max_pull_pages=1))
    assert [call[0] for call in calls] == ["POST"]
    assert ledger.pending_deletions(DOMAIN) == [deletion]
    assert len(ledger.pending(DOMAIN)) == 1


def test_client_empty_filtered_pages_advance_with_bounded_pagination(tmp_path):
    """Empty visible pages are valid when the cursor progresses; each pass stays bounded."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    calls = []

    async def request(method, path, body):
        """Model filtered changes with more events beyond each empty visible page."""
        calls.append((method, path, body))
        return page(len(calls), has_more=True)

    client = PrototypeChatSyncClient(ledger, DOMAIN, request)
    first = asyncio.run(client.step(byte_budget=4096, max_pull_pages=2))
    assert first["cursor"] == 2 and first["hasMore"] is True and first["pullPages"] == 2
    second = asyncio.run(client.step(byte_budget=4096, max_pull_pages=1))
    assert second["cursor"] == 3 and second["hasMore"] is True
    assert calls[-1][1].endswith("after=2&limit=200")


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_client_rejects_unbounded_or_invalid_page_budget(tmp_path, budget):
    """Invalid budgets fail before reading or issuing transport requests."""
    async def request(method, path, body):
        """Detect an accidental request during input validation."""
        pytest.fail("transport must not run")

    client = PrototypeChatSyncClient(ChatSyncLedger(tmp_path / "sync.db"), DOMAIN, request)
    with pytest.raises(ValueError, match="invalid_pull_page_budget"):
        asyncio.run(client.step(byte_budget=4096, max_pull_pages=budget))
