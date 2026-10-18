"""Immutable local batches, independent of upload scheduling and acknowledgement."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import hashlib
import json
import multiprocessing
import os
import sqlite3
import sys

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from services.chat_sync.ledger import (
    ChatDomain,
    ChatOriginal,
    MAX_SYNC_ORIGINAL_BYTES,
    ChatSyncLedger,
    OriginalNeedsFragmentation,
    SourceLocator,
)
from services.chat_sync.protocol import decode_chat_original, encode_chat_original


DOMAIN = ChatDomain("00000000-0000-4000-8000-000000000001", "a" * 32)


def capture(ledger, index, text=None, domain=DOMAIN):
    """Register synthetic verified-source originals through the existing ledger API."""
    return ledger.capture(
        domain, SourceLocator("synthetic-database", "generation-a", str(index)),
        ChatOriginal("user", str(index) if text is None else text),
    )


def test_empty_pending_does_not_create_batch(tmp_path):
    """An empty queue has no frozen identity or delivery implication."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    assert ledger.seal_batch(DOMAIN, byte_budget=4096) is None
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_batches").fetchone()[0] == 0


def test_fixed_members_bytes_hash_and_id_survive_new_chat_and_restart(tmp_path):
    """Retries return exactly the snapshot selected before later originals arrived."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    first = capture(ledger, 1, "  原文\n🙂  ")
    second = capture(ledger, 2)
    pending = ledger.pending(DOMAIN)
    batch = ledger.seal_batch(DOMAIN, byte_budget=sum(len(raw) for raw, _ in pending))
    assert [member[0] for member in batch.members] == [first["messageId"], second["messageId"]]
    assert [(raw, digest) for _, raw, digest in batch.members] == pending
    assert hashlib.sha256(batch.sealed_bytes).hexdigest() == batch.sha256
    assert json.loads(batch.sealed_bytes)["domain"] == json.loads(DOMAIN.key())
    assert json.loads(batch.sealed_bytes)["batchId"] == batch.batch_id
    assert len(batch.sealed_bytes) > sum(len(raw) for raw, _ in pending)
    capture(ledger, 3)
    restarted = ChatSyncLedger(ledger.path)
    assert restarted.seal_batch(DOMAIN, byte_budget=1, limit=1) == batch
    assert len(restarted.pending(DOMAIN)) == 3
    with pytest.raises(FrozenInstanceError):
        batch.batch_id = "f" * 32


def test_record_budget_selects_contiguous_prefix_not_later_smaller_original(tmp_path):
    """A later small record cannot leap over an original that requires more budget."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    first = capture(ledger, 1, "first")
    capture(ledger, 2, "long" * 300)
    capture(ledger, 3, "small")
    budget = len(ledger.pending(DOMAIN)[0][0]) + 500
    batch = ledger.seal_batch(DOMAIN, byte_budget=budget)
    assert [member[0] for member in batch.members] == [first["messageId"]]
    assert len(ledger.pending(DOMAIN)) == 3


def test_first_oversized_original_reports_fragmentation_without_skipping(tmp_path):
    """Keep the complete first original pending and identify the required next action."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    first = capture(ledger, 1, "long" * 300)
    capture(ledger, 2, "small")
    pending = ledger.pending(DOMAIN)
    with pytest.raises(OriginalNeedsFragmentation, match="original_requires_fragmentation") as captured:
        ledger.seal_batch(DOMAIN, byte_budget=len(pending[0][0]) - 1)
    assert captured.value.message_id == first["messageId"]
    assert captured.value.byte_length == len(pending[0][0])
    assert ledger.pending(DOMAIN) == pending
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_batches").fetchone()[0] == 0
    batch = ledger.seal_batch(DOMAIN, byte_budget=len(pending[0][0]))
    assert [member[0] for member in batch.members] == [first["messageId"]]


def test_original_too_large_for_any_batch_stays_local_without_blocking_later_ones(tmp_path):
    """One unsendable original leaves the upload queue; later originals keep uploading."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    huge = capture(ledger, 1, "x" * (MAX_SYNC_ORIGINAL_BYTES + 1))
    small = capture(ledger, 2, "small")
    batch = ledger.seal_batch(DOMAIN, byte_budget=MAX_SYNC_ORIGINAL_BYTES)
    assert [member[0] for member in batch.members] == [small["messageId"]]
    assert ledger.stats(DOMAIN)["localOnlyOriginals"] == 1
    assert ledger.stats(DOMAIN)["pendingOriginals"] == 1
    with sqlite3.connect(ledger.path) as conn:
        # The local original and its identity remain; only its upload task ended.
        assert conn.execute("SELECT COUNT(*) FROM chat_originals WHERE message_id=?", (huge["messageId"],)).fetchone()[0] == 1


def test_queue_of_only_oversized_originals_commits_their_local_only_state(tmp_path):
    """A batch with no sendable member still records the skipped originals durably."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    capture(ledger, 1, "x" * (MAX_SYNC_ORIGINAL_BYTES + 1))
    assert ledger.seal_batch(DOMAIN, byte_budget=MAX_SYNC_ORIGINAL_BYTES) is None
    restarted = ChatSyncLedger(ledger.path)
    assert restarted.pending(DOMAIN) == []
    assert restarted.stats(DOMAIN)["localOnlyOriginals"] == 1


@pytest.mark.parametrize("other", [
    ChatDomain("00000000-0000-4000-8000-000000000002", "a" * 32),
    ChatDomain(DOMAIN.owner_id, "b" * 32),
])
def test_batches_are_isolated_by_full_domain(tmp_path, other):
    """One owner or character cannot consume or reuse another domain's fixed batch."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    first = capture(ledger, 1)
    assert ledger.seal_batch(other, byte_budget=4096) is None
    second = capture(ledger, 1, domain=other)
    batch = ledger.seal_batch(DOMAIN, byte_budget=4096)
    other_batch = ledger.seal_batch(other, byte_budget=4096)
    assert batch.batch_id != other_batch.batch_id
    assert batch.members[0][0] == first["messageId"]
    assert other_batch.members[0][0] == second["messageId"]


def test_concurrent_sealers_choose_one_fixed_batch(tmp_path):
    """Separate SQLite connections cannot seal overlapping batches in one domain."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    for index in range(8):
        capture(ledger, index)
    with ThreadPoolExecutor(max_workers=4) as executor:
        batches = list(executor.map(lambda _: ledger.seal_batch(DOMAIN, byte_budget=4096), range(12)))
    assert all(batch == batches[0] for batch in batches)
    assert len({member[0] for member in batches[0].members}) == len(batches[0].members)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_batches").fetchone()[0] == 1
    assert len(ledger.pending(DOMAIN)) == 8


def test_maximum_batch_contains_200_originals_without_ack(tmp_path):
    """The known count cap leaves the next original pending without claiming delivery."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    for index in range(201):
        capture(ledger, index)
    batch = ledger.seal_batch(DOMAIN, byte_budget=1024 * 1024)
    assert len(batch.members) == 200
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_pending").fetchone()[0] == 201


def test_failed_insert_rolls_back_snapshot_without_consuming_pending(tmp_path):
    """A transactional failure leaves no batch ID to mistake for a completed seal."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    capture(ledger, 1)
    pending = ledger.pending(DOMAIN)
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("CREATE TRIGGER fail_batch BEFORE INSERT ON chat_batches BEGIN SELECT RAISE(ABORT, 'injected_failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected_failure"):
        ledger.seal_batch(DOMAIN, byte_budget=4096)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_batches").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_batch")
    assert ledger.pending(DOMAIN) == pending
    assert len(ledger.seal_batch(DOMAIN, byte_budget=4096).members) == 1


def crash_after_batch_insert(path, witness):
    """Exit a spawned process after the batch insert but before SQLite commit."""
    from services.chat_sync.writer import ChatSyncWriter, _WriterConnection
    writer = ChatSyncWriter(path.parent, witness, database_names=(path.name,))
    with writer.initialize():
        ledger = ChatSyncLedger(path)
    original_execute = _WriterConnection.execute
    def crash_execute(self, sql, parameters=()):
        result = original_execute(self, sql, parameters)
        if sql.startswith("INSERT INTO chat_batches"):
            os._exit(17)
        return result
    _WriterConnection.execute = crash_execute
    ledger.seal_batch(DOMAIN, byte_budget=4096)


def test_process_exit_before_batch_commit_preserves_raw_pending_but_requires_recovery(tmp_path):
    """SQLite retains evidence while the ambiguous external reservation blocks reuse."""
    from services.chat_sync.writer import WriterQuarantined, registered_writer
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    capture(ledger, 1)
    pending = ledger.pending(DOMAIN)
    writer = registered_writer(ledger.path.parent)
    process = multiprocessing.get_context("spawn").Process(target=crash_after_batch_insert, args=(ledger.path, writer.path))
    process.start()
    try:
        process.join(timeout=10)
        assert process.exitcode == 17
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_batches").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chat_pending").fetchone()[0] == len(pending)
    with pytest.raises(WriterQuarantined):
        writer.check()
    with pytest.raises(WriterQuarantined):
        ChatSyncLedger(ledger.path).seal_batch(DOMAIN, byte_budget=4096)


@pytest.mark.parametrize("corruption", ["hash", "domain", "member-id", "member-hash", "canonical", "duplicate"])
def test_corrupt_sealed_batch_is_reported_without_rebuilding(tmp_path, corruption):
    """A damaged snapshot retains its identity and is never silently resealed."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    capture(ledger, 1)
    batch = ledger.seal_batch(DOMAIN, byte_budget=4096)
    body = json.loads(batch.sealed_bytes)
    if corruption == "domain":
        body["domain"][0] = "00000000-0000-4000-8000-000000000002"
    elif corruption == "member-id":
        body["members"][0][0] = "f" * 32
    elif corruption == "member-hash":
        body["members"][0][2] = "f" * 64
    elif corruption == "canonical":
        body["members"][0][1] += " "
        body["members"][0][2] = hashlib.sha256(body["members"][0][1].encode()).hexdigest()
    elif corruption == "duplicate":
        body["members"].append(body["members"][0])
    sealed = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = "f" * 64 if corruption == "hash" else hashlib.sha256(sealed).hexdigest()
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("UPDATE chat_batches SET sealed_bytes=?, sha256=?", (sealed, digest))
    with pytest.raises(ValueError, match="invalid_chat_batch"):
        ledger.seal_batch(DOMAIN, byte_budget=4096)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT batch_id FROM chat_batches").fetchone()[0] == batch.batch_id


@pytest.mark.parametrize("corruption", ["hash", "canonical", "domain", "message"])
def test_bad_pending_original_is_not_frozen(tmp_path, corruption):
    """Validate original bytes and namespace before creating any persisted snapshot."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    record = capture(ledger, 1)
    payload = ledger.pending(DOMAIN)[0][0]
    if corruption == "canonical":
        payload += b" "
    elif corruption == "domain":
        payload = encode_chat_original({**record, "characterSyncId": "f" * 32})
    elif corruption == "message":
        payload = encode_chat_original({**record, "messageId": "f" * 32})
    digest = "f" * 64 if corruption == "hash" else hashlib.sha256(payload).hexdigest()
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("UPDATE chat_originals SET payload=?, sha256=?", (payload, digest))
    with pytest.raises(ValueError, match="invalid_chat_batch"):
        ledger.seal_batch(DOMAIN, byte_budget=4096)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_batches").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chat_pending").fetchone()[0] == 1


@pytest.mark.parametrize("target", ["snapshot", "pending"])
def test_deep_json_corruption_has_fixed_error_without_resealing(tmp_path, target):
    """A correct hash cannot make parser recursion failures escape the batch boundary."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    capture(ledger, 1)
    depth = sys.getrecursionlimit() + 100
    nested = b"[" * depth + b"null" + b"]" * depth
    if target == "snapshot":
        batch = ledger.seal_batch(DOMAIN, byte_budget=4096)
        payload = nested
        with sqlite3.connect(ledger.path) as conn:
            conn.execute(
                "UPDATE chat_batches SET sealed_bytes=?, sha256=?",
                (payload, hashlib.sha256(payload).hexdigest()),
            )
    else:
        # Put nesting inside an original field so the shared decoder is reached.
        payload = ledger.pending(DOMAIN)[0][0].replace(b'"text":"1"', b'"text":' + nested)
        with sqlite3.connect(ledger.path) as conn:
            conn.execute(
                "UPDATE chat_originals SET payload=?, sha256=?",
                (payload, hashlib.sha256(payload).hexdigest()),
            )
    with pytest.raises(ValueError, match="invalid_chat_batch"):
        ledger.seal_batch(DOMAIN, byte_budget=len(payload) + 4096)
    with sqlite3.connect(ledger.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_pending").fetchone()[0] == 1
        row = conn.execute("SELECT batch_id FROM chat_batches").fetchone()
        assert row == (batch.batch_id,) if target == "snapshot" else row is None


def test_sealed_bytes_do_not_follow_mutations_of_original_table(tmp_path):
    """The snapshot owns its bytes instead of reconstructing them from mutable joins."""
    ledger = ChatSyncLedger(tmp_path / "sync.db")
    record = capture(ledger, 1)
    batch = ledger.seal_batch(DOMAIN, byte_budget=4096)
    changed = encode_chat_original({**record, "text": "changed synthetic original"})
    with sqlite3.connect(ledger.path) as conn:
        conn.execute("UPDATE chat_originals SET payload=?, sha256=?", (changed, hashlib.sha256(changed).hexdigest()))
    assert ledger.seal_batch(DOMAIN, byte_budget=4096) == batch
    assert decode_chat_original(batch.members[0][1])["text"] == "1"


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, None])
def test_invalid_byte_budget_is_rejected(tmp_path, budget):
    """The caller must explicitly choose a positive integer original-byte budget."""
    with pytest.raises(ValueError, match="invalid_batch_byte_budget"):
        ChatSyncLedger(tmp_path / "sync.db").seal_batch(DOMAIN, byte_budget=budget)


@pytest.mark.parametrize("limit", [0, -1, 201, True, 1.5])
def test_invalid_count_limit_is_rejected(tmp_path, limit):
    """The first-release batch count never exceeds 200 or coerces booleans."""
    with pytest.raises(ValueError, match="invalid_page_size"):
        ChatSyncLedger(tmp_path / "sync.db").seal_batch(DOMAIN, byte_budget=4096, limit=limit)
