import hashlib
import json
import multiprocessing
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from services.chat_sync.ledger import (
    ChatDomain,
    ChatOriginal,
    ChatSyncLedger,
    IdentityConflict,
    SourceLocator,
)
import services.chat_sync.ledger as ledger_module
from services.chat_sync.protocol import MAX_ORIGIN_SEQ, decode_chat_original, encode_chat_original
from tests.fake_clock import patch_module_clock


DOMAIN = ChatDomain("00000000-0000-4000-8000-000000000001", "a" * 32)
SOURCE = SourceLocator("durable-db-id", "generation-a", "row-1")


def _crash_before_pending_commit(path, witness):
    from services.chat_sync.writer import ChatSyncWriter, _WriterConnection
    writer = ChatSyncWriter(path.parent, witness, database_names=(path.name,))
    with writer.initialize():
        ledger = ChatSyncLedger(path)
    original_execute = _WriterConnection.execute
    def crash_execute(self, sql, parameters=()):
        if sql.startswith("INSERT INTO chat_pending"):
            os._exit(17)
        return original_execute(self, sql, parameters)
    _WriterConnection.execute = crash_execute
    ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "original"))


def test_process_exit_before_pending_commit_requires_recovery(tmp_path):
    from services.chat_sync.writer import WriterQuarantined, registered_writer
    path = tmp_path / "sync.db"
    ChatSyncLedger(path)
    writer = registered_writer(path.parent)
    process = multiprocessing.get_context("spawn").Process(target=_crash_before_pending_commit, args=(path, writer.path))
    process.start()
    try:
        process.join(timeout=10)
        assert process.exitcode == 17
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    with pytest.raises(WriterQuarantined):
        writer.check()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM chat_pending").fetchone()[0] == 0
    # The durable reservation cannot prove whether a later file was restored.
    # An empty SQLite queue therefore does not authorize a new origin sequence.
    with pytest.raises(WriterQuarantined):
        ChatSyncLedger(path).capture(DOMAIN, SOURCE, ChatOriginal("user", "original"))


def test_fixed_utf8_payload_sample(tmp_path, monkeypatch):
    from types import SimpleNamespace

    identifiers = iter(["c" * 32, "b" * 32])
    monkeypatch.setattr(ledger_module, "uuid4", lambda: SimpleNamespace(hex=next(identifiers)))
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "你好\n"))
    expected = (
        '{"channel":"chat","characterSyncId":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"conversationId":null,"messageId":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","originSeq":1,'
        '"originStreamId":"cccccccccccccccccccccccccccccccc","originalTime":null,'
        '"ownerId":"00000000-0000-4000-8000-000000000001",'
        '"replyToMessageId":null,"role":"user","schemaVersion":1,'
        '"scope":"private-chat","text":"你好\\n","timezone":null}'
    ).encode("utf-8")
    assert ledger.pending(DOMAIN) == [(expected, hashlib.sha256(expected).hexdigest())]


def test_identity_backfill_runs_once_not_on_every_poll(tmp_path, monkeypatch):
    """Polling an empty page does not reread and re-verify the whole saved history."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    for row in range(3):
        ledger.capture(DOMAIN, SourceLocator("durable-db-id", "generation-a", f"row-{row}"), ChatOriginal("user", str(row)))
    seen, real = [], ledger_module._remember_origin_identity
    monkeypatch.setattr(ledger_module, "_remember_origin_identity",
                        lambda conn, domain, record: (seen.append(record["messageId"]), real(conn, domain, record)))
    def empty(cursor):
        return {"cursor": cursor, "hasMore": False, "originals": [], "deletedMessageIds": []}
    ledger.apply_changes(DOMAIN, empty(1), after=0)
    assert len(seen) == 3
    seen.clear()
    ChatSyncLedger(ledger.path).apply_changes(DOMAIN, empty(2), after=1)
    assert seen == []


def test_restart_keeps_alias_and_fixed_bytes(tmp_path):
    path = tmp_path / "sync.db"
    ledger = ChatSyncLedger(path)
    original = ChatOriginal("user", "  原文\n🙂  ", "2026-10-09 12:00:00")
    first = ledger.capture(DOMAIN, SOURCE, original)
    pending = ledger.pending(DOMAIN)
    restarted = ChatSyncLedger(path)
    assert restarted.capture(DOMAIN, SOURCE, original) == first
    assert restarted.pending(DOMAIN) == pending
    raw, digest = pending[0]
    assert digest == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)["text"] == original.text
    assert json.loads(raw)["timezone"] is None
    assert b"contextText" not in raw
    assert first["originSeq"] == 1


def test_same_text_is_not_identity(tmp_path):
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    original = ChatOriginal("assistant", "same")
    first = ledger.capture(DOMAIN, SOURCE, original)
    second = ledger.capture(DOMAIN, SourceLocator("durable-db-id", "generation-a", "row-2"), original)
    assert first["messageId"] != second["messageId"]
    assert first["originStreamId"] == second["originStreamId"]
    assert second["originSeq"] == 2


def test_conflict_preserves_original_and_sequence(tmp_path):
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    first = ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "old"))
    with pytest.raises(IdentityConflict, match="source_content_conflict"):
        ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "changed"))
    assert json.loads(ledger.pending(DOMAIN)[0][0]) == first
    second = ledger.capture(DOMAIN, SourceLocator("durable-db-id", "generation-a", "row-2"), ChatOriginal("user", "new"))
    assert second["originSeq"] == 2


@pytest.mark.parametrize("other", [ChatDomain("00000000-0000-4000-8000-000000000002", "a" * 32), ChatDomain(DOMAIN.owner_id, "b" * 32)])
def test_domains_are_isolated(tmp_path, other):
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    first = ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "A"))
    assert ledger.pending(other) == []
    second = ledger.capture(other, SOURCE, ChatOriginal("user", "B"))
    assert first["originStreamId"] != second["originStreamId"]
    assert len(ledger.pending(DOMAIN)) == len(ledger.pending(other)) == 1


def test_new_storage_generation_does_not_reuse_row_identity(tmp_path):
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    first = ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "same"))
    second = ledger.capture(DOMAIN, SourceLocator("durable-db-id", "generation-b", "row-1"), ChatOriginal("user", "same"))
    assert first["messageId"] != second["messageId"]
    assert first["originStreamId"] != second["originStreamId"]


def test_outbox_failure_rolls_back_original_alias_and_sequence(tmp_path):
    path = tmp_path / "sync.db"
    ledger = ChatSyncLedger(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_pending BEFORE INSERT ON chat_pending BEGIN SELECT RAISE(ABORT, 'injected_failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected_failure"):
        ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "original"))
    assert ledger.queue_state(DOMAIN)["oldestEnqueuedAt"] is None
    assert ledger.queue_state(DOMAIN)["lastEnqueuedAt"] is None
    with sqlite3.connect(path) as conn:
        for table in ("chat_originals", "chat_pending", "chat_sources"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_pending")
    assert ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "original"))["originSeq"] == 1


def test_concurrent_retry_has_one_identity(tmp_path):
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(lambda _: ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "hello")), range(12)))
    assert len({record["messageId"] for record in records}) == 1
    assert len(ledger.pending(DOMAIN)) == 1


def test_concurrent_new_records_allocate_distinct_sequences(tmp_path):
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    def capture(index):
        return ledger.capture(DOMAIN, SourceLocator("durable-db-id", "generation-a", str(index)), ChatOriginal("user", "same"))
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(capture, range(12)))
    assert sorted(record["originSeq"] for record in records) == list(range(1, 13))
    assert len({record["messageId"] for record in records}) == 12
    assert len({record["originStreamId"] for record in records}) == 1


def test_returned_payload_cannot_mutate_persisted_original(tmp_path):
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    record = ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "original"))
    record["text"] = "changed"
    assert json.loads(ledger.pending(DOMAIN)[0][0])["text"] == "original"


def test_pending_limit_does_not_ack_or_lose_later_records(tmp_path):
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    for index in range(3):
        ledger.capture(DOMAIN, SourceLocator("durable-db-id", "generation-a", str(index)), ChatOriginal("user", str(index)))
    first_page = ledger.pending(DOMAIN, limit=2)
    assert len(first_page) == 2
    assert ledger.pending(DOMAIN, limit=2) == first_page
    assert len(ledger.pending(DOMAIN)) == 3


def test_sequence_exhaustion_never_rounds_or_wraps(tmp_path):
    """The last JS-safe sequence commits; later captures leave no partial task."""
    path = tmp_path / "sync.db"
    ledger = ChatSyncLedger(path)
    ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "first"))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE chat_sources SET next_seq=?", (MAX_ORIGIN_SEQ,))
    last_source = SourceLocator("durable-db-id", "generation-a", "last")
    assert ledger.capture(DOMAIN, last_source, ChatOriginal("user", "last"))["originSeq"] == MAX_ORIGIN_SEQ
    exhausted = SourceLocator("durable-db-id", "generation-a", "exhausted")
    with pytest.raises(ValueError, match="source_sequence_exhausted"):
        ledger.capture(DOMAIN, exhausted, ChatOriginal("user", "not captured"))
    assert len(ledger.pending(DOMAIN)) == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT next_seq FROM chat_sources").fetchone()[0] == MAX_ORIGIN_SEQ + 1


def test_protocol_rejection_rolls_back_source_registration(tmp_path):
    """Invalid reply linkage must not leave an alias, sequence or pending record."""
    path = tmp_path / "sync.db"
    ledger = ChatSyncLedger(path)
    with pytest.raises(ValueError, match="invalid_chat_original"):
        ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "text", reply_to_message_id="local-only-id"))
    assert ledger.pending(DOMAIN) == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_sources").fetchone()[0] == 0
    assert ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "text"))["originSeq"] == 1


def test_ledger_payload_uses_shared_protocol_without_time_rewriting(tmp_path):
    """Caller-supplied occurrence representation is preserved, including spaces."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    original = ChatOriginal("user", "原文", original_time=" 2026-10-09 12:00:00 ")
    ledger.capture(DOMAIN, SOURCE, original)
    payload, _ = ledger.pending(DOMAIN)[0]
    assert decode_chat_original(payload)["originalTime"] == original.original_time


def test_queue_state_keeps_actual_capture_age_across_retry_and_restart(tmp_path, monkeypatch):
    """New arrivals update idle time without resetting the oldest pending wait."""
    path = tmp_path / "sync.db"
    ledger = ChatSyncLedger(path)
    patch_module_clock(monkeypatch, ledger_module, time_ns=lambda: 100_000_000)
    original = ChatOriginal("user", "  原文🙂  ", original_time="historic-time")
    first = ledger.capture(DOMAIN, SOURCE, original)
    patch_module_clock(monkeypatch, ledger_module, time_ns=lambda: 200_000_000)
    ledger.capture(DOMAIN, SourceLocator("durable-db-id", "generation-a", "row-2"), original)
    ledger = ChatSyncLedger(path)
    assert ledger.capture(DOMAIN, SOURCE, original, queued_at_ms=900) == first
    state = ledger.queue_state(DOMAIN)
    assert state == {
        "pendingOriginals": 2,
        "pendingBytes": sum(len(payload) for payload, _ in ledger.pending(DOMAIN)),
        "oldestEnqueuedAt": 100, "lastEnqueuedAt": 200,
        "legacyPending": False, "hasSealedBatch": False, "pendingDeletions": 0,
    }
    # Sealing and retrying a fixed batch must not refresh arrival clocks.
    first_batch = ledger.seal_batch(DOMAIN, byte_budget=10_000)
    assert ledger.seal_batch(DOMAIN, byte_budget=10_000) == first_batch
    assert ledger.queue_state(DOMAIN) == {**state, "hasSealedBatch": True}


def test_queue_state_ignores_deleted_and_other_domain_originals(tmp_path):
    """Only this role's live raw payload bytes contribute to upload eligibility."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    deleted = ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "deleted"), queued_at_ms=100)
    ledger.capture(DOMAIN, SourceLocator("db", "generation", "live"), ChatOriginal("user", "🙂"), queued_at_ms=200)
    other = ChatDomain(DOMAIN.owner_id, "b" * 32)
    ledger.capture(other, SOURCE, ChatOriginal("user", "another role"), queued_at_ms=300)
    ledger.queue_deletion(DOMAIN, [deleted["messageId"]])
    assert ledger.queue_state(DOMAIN) == {
        "pendingOriginals": 1,
        "pendingBytes": len(ledger.pending(DOMAIN)[0][0]),
        "oldestEnqueuedAt": 200, "lastEnqueuedAt": 200,
        "legacyPending": False, "hasSealedBatch": False, "pendingDeletions": 1,
    }
    empty = ChatDomain(DOMAIN.owner_id, "c" * 32)
    assert ledger.queue_state(empty) == {
        "pendingOriginals": 0, "pendingBytes": 0,
        "oldestEnqueuedAt": None, "lastEnqueuedAt": None,
        "legacyPending": False, "hasSealedBatch": False, "pendingDeletions": 0,
    }


def test_old_pending_schema_keeps_unknown_age_and_explicit_none_stays_legacy(tmp_path):
    """Migration retains old tasks without inventing an enqueue clock from text time."""
    path = tmp_path / "sync.db"
    ledger = ChatSyncLedger(path)
    first = ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "old"), queued_at_ms=100)
    # Recreate the previous two-column table using only this disposable database.
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE chat_pending RENAME TO captured_pending")
        conn.execute("CREATE TABLE chat_pending (domain TEXT NOT NULL, message_id TEXT NOT NULL, "
                     "PRIMARY KEY (domain,message_id))")
        conn.execute("INSERT INTO chat_pending SELECT domain,message_id FROM captured_pending")
        conn.execute("DROP TABLE captured_pending")
    ledger = ChatSyncLedger(path)
    ledger.capture(DOMAIN, SOURCE, ChatOriginal("user", "old"))
    ledger.capture(DOMAIN, SourceLocator("db", "generation", "legacy-source"),
                   ChatOriginal("user", "unknown", original_time="1"), queued_at_ms=None)
    assert ledger.queue_state(DOMAIN)["legacyPending"] is True
    assert ledger.queue_state(DOMAIN)["oldestEnqueuedAt"] is None
    assert ledger.queue_state(DOMAIN)["lastEnqueuedAt"] is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT queued_at_ms FROM chat_pending").fetchall() == [(None,), (None,)]
    ledger.capture(DOMAIN, SourceLocator("db", "generation", "new-source"), ChatOriginal("user", "new"), queued_at_ms=300)
    state = ledger.queue_state(DOMAIN)
    assert state["pendingOriginals"] == 3
    assert state["legacyPending"] is True
    assert state["oldestEnqueuedAt"] == state["lastEnqueuedAt"] == 300
    assert json.loads(ledger.pending(DOMAIN)[0][0])["messageId"] == first["messageId"]


def test_queue_state_counts_beyond_one_upload_page(tmp_path):
    """Scheduler thresholds inspect the whole outbox, including later legacy rows."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    expected_bytes = 0
    for index in range(205):
        record = ledger.capture(
            DOMAIN, SourceLocator("db", "generation", str(index)), ChatOriginal("user", "🙂"),
            queued_at_ms=100 + index if index < 204 else None,
        )
        expected_bytes += len(encode_chat_original(record))
    state = ledger.queue_state(DOMAIN)
    assert state["pendingOriginals"] == 205
    assert state["pendingBytes"] == expected_bytes
    assert state["oldestEnqueuedAt"] == 100
    assert state["lastEnqueuedAt"] == 303
    assert state["legacyPending"] is True


@pytest.mark.parametrize("role", ["system", "human", "tool"])
def test_non_chat_roles_rejected(role):
    with pytest.raises(ValueError, match="invalid_chat_original"):
        ChatOriginal(role, "memo")


@pytest.mark.parametrize("channel,scope", [("memory", "private-chat"), ("chat", "theater"), ("model", "private-chat")])
def test_unsupported_domains_rejected(channel, scope):
    with pytest.raises(ValueError, match="unsupported_sync_domain"):
        ChatDomain(DOMAIN.owner_id, "a" * 32, channel, scope)


@pytest.mark.parametrize("limit", [0, -1, 201, True, 1.5])
def test_pending_reads_are_bounded(tmp_path, limit):
    with pytest.raises(ValueError, match="invalid_page_size"):
        ChatSyncLedger(tmp_path / "sync.db").pending(DOMAIN, limit=limit)


def test_history_and_live_batches_use_durable_bounded_fairness(tmp_path):
    """Live originals lead while continuous arrivals permit every fourth history batch."""
    ledger = ChatSyncLedger(tmp_path / "fair.sqlite3")
    domain = ChatDomain("00000000-0000-4000-8000-000000000001", "a" * 32)
    stream = "f" * 32
    historical = ledger.capture(domain, SourceLocator("trusted", "one", "old"), ChatOriginal("user", "history"),
                                queue_lane="history", proven_source_origin=(stream, 1))
    observed = []
    source_order = []
    for index in range(5):
        ledger.capture(domain, SourceLocator("trusted", "one", str(index)), ChatOriginal("user", f"new {index}"),
                       proven_source_origin=(stream, index + 2))
        batch = ledger.seal_batch(domain, byte_budget=100000, limit=1)
        observed.append(json.loads(batch.members[0][1])["text"])
        source_order.append(json.loads(batch.members[0][1])["originSeq"])
        ledger.ack_batch(domain, {"batchId": batch.batch_id, "messageIds": [batch.members[0][0]], "cursor": 0})
        ledger = ChatSyncLedger(ledger.path)
    assert observed == ["new 0", "new 1", "new 2", "history", "new 3"]
    assert source_order == [2, 3, 4, 1, 5]
    assert historical["messageId"] not in [json.loads(raw)["messageId"] for raw, _ in ledger.pending(domain)]


def test_proven_source_order_is_fixed_and_cannot_rewrite_existing_alias(tmp_path):
    """A trustworthy raw producer fixes wire order before delayed ledger capture."""
    ledger = ChatSyncLedger(tmp_path / "source-order.sqlite3")
    locator = SourceLocator("trusted", "generation", "first")
    stream = "e" * 32
    record = ledger.capture(DOMAIN, locator, ChatOriginal("user", "raw"), proven_source_origin=(stream, 2))
    assert record["originStreamId"] == stream and record["originSeq"] == 2
    assert ledger.capture(DOMAIN, locator, ChatOriginal("user", "raw"), proven_source_origin=(stream, 2)) == record
    with pytest.raises(ValueError, match="source_identity_conflict"):
        ledger.capture(DOMAIN, locator, ChatOriginal("user", "raw"), proven_source_origin=(stream, 1))
    with pytest.raises(ValueError, match="origin_identity_conflict"):
        ledger.capture(DOMAIN, SourceLocator("trusted", "generation", "second"), ChatOriginal("user", "other"), proven_source_origin=(stream, 2))
    for origin in ((stream, 0), (stream, True), (stream, 2**53), ("bad", 1), (stream, None)):
        with pytest.raises(ValueError, match="invalid_chat_source_origin"):
            ledger.capture(DOMAIN, locator, ChatOriginal("user", "raw"), proven_source_origin=origin)
    assert json.loads(ledger.pending(DOMAIN)[0][0]) == record


@pytest.mark.parametrize("invalid", ["domain", "canonical", "duplicate_id", "origin_conflict"])
def test_tombstoned_download_does_not_bypass_page_validation(tmp_path, invalid):
    """Tombstoned IDs still undergo all immutable wire/page validation."""
    ledger = ChatSyncLedger(tmp_path / "excluded.sqlite3")
    identity = "1" * 32
    ledger.queue_deletion(DOMAIN, [identity])
    record = {"schemaVersion": 1, "ownerId": DOMAIN.owner_id, "characterSyncId": DOMAIN.character_uid,
              "channel": "chat", "scope": "private-chat", "messageId": identity,
              "originStreamId": "e" * 32, "originSeq": 1, **ChatOriginal("user", "original").fields()}
    originals = [encode_chat_original(record).decode()]
    if invalid == "domain":
        originals = [encode_chat_original({**record, "characterSyncId": "b" * 32}).decode()]
    elif invalid == "canonical":
        originals = [json.dumps(record)]
    elif invalid == "duplicate_id":
        originals.append(encode_chat_original({**record, "text": "changed"}).decode())
    else:
        originals.append(encode_chat_original({**record, "messageId": "2" * 32}).decode())
    with pytest.raises(ValueError):
        ledger.apply_changes(DOMAIN, {"cursor": 1, "hasMore": False, "originals": originals,
                                      "deletedMessageIds": []}, after=0)
    assert ledger.cursor(DOMAIN) == 0 and ledger.visible_originals(DOMAIN) == []
