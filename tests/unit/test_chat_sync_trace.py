"""Timing observation is opt-in, metadata-only, and cannot accept failed commits."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401
from tests.unit.test_chat_sync_runtime import DOMAIN, UID, setup_runtime
from services.chat_sync.ledger import ChatOriginal, SourceLocator
from services.chat_sync.protocol import encode_chat_original
from services.chat_sync.source import ChatOriginalSource
from services.chat_sync.trace import ChatSyncTrace, route_class


def read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def enable_trace(monkeypatch, tmp_path):
    from services.chat_sync import trace as chat_sync_trace
    path = tmp_path / "trace.jsonl"
    trace = ChatSyncTrace(path)
    monkeypatch.setattr(chat_sync_trace, "_trace", trace)
    return trace, path


def test_default_off_never_reads_clock_or_opens_file(monkeypatch):
    trace = ChatSyncTrace()
    monkeypatch.setattr(trace, "instant", lambda: pytest.fail("disabled trace observed a clock"))
    trace.emit("source_committed", body="private-original")
    assert not trace.enabled
    assert trace._sequence == 0


def test_allowlist_and_concurrent_records_never_include_payloads(tmp_path):
    path = tmp_path / "trace.jsonl"
    trace = ChatSyncTrace(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: trace.emit(
            "http_headers", character_uid=UID, message_id="private-original",
            route="identity", status=200, body="private-original", token="private-token",
            headers={"Authorization": "private-token"}, error="private-error",
        ), range(40)))
    events = read_events(path)
    assert [item["seq"] for item in events] == list(range(1, 41))
    assert len({item["clock_id"] for item in events}) == 1
    assert all(item["wall_ms"] > item["mono_ms"] > 0 for item in events)
    assert all(set(item) == {"v", "event", "clock_id", "seq", "wall_ms", "mono_ms", "character_uid", "route", "status"}
               for item in events)
    assert "private-" not in path.read_text(encoding="utf-8")


def test_failed_observation_is_nonfatal_and_leaves_sequence_gap(tmp_path):
    path = tmp_path / "later" / "trace.jsonl"
    trace = ChatSyncTrace(path)
    trace.emit("source_committed", message_id="d" * 32)
    path.parent.mkdir()
    trace.emit("source_committed", message_id="e" * 32)
    assert [item["seq"] for item in read_events(path)] == [2]


@pytest.mark.parametrize("event", ["assistant_source_selected", "assistant_source_excluded", "assistant_source_finished", "local_reply_committed"])
def test_assistant_source_diagnostic_only_accepts_fixed_metadata(tmp_path, event):
    path = tmp_path / "trace.jsonl"
    trace = ChatSyncTrace(path)
    trace.emit(event, message_id="d" * 32, source_origin="agent", source_reason="nonchat_origin",
               body="private-original", role_name="private-role", callback="private-callback", error="private-error")
    trace.emit(event, message_id="private-id", source_origin="private-origin", source_reason="private-reason")
    events = read_events(path)
    assert len(events) == 2
    assert events[0]["message_id"] == "d" * 32
    assert events[0]["source_origin"] == "agent"
    assert events[0]["source_reason"] == "nonchat_origin"
    assert set(events[1]) == {"v", "event", "clock_id", "seq", "wall_ms", "mono_ms"}
    assert "private-" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("reason", ["local_saved", "local_save_unavailable", "local_save_failed"])
def test_local_only_finish_diagnostic_preserves_fixed_reason(tmp_path, reason):
    path = tmp_path / "trace.jsonl"
    ChatSyncTrace(path).emit("assistant_source_finished", message_id="d" * 32, source_origin="tool", source_reason=reason)
    assert read_events(path)[0]["source_reason"] == reason


def test_source_event_follows_real_commit_and_rollback_has_no_event(tmp_path, monkeypatch):
    trace, path = enable_trace(monkeypatch, tmp_path)
    source = ChatOriginalSource(tmp_path / "sources" / "sources.sqlite3")
    source.commit_unbound(UID, "d" * 32, ChatOriginal("user", "private-original"))
    source.commit_unbound(UID, "d" * 32, ChatOriginal("user", "private-original"))
    assert [item["message_id"] for item in read_events(path)] == ["d" * 32]
    with closing(source._connect()) as conn, conn:
        conn.execute("CREATE TRIGGER reject_original BEFORE INSERT ON source_unbound BEGIN SELECT RAISE(ABORT, 'private-error'); END")
    with pytest.raises(sqlite3.IntegrityError):
        source.commit_unbound(UID, "e" * 32, ChatOriginal("user", "private-original"))
    assert trace._sequence == 1
    assert "private-" not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_runtime_reports_resolver_http_and_guarded_accepted_ids(tmp_path, monkeypatch):
    _, path = enable_trace(monkeypatch, tmp_path)
    runtime, service, _ = setup_runtime(tmp_path)
    resolver = runtime.session_provider

    async def delayed_resolver():
        await asyncio.sleep(0.02)
        return await resolver()

    runtime.session_provider = delayed_resolver
    try:
        await runtime.configure("https://synthetic.invalid")
        await runtime.enable(UID, "Neko")
        visible, deleted = "d" * 32, "e" * 32
        for identity in (visible, deleted):
            record = runtime.ledger.capture(DOMAIN, SourceLocator("f" * 32, "a" * 32, identity),
                                            ChatOriginal("user", "private-original"), message_id=identity)
            service.records.append(encode_chat_original(record).decode("utf-8"))
        service.deleted = [deleted]
        await runtime._channel_pass(UID, "pull", manual=True)
        await runtime._channel_pass(UID, "body", manual=True)
        events = read_events(path)
        assert [item["message_id"] for item in events if item["event"] == "download_committed"] == [visible]
        assert [item["message_id"] for item in events if item["event"] == "upload_ack_committed"] == [visible]
        begin = next(item for item in events if item["event"] == "session_begin")
        end = next(item for item in events if item["event"] == "session_end" and item["span_id"] == begin["span_id"])
        assert end["mono_ms"] - begin["mono_ms"] >= 15
        for item in (item for item in events if item["event"] == "http_begin"):
            span = [event["event"] for event in events if event.get("span_id") == item["span_id"]]
            assert span == ["http_begin", "http_send", "http_headers", "http_body_complete", "http_end"]
        assert "private-original" not in path.read_text(encoding="utf-8")
        assert "synthetic-a" not in path.read_text(encoding="utf-8")
        assert "https://" not in path.read_text(encoding="utf-8")
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_download_cursor_conflict_does_not_emit_committed_message(tmp_path, monkeypatch):
    _, path = enable_trace(monkeypatch, tmp_path)
    runtime, _, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://synthetic.invalid")
        await runtime.enable(UID, "Neko")
        _, fingerprint = await runtime._session()
        binding = runtime.state.binding(UID)
        record = runtime.ledger.capture(DOMAIN, SourceLocator("f" * 32, "a" * 32, "d" * 32),
                                        ChatOriginal("user", "private-original"), message_id="d" * 32)
        page = {"cursor": 2, "hasMore": False, "originals": [encode_chat_original(record).decode()], "deletedMessageIds": []}
        with pytest.raises(ValueError, match="chat_cursor_conflict"):
            runtime._commit_bound(binding, runtime.ledger.apply_changes, DOMAIN, page, after=1, fingerprint=fingerprint)
        assert not any(item["event"] == "download_committed" for item in read_events(path))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_unwritable_trace_does_not_change_successful_sync(tmp_path, monkeypatch):
    trace, _ = enable_trace(monkeypatch, tmp_path)
    trace.path = tmp_path / "absent-directory" / "trace.jsonl"
    runtime, _, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://synthetic.invalid")
        await runtime.enable(UID, "Neko")
        receipt = await runtime._channel_pass(UID, "pull", manual=True)
        assert receipt["cursor"] == 1
        assert runtime.ledger.cursor(DOMAIN) == 1
        assert trace._sequence > 0
    finally:
        await runtime.close()


@pytest.mark.parametrize("path,expected", [
    ("/v1/chat-sync/identity?private-token", "identity"),
    ("/prototype/chat-sync/characters/" + UID + "/changes?after=123", "changes"),
    ("/arbitrary/private-original", "other"),
])
def test_route_class_keeps_only_fixed_labels(path, expected):
    assert route_class(path) == expected
