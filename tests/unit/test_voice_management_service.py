"""Import ownership, local commits and acknowledged/uncertain remote updates."""

import asyncio
import json
import threading
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI

from tests.unit.test_voice_management_storage import MemoryVoiceManager
from utils.voice_management import service
from utils.voice_management.types import (
    ManagementCapabilities, RemoteVoice, VoiceManagementError, VoicePage, VoiceRuntime,
    build_voice_scope,
)


class Adapter:
    capabilities = ManagementCapabilities(True, True, True)

    def __init__(self):
        self.remote = RemoteVoice("remote-original", "Server name", "2025-01-01", "ready", {
            "remote_revision": "1", "clone_model": "voice-model",
        }, True)
        self.detail_error = None
        self.list_error = None
        self.on_detail = None
        self.on_list = None
        self.on_mutation = None
        self.mutations = []

    def resolve_runtime(self, cm):
        scope, bucket = build_voice_scope("minimax", cm.key, "https://vendor.example")
        return VoiceRuntime("minimax", cm.key, "https://vendor.example", scope, bucket,
                            settings={"management_secret": cm.management_secret})

    def capabilities_for(self, runtime):
        return self.capabilities

    def import_metadata(self, runtime):
        return {"minimax_base_url": runtime.base_url}

    def manual_fields(self, runtime):
        return []

    def validate_voice_id(self, value):
        return value

    async def get_voice(self, runtime, voice_id):
        if self.on_detail:
            self.on_detail()
        if self.detail_error:
            raise self.detail_error
        return self.remote

    async def list_voices(self, runtime, *, cursor=None, query=""):
        if self.on_list:
            self.on_list()
        if self.list_error:
            raise self.list_error
        return VoicePage([self.remote], "next")

    async def overwrite(self, runtime, voice_id, *, audio, filename, before_mutation=None):
        if before_mutation:
            await before_mutation(self.remote)
        self.mutations.append((runtime.api_key, voice_id, audio, filename))
        if self.on_mutation:
            return await self.on_mutation()
        self.remote = replace(self.remote, metadata={"remote_revision": "2"})
        return self.remote


@pytest.fixture
def fixture(monkeypatch):
    from utils.voice_management import providers

    cm = MemoryVoiceManager()
    cm.key = "private-synthesis-key"
    cm.management_secret = "private-management-key"
    adapter = Adapter()
    monkeypatch.setattr(providers, "get_adapter", lambda provider: adapter)
    return cm, adapter


def payload(adapter, cm, **kwargs):
    return {
        "provider": "minimax", "remote_voice_id": "remote-original",
        "context_token": service.context_token(adapter.resolve_runtime(cm)), **kwargs,
    }


async def imported(fixture):
    cm, adapter = fixture
    result = await service.import_remote_voice(adapter, cm, payload(adapter, cm))
    return cm, adapter, result["voice_id"]


@pytest.mark.asyncio
async def test_context_and_public_results_do_not_expose_credentials(fixture):
    cm, adapter = fixture
    context = await service.management_context(adapter, cm)
    result = await service.import_remote_voice(adapter, cm, payload(adapter, cm))
    for data in (context, result):
        encoded = json.dumps(data)
        assert cm.key not in encoded and cm.management_secret not in encoded
        assert "scope_id" not in encoded and "storage_key" not in encoded
    assert result["voice_data"]["remote_created_at"] == "2025-01-01"
    assert result["voice_data"]["imported_at"] != "2025-01-01"


@pytest.mark.asyncio
async def test_query_is_read_only_and_import_is_idempotent(fixture):
    cm, adapter = fixture
    token = payload(adapter, cm)["context_token"]
    listing = await service.list_remote_voices(adapter, cm, token=token)
    assert not cm.storage and not listing["voices"][0]["imported"]
    first = await service.import_remote_voice(adapter, cm, payload(adapter, cm))
    second = await service.import_remote_voice(adapter, cm, payload(adapter, cm))
    assert first["voice_id"] == second["voice_id"] and not second["created"]
    listing = await service.list_remote_voices(adapter, cm, token=token)
    assert listing["voices"][0]["local_ref"] == first["voice_id"]
    assert adapter.mutations == []


@pytest.mark.asyncio
async def test_remote_list_tolerates_legacy_library_values(fixture, monkeypatch):
    cm, adapter, ref = await imported(fixture)
    original = cm.get_voices_for_current_api

    def mixed_library(*args, **kwargs):
        return {**original(*args, **kwargs), "legacy-name": "Old voice", "legacy-null": None}

    monkeypatch.setattr(cm, "get_voices_for_current_api", mixed_library)
    before = json.dumps(cm.storage, sort_keys=True)
    result = await service.list_remote_voices(adapter, cm, token=payload(adapter, cm)["context_token"])
    assert result["voices"][0]["local_ref"] == ref
    assert result["voices"][0]["imported"]
    assert json.dumps(cm.storage, sort_keys=True) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["PERMISSION_DENIED", "MANAGEMENT_CONFIG_MISSING"])
async def test_manual_fallback_is_saved_without_claiming_verification(fixture, code):
    cm, adapter = fixture
    adapter.detail_error = VoiceManagementError(code, 403)
    result = await service.import_remote_voice(adapter, cm, payload(adapter, cm))
    assert result["verification"] == "unverified"
    assert not result["voice_data"]["can_overwrite"]


@pytest.mark.asyncio
async def test_manual_import_when_listing_and_details_are_unsupported(fixture):
    cm, adapter = fixture
    adapter.capabilities = ManagementCapabilities(False, False, False)
    adapter.detail_error = AssertionError("must not call upstream")
    result = await service.import_remote_voice(adapter, cm, payload(adapter, cm))
    assert result["verification"] == "unverified" and cm.storage


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, 5, "", "a\n", "a" * 513])
async def test_invalid_remote_id_never_writes(fixture, bad):
    cm, adapter = fixture
    with pytest.raises(VoiceManagementError, match="INVALID_VOICE_ID"):
        await service.import_remote_voice(adapter, cm, payload(adapter, cm, remote_voice_id=bad))
    assert not cm.storage


@pytest.mark.asyncio
async def test_metadata_cannot_choose_bucket_key_or_endpoint(fixture):
    cm, adapter = fixture
    with pytest.raises(VoiceManagementError, match="INVALID_METADATA"):
        await service.import_remote_voice(adapter, cm, payload(adapter, cm, metadata={"base_url": "http://evil"}))
    assert not cm.storage


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["detail", "list"])
async def test_late_results_after_credentials_change_are_rejected(fixture, stage):
    cm, adapter = fixture
    token = payload(adapter, cm)["context_token"]
    def change():
        cm.key = "different-account-key"
    if stage == "detail":
        adapter.on_detail = change
        call = service.import_remote_voice(adapter, cm, {"context_token": token, "remote_voice_id": "remote-original"})
    else:
        adapter.on_list = change
        call = service.list_remote_voices(adapter, cm, token=token)
    with pytest.raises(VoiceManagementError, match="CONTEXT_CHANGED"):
        await call
    assert not cm.storage


@pytest.mark.asyncio
async def test_changed_management_credentials_invalidate_context_without_moving_scope(fixture):
    cm, adapter = fixture
    previous = adapter.resolve_runtime(cm)
    cm.management_secret = "new-management-secret"
    current = adapter.resolve_runtime(cm)
    assert previous.scope_id == current.scope_id
    assert service.context_token(previous) != service.context_token(current)
    with pytest.raises(VoiceManagementError, match="CONTEXT_CHANGED"):
        await service.import_remote_voice(adapter, cm, {
            "remote_voice_id": "remote-original", "context_token": service.context_token(previous),
        })


@pytest.mark.asyncio
async def test_timeout_during_validation_is_not_saved_as_manual_success(fixture):
    cm, adapter = fixture
    adapter.detail_error = VoiceManagementError("UPSTREAM_TIMEOUT", 504)
    with pytest.raises(VoiceManagementError, match="UPSTREAM_TIMEOUT"):
        await service.import_remote_voice(adapter, cm, payload(adapter, cm))
    assert not cm.storage


@pytest.mark.asyncio
async def test_overwrite_keeps_local_and_remote_identity(fixture):
    cm, adapter, ref = await imported(fixture)
    result = await service.overwrite_remote_voice(adapter, cm, ref,
        token=payload(adapter, cm)["context_token"], audio=b"audio", filename="voice.wav")
    assert result["voice_id"] == ref and result["status"] == "completed"
    assert adapter.mutations[0][1] == "remote-original"
    assert cm.get_imported_voice(ref)["remote_voice_id"] == "remote-original"


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_status", ["processing", "ready"])
async def test_refresh_does_not_credit_a_revision_that_existed_before_overwrite(fixture, returned_status):
    cm, adapter, ref = await imported(fixture)
    # Another client has already updated the remote voice since local import.
    adapter.remote = replace(adapter.remote, metadata={"remote_revision": "2"})

    async def acknowledged_but_not_visible():
        return replace(adapter.remote, status=returned_status)

    adapter.on_mutation = acknowledged_but_not_visible
    token = payload(adapter, cm)["context_token"]
    result = await service.overwrite_remote_voice(adapter, cm, ref, token=token, audio=b"audio", filename="v.wav")
    assert result["status"] == "processing"
    refreshed = await service.refresh_overwrite_status(adapter, cm, ref, token=token)
    assert refreshed["status"] == "processing"
    adapter.remote = replace(adapter.remote, metadata={"remote_revision": "3"})
    assert (await service.refresh_overwrite_status(adapter, cm, ref, token=token))["status"] == "completed"


@pytest.mark.asyncio
async def test_missing_initial_revision_disables_overwrite_without_remote_mutation(fixture):
    cm, adapter = fixture
    adapter.remote = replace(adapter.remote, metadata={})
    token = payload(adapter, cm)["context_token"]
    listing = await service.list_remote_voices(adapter, cm, token=token)
    result = await service.import_remote_voice(adapter, cm, payload(adapter, cm))
    assert not listing["voices"][0]["can_overwrite"]
    assert not result["voice_data"]["can_overwrite"]
    with pytest.raises(VoiceManagementError, match="OVERWRITE_UNSUPPORTED"):
        await service.overwrite_remote_voice(adapter, cm, result["voice_id"], token=token, audio=b"audio", filename="v.wav")
    assert adapter.mutations == []


@pytest.mark.asyncio
async def test_revision_disappearing_before_mutation_does_not_leave_pending_record(fixture):
    cm, adapter, ref = await imported(fixture)
    adapter.remote = replace(adapter.remote, metadata={})
    token = payload(adapter, cm)["context_token"]
    with pytest.raises(VoiceManagementError, match="OVERWRITE_UNSUPPORTED"):
        await service.overwrite_remote_voice(adapter, cm, ref, token=token, audio=b"audio", filename="v.wav")
    assert not cm.get_imported_voice(ref).get("overwrite_status")
    assert adapter.mutations == []


@pytest.mark.asyncio
async def test_unknown_update_is_not_retried_and_old_ready_state_cannot_clear_it(fixture):
    cm, adapter, ref = await imported(fixture)
    async def uncertain():
        raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 504)
    adapter.on_mutation = uncertain
    token = payload(adapter, cm)["context_token"]
    with pytest.raises(VoiceManagementError, match="UPDATE_OUTCOME_UNKNOWN"):
        await service.overwrite_remote_voice(adapter, cm, ref, token=token, audio=b"audio", filename="v.wav")
    assert cm.get_imported_voice(ref)["overwrite_status"] == "unknown"
    with pytest.raises(VoiceManagementError, match="UPDATE_OUTCOME_UNKNOWN"):
        await service.overwrite_remote_voice(adapter, cm, ref, token=token, audio=b"audio", filename="v.wav")
    refreshed = await service.refresh_overwrite_status(adapter, cm, ref, token=token)
    assert refreshed["status"] == "unknown" and len(adapter.mutations) == 1
    adapter.remote = replace(adapter.remote, metadata={"remote_revision": "2"})
    refreshed = await service.refresh_overwrite_status(adapter, cm, ref, token=token)
    assert refreshed["status"] == "completed"


@pytest.mark.asyncio
async def test_overlapping_updates_are_rejected_without_second_mutation(fixture):
    cm, adapter, ref = await imported(fixture)
    started, release = asyncio.Event(), asyncio.Event()
    async def delayed():
        started.set()
        await release.wait()
        return replace(adapter.remote, metadata={"remote_revision": "2"})
    adapter.on_mutation = delayed
    token = payload(adapter, cm)["context_token"]
    first = asyncio.create_task(service.overwrite_remote_voice(adapter, cm, ref, token=token, audio=b"a", filename="v.wav"))
    await started.wait()
    try:
        with pytest.raises(VoiceManagementError, match="OPERATION_IN_PROGRESS"):
            await service.overwrite_remote_voice(adapter, cm, ref, token=token, audio=b"b", filename="v.wav")
    finally:
        release.set()
        await first
    assert len(adapter.mutations) == 1


@pytest.mark.asyncio
async def test_cancel_after_submission_records_unknown_and_releases_lock(fixture):
    cm, adapter, ref = await imported(fixture)
    started = asyncio.Event()
    async def delayed():
        started.set()
        await asyncio.Event().wait()
    adapter.on_mutation = delayed
    task = asyncio.create_task(service.overwrite_remote_voice(adapter, cm, ref,
        token=payload(adapter, cm)["context_token"], audio=b"a", filename="v.wav"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cm.get_imported_voice(ref)["overwrite_status"] == "unknown"
    lock = service._OVERWRITE_LOCKS.get(ref)
    assert lock is None or not lock.locked()


@pytest.mark.asyncio
async def test_routes_validate_json_reject_client_credentials_and_disable_cache(fixture, monkeypatch):
    from main_routers.characters_router import voice_management as routes
    cm, adapter = fixture
    monkeypatch.setattr(routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr(routes, "_adapter", lambda provider: adapter)
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        context = await client.get("/api/characters/remote_voices/context", params={"provider": "minimax"})
        assert context.status_code == 200 and "no-store" in context.headers["cache-control"]
        invalid = await client.post("/api/characters/voices/import", content="bad", headers={"content-type": "application/json"})
        assert invalid.status_code == 400
        injection = await client.post("/api/characters/voices/import", json=payload(adapter, cm, api_key="attacker-key"))
        assert injection.status_code == 400 and not cm.storage
        response = await client.post("/api/characters/voices/import", json=payload(adapter, cm))
        assert response.status_code == 200 and response.json()["voice_id"].startswith("voice_")


@pytest.mark.asyncio
async def test_configuration_change_after_claim_records_failed_without_remote_submission(fixture, monkeypatch):
    cm, adapter, ref = await imported(fixture)
    original = cm.aupdate_imported_voice
    claimed = []

    async def change_after_commit(local_ref, scope, values, **kwargs):
        saved = await original(local_ref, scope, values, **kwargs)
        if values.get("overwrite_status") == "processing":
            claimed.append(saved["overwrite_operation_id"])
            cm.key = "switched-after-local-claim"
        return saved

    monkeypatch.setattr(cm, "aupdate_imported_voice", change_after_commit)
    token = payload(adapter, cm)["context_token"]
    with pytest.raises(VoiceManagementError, match="CONTEXT_CHANGED"):
        await service.overwrite_remote_voice(adapter, cm, ref, token=token, audio=b"audio", filename="sample.wav")
    saved = cm.get_imported_voice(ref, include_inactive=True)
    assert claimed == [saved["overwrite_operation_id"]]
    assert saved["overwrite_status"] == "failed"
    assert saved["availability"] == "unavailable"
    assert adapter.mutations == []
    lock = service._OVERWRITE_LOCKS.get(ref)
    assert lock is None or not lock.locked()


@pytest.mark.asyncio
async def test_cancel_during_threaded_claim_joins_transaction_and_cleans_failed(fixture, monkeypatch):
    cm, adapter, ref = await imported(fixture)
    started = asyncio.Event()
    release = threading.Event()
    worker_finished = threading.Event()
    original_save = cm.save_voice_storage
    loop = asyncio.get_running_loop()

    def delayed_save(storage):
        pending = any(
            record.get("overwrite_status") == "processing"
            for bucket in storage.values() for record in bucket.values()
        )
        if pending and not worker_finished.is_set():
            # This pause is inside the actual storage transaction's RLock,
            # after its operation-ID check and before persistent commit.
            loop.call_soon_threadsafe(started.set)
            if not release.wait(timeout=5):
                raise AssertionError("claim test did not release storage worker")
            original_save(storage)
            worker_finished.set()
        else:
            original_save(storage)

    monkeypatch.setattr(cm, "save_voice_storage", delayed_save)
    task = asyncio.create_task(service.overwrite_remote_voice(
        adapter, cm, ref, token=payload(adapter, cm)["context_token"],
        audio=b"audio", filename="sample.wav",
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    assert worker_finished.is_set()
    assert cm.get_imported_voice(ref)["overwrite_status"] == "failed"
    assert adapter.mutations == []
    lock = service._OVERWRITE_LOCKS.get(ref)
    assert lock is None or not lock.locked()


@pytest.mark.asyncio
async def test_cancel_during_async_claim_waits_for_owned_commit_before_cleanup(fixture, monkeypatch):
    cm, adapter, ref = await imported(fixture)
    started, release, claim_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_update = cm.aupdate_imported_voice

    async def delayed_update(local_ref, scope, values, **kwargs):
        if values.get("overwrite_status") == "processing":
            started.set()
            await release.wait()
            saved = await original_update(local_ref, scope, values, **kwargs)
            claim_finished.set()
            return saved
        return await original_update(local_ref, scope, values, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", delayed_update)
    task = asyncio.create_task(service.overwrite_remote_voice(
        adapter, cm, ref, token=payload(adapter, cm)["context_token"],
        audio=b"audio", filename="sample.wav",
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    assert claim_finished.is_set()
    assert cm.get_imported_voice(ref)["overwrite_status"] == "failed"
    assert adapter.mutations == []


@pytest.mark.asyncio
async def test_refresh_commit_cannot_overwrite_new_operation_after_last_read(fixture, monkeypatch):
    cm, adapter, ref = await imported(fixture)
    scope = adapter.resolve_runtime(cm).scope_id
    await cm.aupdate_imported_voice(ref, scope, {
        "overwrite_operation_id": "previous-operation", "overwrite_status": "completed",
        "overwrite_previous_revision": "1",
    })
    adapter.remote = replace(adapter.remote, metadata={"remote_revision": "2"})
    token = payload(adapter, cm)["context_token"]
    refresh_at_commit, release_refresh = asyncio.Event(), asyncio.Event()
    new_submission, release_new = asyncio.Event(), asyncio.Event()
    original_update = cm.aupdate_imported_voice
    paused = False

    async def pause_first_completion(local_ref, expected_scope, values, **kwargs):
        nonlocal paused
        if not paused and values.get("overwrite_status") == "completed":
            paused = True
            refresh_at_commit.set()
            await release_refresh.wait()
        return await original_update(local_ref, expected_scope, values, **kwargs)

    async def delayed_mutation():
        new_submission.set()
        await release_new.wait()
        return replace(adapter.remote, metadata={"remote_revision": "3"})

    monkeypatch.setattr(cm, "aupdate_imported_voice", pause_first_completion)
    adapter.on_mutation = delayed_mutation
    refresh = asyncio.create_task(service.refresh_overwrite_status(adapter, cm, ref, token=token))
    update = None
    try:
        await asyncio.wait_for(refresh_at_commit.wait(), timeout=5)
        update = asyncio.create_task(service.overwrite_remote_voice(
            adapter, cm, ref, token=token, audio=b"new", filename="new.wav",
        ))
        await asyncio.wait_for(new_submission.wait(), timeout=5)
        latest = cm.get_imported_voice(ref)
        assert latest["overwrite_operation_id"] != "previous-operation"
        release_refresh.set()
        with pytest.raises((ValueError, VoiceManagementError)):
            await asyncio.wait_for(refresh, timeout=5)
        latest_after_old_refresh = cm.get_imported_voice(ref)
        assert latest_after_old_refresh["overwrite_status"] == "processing"
        assert latest_after_old_refresh["overwrite_operation_id"] == latest["overwrite_operation_id"]
        assert latest_after_old_refresh["remote_revision"] == "1"
    finally:
        release_refresh.set()
        release_new.set()
        if update is not None:
            await update
        if not refresh.done():
            refresh.cancel()
            try:
                await refresh
            except asyncio.CancelledError:
                pass
    assert cm.get_imported_voice(ref)["remote_revision"] == "3"


@pytest.mark.asyncio
async def test_public_results_strip_private_endpoint_userinfo_and_query_secrets(fixture):
    cm, adapter = fixture
    private_endpoint = "https://url-user:url-password@vendor.example/api?api_key=query-secret"
    endpoint_fields = {
        field: private_endpoint for field in (
            "minimax_base_url", "elevenlabs_base_url", "dashscope_base_url",
            "doubao_base_url", "glm_base_url",
        )
    }
    adapter.remote = replace(adapter.remote, metadata={
        **adapter.remote.metadata, **endpoint_fields,
    })
    token = payload(adapter, cm)["context_token"]
    listing = await service.list_remote_voices(adapter, cm, token=token)
    imported_result = await service.import_remote_voice(adapter, cm, payload(adapter, cm))
    for result in (listing, imported_result):
        encoded = json.dumps(result)
        for secret in ("url-user", "url-password", "query-secret"):
            assert secret not in encoded
    assert not set(endpoint_fields) & set(listing["voices"][0]["metadata"])
    assert not set(endpoint_fields) & set(imported_result["voice_data"])
    # Synthesis still needs the exact configured endpoint in private storage.
    saved = cm.get_imported_voice(imported_result["voice_id"])
    assert all(saved[field] == private_endpoint for field in endpoint_fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_status", ["failed", "unavailable"])
async def test_acknowledged_update_failure_is_terminal_without_changing_identity(fixture, remote_status):
    cm, adapter, ref = await imported(fixture)
    token = payload(adapter, cm)["context_token"]

    async def failed_result():
        return replace(adapter.remote, status=remote_status, can_overwrite=False)

    adapter.on_mutation = failed_result
    result = await service.overwrite_remote_voice(
        adapter, cm, ref, token=token, audio=b"audio", filename="sample.wav",
    )
    saved = cm.get_imported_voice(ref)
    assert result["status"] == "failed"
    assert saved["overwrite_status"] == "failed"
    assert saved["remote_status"] == remote_status
    assert result["voice_id"] == ref and saved["local_ref"] == ref
    assert saved["remote_voice_id"] == "remote-original"
    assert not saved["can_overwrite"]
    # A terminal failure is distinct from an uncertain update. The current
    # vendor capability still controls whether another mutation is permitted.
    with pytest.raises(VoiceManagementError, match="OVERWRITE_UNSUPPORTED"):
        await service.overwrite_remote_voice(
            adapter, cm, ref, token=token, audio=b"retry", filename="sample.wav",
        )
    assert len(adapter.mutations) == 1
