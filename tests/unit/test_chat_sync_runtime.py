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
from tests.chat_sync_legacy_ledger import insert_legacy_original

from services.chat_sync.ledger import ChatDomain, ChatOriginal
from utils.recent_memory_protocol import merge, mutation
from services.chat_sync.runtime import (
    ChatSyncServiceError, DesktopChatSyncRuntime, DesktopSyncState, _load_local_ca_context, _saved_session_commit_guard,
)


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


def setup_runtime(tmp_path, service=None, *, fixed_service_url=None, recent_memory_adapter=None, long_term_memory_adapter=None):
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

    async def recent_character_name(uid):
        return 'Neko' if uid == UID else None

    runtime = DesktopChatSyncRuntime(
        tmp_path / "chat_sync", resolve,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(service.request)),
        session_commit_guard=commit_guard,
        clock=lambda: clock[0], uniform=lambda *_: 0,
        current_character_uid_provider=current_character_uid,
        fixed_service_url=fixed_service_url,
        recent_memory_adapter=recent_memory_adapter, recent_character_name=recent_character_name,
        long_term_memory_adapter=long_term_memory_adapter,
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


def queued_deletions(runtime):
    """Read fixed marker requests straight from the ledger outbox."""
    with closing(sqlite3.connect(runtime.ledger.path)) as conn:
        return conn.execute("SELECT deletion_id, payload FROM chat_deletions WHERE domain=? ORDER BY rowid",
                            (DOMAIN.key(),)).fetchall()


def capture(runtime, index=1):
    """Insert a synthetic legacy original without reading user memory."""
    return insert_legacy_original(runtime.ledger.path, DOMAIN, ChatOriginal("user", "  full text\n🙂  "),
                                  ("db", "generation", str(index)))


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
        assert runtime.ledger.visible_originals(DOMAIN) == []
    finally:
        await runtime.close()


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
        assert runtime.recent_memory is not None
        await runtime.history_changed(UID)
        await runtime.history_changed(OTHER_UID)
        assert sent == [{"type": "chat_sync_history_changed", "characterSyncId": UID, "memoryEditedName": "Synthetic"}]
        assert await runtime.current_character_uid_provider() == UID
        character_data["当前猫娘"] = "Other"
        assert await runtime.current_character_uid_provider() == OTHER_UID
        character_data["当前猫娘"] = "Absent"
        assert await runtime.current_character_uid_provider() is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["delivered", "unchanged", "window-error", "paused-before-hint"])
async def test_recent_download_history_hint_is_fenced_and_optional(tmp_path, monkeypatch, case):
    """Only a downloaded change hints the window, under its grant, and a window failure never fails the pull."""
    monkeypatch.setattr("memory.recent_sync.assert_cloudsave_writable", lambda *args, **kwargs: None)
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko"})
    if case != "unchanged":
        service.rows[UID] = [{"id": "f" * 32, "role": "user", "text": "from phone", "originalTime": None}]
    hinted = []

    async def history_changed(uid):
        hinted.append(uid)
        if case == "window-error":
            raise RuntimeError("synthetic window closed")

    runtime.history_changed = history_changed
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        if case == "paused-before-hint":
            sync = runtime.recent_memory.sync

            async def sync_then_pause(*args, **kwargs):
                result = await sync(*args, **kwargs)
                await runtime.pause(UID)
                return result

            monkeypatch.setattr(runtime.recent_memory, "sync", sync_then_pause)
        await runtime._automatic_channel("pull", await runtime.session_presence_provider())
        assert service.recent_roles == [UID]
        saved = json.loads((tmp_path / "memory" / "Neko" / "recent.json").read_text(encoding="utf-8"))
        assert [row["data"]["content"] for row in saved] == ([] if case == "unchanged" else ["from phone"])
        # A periodic pull that downloaded nothing must not make the window reload its memory.
        assert hinted == ([UID] if case in {"delivered", "window-error"} else [])
        assert runtime.scheduler.last_error(DOMAIN.key()) is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_recent_download_blocked_by_a_newer_local_save_continues_without_the_periodic_wait(tmp_path, monkeypatch):
    """A download that loses to a concurrent local save asks for the next pass instead of waiting two minutes."""
    monkeypatch.setattr("memory.recent_sync.assert_cloudsave_writable", lambda *args, **kwargs: None)
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko"})
    service.rows[UID] = [{"id": "f" * 32, "role": "user", "text": "from phone", "originalTime": None}]
    path = tmp_path / "memory" / "Neko" / "recent.json"

    async def save_during_first_download(request):
        if request.url.path.endswith("/recent-memory") and not service.recent_roles:
            # A local-only entry changes the file without changing the shared projection.
            path.write_text(json.dumps([{"type": "tool", "data": {"content": "local only"}}]), encoding="utf-8")

    service.on_request = save_during_first_download
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        await run_scheduler_tick(runtime)
        assert service.recent_roles == [UID]
        assert runtime.recent_memory.state(DOMAIN.key())["base"] == []
        # The periodic poll is two minutes away; only the unfinished download makes the role due now.
        await run_scheduler_tick(runtime)
        assert service.recent_roles == [UID, UID]
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert [row["data"]["content"] for row in saved] == ["local only", "from phone"]
        assert runtime.scheduler.last_error(DOMAIN.key()) is None
    finally:
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
        assert service.requests == []
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
        assert service.requests == []
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
        assert service.requests == []
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
        assert service.requests == []
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
        assert service.requests == []
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
        assert len(runtime.ledger.visible_originals(DOMAIN)) == 1
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
    assert len(runtime.ledger.visible_originals(DOMAIN)) == 1 and service.requests == []
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
async def test_established_silent_wss_automatically_wakes_recent_pull(tmp_path, local_tls_files):
    """Native keepalive notices a silent established peer, and its failure hint wakes the fallback pull."""
    from websockets.asyncio.server import serve
    ca_path, _, cert_path, key_path = local_tls_files
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    subscribed, pause, paused, release = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def peer(socket):
        await socket.recv()
        await socket.send(json.dumps({"type": "ready"}))
        await socket.recv()
        await socket.send(json.dumps({"type": "subscribed"}))
        subscribed.set()
        await pause.wait()
        # Stop reading without closing: only the client's keepalive can notice.
        socket.transport.pause_reading()
        paused.set()
        try:
            await release.wait()
        finally:
            socket.transport.resume_reading()

    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko"})
    try:
        async with serve(peer, "127.0.0.1", 0, ssl=context, ping_interval=None) as server:
            try:
                runtime._local_ca_file = str(ca_path)
                runtime._notification_connect = runtime._connect_notifications
                await runtime.configure(f"https://localhost:{server.sockets[0].getsockname()[1]}")
                await runtime.enable(UID, "Neko")
                runtime.start_scheduler()
                await asyncio.wait_for(subscribed.wait(), 5)
                await wait_notification(lambda: service.recent_roles)
                # The start and subscribe hints may each pull once; wait until that settles.
                async with asyncio.timeout(6):
                    settled = -1
                    while len(service.recent_roles) != settled:
                        settled = len(service.recent_roles)
                        await asyncio.sleep(1)
                pause.set()
                await asyncio.wait_for(paused.wait(), 5)
                # The frozen test clock never reaches the next poll or a reconnect deadline. The
                # 2 s ping interval, 2 s pong timeout and 1 s close wait notice the peer in about 5 s.
                async with asyncio.timeout(10):
                    while len(service.recent_roles) == settled:
                        await asyncio.sleep(.02)
            finally:
                # An early failure must not leave the peer handler waiting, or the
                # server would wait for it forever while closing.
                pause.set()
                release.set()
                await runtime.close()
    finally:
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
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [4401, 4403, 1008])
async def test_notification_rejection_waits_for_new_authority(tmp_path, code):
    """Server authentication/grant rejection cannot become an infinite reconnect or stop HTTP fallback."""
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko"})
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
    await run_scheduler_tick(runtime)
    # The rejected hint socket stays closed while the fenced automatic pull keeps running.
    assert service.recent_roles == [UID]
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
    assert len(runtime.ledger.visible_originals(DOMAIN)) == 1
    assert runtime._notification_retry_at > runtime.scheduler.clock()
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
        pending = queued_deletions(runtime)

        status = await runtime.status()
        assert status["serviceUrl"] == "https://sync.nekotech.cn"
        assert status["writerRecoveryRequired"] is True
        assert status["bindings"][0]["enabled"] == 0
        with pytest.raises(WriterQuarantined):
            await runtime._identity("synthetic-a")
        assert service.requests == []
        assert runtime.state.settings()["service_url"] == "https://localhost:8443"
        assert runtime.state.binding(UID) == binding
        assert queued_deletions(runtime) == pending
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
            await runtime.recover_writer_role(UID, "Neko")
        runtime.writer.check()
        assert runtime.state.settings()["service_url"] == "https://sync.nekotech.cn"
        binding = runtime.state.binding(UID)
        assert binding["enabled"] == 0 and binding["binding_id"] is None
        assert queued_deletions(runtime) == []
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
def test_changed_service_owner_cannot_rebind_existing_role(tmp_path, change_token):
    """A replacement owner cannot adopt an existing role, even at the same origin/token."""
    async def scenario():
        """Verify rejection before any foreign registration request."""
        runtime, service, session = setup_runtime(tmp_path)
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        before = runtime.state.binding(UID)
        service.requests.clear()
        service.owner = OTHER_OWNER
        if change_token:
            session["snapshot"]["access_token"] = "synthetic-b"
        with pytest.raises(ValueError, match="chat_sync_account_mismatch"):
            await runtime.enable(UID, "Neko")
        assert runtime.state.binding(UID) == before
        # The identity check is the only permitted traffic; nothing registers under the new owner.
        assert [request.url.path for request in service.requests] == ["/v1/chat-sync/identity"]
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
    assert queued_deletions(runtime) == []
    assert len(runtime.ledger.visible_originals(DOMAIN)) == 1
    asyncio.run(runtime.close())


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
        assert runtime.state.bindings() == []
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


def test_sqlite_worker_failure_is_bounded_observed_and_private(tmp_path, monkeypatch, capsys):
    """Disk failures neither crash chat startup nor create an unobserved hot loop."""
    async def scenario():
        from services.chat_sync import runtime as module
        runtime, _, _ = setup_runtime(tmp_path)
        calls = 0

        def failed_deadline():
            nonlocal calls
            calls += 1
            raise sqlite3.OperationalError("synthetic private path and SQL")

        # Every pass reads the durable deadline after its tick, so each pass fails here.
        monkeypatch.setattr(runtime.scheduler, "next_wake_at", failed_deadline)
        # Stay above the Windows event-loop clock resolution (~15.6 ms).
        monkeypatch.setattr(module, "_RECENT_FALLBACK_SECONDS", 0.05)
        runtime.start_scheduler()
        # Each failed pass waits out the 0.2 s write coalescing before retrying.
        await asyncio.sleep(0.6)
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


@pytest.mark.asyncio
async def test_default_memory_adapter_imports_stay_off_the_event_loop(tmp_path, monkeypatch):
    import builtins
    from services.chat_sync import runtime as module
    original = builtins.__import__
    main_thread = threading.get_ident()
    imported = set()
    def checked_import(name, *args, **kwargs):
        if name in {'memory.recent_sync', 'memory.long_term_sync'}:
            assert threading.get_ident() != main_thread, 'memory import blocked the event loop'
            imported.add(name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', checked_import)
    manager = SimpleNamespace(app_docs_dir=tmp_path / 'isolated', local_state_dir=tmp_path / 'installation')
    try:
        await module.get_desktop_chat_sync_runtime(manager)
        assert imported == {'memory.recent_sync', 'memory.long_term_sync'}
    finally:
        await module.close_desktop_chat_sync_runtimes()


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
from tests.real_root_isolation import install
from utils.config_manager import ConfigManager

install(ConfigManager)
from utils import logger_config
logger_config.RobustLoggerConfig._get_documents_directory = lambda self: Path(sys.argv[1]) / 'logs'
from services.chat_sync import runtime as module
# Warm imports before timing constructor cancellation; import placement is
# independently checked by the event-loop regression above.
from memory.recent_sync import RecentMemoryAdapter
from memory.long_term_sync import DesktopLongTermAdapter

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
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script, str(tmp_path / "isolated")],
                            capture_output=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr


def select_runtime_current(runtime, uid=UID):
    """Expose a mutable current selection independently of durable consent."""
    selected = [uid]

    async def current_character_uid():
        return selected[0]

    runtime.current_character_uid_provider = current_character_uid
    return selected


class RecentRoleService(SyntheticService):
    """Serve each role's memory lanes with the real recent-memory codec and record which roles were exchanged."""

    def __init__(self):
        super().__init__()
        self.recent_roles = []
        self.long_term_roles = []
        self.rows, self.hidden, self.receipts = {}, {}, {}
        self.lose_receipt = False

    async def request(self, request):
        parent, lane = request.url.path.rsplit("/", 2)[1:]
        if parent in {"recent-memory", "long-term-memory"}:
            # Lane sub-resources this fake does not implement answer 404, as an older service would.
            return httpx.Response(404, json={"error": "not_found"})
        if lane not in {"recent-memory", "long-term-memory"}:
            return await super().request(request)
        self.requests.append(request)
        if self.on_request is not None:
            await self.on_request(request)
        assert request.headers["X-Neko-Chat-Binding"] == BINDING
        uid = request.url.path.rsplit("/", 2)[1]
        if lane == "long-term-memory":
            # No learned memory has been published, so the lane only reads an empty head.
            assert request.method == "GET"
            self.long_term_roles.append(uid)
            return httpx.Response(200, json={"ownerId": self.owner, "characterSyncId": uid, "revision": 0,
                                             "completedAt": None})
        self.recent_roles.append(uid)
        receipts = self.receipts.setdefault(uid, {})
        if request.method == "GET":
            return httpx.Response(200, json={"schemaVersion": 1, "ownerId": self.owner, "characterSyncId": uid,
                                             "revision": len(receipts), "entries": self.rows.get(uid, [])})
        value = mutation(json.loads(request.content))
        operation = value["operationId"]
        if operation not in receipts:
            self.rows[uid], self.hidden[uid] = merge(self.rows.get(uid, []), self.hidden.get(uid, []), value)
            receipts[operation] = {"ownerId": self.owner, "characterSyncId": uid, "operationId": operation,
                                   "revision": len(receipts) + 1}
        if self.lose_receipt:
            # The service committed the operation, but its receipt never reaches the device.
            self.lose_receipt = False
            raise httpx.ReadError("synthetic response loss")
        return httpx.Response(200, json=receipts[operation])


def recent_row(kind, text, key=None, original_time=None):
    """Build a saved recent.json row that already carries its stable projection identity."""
    metadata = {} if kind == "system" else {"recent_memory_id": key, "recent_memory_time": original_time}
    return {"type": kind, "data": {"content": text, "metadata": metadata}}


def recent_role_runtime(tmp_path, names, *, chats=None, long_term=False):
    """Give each named role a recent-memory file and the production memory channels.

    Rows in ``chats`` already carry their identities, so reading them never rewrites the file.
    """
    from memory.recent_sync import RecentMemoryAdapter

    for name in names.values():
        path = tmp_path / "memory" / name / "recent.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps((chats or {}).get(name, [])), encoding="utf-8")
    adapter = RecentMemoryAdapter(SimpleNamespace(memory_dir=tmp_path / "memory", project_memory_dir=tmp_path / "memory"))
    long_term_adapter = None
    if long_term:
        from memory.long_term_sync import DesktopLongTermAdapter
        long_term_adapter = DesktopLongTermAdapter(SimpleNamespace(app_docs_dir=tmp_path / "documents"))
    runtime, service, session = setup_runtime(tmp_path, RecentRoleService(), recent_memory_adapter=adapter,
                                              long_term_memory_adapter=long_term_adapter)

    async def recent_character_name(uid):
        return names.get(uid)

    runtime.recent_character_name = recent_character_name
    return runtime, service, session


async def run_scheduler_tick(runtime):
    """Run one scheduler decision and wait for the finite channel sweeps it launched."""
    await runtime.scheduler_tick()
    await finish_automatic_flights(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", ["current", "other", "paused", "unbound", "none", "missing-provider"])
async def test_current_role_automatic_pull_requires_enabled_current_intersection(tmp_path, monkeypatch, selected):
    """Consent alone never starts an automatic pull; the selected role still needs an enabled binding."""
    third = "d" * 32
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko", OTHER_UID: "Other", third: "Third"})
    passes = []
    channel_pass = runtime._channel_pass

    async def observed_pass(uid, channel, **kwargs):
        passes.append((uid, channel))
        return await channel_pass(uid, channel, **kwargs)

    monkeypatch.setattr(runtime, "_channel_pass", observed_pass)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        runtime.state.activate(runtime.state.prepare_binding(ChatDomain(OWNER, OTHER_UID)), BINDING)
        # Saved consent without a service binding must not be treated as enabled.
        runtime.state.prepare_binding(ChatDomain(OWNER, third))
        if selected == "paused":
            await runtime.pause(UID)
        if selected == "missing-provider":
            runtime.current_character_uid_provider = None
        else:
            select_runtime_current(runtime, {"current": UID, "other": OTHER_UID, "paused": UID,
                                             "unbound": third, "none": None}[selected])
        service.requests.clear()
        await runtime._automatic_channel("pull", await runtime.session_presence_provider())
        expected = {"current": [UID], "other": [OTHER_UID]}.get(selected, [])
        assert passes == [(uid, "pull") for uid in expected]
        assert service.recent_roles == expected
        if not expected:
            assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,switch_after", [("pull", "identity"), ("pull", "upload"), ("long_term", "identity")])
async def test_current_role_switch_stops_the_old_role_before_its_next_memory_request(tmp_path, channel, switch_after):
    """A response accepted for the selected role cannot carry a deselected role into its next request."""
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko", OTHER_UID: "Other"}, long_term=True,
                                              chats={"Neko": [recent_row("human", "unsent", "1" * 32)]})
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        selected = select_runtime_current(runtime)

        async def switch_role(request):
            if (request.method, request.url.path.rsplit("/", 1)[1]) == {
                    "identity": ("GET", "identity"), "upload": ("PUT", "recent-memory")}[switch_after]:
                selected[0] = OTHER_UID

        service.on_request = switch_role
        service.requests.clear()
        await runtime._automatic_channel(channel, await runtime.session_presence_provider())
        sent = [(request.method, request.url.path.rsplit("/", 1)[1]) for request in service.requests]
        state = runtime.recent_memory.state(DOMAIN.key())
        if switch_after == "upload":
            # The already-issued upload receipt still commits; only the next download stops.
            assert sent == [("GET", "identity"), ("PUT", "recent-memory")]
            assert state["pending"] is None and [row["text"] for row in state["base"]] == ["unsent"]
        else:
            # Neither the unsent chat nor a learned-memory check leaves for the deselected role.
            assert sent == [("GET", "identity")]
            assert state["base"] == []
        assert state["revision"] == 0
        assert runtime.scheduler.last_error(DOMAIN.key()) is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_switch_while_waiting_channel_lock_records_no_attempt(tmp_path, monkeypatch):
    """A sweep queued behind another recent-memory pass rechecks the selection before admission or HTTP."""
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko"},
                                              chats={"Neko": [recent_row("human", "unsent", "1" * 32)]})
    queued = asyncio.Event()
    channel_pass = runtime._channel_pass

    async def queued_pass(*args, **kwargs):
        queued.set()
        return await channel_pass(*args, **kwargs)

    monkeypatch.setattr(runtime, "_channel_pass", queued_pass)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        selected = select_runtime_current(runtime)
        presence = await runtime.session_presence_provider()
        service.requests.clear()
        async with runtime._recent_lock:
            sweep = asyncio.create_task(runtime._automatic_channel("pull", presence))
            # The selected role passed the outer filter and now waits for the channel lock.
            await queued.wait()
            selected[0] = OTHER_UID
        await sweep
        assert service.requests == []
        assert runtime.scheduler.ready(DOMAIN.key(), "pull", runtime.state.binding(UID)["generation"], presence)
        assert runtime.scheduler.last_error(DOMAIN.key()) is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_automatic_login_pause_idle_and_periodic_recent_pull(tmp_path):
    """No consent or no login means no cloud work; a login pulls promptly, then every two minutes until paused."""
    runtime, service, session = recent_role_runtime(tmp_path, {UID: "Neko"})
    try:
        await runtime.configure("https://sync.test")
        await run_scheduler_tick(runtime)
        assert service.requests == [] and "pull" not in runtime._flights
        await runtime.enable(UID, "Neko")
        service.requests.clear()
        session["logged_in"] = False
        await run_scheduler_tick(runtime)
        assert service.requests == [] and "pull" not in runtime._flights
        session["logged_in"] = True
        await run_scheduler_tick(runtime)
        assert service.recent_roles == [UID]
        runtime.test_clock[0] += 119_000
        await run_scheduler_tick(runtime)
        assert service.recent_roles == [UID]
        runtime.test_clock[0] += 1_000
        await run_scheduler_tick(runtime)
        assert service.recent_roles == [UID, UID]
        await runtime.pause(UID)
        flight = runtime._flights["pull"]
        runtime.test_clock[0] += 120_000
        await run_scheduler_tick(runtime)
        # A paused role launches no new channel sweep at all.
        assert runtime._flights["pull"] is flight and service.recent_roles == [UID, UID]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_selection_and_catchup_wake_only_the_selected_role(tmp_path):
    """Selecting a role pulls it promptly; a foreground catch-up never reschedules a deselected role."""
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko", OTHER_UID: "Other"})
    other = ChatDomain(OWNER, OTHER_UID)
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        runtime.state.activate(runtime.state.prepare_binding(other), BINDING)
        selected = select_runtime_current(runtime)
        presence = await runtime.session_presence_provider()
        await run_scheduler_tick(runtime)
        selected[0] = OTHER_UID
        await run_scheduler_tick(runtime)
        # UID's periodic poll is two minutes away; only selecting it again makes it due now.
        selected[0] = UID
        await run_scheduler_tick(runtime)
        assert service.recent_roles == [UID, OTHER_UID, UID]
        await runtime.request_catchup()
        assert not runtime.scheduler.ready(other.key(), "pull", runtime.state.binding(OTHER_UID)["generation"], presence)
        await run_scheduler_tick(runtime)
        assert service.recent_roles == [UID, OTHER_UID, UID, UID]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_chat_hint_also_wakes_the_long_term_memory_lane(tmp_path):
    """A chat change notice makes the selected role's learned-memory check due together with its recent pull."""
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko"}, long_term=True)
    socket = SyntheticNotifications()
    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        runtime._notification_connect = socket.connect
        await runtime._manage_notifications(runtime.state.bindings(), await runtime.session_presence_provider())
        await wait_notification(lambda: socket.receives >= 3)
        await run_scheduler_tick(runtime)
        assert service.recent_roles == service.long_term_roles == [UID]
        await run_scheduler_tick(runtime)
        assert service.recent_roles == service.long_term_roles == [UID]
        # Learned memory has no notification channel of its own; it rides on the chat hint, not the profile one.
        await socket.incoming.put(json.dumps({"type": "changed", "characterSyncId": UID, "channel": "chat"}))
        await wait_notification(lambda: socket.receives >= 4)
        await run_scheduler_tick(runtime)
        assert service.recent_roles == service.long_term_roles == [UID, UID]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_status_counts_a_recent_memory_operation_frozen_by_a_lost_receipt(tmp_path):
    """Status shows the frozen upload, removal and synced revision until the same operation is acknowledged."""
    memo, first, second, third = (recent_row("system", "memo"), recent_row("human", "first", "1" * 32),
                                  recent_row("ai", "second", "2" * 32), recent_row("human", "third", "3" * 32))
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko"}, chats={"Neko": [memo, first]})

    async def progress():
        binding, = (await runtime.status())["bindings"]
        return {key: binding[key] for key in ("pendingOriginals", "pendingDeletions", "cursor", "hasSealedBatch")}

    try:
        await runtime.configure("https://sync.test")
        await runtime.enable(UID, "Neko")
        await run_scheduler_tick(runtime)
        assert await progress() == {"pendingOriginals": 0, "pendingDeletions": 0, "cursor": 1, "hasSealedBatch": False}
        (tmp_path / "memory" / "Neko" / "recent.json").write_text(json.dumps([memo, second, third]), encoding="utf-8")
        service.lose_receipt = True
        await run_scheduler_tick(runtime)
        assert runtime.scheduler.last_error(DOMAIN.key()) == "chat_sync_unavailable"
        # Two new entries and one removal stay frozen in the same unacknowledged operation.
        assert await progress() == {"pendingOriginals": 2, "pendingDeletions": 1, "cursor": 1, "hasSealedBatch": True}
        runtime.test_clock[0] += 3_000
        await run_scheduler_tick(runtime)
        assert await progress() == {"pendingOriginals": 0, "pendingDeletions": 0, "cursor": 2, "hasSealedBatch": False}
        assert [row["text"] for row in service.rows[UID]] == ["memo", "second", "third"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_shared_history_reads_the_recent_memory_projection_offline(tmp_path):
    """History lists saved dialogue under its stable identity, hides the memo and needs no login or HTTP."""
    runtime, service, session = recent_role_runtime(tmp_path, {UID: "Neko"}, chats={"Neko": [
        recent_row("system", "private memo"), recent_row("human", "hello", "1" * 32, "1791540000123"),
        recent_row("ai", "welcome back", "2" * 32)]})
    session["logged_in"] = False
    common = {"characterSyncId": UID, "originSeq": None, "originStreamId": None, "conversationId": None,
              "replyToMessageId": None}
    try:
        assert await runtime.history(UID) == [
            {**common, "messageId": "1" * 32, "role": "user", "text": "hello", "originalTime": "1791540000123"},
            {**common, "messageId": "2" * 32, "role": "assistant", "text": "welcome back", "originalTime": None},
        ]
        # A role whose local character cannot be resolved has no shared projection.
        assert await runtime.history(OTHER_UID) == []
        assert service.requests == []
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
@pytest.mark.parametrize("failure", [ValueError("chat_sync_unavailable"), ChatSyncServiceError(429, 240000)])
async def test_current_role_catchup_and_switch_preserve_retry_and_origin_backpressure(tmp_path, failure):
    """Selection changes and foreground hints never clear persisted error fences."""
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko", OTHER_UID: "Other"})
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
        # The live pull channel serves the newly selected role unless the whole origin is backing off.
        origin_backoff = getattr(failure, "status_code", None) == 429
        assert service.recent_roles == ([] if origin_backoff else [OTHER_UID])
        service.requests.clear()
        await runtime.request_catchup()
        selected[0] = UID
        await runtime.request_catchup()
        await runtime.scheduler_tick()
        await finish_automatic_flights(runtime)
        assert service.requests == []
        assert not runtime.scheduler.ready(DOMAIN.key(), "pull", 1, presence)
        assert runtime.scheduler.last_error(DOMAIN.key()) == (
            "chat_sync_service_rejected" if origin_backoff else "chat_sync_unavailable")
        if origin_backoff:
            assert not runtime.scheduler.origin_ready()
            # Even the selected, otherwise admissible role sends nothing during the origin backoff.
            selected[0] = OTHER_UID
            assert await runtime._channel_pass(OTHER_UID, "pull", presence=presence) is None
            assert service.requests == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_current_role_missing_constructor_provider_cannot_resume_saved_automatic_consent(tmp_path):
    """A caller omitting local selection must not resume every persisted binding."""
    runtime, service, _ = recent_role_runtime(tmp_path, {UID: "Neko"})
    await runtime.configure("https://sync.test")
    await runtime.enable(UID, "Neko")
    await runtime.close()
    reopened = DesktopChatSyncRuntime(
        tmp_path / "chat_sync", runtime.session_provider,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(service.request)),
        session_commit_guard=runtime.session_commit_guard,
        clock=runtime.scheduler.clock, uniform=lambda *_: 0,
        recent_memory_adapter=runtime.recent_memory.adapter, recent_character_name=runtime.recent_character_name,
    )
    try:
        service.requests.clear()
        await reopened.scheduler_tick()
        # The automatic pull channel really ran; it found no selected role to sync.
        assert "pull" in reopened._flights
        await finish_automatic_flights(reopened)
        assert service.requests == []
        binding = reopened.state.binding(UID)
        assert binding["enabled"]
        # Not even an admission attempt was recorded for the saved role.
        assert reopened.scheduler.ready(DOMAIN.key(), "pull", binding["generation"],
                                        await reopened.session_presence_provider())
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
            assert runtime.ledger.visible_originals(DOMAIN)
            assert queued_deletions(runtime) == [(deletion.deletion_id, deletion.payload)]
            assert not runtime.state.binding(UID)['enabled']
    finally:
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
        # Choices saved by older versions still pin the bound domain they approved.
        with runtime.writer.locked():
            runtime.state.queue_new_role(UID, {**runtime.state.new_role(UID), 'historyBinding': runtime.state.binding(UID)})
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
        monkeypatch.setattr(runtime.profiles, 'local', response_race)
        await runtime._run_new_role_sync(UID, saved, await runtime.session_presence_provider())
        assert runtime.state.new_role(UID)['requestId'] == saved['requestId']
        assert runtime.state.new_role_errors() == ({UID: 'chat_sync_binding_changed'} if change == 'domain' else {})
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_setup_account_mismatch_is_visible_without_rebinding_or_resetting_retry(tmp_path):
    """The real owner check must diagnose retained consent and leave all old work intact."""
    from services.chat_sync.profile_sync import DesktopProfileAdapter
    from tests.unit.test_character_profile_sync import Config, ProfileService

    runtime, service, _ = setup_runtime(tmp_path, ProfileService())
    runtime.profiles.adapter = DesktopProfileAdapter(Config())
    try:
        await runtime.enable(UID, 'Synthetic')
        capture(runtime)
        originals = runtime.ledger.visible_originals(DOMAIN)
        await runtime.pause(UID)
        await runtime.save_character_sync_choice(UID, 'Synthetic', True)
        runtime.state.defer_new_role(UID, runtime.test_clock[0] + 30_000)
        saved, binding = runtime.state.new_role(UID), runtime.state.binding(UID)
        service.owner = OTHER_OWNER
        service.requests.clear()
        await runtime._run_new_role_sync(UID, saved, await runtime.session_presence_provider())
        retained = runtime.state.new_role(UID)
        assert retained['lastError'] == 'chat_sync_account_mismatch'
        assert retained['next_at'] == saved['next_at'] and 'error' not in retained
        assert (await runtime.status())['syncSetupErrors'] == {UID: 'chat_sync_account_mismatch'}
        assert runtime.state.binding(UID) == binding and runtime.ledger.visible_originals(DOMAIN) == originals
        assert [request.url.path for request in service.requests] == ['/v1/chat-sync/identity']
        with pytest.raises(ValueError, match='chat_sync_account_mismatch'):
            await runtime.recover_writer_role(UID, 'Synthetic')
        assert runtime.state.binding(UID) == binding and runtime.ledger.visible_originals(DOMAIN) == originals
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


