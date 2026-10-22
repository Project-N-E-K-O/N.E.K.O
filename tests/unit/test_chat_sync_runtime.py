"""Real HTTP adapter tests using disposable state and synthetic OAuth identities."""

import asyncio
from contextlib import asynccontextmanager, closing, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import ssl
import subprocess
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from services.chat_sync.ledger import ChatDomain, ChatOriginal, SourceLocator
from services.chat_sync.runtime import (
    ChatSyncServiceError, DesktopChatSyncRuntime, DesktopSyncState, _load_local_ca_context, _saved_session_commit_guard,
)
from services.chat_sync.protocol import encode_chat_original
from services.chat_sync.source import ChatOriginalSource


# Lifecycle, cancellation and recovery scenarios with many durable SQLite
# commits; they run in the runtime shard instead of the main xdist gate.
pytestmark = pytest.mark.runtime

OWNER = "00000000-0000-4000-8000-000000000001"
OTHER_OWNER = "00000000-0000-4000-8000-000000000002"
UID = "a" * 32
OTHER_UID = "b" * 32
BINDING = "c" * 32
DOMAIN = ChatDomain(OWNER, UID)


class SyntheticService:
    """Emulate binding receipts and fixed original requests without real network."""

    def __init__(self):
        """Keep observable synthetic requests for namespace and retry assertions."""
        self.requests = []
        self.owner = OWNER
        self.records = []
        self.deleted = []
        self.on_request = None

    async def request(self, request):
        """Use a real httpx request while never accepting production credentials."""
        self.requests.append(request)
        if self.on_request is not None:
            await self.on_request(request)
        path = request.url.path
        if path == "/v1/chat-sync/identity":
            return httpx.Response(200, json={"ownerId": self.owner})
        body = json.loads(request.content) if request.content else None
        if path.endswith("/bindings"):
            return httpx.Response(200, json={
                "ownerId": self.owner, "characterSyncId": UID,
                "deviceId": body["deviceId"], "bindingId": BINDING,
            })
        if path.endswith("/" + UID):
            return httpx.Response(200, json={"ownerId": self.owner, "characterSyncId": UID, **body})
        assert request.headers["X-Neko-Chat-Binding"] == BINDING
        if "/batches/" in path:
            self.records = body["originals"]
            return httpx.Response(200, json={
                "batchId": path.rsplit("/", 1)[1],
                "messageIds": [json.loads(raw)["messageId"] for raw in self.records], "cursor": 999,
            })
        if "/deletions/" in path:
            assert len(body["messageIds"]) <= 200
            self.deleted = list(dict.fromkeys(self.deleted + body["messageIds"]))
            return httpx.Response(200, json={
                "deletionId": path.rsplit("/", 1)[1], "messageIds": body["messageIds"], "cursor": 1000,
            })
        assert path.endswith("/changes")
        return httpx.Response(200, json={
            "cursor": 1, "hasMore": False, "originals": self.records, "deletedMessageIds": self.deleted,
        })


def setup_runtime(tmp_path, service=None, *, fixed_service_url=None):
    """Inject only synthetic session state and an in-memory HTTP transport."""
    service = service or SyntheticService()
    session = {"logged_in": True, "snapshot": {
        "access_token": "synthetic-a", "base_url": "https://community.project-neko.cn",
        # This deliberately false local hint must never become the bound owner.
        "local_user_id": OTHER_OWNER,
    }}

    async def resolve():
        """Represent the existing resolver's already validated exact snapshot."""
        return session

    @contextmanager
    def commit_guard(expected):
        """Represent the locked local credential snapshot without touching real tokens."""
        if not session["logged_in"]:
            raise ValueError("chat_sync_login_required")
        snapshot = session["snapshot"]
        fingerprint = hashlib.sha256(json.dumps({
            "token": snapshot["access_token"], "origin": snapshot["base_url"],
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if fingerprint != expected:
            raise ValueError("chat_sync_session_changed")
        yield

    # Advance this explicit scheduler clock in retry tests; production admission
    # remains active for synthetic transports instead of using a test bypass.
    clock = [1_000_000]

    async def current_character_uid():
        """Select one disposable role without loading production configuration."""
        return UID

    runtime = DesktopChatSyncRuntime(
        tmp_path / "chat_sync", resolve,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(service.request)),
        session_commit_guard=commit_guard,
        clock=lambda: clock[0], uniform=lambda *_: 0,
        current_character_uid_provider=current_character_uid,
        fixed_service_url=fixed_service_url,
    )
    runtime.test_clock = clock
    return runtime, service, session


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [
    ("saved_unverified", "chat_sync_session_unavailable"),
    ("rejected", "chat_sync_login_required"),
    ("missing_snapshot", "chat_sync_login_required"),
    ("invalid_snapshot", "chat_sync_login_required"),
    ("missing_token", "chat_sync_login_required"),
    ("invalid_token", "chat_sync_login_required"),
    ("wrong_origin", "chat_sync_login_required"),
])
async def test_session_resolver_preserves_saved_unverified_session_without_accepting_rejected_credentials(tmp_path, case, expected):
    """Use the existing saved-session fact without treating malformed credentials as usable."""
    runtime, service, session = setup_runtime(tmp_path)
    try:
        session["logged_in"] = False
        if case == "rejected":
            session["rejected"] = True
        elif case == "missing_snapshot":
            session.pop("snapshot")
        elif case == "invalid_snapshot":
            session["snapshot"] = "synthetic-invalid"
        elif case == "missing_token":
            session["snapshot"].pop("access_token")
        elif case == "invalid_token":
            session["snapshot"]["access_token"] = 7
        elif case == "wrong_origin":
            session["snapshot"]["base_url"] = "https://foreign.test"
        with pytest.raises(ValueError, match="^" + expected + "$"):
            await runtime._session()
        assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_saved_session_validation_outage_recovers_automatic_pull_with_the_same_token(tmp_path):
    """Temporary OAuth validation failure retries without logout, new consent or token replacement."""
    runtime, service, session = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        presence = await runtime.session_presence_provider()

        async def saved_presence():
            # Production presence checks the saved credential snapshot independently
            # of the resolver's current ability to validate that same session.
            return presence

        runtime.session_presence_provider = saved_presence
        service.requests.clear()
        session["logged_in"] = False
        with pytest.raises(ValueError, match="^chat_sync_session_unavailable$"):
            await runtime._channel_pass(UID, "pull", presence=presence)
        generation = runtime.state.binding(UID)["generation"]
        assert runtime.scheduler.last_error(DOMAIN.key()) == "chat_sync_session_unavailable"
        assert not runtime.scheduler.ready(DOMAIN.key(), "pull", generation, presence)
        assert runtime.ledger.cursor(DOMAIN) == 0 and service.requests == []
        session["logged_in"] = True
        runtime.test_clock[0] += 5000
        await runtime.scheduler_tick()
        await finish_automatic_flights(runtime)
        assert runtime.ledger.cursor(DOMAIN) == 1
        assert sum(request.url.path.endswith("/changes") for request in service.requests) == 1
        assert session["snapshot"]["access_token"] == "synthetic-a"
    finally:
        await runtime.close()


def capture(runtime, index=1):
    """Register synthetic verified originals without reading user memory."""
    return runtime.ledger.capture(DOMAIN, SourceLocator("db", "generation", str(index)), ChatOriginal("user", "  full text\n🙂  "))


@pytest.mark.asyncio
async def test_local_only_reply_history_delegates_without_auth_or_sync_work(tmp_path, monkeypatch):
    """The passive local lane never authenticates, recovers ledger work or sends HTTP."""
    from unittest.mock import AsyncMock
    import services.chat_sync.source as source

    runtime, service, session = setup_runtime(tmp_path)
    session["logged_in"] = False
    local = {"messageId": "e" * 32, "characterSyncId": UID, "role": "assistant",
             "text": "  synthetic local reply\r\n🙂  ", "originalTime": "1791540000123"}
    read = AsyncMock(return_value=[local])
    monkeypatch.setattr(source, "local_replies", read, raising=False)
    runtime._recover_originals = AsyncMock(side_effect=AssertionError("local display cannot recover sync work"))
    try:
        assert await runtime.local_replies(UID) == [local]
        read.assert_awaited_once_with(runtime, UID)
        runtime._recover_originals.assert_not_awaited()
        assert service.requests == []
        assert runtime.state.binding(UID) is None
        assert runtime.ledger.pending(DOMAIN) == []
        assert runtime.ledger.visible_originals(DOMAIN) == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_clear_saves_replies_waiting_out_their_grace_before_its_snapshot(tmp_path, monkeypatch):
    """A reply that just finished is saved first, so the fixed clear snapshot includes it."""
    from unittest.mock import AsyncMock
    import services.chat_sync.source as source

    runtime, _, _ = setup_runtime(tmp_path)
    events = []
    monkeypatch.setattr(source, "flush_terminated_originals",
                        AsyncMock(side_effect=lambda uid: events.append(("flush", uid))))
    monkeypatch.setattr(source, "local_reply_ids", AsyncMock(return_value=[]), raising=False)
    monkeypatch.setattr(source, "known_source_ids", AsyncMock(return_value=[]))
    runtime.history = AsyncMock(side_effect=lambda uid: events.append(("snapshot", uid)) or [])
    try:
        await runtime.clear(UID)
        assert events == [("flush", UID), ("snapshot", UID)]
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_originals", [False, True])
async def test_clear_keeps_local_reply_ids_outside_canonical_deletion(tmp_path, monkeypatch, has_originals):
    """Local cleanup runs after canonical deletion, including an empty sync boundary."""
    from unittest.mock import AsyncMock
    import services.chat_sync.source as source

    runtime, service, _ = setup_runtime(tmp_path)
    canonical_id, local_id = "d" * 32, "e" * 32
    events = []
    runtime.history = AsyncMock(return_value=[{"messageId": canonical_id}] if has_originals else [])
    monkeypatch.setattr(source, "known_source_ids", AsyncMock(return_value=[]))
    snapshot = AsyncMock(return_value=[local_id])
    monkeypatch.setattr(source, "local_reply_ids", snapshot, raising=False)

    async def delete(uid, ids):
        events.append(("canonical", uid, ids))
        return {"deletionIds": ["f" * 32], "messageIds": ids, "pending": True}

    async def clear_local(instance, uid, identities):
        assert instance is runtime
        assert identities == [local_id]
        events.append(("local", uid))
        return [local_id]

    runtime.delete = AsyncMock(side_effect=delete)
    reader = AsyncMock(side_effect=clear_local)
    monkeypatch.setattr(source, "clear_local_replies", reader, raising=False)
    try:
        receipt = await runtime.clear(UID)
        assert receipt["messageIds"] == ([canonical_id] if has_originals else [])
        assert receipt["localMessageIds"] == [local_id]
        assert events == ([("canonical", UID, [canonical_id])] if has_originals else []) + [("local", UID)]
        snapshot.assert_awaited_once_with(runtime, UID)
        reader.assert_awaited_once_with(runtime, UID, [local_id])
        if has_originals:
            runtime.delete.assert_awaited_once_with(UID, [canonical_id])
        else:
            runtime.delete.assert_not_awaited()
        assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_clear_does_not_report_success_when_local_reply_cleanup_fails(tmp_path, monkeypatch):
    """A local-only storage failure must remain visible instead of claiming completion."""
    from unittest.mock import AsyncMock
    import services.chat_sync.source as source

    runtime, _, _ = setup_runtime(tmp_path)
    runtime.history = AsyncMock(return_value=[])
    monkeypatch.setattr(source, "known_source_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(source, "local_reply_ids", AsyncMock(return_value=["e" * 32]), raising=False)
    monkeypatch.setattr(source, "clear_local_replies", AsyncMock(side_effect=OSError("synthetic storage fault")), raising=False)
    try:
        with pytest.raises(OSError, match="synthetic storage fault"):
            await runtime.clear(UID)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_clear_preserves_canonical_writer_outage_receipt(tmp_path, monkeypatch):
    """Local cleanup cannot replace the existing canonical outage deletion contract."""
    from unittest.mock import AsyncMock
    import services.chat_sync.recovery as recovery
    import services.chat_sync.source as source
    from services.chat_sync.writer import WriterQuarantined

    runtime, _, _ = setup_runtime(tmp_path)
    canonical_id, local_id = "d" * 32, "e" * 32
    runtime.history = AsyncMock(side_effect=WriterQuarantined())
    monkeypatch.setattr(source, "local_reply_ids", AsyncMock(return_value=[local_id]), raising=False)
    monkeypatch.setattr(recovery, "quarantined_local_ids", lambda instance, uid: [canonical_id])
    runtime.delete = AsyncMock(return_value={"deletionIds": [], "messageIds": [canonical_id],
                                            "pending": False, "recoveryRequired": True})
    local = AsyncMock(return_value=[local_id])
    monkeypatch.setattr(source, "clear_local_replies", local)
    try:
        assert await runtime.clear(UID) == {"deletionIds": [], "messageIds": [canonical_id], "pending": False,
                                            "recoveryRequired": True, "localMessageIds": [local_id]}
        runtime.delete.assert_awaited_once_with(UID, [canonical_id])
        local.assert_awaited_once_with(runtime, UID, [local_id])
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [False, True])
async def test_clear_removes_real_local_reply_bytes_without_cloud_markers(tmp_path, bound):
    """Local tombstones survive retries while another role and sync IDs stay separate."""
    runtime, service, _ = setup_runtime(tmp_path)
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    local_id, other_id = "e" * 32, "f" * 32
    raw = ChatOriginal("assistant", "  synthetic local reply\r\n🙂  ", original_time="1791540000123")
    source.commit_local_reply(UID, local_id, raw, "agent")
    source.commit_local_reply(OTHER_UID, other_id, raw, "tool")
    canonical = []
    if bound:
        runtime.state.prepare_binding(DOMAIN)
        canonical = [capture(runtime)["messageId"]]
    try:
        assert [item["messageId"] for item in await runtime.local_replies(UID)] == [local_id]
        receipt = await runtime.clear(UID)
        assert receipt["messageIds"] == canonical
        assert receipt["localMessageIds"] == [local_id]
        assert await runtime.local_replies(UID) == []
        assert [item["messageId"] for item in await runtime.local_replies(OTHER_UID)] == [other_id]
        assert [identity for deletion in runtime.ledger.pending_deletions(DOMAIN)
                for identity in deletion.message_ids] == canonical
        assert service.requests == []
        with sqlite3.connect(source.path) as connection:
            assert connection.execute("SELECT original FROM source_local_replies WHERE record_id=?", (local_id,)).fetchone() == (None,)
        reopened = ChatOriginalSource(source.path)
        reopened.commit_local_reply(UID, local_id, raw, "agent")
        assert await runtime.local_replies(UID) == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_clear_preserves_local_reply_arriving_during_canonical_delete(tmp_path, monkeypatch):
    """Awaiting canonical deletion cannot widen the already reviewed local boundary."""
    runtime, service, _ = setup_runtime(tmp_path)
    runtime.state.prepare_binding(DOMAIN)
    canonical_id = capture(runtime)["messageId"]
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    old_id, new_id, other_id = "d" * 32, "e" * 32, "f" * 32
    raw = ChatOriginal("assistant", "synthetic local reply", original_time="1791540000123")
    source.commit_local_reply(UID, old_id, raw, "agent")
    source.commit_local_reply(OTHER_UID, other_id, raw, "tool")
    entered, release = asyncio.Event(), asyncio.Event()
    original_delete = runtime.delete

    async def delayed_delete(uid, identities):
        assert uid == UID and identities == [canonical_id]
        entered.set()
        await release.wait()
        return await original_delete(uid, identities)

    monkeypatch.setattr(runtime, "delete", delayed_delete)
    clearing = asyncio.create_task(runtime.clear(UID))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        source.commit_local_reply(UID, new_id, raw, "agent")
        release.set()
        receipt = await clearing
        assert receipt["localMessageIds"] == [old_id]
        assert [item["messageId"] for item in await runtime.local_replies(UID)] == [new_id]
        assert [item["messageId"] for item in await runtime.local_replies(OTHER_UID)] == [other_id]
        assert [identity for deletion in runtime.ledger.pending_deletions(DOMAIN)
                for identity in deletion.message_ids] == [canonical_id]
        with sqlite3.connect(source.path) as connection:
            assert connection.execute("SELECT original FROM source_local_replies WHERE record_id=?", (old_id,)).fetchone() == (None,)
            assert connection.execute("SELECT original FROM source_local_replies WHERE record_id=?", (new_id,)).fetchone()[0] is not None
        assert service.requests == []
    finally:
        release.set()
        await asyncio.gather(clearing, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_local_history_hint_observes_only_committed_download_and_is_owned(tmp_path):
    """A lightweight local hint cannot precede the fenced page commit or outlive close."""
    runtime, _, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    observed, started = [], asyncio.Event()

    async def notify(uid):
        observed.append((uid, runtime.ledger.cursor(DOMAIN)))
        started.set()
        await asyncio.Event().wait()

    runtime.history_changed = notify
    receipt = await runtime._channel_pass(UID, "pull", manual=True)
    assert receipt["cursor"] == 1
    for _ in range(30):
        if observed:
            break
        await asyncio.sleep(.01)
    assert observed == [(UID, 1)]
    assert started.is_set()
    await runtime._channel_pass(UID, "pull", manual=True)
    assert observed == [(UID, 1)]
    await runtime.close()
    assert not runtime._background


@pytest.mark.asyncio
async def test_production_history_notifier_routes_only_exact_local_character_uid(tmp_path, monkeypatch):
    """The production factory sends the safe hint through the existing role socket."""
    import services.chat_sync.runtime as module
    import main_routers.shared_state as shared

    sent = []

    async def send_json(payload):
        sent.append(payload)

    character_data = {"当前猫娘": "Synthetic", "猫娘": {"Synthetic": {"_reserved": {"character_uid": UID}},
                       "Other": {"_reserved": {"character_uid": OTHER_UID}}}}

    async def characters():
        return character_data

    config = SimpleNamespace(app_docs_dir=tmp_path / "selected", local_state_dir=tmp_path / "anchor",
                             aload_characters=characters)
    monkeypatch.setattr(module, "_runtimes", {})
    monkeypatch.setattr(module, "_runtime_lock", asyncio.Lock())
    monkeypatch.setattr(shared, "get_session_manager", lambda: {"Synthetic": SimpleNamespace(
        websocket=SimpleNamespace(send_json=send_json))})
    runtime = await module.get_desktop_chat_sync_runtime(config)
    try:
        await runtime.history_changed(UID)
        await runtime.history_changed(OTHER_UID)
        assert sent == [{"type": "chat_sync_history_changed", "characterSyncId": UID}]
        assert await runtime.current_character_uid_provider() == UID
        character_data["当前猫娘"] = "Other"
        assert await runtime.current_character_uid_provider() == OTHER_UID
        character_data["当前猫娘"] = "Absent"
        assert await runtime.current_character_uid_provider() is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_rejected_download_commit_never_hints_and_hint_failure_cannot_undo_commit(tmp_path):
    """Window delivery is optional, while the writer/session commit is authoritative."""
    runtime, _, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    observed = []

    async def notify(uid):
        observed.append(uid)
        raise RuntimeError("synthetic window closed")

    runtime.history_changed = notify
    commit_bound = runtime._commit_bound

    def reject(binding, operation, *args, **kwargs):
        if operation == runtime.ledger.apply_changes:
            raise ValueError("chat_sync_unavailable")
        return commit_bound(binding, operation, *args, **kwargs)

    runtime._commit_bound = reject
    with pytest.raises(ValueError, match="chat_sync_unavailable"):
        await runtime._channel_pass(UID, "pull", manual=True)
    assert observed == [] and runtime.ledger.cursor(DOMAIN) == 0
    runtime._commit_bound = commit_bound
    runtime.test_clock[0] += 5000
    receipt = await runtime._channel_pass(UID, "pull", manual=True)
    for _ in range(30):
        if observed:
            break
        await asyncio.sleep(.01)
    assert observed == [UID] and receipt["cursor"] == runtime.ledger.cursor(DOMAIN) == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_manual_page_commit_returning_after_close_creates_no_hint_task(tmp_path):
    """A worker can finish its fenced commit before shutdown snapshots owned tasks."""
    runtime, _, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    committed, release = threading.Event(), threading.Event()
    commit_bound, track = runtime._commit_bound, runtime._track
    tracked = []

    async def notify(uid):
        raise AssertionError("closed runtime must not notify")

    def delayed(binding, operation, *args, **kwargs):
        result = commit_bound(binding, operation, *args, **kwargs)
        if operation == runtime.ledger.apply_changes:
            committed.set()
            assert release.wait(timeout=3)
        return result

    def record(task):
        tracked.append(task)
        track(task)

    runtime.history_changed = notify
    runtime._commit_bound, runtime._track = delayed, record
    manual = asyncio.create_task(runtime._channel_pass(UID, "pull", manual=True))
    try:
        for _ in range(100):
            if committed.is_set():
                break
            await asyncio.sleep(.01)
        assert committed.is_set()
        await runtime.close()
    finally:
        release.set()
    await manual
    assert tracked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
async def test_body_deadline_wakes_at_200ms_without_retry_spin(tmp_path, monkeypatch, retry):
    """Use the observed queue deadline, then return to a bounded tick if admission blocks."""
    runtime, service, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    runtime.ledger.capture(DOMAIN, SourceLocator("deadline", "generation", "1"), ChatOriginal("user", "synthetic"),
                           queued_at_ms=runtime.test_clock[0])
    if retry:
        runtime.scheduler.failure(DOMAIN.key(), "body", runtime.state.binding(UID)["generation"],
                                  await runtime.session_presence_provider(), ValueError("chat_sync_unavailable"))
    service.requests.clear()
    waits, passes = [], []

    async def tick():
        passes.append(runtime.test_clock[0])
        await runtime._channel_pass(UID, "body", presence=await runtime.session_presence_provider())
        if len(passes) == 4:
            runtime._closing = True
            runtime._wake.set()

    async def wait_for(awaitable, timeout):
        if runtime._wake.is_set():
            return await awaitable
        awaitable.close()
        waits.append(timeout)
        runtime.test_clock[0] += round(timeout * 1000)
        raise TimeoutError

    monkeypatch.setattr(runtime, "scheduler_tick", tick)
    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    await runtime._run_scheduler()
    assert waits[0] == pytest.approx(.2)
    assert passes[2] == 1_000_200
    assert waits[1] == 1
    bodies = [request for request in service.requests if "/batches/" in request.url.path]
    assert len(bodies) == (0 if retry else 1)
    await runtime.close()


class SyntheticNotifications:
    """Expose real client frame handling without a network or developer credentials."""

    def __init__(self, *, acknowledge=True):
        self.incoming = asyncio.Queue()
        self.frames = []
        self.connections = 0
        self.closed = False
        self.acknowledge = acknowledge
        self.receives = 0

    @asynccontextmanager
    async def connect(self, origin):
        assert origin == "https://sync.test"
        self.connections += 1
        try:
            yield self
        finally:
            self.closed = True

    async def send(self, raw):
        frame = json.loads(raw)
        self.frames.append(frame)
        if frame["type"] == "authenticate":
            await self.incoming.put(json.dumps({"type": "ready"}))
        elif self.acknowledge:
            await self.incoming.put(json.dumps({"type": "subscribed"}))

    async def recv(self):
        self.receives += 1
        raw = await self.incoming.get()
        if isinstance(raw, BaseException):
            raise raw
        return raw


async def wait_notification(predicate):
    """Wait only for finite synthetic callbacks, with a short failure deadline."""
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.001)


@pytest.mark.asyncio
async def test_subscribed_repairs_legacy_local_login_blockers_without_changing_delivery_progress(tmp_path):
    """Only the successful grant acknowledgement repairs the same saved login's old blocker."""
    runtime, service, _ = setup_runtime(tmp_path)
    socket = SyntheticNotifications(acknowledge=False)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        binding = runtime.state.binding(UID)
        presence = await runtime.session_presence_provider()
        for channel in ("pull", "profile"):
            runtime.scheduler.failure(DOMAIN.key(), channel, binding["generation"], presence,
                                      ValueError("chat_sync_login_required"))
        runtime._notification_connect = socket.connect
        service.requests.clear()
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: len(socket.frames) == 2)
        await runtime.request_catchup()
        for channel in ("pull", "profile"):
            assert not runtime.scheduler.ready(DOMAIN.key(), channel, binding["generation"], presence)
        await socket.incoming.put(json.dumps({"type": "subscribed"}))
        await wait_notification(lambda: socket.receives >= 3)
        for channel in ("pull", "profile"):
            assert runtime.scheduler.ready(DOMAIN.key(), channel, binding["generation"], presence)
        assert runtime.ledger.cursor(DOMAIN) == 0 and service.requests == []
        receipt = await runtime._channel_pass(UID, "pull", presence=presence)
        assert receipt["cursor"] == runtime.ledger.cursor(DOMAIN) == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("hint", ["changed", "catchup", "disconnect"])
async def test_ordinary_notification_hints_cannot_repair_a_later_local_login_blocker(tmp_path, hint):
    """An established connection's generic hints carry no new authentication proof."""
    runtime, service, _ = setup_runtime(tmp_path)
    socket = SyntheticNotifications()
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        binding = runtime.state.binding(UID)
        presence = await runtime.session_presence_provider()
        runtime._notification_connect = socket.connect
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: socket.receives >= 3)
        runtime.scheduler.failure(DOMAIN.key(), "pull", binding["generation"], presence,
                                  ValueError("chat_sync_login_required"))
        service.requests.clear()
        if hint == "changed":
            receives = socket.receives
            await socket.incoming.put(json.dumps({"type": "changed", "characterSyncId": UID, "channel": "chat"}))
            await wait_notification(lambda: socket.receives > receives)
        elif hint == "catchup":
            await runtime.request_catchup()
        else:
            await socket.incoming.put(OSError("synthetic disconnected"))
            await wait_notification(lambda: runtime._notification_task.done())
        assert not runtime.scheduler.ready(DOMAIN.key(), "pull", binding["generation"], presence)
        assert runtime.scheduler.last_error(DOMAIN.key()) == "chat_sync_login_required"
        assert runtime.ledger.cursor(DOMAIN) == 0 and service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_disconnect_before_subscribed_cannot_repair_legacy_login_blocker(tmp_path):
    """A ready frame and subscribe request do not establish a successful grant."""
    runtime, service, _ = setup_runtime(tmp_path)
    socket = SyntheticNotifications(acknowledge=False)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        binding = runtime.state.binding(UID)
        presence = await runtime.session_presence_provider()
        runtime.scheduler.failure(DOMAIN.key(), "pull", binding["generation"], presence,
                                  ValueError("chat_sync_login_required"))
        runtime._notification_connect = socket.connect
        service.requests.clear()
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: len(socket.frames) == 2)
        await socket.incoming.put(OSError("synthetic disconnected before subscribed"))
        await wait_notification(lambda: runtime._notification_task.done())
        assert not runtime.scheduler.ready(DOMAIN.key(), "pull", binding["generation"], presence)
        assert runtime.scheduler.last_error(DOMAIN.key()) == "chat_sync_login_required"
        assert runtime.ledger.cursor(DOMAIN) == 0 and service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "logout", "replacement", "writer_epoch", "writer_quarantined"])
async def test_late_subscribed_cannot_repair_a_login_blocker_after_authority_changes(tmp_path, monkeypatch, change):
    """A late acknowledgement never borrows a newer grant or another saved session."""
    runtime, service, session = setup_runtime(tmp_path)
    socket = SyntheticNotifications(acknowledge=False)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        binding = runtime.state.binding(UID)
        presence = await runtime.session_presence_provider()
        runtime.scheduler.failure(DOMAIN.key(), "pull", binding["generation"], presence,
                                  ValueError("chat_sync_login_required"))
        runtime._notification_connect = socket.connect
        service.requests.clear()
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: len(socket.frames) == 2)
        if change == "generation":
            await runtime.pause(UID)
            runtime.state.activate(runtime.state.binding(UID), BINDING)
        elif change == "logout":
            session["logged_in"] = False
        elif change == "replacement":
            session["snapshot"]["access_token"] = "synthetic-new"
        elif change == "writer_epoch":
            # Rotate only this disposable fixture's installation authority while
            # retaining the blocker to test the late acknowledgement fence.
            epoch = runtime.writer.epoch()
            runtime.writer.recover(lambda: None)
            assert runtime.writer.epoch() != epoch
        else:
            from services.chat_sync.writer import WriterQuarantined

            def require_epoch(expected):
                raise WriterQuarantined()

            monkeypatch.setattr(runtime.writer, "require_epoch", require_epoch)
        await socket.incoming.put(json.dumps({"type": "subscribed"}))
        await wait_notification(lambda: runtime._notification_task.done())
        assert not runtime.scheduler.ready(DOMAIN.key(), "pull", binding["generation"], presence)
        assert runtime.scheduler.last_error(DOMAIN.key()) == "chat_sync_login_required"
        assert runtime.ledger.cursor(DOMAIN) == 0 and service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    ChatSyncServiceError(401), ChatSyncServiceError(403), ChatSyncServiceError(429, 240000),
    ChatSyncServiceError(503), ValueError("invalid_chat_sync_response"), ValueError("chat_sync_session_changed"),
])
async def test_successful_subscribed_preserves_independent_http_and_schema_fences(tmp_path, failure):
    """A verified socket cannot forgive HTTP rejection, throttling or invalid pages."""
    runtime, service, _ = setup_runtime(tmp_path)
    socket = SyntheticNotifications(acknowledge=False)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        binding = runtime.state.binding(UID)
        presence = await runtime.session_presence_provider()
        runtime._notification_connect = socket.connect
        service.requests.clear()
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: len(socket.frames) == 2)
        runtime.scheduler.failure(DOMAIN.key(), "pull", binding["generation"], presence, failure)
        await socket.incoming.put(json.dumps({"type": "subscribed"}))
        await wait_notification(lambda: socket.receives >= 3)
        assert not runtime.scheduler.ready(DOMAIN.key(), "pull", binding["generation"], presence, manual=True)
        assert runtime.ledger.cursor(DOMAIN) == 0 and service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_notification_subscribed_catches_up_current_role_without_consuming_cursor(tmp_path):
    """A subscribe acknowledgement wakes only the selected role's channels."""
    runtime, service, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        other = ChatDomain(OWNER, OTHER_UID)
        runtime.state.activate(runtime.state.prepare_binding(other), BINDING)
        capture(runtime)
        presence = await runtime.session_presence_provider()
        for domain in (DOMAIN, other):
            for channel in ("pull", "profile"):
                assert runtime.scheduler.claim(domain.key(), channel, 1, presence)
                runtime.scheduler.success(domain.key(), channel)
        socket = SyntheticNotifications(acknowledge=False)
        runtime._notification_connect = socket.connect
        service.requests.clear()
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: len(socket.frames) == 2)
        assert not runtime.scheduler.ready(DOMAIN.key(), "pull", 1, presence)
        assert socket.frames == [
            {"type": "authenticate", "token": "synthetic-a"},
            {"type": "subscribe", "characters": [
                {"characterSyncId": UID, "bindingId": BINDING}]}]
        await socket.incoming.put(json.dumps({"type": "subscribed"}))
        await wait_notification(lambda: runtime.scheduler.ready(DOMAIN.key(), "profile", 1, presence))
        for domain in (DOMAIN, other):
            for channel in ("pull", "profile"):
                assert runtime.scheduler.ready(domain.key(), channel, 1, presence) == (domain == DOMAIN)
            assert runtime.ledger.stats(domain)["cursor"] == 0
        assert len(runtime.ledger.pending(DOMAIN)) == 1
        assert service.requests == []
    finally:
        await runtime.close()
    assert socket.closed and not runtime._background


@pytest.mark.asyncio
async def test_changed_notification_coalesces_only_its_channel_without_http_or_ack(tmp_path):
    """Duplicate profile hints do not clear chat due time or consume pending raw."""
    runtime, service, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    capture(runtime)
    presence = await runtime.session_presence_provider()
    socket = SyntheticNotifications()
    runtime._notification_connect = socket.connect
    await runtime._manage_notifications(runtime.state.bindings(), presence)
    await wait_notification(lambda: socket.receives >= 3)
    for channel in ("pull", "profile"):
        assert runtime.scheduler.claim(DOMAIN.key(), channel, 1, presence)
        runtime.scheduler.success(DOMAIN.key(), channel)
    service.requests.clear()
    for _ in range(2):
        await socket.incoming.put(json.dumps({"type": "changed", "characterSyncId": UID, "channel": "profile"}))
    await wait_notification(lambda: socket.receives >= 5)
    assert runtime.scheduler.ready(DOMAIN.key(), "profile", 1, presence)
    assert not runtime.scheduler.ready(DOMAIN.key(), "pull", 1, presence)
    assert runtime.ledger.stats(DOMAIN)["cursor"] == 0
    assert len(runtime.ledger.pending(DOMAIN)) == 1 and service.requests == []
    await runtime.close()


@pytest.mark.asyncio
async def test_notification_production_transport_uses_verified_wss_and_closes(tmp_path, local_tls_files):
    """Exercise the real WSS connector against a disposable TLS socket, not Auth."""
    from websockets.asyncio.server import serve
    ca_path, _, cert_path, key_path = local_tls_files
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    received = []

    async def peer(socket):
        assert socket.request.path == "/v1/chat-sync/notifications"
        received.append(json.loads(await socket.recv()))
        await socket.send(json.dumps({"type": "ready"}))
        received.append(json.loads(await socket.recv()))
        await socket.send(json.dumps({"type": "subscribed"}))
        await socket.wait_closed()

    runtime, _, _ = setup_runtime(tmp_path)
    async with serve(peer, "127.0.0.1", 0, ssl=context) as server:
        runtime._local_ca_file = str(ca_path)
        runtime._notification_connect = runtime._connect_notifications
        await runtime.configure(f"https://localhost:{server.sockets[0].getsockname()[1]}")
        await runtime.enable(UID, "Neko")
        presence = await runtime.session_presence_provider()
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: len(received) == 2)
        assert received[0] == {"type": "authenticate", "token": "synthetic-a"}
        assert received[1] == {"type": "subscribe", "characters": [{"characterSyncId": UID, "bindingId": BINDING}]}
        await runtime.close()
        assert not runtime._background


@pytest.mark.asyncio
async def test_established_silent_wss_automatically_wakes_pull_within_eight_seconds(tmp_path, local_tls_files):
    """Use production keepalive against a TLS peer that silently stops reading."""
    from websockets.asyncio.server import serve
    ca_path, _, cert_path, key_path = local_tls_files
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    pause, paused, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def peer(socket):
        await socket.recv()
        await socket.send(json.dumps({"type": "ready"}))
        await socket.recv()
        await socket.send(json.dumps({"type": "subscribed"}))
        await pause.wait()
        socket.transport.pause_reading()
        paused.set()
        try:
            await release.wait()
        finally:
            socket.transport.resume_reading()

    runtime, service, _ = setup_runtime(tmp_path)
    try:
        async with serve(peer, "127.0.0.1", 0, ssl=context, ping_interval=None) as server:
            runtime._local_ca_file = str(ca_path)
            runtime._notification_connect = runtime._connect_notifications
            await runtime.configure(f"https://localhost:{server.sockets[0].getsockname()[1]}")
            await runtime.enable(UID, "Neko")
            try:
                runtime.start_scheduler()
                await wait_notification(lambda: any(x.url.path.endswith('/changes') for x in service.requests))
                await finish_automatic_flights(runtime)
                before = sum(x.url.path.endswith('/changes') for x in service.requests)
                pause.set()
                await paused.wait()
                async with asyncio.timeout(7.5):
                    while sum(x.url.path.endswith('/changes') for x in service.requests) == before:
                        await asyncio.sleep(.02)
                assert runtime.ledger.cursor(DOMAIN) == 1
            finally:
                # Release the silent peer before server shutdown even on RED.
                release.set()
                await runtime.close()
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_transport_disconnect_reconnect_deadline_is_at_most_three_seconds(tmp_path):
    """A new network failure stays bounded independently of HTTP and auth gates."""
    runtime, _, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        socket = SyntheticNotifications()
        runtime._notification_connect = socket.connect
        presence = await runtime.session_presence_provider()
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: socket.receives >= 3)
        await socket.incoming.put(OSError("synthetic disconnected"))
        await wait_notification(lambda: runtime._notification_task.done())
        assert 0 < runtime._notification_retry_at - runtime.scheduler.clock() <= 3000
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("sent_code", [1002, 1007, 1008, 1009, 1011, None])
async def test_sent_only_close_keeps_protocol_backoff_and_fast_transport_recovery(tmp_path, sent_code):
    """A missing close reply must not turn local protocol rejection into transport loss."""
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    runtime, _, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        socket = SyntheticNotifications()
        runtime._notification_connect = socket.connect
        presence = await runtime.session_presence_provider()
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: socket.receives >= 3)
        sent = Close(sent_code, "") if sent_code is not None else None
        await socket.incoming.put(ConnectionClosedError(None, sent))
        await wait_notification(lambda: runtime._notification_task.done())
        delay = runtime._notification_retry_at - runtime.scheduler.clock()
        if sent_code in {1002, 1007, 1008, 1009}:
            assert delay == 5000
        else:
            # Native keepalive expiry sends 1011; a broken network may send nothing.
            assert 0 < delay <= 3000
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["pause", "logout", "replacement", "close"])
async def test_late_notification_cannot_borrow_changed_authority(tmp_path, change):
    """A previously subscribed socket cannot commit after the captured grant ends."""
    runtime, _, session = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    presence = await runtime.session_presence_provider()
    socket = SyntheticNotifications()
    runtime._notification_connect = socket.connect
    await runtime._manage_notifications(runtime.state.bindings(), presence)
    await wait_notification(lambda: socket.receives >= 3)
    assert runtime.scheduler.claim(DOMAIN.key(), "pull", 1, presence)
    runtime.scheduler.success(DOMAIN.key(), "pull")
    if change == "pause":
        await runtime.pause(UID)
    elif change == "logout":
        session["logged_in"] = False
    elif change == "replacement":
        session["snapshot"]["access_token"] = "synthetic-new"
    else:
        await runtime.close()
    await socket.incoming.put(json.dumps({"type": "changed", "characterSyncId": UID, "channel": "chat"}))
    if change != "close":
        await wait_notification(lambda: runtime._notification_task.done())
    assert runtime.ledger.stats(DOMAIN)["cursor"] == 0
    # Logout/refresh cannot reuse the old hint, and pause has no enabled grant.
    assert not runtime.scheduler.ready(DOMAIN.key(), "pull", 1, presence)
    await runtime.close()
    assert socket.closed


@pytest.mark.asyncio
async def test_notification_disconnect_backoff_and_foreground_keep_http_fences(tmp_path):
    """Transient connection loss wakes fallback but cannot erase HTTP backoff."""
    from services.chat_sync.runtime import ChatSyncServiceError
    runtime, _, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    presence = await runtime.session_presence_provider()
    socket = SyntheticNotifications()
    runtime._notification_connect = socket.connect
    await runtime._manage_notifications(runtime.state.bindings(), presence)
    await wait_notification(lambda: socket.receives >= 3)
    runtime.scheduler.failure(DOMAIN.key(), "pull", 1, presence, ChatSyncServiceError(429, 240000))
    await socket.incoming.put(OSError("synthetic disconnected"))
    await wait_notification(lambda: runtime._notification_task.done())
    await runtime.request_catchup()
    assert not runtime.scheduler.ready(DOMAIN.key(), "pull", 1, presence)
    await runtime._manage_notifications(runtime.state.bindings(), presence)
    assert socket.connections == 1
    runtime.test_clock[0] += 5000
    await runtime._manage_notifications(runtime.state.bindings(), presence)
    assert socket.connections == 1
    runtime.test_clock[0] += 235000
    await runtime._manage_notifications(runtime.state.bindings(), presence)
    await wait_notification(lambda: socket.connections == 2)
    assert runtime.ledger.stats(DOMAIN)["cursor"] == 0
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [4401, 4403, 1008])
async def test_notification_rejection_waits_for_new_authority(tmp_path, code):
    """Server authentication/grant rejection cannot become an infinite reconnect."""
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close
    runtime, service, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    presence = await runtime.session_presence_provider()
    socket = SyntheticNotifications()
    runtime._notification_connect = socket.connect
    await runtime._manage_notifications(runtime.state.bindings(), presence)
    await wait_notification(lambda: socket.receives >= 3)
    await socket.incoming.put(ConnectionClosedError(Close(code, ""), None))
    await wait_notification(lambda: runtime._notification_task.done())
    runtime.test_clock[0] += 999999
    await runtime._manage_notifications(runtime.state.bindings(), presence)
    assert socket.connections == 1
    service.requests.clear()
    await runtime.scheduler_tick()
    await finish_automatic_flights(runtime)
    assert any(request.url.path.endswith("/changes") for request in service.requests)
    assert socket.connections == 1
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("frame", [
    {"type": "changed", "characterSyncId": UID, "channel": "binding"},
    {"type": "changed", "characterSyncId": "f" * 32, "channel": "chat"},
    {"type": "changed", "characterSyncId": UID, "channel": "chat", "cursor": 999},
    {"type": "changed", "characterSyncId": UID, "channel": ["chat"]},
    {"type": "subscribed"}, None,
])
async def test_invalid_notification_never_applies_server_progress_or_payload(tmp_path, frame):
    """Malformed/foreign hints close the connection; only fenced HTTP can recover."""
    runtime, service, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    capture(runtime)
    presence = await runtime.session_presence_provider()
    socket = SyntheticNotifications()
    runtime._notification_connect = socket.connect
    await runtime._manage_notifications(runtime.state.bindings(), presence)
    await wait_notification(lambda: socket.receives >= 3)
    service.requests.clear()
    await socket.incoming.put(json.dumps(frame))
    await wait_notification(lambda: runtime._notification_task.done())
    assert socket.closed and service.requests == []
    assert runtime.ledger.stats(DOMAIN)["cursor"] == 0
    assert len(runtime.ledger.pending(DOMAIN)) == 1
    assert runtime._notification_retry_at > runtime.scheduler.clock()
    await runtime.close()


@pytest.mark.asyncio
async def test_two_roles_still_share_one_inflight_upload_without_normal_cooldown(tmp_path):
    """Removing a timer never permits concurrent body sends from this device."""
    runtime, service, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    other = ChatDomain(OWNER, OTHER_UID)
    runtime.state.activate(runtime.state.prepare_binding(other), BINDING)
    capture(runtime)
    runtime.ledger.capture(other, SourceLocator("other", "generation", "1"), ChatOriginal("user", "other"))
    entered, release = asyncio.Event(), asyncio.Event()
    bodies = []

    async def block(request):
        if "/batches/" in request.url.path:
            bodies.append(request)
            if len(bodies) == 1:
                entered.set()
                await release.wait()

    service.on_request = block
    first = asyncio.create_task(runtime._channel_pass(UID, "body", manual=True))
    await asyncio.wait_for(entered.wait(), timeout=3)
    second = asyncio.create_task(runtime._channel_pass(OTHER_UID, "body", manual=True))
    await asyncio.sleep(.01)
    assert len(bodies) == 1
    release.set()
    assert [receipt["uploaded"] for receipt in await asyncio.gather(first, second)] == [1, 1]
    assert len(bodies) == 2
    await runtime.close()


@pytest.mark.asyncio
async def test_bounded_pull_continues_next_pass_until_terminal_without_periodic_wait(tmp_path):
    """A finite page budget yields between passes without stranding later pages."""
    class PagedService(SyntheticService):
        async def request(self, request):
            if request.url.path.endswith("/changes"):
                self.requests.append(request)
                after = int(request.url.params["after"])
                return httpx.Response(200, json={"cursor": after + 1, "hasMore": after < 2,
                                                "originals": [], "deletedMessageIds": []})
            return await super().request(request)

    runtime, service, _ = setup_runtime(tmp_path, PagedService())
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    service.requests.clear()
    for cursor in (1, 2, 3):
        receipt = await runtime._channel_pass(UID, "pull", max_pull_pages=1)
        assert receipt is not None
        assert receipt["cursor"] == cursor and receipt["pullPages"] == 1
        assert receipt["hasMore"] == (cursor < 3)
    assert sum(request.url.path.endswith("/changes") for request in service.requests) == 3
    assert await runtime._channel_pass(UID, "pull", max_pull_pages=1) is None
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["approved", "draft", "retry", "throttled", "auth"])
async def test_approved_profile_wakes_promptly_without_approving_drafts_or_bypassing_errors(tmp_path, case):
    """Only an already sealed approved operation bypasses periodic profile due."""
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from services.chat_sync.runtime import ChatSyncServiceError
    from tests.unit.test_character_profile_sync import Config, ProfileService
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Synthetic")
    presence = await runtime.session_presence_provider()
    local = await runtime.profiles.local(UID)
    generation = runtime.state.binding(UID)["generation"]
    assert runtime.scheduler.claim(DOMAIN.key(), "profile", generation, presence)
    runtime.scheduler.success(DOMAIN.key(), "profile")
    operation = None
    if case != "draft":
        await runtime.profiles.approve(UID, local["local_hash"], 0)
        operation = runtime.profiles.store.next_operation(DOMAIN.key())
    if case in {"retry", "throttled", "auth"}:
        runtime.scheduler.failure(DOMAIN.key(), "profile", generation, presence,
                                  ChatSyncServiceError({"retry": 503, "throttled": 429, "auth": 403}[case], 240000))
    service.requests.clear()
    await runtime._automatic_profiles(runtime.state.bindings(), presence)
    if case == "approved":
        sent = [request for request in service.profile_requests if request.method == "PUT"]
        assert len(sent) == 1 and sent[0].content == bytes(operation["body"])
        assert runtime.profiles.store.next_operation(DOMAIN.key()) is None
    else:
        assert service.requests == []
        retained = runtime.profiles.store.next_operation(DOMAIN.key())
        assert retained == operation
    await runtime.close()


class HistoryService(SyntheticService):
    """Expose authenticated cloud names without involving a live account."""

    async def request(self, request):
        """Keep history preview metadata separate from uploaded raw payloads."""
        if request.url.path == "/prototype/chat-sync/characters":
            self.requests.append(request)
            if self.on_request is not None:
                await self.on_request(request)
            return httpx.Response(200, json={"characters": [{"characterSyncId": UID, "displayName": "Cloud Neko"}]})
        return await super().request(request)


@pytest.mark.asyncio
async def test_history_approval_freezes_members_and_restarts_pages_without_automatic_promotion(tmp_path):
    """A reviewed fixed set survives restart and does not include later originals."""
    runtime, service, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identities = [f"{index:032x}" for index in range(205)]
    for identity in identities:
        source.commit_unbound(UID, identity, ChatOriginal("user", "  raw\r\n🙂  "))
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Local Neko")
    await runtime._recover_originals()
    assert runtime.ledger.pending(DOMAIN) == []
    preview = await runtime.preview_history(UID, "Local Neko")
    assert {key: preview[key] for key in ("localName", "cloudName", "count", "unverifiedCount")} == {
        "localName": "Local Neko", "cloudName": "Cloud Neko", "count": 205, "unverifiedCount": None}
    assert "raw" not in json.dumps(preview)
    source.commit_unbound(UID, "f" * 32, ChatOriginal("user", "later"))
    approved = await runtime.approve_history(UID, preview["taskId"])
    assert approved["count"] == 205
    await runtime._advance_history(UID)
    await runtime._recover_originals()
    assert len(runtime.ledger.pending(DOMAIN)) == 200
    before = runtime.ledger.pending(DOMAIN)[0][0]
    await runtime.close()
    reopened, _, _ = setup_runtime(tmp_path, service)
    assert (await reopened.approve_history(UID, preview["taskId"]))["taskId"] == preview["taskId"]
    # Registration remains bounded by outstanding history, including across restart.
    assert await reopened._advance_history(UID) == 0
    batch = reopened.ledger.seal_batch(DOMAIN, byte_budget=1000000)
    reopened.ledger.ack_batch(DOMAIN, {"batchId": batch.batch_id, "messageIds": [row[0] for row in batch.members], "cursor": 0})
    await reopened._advance_history(UID)
    await reopened._recover_originals()
    payloads = [json.loads(raw) for raw in reopened.ledger.visible_originals(DOMAIN)]
    assert len(payloads) == 205 and {item["messageId"] for item in payloads} == set(identities)
    assert reopened.ledger.visible_originals(DOMAIN)[0] == before
    assert len({(item["originStreamId"], item["originSeq"]) for item in payloads}) == 205
    await reopened.close()


@pytest.mark.asyncio
async def test_history_deletion_cancels_unregistered_members_and_keeps_later_pages_runnable(tmp_path):
    """Removing a complete first page cannot hide the remaining approved task."""
    runtime, _, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identities = [f"{index:032x}" for index in range(405)]
    for identity in identities:
        source.commit_unbound(UID, identity, ChatOriginal("user", "secret"))
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    preview = await runtime.preview_history(UID, "Neko")
    await runtime.approve_history(UID, preview["taskId"])
    await runtime.delete(UID, identities[:201])
    await runtime._channel_pass(UID, "body", manual=True)
    await runtime._recover_originals()
    # Local registration precedes status selection even while marker upload waits.
    assert runtime.ledger.queue_state(DOMAIN)["pendingOriginals"] == 200
    batch = runtime.ledger.seal_batch(DOMAIN, byte_budget=1000000)
    runtime.ledger.ack_batch(DOMAIN, {"batchId": batch.batch_id, "messageIds": [row[0] for row in batch.members], "cursor": 0})
    await runtime._advance_history(UID)
    await runtime._recover_originals()
    assert {json.loads(raw)["messageId"] for raw in runtime.ledger.visible_originals(DOMAIN)} == set(identities[201:])
    with sqlite3.connect(source.path) as conn:
        assert conn.execute("SELECT count(*) FROM source_unbound WHERE deleted=1 AND original IS NOT NULL").fetchone()[0] == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_history_registration_waits_for_ack_and_deleting_first_page_releases_capacity(tmp_path):
    """Frequent local ticks cannot register the complete history behind one slow PUT."""
    runtime, _, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identities = [f"{index:032x}" for index in range(405)]
    for identity in identities:
        source.commit_unbound(UID, identity, ChatOriginal("user", "original"))
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    preview = await runtime.preview_history(UID, "Neko")
    await runtime.approve_history(UID, preview["taskId"])
    assert await runtime._advance_history(UID) == 200
    for _ in range(4):
        assert await runtime._advance_history(UID) == 0
    await runtime._recover_originals()
    assert await runtime._advance_history(UID) == 0
    await runtime.delete(UID, identities[:200])
    assert await runtime._advance_history(UID) == 200
    await runtime._recover_originals()
    assert {json.loads(raw)["messageId"] for raw in runtime.ledger.visible_originals(DOMAIN)} == set(identities[200:400])
    assert source.task(UID, preview["taskId"])["registered"] == 200
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["pause", "logout", "switch", "owner"])
async def test_history_approval_rejects_stale_preview(tmp_path, change):
    """A consent click cannot borrow a later session, generation or account."""
    runtime, service, session = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    source.commit_unbound(UID, "1" * 32, ChatOriginal("user", "original"))
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    preview = await runtime.preview_history(UID, "Neko")
    if change == "pause":
        await runtime.pause(UID)
        await runtime.enable(UID, "Neko")
    elif change == "logout":
        session["logged_in"] = False
    elif change == "switch":
        session["snapshot"]["access_token"] = "synthetic-b"
    else:
        service.owner = OTHER_OWNER
    with pytest.raises(ValueError):
        await runtime.approve_history(UID, preview["taskId"])
    assert source.pending(200) == [] and runtime.ledger.pending(DOMAIN) == []
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["device", "source_generation", "source_database"])
async def test_history_preview_cannot_approve_changed_local_identity(tmp_path, change):
    """A fixed consent snapshot is pinned to both source and installation identity."""
    runtime, _, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    source.commit_unbound(UID, "1" * 32, ChatOriginal("user", "before"))
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    preview = await runtime.preview_history(UID, "Neko")
    if change == "device":
        with sqlite3.connect(runtime.state.path) as conn:
            conn.execute("UPDATE sync_settings SET device_id=?", ("e" * 32,))
    else:
        with sqlite3.connect(source.path) as conn:
            column = "generation" if change == "source_generation" else "database_id"
            conn.execute(f"UPDATE source_identity SET {column}=?", ("e" * 32,))
    with pytest.raises(ValueError, match="chat_sync_history_changed"):
        await runtime.approve_history(UID, preview["taskId"])
    assert source.pending(200) == []
    await runtime.close()


@pytest.mark.asyncio
async def test_history_page_rejects_bytes_changed_after_preview_even_with_changed_hash(tmp_path):
    """Task members fix immutable content hashes as well as stable record IDs."""
    from dataclasses import asdict
    runtime, _, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    source.commit_unbound(UID, "1" * 32, ChatOriginal("user", "before"))
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    preview = await runtime.preview_history(UID, "Neko")
    await runtime.approve_history(UID, preview["taskId"])
    changed = json.dumps(asdict(ChatOriginal("user", "after")), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(source.path) as conn:
        conn.execute("UPDATE source_unbound SET original=?,original_hash=?", (changed, hashlib.sha256(changed.encode()).hexdigest()))
    with pytest.raises(ValueError, match="chat_source_integrity_error"):
        await runtime._advance_history(UID)
    assert source.pending(200) == [] and runtime.ledger.pending(DOMAIN) == []
    await runtime.close()


@pytest.mark.asyncio
async def test_approved_history_resumes_after_pause_and_source_page_crash_without_requeuing_ack(tmp_path):
    """A committed source page retains exact reply IDs through a lost ledger commit."""
    runtime, service, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    parent, reply = "1" * 32, "2" * 32
    source.commit_unbound(UID, parent, ChatOriginal("user", "  original\r\n🙂  "))
    source.commit_unbound(UID, reply, ChatOriginal("assistant", "reply", reply_to_message_id=parent))
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    preview = await runtime.preview_history(UID, "Neko")
    await runtime.approve_history(UID, preview["taskId"])
    await runtime.pause(UID)
    assert await runtime._advance_history(UID) == 0
    await runtime.enable(UID, "Neko")
    assert await runtime._advance_history(UID) == 2
    assert runtime.ledger.pending(DOMAIN) == []
    await runtime.close()
    reopened, _, _ = setup_runtime(tmp_path, service)
    await reopened._recover_originals()
    original_bytes = [raw for raw, _ in reopened.ledger.pending(DOMAIN)]
    originals = [json.loads(raw) for raw in original_bytes]
    assert originals[1]["replyToMessageId"] == parent and originals[1]["messageId"] == reply
    assert originals[0]["text"] == "  original\r\n🙂  "
    await reopened._channel_pass(UID, "body", manual=True)
    assert reopened.ledger.pending(DOMAIN) == []
    # A repeated consent and fresh preview never register delivered history again.
    assert (await reopened.approve_history(UID, preview["taskId"]))["status"] == "registered"
    assert (await reopened.preview_history(UID, "Renamed"))["count"] == 0
    await reopened._recover_originals()
    assert reopened.ledger.pending(DOMAIN) == []
    assert reopened.ledger.visible_originals(DOMAIN) == original_bytes
    await reopened.close()


@pytest.mark.asyncio
async def test_history_preview_leaves_unknown_legacy_and_other_role_sources_local(tmp_path):
    """Unknown scope and legacy rows never become candidates by matching text or UID."""
    runtime, _, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    source.commit_unbound(UID, "1" * 32, ChatOriginal("user", "same text"))
    source.commit_unbound(UID, "2" * 32, ChatOriginal("user", "unknown scope"))
    source.commit_unbound(OTHER_UID, "3" * 32, ChatOriginal("user", "same text"))
    source.commit_unbound(UID, "4" * 32, ChatOriginal("user", "unknown source order"))
    with sqlite3.connect(source.path) as conn:
        conn.execute("UPDATE source_unbound SET scope='unknown-legacy' WHERE record_id=?", ("2" * 32,))
        conn.execute("UPDATE source_unbound SET origin_stream_id=NULL,origin_seq=NULL WHERE record_id=?", ("4" * 32,))
    legacy = tmp_path / "time_indexed.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE history(message TEXT)")
        conn.execute("INSERT INTO history VALUES ('normalized derived text')")
    before = legacy.read_bytes()
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    preview = await runtime.preview_history(UID, "Neko")
    assert preview["count"] == 1 and preview["unverifiedCount"] is None
    other_domain = ChatDomain(OWNER, OTHER_UID)
    prepared = runtime.state.prepare_binding(other_domain)
    runtime.state.activate(prepared, BINDING)
    with pytest.raises(ValueError, match="chat_sync_history_changed"):
        await runtime.approve_history(OTHER_UID, preview["taskId"])
    await runtime.approve_history(UID, preview["taskId"])
    await runtime._advance_history(UID)
    await runtime._recover_originals()
    assert [json.loads(raw)["messageId"] for raw, _ in runtime.ledger.pending(DOMAIN)] == ["1" * 32]
    await runtime.delete(UID, ["1" * 32, "3" * 32])
    with sqlite3.connect(source.path) as conn:
        assert conn.execute("SELECT original FROM source_unbound WHERE record_id=?", ("3" * 32,)).fetchone()[0] is not None
    assert legacy.read_bytes() == before
    await runtime.close()


@pytest.mark.asyncio
async def test_unbound_clear_and_late_bound_retry_cannot_resurrect_raw_payload(tmp_path):
    """Local deletion protects source IDs even before an owner exists."""
    runtime, _, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identity = "1" * 32
    original = ChatOriginal("user", "local only")
    source.commit_unbound(UID, identity, original)
    assert (await runtime.clear(UID))["messageIds"] == [identity]
    assert runtime.state.binding(UID) is None
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    source.commit(OWNER, UID, identity, original)
    source.commit_unbound(UID, identity, original)
    assert (await runtime.preview_history(UID, "Neko"))["count"] == 0
    assert source.pending(200) == []
    with sqlite3.connect(source.path) as conn:
        assert conn.execute("SELECT original,deleted FROM source_unbound").fetchone() == (None, 1)
    await runtime.close()


@pytest.mark.asyncio
async def test_logout_during_cloud_name_lookup_cannot_prepare_history(tmp_path):
    """A late authenticated cloud response cannot create consent after logout."""
    runtime, service, session = setup_runtime(tmp_path, HistoryService())
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")

    async def logout(request):
        """Change the synthetic credential snapshot during cloud role discovery."""
        if request.url.path == "/prototype/chat-sync/characters":
            session["logged_in"] = False

    service.on_request = logout
    with pytest.raises(ValueError, match="chat_sync_session_changed"):
        await runtime.preview_history(UID, "Neko")
    assert await runtime._history_status(UID) is None
    await runtime.close()


@pytest.mark.asyncio
async def test_source_save_order_precedes_binding_and_history_approval_at_same_millisecond(tmp_path):
    """Delayed history registration cannot reverse one source's actual save order."""
    runtime, _, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    first = ChatOriginal("user", "A", original_time="123")
    second = ChatOriginal("assistant", "B", original_time="123", reply_to_message_id="1" * 32)
    source.commit_unbound(UID, "1" * 32, first)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    source.commit(OWNER, UID, "2" * 32, second)
    await runtime._recover_originals()
    before = runtime.ledger.pending(DOMAIN)[0][0]
    preview = await runtime.preview_history(UID, "Neko")
    await runtime.approve_history(UID, preview["taskId"])
    await runtime._advance_history(UID)
    await runtime._recover_originals()
    records = [json.loads(raw) for raw in runtime.ledger.visible_originals(DOMAIN)]
    by_text = {record["text"]: record for record in records}
    assert by_text["A"]["originStreamId"] == by_text["B"]["originStreamId"]
    assert by_text["A"]["originSeq"] < by_text["B"]["originSeq"]
    assert by_text["B"]["replyToMessageId"] == "1" * 32
    assert before in runtime.ledger.visible_originals(DOMAIN)
    ui_order = sorted(records, key=lambda record: (int(record["originalTime"]), record["originStreamId"], record["originSeq"]))
    assert [record["text"] for record in ui_order] == ["A", "B"]
    await runtime.close()


@pytest.mark.asyncio
async def test_status_retains_awaiting_registration_from_an_older_approved_snapshot(tmp_path):
    """Opening a newer empty preview cannot hide unfinished approved history."""
    runtime, _, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    for index in range(201):
        source.commit_unbound(UID, f"{index:032x}", ChatOriginal("user", "original"))
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    first = await runtime.preview_history(UID, "Neko")
    await runtime.approve_history(UID, first["taskId"])
    assert await runtime._advance_history(UID) == 200
    latest = await runtime.preview_history(UID, "Neko")
    assert latest["count"] == 0
    state = await runtime.status()
    progress = state["bindings"][0]["historyBackfill"]
    assert progress["taskId"] == latest["taskId"] and progress["status"] == "preview"
    assert progress["awaitingRegistration"] == 1
    await runtime.delete(UID, [f"{200:032x}"])
    assert (await runtime.status())["bindings"][0]["historyBackfill"]["awaitingRegistration"] == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_history_page_rejects_source_order_changed_after_fixed_preview(tmp_path):
    """Approved members pin raw source identity rather than trusting a later row."""
    runtime, _, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    source.commit_unbound(UID, "1" * 32, ChatOriginal("user", "original"))
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    preview = await runtime.preview_history(UID, "Neko")
    await runtime.approve_history(UID, preview["taskId"])
    with sqlite3.connect(source.path) as conn:
        conn.execute("UPDATE source_unbound SET origin_seq=origin_seq+1")
    with pytest.raises(ValueError, match="chat_source_integrity_error"):
        await runtime._advance_history(UID)
    assert runtime.ledger.pending(DOMAIN) == []
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "clear"])
async def test_binding_deletes_unapproved_unbound_original_on_both_devices(tmp_path, operation):
    """Body backfill approval does not postpone existing cross-device deletion."""
    runtime, service, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identity = "1" * 32
    original = ChatOriginal("user", "private local original")
    source.commit_unbound(UID, identity, original)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    receipt = await runtime.delete(UID, [identity]) if operation == "delete" else await runtime.clear(UID)
    assert receipt["messageIds"] == [identity] and receipt["pending"] is True
    assert len(receipt["deletionIds"]) == 1
    await runtime.flush_deletions(UID)
    assert any("/deletions/" in request.url.path for request in service.requests)
    assert service.deleted == [identity]
    source.commit(OWNER, UID, identity, original)
    source.commit_unbound(UID, identity, original)
    assert (await runtime.preview_history(UID, "Neko"))["count"] == 0
    assert runtime.ledger.pending(DOMAIN) == [] and source.pending(200) == []
    with sqlite3.connect(source.path) as connection:
        assert connection.execute("SELECT original,deleted FROM source_unbound WHERE record_id=?", (identity,)).fetchone() == (None, 1)
    await runtime.close()


@pytest.mark.asyncio
async def test_member_deleted_before_approval_keeps_cross_device_deletion_authority(tmp_path):
    """Deletion uses the bound domain before and after body backfill approval."""
    runtime, _, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identity = "1" * 32
    source.commit_unbound(UID, identity, ChatOriginal("user", "local only"))
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    preview = await runtime.preview_history(UID, "Neko")
    assert (await runtime.delete(UID, [identity]))["pending"] is True
    await runtime.approve_history(UID, preview["taskId"])
    assert (await runtime.delete(UID, [identity]))["pending"] is True
    assert runtime.ledger.pending_deletions(DOMAIN)
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "clear"])
@pytest.mark.parametrize("phase", ["before", "inflight"])
async def test_cross_device_delete_fences_late_pull_before_body_approval(tmp_path, operation, phase):
    """The existing ledger tombstone prevents late page resurrection before approval."""
    runtime, service, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identity = "1" * 32
    original = ChatOriginal("user", "original")
    source.commit_unbound(UID, identity, original)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    async def delete_locally():
        """Persist deletion without borrowing the independently running pull lane."""
        receipt = await runtime.delete(UID, [identity]) if operation == "delete" else await runtime.clear(UID)
        assert receipt["pending"] is True

    if phase == "before":
        await delete_locally()
    else:
        async def delete_during_request(request):
            """Finish local hiding while the old cloud page remains in flight."""
            if request.url.path.endswith("/changes"):
                await delete_locally()
        service.on_request = delete_during_request
    service.records = [encode_chat_original({"schemaVersion": 1, "ownerId": OWNER, "characterSyncId": UID,
                                            "channel": "chat", "scope": "private-chat", "messageId": identity,
                                            "originStreamId": "e" * 32, "originSeq": 1, **original.fields()}).decode()]
    if operation == "clear":
        # Remote tombstones remain compatible with the local cross-device fence.
        service.deleted = [identity]
    await runtime._channel_pass(UID, "pull", manual=True)
    assert await runtime.history(UID) == []
    assert runtime.ledger.cursor(DOMAIN) == 1
    assert (await runtime.delete(UID, [identity]))["pending"] is True
    await runtime.flush_deletions(UID)
    assert any("/deletions/" in request.url.path for request in service.requests)
    assert service.deleted == [identity]
    with sqlite3.connect(runtime.ledger.path) as connection:
        assert connection.execute("SELECT count(*) FROM chat_origin_identities WHERE domain=?", (DOMAIN.key(),)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM chat_tombstones WHERE domain=?", (DOMAIN.key(),)).fetchone()[0] == 1
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["approved_waiting", "authorized", "unknown", "downloaded"])
async def test_approved_and_existing_cloud_deletion_authority_is_preserved(tmp_path, state):
    """Body consent never limits the existing exact-ID deletion authority."""
    runtime, service, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identity = "1" * 32
    original = ChatOriginal("user", "original")
    if state != "unknown":
        source.commit_unbound(UID, identity, original)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    if state in ("approved_waiting", "authorized"):
        preview = await runtime.preview_history(UID, "Neko")
        await runtime.approve_history(UID, preview["taskId"])
        if state == "authorized":
            await runtime._advance_history(UID)
    elif state == "downloaded":
        raw = encode_chat_original({"schemaVersion": 1, "ownerId": OWNER, "characterSyncId": UID,
                                    "channel": "chat", "scope": "private-chat", "messageId": identity,
                                    "originStreamId": "e" * 32, "originSeq": 1, **original.fields()})
        runtime.ledger.apply_changes(DOMAIN, {"cursor": 1, "hasMore": False, "originals": [raw.decode()],
                                             "deletedMessageIds": []}, after=0)
    receipt = await runtime.delete(UID, [identity])
    assert receipt["pending"] is True and len(receipt["deletionIds"]) == 1
    await runtime.flush_deletions(UID)
    assert service.deleted == [identity]
    assert runtime.ledger.visible_originals(DOMAIN) == []
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "clear"])
async def test_unbound_deletions_resume_after_first_binding_without_body_approval(tmp_path, operation):
    """Deleted trusted IDs cross devices after binding in bounded marker pages."""
    runtime, service, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identities = [f"{index:032x}" for index in range(205)]
    for identity in identities:
        source.commit_unbound(UID, identity, ChatOriginal("user", "raw"))
    receipt = await runtime.delete(UID, identities) if operation == "delete" else await runtime.clear(UID)
    assert receipt["messageIds"] == identities
    assert service.requests == []
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    pending = runtime.ledger.pending_deletions(DOMAIN)
    assert len(pending) == 1 and pending[0].message_ids == tuple(identities[:200])
    assert source.pending(200) == [] and runtime.ledger.pending(DOMAIN) == []
    await runtime.flush_deletions(UID)
    assert service.deleted == identities
    requests = [request for request in service.requests if "/deletions/" in request.url.path]
    assert [len(json.loads(request.content)["messageIds"]) for request in requests] == [200, 5]
    await runtime.close()
    reopened, _, _ = setup_runtime(tmp_path, service)
    await reopened._recover_originals()
    assert reopened.ledger.pending_deletions(DOMAIN) == []
    assert (await reopened.preview_history(UID, "Neko"))["count"] == 0
    assert await reopened.history(UID) == []
    await reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ack_before_restart", [False, True])
async def test_unbound_deletion_source_gap_never_recreates_marker_after_ledger_commit(tmp_path, monkeypatch, ack_before_restart):
    """Source progress loss reuses a queued or acknowledged permanent deletion fence."""
    runtime, service, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identity = "1" * 32
    source.commit_unbound(UID, identity, ChatOriginal("user", "raw"))
    await runtime.delete(UID, [identity])
    await runtime.configure("https://sync.test")
    captured = ChatOriginalSource.deletions_captured

    def crash_after_ledger(self, uid, message_ids):
        """Represent process loss after marker/tombstone commit and before source ACK."""
        raise RuntimeError("synthetic source progress loss")

    monkeypatch.setattr(ChatOriginalSource, "deletions_captured", crash_after_ledger)
    with pytest.raises(RuntimeError, match="synthetic source progress loss"):
        await runtime.enable(UID, "Neko")
    queued = runtime.ledger.pending_deletions(DOMAIN)[0]
    if ack_before_restart:
        runtime.ledger.ack_deletion(DOMAIN, {"deletionId": queued.deletion_id, "messageIds": [identity], "cursor": 0})
    await runtime.close()
    monkeypatch.setattr(ChatOriginalSource, "deletions_captured", captured)
    reopened, _, _ = setup_runtime(tmp_path, service)
    await reopened._recover_originals()
    remaining = reopened.ledger.pending_deletions(DOMAIN)
    assert [item.deletion_id for item in remaining] == ([] if ack_before_restart else [queued.deletion_id])
    assert ChatOriginalSource(source.path).pending_deletions(UID, 200) == []
    assert await reopened.history(UID) == []
    await reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_domain", ["matching", "other_owner", "other_role"])
async def test_unbound_deleted_id_beyond_registration_page_cannot_revive_from_pull(tmp_path, remote_domain):
    """Late pages use real markers without borrowing IDs from a foreign domain."""
    runtime, service, _ = setup_runtime(tmp_path, HistoryService())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    identities = [f"{index:032x}" for index in range(1005)]
    original = ChatOriginal("user", "raw")
    for identity in identities:
        source.commit_unbound(UID, identity, original)
    await runtime.delete(UID, identities)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    service.records = [encode_chat_original({"schemaVersion": 1,
                                            "ownerId": OTHER_OWNER if remote_domain == "other_owner" else OWNER,
                                            "characterSyncId": OTHER_UID if remote_domain == "other_role" else UID,
                                            "channel": "chat", "scope": "private-chat", "messageId": identities[-1],
                                            "originStreamId": "e" * 32, "originSeq": 1, **original.fields()}).decode()]
    if remote_domain != "matching":
        with pytest.raises(ValueError, match="invalid_chat_changes"):
            await runtime._channel_pass(UID, "pull", manual=True)
        assert runtime.ledger.cursor(DOMAIN) == 0
        assert not any(identities[-1] in deletion.message_ids for deletion in runtime.ledger.pending_deletions(DOMAIN))
        with sqlite3.connect(source.path) as connection:
            assert connection.execute("SELECT deletion_pending FROM source_unbound WHERE record_id=?",
                                      (identities[-1],)).fetchone() == (1,)
        await runtime.close()
        return
    await runtime._channel_pass(UID, "pull", manual=True)
    assert runtime.ledger.visible_originals(DOMAIN) == []
    assert runtime.ledger.cursor(DOMAIN) == 1
    assert any(identities[-1] in deletion.message_ids for deletion in runtime.ledger.pending_deletions(DOMAIN))
    with sqlite3.connect(source.path) as connection:
        assert connection.execute("SELECT original,deletion_pending FROM source_unbound WHERE record_id=?",
                                  (identities[-1],)).fetchone() == (None, 0)
    await runtime.flush_deletions(UID)
    assert identities[-1] in service.deleted
    assert not any("/batches/" in request.url.path for request in service.requests)
    await runtime.close()


def test_default_is_disabled_and_state_survives_restart(tmp_path):
    """The built-in origin does not grant consent to any existing character."""
    state = DesktopSyncState(tmp_path / "chat_sync" / "state.sqlite3")
    assert state.settings()["service_url"] == "https://sync.nekotech.cn"
    assert state.bindings() == []
    device = state.settings()["device_id"]
    assert len(device) == 32
    assert DesktopSyncState(state.path).settings()["device_id"] == device


@pytest.mark.asyncio
async def test_production_factory_reports_builtin_service_for_legacy_setup(tmp_path, monkeypatch):
    """The actual desktop factory cannot revive a persisted local test endpoint."""
    import services.chat_sync.runtime as module

    config = SimpleNamespace(app_docs_dir=tmp_path / "selected", local_state_dir=tmp_path / "anchor")
    monkeypatch.setattr(module, "_runtimes", {})
    monkeypatch.setattr(module, "_runtime_lock", asyncio.Lock())
    runtime = await module.get_desktop_chat_sync_runtime(config)
    try:
        runtime.state.configure("https://localhost:8443")
        status = await runtime.status()
        assert status["serviceUrl"] == "https://sync.nekotech.cn"
        assert status["writerRecoveryRequired"] is True
        assert runtime.state.settings()["service_url"] == "https://localhost:8443"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_builtin_service_blocks_legacy_origin_without_retargeting_saved_tasks(tmp_path):
    """A saved test origin requires recovery before any old permission reaches the built-in service."""
    from services.chat_sync.writer import WriterQuarantined

    runtime, service, _ = setup_runtime(tmp_path, fixed_service_url="https://sync.nekotech.cn")
    try:
        runtime.state.configure("https://localhost:8443")
        prepared = runtime.state.prepare_binding(DOMAIN)
        binding = runtime.state.activate(prepared, BINDING)
        runtime.ledger.queue_deletion(DOMAIN, ["d" * 32])
        pending = runtime.ledger.pending_deletions(DOMAIN)

        status = await runtime.status()
        assert status["serviceUrl"] == "https://sync.nekotech.cn"
        assert status["writerRecoveryRequired"] is True
        assert status["bindings"][0]["enabled"] == 0
        with pytest.raises(WriterQuarantined):
            await runtime._identity("synthetic-a")
        assert service.requests == []
        assert runtime.state.settings()["service_url"] == "https://localhost:8443"
        assert runtime.state.binding(UID) == binding
        assert runtime.ledger.pending_deletions(DOMAIN) == pending
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_builtin_service_changes_legacy_origin_only_inside_explicit_recovery(tmp_path):
    """Existing recovery stages old deletes and revokes old bindings before using the built-in origin."""
    from unittest.mock import AsyncMock

    runtime, service, _ = setup_runtime(tmp_path, fixed_service_url="https://sync.nekotech.cn")
    try:
        runtime.state.configure("https://localhost:8443")
        prepared = runtime.state.prepare_binding(DOMAIN)
        runtime.state.activate(prepared, BINDING)
        runtime.ledger.queue_deletion(DOMAIN, ["d" * 32])
        runtime.enable = AsyncMock(side_effect=ValueError("synthetic_stop_after_recovery"))

        with pytest.raises(ValueError, match="synthetic_stop_after_recovery"):
            await runtime.recover_writer_role(UID, "Neko", service_url="https://sync.nekotech.cn")
        runtime.writer.check()
        assert runtime.state.settings()["service_url"] == "https://sync.nekotech.cn"
        binding = runtime.state.binding(UID)
        assert binding["enabled"] == 0 and binding["binding_id"] is None
        assert runtime.ledger.pending_deletions(DOMAIN) == []
        with sqlite3.connect(runtime.state.path.parent / "sources.sqlite3") as conn:
            assert conn.execute("SELECT message_id FROM source_recovery_delete_ids").fetchall() == [("d" * 32,)]
        runtime.enable.assert_awaited_once_with(UID, "Neko")
        assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_builtin_service_requires_no_setup_and_rejects_custom_configuration(tmp_path):
    """Production origin policy applies to actual HTTP even without a saved configuration."""
    runtime, service, _ = setup_runtime(tmp_path, fixed_service_url="https://sync.nekotech.cn")
    try:
        assert await runtime._identity("synthetic-a") == OWNER
        assert str(service.requests[0].url) == "https://sync.nekotech.cn/v1/chat-sync/identity"
        with pytest.raises(ValueError, match="invalid_chat_sync_service_url"):
            await runtime.configure("https://localhost:8443")
        assert runtime.state.settings()["service_url"] == "https://sync.nekotech.cn"
    finally:
        await runtime.close()


@pytest.mark.parametrize("url", [
    "http://sync.test", "https://user:secret@sync.test", "https://sync.test/path",
    "https://sync.test?query=1", "https://sync.test#fragment", " https://sync.test",
    "https://sync.test\n", "https://sync.test:invalid", None,
])
def test_invalid_service_origins_do_not_change_state(tmp_path, url):
    """Explicit HTTPS setup rejects ambiguous token destinations before I/O."""
    state = DesktopSyncState(tmp_path / "state.sqlite3")
    with pytest.raises(ValueError, match="invalid_chat_sync_service_url"):
        state.configure(url)
    assert state.settings()["service_url"] == "https://sync.nekotech.cn"


def test_binding_uses_server_owner_and_pins_origin(tmp_path):
    """The wrong local_user_id hint cannot bind or redirect a role's originals."""
    async def scenario():
        """Exercise actual httpx headers and canonical binding envelopes."""
        runtime, service, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test/")
        binding = await runtime.enable(UID, "Neko")
        assert binding["owner_id"] == OWNER
        assert binding["binding_id"] == BINDING
        assert binding["enabled"] == 1
        sent = service.requests[-1]
        assert sent.headers["Authorization"] == "Bearer synthetic-a"
        body = json.loads(sent.content)
        assert set(body) == {"deviceId", "requestId"}
        assert sent.content == json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        with pytest.raises(ValueError, match="chat_sync_service_already_bound"):
            await runtime.configure("https://other.test")
        assert (await runtime.status())["serviceUrl"] == "https://sync.test"
        await runtime.close()

    asyncio.run(scenario())


def test_response_loss_reuses_persisted_binding_request(tmp_path):
    """Retry after an accepted but lost reply keeps the exact device/request IDs."""
    async def scenario():
        """Restart with a new HTTP client but the same dedicated state files."""
        runtime, service, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")

        async def lose_first_response(request):
            """Raise after the first binding request was observable at the service."""
            if request.url.path.endswith("/bindings"):
                service.on_request = None
                raise httpx.ReadError("synthetic response loss")

        service.on_request = lose_first_response
        with pytest.raises(ValueError, match="chat_sync_unavailable"):
            await runtime.enable(UID, "Neko")
        first_body = service.requests[-1].content
        assert runtime.state.binding(UID)["enabled"] == 0
        await runtime.close()
        runtime, _, _ = setup_runtime(tmp_path, service)
        await runtime.enable(UID, "Neko")
        assert service.requests[-1].content == first_body
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change_token", [False, True])
def test_changed_service_owner_cannot_rebind_or_send_existing_role(tmp_path, change_token):
    """A replacement owner cannot adopt old work, even at the same origin/token."""
    async def scenario():
        """Verify rejection before any foreign registration or original request."""
        runtime, service, session = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        capture(runtime)
        deleted = capture(runtime, 2)
        await runtime.delete(UID, [deleted["messageId"]])
        batch = runtime.ledger.seal_batch(DOMAIN, byte_budget=10000)
        pending = runtime.ledger.pending(DOMAIN)
        deletions = runtime.ledger.pending_deletions(DOMAIN)
        assert batch is not None
        assert len(pending) == len(deletions) == 1
        before = runtime.state.binding(UID)
        service.requests.clear()
        service.owner = OTHER_OWNER
        if change_token:
            session["snapshot"]["access_token"] = "synthetic-b"
        for operation in [
            runtime.enable(UID, "Neko"), runtime.sync(UID, byte_budget=10000, max_pull_pages=1),
        ]:
            with pytest.raises(ValueError, match="chat_sync_account_mismatch"):
                await operation
        # The rejected automatic task is now durably fenced. A repeated marker
        # button cannot turn that rejection into another foreign-owner request.
        assert await runtime.flush_deletions(UID) == 0
        assert runtime.state.binding(UID) == before
        assert runtime.ledger.pending(DOMAIN) == pending
        assert runtime.ledger.pending_deletions(DOMAIN) == deletions
        assert runtime.ledger.seal_batch(DOMAIN, byte_budget=10000) == batch
        assert runtime.ledger.cursor(DOMAIN) == 0
        # Identity checks are the only permitted traffic; no old body or marker changes owner.
        assert [request.url.path for request in service.requests] == ["/v1/chat-sync/identity"] * 2
        foreign_domain = ChatDomain(OTHER_OWNER, UID)
        assert runtime.ledger.pending(foreign_domain) == []
        assert runtime.ledger.pending_deletions(foreign_domain) == []
        assert runtime.ledger.visible_originals(foreign_domain) == []
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["logout", "switch", "pause"])
def test_changed_session_or_binding_cannot_ack_inflight_upload(tmp_path, change):
    """An accepted upload response cannot clear pending work after ownership changes."""
    async def scenario():
        """Mutate the boundary during the real mock-transport request."""
        runtime, service, session = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        capture(runtime)

        async def change_during_upload(request):
            """Simulate user actions independent of the sync step's async lock."""
            if "/batches/" in request.url.path:
                if change == "logout":
                    session["logged_in"] = False
                elif change == "switch":
                    session["snapshot"]["access_token"] = "synthetic-b"
                else:
                    await runtime.pause(UID)

        service.on_request = change_during_upload
        with pytest.raises(ValueError, match="chat_sync_(login_required|session_changed|binding_changed)"):
            await runtime.sync(UID, byte_budget=10000, max_pull_pages=1)
        assert len(runtime.ledger.pending(DOMAIN)) == 1
        assert runtime.ledger.seal_batch(DOMAIN, byte_budget=10000) is not None
        assert runtime.ledger.cursor(DOMAIN) == 0
        await runtime.close()

    asyncio.run(scenario())


def test_manual_sync_download_and_offline_delete_have_no_model_dependency(tmp_path):
    """Bound upload, download and deletion use the ledger without recapturing pages."""
    async def scenario():
        """Run the observable request sequence without initializing NEKO services."""
        runtime, service, session = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        record = capture(runtime)
        result = await runtime.sync(UID, byte_budget=10000, max_pull_pages=1)
        assert result["uploaded"] == 1
        assert runtime.ledger.pending(DOMAIN) == []
        assert await runtime.history(UID) == [record]
        assert await runtime.history(OTHER_UID) == []
        session["logged_in"] = False
        deletion = await runtime.delete(UID, [record["messageId"]])
        assert deletion["pending"] is True
        assert await runtime.history(UID) == []
        assert len(runtime.ledger.pending_deletions(DOMAIN)) == 1
        # Local consent pause retains both the fixed deletion and original owner.
        await runtime.pause(UID)
        assert runtime.state.binding(UID)["owner_id"] == OWNER
        with pytest.raises(ValueError, match="chat_sync_not_enabled"):
            await runtime.sync(UID, byte_budget=10000, max_pull_pages=1)
        session["logged_in"] = True
        await runtime.enable(UID, "Neko")
        service.requests.clear()
        await runtime.sync(UID, byte_budget=10000, max_pull_pages=1)
        operations = [request.url.path for request in service.requests]
        assert "/deletions/" in operations[1]
        assert operations[-1].endswith("/changes")
        assert runtime.ledger.pending_deletions(DOMAIN) == []
        assert await runtime.history(UID) == []
        await runtime.close()

    asyncio.run(scenario())


def test_capture_retains_bound_owner_while_logged_out_and_paused(tmp_path):
    """Consent pauses networking while real saved originals keep their prior owner."""
    async def scenario():
        """Capture through the runtime API without contacting OAuth or the service."""
        runtime, service, session = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        await runtime.pause(UID)
        session["logged_in"] = False
        before = len(service.requests)
        original = ChatOriginal("user", " exact text ", "1791540000123")
        locator = SourceLocator("new-source", "generation", "d" * 32)
        first = await runtime.capture_original(UID, locator, original, message_id="d" * 32)
        assert first["ownerId"] == OWNER
        assert first["messageId"] == "d" * 32
        assert await runtime.capture_original(UID, locator, original, message_id="d" * 32) == first
        assert len(runtime.ledger.pending(DOMAIN)) == 1
        assert len(service.requests) == before
        assert await runtime.capture_original(OTHER_UID, locator, original) is None
        await runtime.close()

    asyncio.run(scenario())


def test_deletion_flush_sends_no_original_body_and_reports_remaining_work(tmp_path):
    """The independent marker channel does not seal or upload unrelated pending chat."""
    async def scenario():
        """Inspect actual HTTP operations and durable ledger counts after the flush."""
        runtime, service, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        capture(runtime)
        await runtime.delete(UID, ["f" * 32])
        before = (await runtime.status())["bindings"][0]
        assert before["pendingOriginals"] == before["pendingDeletions"] == 1
        service.requests.clear()
        assert await runtime.flush_deletions(UID) == 1
        assert [request.method for request in service.requests] == ["GET", "POST"]
        assert "/deletions/" in service.requests[-1].url.path
        assert json.loads(service.requests[-1].content) == {"messageIds": ["f" * 32]}
        after = (await runtime.status())["bindings"][0]
        assert after["pendingOriginals"] == 1
        assert after["pendingDeletions"] == 0
        assert after["hasSealedBatch"] is False
        await runtime.close()

    asyncio.run(scenario())


def test_background_delete_returns_local_state_before_slow_network_finishes(tmp_path):
    """A pending deletion is hidden durably while its detached transport is waiting."""
    async def scenario():
        """Use explicit events instead of timers to verify the local/network boundary."""
        runtime, service, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        record = capture(runtime)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delay_deletion(request):
            """Pause only the marker request, after the local tombstone already exists."""
            if "/deletions/" in request.url.path:
                entered.set()
                await release.wait()

        service.on_request = delay_deletion
        await runtime.delete(UID, [record["messageId"]])
        runtime.start_deletion_flush(UID)
        await asyncio.wait_for(entered.wait(), 2)
        assert await runtime.history(UID) == []
        assert len(runtime.ledger.pending_deletions(DOMAIN)) == 1
        release.set()
        await asyncio.gather(*runtime._background)
        assert runtime.ledger.pending_deletions(DOMAIN) == []
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["ack_batch", "ack_deletion", "apply_changes"])
@pytest.mark.parametrize("change", ["pause", "logout", "token", "close"])
def test_pause_fences_a_response_waiting_for_its_commit_worker(tmp_path, monkeypatch, operation, change):
    """Consent/session changes completed before the gate prevent every late mutation."""
    async def scenario():
        """Stop dispatch after the async guard, then finish pause before the worker."""
        runtime, service, session = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        record = capture(runtime)
        if operation == "ack_deletion":
            await runtime.delete(UID, [record["messageId"]])
        elif operation == "apply_changes":
            batch = runtime.ledger.seal_batch(DOMAIN, byte_budget=10000)
            runtime.ledger.ack_batch(DOMAIN, {"batchId": batch.batch_id, "messageIds": [record["messageId"]], "cursor": 0})
            service.records = [encode_chat_original(record).decode("utf-8")]
        pending_before = runtime.ledger.pending(DOMAIN)
        deletions_before = runtime.ledger.pending_deletions(DOMAIN)
        entered, release = asyncio.Event(), asyncio.Event()
        original_to_thread = asyncio.to_thread

        async def dispatch(function, *args, **kwargs):
            """Delay only the selected local mutation without holding its short gate."""
            target = args[1] if getattr(function, "__name__", "") == "_commit_bound" else function
            if getattr(target, "__name__", "") == operation:
                entered.set()
                await release.wait()
            return await original_to_thread(function, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", dispatch)
        task = asyncio.create_task(runtime.sync(UID, byte_budget=10000, max_pull_pages=1))
        await asyncio.wait_for(entered.wait(), 2)
        if change == "pause":
            await runtime.pause(UID)
        elif change == "logout":
            session["logged_in"] = False
        elif change == "close":
            await runtime.close()
        else:
            session["snapshot"]["access_token"] = "replacement-synthetic-token"
        release.set()
        with pytest.raises(ValueError, match="chat_sync_(binding_changed|login_required|session_changed|closing)"):
            await task
        assert runtime.ledger.pending(DOMAIN) == pending_before
        assert runtime.ledger.pending_deletions(DOMAIN) == deletions_before
        assert runtime.ledger.cursor(DOMAIN) == 0
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["logout", "token"])
def test_enable_fences_a_binding_response_waiting_for_activation(tmp_path, monkeypatch, change):
    """A late successful remote grant cannot enable consent after local logout."""
    async def scenario():
        """Change only synthetic credentials after the async check and before activation."""
        runtime, _, session = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        entered, release = asyncio.Event(), asyncio.Event()
        original_to_thread = asyncio.to_thread

        async def dispatch(function, *args, **kwargs):
            """Hold activation outside the local and credential commit gates."""
            if getattr(function, "__name__", "") == "_activate":
                entered.set()
                await release.wait()
            return await original_to_thread(function, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", dispatch)
        task = asyncio.create_task(runtime.enable(UID, "Neko"))
        await asyncio.wait_for(entered.wait(), 2)
        if change == "logout":
            session["logged_in"] = False
        else:
            session["snapshot"]["access_token"] = "replacement-synthetic-token"
        release.set()
        with pytest.raises(ValueError, match="chat_sync_(login_required|session_changed)"):
            await task
        assert runtime.state.binding(UID)["enabled"] == 0
        assert runtime.state.binding(UID)["binding_id"] is None
        await runtime.close()

    asyncio.run(scenario())


def test_clear_covers_401_pending_sources_with_fixed_chunk_retry_after_restart(tmp_path):
    """One clear includes pending WAL IDs and retries <=200-ID markers unchanged."""
    async def scenario():
        """Persist a large source boundary, lose a reply and resume from actual SQLite."""
        runtime, service, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
        identities = [f"{index:032x}" for index in range(1, 402)]
        for identity in identities:
            source.commit(OWNER, UID, identity, ChatOriginal("user", "private original " + identity))
        assert len(await runtime.history(UID)) == 200
        runtime.ledger.seal_batch(DOMAIN, byte_budget=1024 * 1024)
        receipt = await runtime.clear(UID)
        assert set(receipt["messageIds"]) == set(identities)
        fixed = runtime.ledger.pending_deletions(DOMAIN)
        assert [len(item.message_ids) for item in fixed] == [200, 200, 1]
        assert await runtime.history(UID) == []
        assert runtime.ledger.pending(DOMAIN) == []
        assert runtime.ledger.seal_batch(DOMAIN, byte_budget=10000) is None
        # Both raw stores have reclaimed current readable text, retaining IDs.
        with sqlite3.connect(source.path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM source_originals WHERE original IS NOT NULL").fetchone()[0] == 0
        with sqlite3.connect(runtime.ledger.path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM chat_originals").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM chat_deleted_originals").fetchone()[0] == 401
        attempts = 0

        async def lose_second_accepted_response(request):
            """The service accepted the fixed marker before the connection disappeared."""
            nonlocal attempts
            if "/deletions/" in request.url.path:
                attempts += 1
                if attempts == 2:
                    service.deleted.extend(json.loads(request.content)["messageIds"])
                    raise httpx.ReadError("synthetic response loss")

        service.on_request = lose_second_accepted_response
        with pytest.raises(ValueError, match="chat_sync_unavailable"):
            await runtime.flush_deletions(UID)
        remaining = runtime.ledger.pending_deletions(DOMAIN)
        assert remaining == fixed[1:]
        failed_request = service.requests[-1]
        await runtime.close()
        runtime, _, _ = setup_runtime(tmp_path, service)
        service.on_request = None
        service.requests.clear()
        assert await runtime.flush_deletions(UID) == 0
        assert service.requests == []
        runtime.test_clock[0] += 5000
        assert await runtime.flush_deletions(UID) == 2
        retried = service.requests[1]
        assert retried.url.path == failed_request.url.path
        assert retried.content == failed_request.content
        assert set(service.deleted) == set(identities)
        assert runtime.ledger.pending_deletions(DOMAIN) == []
        assert await runtime.history(UID) == []
        await runtime.close()

    asyncio.run(scenario())


def test_large_deletion_snapshot_rolls_back_every_marker_and_tombstone(tmp_path):
    """Failure during the second chunk must not leave a partially cleared boundary."""
    runtime, _, _ = setup_runtime(tmp_path)
    record = capture(runtime)
    identities = [record["messageId"]] + [f"{index:032x}" for index in range(1, 401)]
    with sqlite3.connect(runtime.ledger.path) as connection:
        connection.execute("""CREATE TRIGGER reject_second_deletion BEFORE INSERT ON chat_deletions
            WHEN (SELECT COUNT(*) FROM chat_deletions)=1 BEGIN SELECT RAISE(ABORT,'synthetic failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
        runtime.ledger.queue_deletions(DOMAIN, identities)
    assert runtime.ledger.deleted_message_ids(DOMAIN) == []
    assert runtime.ledger.pending_deletions(DOMAIN) == []
    assert len(runtime.ledger.visible_originals(DOMAIN)) == 1
    asyncio.run(runtime.close())


def test_source_delete_crash_gap_recovers_without_reintroducing_body(tmp_path):
    """Recovery purges a committed tombstone before replaying its old source WAL."""
    async def scenario():
        """Simulate interruption after marker commit but before source/ledger cleanup."""
        runtime, _, _ = setup_runtime(tmp_path)
        runtime.state.prepare_binding(DOMAIN)
        source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
        identity = "d" * 32
        source.commit(OWNER, UID, identity, ChatOriginal("user", "erase this raw text"))
        original = (await runtime.history(UID))[0]
        runtime.ledger.seal_batch(DOMAIN, byte_budget=10000)
        runtime.ledger.queue_deletion(DOMAIN, [identity])
        await runtime.close()
        runtime, _, _ = setup_runtime(tmp_path)
        assert await runtime.history(UID) == []
        assert runtime.ledger.pending(DOMAIN) == []
        with sqlite3.connect(runtime.ledger.path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM chat_originals").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM chat_batches").fetchone()[0] == 0
        runtime.ledger.apply_changes(DOMAIN, {
            "cursor": 1, "hasMore": False, "originals": [encode_chat_original(original).decode()], "deletedMessageIds": [],
        }, after=0)
        assert await runtime.history(UID) == []
        changed = {**original, "text": "changed old original"}
        with pytest.raises(ValueError, match="original_content_conflict"):
            runtime.ledger.apply_changes(DOMAIN, {
                "cursor": 2, "hasMore": False, "originals": [encode_chat_original(changed).decode()], "deletedMessageIds": [],
            }, after=1)
        assert runtime.ledger.cursor(DOMAIN) == 1
        await runtime.close()

    asyncio.run(scenario())


def test_deleted_source_alias_cannot_allocate_a_new_message_identity(tmp_path):
    """Readable raw removal retains the source alias/hash and origin sequence mapping."""
    async def scenario():
        """Try a producer retry and changed data after deleting a genuine fixed alias."""
        runtime, _, _ = setup_runtime(tmp_path)
        runtime.state.prepare_binding(DOMAIN)
        original = ChatOriginal("user", "erase this raw text")
        source = SourceLocator("verified", "generation", "stable-record")
        record = await runtime.capture_original(UID, source, original)
        await runtime.delete(UID, [record["messageId"]])
        with pytest.raises(ValueError, match="chat_original_deleted"):
            await runtime.capture_original(UID, source, original)
        with pytest.raises(ValueError, match="source_content_conflict"):
            await runtime.capture_original(UID, source, ChatOriginal("user", "different original"))
        assert await runtime.history(UID) == []
        await runtime.close()

    asyncio.run(scenario())


def test_remote_tombstone_reclaims_downloaded_body_on_local_history_recovery(tmp_path):
    """Remote deletion cleanup removes current raw bytes without an upload echo."""
    async def scenario():
        """Download one canonical original, then apply a separate deletion page."""
        runtime, _, _ = setup_runtime(tmp_path)
        runtime.state.prepare_binding(DOMAIN)
        record = capture(runtime)
        raw = encode_chat_original(record).decode()
        # A separate runtime has no local source alias for this downloaded ID.
        other, _, _ = setup_runtime(tmp_path / "download-device")
        other.state.prepare_binding(DOMAIN)
        other.ledger.apply_changes(DOMAIN, {"cursor": 1, "hasMore": False, "originals": [raw], "deletedMessageIds": []}, after=0)
        other.ledger.apply_changes(DOMAIN, {"cursor": 2, "hasMore": False, "originals": [], "deletedMessageIds": [record["messageId"]]}, after=1)
        assert await other.history(UID) == []
        with sqlite3.connect(other.ledger.path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM chat_downloaded").fetchone()[0] == 0
        assert other.ledger.stats(DOMAIN)["pendingOriginals"] == 0
        await other.close()
        await runtime.close()

    asyncio.run(scenario())


def test_manual_sync_and_detached_flush_share_only_the_deletion_flight(tmp_path):
    """Concurrent operations cannot acknowledge the same marker twice locally."""
    async def scenario():
        """Block the marker request and start a manual pass against the same queue."""
        runtime, service, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        await runtime.delete(UID, ["f" * 32])
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_marker(request):
            """Keep HTTP pending while the other operation reaches the marker lock."""
            if "/deletions/" in request.url.path:
                entered.set()
                await release.wait()

        service.on_request = slow_marker
        flushing = asyncio.create_task(runtime.flush_deletions(UID))
        await asyncio.wait_for(entered.wait(), 2)
        syncing = asyncio.create_task(runtime.sync(UID, byte_budget=10000, max_pull_pages=1))
        release.set()
        await asyncio.gather(flushing, syncing)
        assert sum("/deletions/" in request.url.path for request in service.requests) == 1
        assert runtime.ledger.pending_deletions(DOMAIN) == []
        await runtime.close()

    asyncio.run(scenario())


def test_saved_session_commit_guard_shares_the_actual_logout_file_lock(tmp_path, monkeypatch):
    """Use real credential locks and logout code only against disposable synthetic files."""
    from concurrent.futures import ThreadPoolExecutor, wait
    import threading
    from main_routers import card_drop_router as credentials

    social, auth = tmp_path / "social.json", tmp_path / "auth.json"
    social.write_text(json.dumps({"token": "synthetic-a", "baseUrl": "https://community.project-neko.cn"}))
    auth.write_text(json.dumps({"access_token": "synthetic-a"}))
    monkeypatch.setattr(credentials, "_social_session_paths", lambda: [social])
    monkeypatch.setattr(credentials, "_auth_path", lambda: auth)
    monkeypatch.setattr(credentials, "_clear_native_delegates", lambda: None)
    monkeypatch.setattr(credentials, "_native_sync_tickets", {})
    fingerprint = hashlib.sha256(json.dumps({
        "token": "synthetic-a", "origin": "https://community.project-neko.cn",
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    entered = threading.Event()

    def logout():
        """Attempt the production credential cleanup while a response commit is gated."""
        entered.set()
        return credentials._clear_auth()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with _saved_session_commit_guard(fingerprint):
            # The guard holds the very lock file the logout writer takes.
            assert (tmp_path / "social.json.lock").exists()
            clearing = pool.submit(logout)
            assert entered.wait(2)
            # Unfenced, this cleanup of two small files finishes well within the
            # window; fenced, it waits for the guard (the lock timeout is 2 s).
            assert not wait([clearing], timeout=0.3).done
            assert not clearing.done()
        assert clearing.result(timeout=2) is True
    with pytest.raises(ValueError, match="chat_sync_login_required"):
        with _saved_session_commit_guard(fingerprint):
            pytest.fail("A completed logout allowed an old response commit")


@pytest.mark.parametrize("body", [b"x" * (4 * 1024 * 1024 + 1), b"not-json", b"[" * 2000 + b"]" * 2000],
                         ids=["oversized", "malformed", "deeply-nested"])
def test_http_response_is_bounded_safely_parsed_and_always_closed(tmp_path, body):
    """Oversized, malformed and deeply nested service bodies never reach ledger state."""
    async def scenario():
        """Read a real streamed httpx response with an intentionally false length header."""
        runtime, _, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")

        class Stream(httpx.AsyncByteStream):
            """Expose close state for every interrupted parser/read path."""

            closed = False

            async def __aiter__(self):
                """Supply synthetic bytes only; no socket or credentials are involved."""
                yield body

            async def aclose(self):
                """Record closure after successful context exit or early failure."""
                self.closed = True

        stream = Stream()
        await runtime.http.aclose()
        runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Length": "1"}, stream=stream),
        ))
        with pytest.raises(ValueError, match="invalid_chat_sync_response"):
            await runtime._request("GET", "/v1/chat-sync/identity", "synthetic-a")
        assert stream.closed
        assert runtime.ledger.cursor(DOMAIN) == 0
        assert runtime.state.bindings() == []
        await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["source-only", "partially-captured", "capture-marker-gap", "deleted"])
def test_status_counts_pending_source_and_ledger_identity_union(tmp_path, stage):
    """Raw WAL backlog cannot disappear from status or be double-counted during recovery."""
    async def scenario():
        """Create only disposable sources, including an unrelated owner's record."""
        runtime, _, _ = setup_runtime(tmp_path)
        runtime.state.prepare_binding(DOMAIN)
        source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
        identities = [f"{index:032x}" for index in range(1, 202)]
        for identity in identities:
            source.commit(OWNER, UID, identity, ChatOriginal("user", "pending original " + identity))
        source.commit(OTHER_OWNER, UID, "f" * 32, ChatOriginal("user", "unrelated owner"))
        if stage in {"partially-captured", "capture-marker-gap"}:
            assert len(await runtime.history(UID)) == 200
        if stage == "capture-marker-gap":
            # Capture committed, but a crash prevented clearing the same WAL marker.
            with sqlite3.connect(source.path) as connection:
                connection.execute("UPDATE source_originals SET pending=1 WHERE record_id=?", (identities[0],))
        if stage == "deleted":
            runtime.ledger.queue_deletion(DOMAIN, [identities[0]])
        binding = (await runtime.status())["bindings"][0]
        assert binding["pendingOriginals"] == (200 if stage == "deleted" else 201)
        await runtime.close()

    asyncio.run(scenario())


def test_manual_pass_receipt_counts_backlog_recovered_in_its_finally(tmp_path):
    """A bounded pass cannot report no more work while final recovery queues new originals."""
    async def scenario():
        """Drain 401 proven originals while advancing the real admission clock."""
        runtime, _, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
        for index in range(1, 402):
            source.commit(OWNER, UID, f"{index:032x}", ChatOriginal("user", "pending original"))
        for uploaded, remaining in [(200, 201), (200, 1), (1, 0)]:
            receipt = await runtime.sync(UID, byte_budget=1024 * 1024, max_pull_pages=1)
            assert receipt["uploaded"] == uploaded
            assert receipt["pendingOriginals"] == remaining
            assert receipt["pendingDeletions"] == 0
            assert receipt["hasMore"] is bool(remaining)
            assert (await runtime.status())["bindings"][0]["pendingOriginals"] == remaining
            runtime.test_clock[0] += 120_000
        await runtime.close()

    asyncio.run(scenario())


def test_redirect_does_not_forward_bearer_even_with_redirecting_injected_client(tmp_path):
    """Server redirects never turn a configured origin into a token forwarding proxy."""
    async def scenario():
        """Enable redirect following on the injected client to verify per-call fencing."""
        runtime, service, _ = setup_runtime(tmp_path)
        runtime.http.follow_redirects = True
        await runtime.configure("https://sync.test")

        async def redirect(request):
            """Record the only request and respond with a foreign token destination."""
            service.requests.append(request)
            return httpx.Response(307, headers={"Location": "https://foreign.test/identity"})

        await runtime.http.aclose()
        runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True)
        with pytest.raises(ValueError, match="chat_sync_service_rejected"):
            await runtime.enable(UID, "Neko")
        assert len(service.requests) == 1
        assert service.requests[0].url.host == "sync.test"
        await runtime.close()

    asyncio.run(scenario())


@pytest.fixture
def local_tls_files(tmp_path):
    """Issue a disposable localhost leaf from a test CA using an existing dependency."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.now(timezone.utc)

    def issue_ca(name):
        """Create independent synthetic roots, never a system or account credential."""
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        certificate = (
            x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=None, decipher_only=None,
            ), critical=True).sign(key, hashes.SHA256())
        )
        path = tmp_path / (name + ".pem")
        path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        return key, certificate, path

    ca_key, ca, ca_path = issue_ca("synthetic-local-ca")
    _, _, other_ca_path = issue_ca("synthetic-unrelated-ca")
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    certificate_path, key_path = tmp_path / "localhost.pem", tmp_path / "localhost.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    return ca_path, other_ca_path, certificate_path, key_path


@asynccontextmanager
async def local_tls_service(files):
    """Serve one bounded synthetic identity over real TLS on an ephemeral loopback port."""
    requests = []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(files[2]), str(files[3]))

    async def respond(reader, writer):
        """Record only synthetic HTTP requests and close the disposable connection."""
        try:
            requests.append(await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2))
            body = json.dumps({"ownerId": OWNER}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                         + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0, ssl=context, ssl_shutdown_timeout=1)
    try:
        yield server.sockets[0].getsockname()[1], requests
    finally:
        server.close()
        await server.wait_closed()


async def unused_session():
    """TLS adapter tests never resolve stored OAuth or user data."""
    raise AssertionError("Unexpected account session lookup")


def test_local_ca_adds_trust_without_disabling_chain_or_hostname_checks(local_tls_files):
    """The added root leaves default trust and required certificate validation intact."""
    context = _load_local_ca_context(str(local_tls_files[0]))
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.cert_store_stats()["x509_ca"] >= ssl.create_default_context().cert_store_stats()["x509_ca"] + 1


def test_local_ca_verified_https_loads_in_worker_and_ignores_proxy(tmp_path, local_tls_files, monkeypatch):
    """Only the opted-in sync transport trusts the test CA, with real TLS and no proxy."""
    monkeypatch.setenv("NEKO_CHAT_SYNC_LOCAL_CA_FILE", str(local_tls_files[0]))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    owner_thread = threading.get_ident()
    loads = []

    def load(path):
        """Prove file parsing runs outside the event loop, once per runtime."""
        assert threading.get_ident() != owner_thread
        loads.append(path)
        return _load_local_ca_context(path)

    monkeypatch.setattr("services.chat_sync.runtime._load_local_ca_context", load)

    async def scenario():
        """Use synthetic bearer bytes only against the disposable localhost listener."""
        async with local_tls_service(local_tls_files) as (port, requests):
            runtime = DesktopChatSyncRuntime(tmp_path / "sync", unused_session)
            try:
                await runtime.configure(f"https://localhost:{port}")
                assert await runtime._identity("synthetic-local-token") == OWNER
                assert await runtime._identity("synthetic-local-token") == OWNER
                assert len(requests) == 2
                assert b"Authorization: Bearer synthetic-local-token\r\n" in requests[0]
                assert loads == [str(local_tls_files[0])]
            finally:
                await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["wrong-hostname", "wrong-ca", "not-enabled"])
def test_local_https_still_rejects_invalid_server_identity(tmp_path, local_tls_files, monkeypatch, failure):
    """No flag, an unrelated root or an unmatched hostname prevents HTTP transmission."""
    monkeypatch.delenv("NEKO_CHAT_SYNC_LOCAL_CA_FILE", raising=False)
    if failure != "not-enabled":
        ca_path = local_tls_files[1] if failure == "wrong-ca" else local_tls_files[0]
        monkeypatch.setenv("NEKO_CHAT_SYNC_LOCAL_CA_FILE", str(ca_path))

    async def scenario():
        """Handshake failures never send the synthetic bearer or accept an owner."""
        async with local_tls_service(local_tls_files) as (port, requests):
            runtime = DesktopChatSyncRuntime(tmp_path / "sync", unused_session)
            try:
                host = "127.0.0.1" if failure == "wrong-hostname" else "localhost"
                await runtime.configure(f"https://{host}:{port}")
                with pytest.raises(ValueError, match="^chat_sync_unavailable$"):
                    await runtime._identity("synthetic-local-token")
                assert requests == []
                assert runtime.state.bindings() == []
            finally:
                await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("origin", ["https://sync.test", "https://localhost.evil.test", "https://192.168.1.1"])
def test_local_ca_refuses_remote_configuration_and_previously_saved_origin(tmp_path, monkeypatch, origin):
    """Both initial setup and request dispatch fence a remote origin before CA or HTTP I/O."""
    monkeypatch.setenv("NEKO_CHAT_SYNC_LOCAL_CA_FILE", str(tmp_path / "must-not-load.pem"))

    def forbidden_load(path):
        """A remote target must be rejected before trying to parse any CA file."""
        pytest.fail("Remote origin reached local CA loading")

    monkeypatch.setattr("services.chat_sync.runtime._load_local_ca_context", forbidden_load)

    async def scenario():
        """Represent an older saved remote endpoint without touching actual state."""
        runtime = DesktopChatSyncRuntime(tmp_path / "sync", unused_session)
        try:
            with pytest.raises(ValueError, match="^chat_sync_local_ca_loopback_required$"):
                await runtime.configure(origin)
            assert runtime.state.settings()["service_url"] == "https://sync.nekotech.cn"
            runtime.state.configure(origin)
            with pytest.raises(ValueError, match="^chat_sync_local_ca_loopback_required$"):
                await runtime._identity("synthetic-local-token")
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("contents", [None, "invalid private local CA content"])
def test_missing_or_invalid_local_ca_has_fixed_safe_error(tmp_path, monkeypatch, contents):
    """Bad test configuration exposes neither the local file path nor parser details."""
    path = tmp_path / "private-test-ca-path.pem"
    if contents is not None:
        path.write_text(contents)
    monkeypatch.setenv("NEKO_CHAT_SYNC_LOCAL_CA_FILE", str(path))

    async def scenario():
        """A configuration failure occurs before sending any HTTP request."""
        runtime = DesktopChatSyncRuntime(tmp_path / "sync", unused_session)
        try:
            await runtime.configure("https://localhost:8443")
            with pytest.raises(ValueError, match="^chat_sync_local_ca_invalid$") as error:
                await runtime._identity("synthetic-local-token")
            assert str(path) not in str(error.value)
            assert runtime.state.bindings() == []
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_injected_synthetic_client_ignores_machine_local_ca_flag(tmp_path, monkeypatch):
    """Test transports stay isolated even if the process has a broken developer CA path."""
    monkeypatch.setenv("NEKO_CHAT_SYNC_LOCAL_CA_FILE", str(tmp_path / "missing.pem"))

    async def scenario():
        """A synthetic service can keep its explicit test origin without local CA I/O."""
        runtime, _, _ = setup_runtime(tmp_path)
        try:
            await runtime.configure("https://sync.test")
            assert (await runtime.enable(UID, "Neko"))["owner_id"] == OWNER
        finally:
            await runtime.close()

    asyncio.run(scenario())


async def finish_automatic_flights(runtime):
    """Observe only finite channel sweeps, never the persistent scheduler loop."""
    await asyncio.gather(*list(runtime._flights.values()))


def test_automatic_login_pause_idle_upload_and_periodic_download(tmp_path):
    """Login wakes opted-in work; short ticks perform no cloud work while stopped."""
    async def scenario():
        runtime, service, session = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.scheduler_tick()
        assert service.requests == []
        await runtime.enable(UID, "Neko")
        runtime.ledger.capture(DOMAIN, SourceLocator("auto", "generation", "1"), ChatOriginal("user", "new"),
                               queued_at_ms=runtime.test_clock[0])
        service.requests.clear()
        session["logged_in"] = False
        await runtime.scheduler_tick()
        assert service.requests == []
        session["logged_in"] = True
        await runtime.scheduler_tick()
        await finish_automatic_flights(runtime)
        assert sum(request.url.path.endswith("/changes") for request in service.requests) == 1
        assert not any("/batches/" in request.url.path for request in service.requests)
        runtime.test_clock[0] += 1_000
        await runtime.scheduler_tick()
        await finish_automatic_flights(runtime)
        assert sum("/batches/" in request.url.path for request in service.requests) == 1
        assert runtime.ledger.pending(DOMAIN) == []
        runtime.test_clock[0] += 119_000
        await runtime.scheduler_tick()
        await finish_automatic_flights(runtime)
        assert sum(request.url.path.endswith("/changes") for request in service.requests) == 2
        await runtime.pause(UID)
        before = len(service.requests)
        runtime.test_clock[0] += 120_000
        await runtime.scheduler_tick()
        assert len(service.requests) == before
        await runtime.close()
    asyncio.run(scenario())


def test_manual_body_gate_is_shared_across_characters(tmp_path):
    """The shared body lock replaces minute cooldown without delaying other roles."""
    async def scenario():
        runtime, service, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        other = ChatDomain(OWNER, OTHER_UID)
        prepared = runtime.state.prepare_binding(other)
        runtime.state.activate(prepared, BINDING)
        capture(runtime)
        runtime.ledger.capture(other, SourceLocator("other", "generation", "1"), ChatOriginal("user", "other"))
        assert (await runtime.sync(UID, byte_budget=10000, max_pull_pages=1))["uploaded"] == 1
        service.requests.clear()
        assert (await runtime._channel_pass(OTHER_UID, "body", manual=True))["uploaded"] == 1
        await runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["pause_resume", "close"])
def test_claim_cannot_borrow_a_new_binding_or_send_after_close(tmp_path, monkeypatch, change):
    """The consent captured before admission survives the claim/transport race."""
    async def scenario():
        runtime, service, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        capture(runtime)
        entered, release = asyncio.Event(), asyncio.Event()
        original = runtime._bound_transport

        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(runtime, "_bound_transport", blocked)
        service.requests.clear()
        task = asyncio.create_task(runtime._channel_pass(UID, "body", manual=True))
        await asyncio.wait_for(entered.wait(), 2)
        if change == "pause_resume":
            await runtime.pause(UID)
            runtime.state.activate(runtime.state.binding(UID), BINDING)
        else:
            await runtime.close()
        release.set()
        with pytest.raises(ValueError, match="chat_sync_(binding_changed|closing)"):
            await task
        assert service.requests == []
        assert len(runtime.ledger.pending(DOMAIN)) == 1
        assert not runtime.ledger.stats(DOMAIN)["hasSealedBatch"]
        await runtime.close()
    asyncio.run(scenario())


def test_body_failure_does_not_stop_independent_periodic_download(tmp_path):
    """A body retry and a cloud download use separate deadlines and flights."""
    async def scenario():
        runtime, service, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        runtime.ledger.capture(DOMAIN, SourceLocator("auto", "generation", "1"), ChatOriginal("user", "waiting"),
                               queued_at_ms=runtime.test_clock[0] - 60_000)
        await runtime.http.aclose()

        async def reject_body(request):
            if "/batches/" in request.url.path:
                service.requests.append(request)
                return httpx.Response(503, json={"detail": "untrusted private text"})
            return await service.request(request)

        runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(reject_body))
        service.requests.clear()
        for _ in range(2):
            await runtime.scheduler_tick()
            await finish_automatic_flights(runtime)
            runtime.test_clock[0] += 120_000
        assert sum(request.url.path.endswith("/changes") for request in service.requests) == 2
        assert len(runtime.ledger.pending(DOMAIN)) == 1
        assert runtime.scheduler.last_error(DOMAIN.key()) == "chat_sync_service_rejected"
        await runtime.close()
    asyncio.run(scenario())


def test_retry_after_covers_control_markers_pull_and_fixed_batch_restart(tmp_path):
    """Server pressure cannot be bypassed by another lane, button or restart."""
    async def scenario():
        runtime, service, _ = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        first = capture(runtime)
        await runtime.http.aclose()

        async def throttled(request):
            if "/batches/" in request.url.path:
                service.requests.append(request)
                return httpx.Response(429, headers={"Retry-After": "240"}, json={"detail": "do not log"})
            return await service.request(request)

        runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(throttled))
        with pytest.raises(ValueError, match="chat_sync_service_rejected"):
            await runtime.sync(UID, byte_budget=10000, max_pull_pages=1)
        failed = next(request for request in service.requests if "/batches/" in request.url.path)
        await runtime.delete(UID, ["f" * 32])
        capture(runtime, 2)
        await runtime.close()
        runtime, _, _ = setup_runtime(tmp_path, service)
        service.requests.clear()
        with pytest.raises(ValueError, match="chat_sync_retry_later"):
            await runtime.enable(UID, "Neko")
        assert await runtime.flush_deletions(UID) == 0
        assert await runtime._channel_pass(UID, "pull", manual=True) is None
        assert service.requests == []
        runtime.test_clock[0] += 240_000
        assert await runtime.flush_deletions(UID) == 1
        receipt = await runtime._channel_pass(UID, "body", manual=True)
        assert receipt["uploaded"] == 1
        retried = next(request for request in service.requests if "/batches/" in request.url.path)
        assert retried.url.path == failed.url.path and retried.content == failed.content
        assert json.loads(service.records[0])["messageId"] == first["messageId"]
        assert len(runtime.ledger.pending(DOMAIN)) == 1
        await runtime.close()
    asyncio.run(scenario())


def test_sqlite_worker_failure_is_bounded_observed_and_private(tmp_path, monkeypatch, capsys):
    """Disk failures neither crash chat startup nor create an unobserved hot loop."""
    async def scenario():
        from services.chat_sync import runtime as module
        runtime, _, _ = setup_runtime(tmp_path)
        calls = 0

        def failed_arm():
            nonlocal calls
            calls += 1
            raise sqlite3.OperationalError("synthetic private path and SQL")

        monkeypatch.setattr(runtime.scheduler, "arm_wake", failed_arm)
        # Stay above the Windows event-loop clock resolution (~15.6 ms).
        monkeypatch.setattr(module, "_LOCAL_WAKE_SECONDS", 0.05)
        runtime.start_scheduler()
        await asyncio.sleep(0.1)
        assert 1 <= calls <= 5
        assert not runtime._scheduler_task.done()
        await runtime.close()
        assert runtime._scheduler_task.cancelled()
    asyncio.run(scenario())
    assert "synthetic private path" not in capsys.readouterr().out


@pytest.mark.parametrize("bootstrap", [False, True])
def test_shutdown_does_not_register_or_start_late_runtime_construction(tmp_path, monkeypatch, bootstrap):
    """Cancelled boot and late requests cannot register a runtime after shutdown."""
    async def scenario():
        from services.chat_sync import runtime as module
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        created = []
        original = module.DesktopChatSyncRuntime

        def blocked(*args, **kwargs):
            """Block only disposable construction, never application or real data."""
            entered.set()
            assert release.wait(2)
            runtime = original(*args, **kwargs)
            created.append(runtime)
            completed.set()
            return runtime

        monkeypatch.setattr(module, "DesktopChatSyncRuntime", blocked)
        manager = SimpleNamespace(app_docs_dir=tmp_path / "isolated", local_state_dir=tmp_path / "installation")
        if bootstrap:
            module.schedule_desktop_chat_sync_start(manager)
            request = None
        else:
            request = asyncio.create_task(module.get_desktop_chat_sync_runtime(manager))
        assert await asyncio.to_thread(entered.wait, 1)
        await module.close_desktop_chat_sync_runtimes()
        release.set()
        if request is not None:
            with pytest.raises(ValueError, match="chat_sync_closing"):
                await request
        assert await asyncio.to_thread(completed.wait, 1)
        assert len(created) == 1 and created[0].http is None and created[0]._scheduler_task is None
        assert not created[0]._background
        assert module._runtimes == {}
    asyncio.run(scenario())


def test_real_loop_exit_leaves_late_default_construction_without_http_or_worker(tmp_path):
    """Exercise genuine asyncio.run teardown without pytest's nested-loop patch."""
    script = r'''
import asyncio
from pathlib import Path
import sys
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import patch
from services.chat_sync import runtime as module

entered, release = threading.Event(), threading.Event()
created = []
connections = []
original = module.DesktopChatSyncRuntime
original_connect = sqlite3.connect

class ObservedConnection(sqlite3.Connection):
    def close(self):
        self.was_closed = True
        super().close()

def connect(*args, **kwargs):
    parent = kwargs.pop('factory', sqlite3.Connection)
    class TrackedConnection(parent):
        def close(self):
            self.was_closed = True
            super().close()
    connection = original_connect(*args, **kwargs, factory=TrackedConnection)
    connection.was_closed = False
    connections.append(connection)
    return connection

def blocked(*args, **kwargs):
    entered.set()
    assert release.wait(2)
    runtime = original(*args, **kwargs)
    created.append(runtime)
    return runtime

async def main():
    module.schedule_desktop_chat_sync_start(SimpleNamespace(app_docs_dir=Path(sys.argv[1]), local_state_dir=Path(sys.argv[1]).parent / "installation"))
    assert await asyncio.to_thread(entered.wait, 1)
    await module.close_desktop_chat_sync_runtimes()
    # Return while construction still owns its thread. asyncio.run joins its
    # default executor for real, without a manually awaited cleanup coroutine.
    threading.Timer(0.02, release.set).start()

with patch.object(module, 'DesktopChatSyncRuntime', blocked), patch.object(
    module.httpx, 'AsyncClient', side_effect=AssertionError('Unowned default HTTP client allocated')
), patch.object(sqlite3, 'connect', connect):
    asyncio.run(main())
assert len(created) == 1
assert created[0].http is None and created[0]._scheduler_task is None
assert not created[0]._background and module._runtimes == {}
assert connections and all(connection.was_closed for connection in connections)
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path / "isolated")],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@contextmanager
def _controlled_pull_deadline(monkeypatch):
    """Advance only this test's loop after the intended request is waiting."""
    import services.chat_sync.runtime as module

    loop = asyncio.get_running_loop()
    real_time = loop.time
    network_budget = 60
    offset = 0

    def advance(seconds=network_budget + 1):
        nonlocal offset
        offset += seconds

    # Real SQLite/witness preparation must not consume a tiny wall-clock budget.
    # Advancing the loop still exercises the production asyncio.timeout callback.
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(module, "_PULL_NETWORK_SECONDS", network_budget)
        clock_patch.setattr(loop, "time", lambda: real_time() + offset)
        yield advance


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["identity", "changes"])
async def test_pull_network_budget_releases_stalled_get(tmp_path, monkeypatch, endpoint):
    """A download deadline releases the lock without accepting an incomplete page."""
    runtime, service, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    original = runtime._request
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def stalled(method, path, token, **kwargs):
        if endpoint in path:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return await original(method, path, token, **kwargs)

    monkeypatch.setattr(runtime, "_request", stalled)
    try:
        with _controlled_pull_deadline(monkeypatch) as expire:
            task = asyncio.create_task(runtime._channel_pass(UID, "pull", manual=True))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                expire()
                await asyncio.wait_for(cancelled.wait(), 1)
                with pytest.raises(ValueError, match="^chat_sync_pull_timeout$"):
                    await asyncio.wait_for(task, 5)
                assert not runtime._pull_lock.locked()
                assert runtime.scheduler.last_error(DOMAIN.key()) == "chat_sync_pull_timeout"
                assert runtime.ledger.cursor(DOMAIN) == 0
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_pull_budget_is_shared_and_external_cancel_stays_cancelled(tmp_path, monkeypatch):
    """Identity consumes the page budget; caller cancellation is not a retry error."""
    import services.chat_sync.runtime as module
    runtime, _, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    original = runtime._request
    identity_entered, release_identity, changes_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    changes_cancelled = asyncio.Event()

    async def delayed(method, path, token, **kwargs):
        if path.endswith("/identity"):
            identity_entered.set()
            await release_identity.wait()
        elif "/changes" in path:
            changes_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                changes_cancelled.set()
        return await original(method, path, token, **kwargs)

    monkeypatch.setattr(runtime, "_request", delayed)
    try:
        with _controlled_pull_deadline(monkeypatch) as advance:
            task = asyncio.create_task(runtime._channel_pass(UID, "pull", manual=True))
            try:
                await asyncio.wait_for(identity_entered.wait(), 5)
                half_budget = module._PULL_NETWORK_SECONDS / 2
                advance(half_budget)
                release_identity.set()
                await asyncio.wait_for(changes_entered.wait(), 5)
                # Only the shared deadline has expired: resetting the budget for
                # changes would leave another half-budget and fail this assertion.
                advance(half_budget + 1)
                await asyncio.wait_for(changes_cancelled.wait(), 1)
                with pytest.raises(ValueError, match="^chat_sync_pull_timeout$"):
                    await asyncio.wait_for(task, 5)
                runtime.test_clock[0] += 3001
                identity_entered.clear()
                release_identity.clear()
                task = asyncio.create_task(runtime._channel_pass(UID, "pull", manual=True))
                await asyncio.wait_for(identity_entered.wait(), 5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not runtime._pull_lock.locked()
                # Direct identity calls still have no pull deadline.
                release_identity.set()
                assert await runtime._identity("synthetic-a") == OWNER
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        await runtime.close()


def chat_ledger_rows(runtime):
    """Compare complete stored values, including sealed bytes and identities."""
    with sqlite3.connect(runtime.ledger.path) as connection:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'chat_%'").fetchall()
        return {name: sorted(connection.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
                for (name,) in tables if not name.startswith("chat_sync_")}


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["identity", "changes"])
@pytest.mark.parametrize("stop", ["timeout", "cancel", "pause", "logout", "close"])
async def test_pull_stall_preserves_complete_outbox(tmp_path, monkeypatch, endpoint, stop):
    """No incomplete download can alter pending bytes, fixed IDs or tombstones."""
    runtime, service, session = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    for index in range(3):
        runtime.ledger.capture(DOMAIN, SourceLocator("budget", "generation", str(index)),
                               ChatOriginal("user", f"pending {index}\n🙂"))
    batch = runtime.ledger.seal_batch(DOMAIN, byte_budget=10000)
    deletion = runtime.ledger.queue_deletion(DOMAIN, ["d" * 32])
    before = chat_ledger_rows(runtime)
    writer_epoch = runtime.writer.epoch()
    hints = []
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def notify(uid):
        hints.append(uid)

    async def block(request):
        if endpoint in request.url.path:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    runtime.history_changed = notify
    service.on_request = block
    try:
        with _controlled_pull_deadline(monkeypatch) as expire:
            task = asyncio.create_task(runtime._channel_pass(UID, "pull", manual=True))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                if stop == "cancel":
                    task.cancel()
                elif stop == "pause":
                    await runtime.pause(UID)
                elif stop == "logout":
                    session["logged_in"] = False
                elif stop == "close":
                    runtime._track(task)
                    await runtime.close()
                if stop not in {"cancel", "close"}:
                    expire()
                # Observe network cancellation independently of durable local
                # cleanup, which can take longer on Windows or a loaded disk.
                await asyncio.wait_for(cancelled.wait(), 1)
                expected = asyncio.CancelledError if stop in {"cancel", "close"} else ValueError
                with pytest.raises(expected):
                    await asyncio.wait_for(task, 5)
                assert chat_ledger_rows(runtime) == before
                runtime.writer.require_epoch(writer_epoch)
                assert runtime.ledger.seal_batch(DOMAIN, byte_budget=10000) == batch
                assert runtime.ledger.pending_deletions(DOMAIN) == [deletion]
                assert len(runtime.ledger.pending(DOMAIN)) == 3
                assert runtime.ledger.cursor(DOMAIN) == 0 and hints == []
                assert not runtime._pull_lock.locked()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["body", "deletion", "profile"])
@pytest.mark.parametrize("phase", ["identity", "write"])
async def test_pull_timeout_does_not_cancel_shared_client_writes(tmp_path, monkeypatch, channel, phase):
    """Only download I/O expires while an exact approved write stays in flight."""
    import services.chat_sync.runtime as module
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Synthetic")
    batch = deletion = operation = None
    if channel == "body":
        capture(runtime)
        batch = runtime.ledger.seal_batch(DOMAIN, byte_budget=10000)
    elif channel == "deletion":
        deletion = runtime.ledger.queue_deletion(DOMAIN, ["d" * 32])
    else:
        local = await runtime.profiles.local(UID)
        await runtime.profiles.approve(UID, local["local_hash"], 0)
        operation = runtime.profiles.store.next_operation(DOMAIN.key())
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def block(request):
        if request.url.path.endswith("/changes"):
            await asyncio.Event().wait()
        is_write = request.method in {"PUT", "POST"}
        if asyncio.current_task().get_name() == "approved-write" and (
            (phase == "identity" and request.url.path.endswith("/identity")) or (phase == "write" and is_write)
        ):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    service.on_request = block
    monkeypatch.setattr(module, "_PULL_NETWORK_SECONDS", .1)
    writing = runtime.profiles.step(UID, manual=True) if channel == "profile" else runtime._channel_pass(UID, channel, manual=True)
    task = asyncio.create_task(writing, name="approved-write")
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(ValueError, match="^chat_sync_pull_timeout$"):
            await runtime._channel_pass(UID, "pull", manual=True)
        assert not task.done() and not cancelled.is_set()
        assert not runtime.http.is_closed
        if batch:
            assert runtime.ledger.seal_batch(DOMAIN, byte_budget=10000) == batch
        if deletion:
            assert runtime.ledger.pending_deletions(DOMAIN) == [deletion]
        if operation:
            assert runtime.profiles.store.next_operation(DOMAIN.key()) == operation
        release.set()
        await asyncio.wait_for(task, 2)
        if batch:
            sent = next(request for request in service.requests if "/batches/" in request.url.path)
            assert json.loads(sent.content) == {"originals": [payload.decode() for _, payload, _ in batch.members]}
            assert sent.url.path.endswith(batch.batch_id)
            assert runtime.ledger.pending(DOMAIN) == []
        if deletion:
            sent = next(request for request in service.requests if "/deletions/" in request.url.path)
            assert sent.content == deletion.payload and sent.url.path.endswith(deletion.deletion_id)
            assert runtime.ledger.pending_deletions(DOMAIN) == []
        if operation:
            sent = next(request for request in service.profile_requests if request.method == "PUT")
            assert sent.content == bytes(operation["body"])
            assert runtime.profiles.store.next_operation(DOMAIN.key()) is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_more", [False, True])
async def test_pull_deadline_never_cancels_started_page_commit(tmp_path, monkeypatch, has_more):
    """An accepted worker commit may outlive network budget and still notify once."""
    import services.chat_sync.runtime as module
    class PagedService(SyntheticService):
        async def request(self, request):
            if request.url.path.endswith("/changes"):
                self.requests.append(request)
                return httpx.Response(200, json={"cursor": 1, "hasMore": has_more,
                                                "originals": [], "deletedMessageIds": []})
            return await super().request(request)

    runtime, service, _ = setup_runtime(tmp_path, PagedService())
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    entered, release = threading.Event(), threading.Event()
    original = runtime._commit_bound
    hints = []
    loop = asyncio.get_running_loop()
    real_time = loop.time
    network_budget = 60

    def slow_commit(binding, operation, *args, **kwargs):
        if operation == runtime.ledger.apply_changes:
            entered.set()
            assert release.wait(5)
        return original(binding, operation, *args, **kwargs)

    async def notify(uid):
        hints.append(uid)

    runtime.history_changed = notify
    monkeypatch.setattr(runtime, "_commit_bound", slow_commit)
    # Setup and worker admission must finish before this test expires the budget.
    # Windows SQLite/witness checks need not fit an artificial 100 ms deadline.
    monkeypatch.setattr(module, "_PULL_NETWORK_SECONDS", network_budget)
    task = asyncio.create_task(runtime._channel_pass(UID, "pull", manual=True))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        # Expire the actual loop clock only after the commit owns its worker.
        # Two turns also run any incorrectly retained outer timeout callback.
        monkeypatch.setattr(loop, "time", lambda: real_time() + network_budget + 1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done() and runtime.ledger.cursor(DOMAIN) == 0
        release.set()
        if has_more:
            with pytest.raises(ValueError, match="^chat_sync_pull_timeout$"):
                await asyncio.wait_for(task, 1)
            assert runtime.scheduler.last_error(DOMAIN.key()) == "chat_sync_pull_timeout"
        else:
            receipt = await asyncio.wait_for(task, 1)
            assert receipt["cursor"] == 1 and runtime.scheduler.last_error(DOMAIN.key()) is None
        await wait_notification(lambda: hints == [UID])
        assert runtime.ledger.cursor(DOMAIN) == 1
        assert sum(request.url.path.endswith("/changes") for request in service.requests) == 1
    finally:
        monkeypatch.setattr(loop, "time", real_time)
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["identity", "changes"])
async def test_pull_timeout_leaves_inactive_role_until_it_becomes_current(tmp_path, monkeypatch, endpoint):
    """A timed out current GET releases its flight without downloading inactive roles."""
    import services.chat_sync.runtime as module
    runtime, service, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    other = ChatDomain(OWNER, OTHER_UID)
    runtime.state.activate(runtime.state.prepare_binding(other), BINDING)
    selected = select_runtime_current(runtime)
    used = False
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def block_first(request):
        nonlocal used
        if not used and endpoint in request.url.path:
            used = True
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    service.on_request = block_first
    try:
        presence = await runtime.session_presence_provider()
        with _controlled_pull_deadline(monkeypatch) as expire:
            task = asyncio.create_task(runtime._automatic_channel("pull", presence))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                expire()
                await asyncio.wait_for(cancelled.wait(), 1)
                await asyncio.wait_for(task, 5)
                assert used
                assert runtime.ledger.cursor(DOMAIN) == 0
                assert runtime.ledger.cursor(other) == 0
                assert runtime.scheduler.last_error(DOMAIN.key()) == "chat_sync_pull_timeout"
                assert runtime.scheduler.last_error(other.key()) is None
                assert not runtime._pull_lock.locked()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        selected[0] = OTHER_UID
        monkeypatch.setattr(module, "_PULL_NETWORK_SECONDS", 5)
        await asyncio.wait_for(runtime._automatic_channel("pull", presence), 6)
        assert runtime.ledger.cursor(other) == 1
        assert runtime.ledger.cursor(DOMAIN) == 0
    finally:
        await runtime.close()


def select_runtime_current(runtime, uid=UID):
    """Expose a mutable current selection independently of durable consent."""
    selected = [uid]

    async def current_character_uid():
        return selected[0]

    runtime.current_character_uid_provider = current_character_uid
    return selected


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["body", "pull"])
@pytest.mark.parametrize("selected_uid", [UID, OTHER_UID, None, "missing-provider"])
async def test_current_role_automatic_channels_require_enabled_current_intersection(tmp_path, channel, selected_uid):
    """Consent alone never sends inactive chat; a current role still needs consent."""
    runtime, service, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        runtime.ledger.capture(DOMAIN, SourceLocator("current", "generation", "1"), ChatOriginal("user", "current"),
                               queued_at_ms=runtime.test_clock[0] - 1000)
        if selected_uid == OTHER_UID:
            runtime.state.prepare_binding(ChatDomain(OWNER, OTHER_UID))
        if selected_uid == "missing-provider":
            runtime.current_character_uid_provider = None
        else:
            select_runtime_current(runtime, selected_uid)
        service.requests.clear()
        presence = await runtime.session_presence_provider()
        await runtime._automatic_channel(channel, presence)
        outgoing = [request for request in service.requests if "/batches/" in request.url.path or request.url.path.endswith("/changes")]
        assert len(outgoing) == int(selected_uid == UID)
        if selected_uid != UID:
            assert service.requests == []
            assert len(runtime.ledger.pending(DOMAIN)) == 1
            assert runtime.ledger.cursor(DOMAIN) == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["body", "pull"])
async def test_current_role_automatic_channels_leave_other_enabled_role_queued(tmp_path, channel):
    """A second enabled role keeps its bytes and cursor until it becomes current."""
    runtime, service, _ = setup_runtime(tmp_path)
    other = ChatDomain(OWNER, OTHER_UID)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        runtime.state.activate(runtime.state.prepare_binding(other), BINDING)
        runtime.ledger.capture(DOMAIN, SourceLocator("current", "generation", "1"), ChatOriginal("user", "current"),
                               queued_at_ms=runtime.test_clock[0] - 1000)
        runtime.ledger.capture(other, SourceLocator("other", "generation", "1"), ChatOriginal("user", "inactive"),
                               queued_at_ms=runtime.test_clock[0] - 1000)
        before = runtime.ledger.pending(other)
        service.requests.clear()
        await runtime._automatic_channel(channel, await runtime.session_presence_provider())
        assert runtime.ledger.pending(other) == before
        assert runtime.ledger.cursor(other) == 0
        assert not any(OTHER_UID in request.url.path for request in service.requests)
        assert any(UID in request.url.path for request in service.requests)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_notification_subscription_replaces_only_selected_grant(tmp_path):
    """Switching roles closes the old subscription and never subscribes all consent."""
    runtime, service, _ = setup_runtime(tmp_path)
    sockets = []

    @asynccontextmanager
    async def connect(origin):
        socket = SyntheticNotifications()
        sockets.append(socket)
        async with socket.connect(origin):
            yield socket

    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        runtime.state.activate(runtime.state.prepare_binding(ChatDomain(OWNER, OTHER_UID)), BINDING)
        selected = select_runtime_current(runtime)
        runtime._notification_connect = connect
        presence = await runtime.session_presence_provider()
        service.requests.clear()
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: sockets and len(sockets[0].frames) == 2)
        assert sockets[0].frames[1]["characters"] == [{"characterSyncId": UID, "bindingId": BINDING}]
        selected[0] = OTHER_UID
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        await wait_notification(lambda: len(sockets) == 2 and len(sockets[1].frames) == 2)
        assert sockets[0].closed
        assert sockets[1].frames[1]["characters"] == [{"characterSyncId": OTHER_UID, "bindingId": BINDING}]
        selected[0] = None
        await runtime._manage_notifications(runtime.state.bindings(), presence)
        assert sockets[1].closed and runtime._notification_task is None
        assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_switch_after_identity_stops_old_changes_request(tmp_path):
    """An accepted identity response does not authorize a now inactive next GET."""
    runtime, service, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        selected = select_runtime_current(runtime)

        async def switch(request):
            if request.url.path.endswith("/identity"):
                selected[0] = OTHER_UID

        service.on_request = switch
        service.requests.clear()
        await runtime._automatic_channel("pull", await runtime.session_presence_provider())
        assert any(request.url.path.endswith("/identity") for request in service.requests)
        assert not any(request.url.path.endswith("/changes") for request in service.requests)
        assert runtime.ledger.cursor(DOMAIN) == 0
        assert runtime.scheduler.last_error(DOMAIN.key()) is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_switch_after_body_send_preserves_exact_ack(tmp_path):
    """A current selection change cannot reject a fixed already sent batch receipt."""
    runtime, service, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        runtime.ledger.capture(DOMAIN, SourceLocator("current", "generation", "1"), ChatOriginal("user", "current"),
                               queued_at_ms=runtime.test_clock[0] - 1000)
        batch = runtime.ledger.seal_batch(DOMAIN, byte_budget=10000)
        selected = select_runtime_current(runtime)

        async def switch(request):
            if "/batches/" in request.url.path:
                selected[0] = OTHER_UID

        service.on_request = switch
        service.requests.clear()
        await runtime._automatic_channel("body", await runtime.session_presence_provider())
        sent = [request for request in service.requests if "/batches/" in request.url.path]
        assert len(sent) == 1 and sent[0].url.path.endswith("/" + batch.batch_id)
        assert json.loads(sent[0].content)["originals"] == [raw.decode() for _, raw, _ in batch.members]
        assert runtime.ledger.pending(DOMAIN) == []
        assert not runtime.ledger.stats(DOMAIN)["hasSealedBatch"]
        assert runtime.scheduler.last_error(DOMAIN.key()) is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_switch_after_page_send_commits_page_without_next_get(tmp_path):
    """Already downloaded pages commit, while later pages wait for reuse of the role."""
    class PagedService(SyntheticService):
        async def request(self, request):
            if request.url.path.endswith("/changes"):
                self.requests.append(request)
                if self.on_request is not None:
                    await self.on_request(request)
                after = int(request.url.params["after"])
                return httpx.Response(200, json={"cursor": after + 1, "hasMore": after == 0,
                                                "originals": [], "deletedMessageIds": []})
            return await super().request(request)

    runtime, service, _ = setup_runtime(tmp_path, PagedService())
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        selected = select_runtime_current(runtime)

        async def switch(request):
            if request.url.path.endswith("/changes"):
                selected[0] = OTHER_UID

        service.on_request = switch
        service.requests.clear()
        await runtime._automatic_channel("pull", await runtime.session_presence_provider())
        assert runtime.ledger.cursor(DOMAIN) == 1
        assert sum(request.url.path.endswith("/changes") for request in service.requests) == 1
        assert runtime.scheduler.last_error(DOMAIN.key()) is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,lock_name", [("body", "_lock"), ("pull", "_pull_lock")])
async def test_current_role_switch_while_waiting_channel_lock_does_not_start_http(tmp_path, channel, lock_name):
    """Queued automatic work rechecks selection after acquiring its existing lock."""
    runtime, service, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        runtime.ledger.capture(DOMAIN, SourceLocator("current", "generation", "1"), ChatOriginal("user", "current"),
                               queued_at_ms=runtime.test_clock[0] - 1000)
        selected = select_runtime_current(runtime)
        lock = getattr(runtime, lock_name)
        await lock.acquire()
        service.requests.clear()
        task = asyncio.create_task(runtime._automatic_channel(channel, await runtime.session_presence_provider()))
        try:
            await asyncio.sleep(.01)
            assert not task.done()
            selected[0] = OTHER_UID
        finally:
            lock.release()
        await asyncio.wait_for(task, 2)
        assert service.requests == []
        assert len(runtime.ledger.pending(DOMAIN)) == 1
        assert runtime.ledger.cursor(DOMAIN) == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_inactive_manual_sync_and_deletion_remain_authorized(tmp_path):
    """Explicit sync and previously approved deletion survive current-role filtering."""
    runtime, service, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        record = capture(runtime)
        select_runtime_current(runtime, OTHER_UID)
        service.requests.clear()
        assert (await runtime.sync(UID, byte_budget=10000, max_pull_pages=1))["uploaded"] == 1
        assert runtime.ledger.pending(DOMAIN) == []
        await runtime.delete(UID, [record["messageId"]])
        service.requests.clear()
        await runtime._automatic_channel("deletion", await runtime.session_presence_provider())
        assert service.deleted == [record["messageId"]]
        assert runtime.ledger.pending_deletions(DOMAIN) == []
        assert any("/deletions/" in request.url.path for request in service.requests)
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError("chat_sync_pull_timeout"), ChatSyncServiceError(429, 240000)])
async def test_current_role_catchup_and_switch_preserve_retry_and_origin_backpressure(tmp_path, failure):
    """Selection changes and foreground hints never clear persisted error fences."""
    runtime, service, _ = setup_runtime(tmp_path)
    other = ChatDomain(OWNER, OTHER_UID)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        runtime.state.activate(runtime.state.prepare_binding(other), BINDING)
        selected = select_runtime_current(runtime)
        presence = await runtime.session_presence_provider()
        runtime.scheduler.failure(DOMAIN.key(), "pull", 1, presence, failure)
        selected[0] = OTHER_UID
        await runtime.scheduler_tick()
        await finish_automatic_flights(runtime)
        service.requests.clear()
        await runtime.request_catchup()
        selected[0] = UID
        await runtime.request_catchup()
        await runtime.scheduler_tick()
        await finish_automatic_flights(runtime)
        assert service.requests == []
        assert not runtime.scheduler.ready(DOMAIN.key(), "pull", 1, presence)
        assert runtime.scheduler.last_error(DOMAIN.key()) in {"chat_sync_pull_timeout", "chat_sync_service_rejected"}
        if getattr(failure, "status_code", None) == 429:
            assert not runtime.scheduler.origin_ready()
            assert await runtime._channel_pass(OTHER_UID, "pull", manual=True) is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_missing_constructor_provider_cannot_resume_saved_automatic_consent(tmp_path):
    """A caller omitting local selection must not resume every persisted binding."""
    runtime, service, _ = setup_runtime(tmp_path)
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    await runtime.close()
    reopened = DesktopChatSyncRuntime(
        tmp_path / "chat_sync", runtime.session_provider,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(service.request)),
        session_commit_guard=runtime.session_commit_guard,
        clock=runtime.scheduler.clock, uniform=lambda *_: 0,
    )
    try:
        service.requests.clear()
        await reopened.scheduler_tick()
        await finish_automatic_flights(reopened)
        assert service.requests == []
        assert reopened.ledger.cursor(DOMAIN) == 0
        assert reopened.state.binding(UID)["enabled"]
    finally:
        await reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["resolver", "connect", "guard"])
async def test_current_role_initial_notification_wait_rechecks_before_outbound_frame(tmp_path, monkeypatch, stage):
    """A selection change during setup cannot authenticate or subscribe a stale role."""
    runtime, _, _ = setup_runtime(tmp_path)
    socket = SyntheticNotifications()
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        selected = select_runtime_current(runtime)
        entered, release = asyncio.Event(), asyncio.Event()
        if stage == "resolver":
            original_session = runtime._session

            async def resolve():
                result = await original_session()
                entered.set()
                await release.wait()
                return result

            monkeypatch.setattr(runtime, "_session", resolve)
        elif stage == "guard":
            original_guard = runtime._guard

            async def guard(*args, **kwargs):
                await original_guard(*args, **kwargs)
                entered.set()
                await release.wait()

            monkeypatch.setattr(runtime, "_guard", guard)

        @asynccontextmanager
        async def connect(origin):
            async with socket.connect(origin):
                if stage == "connect":
                    entered.set()
                    await release.wait()
                yield socket

        runtime._notification_connect = connect
        await runtime._manage_notifications(runtime.state.bindings(), await runtime.session_presence_provider())
        await asyncio.wait_for(entered.wait(), 2)
        selected[0] = OTHER_UID
        release.set()
        await wait_notification(lambda: runtime._notification_task.done() or len(socket.frames) >= 2)
        assert not any(frame["type"] == "subscribe" for frame in socket.frames)
        if stage == "resolver":
            assert socket.connections == 0
        elif stage == "connect":
            assert socket.connections == 1 and socket.frames == []
        else:
            assert [frame["type"] for frame in socket.frames] == ["authenticate"]
        assert runtime._notification_task.done()
        assert runtime._notification_failures == 0 and runtime._notification_blocked_key is None
        assert runtime.scheduler.last_error(DOMAIN.key()) is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_idle_recovery_does_not_advance_durable_writer_heads(tmp_path):
    from services.chat_sync.source import ChatOriginalSource
    runtime, service, _ = setup_runtime(tmp_path)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Synthetic")
        ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
        await runtime.delete(UID, ["f" * 32])
        before = runtime.writer.path.read_bytes()
        for _ in range(3):
            await runtime._recover_originals()
        assert runtime.writer.path.read_bytes() == before
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bound_role', [False, True])
async def test_explicit_pause_revokes_retained_on_without_a_setup_error(tmp_path, bound_role):
    """A real pause cancels saved On but retains local originals and deletion work."""
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    try:
        if bound_role:
            await runtime.enable(UID, 'Synthetic')
            capture(runtime)
            deletion = runtime.ledger.queue_deletion(DOMAIN, ['e' * 32])
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        saved = runtime.state.new_role(UID)
        await runtime.pause(UID)
        assert runtime.state.choices()[UID] is False
        assert runtime.state.new_role(UID) is None
        service.requests.clear()
        await runtime._run_new_role_sync(UID, saved, await runtime.session_presence_provider())
        assert runtime.state.new_role_errors() == {} and service.requests == []
        if bound_role:
            assert runtime.ledger.pending(DOMAIN)
            assert runtime.ledger.pending_deletions(DOMAIN) == [deletion]
            assert not runtime.state.binding(UID)['enabled']
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("include_history", [False, True])
async def test_memory_browser_archive_is_the_shared_history_source(tmp_path, include_history):
    """Pre-enable chats survive review and sync once only with history consent."""
    from memory.chat_history import read_chat_history
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_chat_history import archive
    from tests.unit.test_character_profile_sync import Config, ProfileService

    path = tmp_path / "time_indexed.db"
    archive(path, [{"type": "human", "data": {"content": "same"}}] * 2 + [
        {"type": "ai", "data": {"content": "tool", "metadata": {"chat_original_source": {
            "recordId": "3" * 32, "originalTime": "1234567890123", "origin": "tool"}}}}])
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    source.commit_local_reply(UID, "4" * 32, ChatOriginal("assistant", "earlier tool record", original_time="1234567890122"), "tool")
    async def history(uid):
        return read_chat_history(path, uid)
    runtime.chat_history_provider = history
    try:
        local = await runtime.history(UID)
        ids = {row["messageId"] for row in local}
        assert len(local) == len(ids) == 4
        assert len(await runtime.local_replies(UID)) == 0
        assert service.requests == [] and runtime.state.bindings() == []
        (tmp_path / "recent.json").write_text('[]')
        assert await runtime.history(UID) == local
        await runtime.save_character_sync_choice(UID, "Synthetic", True, include_history=include_history)
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        result = await runtime.sync(UID, byte_budget=10000, max_pull_pages=1)
        assert result["uploaded"] == (4 if include_history else 0)
        assert {json.loads(raw)["messageId"] for raw in service.records} == (ids if include_history else set())
        assert len(await runtime.history(UID)) == 4
        assert (await runtime.preview_history(UID, "Synthetic"))["count"] == (0 if include_history else 4)
        await runtime.clear(UID)
        assert await runtime.history(UID) == []
        assert await runtime.local_replies(UID) == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError, OSError, sqlite3.DatabaseError])
async def test_unavailable_archive_keeps_existing_history_and_sync_choice_usable(tmp_path, failure):
    from unittest.mock import AsyncMock
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService

    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    source.commit_unbound(UID, "f" * 32, ChatOriginal("user", "existing local chat"))
    runtime.chat_history_provider = AsyncMock(side_effect=failure("private archive detail"))
    try:
        assert [row["text"] for row in await runtime.history(UID)] == ["existing local chat"]
        await runtime.save_character_sync_choice(UID, "Synthetic", True, include_history=True)
        assert runtime.state.new_role(UID) is not None
        assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["save", "request"])
async def test_bound_history_choice_requires_a_complete_archive_before_freezing_range(tmp_path, entry):
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService

    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    source.commit_unbound(UID, "1" * 32, ChatOriginal("user", "already indexed"))
    available = False
    async def history(uid):
        if not available:
            raise sqlite3.OperationalError("private locked archive")
        return [{"messageId": "2" * 32, "role": "assistant", "text": "restored older chat", "origin": "legacy",
                 "originalTime": None, "timezone": None, "conversationId": None, "replyToMessageId": None}]
    try:
        await runtime.enable(UID, "Synthetic")
        await runtime.save_character_sync_choice(UID, "Synthetic", True)
        previous = runtime.state.new_role(UID)
        runtime.chat_history_provider = history
        assert [row["text"] for row in await runtime.history(UID)] == ["already indexed"]
        with pytest.raises(ValueError, match="chat_sync_unavailable"):
            if entry == "save":
                await runtime.save_character_sync_choice(UID, "Synthetic", True)
            else:
                await runtime.request_new_role_sync(UID, "Synthetic", replace=True)
        assert runtime.state.new_role(UID) == previous
        available = True
        await runtime.save_character_sync_choice(UID, "Synthetic", True)
        assert runtime.state.new_role(UID)["throughSeq"] > previous["throughSeq"]
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        await runtime.sync(UID, byte_budget=10000, max_pull_pages=1)
        assert {json.loads(raw)["text"] for raw in service.records} == {"already indexed", "restored older chat"}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_unbound_history_grant_retries_unavailable_archive_without_finishing_early(tmp_path):
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService

    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    available = False
    async def history(uid):
        if not available:
            raise OSError("private archive failure")
        return [{"messageId": "2" * 32, "role": "user", "text": "older archive chat", "origin": "legacy",
                 "originalTime": None, "timezone": None, "conversationId": None, "replyToMessageId": None}]
    runtime.chat_history_provider = history
    try:
        await runtime.request_new_role_sync(UID, "Synthetic")
        saved = runtime.state.new_role(UID)
        presence = await runtime.session_presence_provider()
        await runtime._run_new_role_sync(UID, saved, presence)
        pending = runtime.state.new_role(UID)
        assert pending and pending["requestId"] == saved["requestId"]
        assert pending["lastError"] == "chat_sync_unavailable"
        assert not service.records
        available = True
        await runtime._run_new_role_sync(UID, pending, presence)
        await runtime.sync(UID, byte_budget=10000, max_pull_pages=1)
        assert runtime.state.new_role(UID) is None
        assert [json.loads(raw)["text"] for raw in service.records] == ["older archive chat"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_bad_archive_original_is_isolated_and_user_tool_provenance_cannot_block_neighbors(tmp_path):
    from unittest.mock import AsyncMock
    runtime, _, _ = setup_runtime(tmp_path)
    base = {"role": "user", "text": "kept user", "origin": "tool", "originalTime": None,
            "timezone": None, "conversationId": None, "replyToMessageId": None}
    runtime.chat_history_provider = AsyncMock(return_value=[
        {**base, "messageId": "1" * 32},
        {**base, "messageId": "2" * 32, "originalTime": 123},
        {**base, "messageId": "3" * 32, "role": "assistant", "origin": "tool", "text": "kept tool"}])
    try:
        assert {row["text"] for row in await runtime.history(UID)} == {"kept user", "kept tool"}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_excluding_old_history_cannot_use_an_unavailable_archive_cutoff(tmp_path):
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService
    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    available = False
    async def history(uid):
        if not available:
            raise OSError("private archive failure")
        return [{"messageId": "2" * 32, "role": "user", "text": "excluded older chat", "origin": "legacy",
                 "originalTime": None, "timezone": None, "conversationId": None, "replyToMessageId": None}]
    runtime.chat_history_provider = history
    try:
        with pytest.raises(ValueError, match="chat_sync_unavailable"):
            await runtime.request_new_role_sync(UID, "Synthetic", include_history=False)
        assert runtime.state.new_role(UID) is None and runtime.state.choices() == {}
        available = True
        await runtime.request_new_role_sync(UID, "Synthetic", include_history=False)
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        await runtime.sync(UID, byte_budget=10000, max_pull_pages=1)
        assert service.records == []
        assert [row["text"] for row in await runtime.history(UID)] == ["excluded older chat"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_bound_choice_without_history_can_save_while_archive_is_unavailable(tmp_path, enabled):
    from unittest.mock import AsyncMock
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService
    runtime, _, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    try:
        await runtime.enable(UID, "Synthetic")
        runtime.chat_history_provider = AsyncMock(side_effect=OSError())
        await runtime.save_character_sync_choice(UID, "Synthetic", enabled, include_history=False)
        assert runtime.state.choices()[UID] is enabled
        assert runtime.state.new_role(UID) is None
        if not enabled:
            runtime.chat_history_provider.assert_not_awaited()
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["provider", "batch"])
async def test_archive_tolerance_does_not_swallow_writer_quarantine(tmp_path, monkeypatch, stage):
    from unittest.mock import AsyncMock
    from services.chat_sync.writer import WriterQuarantined
    runtime, _, _ = setup_runtime(tmp_path)
    runtime.chat_history_provider = AsyncMock(return_value=[{
        "messageId": "2" * 32, "role": "user", "text": "archive text", "origin": "legacy",
        "originalTime": None, "timezone": None, "conversationId": None, "replyToMessageId": None}])
    if stage == "provider":
        runtime.chat_history_provider.side_effect = WriterQuarantined()
    else:
        def quarantined(*_args):
            raise WriterQuarantined()
        monkeypatch.setattr(ChatOriginalSource, "commit_history", quarantined)
    try:
        with pytest.raises(WriterQuarantined):
            await runtime._refresh_chat_history(UID)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_conflicting_archive_batch_keeps_existing_roles_and_reports_incomplete_refresh(tmp_path, caplog):
    from unittest.mock import AsyncMock
    runtime, _, _ = setup_runtime(tmp_path)
    source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
    source.commit_unbound(UID, "1" * 32, ChatOriginal("user", "existing target chat"))
    source.commit_unbound(OTHER_UID, "2" * 32, ChatOriginal("user", "other role private chat"))
    base = {"role": "user", "origin": "legacy", "originalTime": None, "timezone": None,
            "conversationId": None, "replyToMessageId": None}
    runtime.chat_history_provider = AsyncMock(return_value=[
        {**base, "messageId": "3" * 32, "text": "rolled back archive chat"},
        {**base, "messageId": "2" * 32, "text": "private conflicting archive chat"}])
    try:
        assert await runtime._refresh_chat_history(UID) is False
        assert [row["text"] for row in await runtime.history(UID)] == ["existing target chat"]
        assert source.stored_ids(UID) == {"1" * 32}
        assert [row["text"] for row in source.local_history(OTHER_UID)] == ["other role private chat"]
        assert "archive" in caplog.text.lower() and "private" not in caplog.text
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["chat", "proactive", "tool", "agent"])
async def test_history_refresh_during_reply_tail_cannot_steal_live_upload(tmp_path, monkeypatch, origin):
    """Archive caching precedes the delayed original seal in a real completed turn."""
    from unittest.mock import AsyncMock
    from memory.chat_history import read_chat_history
    import services.chat_sync.source as source_module
    from tests.unit.test_chat_history import archive

    runtime, service, _ = setup_runtime(tmp_path)
    path = tmp_path / "time_indexed.db"
    proof = {"recordId": "5" * 32, "originalTime": "1234567890123", "origin": origin}
    archive(path, [{"type": "ai", "data": {"content": "reply", "metadata": {"chat_original_source": proof}}}])
    async def history(uid):
        return read_chat_history(path, uid)
    runtime.chat_history_provider = history
    monkeypatch.setattr("services.chat_sync.runtime.get_desktop_chat_sync_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(source_module, "character_uid_for", AsyncMock(return_value=UID))
    original_save, tool_save = source_module.save_original, source_module.save_local_reply
    async def save(*args, **kwargs):
        return await original_save(*args, config_manager=SimpleNamespace(), **kwargs)
    async def save_tool(*args, **kwargs):
        return await tool_save(*args, config_manager=SimpleNamespace(), **kwargs)
    monkeypatch.setattr(source_module, "save_original", save)
    monkeypatch.setattr(source_module, "save_local_reply", save_tool)
    buffer = source_module.AssistantOriginalBuffer(tail_grace=3600)
    try:
        await runtime.enable(UID, "Synthetic")
        buffer.append({"chat_original_source": proof, "text": "reply"})
        await buffer.finish("Synthetic", proof["recordId"])
        await runtime.history(UID)
        source = ChatOriginalSource(runtime.state.path.parent / "sources.sqlite3")
        assert proof["recordId"] not in source.stored_ids(UID)
        # A late tail must retain the full producer bytes, not the earlier archive projection.
        buffer.append({"chat_original_source": proof, "text": " late tail"})
        await buffer.flush_terminated(character_uid=UID)
        result = await runtime.sync(UID, byte_budget=10000, max_pull_pages=1)
        assert result["uploaded"] == 1
        row, = map(json.loads, service.records)
        assert row["messageId"] == proof["recordId"] and row["text"] == "reply late tail"
        assert len(await runtime.history(UID)) == 1
        assert (await runtime.sync(UID, byte_budget=10000, max_pull_pages=1))["uploaded"] == 0
    finally:
        buffer.discard()
        await runtime.close()


@pytest.mark.asyncio
async def test_active_bound_card_history_is_not_reported_as_awaiting_enable(tmp_path):
    """Deferred history processing for a non-current role does not falsify enabled UI state."""
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService
    runtime, _, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    try:
        await runtime.enable(UID, 'Synthetic')
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        assert runtime.state.new_role(UID) is not None
        assert (await runtime.status())['syncPendingChoices'] == {}
        await runtime.pause(UID)
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        assert (await runtime.status())['syncPendingChoices'] == {UID: True}
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['generation', 'domain'])
async def test_binding_race_blocks_only_a_changed_saved_domain(tmp_path, monkeypatch, change):
    """A transient same-domain response fence is retryable; an account/binding replacement is not."""
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService
    runtime, _, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    try:
        await runtime.enable(UID, 'Synthetic')
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        saved = runtime.state.new_role(UID)
        async def response_race(*args, **kwargs):
            def replace_binding():
                with runtime.writer.locked(), closing(runtime.state._connect()) as conn, conn:
                    if change == 'generation':
                        conn.execute('UPDATE sync_bindings SET generation=generation+1 WHERE character_uid=?', (UID,))
                    else:
                        conn.execute('UPDATE sync_bindings SET binding_id=? WHERE character_uid=?', ('f' * 32, UID))
            await asyncio.to_thread(replace_binding)
            raise ValueError('chat_sync_binding_changed')
        monkeypatch.setattr(runtime, 'preview_history', response_race)
        await runtime._run_new_role_sync(UID, saved, await runtime.session_presence_provider())
        assert runtime.state.new_role(UID)['requestId'] == saved['requestId']
        assert runtime.state.new_role_errors() == ({UID: 'chat_sync_binding_changed'} if change == 'domain' else {})
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('after_approval', [False, True])
async def test_failed_combined_history_cancels_only_its_own_unapproved_preview(tmp_path, monkeypatch, after_approval):
    """Failed setup does not accumulate preview jobs or revoke a manual/approved snapshot."""
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService
    runtime, _, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    try:
        await runtime.enable(UID, 'Synthetic')
        source.commit_unbound(UID, 'e' * 32, ChatOriginal('user', 'fixed trusted history'))
        manual = await runtime.preview_history(UID, 'Synthetic')
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        actual_approve = runtime.approve_history
        async def approval_failure(*args, **kwargs):
            if after_approval:
                await actual_approve(*args, **kwargs)
            raise ValueError('chat_sync_session_changed')
        monkeypatch.setattr(runtime, 'approve_history', approval_failure)
        await runtime._run_new_role_sync(UID, runtime.state.new_role(UID), await runtime.session_presence_provider())
        with sqlite3.connect(source.path) as conn:
            other = conn.execute('SELECT task_id,status FROM source_history_tasks WHERE task_id!=?', (manual['taskId'],)).fetchall()
        assert len(other) == 1
        assert other[0][1] == ('approved' if after_approval else 'cancelled')
        assert source.task(UID, manual['taskId'])['status'] == 'preview'
        assert runtime.state.new_role_errors() == {UID: 'chat_sync_session_changed'}
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('manual', [False, True])
async def test_setup_account_mismatch_is_visible_without_rebinding_or_resetting_retry(tmp_path, manual):
    """The real owner check must diagnose retained consent and leave all old work intact."""
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService

    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    try:
        await runtime.enable(UID, 'Synthetic')
        capture(runtime)
        pending = runtime.ledger.pending(DOMAIN)
        await runtime.pause(UID)
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        runtime.state.defer_new_role(UID, runtime.test_clock[0] + 30_000)
        saved, binding = runtime.state.new_role(UID), runtime.state.binding(UID)
        service.owner = OTHER_OWNER
        service.requests.clear()
        operation = runtime._run_new_role_sync(UID, saved, await runtime.session_presence_provider(), manual=manual)
        if manual:
            with pytest.raises(ValueError, match='chat_sync_account_mismatch'):
                await operation
        else:
            await operation
        retained = runtime.state.new_role(UID)
        assert retained['lastError'] == 'chat_sync_account_mismatch'
        assert retained['next_at'] == saved['next_at'] and 'error' not in retained
        assert (await runtime.status())['syncSetupErrors'] == {UID: 'chat_sync_account_mismatch'}
        assert runtime.state.binding(UID) == binding and runtime.ledger.pending(DOMAIN) == pending
        assert [request.url.path for request in service.requests] == ['/v1/chat-sync/identity']
        with pytest.raises(ValueError, match='chat_sync_account_mismatch'):
            await runtime.recover_writer_role(UID, 'Synthetic')
        assert runtime.state.binding(UID) == binding and runtime.ledger.pending(DOMAIN) == pending
        service.owner = OWNER
        await runtime._run_new_role_sync(UID, retained, await runtime.session_presence_provider())
        assert runtime.state.new_role(UID) is None and runtime.state.new_role_errors() == {}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_setup_diagnostics_cannot_expose_unknown_errors_or_overwrite_a_newer_choice(tmp_path, monkeypatch):
    """Retained diagnostics contain fixed codes and belong to one saved request."""
    from unittest.mock import AsyncMock
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService

    runtime, _, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    try:
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        saved = runtime.state.new_role(UID)
        monkeypatch.setattr(runtime, 'enable', AsyncMock(side_effect=ValueError('private token in an unknown error')))
        await runtime._run_new_role_sync(UID, saved, await runtime.session_presence_provider())
        assert runtime.state.new_role_errors() == {}
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        runtime.state.record_new_role_error(UID, saved['requestId'], 'chat_sync_account_mismatch')
        assert runtime.state.new_role_errors() == {}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cancelled_setup_cleans_preview_committed_before_thread_returns(tmp_path, monkeypatch):
    """Cancellation cannot orphan a background snapshot hidden in a running SQLite thread."""
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService
    runtime, _, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    source = ChatOriginalSource(runtime.state.path.parent / 'sources.sqlite3')
    entered, release = threading.Event(), threading.Event()
    worker = None
    try:
        await runtime.enable(UID, 'Synthetic')
        source.commit_unbound(UID, 'e' * 32, ChatOriginal('user', 'fixed trusted history'))
        manual = await runtime.preview_history(UID, 'Synthetic')
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        original_prepare = ChatOriginalSource.prepare_history
        def committed_but_not_returned(self, *args, **kwargs):
            result = original_prepare(self, *args, **kwargs)
            entered.set()
            assert release.wait(5), 'test must release the committed source thread'
            return result
        monkeypatch.setattr(ChatOriginalSource, 'prepare_history', committed_but_not_returned)
        worker = asyncio.create_task(runtime._run_new_role_sync(
            UID, runtime.state.new_role(UID), await runtime.session_presence_provider()))
        assert await asyncio.to_thread(entered.wait, 5)
        worker.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await worker
        # This read also waits for the real writer thread to leave its commit lock.
        assert source.task(UID)['status'] == 'cancelled'
        assert source.task(UID, manual['taskId'])['status'] == 'preview'
        assert runtime.state.new_role(UID) is not None and runtime.state.new_role_errors() == {}
    finally:
        release.set()
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)
        await runtime.close()
