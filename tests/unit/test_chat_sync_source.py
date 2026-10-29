"""Proven new originals use disposable WAL files and synthetic owners only."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from services.chat_sync.ledger import ChatDomain, ChatOriginal, ChatSyncLedger
from services.chat_sync.protocol import MAX_ORIGIN_SEQ
from services.chat_sync.runtime import DesktopSyncState
from services.chat_sync.trace import ChatSyncTrace
from services.chat_sync.writer import registered_writer, WriterQuarantined
import services.chat_sync.source as source_module
from services.chat_sync.source import AssistantOriginalBuffer, ChatOriginalSource, mark_turn_origin, save_original
from tests.fake_clock import patch_module_clock

OWNER = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
UID = "a" * 32
DOMAIN = ChatDomain(OWNER, UID)
RAW = ChatOriginal("user", "  原文\r\n🙂e\u0301  ", original_time="1234567890123", timezone="UTC")


@pytest.fixture(autouse=True)
def no_telemetry(monkeypatch):
    """Keep actual producer tests isolated from the user's telemetry singleton."""
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", Mock())


def make_runtime(tmp_path, *, bound=True):
    """Exercise real source, consent and ledger transactions without any HTTP."""
    state = DesktopSyncState(tmp_path / "state.sqlite3")
    if bound:
        state.prepare_binding(DOMAIN)
    return SimpleNamespace(state=state, ledger=ChatSyncLedger(tmp_path / "originals.sqlite3"),
                           writer=registered_writer(tmp_path), wake_scheduler=Mock())


def source_for(runtime):
    """Locate only the dedicated synthetic source database."""
    return ChatOriginalSource(Path(runtime.state.path).parent / "sources.sqlite3")


def pending_rows(source):
    """Read bound originals still marked pending, in durable insertion order."""
    with closing(sqlite3.connect(source.path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM source_originals WHERE pending=1 ORDER BY rowid")]


def legacy_local_reply(source, uid, record_id, original, origin="agent"):
    """Recreate a local-only reply row written by an earlier producer."""
    payload = json.dumps(asdict(original), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    with closing(sqlite3.connect(source.path)) as connection, connection:
        connection.execute("INSERT INTO source_local_replies (record_id,uid,origin,original,original_hash) VALUES (?,?,?,?,?)",
                           (record_id, uid, origin, payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()))


def test_archive_batch_uses_one_transaction_and_rolls_back_conflicting_records(tmp_path, monkeypatch):
    source = source_for(make_runtime(tmp_path, bound=False))
    connect, statements = source._connect, []
    def counted_connection():
        connection = connect()
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(source, "_connect", counted_connection)
    trace = Mock()
    monkeypatch.setattr(source_module, "get_trace", lambda: trace)
    records = [(f"{index:032x}", RAW) for index in range(1, 41)]
    records.append(("f" * 32, ChatOriginal("assistant", "assistant text")))
    source.commit_history(UID, records)
    assert statements.count("BEGIN IMMEDIATE") == statements.count("COMMIT") == 1
    assert len(source.stored_ids(UID)) == 41
    assert trace.emit.call_count == 41
    before = source.stored_ids(UID)
    with pytest.raises(ValueError, match="identity_conflict"):
        source.commit_history(UID, [("e" * 32, RAW), ("f" * 32, RAW)])
    assert source.stored_ids(UID) == before
    assert trace.emit.call_count == 41


@pytest.mark.parametrize("bound", [False, True])
def test_local_only_identity_cannot_be_promoted_to_a_sync_original(tmp_path, bound):
    """Retries cannot reclassify a fixed local-only identity into an upload lane."""
    source = ChatOriginalSource(tmp_path / "sources.sqlite3")
    raw = ChatOriginal("assistant", RAW.text)
    legacy_local_reply(source, UID, "5" * 32, raw)
    with pytest.raises(ValueError, match="chat_source_identity_conflict"):
        if bound:
            source.commit(OWNER, UID, "5" * 32, raw)
        else:
            source.commit_unbound(UID, "5" * 32, raw)
    assert source.read_local_replies(source.path, UID)[0]["text"] == RAW.text


def test_local_only_read_rejects_changed_bytes_and_does_not_initialize_missing_store(tmp_path):
    """Only the saved proof is restored; a read never mints a new source database."""
    missing = tmp_path / "missing.sqlite3"
    assert ChatOriginalSource.read_local_replies(missing, UID) == []
    assert not missing.exists()
    source = ChatOriginalSource(tmp_path / "sources.sqlite3")
    legacy_local_reply(source, UID, "5" * 32, ChatOriginal("assistant", "original"))
    with sqlite3.connect(source.path) as conn:
        conn.execute("UPDATE source_local_replies SET original=replace(original,?,?)", ("original", "changed"))
    with pytest.raises(ValueError, match="chat_source_integrity_error"):
        ChatOriginalSource.read_local_replies(source.path, UID)


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.asyncio
async def test_local_only_restore_keeps_existing_writer_visibility_fence(tmp_path, monkeypatch, trusted):
    """Copied authority cannot expose recovered local replies in a new process."""
    runtime = make_runtime(tmp_path)
    legacy_local_reply(source_for(runtime), UID, "5" * 32, ChatOriginal("assistant", RAW.text))
    monkeypatch.setattr(runtime.writer, "check", Mock(side_effect=WriterQuarantined()))
    monkeypatch.setattr(runtime.writer, "local_visibility_trusted", Mock(return_value=trusted))
    assert len(await source_module.local_replies(runtime, UID)) == (1 if trusted else 0)


@pytest.mark.asyncio
async def test_buffer_discard_terminal_exclusion_and_retry(monkeypatch):
    """Temporary output is discarded, while a failed durable save can be retried."""
    local = AsyncMock(side_effect=[OSError("synthetic fault"), "5" * 32])
    monkeypatch.setattr(source_module, "save_original", local)
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": "chat"}
    buffer = AssistantOriginalBuffer()
    message = {"chat_original_source": proof, "text": RAW.text}
    buffer.append(message)
    buffer.discard()
    await buffer.finish("Lan")
    buffer.append(message)
    await buffer.finish("Lan", proof["recordId"], excluded=True)
    local.assert_not_awaited()
    retry = {**proof, "recordId": "6" * 32}
    buffer.append({"chat_original_source": retry, "text": RAW.text})
    await buffer.finish("Lan", retry["recordId"])
    assert retry["recordId"] in buffer.records
    await buffer.finish("Lan", retry["recordId"])
    assert local.await_count == 2
    assert buffer.records == {}


def test_source_commit_retries_keep_raw_bytes_and_arrival_time(tmp_path, monkeypatch):
    """No matching by normalized text, timestamp or mutable projection is allowed."""
    source = ChatOriginalSource(tmp_path / "source.sqlite3")
    identity = "1" * 32
    patch_module_clock(monkeypatch, source_module, time_ns=lambda: 100_000_000)
    source.commit(OWNER, UID, identity, RAW)
    first = pending_rows(source)[0]
    assert first["queued_at_ms"] == 100
    patch_module_clock(monkeypatch, source_module, time_ns=lambda: 900_000_000)
    source.commit(OWNER, UID, identity, RAW)
    reopened = ChatOriginalSource(source.path)
    assert pending_rows(reopened) == [first]
    assert ChatOriginal(**json.loads(first["original"])) == RAW
    assert first["record_id"] == identity
    with pytest.raises(ValueError, match="chat_source_identity_conflict"):
        source.commit(OWNER, UID, identity, ChatOriginal("user", RAW.text.strip()))
    with pytest.raises(ValueError, match="chat_source_identity_conflict"):
        source.commit(OTHER, UID, identity, RAW)
    assert len(pending_rows(source)) == 1


def test_concurrent_commits_keep_one_identity_and_atomic_failure_leaves_no_record(tmp_path):
    """SQLite serialization prevents duplicate raw/pending rows across writers."""
    source = ChatOriginalSource(tmp_path / 'source.sqlite3')
    identity = '8' * 32

    def commit_same_original(_index):
        """Construct independent handles to exercise real SQLite locking."""
        ChatOriginalSource(source.path).commit(OWNER, UID, identity, RAW)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(commit_same_original, range(24)))
    assert len(pending_rows(source)) == 1
    with sqlite3.connect(source.path) as connection:
        connection.execute("CREATE TRIGGER fail_source BEFORE INSERT ON source_originals "
                           "BEGIN SELECT RAISE(ABORT, 'synthetic rollback'); END")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic rollback'):
        source.commit(OWNER, UID, '9' * 32, RAW)
    assert [row["record_id"] for row in pending_rows(source)] == [identity]
    with sqlite3.connect(source.path) as connection:
        # Idempotent retries and rolled-back raw inserts never consume order.
        assert connection.execute("SELECT next_seq FROM source_origin_streams WHERE uid=?", (UID,)).fetchone() == (2,)


def test_source_sequence_exhaustion_keeps_stream_and_rejects_next_raw_commit(tmp_path):
    """The final safe sequence commits once without wrapping or creating a new stream."""
    source = ChatOriginalSource(tmp_path / "sources.sqlite3")
    source.commit_unbound(UID, "1" * 32, RAW)
    with sqlite3.connect(source.path) as connection:
        stream = connection.execute("SELECT stream_id FROM source_origin_streams WHERE uid=?", (UID,)).fetchone()[0]
        connection.execute("UPDATE source_origin_streams SET next_seq=? WHERE uid=?", (MAX_ORIGIN_SEQ, UID))
    source.commit(OWNER, UID, "2" * 32, RAW)
    assert pending_rows(source)[0]["origin_seq"] == MAX_ORIGIN_SEQ
    with pytest.raises(ValueError, match="^source_sequence_exhausted$"):
        source.commit_unbound(UID, "3" * 32, RAW)
    with sqlite3.connect(source.path) as connection:
        assert connection.execute("SELECT stream_id,next_seq FROM source_origin_streams WHERE uid=?", (UID,)).fetchone() == (stream, MAX_ORIGIN_SEQ + 1)
        assert connection.execute("SELECT count(*) FROM source_unbound WHERE record_id=?", ("3" * 32,)).fetchone()[0] == 0


def test_legacy_source_migration_keeps_unknown_queue_age(tmp_path):
    """A pre-clock WAL remains readable without pretending it arrived today."""
    runtime = make_runtime(tmp_path)
    path = tmp_path / "sources.sqlite3"
    payload = json.dumps(asdict(RAW), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    identity = "b" * 32
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE source_originals (record_id TEXT PRIMARY KEY, owner TEXT NOT NULL, uid TEXT NOT NULL, "
                     "original TEXT, original_hash TEXT NOT NULL, pending INTEGER NOT NULL DEFAULT 1, "
                     "deleted INTEGER NOT NULL DEFAULT 0)")
        conn.execute("INSERT INTO source_originals VALUES (?,?,?,?,?,1,0)",
                     (identity, OWNER, UID, payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()))
    source = source_for(runtime)
    from services.chat_sync.writer import WriterQuarantined
    with pytest.raises(WriterQuarantined):
        source.stored_ids(UID)
    with sqlite3.connect(source.path) as conn:
        row = conn.execute("SELECT queued_at_ms,origin_seq,origin_stream_id FROM source_originals").fetchone()
        assert row == (None, None, None)
        assert conn.execute("SELECT count(*) FROM source_origin_streams").fetchone()[0] == 0
    # Unknown historical provenance is retained; a missing writer head never
    # self-authorizes legacy upload or backfills an arrival clock/origin.



@pytest.mark.asyncio
async def test_save_preserves_unbound_proof_without_upload_and_keeps_paused_owner(tmp_path, monkeypatch):
    """Approved offline originals survive before binding without guessing an owner."""
    runtime = make_runtime(tmp_path, bound=False)
    manager = SimpleNamespace(aload_characters=AsyncMock(return_value={"猫娘": {
        "Lan": {"_reserved": {"character_uid": UID}},
    }}))
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    assert await save_original("Lan", "user", RAW.text, config_manager=manager) is None
    assert not (tmp_path / "sources.sqlite3").exists()
    runtime.state.save_choice(UID, True)
    unbound_id = await save_original("Lan", "user", RAW.text, config_manager=manager)
    assert len(unbound_id) == 32
    with sqlite3.connect(tmp_path / "sources.sqlite3") as connection:
        row = connection.execute("SELECT record_id,original FROM source_unbound").fetchone()
    assert row[0] == unbound_id and json.loads(row[1])["text"] == RAW.text
    assert pending_rows(source_for(runtime)) == []
    runtime.wake_scheduler.assert_not_called()
    runtime.state.prepare_binding(DOMAIN)
    runtime.state.pause(UID)
    identity = await save_original("Lan", "user", RAW.text, original_time=RAW.original_time, config_manager=manager)
    assert len(identity) == 32
    assert runtime.state.binding(UID)["enabled"] == 0
    assert runtime.ledger.pending(DOMAIN) == []
    row = pending_rows(source_for(runtime))[0]
    assert row["owner"] == OWNER
    assert row["record_id"] == identity
    original = ChatOriginal(**json.loads(row["original"]))
    assert original.text == RAW.text
    assert original.original_time == RAW.original_time
    runtime.wake_scheduler.assert_called_once_with()


@pytest.mark.asyncio
async def test_save_keeps_pending_when_ledger_temporarily_fails(tmp_path, monkeypatch):
    """Local raw persistence does not depend on HTTP or a successful ledger pass."""
    runtime = make_runtime(tmp_path)
    manager = SimpleNamespace(aload_characters=AsyncMock(return_value={"猫娘": {
        "Lan": {"_reserved": {"character_uid": UID}},
    }}))
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    runtime.ledger.capture = Mock(side_effect=RuntimeError("synthetic unavailable"))
    identity = await save_original("Lan", "user", RAW.text, config_manager=manager)
    assert pending_rows(source_for(runtime))[0]["record_id"] == identity
    runtime.wake_scheduler.assert_called_once_with()
    runtime.ledger.capture.assert_not_called()


@pytest.mark.asyncio
async def test_save_does_not_wake_before_original_commit_succeeds(tmp_path, monkeypatch):
    """An unsuccessful raw commit creates no work for the automatic worker."""
    runtime = make_runtime(tmp_path)
    manager = SimpleNamespace(aload_characters=AsyncMock(return_value={"猫娘": {
        "Lan": {"_reserved": {"character_uid": UID}},
    }}))
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(ChatOriginalSource, "commit", Mock(side_effect=sqlite3.OperationalError("synthetic disk failure")))
    with pytest.raises(sqlite3.OperationalError, match="synthetic disk failure"):
        await save_original("Lan", "user", RAW.text, config_manager=manager)
    runtime.wake_scheduler.assert_not_called()
    assert pending_rows(source_for(runtime)) == []
    assert runtime.ledger.pending(DOMAIN) == []


@pytest.mark.asyncio
async def test_assistant_buffer_keeps_exact_chunks_and_independent_interleaved_ids(monkeypatch):
    """A late completion only seals its own message and leaves another reply intact."""
    save = AsyncMock()
    monkeypatch.setattr(source_module, "save_original", save)
    manager = SimpleNamespace()
    old = mark_turn_origin(manager, "old", "chat")
    newer = mark_turn_origin(manager, "new", "proactive")
    assert mark_turn_origin(manager, "old", "agent") == old
    buffer = AssistantOriginalBuffer()
    for proof, text in [(old, "  old\n"), (newer, "new"), (old, "🙂  ")]:
        buffer.append({"chat_original_source": proof, "text": text})
    await buffer.finish("Lan", old["recordId"])
    save.assert_awaited_once_with("Lan", "assistant", "  old\n🙂  ",
                                  record_id=old["recordId"], original_time=old["originalTime"])
    assert list(buffer.records) == [newer["recordId"]]
    await buffer.finish("Lan", newer["recordId"])
    assert save.await_count == 2


@pytest.mark.parametrize("origin", ["agent", "avatar", "tool", "mirror", None])
@pytest.mark.asyncio
async def test_explicit_nonchat_and_unknown_assistant_sources_are_not_uploaded(monkeypatch, origin):
    """Only ordinary/private output enters upload lanes."""
    save = AsyncMock()
    monkeypatch.setattr(source_module, "save_original", save)
    buffer = AssistantOriginalBuffer()
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": origin}
    buffer.append({"chat_original_source": proof, "text": "hidden tool reply"})
    buffer.append({"text": "legacy projection"})
    await buffer.finish("Lan")
    save.assert_not_awaited()
    assert buffer.records == {}


@pytest.mark.asyncio
async def test_discarded_or_avatar_terminal_output_is_not_saved(monkeypatch):
    """Discard follows existing chat persistence and never keeps temporary tokens."""
    save = AsyncMock()
    monkeypatch.setattr(source_module, "save_original", save)
    proof = mark_turn_origin(SimpleNamespace(), "reply", "chat")
    buffer = AssistantOriginalBuffer()
    message = {"chat_original_source": proof, "text": "temporary"}
    buffer.append(message)
    buffer.discard()
    await buffer.finish("Lan")
    buffer.append(message)
    await buffer.finish("Lan", proof["recordId"], excluded=True)
    save.assert_not_awaited()
    assert buffer.records == {}


def test_source_diagnostic_records_first_fixed_origin_only(monkeypatch):
    """A later ordinary send cannot relabel an excluded callback reply."""
    trace = Mock(enabled=True)
    monkeypatch.setattr(source_module, "get_trace", lambda: trace)
    manager = SimpleNamespace()
    proof = mark_turn_origin(manager, "synthetic-turn", "agent")
    assert mark_turn_origin(manager, "synthetic-turn", "chat") == proof
    trace.emit.assert_called_once_with(
        "assistant_source_selected", message_id=proof["recordId"], source_origin="agent",
    )


@pytest.mark.asyncio
async def test_source_diagnostic_distinguishes_finish_and_preserves_retry(monkeypatch):
    """Optional evidence distinguishes missing, failed and accepted raw saves."""
    trace, save = Mock(enabled=True), AsyncMock(side_effect=OSError("private-error"))
    monkeypatch.setattr(source_module, "get_trace", lambda: trace)
    monkeypatch.setattr(source_module, "save_original", save)
    proof = {"recordId": "e" * 32, "originalTime": "123", "origin": "chat"}
    buffer = AssistantOriginalBuffer()
    buffer.append({"chat_original_source": proof, "text": "private-original"})
    await buffer.finish("private-role", proof["recordId"])
    assert proof["recordId"] in buffer.records
    trace.emit.assert_called_with(
        "assistant_source_finished", message_id=proof["recordId"], source_origin="chat",
        source_reason="save_failed", outcome="error",
    )
    save.side_effect = None
    save.return_value = None
    await buffer.finish("private-role", proof["recordId"])
    assert buffer.records == {}
    trace.emit.assert_called_with(
        "assistant_source_finished", message_id=proof["recordId"], source_origin="chat",
        source_reason="save_unavailable", outcome="error",
    )
    saved = {**proof, "recordId": "f" * 32}
    buffer.append({"chat_original_source": saved, "text": "private-original"})
    save.return_value = saved["recordId"]
    await buffer.finish("private-role", saved["recordId"])
    trace.emit.assert_called_with(
        "assistant_source_finished", message_id=saved["recordId"], source_origin="chat",
        source_reason="saved", outcome="success",
    )
    excluded = {**proof, "recordId": "d" * 32}
    buffer.append({"chat_original_source": excluded, "text": "private-original"})
    await buffer.finish("private-role", excluded["recordId"], excluded=True)
    trace.emit.assert_called_with(
        "assistant_source_finished", message_id=excluded["recordId"], source_origin="chat",
        source_reason="terminal_excluded", outcome="success",
    )
    assert save.await_count == 3
    assert "private-" not in str(trace.emit.call_args_list)


@pytest.mark.asyncio
async def test_transcript_after_terminal_joins_the_same_original_within_grace(monkeypatch):
    """Gemini text after turn_complete is saved with its reply, under the bubble's own ID."""
    remote = AsyncMock(return_value="5" * 32)
    monkeypatch.setattr(source_module, "save_original", remote)
    monkeypatch.setattr(source_module, "character_uid_for", AsyncMock(return_value=UID))
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": "chat"}
    buffer = AssistantOriginalBuffer(tail_grace=0.05)
    buffer.append({"chat_original_source": proof, "text": "first half"})
    await buffer.finish("Lan", proof["recordId"])
    remote.assert_not_awaited()
    for text in (" late", " tail"):
        buffer.append({"chat_original_source": proof, "text": text})
    # The pending reply survives a later response's discard.
    buffer.discard()
    await asyncio.sleep(0.2)
    remote.assert_awaited_once_with("Lan", "assistant", "first half late tail",
                                    record_id=proof["recordId"], original_time="123", character_uid=UID)
    # Text after sealing cannot reuse the immutable identity, even after a discard.
    buffer.discard()
    buffer.append({"chat_original_source": proof, "text": " too late"})
    await buffer.finish("Lan")
    remote.assert_awaited_once()
    assert buffer.records == {}


@pytest.mark.asyncio
async def test_chunk_during_save_is_dropped_and_failed_save_stays_retryable(monkeypatch):
    """The identity closes before the save awaits; a failure reopens it for retry."""
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": "chat"}
    buffer = AssistantOriginalBuffer()
    calls = []

    async def save(name, role, text, *, record_id, original_time, character_uid=None):
        calls.append(text)
        buffer.append({"chat_original_source": proof, "text": " mid-save"})
        if len(calls) == 1:
            raise OSError("synthetic fault")
        return record_id

    monkeypatch.setattr(source_module, "save_original", save)
    buffer.append({"chat_original_source": proof, "text": "reply"})
    await buffer.finish("Lan", proof["recordId"])
    assert proof["recordId"] in buffer.records
    await buffer.finish("Lan", proof["recordId"])
    assert calls == ["reply", "reply"]
    assert buffer.records == {}


@pytest.mark.asyncio
async def test_rename_during_grace_still_saves_under_the_pinned_uid(monkeypatch):
    """The UID is resolved at the terminal, so a rename before the seal loses nothing."""
    remote = AsyncMock(return_value="5" * 32)
    monkeypatch.setattr(source_module, "save_original", remote)
    resolve = AsyncMock(return_value=UID)
    monkeypatch.setattr(source_module, "character_uid_for", resolve)
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": "chat"}
    buffer = AssistantOriginalBuffer(tail_grace=30)
    buffer.append({"chat_original_source": proof, "text": "reply"})
    await buffer.finish("Old name", proof["recordId"])
    # After a rename the old name resolves to nothing.
    resolve.return_value = None
    await buffer.flush_terminated()
    assert remote.await_args.kwargs["character_uid"] == UID
    resolve.assert_awaited_once_with("Old name")


@pytest.mark.asyncio
async def test_repeated_terminal_keeps_the_stricter_exclusion(monkeypatch):
    """A later ordinary terminal cannot undo an earlier exclusion of the same reply."""
    remote = AsyncMock(return_value="5" * 32)
    monkeypatch.setattr(source_module, "save_original", remote)
    monkeypatch.setattr(source_module, "character_uid_for", AsyncMock(return_value=UID))
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": "chat"}
    buffer = AssistantOriginalBuffer(tail_grace=30)
    buffer.append({"chat_original_source": proof, "text": "avatar"})
    await buffer.finish("Lan", proof["recordId"], excluded=True)
    buffer.append({"chat_original_source": proof, "text": " late"})
    await buffer.finish("Lan", proof["recordId"])
    await buffer.flush_terminated()
    remote.assert_not_awaited()
    assert buffer.records == {}


@pytest.mark.asyncio
async def test_deferred_seal_keeps_excluded_decision(monkeypatch):
    """Waiting for late text never turns an excluded reply into an upload."""
    remote = AsyncMock(return_value="x")
    monkeypatch.setattr(source_module, "save_original", remote)
    monkeypatch.setattr(source_module, "character_uid_for", AsyncMock(return_value=UID))
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": "chat"}
    buffer = AssistantOriginalBuffer(tail_grace=30)
    buffer.append({"chat_original_source": proof, "text": "reply"})
    await buffer.finish("Lan", proof["recordId"], excluded=True)
    buffer.append({"chat_original_source": proof, "text": " late"})
    # Shutdown seals completed replies at once instead of waiting out the grace.
    await buffer.flush_terminated()
    remote.assert_not_awaited()
    assert buffer.records == {}


@pytest.mark.parametrize("kind,reason", [
    ("mirror", "mirror"), ("unknown", "unknown_origin"),
    ("invalid_id", "invalid_identity"), ("invalid_text", "invalid_text"),
])
def test_source_diagnostic_does_not_infer_unknown_or_mirror_sources(monkeypatch, kind, reason):
    """Diagnostic identities never come from text or arbitrary untrusted values."""
    trace = Mock(enabled=True)
    monkeypatch.setattr(source_module, "get_trace", lambda: trace)
    proof = {"recordId": "d" * 32, "originalTime": "123", "origin": "chat"}
    message = {"chat_original_source": proof, "text": "private-original"}
    if kind == "mirror":
        message["metadata"] = {"mirror": {"text": "private-mirror"}}
    elif kind == "unknown":
        message.pop("chat_original_source")
    elif kind == "invalid_id":
        proof["recordId"] = "private-id"
    else:
        message["text"] = {"body": "private-original"}
    buffer = AssistantOriginalBuffer()
    buffer.append(message)
    assert buffer.records == {}
    trace.emit.assert_called_once_with(
        "assistant_source_excluded", message_id=None if kind in ("unknown", "invalid_id") else "d" * 32,
        source_origin=None if kind == "unknown" else "chat", source_reason=reason,
    )
    assert "private-" not in str(trace.emit.call_args_list)


@pytest.mark.asyncio
async def test_disabled_or_failed_source_diagnostic_does_not_change_behavior(tmp_path, monkeypatch):
    """Unavailable observation files cannot change source choice or consume a save."""
    for path in (None, tmp_path / "absent-directory" / "trace.jsonl"):
        trace = ChatSyncTrace(path)
        save = AsyncMock(return_value="e" * 32)
        monkeypatch.setattr(source_module, "get_trace", lambda: trace)
        monkeypatch.setattr(source_module, "save_original", save)
        proof = mark_turn_origin(SimpleNamespace(), "synthetic-turn", "chat")
        buffer = AssistantOriginalBuffer()
        buffer.append({"chat_original_source": proof, "text": "private-original"})
        await buffer.finish("private-role", proof["recordId"])
        save.assert_awaited_once_with("private-role", "assistant", "private-original",
                                      record_id=proof["recordId"], original_time=proof["originalTime"])
        assert buffer.records == {}
        if path is None:
            assert trace._sequence == 0
        else:
            assert trace._sequence == 2
            assert not path.exists()


@pytest.mark.parametrize('composer_source', ['', 'react-chat-window', 'legacy-text-button'])
@pytest.mark.asyncio
async def test_real_turn_input_preserves_full_text_without_waiting_for_reply(monkeypatch, composer_source):
    """The real accepted input pipeline saves a standalone user under its frozen ID."""
    from tests.unit.test_core_game_route_memory_contract import _make_transcript_manager
    from main_logic.core.turn import TurnMixin

    manager = _make_transcript_manager()
    manager._config_manager = object()
    manager._active_text_request_id = "later-request"
    save = AsyncMock(return_value="6" * 32)
    monkeypatch.setattr("main_logic.core.turn.save_original", save)
    assert await TurnMixin.handle_input_transcript(
        manager, RAW.text, is_voice_source=False,
        chat_original_text=RAW.text, chat_request_id="accepted-request",
        source=composer_source,
    )
    assert save.await_args.args == ("Lan", "user", RAW.text)
    assert save.await_args.kwargs["original_time"].isdigit()
    assert manager.sync_message_queue.messages[-1]["data"]["data"] == RAW.text.strip()
    proof = manager.sync_message_queue.messages[-1]["data"]["chat_original_source"]
    assert proof['recordId'] == save.await_args.kwargs['record_id']
    assert proof['originalTime'] == save.await_args.kwargs['original_time']


@pytest.mark.asyncio
async def test_real_turn_final_asr_and_explicit_nonchat_source(monkeypatch):
    """Only admitted ordinary final ASR gets raw storage; synthetic input stays out."""
    from tests.unit.test_core_game_route_memory_contract import _make_transcript_manager
    from main_logic.core.turn import TurnMixin

    manager = _make_transcript_manager()
    manager._config_manager = object()
    manager._inject_pending_user_directives = AsyncMock()
    manager._dispatch_mini_game_invite_keyword = AsyncMock()
    manager._broadcast_voice_transcript_observed = AsyncMock()
    manager._fire_task = lambda coroutine: coroutine.close()
    save = AsyncMock(return_value="7" * 32)
    monkeypatch.setattr("main_logic.core.turn.save_original", save)
    assert await TurnMixin.handle_input_transcript(manager, "  最终转写内容。  ", source_game_route_identity=None)
    assert save.await_args.args == ("Lan", "user", "最终转写内容。")
    save.reset_mock()
    await TurnMixin.handle_input_transcript(manager, "avatar description", is_voice_source=False, source="avatar_drop")
    save.assert_not_awaited()
    await TurnMixin.handle_input_transcript(manager, "synthetic scaffold", is_voice_source=False, chat_input_source="avatar-drop")
    save.assert_not_awaited()
    assert 'source' not in manager.sync_message_queue.messages[-1]['data']


@pytest.mark.asyncio
async def test_real_output_and_late_turn_end_share_frozen_message_identity(monkeypatch):
    """Actual chunk publication carries one identity through an interleaved close."""
    from tests.unit.test_core_game_route_memory_contract import _make_manager
    from main_logic.core.turn import TurnMixin

    manager = _make_manager()
    save = AsyncMock()
    monkeypatch.setattr(source_module, "save_original", save)
    await TurnMixin.send_lanlan_response(manager, "  完整\n", turn_id="old-speech")
    manager.current_speech_id = "new-speech"
    await TurnMixin.send_lanlan_response(manager, "新回复", turn_id="new-speech")
    await TurnMixin.send_lanlan_response(manager, "🙂  ", turn_id="old-speech")
    messages = [item["data"] for item in manager.sync_message_queue.messages]
    record_ids = [message["chat_original_source"]["recordId"] for message in messages]
    assert record_ids[0] == record_ids[2]
    assert record_ids[1] != record_ids[0]
    reply = SimpleNamespace(speech_id="old-speech", meta=None, turn_ended=False)
    terminal = TurnMixin._queue_turn_end(manager, "old-request", reply_turn=reply)
    assert terminal["chat_original_id"] == record_ids[0]
    buffer = AssistantOriginalBuffer()
    for message in messages:
        buffer.append(message)
    await buffer.finish("Lan", terminal["chat_original_id"])
    assert save.await_args.args == ("Lan", "assistant", "  完整\n🙂  ")
    assert list(buffer.records) == [record_ids[1]]


@pytest.mark.asyncio
async def test_actual_connector_saves_exact_reply_and_keeps_memory_projection_separate(monkeypatch):
    """Run the real consumer with fake memory I/O, never a listener or real account."""
    from main_logic import cross_server

    save = AsyncMock()
    memory = AsyncMock(return_value=(True, "", {}))
    monkeypatch.setattr(source_module, "save_original", save)
    monkeypatch.setattr(cross_server, "_post_memory_server", memory)
    monkeypatch.setattr(cross_server, "_publish_analyze_request_with_fallback", AsyncMock(return_value=True))
    queue = asyncio.Queue()
    ordinary = mark_turn_origin(SimpleNamespace(), "ordinary", "chat")
    excluded = mark_turn_origin(SimpleNamespace(), "avatar", "avatar")
    completed = asyncio.get_running_loop().create_future()
    for proof, text in [(ordinary, "  完整\n"), (ordinary, "🙂  ")]:
        queue.put_nowait({"type": "json", "data": {
            "type": "gemini_response", "text": text, "chat_original_source": proof,
            "chat_original_id": proof["recordId"],
        }})
    queue.put_nowait({"type": "system", "data": "turn end", "chat_original_id": ordinary["recordId"]})
    queue.put_nowait({"type": "json", "data": {
        "type": "gemini_response", "text": "mirror with memory enabled", "chat_original_source": ordinary,
        "metadata": {"mirror": {"event": {"hasUserText": True}}},
    }})
    queue.put_nowait({"type": "json", "data": {
        "type": "gemini_response", "text": "avatar tool reply", "chat_original_source": excluded,
        "chat_original_id": excluded["recordId"],
    }})
    queue.put_nowait({"type": "system", "data": "session end", "_memory_settlement_done": completed})
    connector = asyncio.create_task(cross_server.run_sync_connector(queue, "Lan", config={"monitor": False, "bullet": False}))
    try:
        await asyncio.wait_for(completed, 1)
    finally:
        connector.cancel()
        await asyncio.gather(connector, return_exceptions=True)
    save.assert_awaited_once_with("Lan", "assistant", "  完整\n🙂  ",
                                  record_id=ordinary["recordId"], original_time=ordinary["originalTime"])
    assert memory.await_count >= 1
    projected = str(memory.await_args.args[2])
    assert "avatar tool reply" in projected
    assert "🙂" not in projected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix,post_text",
    [
        ("", "Synthetic tool result050."),
        ("Ordinary preface050. ", "Synthetic tool result050."),
        ("Ordinary preface050. ", ""),
    ],
    ids=["tool-only", "mixed-after-visible-prefix", "close-without-post-tool-text"],
)
@pytest.mark.parametrize(
    "tool_error", [False, True], ids=["tool-success", "tool-failure"]
)
async def test_actual_tool_dispatch_syncs_complete_reply_text(
    tmp_path, monkeypatch, prefix, post_text, tool_error
):
    from tests.unit.test_tool_calling import _FakeLLM, _init_bare
    from tests.unit.test_core_game_route_memory_contract import _make_manager
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.core.turn import TurnMixin
    from main_logic.tool_calling import ToolDefinition, ToolRegistry
    from utils.llm_client import LLMStreamChunk

    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", Mock())
    monkeypatch.setattr(
        "httpx.AsyncClient.send",
        AsyncMock(side_effect=AssertionError("unexpected_http")),
    )
    runtime = make_runtime(tmp_path)
    config = SimpleNamespace(
        aload_characters=AsyncMock(
            return_value={"猫娘": {"Lan": {"_reserved": {"character_uid": UID}}}}
        )
    )
    monkeypatch.setattr(
        "services.chat_sync.runtime.get_desktop_chat_sync_runtime",
        AsyncMock(return_value=runtime),
    )
    original_save = source_module.save_original

    async def saved_original(name, text_role, text, **kwargs):
        return await original_save(
            name, text_role, text, config_manager=config, **kwargs
        )

    monkeypatch.setattr(source_module, "save_original", saved_original)
    seen = []

    async def echo(arguments):
        seen.append(arguments)
        if tool_error:
            raise RuntimeError("synthetic tool failure050")
        return {"value": "Synthetic tool result050"}

    definition = ToolDefinition(
        name="acceptance_echo050", description="Synthetic read-only echo", handler=echo
    )
    manager = _make_manager()
    manager.tool_registry = ToolRegistry()
    manager.tool_registry.register(definition)
    owner = TurnMixin._begin_reply_turn(
        manager, speech_id=manager.current_speech_id, request_id="source050"
    )
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    first = ([LLMStreamChunk(content=prefix)] if prefix else []) + [
        LLMStreamChunk(
            content="",
            tool_call_deltas=[
                {
                    "index": 0,
                    "id": "call050",
                    "type": "function",
                    "function": {
                        "name": definition.name,
                        "arguments": '{"sample":"050"}',
                    },
                }
            ],
        ),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]
    client.llm = _FakeLLM(
        [first, [LLMStreamChunk(content=post_text, finish_reason="stop")]]
    )
    client._tool_definitions = [definition]
    client.max_tool_iterations = 4
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False
    client._active_reply_owner = owner
    client.on_tool_call = manager._make_tool_call_handler(client)
    manager.session = client
    owner.session = client
    visible = []
    async for chunk in client._astream_with_tools(
        [{"role": "user", "content": "Invoke the synthetic echo."}]
    ):
        if chunk.content:
            await TurnMixin.send_lanlan_response(
                manager,
                chunk.content,
                is_first_chunk=not visible,
                turn_id=owner.speech_id,
            )
            visible.append(chunk.content)
    assert seen == [{"sample": "050"}], "Actual registry execution must be observed"
    terminal = TurnMixin._queue_turn_end(manager, "source050", reply_turn=owner)
    from main_logic import cross_server

    monkeypatch.setattr(
        cross_server, "_post_memory_server", AsyncMock(return_value=(True, "", {}))
    )
    monkeypatch.setattr(
        cross_server,
        "_publish_analyze_request_with_fallback",
        AsyncMock(return_value=True),
    )
    queue = asyncio.Queue()
    proofs = []
    for event in manager.sync_message_queue.messages:
        queue.put_nowait(event)
        if event["type"] == "json" and event["data"]["type"] == "gemini_response":
            proofs.append(event["data"]["chat_original_source"])
    completed = asyncio.get_running_loop().create_future()
    queue.put_nowait(
        {"type": "system", "data": "session end", "_memory_settlement_done": completed}
    )
    connector = asyncio.create_task(
        cross_server.run_sync_connector(
            queue, "Lan", config={"monitor": False, "bullet": False}
        )
    )
    try:
        await asyncio.wait_for(completed, 5)
    finally:
        connector.cancel()
        await asyncio.gather(connector, return_exceptions=True)
    source = source_for(runtime)
    local = source.read_local_replies(source.path, UID)
    report = {
        "scope": "scripted model, actual client tool loop, manager dispatch, actual connector, raw output and SQLite source store",
        "actual_tool_calls": len(seen),
        "visible_chunks": len(visible),
        "proof_origins": [p["origin"] for p in proofs],
        "local_reply_count": len(local),
        "pending_upload_sources": len(pending_rows(source)),
    }
    assert local == [], report
    pending = pending_rows(source)
    assert len(pending) == 1, report
    row = pending[0]
    assert json.loads(row["original"])["text"] == "".join(visible) == prefix + post_text
    assert row["record_id"] == terminal["chat_original_id"]
    assert seen == [{"sample": "050"}], "Reading or synchronizing history must not execute a tool again"
