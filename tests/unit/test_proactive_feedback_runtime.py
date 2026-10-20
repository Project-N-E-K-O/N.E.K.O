"""Transport ownership and exact acknowledgement failure-mode regressions."""
import asyncio
import copy
import json
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
from portalocker.exceptions import AlreadyLocked, LockException

from services.proactive_feedback import host
from services.proactive_feedback.protocol import COMMUNITY_ORIGIN, SCHEMA_VERSION, canonical_bytes, payload_hash, valid_receipt
from services.proactive_feedback.runtime import ProactiveFeedbackRuntime, SyncFailure, drain_settings
from main_logic.recommendation_feedback import FeedbackConflict


def make_batch():
    return {"schema_version": SCHEMA_VERSION, "batch_id": str(uuid4()), "source_id": str(uuid4()), "items": [
        {"id": "r1", "kind": "recommendation", "sequence": 1, "payload": {"title": "音乐"}},
        {"id": "e1", "kind": "event", "sequence": 2, "payload": {"kind": "shown"}},
    ]}


def receipt(batch, status="stored"):
    return {**{key: batch[key] for key in ("schema_version", "batch_id", "source_id")},
            "batch_hash": payload_hash(batch), "results": [
                {"id": item["id"], "kind": item["kind"], "payload_hash": payload_hash(item["payload"]), "status": status}
                for item in reversed(batch["items"])]}


class Store:
    def __init__(self):
        self.batch = make_batch()
        self.acks, self.errors, self.settings = [], [], []
        self.blocked = False

    def prepare_batch(self, owner, limit):
        assert limit == 100
        return copy.deepcopy(self.batch)

    def sync_status(self, owner):
        return {"pending": len(self.batch["items"]) if self.batch else 0, "blocked": self.blocked,
                "acked": sum(len(ack[1]["results"]) for ack in self.acks),
                "last_error": self.errors[-1][2] if self.errors else None}

    def resume_blocked_batch(self, owner, batch_id, batch_hash):
        if not self.blocked or not self.batch or self.batch["batch_id"] != batch_id or payload_hash(self.batch) != batch_hash:
            raise FeedbackConflict("feedback_resume_conflict")
        self.blocked = False
        return True

    def acknowledge_batch(self, owner, result):
        assert valid_receipt(self.batch, result)
        self.acks.append((copy.deepcopy(owner), result))
        self.batch = None
        return True

    def mark_batch_error(self, owner, batch_id, code, permanent=False):
        self.errors.append((copy.deepcopy(owner), batch_id, code, permanent))
        self.blocked = permanent

    def enqueue_settings(self, owner, entry_id, payload):
        self.settings.append((owner, entry_id, payload))


@pytest.fixture
def setup():
    saved = {"base_url": COMMUNITY_ORIGIN, "local_user_id": "user-a", "access_token": "secret-token"}
    store = Store()
    now = [0.0]
    requests = []
    owner = host.owner_from_snapshot(saved)

    async def resolver():
        return {"logged_in": True, "snapshot": dict(saved)}

    @contextmanager
    def attribution():
        yield host.owner_from_snapshot(saved)

    async def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"user": {"id": saved["local_user_id"]}})
        batch = json.loads(request.content)
        return httpx.Response(200, json=receipt(batch))

    return saved, store, now, requests, owner, resolver, attribution, handler


def runtime_for(setup, handler=None, **kwargs):
    saved, store, now, requests, owner, resolver, attribution, default_handler = setup
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler or default_handler))
    runtime = ProactiveFeedbackRuntime(store, snapshot_provider=lambda: dict(saved), session_provider=resolver,
                                       attribution_context=attribution, clock=lambda: now[0], client=client,
                                       fenced_snapshot_provider=lambda: dict(saved), **kwargs)
    return runtime, client


@pytest.mark.asyncio
async def test_success_exact_canonical_body_and_authenticated_target(setup):
    runtime, client = runtime_for(setup)
    batch = copy.deepcopy(setup[1].batch)
    try:
        result = await runtime.sync_once()
        assert result["state"] == "synced"
        assert setup[1].acks[0][0] == setup[4]
        assert [request.url.path for request in setup[3]] == ["/api/users/me", "/api/proactive/feedback/batches"]
        assert all(str(request.url).startswith(COMMUNITY_ORIGIN + "/") for request in setup[3])
        assert setup[3][1].content == canonical_bytes(batch)
        assert "secret-token" not in json.dumps(await runtime.status())
        assert (await runtime.sync_once())["state"] == "idle"
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_lost_response_retries_frozen_body_and_duplicate_receipt(setup):
    bodies = []

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"user": {"id": "user-a"}})
        bodies.append(request.content)
        if len(bodies) == 1:
            # Server committed, but the acknowledgement was lost in transit.
            raise httpx.ReadError("sensitive upstream text", request=request)
        return httpx.Response(200, json=receipt(json.loads(request.content), "duplicate"))

    runtime, client = runtime_for(setup, handler)
    try:
        assert (await runtime.sync_once()) == {"state": "retry_wait", "error_code": "cloud_unavailable"}
        assert not setup[1].acks
        assert (await runtime.sync_once())["state"] == "retry_wait"
        setup[2][0] = 31
        assert (await runtime.sync_once())["state"] == "synced"
        assert len(bodies) == 2 and bodies[0] == bodies[1]
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.parametrize("malformation", ["partial", "duplicate", "wrong_batch", "wrong_source", "wrong_hash", "wrong_payload", "invalid_status", "invalid_json", "oversized"])
@pytest.mark.asyncio
async def test_malformed_ack_never_discards_pending(setup, malformation):
    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"user": {"id": "user-a"}})
        result = receipt(json.loads(request.content))
        if malformation == "partial":
            result["results"].pop()
        elif malformation == "duplicate":
            result["results"][1] = result["results"][0]
        elif malformation == "wrong_batch":
            result["batch_id"] = str(uuid4())
        elif malformation == "wrong_source":
            result["source_id"] = str(uuid4())
        elif malformation == "wrong_hash":
            result["batch_hash"] = "wrong"
        elif malformation == "wrong_payload":
            result["results"][0]["payload_hash"] = "wrong"
        elif malformation == "invalid_status":
            result["results"][0]["status"] = "failed"
        elif malformation == "invalid_json":
            return httpx.Response(200, content=b'{"truncated":')
        elif malformation == "oversized":
            return httpx.Response(200, content=b"x" * (512 * 1024 + 1))
        return httpx.Response(200, json=result)

    runtime, client = runtime_for(setup, handler)
    try:
        assert (await runtime.sync_once())["error_code"] == "invalid_receipt"
        assert setup[1].batch and not setup[1].acks
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.parametrize("stage", ["resolver", "identity", "post", "commit"])
@pytest.mark.asyncio
async def test_account_replacement_before_ack_keeps_original_batch(setup, stage):
    saved, store = setup[:2]
    posts = []

    async def handler(request):
        if request.method == "GET":
            if stage == "identity":
                saved["local_user_id"] = "user-b"
            return httpx.Response(200, json={"user": {"id": "user-a"}})
        posts.append(request)
        if stage == "post":
            saved["local_user_id"] = "user-b"
        return httpx.Response(200, json=receipt(json.loads(request.content)))

    runtime, client = runtime_for(setup, handler)
    if stage == "resolver":
        async def resolver():
            original = dict(saved)
            saved["local_user_id"] = "user-b"
            return {"logged_in": True, "snapshot": original}
        runtime.session_provider = resolver
    if stage == "commit":
        @contextmanager
        def commit_context():
            saved["local_user_id"] = "user-b"
            yield host.owner_from_snapshot(saved)
        runtime.attribution_context = commit_context
    try:
        assert (await runtime.sync_once())["state"] == "session_changed"
        assert store.batch and not store.acks and not store.errors
        assert len(posts) == (1 if stage in ("post", "commit") else 0)
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_same_owner_oauth_refresh_uses_new_token(setup):
    runtime, client = runtime_for(setup)

    async def resolver():
        setup[0]["access_token"] = "rotated-token"
        return {"logged_in": True, "snapshot": dict(setup[0])}

    runtime.session_provider = resolver
    try:
        assert (await runtime.sync_once())["state"] == "synced"
        assert all(request.headers["authorization"] == "Bearer rotated-token" for request in setup[3])
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.parametrize("mode", ["anonymous", "wrong_origin", "identity_mismatch", "offline", "not_logged_in", "conflict", "rejected", "redirect", "unauthorized", "missing", "timeout"])
@pytest.mark.asyncio
async def test_rejections_retain_data_and_block_only_permanent_conflicts(setup, mode):
    requests = []

    async def handler(request):
        requests.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("secret detail", request=request)
        if request.method == "GET":
            return httpx.Response(200, json={"user": {"id": "user-b" if mode == "identity_mismatch" else "user-a"}})
        status = {"conflict": 409, "rejected": 422, "redirect": 302, "unauthorized": 401, "missing": 404}.get(mode, 503)
        return httpx.Response(status, headers={"location": "https://evil.example/"})

    if mode == "anonymous":
        setup[0].pop("access_token")
    elif mode == "wrong_origin":
        setup[0]["base_url"] = "https://evil.example"
    runtime, client = runtime_for(setup, handler)
    if mode == "not_logged_in":
        async def resolver():
            return {"logged_in": False, "snapshot": dict(setup[0])}
        runtime.session_provider = resolver
    try:
        result = await runtime.sync_once()
        assert result["state"] == ("signed_out" if mode == "anonymous" else "blocked" if mode in ("wrong_origin", "identity_mismatch", "conflict", "rejected") else "retry_wait")
        assert setup[1].batch and not setup[1].acks
        if mode in ("anonymous", "wrong_origin", "not_logged_in"):
            assert not requests
        if mode in ("identity_mismatch", "conflict", "rejected"):
            assert setup[1].blocked
            assert (await runtime.sync_once())["state"] == "blocked"
        assert "secret" not in json.dumps(result)
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_worker_cancellation_during_http_keeps_pending(setup):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runtime, client = runtime_for(setup, handler)
    try:
        runtime.start()
        runtime.start()
        await asyncio.wait_for(started.wait(), 3)
        status = await asyncio.wait_for(runtime.status(), 3)
        assert status["pending"] == 2 and status["fully_synced"] is False
        await asyncio.wait_for(runtime.close(), 3)
        assert cancelled.is_set()
        assert setup[1].batch and not setup[1].acks
        assert await runtime.sync_once() == {"state": "stopped"}
        with pytest.raises(RuntimeError, match="feedback_runtime_closed"):
            runtime.start()
    finally:
        await runtime.close()
        await client.aclose()


def test_settings_transfer_acknowledges_only_durable_members(monkeypatch):
    from utils import preferences
    owner = host.owner_from_snapshot({"local_user_id": "a", "base_url": COMMUNITY_ORIGIN, "access_token": "x"})
    entries = [{"id": "one", "owner": owner, "payload": {"revision": 1}},
               {"id": "two", "owner": owner, "payload": {"revision": 2}}]
    acks = []
    monkeypatch.setattr(preferences, "pending_settings_changes", lambda limit: entries, raising=False)
    monkeypatch.setattr(preferences, "acknowledge_settings_changes", lambda ids: acks.extend(ids), raising=False)
    store = Store()
    original = store.enqueue_settings

    def enqueue(owner, entry_id, payload):
        if entry_id == "two":
            raise OSError("disk full")
        original(owner, entry_id, payload)

    store.enqueue_settings = enqueue
    with pytest.raises(OSError):
        drain_settings(store)
    assert acks == ["one"]
    assert len(store.settings) == 1


def test_protocol_rejects_extra_and_untyped_receipt_members():
    batch = make_batch()
    assert not valid_receipt(batch, [])
    result = receipt(batch)
    result["results"][0]["id"] = []
    assert not valid_receipt(batch, result)
    result = receipt(batch)
    result["results"][0]["secret"] = "unrecognized"
    assert not valid_receipt(batch, result)
    with pytest.raises(ValueError):
        canonical_bytes({"invalid": float("nan")})


@pytest.mark.asyncio
async def test_invalid_resolver_or_rejected_local_commit_does_not_ack(setup):
    runtime, client = runtime_for(setup)

    async def resolver():
        return []

    try:
        runtime.session_provider = resolver
        assert (await runtime.sync_once())["error_code"] == "authentication_required"
        assert not setup[1].acks
        setup[2][0] = 31
        runtime.session_provider = setup[5]
        setup[1].acknowledge_batch = lambda owner, receipt: False
        assert (await runtime.sync_once())["error_code"] == "invalid_receipt"
        assert setup[1].batch is not None
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_worker_survives_local_io_failure_and_only_runs_again_when_woken(setup):
    first, second = asyncio.Event(), asyncio.Event()
    calls = []
    loop = asyncio.get_running_loop()

    def drain(store):
        calls.append(True)
        if len(calls) == 1:
            loop.call_soon_threadsafe(first.set)
            raise ValueError("bad local journal")
        loop.call_soon_threadsafe(second.set)

    runtime, client = runtime_for(setup, settings_drain=drain)
    try:
        runtime.start()
        await asyncio.wait_for(first.wait(), 3)
        # The task has yielded to its bounded event wait, not an immediate loop.
        await asyncio.sleep(0)
        assert len(calls) == 1
        assert (await runtime.status())["settings_import_error"] == "settings_import_unavailable"
        runtime._wake.set()
        await asyncio.wait_for(second.wait(), 3)
        await asyncio.wait_for(runtime._lock.acquire(), 3)
        runtime._lock.release()
        assert setup[1].acks
        assert (await runtime.status())["settings_import_error"] == ""
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_closing_guard_and_status_reject_work_after_shutdown(setup):
    runtime, client = runtime_for(setup)
    try:
        await runtime.close()
        with pytest.raises(SyncFailure, match="closing"):
            await runtime._guard(setup[4])
        with pytest.raises(SyncFailure, match="closing"):
            await runtime.status()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_status_includes_undrained_current_owner_journal_without_mutating(setup):
    setup[1].batch = None
    owner = setup[4]
    entries = [{"id": "same-owner", "owner": owner, "payload": {}},
               {"id": "other-owner", "owner": {**owner, "key": "other", "user_id": "other"}, "payload": {}}]
    runtime, client = runtime_for(setup, settings_pending_provider=lambda: entries)
    try:
        status = await runtime.status()
        assert status["pending"] == 0 and status["settings_journal_pending"] == 1
        assert status["fully_synced"] is False
        assert len(entries) == 2 and not setup[1].acks and not setup[3]
        entries.pop(0)
        status = await runtime.status()
        assert status["settings_journal_pending"] == 0 and status["local_queue_empty"] is True
        assert status["fully_synced"] is False and status["has_cloud_receipt"] is False
        assert "other-owner" not in json.dumps(status) and "other" not in json.dumps(status)
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_unreadable_settings_journal_is_unknown_and_never_reports_fully_synced(setup):
    setup[1].batch = None

    def unreadable():
        raise ValueError("private damaged JSON details")

    runtime, client = runtime_for(setup, settings_pending_provider=unreadable)
    try:
        status = await runtime.status()
        assert status["settings_journal_pending"] is None
        assert status["settings_import_error"] == "settings_journal_unavailable"
        assert status["fully_synced"] is False
        assert "private" not in json.dumps(status)
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_empty_queue_requires_current_identity_and_a_cloud_receipt(setup):
    runtime, client = runtime_for(setup)
    try:
        before = await runtime.status()
        assert not before["identity_verified"] and not before["has_cloud_receipt"]
        assert (await runtime.sync_once())["state"] == "synced"
        status = await runtime.status()
        assert status["fully_synced"] and status["identity_verified"] and status["has_cloud_receipt"]
        assert status["owner_key"] == setup[4]["key"]
        setup[0]["access_token"] = "refreshed-token"
        status = await runtime.status()
        assert not status["identity_verified"] and not status["fully_synced"]
        # Explicit sync can reverify an empty queue without inventing a batch.
        assert (await runtime.sync_once())["state"] == "idle"
        assert (await runtime.status())["fully_synced"]
        setup[0].pop("access_token")
        status = await runtime.status()
        assert not status["signed_in"] and not status["fully_synced"]
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_never_uploaded_empty_queue_is_not_cloud_success(setup):
    setup[1].batch = None
    runtime, client = runtime_for(setup)
    try:
        assert (await runtime.sync_once())["state"] == "idle"
        status = await runtime.status()
        assert status["identity_verified"] and status["local_queue_empty"]
        assert not status["has_cloud_receipt"] and not status["fully_synced"]
        assert len(setup[3]) == 1 and setup[3][0].method == "GET"
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.parametrize("rejected_status", [409, 422])
@pytest.mark.asyncio
async def test_explicit_resume_after_cloud_repair_retries_identical_batch(setup, rejected_status):
    bodies = []
    repaired = False

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"user": {"id": "user-a"}})
        bodies.append(request.content)
        return (httpx.Response(200, json=receipt(json.loads(request.content))) if repaired
                else httpx.Response(rejected_status))

    runtime, client = runtime_for(setup, handler)
    batch = copy.deepcopy(setup[1].batch)
    command = {"owner_key": setup[4]["key"], "batch_id": batch["batch_id"], "batch_hash": payload_hash(batch)}
    try:
        assert (await runtime.sync_once())["state"] == "blocked"
        repaired = True
        assert (await runtime.sync_once())["state"] == "blocked"  # Never auto-release.
        result = await runtime.resume_blocked_batch(**command)
        assert result["state"] == "resumed" and setup[1].batch == batch
        assert not setup[1].acks and len(bodies) == 1
        assert (await runtime.sync_once())["state"] == "synced"
        assert bodies[0] == bodies[1] == canonical_bytes(batch)
        with pytest.raises(FeedbackConflict):
            await runtime.resume_blocked_batch(**command)
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.parametrize("invalid", ["owner", "batch", "hash", "signed_out", "origin", "paused"])
@pytest.mark.asyncio
async def test_resume_rejects_stale_cross_account_or_paused_requests(setup, invalid):
    store = setup[1]
    store.blocked = True
    original = copy.deepcopy(store.batch)
    command = {"owner_key": setup[4]["key"], "batch_id": original["batch_id"], "batch_hash": payload_hash(original)}
    if invalid == "owner":
        setup[0]["local_user_id"] = "user-b"
    elif invalid == "batch":
        command["batch_id"] = str(uuid4())
    elif invalid == "hash":
        command["batch_hash"] = "0" * 64
    elif invalid == "signed_out":
        setup[0].pop("access_token")
        command["owner_key"] = host.owner_from_snapshot(setup[0])["key"]
    elif invalid == "origin":
        setup[0]["base_url"] = "https://example.invalid"
        command["owner_key"] = host.owner_from_snapshot(setup[0])["key"]
    runtime, client = runtime_for(setup, upload_paused=invalid == "paused")
    try:
        with pytest.raises((SyncFailure, FeedbackConflict)):
            await runtime.resume_blocked_batch(**command)
        assert store.blocked and store.batch == original and not store.acks
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_resumes_apply_at_most_one_cas(setup):
    setup[1].blocked = True
    batch = setup[1].batch
    command = {"owner_key": setup[4]["key"], "batch_id": batch["batch_id"], "batch_hash": payload_hash(batch)}
    runtime, client = runtime_for(setup)
    try:
        results = await asyncio.gather(runtime.resume_blocked_batch(**command), runtime.resume_blocked_batch(**command), return_exceptions=True)
        assert sum(isinstance(result, dict) and result["state"] == "resumed" for result in results) == 1
        assert sum(isinstance(result, FeedbackConflict) for result in results) == 1
        assert setup[1].batch == batch and not setup[1].acks
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_resume_cancelled_before_fenced_commit_preserves_blocked(setup):
    import threading

    setup[1].blocked = True
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    runtime, client = runtime_for(setup)
    batch = setup[1].batch
    command = {"owner_key": setup[4]["key"], "batch_id": batch["batch_id"], "batch_hash": payload_hash(batch)}

    @contextmanager
    def delayed_fence():
        entered.set()
        assert release.wait(5)
        try:
            yield host.owner_from_snapshot(setup[0])
        finally:
            exited.set()

    runtime.attribution_context = delayed_fence
    task = asyncio.create_task(runtime.resume_blocked_batch(**command))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(exited.wait, 3)
        assert setup[1].blocked and setup[1].batch == batch
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_resume_rechecks_same_account_token_under_commit_fence(setup):
    setup[1].blocked = True
    runtime, client = runtime_for(setup)
    batch = setup[1].batch
    command = {"owner_key": setup[4]["key"], "batch_id": batch["batch_id"], "batch_hash": payload_hash(batch)}

    @contextmanager
    def replaced_token_fence():
        setup[0]["access_token"] = "new-session-token"
        yield host.owner_from_snapshot(setup[0])

    runtime.attribution_context = replaced_token_fence
    try:
        with pytest.raises(SyncFailure, match="session_changed"):
            await runtime.resume_blocked_batch(**command)
        assert setup[1].blocked and setup[1].batch == batch
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_maintenance_pause_drains_local_journal_without_network(setup):
    drained = []
    runtime, client = runtime_for(setup, upload_paused=True, settings_drain=lambda store: drained.append(store))
    original = copy.deepcopy(setup[1].batch)
    try:
        assert await runtime.sync_once() == {"state": "paused", "error_code": "upload_paused"}
        assert drained == [setup[1]] and not setup[3]
        status = await runtime.status()
        assert status["upload_paused"] and not status["fully_synced"]
        assert setup[1].batch == original and not setup[1].acks
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.parametrize("stage", ["resolver", "identity", "fence"])
@pytest.mark.asyncio
async def test_resume_account_replacement_at_each_boundary_preserves_original(setup, stage):
    setup[1].blocked = True
    original = copy.deepcopy(setup[1].batch)
    command = {"owner_key": setup[4]["key"], "batch_id": original["batch_id"], "batch_hash": payload_hash(original)}

    async def handler(request):
        assert request.method == "GET"
        if stage == "identity":
            setup[0]["local_user_id"] = "user-b"
        return httpx.Response(200, json={"user": {"id": "user-a"}})

    runtime, client = runtime_for(setup, handler)
    if stage == "resolver":
        async def replace():
            previous = dict(setup[0])
            setup[0]["local_user_id"] = "user-b"
            return {"logged_in": True, "snapshot": previous}
        runtime.session_provider = replace
    elif stage == "fence":
        @contextmanager
        def replace_fence():
            setup[0]["local_user_id"] = "user-b"
            yield host.owner_from_snapshot(setup[0])
        runtime.attribution_context = replace_fence
    try:
        with pytest.raises(SyncFailure, match="session_changed"):
            await runtime.resume_blocked_batch(**command)
        assert setup[1].blocked and setup[1].batch == original and not setup[1].acks
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_resume_cancelled_during_identity_leaves_persistent_block(setup):
    setup[1].blocked = True
    original = copy.deepcopy(setup[1].batch)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runtime, client = runtime_for(setup, handler)
    task = asyncio.create_task(runtime.resume_blocked_batch(owner_key=setup[4]["key"],
                                                           batch_id=original["batch_id"], batch_hash=payload_hash(original)))
    try:
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set() and setup[1].blocked and setup[1].batch == original
    finally:
        if not task.done():
            task.cancel()
        await runtime.close()
        await client.aclose()


@pytest.mark.parametrize("failure", ["resolver", "resolved_owner", "missing_token", "identity", "store", "closed"])
@pytest.mark.asyncio
async def test_resume_failures_preserve_blocked_and_do_not_publish_success(setup, failure):
    setup[1].blocked = True
    batch = copy.deepcopy(setup[1].batch)
    runtime, client = runtime_for(setup)
    command = {"owner_key": setup[4]["key"], "batch_id": batch["batch_id"], "batch_hash": payload_hash(batch)}
    if failure in {"resolver", "resolved_owner", "missing_token"}:
        async def invalid():
            snapshot = dict(setup[0])
            if failure == "resolved_owner":
                snapshot["local_user_id"] = "user-b"
            elif failure == "missing_token":
                snapshot["access_token"] = 10  # Truthy, but not a bearer string.
            return {"logged_in": failure != "resolver", "snapshot": snapshot}
        runtime.session_provider = invalid
    elif failure == "identity":
        async def bad_identity(*args, **kwargs):
            return {"user": {"id": "user-b"}}
        runtime._json_request = bad_identity
    elif failure == "store":
        setup[1].resume_blocked_batch = lambda *args: False
    elif failure == "closed":
        await runtime.close()
    try:
        with pytest.raises(SyncFailure):
            await runtime.resume_blocked_batch(**command)
        assert setup[1].blocked and setup[1].batch == batch and not setup[1].acks
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_worker_retries_settings_lock_timeout_without_discarding_facts(setup):
    first, retried = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    calls = []

    def drain(store):
        calls.append(store)
        if len(calls) == 1:
            loop.call_soon_threadsafe(first.set)
            raise AlreadyLocked("private lock path")
        loop.call_soon_threadsafe(retried.set)

    runtime, client = runtime_for(setup, settings_drain=drain)
    try:
        runtime.start()
        await asyncio.wait_for(first.wait(), 3)
        async with runtime._lock:
            pass
        assert not runtime._task.done() and setup[1].acks
        status = await runtime.status()
        assert status["running"] and status["settings_import_error"] == "settings_import_unavailable"
        assert not status["fully_synced"] and "private" not in json.dumps(status)
        runtime._wake.set()
        await asyncio.wait_for(retried.wait(), 3)
        async with runtime._lock:
            pass
        assert (await runtime.status())["fully_synced"]
        await asyncio.wait_for(runtime.close(), 3)
        assert runtime._task is None
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_worker_retries_lock_timeout_before_settings_drain(setup):
    failed, uploaded = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    calls = 0

    def snapshot():
        nonlocal calls
        calls += 1
        if calls == 1:
            loop.call_soon_threadsafe(failed.set)
            raise LockException("private snapshot lock path")
        return dict(setup[0])

    async def handler(request):
        response = await setup[7](request)
        if request.method == "POST":
            uploaded.set()
        return response

    runtime, client = runtime_for(setup, handler)
    runtime.snapshot_provider = snapshot
    try:
        runtime.start()
        await asyncio.wait_for(failed.wait(), 3)
        # This read also proves the failed pass has released the worker lock.
        status = await runtime.status()
        assert status["running"] and status["pending"] == 2
        runtime._wake.set()
        await asyncio.wait_for(uploaded.wait(), 3)
        async with runtime._lock:
            pass
        assert (await runtime.status())["fully_synced"]
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.parametrize("read_number", [1, 2])
@pytest.mark.parametrize("error", [AlreadyLocked, OSError, ValueError])
@pytest.mark.asyncio
async def test_status_failed_journal_confirmation_is_unknown(setup, read_number, error):
    calls = 0

    def pending():
        nonlocal calls
        calls += 1
        if calls == read_number:
            raise error("private journal details")
        return []

    runtime, client = runtime_for(setup, settings_pending_provider=pending)
    try:
        assert (await runtime.sync_once())["state"] == "synced"
        status = await runtime.status()
        assert calls == read_number
        assert status["settings_journal_pending"] is None
        assert status["settings_import_error"] == "settings_journal_unavailable"
        assert not status["fully_synced"] and not status["local_queue_empty"]
        assert "private" not in json.dumps(status)
        # A later successful read needs no repair or mutation to report success.
        assert (await runtime.status())["fully_synced"]
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.parametrize("reason", ["journal", "outbox", "blocked", "import_error"])
@pytest.mark.asyncio
async def test_status_does_not_repeat_slow_journal_read_when_already_incomplete(setup, reason):
    calls = []

    def pending():
        calls.append(True)
        return [{"owner": setup[4]}] if reason == "journal" else []

    runtime, client = runtime_for(setup, settings_pending_provider=pending)
    if reason != "outbox":
        setup[1].batch = None
    if reason == "blocked":
        setup[1].blocked = True
    if reason == "import_error":
        runtime._settings_import_error = "settings_import_unavailable"
    try:
        status = await runtime.status()
        assert calls == [True] and not status["local_queue_empty"] and not status["fully_synced"]
    finally:
        await runtime.close()
        await client.aclose()


@pytest.mark.parametrize("interruption", ["cancel", "close", "account", "token"])
@pytest.mark.asyncio
async def test_status_confirmation_respects_cancellation_close_and_identity(setup, interruption):
    import threading

    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    calls = 0

    def pending():
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            try:
                assert release.wait(5)
            finally:
                exited.set()
        return []

    runtime, client = runtime_for(setup, settings_pending_provider=pending)
    task = None
    try:
        assert (await runtime.sync_once())["state"] == "synced"
        task = asyncio.create_task(runtime.status())
        assert await asyncio.to_thread(entered.wait, 3)
        if interruption == "cancel":
            task.cancel()
        elif interruption == "close":
            await asyncio.wait_for(runtime.close(), 3)
        elif interruption == "account":
            setup[0]["local_user_id"] = "user-b"
        else:
            setup[0]["access_token"] = "new-token"
        release.set()
        with pytest.raises(asyncio.CancelledError if interruption == "cancel" else SyncFailure):
            await asyncio.wait_for(task, 3)
        assert await asyncio.to_thread(exited.wait, 3)
        assert not runtime._settings_lock.locked()
        assert len(setup[1].acks) == 1
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await runtime.close()
        await client.aclose()
