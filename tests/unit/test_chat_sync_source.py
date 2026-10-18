"""Proven new originals use disposable WAL files and synthetic owners only."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
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
from services.chat_sync.source import (
    AssistantOriginalBuffer, ChatOriginalSource, known_source_ids, mark_turn_origin,
    purge_source_originals, recover_sources, save_original,
)
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


def test_purge_erases_recovery_and_outage_copies_but_keeps_identities(tmp_path):
    """A permanent local delete leaves no readable body in recovery or outage storage."""
    from services.chat_sync.recovery import save_outage
    runtime = make_runtime(tmp_path)
    source, directory = source_for(runtime), Path(runtime.state.path).parent
    candidate, outage = "1" * 32, "2" * 32
    with sqlite3.connect(source.path) as conn:
        conn.execute("INSERT INTO source_recovery_candidates (record_id,owner,uid,original,original_hash,payload,kind) "
                     "VALUES (?,?,?,?,?,?,'local')", (candidate, OWNER, UID, "candidate text", "h", b"candidate payload"))
    save_outage(directory, UID, outage, ChatOriginal("user", "outage text"))
    # Nothing else holds these IDs, so the recovery copy alone must make the purge run.
    source.purge(OWNER, UID, [candidate, outage])
    with sqlite3.connect(source.path) as conn:
        assert conn.execute("SELECT original,payload,deleted FROM source_recovery_candidates WHERE record_id=?",
                            (candidate,)).fetchone() == (None, None, 1)
    with sqlite3.connect(directory / "outage.sqlite3") as conn:
        assert conn.execute("SELECT original,deleted FROM outage_originals WHERE record_id=?",
                            (outage,)).fetchone() == (None, 1)


def test_deleted_cloud_role_reassignment_retains_local_history_without_old_requests(tmp_path):
    """An explicit new identity retains verified text, never the retired domain's grants or deletes."""
    from services.chat_sync.protocol import decode_chat_original, encode_chat_original
    runtime = make_runtime(tmp_path)
    source, new_uid = source_for(runtime), "b" * 32
    source.commit(OWNER, UID, "1" * 32, RAW)
    source.commit_unbound(UID, "2" * 32, RAW)
    source.commit_unbound(UID, "3" * 32, RAW)
    source.purge(OWNER, UID, ["3" * 32])
    source.commit_local_reply(UID, "4" * 32, ChatOriginal("assistant", "tool stays local"), "tool")
    record = {"schemaVersion": 1, "ownerId": OWNER, "characterSyncId": UID,
              "channel": "chat", "scope": "private-chat", "messageId": "5" * 32,
              "originStreamId": "6" * 32, "originSeq": 7, **RAW.fields()}
    runtime.ledger.apply_changes(DOMAIN, {"cursor": 1, "hasMore": False,
        "originals": [encode_chat_original(record).decode()], "deletedMessageIds": []}, after=0)
    old_ledger = runtime.ledger.visible_originals(DOMAIN)
    assert source.reassign_role(OWNER, UID, new_uid, runtime.ledger) == new_uid
    assert runtime.ledger.visible_originals(DOMAIN) == old_ledger
    assert source.pending(200) == []
    assert source.pending_deletions(new_uid, 200) == []
    visible = {row["messageId"]: row for row in source.read_role_originals(OWNER, new_uid)}
    assert set(visible) == {"1" * 32, "2" * 32, "5" * 32}
    assert all(row["characterSyncId"] == new_uid and row["text"] == RAW.text for row in visible.values())
    assert visible["5" * 32]["originStreamId"] == record["originStreamId"]
    assert visible["5" * 32]["originSeq"] == 7
    assert source.read_local_replies(source.path, new_uid)[0]["text"] == "tool stays local"
    binding = {"owner_id": OWNER, "character_uid": new_uid, "binding_id": "8" * 32, "generation": 1}
    task = source.prepare_history(binding, "https://sync.synthetic.test", "synthetic", "local", "cloud", device_id="9" * 32)
    assert task["count"] == 3
    task = source.approve_history(new_uid, task["task_id"])
    assert source.advance_history(task, binding) == 3
    for row, locator in source.pending(200):
        runtime.ledger.capture(ChatDomain(OWNER, new_uid), locator, ChatOriginal(**json.loads(row["original"])),
            message_id=row["record_id"], queue_lane="history", proven_source_origin=(row["origin_stream_id"], row["origin_seq"]))
    new_records = [decode_chat_original(raw) for raw in runtime.ledger.visible_originals(ChatDomain(OWNER, new_uid))]
    assert {row["messageId"] for row in new_records} == set(visible)
    assert runtime.ledger.pending_deletions(ChatDomain(OWNER, new_uid)) == []


def test_role_reassignment_is_fixed_idempotent_and_rejects_late_old_producer(tmp_path):
    """Crash recovery reuses one new UID, and cannot move a later role or publish old account text."""
    runtime = make_runtime(tmp_path)
    source, new_uid = source_for(runtime), "b" * 32
    source.commit(OWNER, UID, "1" * 32, RAW)
    source.reassign_role(OWNER, UID, new_uid, runtime.ledger)
    source.commit_unbound(new_uid, "2" * 32, RAW)
    reopened = ChatOriginalSource(source.path)
    assert reopened.reassign_role(OWNER, UID, new_uid, runtime.ledger) == new_uid
    assert reopened.reassignment(UID) == {"ownerId": OWNER, "oldUid": UID, "newUid": new_uid}
    assert len(reopened.read_role_originals(OWNER, new_uid)) == 2
    with pytest.raises(ValueError, match="chat_source_role_reassigned"):
        reopened.commit_unbound(UID, "3" * 32, RAW)
    with pytest.raises(ValueError, match="chat_source_role_reassigned"):
        reopened.commit(OWNER, UID, "3" * 32, RAW)
    with pytest.raises(ValueError, match="chat_source_role_reassigned"):
        reopened.commit_local_reply(UID, "3" * 32, ChatOriginal("assistant", "late"), "tool")
    with pytest.raises(ValueError, match="chat_sync_binding_conflict"):
        reopened.reassign_role(OWNER, UID, "c" * 32, runtime.ledger)
    with pytest.raises(ValueError, match="chat_sync_owner_mismatch"):
        reopened.reassign_role(OTHER, UID, new_uid, runtime.ledger)
    with pytest.raises(ValueError, match="chat_sync_owner_mismatch"):
        reopened.read_role_originals(OTHER, new_uid)


def test_role_reassignment_rolls_back_and_keeps_unknown_or_deleted_sources_unpublished(tmp_path):
    """A failed transfer leaves the old source intact; missing source order is never invented."""
    runtime = make_runtime(tmp_path)
    source, new_uid = source_for(runtime), "b" * 32
    source.commit(OWNER, UID, "1" * 32, RAW)
    source.commit_unbound(UID, "2" * 32, RAW)
    with sqlite3.connect(source.path) as connection:
        connection.execute("UPDATE source_unbound SET origin_seq=NULL,origin_stream_id=NULL WHERE record_id=?", ("2" * 32,))
        connection.execute("CREATE TRIGGER fail_reassignment BEFORE INSERT ON source_role_reassignments "
                           "BEGIN SELECT RAISE(ABORT, 'synthetic rollback'); END")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic rollback"):
        source.reassign_role(OWNER, UID, new_uid, runtime.ledger)
    assert source.known_ids(OWNER, UID) == ["1" * 32, "2" * 32]
    assert source.reassignment(UID) is None
    with sqlite3.connect(source.path) as connection:
        connection.execute("DROP TRIGGER fail_reassignment")
    source.reassign_role(OWNER, UID, new_uid, runtime.ledger)
    task = source.prepare_history({"owner_id": OWNER, "character_uid": new_uid}, "https://sync.synthetic.test",
        "synthetic", "local", "cloud", device_id="9" * 32)
    assert task["count"] == 1
    with sqlite3.connect(source.path) as connection:
        assert connection.execute("SELECT original FROM source_unbound WHERE record_id=?", ("2" * 32,)).fetchone()[0]


@pytest.mark.parametrize("lane", ["another_role", "local_tool"])
def test_role_reassignment_does_not_steal_another_roles_source_or_promote_tool_identity(tmp_path, lane):
    """A downloaded ID collision must roll back the transfer rather than change its provenance."""
    from services.chat_sync.protocol import encode_chat_original
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    if lane == "another_role":
        source.commit_unbound("c" * 32, "1" * 32, RAW)
    else:
        source.commit_local_reply(UID, "1" * 32, ChatOriginal("assistant", "tool stays local"), "tool")
    wire = {"schemaVersion": 1, "ownerId": OWNER, "characterSyncId": UID,
        "channel": "chat", "scope": "private-chat", "messageId": "1" * 32,
        "originStreamId": "6" * 32, "originSeq": 7, **RAW.fields()}
    runtime.ledger.apply_changes(DOMAIN, {"cursor": 1, "hasMore": False,
        "originals": [encode_chat_original(wire).decode()], "deletedMessageIds": []}, after=0)
    with pytest.raises(ValueError, match="chat_source_identity_conflict"):
        source.reassign_role(OWNER, UID, "b" * 32, runtime.ledger)
    assert source.reassignment(UID) is None
    if lane == "another_role":
        assert source.known_ids(None, "c" * 32) == ["1" * 32]
    else:
        assert source.read_local_replies(source.path, UID)[0]["text"] == "tool stays local"


def test_role_reassignment_rejects_conflicting_ledger_text_without_moving_sources(tmp_path):
    """A canonical but changed ledger body cannot silently replace proven source bytes."""
    from services.chat_sync.protocol import encode_chat_original
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    source.commit(OWNER, UID, "1" * 32, RAW)
    wire = {"schemaVersion": 1, "ownerId": OWNER, "characterSyncId": UID,
        "channel": "chat", "scope": "private-chat", "messageId": "1" * 32,
        "originStreamId": "6" * 32, "originSeq": 7, **RAW.fields(), "text": "different bytes"}
    runtime.ledger.apply_changes(DOMAIN, {"cursor": 1, "hasMore": False,
        "originals": [encode_chat_original(wire).decode()], "deletedMessageIds": []}, after=0)
    with pytest.raises(ValueError, match="chat_source_integrity_error"):
        source.reassign_role(OWNER, UID, "b" * 32, runtime.ledger)
    assert source.reassignment(UID) is None
    assert source.known_ids(OWNER, UID) == ["1" * 32]


def test_reassigned_sources_remain_pinned_to_the_original_account(tmp_path):
    """Neither a late bound producer nor another reassignment can adopt another account's source."""
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    source.reassign_role(OWNER, UID, "b" * 32, runtime.ledger)
    with pytest.raises(ValueError, match="chat_sync_owner_mismatch"):
        source.commit(OTHER, "b" * 32, "1" * 32, RAW)
    with pytest.raises(ValueError, match="chat_sync_owner_mismatch"):
        source.reassign_role(OTHER, "b" * 32, "c" * 32, runtime.ledger)


def test_unknown_history_stays_visible_across_repeated_reassignment_without_upload_permission(tmp_path):
    """Legacy source order stays unknown while its previous local ledger remains readable after a UID change."""
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    source.commit(OWNER, UID, "1" * 32, RAW)
    row, locator = source.pending(1)[0]
    runtime.ledger.capture(DOMAIN, locator, RAW, message_id=row["record_id"],
        proven_source_origin=(row["origin_stream_id"], row["origin_seq"]))
    with sqlite3.connect(source.path) as connection:
        connection.execute("UPDATE source_originals SET origin_stream_id=NULL,origin_seq=NULL WHERE uid=?", (UID,))
    source.reassign_role(OWNER, UID, "b" * 32, runtime.ledger)
    source.reassign_role(OWNER, "b" * 32, "c" * 32, runtime.ledger)
    visible = source.read_role_originals(OWNER, "c" * 32, runtime.ledger)
    assert [row["text"] for row in visible] == [RAW.text]
    task = source.prepare_history({"owner_id": OWNER, "character_uid": "c" * 32}, "https://sync.synthetic.test",
        "synthetic", "local", "cloud", device_id="9" * 32)
    assert task["count"] == 0
    source.purge(OWNER, "c" * 32, ["1" * 32])
    assert source.read_role_originals(OWNER, "c" * 32, runtime.ledger) == []


@pytest.mark.asyncio
async def test_late_optional_source_save_after_uid_reassignment_does_not_break_normal_chat(tmp_path, monkeypatch):
    """A producer holding an old config snapshot cannot recreate that domain or fail the primary conversation."""
    import threading
    runtime = make_runtime(tmp_path)
    runtime._commit_lock = threading.Lock()
    source_for(runtime).reassign_role(OWNER, UID, "b" * 32, runtime.ledger)
    manager = SimpleNamespace(aload_characters=AsyncMock(return_value={"猫娘": {
        "Lan": {"_reserved": {"character_uid": UID}},
    }}))
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    assert await save_original("Lan", "user", RAW.text, record_id="2" * 32,
        original_time=RAW.original_time, config_manager=manager) == "2" * 32
    assert source_for(runtime).known_ids(OWNER, UID) == []
    assert [row["text"] for row in source_for(runtime).read_role_originals(OWNER, "b" * 32)] == [RAW.text]
    assert source_for(runtime).pending(200) == []


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_late_reassigned_producer_observes_current_new_uid_grant_and_off(tmp_path, monkeypatch, enabled):
    """A saved On may capture a late producer normally; a newer Off leaves only unbound local text."""
    import threading
    runtime = make_runtime(tmp_path)
    runtime._commit_lock = threading.Lock()
    source = source_for(runtime)
    source.reassign_role(OWNER, UID, "b" * 32, runtime.ledger)
    prepared = runtime.state.prepare_binding(ChatDomain(OWNER, "b" * 32))
    runtime.state.activate(prepared, "c" * 32)
    runtime.state.save_choice("b" * 32, enabled)
    if not enabled:
        runtime.state.pause("b" * 32)
    manager = SimpleNamespace(aload_characters=AsyncMock(return_value={"猫娘": {
        "Lan": {"_reserved": {"character_uid": UID}},
    }}))
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    assert await save_original("Lan", "user", RAW.text, record_id="2" * 32,
        original_time=RAW.original_time, config_manager=manager) == "2" * 32
    assert len(source.pending(200)) == int(enabled)
    assert [row["text"] for row in source.read_role_originals(OWNER, "b" * 32)] == [RAW.text]


@pytest.mark.asyncio
async def test_late_local_tool_reply_follows_fixed_uid_lineage_without_uploading(tmp_path, monkeypatch):
    """A tool completion holding an old config stays visible locally after two cloud retirements."""
    import threading
    runtime = make_runtime(tmp_path)
    runtime._commit_lock = threading.Lock()
    source = source_for(runtime)
    source.reassign_role(OWNER, UID, "b" * 32, runtime.ledger)
    source.reassign_role(OWNER, "b" * 32, "c" * 32, runtime.ledger)
    manager = SimpleNamespace(aload_characters=AsyncMock(return_value={"猫娘": {
        "Lan": {"_reserved": {"character_uid": UID}},
    }}))
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    proof = {"recordId": "5" * 32, "originalTime": RAW.original_time, "origin": "tool"}
    assert await source_module.save_local_reply("Lan", RAW.text, proof, config_manager=manager) == proof["recordId"]
    assert source.read_local_replies(source.path, "c" * 32)[0]["text"] == RAW.text
    assert source.pending(200) == [] and source.known_ids(OWNER, "c" * 32) == []
    runtime.wake_scheduler.assert_not_called()


def test_local_only_reply_is_durable_exact_and_never_an_upload_candidate(tmp_path):
    """Local replies survive reopening without entering any source upload lane."""
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    raw = ChatOriginal("assistant", RAW.text, original_time=RAW.original_time, timezone="UTC")
    source.commit_local_reply(UID, "5" * 32, raw, "agent")
    source.commit_local_reply(UID, "5" * 32, raw, "agent")
    source.commit_local_reply("b" * 32, "6" * 32, raw, "tool")
    reopened = ChatOriginalSource(source.path)
    assert reopened.read_local_replies(source.path, UID) == [{
        "messageId": "5" * 32, "characterSyncId": UID, "role": "assistant",
        "text": RAW.text, "originalTime": RAW.original_time,
    }]
    assert reopened.pending(200) == []
    assert reopened.known_ids(OWNER, UID) == []
    with sqlite3.connect(source.path) as conn:
        for table in ("source_originals", "source_unbound", "source_origin_streams", "source_history_members"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    assert runtime.ledger.pending(DOMAIN) == []


@pytest.mark.parametrize("first_local", [False, True])
@pytest.mark.parametrize("bound", [False, True])
def test_local_only_identity_cannot_be_promoted_to_a_sync_original(tmp_path, first_local, bound):
    """Retries cannot reclassify a fixed identity across local and upload lanes."""
    source = ChatOriginalSource(tmp_path / "sources.sqlite3")
    raw = ChatOriginal("assistant", RAW.text)
    save_sync = lambda: source.commit(OWNER, UID, "5" * 32, raw) if bound else source.commit_unbound(UID, "5" * 32, raw)
    save_local = lambda: source.commit_local_reply(UID, "5" * 32, raw, "agent")
    first, second = (save_local, save_sync) if first_local else (save_sync, save_local)
    first()
    with pytest.raises(ValueError, match="chat_source_identity_conflict"):
        second()


def test_local_only_read_rejects_changed_bytes_and_does_not_initialize_missing_store(tmp_path):
    """Only the saved proof is restored; a read never mints a new source database."""
    missing = tmp_path / "missing.sqlite3"
    assert ChatOriginalSource.read_local_replies(missing, UID) == []
    assert not missing.exists()
    source = ChatOriginalSource(tmp_path / "sources.sqlite3")
    source.commit_local_reply(UID, "5" * 32, ChatOriginal("assistant", "original"), "agent")
    with sqlite3.connect(source.path) as conn:
        conn.execute("UPDATE source_local_replies SET original=replace(original,?,?)", ("original", "changed"))
    with pytest.raises(ValueError, match="chat_source_integrity_error"):
        ChatOriginalSource.read_local_replies(source.path, UID)


def test_local_only_clear_erases_body_and_late_retry_never_revives_it(tmp_path):
    """Clearing one role keeps only tombstones and leaves other roles untouched."""
    source = ChatOriginalSource(tmp_path / "sources.sqlite3")
    raw = ChatOriginal("assistant", RAW.text)
    source.commit_local_reply(UID, "5" * 32, raw, "agent")
    source.commit_local_reply("b" * 32, "6" * 32, raw, "tool")
    assert source.clear_local_replies(UID) == ["5" * 32]
    source.commit_local_reply(UID, "5" * 32, raw, "agent")
    assert source.clear_local_replies(UID) == ["5" * 32]
    assert source.read_local_replies(source.path, UID) == []
    assert len(source.read_local_replies(source.path, "b" * 32)) == 1
    with sqlite3.connect(source.path) as conn:
        assert conn.execute("SELECT original,deleted FROM source_local_replies WHERE uid=?", (UID,)).fetchone() == (None, 1)
    assert source.pending(200) == []


@pytest.mark.asyncio
async def test_local_only_save_and_restore_never_require_a_cloud_binding(tmp_path, monkeypatch):
    """The real save helper commits under the local UID without waking uploads."""
    runtime = make_runtime(tmp_path, bound=False)
    manager = SimpleNamespace(aload_characters=AsyncMock(return_value={"猫娘": {
        "Lan": {"_reserved": {"character_uid": UID}},
    }}))
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    proof = {"recordId": "5" * 32, "originalTime": RAW.original_time, "origin": "agent"}
    assert await source_module.save_local_reply("Lan", RAW.text, proof, config_manager=manager) == proof["recordId"]
    assert await source_module.local_replies(runtime, UID) == [{
        "messageId": proof["recordId"], "characterSyncId": UID, "role": "assistant",
        "text": RAW.text, "originalTime": RAW.original_time,
    }]
    runtime.wake_scheduler.assert_not_called()
    assert await recover_sources(runtime) == 0
    runtime.state.prepare_binding(DOMAIN)
    assert await recover_sources(runtime) == 0
    assert runtime.ledger.pending(DOMAIN) == []
    assert await source_module.clear_local_replies(runtime, UID) == [proof["recordId"]]
    assert await source_module.local_replies(runtime, UID) == []


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.asyncio
async def test_local_only_restore_keeps_existing_writer_visibility_fence(tmp_path, monkeypatch, trusted):
    """Copied authority cannot expose recovered local replies in a new process."""
    runtime = make_runtime(tmp_path)
    source_for(runtime).commit_local_reply(UID, "5" * 32, ChatOriginal("assistant", RAW.text), "agent")
    monkeypatch.setattr(runtime.writer, "check", Mock(side_effect=WriterQuarantined()))
    monkeypatch.setattr(runtime.writer, "local_visibility_trusted", Mock(return_value=trusted))
    assert len(await source_module.local_replies(runtime, UID)) == (1 if trusted else 0)


@pytest.mark.asyncio
async def test_clear_without_local_replies_does_not_require_a_source_writer(tmp_path, monkeypatch):
    """Canonical outage clearing needs no extra write when this lane is empty."""
    runtime = make_runtime(tmp_path)
    source_for(runtime)
    monkeypatch.setattr(ChatOriginalSource, "_connect", Mock(side_effect=WriterQuarantined()))
    assert await source_module.clear_local_replies(runtime, UID) == []


@pytest.mark.asyncio
async def test_repeated_local_clear_returns_tombstones_without_a_writer_commit(tmp_path, monkeypatch):
    """An already-cleared local lane does not break canonical outage clearing."""
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    source.commit_local_reply(UID, "5" * 32, ChatOriginal("assistant", RAW.text), "agent")
    source.clear_local_replies(UID)
    monkeypatch.setattr(ChatOriginalSource, "_connect", Mock(side_effect=WriterQuarantined()))
    assert await source_module.clear_local_replies(runtime, UID, ["5" * 32]) == ["5" * 32]
    assert source.read_local_replies(source.path, UID) == []


@pytest.mark.parametrize("origin", ["agent", "tool"])
@pytest.mark.asyncio
async def test_local_only_buffer_saves_full_reply_without_uploading(monkeypatch, origin):
    """A tool/mixed reply has one durable local identity and exact complete bytes."""
    remote, local = AsyncMock(), AsyncMock(return_value="5" * 32)
    monkeypatch.setattr(source_module, "save_original", remote)
    monkeypatch.setattr(source_module, "save_local_reply", local, raising=False)
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": origin}
    buffer = AssistantOriginalBuffer()
    for text in ("  first\r\n", "🙂e\u0301  "):
        buffer.append({"chat_original_source": proof, "text": text})
    await buffer.finish("Lan", proof["recordId"])
    local.assert_awaited_once_with("Lan", "  first\r\n🙂e\u0301  ", proof)
    remote.assert_not_awaited()
    assert buffer.records == {}


@pytest.mark.asyncio
async def test_local_only_buffer_discard_terminal_exclusion_and_retry(monkeypatch):
    """Temporary output is discarded, while a failed durable save can be retried."""
    local = AsyncMock(side_effect=[OSError("synthetic fault"), "5" * 32])
    monkeypatch.setattr(source_module, "save_local_reply", local, raising=False)
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": "agent"}
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


def test_source_commit_retries_keep_raw_bytes_and_fixed_locator(tmp_path, monkeypatch):
    """No matching by normalized text, timestamp or mutable projection is allowed."""
    source = ChatOriginalSource(tmp_path / "source.sqlite3")
    identity = "1" * 32
    patch_module_clock(monkeypatch, source_module, time_ns=lambda: 100_000_000)
    source.commit(OWNER, UID, identity, RAW)
    first, locator = source.pending(200)[0]
    assert first["queued_at_ms"] == 100
    patch_module_clock(monkeypatch, source_module, time_ns=lambda: 900_000_000)
    source.commit(OWNER, UID, identity, RAW)
    reopened = ChatOriginalSource(source.path)
    assert reopened.pending(200)[0] == (first, locator)
    assert ChatOriginal(**json.loads(first["original"])) == RAW
    assert locator.record_id == identity
    with pytest.raises(ValueError, match="chat_source_identity_conflict"):
        source.commit(OWNER, UID, identity, ChatOriginal("user", RAW.text.strip()))
    with pytest.raises(ValueError, match="chat_source_identity_conflict"):
        source.commit(OTHER, UID, identity, RAW)
    assert len(source.pending(200)) == 1


def test_concurrent_commits_keep_one_identity_and_atomic_failure_leaves_no_record(tmp_path):
    """SQLite serialization prevents duplicate raw/pending rows across writers."""
    source = ChatOriginalSource(tmp_path / 'source.sqlite3')
    identity = '8' * 32

    def commit_same_original(_index):
        """Construct independent handles to exercise real SQLite locking."""
        ChatOriginalSource(source.path).commit(OWNER, UID, identity, RAW)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(commit_same_original, range(24)))
    assert len(source.pending(200)) == 1
    with sqlite3.connect(source.path) as connection:
        connection.execute("CREATE TRIGGER fail_source BEFORE INSERT ON source_originals "
                           "BEGIN SELECT RAISE(ABORT, 'synthetic rollback'); END")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic rollback'):
        source.commit(OWNER, UID, '9' * 32, RAW)
    assert source.known_ids(OWNER, UID) == [identity]
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
    assert source.pending(1)[0][0]["origin_seq"] == MAX_ORIGIN_SEQ
    with pytest.raises(ValueError, match="^source_sequence_exhausted$"):
        source.commit_unbound(UID, "3" * 32, RAW)
    with sqlite3.connect(source.path) as connection:
        assert connection.execute("SELECT stream_id,next_seq FROM source_origin_streams WHERE uid=?", (UID,)).fetchone() == (stream, MAX_ORIGIN_SEQ + 1)
        assert connection.execute("SELECT count(*) FROM source_unbound WHERE record_id=?", ("3" * 32,)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_restart_recovers_gap_and_capture_retry_does_not_duplicate(tmp_path, monkeypatch):
    """A crash after either commit retains one source ID and one outbox record."""
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    identity = "2" * 32
    patch_module_clock(monkeypatch, source_module, time_ns=lambda: 100_000_000)
    source.commit(OWNER, UID, identity, RAW)
    patch_module_clock(monkeypatch, source_module, time_ns=lambda: 900_000_000)
    captured = source_module.ChatOriginalSource.captured

    def crash_after_capture(self, record_id):
        """Simulate process loss before the source pending marker is cleared."""
        raise RuntimeError("synthetic crash")

    monkeypatch.setattr(ChatOriginalSource, "captured", crash_after_capture)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        await recover_sources(runtime)
    assert len(runtime.ledger.pending(DOMAIN)) == 1
    monkeypatch.setattr(ChatOriginalSource, "captured", captured)
    reopened = make_runtime(tmp_path)
    assert await recover_sources(reopened) == 1
    assert await recover_sources(reopened) == 0
    payload = json.loads(reopened.ledger.pending(DOMAIN)[0][0])
    assert payload["messageId"] == identity
    assert payload["text"] == RAW.text
    assert payload["originalTime"] == RAW.original_time
    assert len(reopened.ledger.pending(DOMAIN)) == 1
    assert reopened.ledger.queue_state(DOMAIN)["oldestEnqueuedAt"] == 100
    assert reopened.ledger.queue_state(DOMAIN)["lastEnqueuedAt"] == 100


@pytest.mark.asyncio
async def test_legacy_source_recovery_keeps_unknown_queue_age(tmp_path):
    """A pre-clock WAL remains recoverable without pretending it arrived today."""
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
        source.pending(1)
    with sqlite3.connect(source.path) as conn:
        row = conn.execute("SELECT queued_at_ms,origin_seq,origin_stream_id FROM source_originals").fetchone()
        assert row == (None, None, None)
        assert conn.execute("SELECT count(*) FROM source_origin_streams").fetchone()[0] == 0
    with pytest.raises(WriterQuarantined):
        await recover_sources(runtime)
    # Unknown historical provenance is retained; a missing writer head never
    # self-authorizes legacy upload or backfills an arrival clock/origin.



@pytest.mark.asyncio
async def test_deleted_pending_and_captured_sources_are_purged_after_restart(tmp_path):
    """The deletion ledger wins even if its subsequent source cleanup never ran."""
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    ids = ["3" * 32, "4" * 32]
    source.commit(OWNER, UID, ids[0], RAW)
    await recover_sources(runtime)
    source.commit(OWNER, UID, ids[1], RAW)
    runtime.ledger.queue_deletion(DOMAIN, ids)
    assert await recover_sources(make_runtime(tmp_path)) == 0
    with sqlite3.connect(source.path) as connection:
        rows = connection.execute("SELECT original,pending,deleted FROM source_originals").fetchall()
    assert rows == [(None, 0, 1), (None, 0, 1)]
    # A late producer retry cannot refill a tombstoned source with the old body.
    for identity in ids:
        source.commit(OWNER, UID, identity, RAW)
    assert source.pending(200) == []
    assert runtime.ledger.visible_originals(DOMAIN) == []


@pytest.mark.asyncio
async def test_recovery_rejects_body_corruption_before_ledger_registration(tmp_path):
    """Source identity proof never accepts a valid JSON body with changed bytes."""
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    source.commit(OWNER, UID, 'e' * 32, RAW)
    with sqlite3.connect(source.path) as connection:
        connection.execute('UPDATE source_originals SET original=replace(original,?,?)', ('原文', '篡改'))
    with pytest.raises(ValueError, match='chat_source_integrity_error'):
        await recover_sources(runtime)
    assert runtime.ledger.pending(DOMAIN) == []
    assert len(source.pending(200)) == 1


@pytest.mark.asyncio
async def test_known_ids_include_all_pending_over_page_limit_and_owner_isolation(tmp_path):
    """Clear fixes its identity boundary before bounded recovery or upload occurs."""
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    identities = [f"{index:032x}" for index in range(205)]
    for identity in identities:
        source.commit(OWNER, UID, identity, RAW)
    source.commit(OTHER, UID, "f" * 32, RAW)
    assert await known_source_ids(runtime, UID) == identities
    assert await recover_sources(runtime, max_records=2) == 2
    assert await known_source_ids(runtime, UID) == identities
    await purge_source_originals(runtime, UID, identities)
    assert [row["record_id"] for row, _ in source.pending(300)] == ["f" * 32]
    assert await recover_sources(runtime) == 0


@pytest.mark.parametrize("maximum", [0, -1, True, 1.5])
@pytest.mark.asyncio
async def test_recovery_rejects_invalid_boundaries(tmp_path, maximum):
    """Invalid limits fail before source or network access."""
    with pytest.raises(ValueError, match="invalid_chat_source_recovery_limit"):
        await recover_sources(make_runtime(tmp_path), max_records=maximum)


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
    assert source_for(runtime).pending(200) == []
    runtime.wake_scheduler.assert_not_called()
    runtime.state.prepare_binding(DOMAIN)
    runtime.state.pause(UID)
    assert await recover_sources(runtime) == 0
    identity = await save_original("Lan", "user", RAW.text, original_time=RAW.original_time, config_manager=manager)
    assert len(identity) == 32
    assert runtime.state.binding(UID)["enabled"] == 0
    assert runtime.ledger.pending(DOMAIN) == []
    await recover_sources(runtime)
    payload = json.loads(runtime.ledger.pending(DOMAIN)[0][0])
    assert payload["ownerId"] == OWNER
    assert payload["messageId"] == identity
    assert payload["text"] == RAW.text
    assert payload["originalTime"] == RAW.original_time
    runtime.wake_scheduler.assert_called_once_with()


@pytest.mark.asyncio
async def test_save_keeps_pending_when_ledger_temporarily_fails(tmp_path, monkeypatch):
    """Local raw persistence does not depend on HTTP or a successful ledger pass."""
    runtime = make_runtime(tmp_path)
    manager = SimpleNamespace(aload_characters=AsyncMock(return_value={"猫娘": {
        "Lan": {"_reserved": {"character_uid": UID}},
    }}))
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    capture = runtime.ledger.capture
    runtime.ledger.capture = Mock(side_effect=RuntimeError("synthetic unavailable"))
    identity = await save_original("Lan", "user", RAW.text, config_manager=manager)
    assert source_for(runtime).pending(1)[0][0]["record_id"] == identity
    runtime.wake_scheduler.assert_called_once_with()
    runtime.ledger.capture = capture
    assert await recover_sources(runtime) == 1


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
    assert source_for(runtime).pending(1) == []
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
    """Only ordinary/private output enters upload lanes; tools stay local."""
    save, local = AsyncMock(), AsyncMock()
    monkeypatch.setattr(source_module, "save_original", save)
    monkeypatch.setattr(source_module, "save_local_reply", local)
    buffer = AssistantOriginalBuffer()
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": origin}
    buffer.append({"chat_original_source": proof, "text": "hidden tool reply"})
    buffer.append({"text": "legacy projection"})
    await buffer.finish("Lan")
    save.assert_not_awaited()
    assert local.await_count == (1 if origin in ("agent", "tool") else 0)


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
async def test_late_tool_evidence_keeps_preface_local_without_tainting_another_reply(
    monkeypatch,
):
    """A slow tool changes the matching completed record, never its emitted proof or sibling."""
    save, local = AsyncMock(), AsyncMock()
    monkeypatch.setattr(source_module, "save_original", save)
    monkeypatch.setattr(source_module, "save_local_reply", local)
    manager = SimpleNamespace()
    old = mark_turn_origin(manager, "old", "chat")
    newer = mark_turn_origin(manager, "new", "chat")
    buffer = AssistantOriginalBuffer()
    buffer.append({"chat_original_source": old, "text": "preface "})
    buffer.mark_tool(old["recordId"])
    buffer.append({"chat_original_source": newer, "text": "ordinary"})
    await buffer.finish("Lan", newer["recordId"])
    save.assert_awaited_once_with(
        "Lan",
        "assistant",
        "ordinary",
        record_id=newer["recordId"],
        original_time=newer["originalTime"],
    )
    buffer.append({"chat_original_source": old, "text": "tool result"})
    await buffer.finish("Lan", old["recordId"])
    local.assert_awaited_once_with(
        "Lan", "preface tool result", {**old, "origin": "tool"}
    )
    assert mark_turn_origin(manager, "old", "chat") == old


@pytest.mark.asyncio
async def test_tool_evidence_survives_failed_local_save_and_is_cleared_on_discard(
    monkeypatch,
):
    """Retry retains the whole local record; discard cannot contaminate a replacement."""
    save = AsyncMock()
    local = AsyncMock(side_effect=[OSError("synthetic failure"), "saved"])
    monkeypatch.setattr(source_module, "save_original", save)
    monkeypatch.setattr(source_module, "save_local_reply", local)
    proof = mark_turn_origin(SimpleNamespace(), "reply", "chat")
    buffer = AssistantOriginalBuffer()
    buffer.append({"chat_original_source": proof, "text": "preface"})
    buffer.mark_tool(proof["recordId"])
    await buffer.finish("Lan")
    await buffer.finish("Lan")
    assert (
        local.await_count == 2 and local.await_args_list[0] == local.await_args_list[1]
    )
    save.assert_not_awaited()
    buffer.mark_tool(proof["recordId"])
    buffer.discard()
    replacement = mark_turn_origin(SimpleNamespace(), "replacement", "chat")
    buffer.mark_tool(replacement["recordId"])
    buffer.discard()
    buffer.append({"chat_original_source": replacement, "text": "replacement"})
    await buffer.finish("Lan")
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_dispatch_marks_its_owner_before_await_and_ignores_closed_or_retired_turns():
    """Source evidence is tied to the invoking owner, not a mutable current turn."""
    from main_logic.core.tool_calling import ToolCallingMixin
    from main_logic.tool_calling import ToolCall, ToolResult
    from tests.unit.test_core_game_route_memory_contract import _FakeQueue

    started, release = asyncio.Event(), asyncio.Event()

    async def execute(call):
        started.set()
        await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    active, retired = object(), object()
    manager = SimpleNamespace(
        session=active,
        current_speech_id="new",
        sync_message_queue=_FakeQueue(),
        tool_registry=SimpleNamespace(get=lambda _name: None, execute=execute),
    )
    owner = SimpleNamespace(speech_id="old", turn_ended=False)
    call = ToolCall(name="synthetic", arguments={}, reply_owner=owner)
    task = asyncio.create_task(
        ToolCallingMixin._on_tool_call(manager, call, invoking_session=retired)
    )
    await started.wait()
    proof = manager._chat_original_turns["old"]
    assert manager.sync_message_queue.messages == [
        {
            "type": "system",
            "data": "chat original tool",
            "chat_original_id": proof["recordId"],
        }
    ]
    assert "new" not in manager._chat_original_turns
    release.set()
    await task
    owner.turn_ended = True
    await ToolCallingMixin._on_tool_call(manager, call, invoking_session=active)
    await ToolCallingMixin._on_tool_call(
        manager, ToolCall(name="synthetic", arguments={}), invoking_session=retired
    )
    assert len(manager.sync_message_queue.messages) == 1
    await ToolCallingMixin._on_tool_call(
        manager, ToolCall(name="synthetic", arguments={}), invoking_session=active
    )
    assert "new" not in manager._chat_original_turns
    assert len(manager.sync_message_queue.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("api_type", ["gpt", "gemini"])
@pytest.mark.parametrize("tool_error", [False, True])
async def test_realtime_tool_scope_keeps_preface_and_rotated_continuation_local(monkeypatch, api_type, tool_error):
    """Real tool dispatch carries frozen provenance across provider responses, not across user turns."""
    from main_logic.omni_realtime_client import OmniRealtimeClient
    from main_logic.core.turn import TurnMixin
    from main_logic.tool_calling import ToolCall, ToolResult
    from tests.unit.test_core_game_route_memory_contract import _make_manager
    from tests.unit.test_realtime_tool_ownership import _wait_for_tool_tasks

    manager = _make_manager()
    client = OmniRealtimeClient("wss://example.invalid/realtime", "synthetic", api_type=api_type,
                               get_host_turn_id=lambda: manager.current_speech_id)
    manager.session = client
    client.ws = SimpleNamespace(send_tool_response=AsyncMock()) if api_type == "gemini" else object()
    if api_type == "gemini":
        from main_logic.omni_realtime_client import _gemini_support
        monkeypatch.setattr(_gemini_support, "types", SimpleNamespace(
            FunctionResponse=lambda **kwargs: SimpleNamespace(**kwargs),
        ))
        client._gemini_session = client.ws
    client._on_connection_attached()
    client._send_tool_results_openai_realtime = AsyncMock()
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(call):
        started.set()
        await release.wait()
        if tool_error:
            raise RuntimeError("synthetic tool failure")
        return ToolResult(name=call.name, call_id=call.call_id, output={"value": "synthetic"})

    manager.tool_registry = SimpleNamespace(get=lambda _name: None, execute=execute)
    client.on_tool_call = manager._make_tool_call_handler(client)
    await TurnMixin.send_lanlan_response(manager, "preface", is_first_chunk=True)
    original_proof = dict(manager._chat_original_turns[manager.current_speech_id])
    owner = client._capture_tool_task_owner(client.ws)
    call = ToolCall(name="synthetic", arguments={}, call_id="source-scope-test")
    if api_type == "gemini":
        client._start_gemini_tool_batch([call], owner)
    else:
        client._start_raw_tool_call(call, owner)
    await asyncio.wait_for(started.wait(), 1)
    release.set()
    await _wait_for_tool_tasks(client)
    if api_type == "gemini":
        client.ws.send_tool_response.assert_awaited_once()
    # No-server-VAD terminal and Gemini continuation can rotate the host speech ID in the same tool scope.
    manager.current_speech_id = "tool-continuation"
    await TurnMixin.send_lanlan_response(manager, "tool reply", is_first_chunk=True)
    # Ordinary proactive speech intentionally reuses the provider tool scope; its explicit origin starts a new reply.
    manager.current_speech_id = "ordinary-proactive"
    mark_turn_origin(manager, manager.current_speech_id, "proactive")
    await TurnMixin.send_lanlan_response(manager, "ordinary proactive", is_first_chunk=True)
    client.note_user_turn_started()
    manager.current_speech_id = "next-user-turn"
    await TurnMixin.send_lanlan_response(manager, "ordinary reply", is_first_chunk=True)
    save, local = AsyncMock(), AsyncMock()
    monkeypatch.setattr(source_module, "save_original", save)
    monkeypatch.setattr(source_module, "save_local_reply", local)
    buffer = AssistantOriginalBuffer()
    for event in manager.sync_message_queue.messages:
        if event["type"] == "system" and event["data"] == "chat original tool":
            buffer.mark_tool(event["chat_original_id"])
        elif event["type"] == "json" and event["data"]["type"] == "gemini_response":
            buffer.append(event["data"])
    await buffer.finish("Lan")
    assert [call.args[1] for call in local.await_args_list] == ["preface", "tool reply"]
    assert [call.args[2] for call in save.await_args_list] == ["ordinary proactive", "ordinary reply"]
    assert manager._chat_original_turns["old-speech"] == original_proof

@pytest.mark.asyncio
async def test_source_diagnostic_exclusion_is_once_per_reply_and_never_uploads(monkeypatch):
    """Actual collector exclusions identify one reply without logging every chunk."""
    trace, save = Mock(enabled=True), AsyncMock()
    monkeypatch.setattr(source_module, "get_trace", lambda: trace)
    monkeypatch.setattr(source_module, "save_original", save)
    monkeypatch.setattr(source_module, "save_local_reply", AsyncMock(return_value="d" * 32))
    proof = {"recordId": "d" * 32, "originalTime": "123", "origin": "agent"}
    buffer = AssistantOriginalBuffer()
    for _ in range(10):
        buffer.append({"chat_original_source": proof, "text": "private-original"})
    trace.emit.assert_called_once_with(
        "assistant_source_excluded", message_id=proof["recordId"], source_origin="agent",
        source_reason="nonchat_origin",
    )
    await buffer.finish("private-role", proof["recordId"])
    save.assert_not_awaited()
    trace.emit.assert_called_with(
        "assistant_source_finished", message_id=proof["recordId"], source_origin="agent",
        source_reason="local_saved", outcome="success",
    )
    buffer.append({"chat_original_source": proof, "text": "private-original"})
    assert sum(call.args[0] == "assistant_source_excluded" for call in trace.emit.call_args_list) == 2
    assert "private-" not in str(trace.emit.call_args_list)


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
async def test_clear_snapshot_first_saves_only_that_characters_waiting_replies(monkeypatch):
    """A clear right after a reply includes it instead of letting it upload afterwards."""
    remote = AsyncMock(return_value="x")
    monkeypatch.setattr(source_module, "save_original", remote)
    uids = {"Lan": UID, "Other": "b" * 32}
    monkeypatch.setattr(source_module, "character_uid_for", AsyncMock(side_effect=lambda name: uids[name]))
    buffer = AssistantOriginalBuffer(tail_grace=30)
    mine = {"recordId": "5" * 32, "originalTime": "123", "origin": "chat"}
    other = {"recordId": "6" * 32, "originalTime": "124", "origin": "chat"}
    buffer.append({"chat_original_source": mine, "text": "mine"})
    buffer.append({"chat_original_source": other, "text": "other"})
    await buffer.finish("Lan", mine["recordId"])
    await buffer.finish("Other", other["recordId"])
    await source_module.flush_terminated_originals(UID)
    assert [call.args[2] for call in remote.await_args_list] == ["mine"]
    assert list(buffer.records) == [other["recordId"]]
    await buffer.flush_terminated()


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


@pytest.mark.parametrize("marked_tool,excluded", [(True, False), (False, True)])
@pytest.mark.asyncio
async def test_deferred_seal_keeps_local_only_and_excluded_decisions(monkeypatch, marked_tool, excluded):
    """Waiting for late text never turns a local-only or excluded reply into an upload."""
    remote, local = AsyncMock(return_value="x"), AsyncMock(return_value="x")
    monkeypatch.setattr(source_module, "save_original", remote)
    monkeypatch.setattr(source_module, "save_local_reply", local)
    monkeypatch.setattr(source_module, "character_uid_for", AsyncMock(return_value=UID))
    proof = {"recordId": "5" * 32, "originalTime": "123", "origin": "chat"}
    buffer = AssistantOriginalBuffer(tail_grace=30)
    buffer.append({"chat_original_source": proof, "text": "reply"})
    if marked_tool:
        buffer.mark_tool(proof["recordId"])
    await buffer.finish("Lan", proof["recordId"], excluded=excluded)
    buffer.append({"chat_original_source": proof, "text": " late"})
    # Shutdown seals completed replies at once instead of waiting out the grace.
    await buffer.flush_terminated()
    remote.assert_not_awaited()
    assert [call.args[1] for call in local.await_args_list] == (["reply late"] if marked_tool else [])
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
    """The real accepted input pipeline saves a standalone user and publishes its ID."""
    from tests.unit.test_core_game_route_memory_contract import _make_transcript_manager
    from main_logic.core.turn import TurnMixin

    manager = _make_transcript_manager()
    manager._config_manager = object()
    manager._active_text_request_id = "later-request"
    manager.websocket = SimpleNamespace(send_json=AsyncMock())
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
    manager.websocket.send_json.assert_awaited_once_with({
        "type": "chat_original_identity", "role": "user",
        "request_id": "accepted-request", "message_id": "6" * 32,
    })


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
    assert messages[0]["chat_original_id"] == messages[2]["chat_original_id"]
    assert messages[1]["chat_original_id"] != messages[0]["chat_original_id"]
    reply = SimpleNamespace(speech_id="old-speech", meta=None, turn_ended=False)
    terminal = TurnMixin._queue_turn_end(manager, "old-request", reply_turn=reply)
    assert terminal["chat_original_id"] == messages[0]["chat_original_id"]
    buffer = AssistantOriginalBuffer()
    for message in messages:
        buffer.append(message)
    await buffer.finish("Lan", terminal["chat_original_id"])
    assert save.await_args.args == ("Lan", "assistant", "  完整\n🙂  ")
    assert list(buffer.records) == [messages[1]["chat_original_id"]]


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
async def test_actual_tool_dispatch_keeps_whole_reply_local(
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
    original_save, local_save = (
        source_module.save_original,
        source_module.save_local_reply,
    )

    async def saved_original(name, text_role, text, **kwargs):
        return await original_save(
            name, text_role, text, config_manager=config, **kwargs
        )

    async def saved_local(name, text, proof):
        return await local_save(name, text, proof, config_manager=config)

    monkeypatch.setattr(source_module, "save_original", saved_original)
    monkeypatch.setattr(source_module, "save_local_reply", saved_local)
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
        "pending_upload_sources": len(source.pending(200)),
        "ledger_pending": len(runtime.ledger.pending(DOMAIN)),
    }
    assert len(local) == 1, report
    assert local[0]["text"] == "".join(visible) == prefix + post_text
    assert source.pending(200) == [] and runtime.ledger.pending(DOMAIN) == []


@pytest.mark.asyncio
async def test_recovery_filters_ineligible_rows_before_applying_the_page_limit(tmp_path):
    runtime = make_runtime(tmp_path)
    source = source_for(runtime)
    for index in range(205):
        source.commit(OTHER, UID, f"{index + 1:032x}", RAW)
    identity = "f" * 32
    source.commit(OWNER, UID, identity, RAW)
    assert await recover_sources(runtime, max_records=1) == 1
    assert json.loads(runtime.ledger.pending(DOMAIN)[0][0])["messageId"] == identity
    assert len(source.pending(300)) == 205


@pytest.mark.asyncio
@pytest.mark.parametrize("preempted", [False, True])
@pytest.mark.parametrize("origin", ["agent", "proactive"])
async def test_gemini_agent_origin_follows_only_its_owned_provider_response(preempted, origin):
    from main_logic.core.turn import TurnMixin
    provider = object()
    session = SimpleNamespace(_connection_generation=2, _gemini_session=provider, _tool_scope_generation=3)
    session._gemini_proactive_outcome_owner = (2, provider, "ticket", object(), 3)
    manager = SimpleNamespace(session=session, _takeover_active=False, audio_resampler=Mock(),
        _reset_ai_turn_buffer=Mock(), send_user_activity=AsyncMock(), lock=asyncio.Lock(),
        state=SimpleNamespace(mark_user_input_preempt=Mock(), fire=AsyncMock()), current_speech_id="old")
    mark_turn_origin(manager, "old", origin)
    async def clear():
        if preempted:
            session._tool_scope_generation += 1
    manager._clear_tts_pipeline = clear
    await TurnMixin.handle_new_message(manager)
    proof = mark_turn_origin(manager, manager.current_speech_id, "chat")
    assert proof['origin'] == ('chat' if preempted else origin)
