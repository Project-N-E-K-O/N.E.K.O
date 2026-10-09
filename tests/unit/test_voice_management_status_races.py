"""Actual adapters, HTTP routes and JSON commits under controlled refresh ordering."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from main_routers.characters_router import voice_management as routes
from tests.unit.test_voice_management_storage import MemoryVoiceManager, doubao_import  # noqa: F401
from utils.file_utils import atomic_write_json
from utils.voice_management import providers, service
from utils.voice_management.providers.cosyvoice import CosyVoiceAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts", "cosyvoice", "cosyvoice_intl"], indirect=True)
@pytest.mark.parametrize("terminal", ["completed", "failed"])
@pytest.mark.parametrize("cancel_waiter", [False, True])
@pytest.mark.parametrize("winner_revision", ["2", "3"])
async def test_refresh_wins_between_preflight_and_overwrite_claim(
    remote_record, terminal, cancel_waiter, winner_revision, monkeypatch,
):
    from tests.unit.test_voice_management_revisions import isolated_api, upstream_transport
    from tests.unit.test_voice_management_routes import _wav
    from utils import voice_clone

    cm, adapter, ref, data, storage = remote_record
    provider = adapter.resolve_runtime(cm).provider
    await cm.aupdate_imported_voice(ref, data["scope_id"], {
        "remote_revision": revision_for(provider, "2"), "overwrite_status": terminal,
        "can_overwrite": True,
    })
    api = isolated_api(cm, monkeypatch)
    token = service.context_token(adapter.resolve_runtime(cm))
    query_entered, release_query = asyncio.Event(), asyncio.Event()
    claim_entered, release_claim = asyncio.Event(), asyncio.Event()
    queries, mutations, uploads = 0, [], []

    class Uploader:
        def __init__(self, *args):
            pass

        async def upload_file(self, *args):
            uploads.append(args)
            return "https://controlled.upload/reference.wav"

    monkeypatch.setattr(voice_clone, "QwenVoiceCloneClient", Uploader)

    async def upstream(request):
        nonlocal queries
        body = json.loads(request.content)
        if request.url.path.endswith("/voice_clone") or body.get("input", {}).get("action") == "update_voice":
            current = await asyncio.to_thread(cm.get_imported_voice, ref)
            assert current["remote_revision"] == revision_for(provider, winner_revision)
            mutations.append(request)
            reply = {"code": 0, "speaker_id": data["remote_voice_id"]} if provider == "doubao_tts" else {"output": {}}
            return httpx.Response(200, json=reply)
        queries += 1
        index = queries
        if index == 1:
            query_entered.set()
            await release_query.wait()
        revision = "2" if index == 2 else str(int(winner_revision) + 1) if index >= 4 else winner_revision
        return response_for(provider, data, "stale-ready", revision)

    upstream_transport(monkeypatch, upstream)
    original = cm.aupdate_imported_voice

    async def pause_claim(local_ref, scope, values, **kwargs):
        if values.get("overwrite_status") == "processing" and values.get("overwrite_operation_id") != "same-operation":
            claim_entered.set()
            await release_claim.wait()
        return await original(local_ref, scope, values, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", pause_claim)
    refresh = overwrite = None
    async with api:
        try:
            refresh = asyncio.create_task(api.get(
                f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token},
            ))
            await asyncio.wait_for(query_entered.wait(), 5)
            overwrite = asyncio.create_task(api.post(
                f"/api/characters/voices/{ref}/overwrite", data={"context_token": token},
                files={"audio": ("reference.wav", _wav(), "audio/wav")},
            ))
            await asyncio.wait_for(claim_entered.wait(), 5)
            release_query.set()
            refreshed = await asyncio.wait_for(refresh, 5)
            assert refreshed.status_code == 200
            assert refreshed.json()["voice_data"]["remote_revision"] == revision_for(provider, winner_revision)
            winner = await asyncio.to_thread(cm.get_imported_voice, ref)
            winner_bytes = await asyncio.to_thread(storage.read_bytes)
            if cancel_waiter:
                overwrite.cancel()
            release_claim.set()
            if cancel_waiter:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(overwrite, 5)
            else:
                result = await asyncio.wait_for(overwrite, 5)
                assert result.status_code == 409 and result.json()["code"] == "VOICE_STATE_CHANGED"
            saved = await asyncio.to_thread(cm.get_imported_voice, ref)
            assert saved == winner
            assert await asyncio.to_thread(storage.read_bytes) == winner_bytes
            assert saved["overwrite_operation_id"] == "same-operation"
            assert saved["overwrite_status"] == terminal
            assert len(mutations) == 0 and queries == 2
            # CosyVoice may already have uploaded its reference; no voice update
            # was submitted. A rejected claim does not promise upload rollback.
            assert len(uploads) == (0 if provider == "doubao_tts" else 1)
            remaining_lock = service._OVERWRITE_LOCKS.get(ref)
            assert remaining_lock is None or not remaining_lock.locked()
            if not cancel_waiter:
                retry = await api.post(
                    f"/api/characters/voices/{ref}/overwrite", data={"context_token": token},
                    files={"audio": ("reference.wav", _wav(), "audio/wav")},
                )
                assert retry.status_code == 200 and retry.json()["status"] == "completed"
                retried = await asyncio.to_thread(cm.get_imported_voice, ref)
                assert retried["local_ref"] == ref and retried["remote_voice_id"] == winner["remote_voice_id"]
                assert retried["overwrite_operation_id"] != winner["overwrite_operation_id"]
                assert retried["overwrite_previous_revision"] == revision_for(provider, winner_revision)
                assert retried["remote_revision"] == revision_for(provider, str(int(winner_revision) + 1))
                assert queries == 4 and len(mutations) == 1
            assert await cm.adelete_imported_voice(ref)
        finally:
            release_query.set()
            release_claim.set()
            for task in (refresh, overwrite):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*[task for task in (refresh, overwrite) if task], return_exceptions=True)


def revision_for(provider, value):
    # CosyVoice's documented revision is a modification time, not a counter.
    if provider != "doubao_tts" and isinstance(value, str) and value.isdecimal():
        return f"2026-10-06 10:00:{int(value):02d}"
    return value


@pytest.fixture
def remote_record(request, monkeypatch, doubao_import, tmp_path):
    provider = request.param
    if provider == "doubao_tts":
        cm, adapter, ref, data = doubao_import
    else:
        cm, adapter = MemoryVoiceManager(), CosyVoiceAdapter(provider)
        monkeypatch.setattr(cm, "get_cosyvoice_clone_runtime", lambda selected: {
            "api_key": "controlled-key", "base_url": "https://dashscope-intl.aliyuncs.com/api/v1" if selected.endswith("_intl") else "https://dashscope.aliyuncs.com/api/v1",
        }, raising=False)
        lookup = providers.get_adapter
        monkeypatch.setattr(providers, "get_adapter", lambda selected: adapter if selected == provider else lookup(selected))
        runtime = adapter.resolve_runtime(cm)
        ref, data, _ = cm.import_remote_voice(runtime.scope_id, provider, "remote-cosy", {
            **adapter.import_metadata(runtime), "remote_revision": revision_for(provider, "1"), "can_overwrite": True,
        })
    storage = tmp_path / "voice_storage.json"
    atomic_write_json(storage, cm.storage)
    monkeypatch.setattr(cm, "load_voice_storage", lambda: json.loads(storage.read_text(encoding="utf-8")))
    monkeypatch.setattr(cm, "save_voice_storage", lambda value: atomic_write_json(storage, value))
    cm.update_imported_voice(ref, data["scope_id"], {
        "overwrite_operation_id": "same-operation", "overwrite_status": "processing",
        "overwrite_previous_revision": revision_for(provider, "1"),
    })
    return cm, adapter, ref, data, storage


def response_for(provider, data, kind, revision):
    if provider == "doubao_tts":
        state = {"pending": "Training", "stale-ready": "Success", "failed": "Unknown", "completed": "Success"}[kind]
        return httpx.Response(200, json={"Result": {"Statuses": [{
            "SpeakerID": data["remote_voice_id"], "State": state, "Version": revision,
            "AvailableTrainingTimes": 5,
        }]}})
    state = {"pending": "UNKNOWN", "stale-ready": "OK", "failed": "UNDEPLOYED", "completed": "OK"}[kind]
    return httpx.Response(200, json={"output": {
        "voice_id": data["remote_voice_id"], "target_model": "cosyvoice-v3-plus",
        "status": state, "gmt_modified": revision_for(provider, revision),
    }})


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts", "cosyvoice", "cosyvoice_intl"], indirect=True)
@pytest.mark.parametrize("checkpoint", ["remote", "commit"])
@pytest.mark.parametrize("stale_kind", ["stale-ready", "failed", "pending"])
async def test_parallel_refresh_returns_persisted_winner(remote_record, checkpoint, stale_kind, monkeypatch):
    cm, adapter, ref, data, storage = remote_record
    provider = adapter.resolve_runtime(cm).provider
    token = service.context_token(adapter.resolve_runtime(cm))
    entered, release = asyncio.Event(), asyncio.Event()
    calls, paused = 0, False
    client = httpx.AsyncClient
    monkeypatch.setattr(routes, "get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(routes.router)
    api = client(transport=httpx.ASGITransport(app=app), base_url="http://isolated.local")

    async def upstream(request):
        nonlocal calls
        calls += 1
        stale = calls == 1
        if stale and checkpoint == "remote":
            entered.set()
            await release.wait()
        kind = stale_kind if stale else ("pending" if stale_kind == "pending" else "completed")
        revision = "2" if stale and stale_kind == "pending" else "1" if stale else "3"
        return response_for(provider, data, kind, revision)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **{**kwargs, "transport": httpx.MockTransport(upstream)},
    ))
    update = cm.aupdate_imported_voice

    async def delayed_commit(local_ref, scope, values, **kwargs):
        nonlocal paused
        if checkpoint == "commit" and not paused:
            paused = True
            entered.set()
            await release.wait()
        return await update(local_ref, scope, values, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", delayed_commit)
    path = f"/api/characters/voices/{ref}/overwrite_status"
    async with api:
        old = asyncio.create_task(api.get(path, params={"context_token": token}))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            current = await api.get(path, params={"context_token": token})
            assert current.status_code == 200
            before_late = await asyncio.to_thread(storage.read_bytes)
            release.set()
            late = await asyncio.wait_for(old, timeout=5)
            assert late.status_code == 200
            expected = "processing" if stale_kind == "pending" else "completed"
            assert late.json()["voice_data"] == current.json()["voice_data"]
            assert late.json()["details"]["voice_state"] == current.json()["details"]["voice_state"]
            assert current.json()["details"]["state_sync"] == "saved"
            assert late.json()["details"]["state_sync"] == "unchanged"
            assert late.json()["status"] == expected
            assert late.json()["voice_data"]["remote_revision"] == revision_for(provider, "3")
            assert "_record_revision" not in late.json()["voice_data"]
            assert "scope_id" not in late.json()["voice_data"]
            # The losing refresh performs no write, including no counter increment.
            assert await asyncio.to_thread(storage.read_bytes) == before_late
            again = await api.get(path, params={"context_token": token})
            assert again.status_code == 200 and again.json()["status"] == expected
            assert calls == 3  # Only detail queries; no retry or mutation request.
        finally:
            release.set()
            if not old.done():
                old.cancel()
            await asyncio.gather(old, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts"], indirect=True)
@pytest.mark.parametrize("change", ["config", "operation", "delete", "read-failure", "save-failure", "cancel"])
async def test_refresh_commit_conflicts_do_not_claim_success(remote_record, change, monkeypatch):
    cm, adapter, ref, data, storage = remote_record
    token = service.context_token(adapter.resolve_runtime(cm))
    entered, release = asyncio.Event(), asyncio.Event()
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **{**kwargs, "transport": httpx.MockTransport(lambda request: response_for("doubao_tts", data, "completed", "3"))},
    ))
    original = cm.aupdate_imported_voice

    async def delayed_commit(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", delayed_commit)
    pending = asyncio.create_task(service.refresh_overwrite_status(adapter, cm, ref, token=token))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        if change == "config":
            # Force the conditional path to return a winner, then invalidate its context.
            await original(ref, data["scope_id"], {"overwrite_status": "completed"})
            cm.raw["ttsModelApiKey"] = "changed-key"
            cm.raw["assistApiKeyDoubaoTts"] = "changed-key"
        elif change == "operation":
            await original(ref, data["scope_id"], {"overwrite_operation_id": "new-operation"})
        elif change == "delete":
            await original(ref, data["scope_id"], {"overwrite_status": "completed"})
            assert await cm.adelete_imported_voice(ref)
        elif change == "read-failure":
            await asyncio.to_thread(storage.write_text, "{broken", encoding="utf-8")
        elif change == "save-failure":
            def reject_save(value):
                raise OSError("controlled save failure")
            monkeypatch.setattr(cm, "save_voice_storage", reject_save)
        else:
            pending.cancel()
        before = await asyncio.to_thread(storage.read_bytes)
        release.set()
        error_type = asyncio.CancelledError if change == "cancel" else service.VoiceManagementError
        with pytest.raises(error_type) as error:
            await asyncio.wait_for(pending, timeout=5)
        if change == "config":
            assert error.value.code == "CONTEXT_CHANGED"
        elif change in {"operation", "delete"}:
            assert error.value.code == "CONTEXT_CHANGED"
        elif change in {"read-failure", "save-failure"}:
            assert error.value.code == "STORAGE_ERROR"
            assert error.value.details["state_sync"] == "failed"
        assert await asyncio.to_thread(storage.read_bytes) == before
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts", "cosyvoice", "cosyvoice_intl"], indirect=True)
async def test_cancel_remote_refresh_can_query_again(remote_record, monkeypatch):
    cm, adapter, ref, data, storage = remote_record
    provider = adapter.resolve_runtime(cm).provider
    token = service.context_token(adapter.resolve_runtime(cm))
    entered, release = asyncio.Event(), asyncio.Event()
    client = httpx.AsyncClient
    calls = 0

    async def upstream(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return response_for(provider, data, "completed", "3")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **{**kwargs, "transport": httpx.MockTransport(upstream)},
    ))
    before = await asyncio.to_thread(storage.read_bytes)
    pending = asyncio.create_task(service.refresh_overwrite_status(adapter, cm, ref, token=token))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert await asyncio.to_thread(storage.read_bytes) == before
        result = await service.refresh_overwrite_status(adapter, cm, ref, token=token)
        assert result["status"] == "completed"
        assert result["voice_data"]["remote_revision"] == revision_for(provider, "3")
        assert calls == 2
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts"], indirect=True)
async def test_cancel_waiter_does_not_rollback_started_storage_commit(remote_record, monkeypatch):
    import threading

    cm, adapter, ref, data, storage = remote_record
    token = service.context_token(adapter.resolve_runtime(cm))
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **{**kwargs, "transport": httpx.MockTransport(lambda request: response_for("doubao_tts", data, "completed", "3"))},
    ))
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    save = cm.save_voice_storage

    def delayed_save(value):
        entered.set()
        assert release.wait(5)
        try:
            save(value)
        finally:
            finished.set()

    pending = None
    try:
        with monkeypatch.context() as delay:
            delay.setattr(cm, "save_voice_storage", delayed_save)
            pending = asyncio.create_task(service.refresh_overwrite_status(adapter, cm, ref, token=token))
            assert await asyncio.to_thread(entered.wait, 5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            release.set()
            assert await asyncio.to_thread(finished.wait, 5)
        # Join the next real transaction; the canceled worker must release its lock.
        persisted = await cm.aupdate_imported_voice(ref, data["scope_id"], {}, expected_record_revision=0)
        assert persisted["overwrite_status"] == "completed"
        assert persisted["remote_revision"] == "3"
        assert persisted["_record_revision"] == 2
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts", "cosyvoice", "cosyvoice_intl"], indirect=True)
@pytest.mark.parametrize("checkpoint", ["remote", "commit"])
@pytest.mark.parametrize("initial_status", ["processing", "unknown"])
@pytest.mark.parametrize("observed,confirmed", [("completed", "completed"), ("failed", "failed"), ("completed", "pending")])
async def test_pending_winner_does_not_discard_terminal_observation(
    remote_record, checkpoint, initial_status, observed, confirmed, monkeypatch,
):
    cm, adapter, ref, data, storage = remote_record
    await cm.aupdate_imported_voice(ref, data["scope_id"], {"overwrite_status": initial_status})
    provider = adapter.resolve_runtime(cm).provider
    token = service.context_token(adapter.resolve_runtime(cm))
    pending_entered, terminal_entered = asyncio.Event(), asyncio.Event()
    release_pending, release_terminal = asyncio.Event(), asyncio.Event()
    client = httpx.AsyncClient
    monkeypatch.setattr(routes, "get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(routes.router)
    api = client(transport=httpx.ASGITransport(app=app), base_url="http://isolated.local")
    calls = 0

    async def upstream(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            pending_entered.set()
            await release_pending.wait()
            return response_for(provider, data, "pending", "2")
        if calls == 2:
            if checkpoint == "remote":
                terminal_entered.set()
                await release_terminal.wait()
            return response_for(provider, data, observed, "3")
        assert calls == 3, "Reconciliation must query again once without a mutation or unbounded retry"
        return response_for(provider, data, confirmed, "3")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **{**kwargs, "transport": httpx.MockTransport(upstream)},
    ))
    update = cm.aupdate_imported_voice
    paused = False

    async def delayed_commit(local_ref, scope, values, **kwargs):
        nonlocal paused
        if checkpoint == "commit" and values.get("overwrite_status") in {"completed", "failed"} and not paused:
            paused = True
            terminal_entered.set()
            await release_terminal.wait()
        return await update(local_ref, scope, values, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", delayed_commit)
    path = f"/api/characters/voices/{ref}/overwrite_status"
    late = None
    async with api:
        early = asyncio.create_task(api.get(path, params={"context_token": token}))
        try:
            await asyncio.wait_for(pending_entered.wait(), timeout=5)
            late = asyncio.create_task(api.get(path, params={"context_token": token}))
            await asyncio.wait_for(terminal_entered.wait(), timeout=5)
            release_pending.set()
            first = await asyncio.wait_for(early, timeout=5)
            assert first.status_code == 200
            assert first.json()["status"] == initial_status
            release_terminal.set()
            final = await asyncio.wait_for(late, timeout=5)
            assert final.status_code == 200
            assert calls == 3
            expected = initial_status if confirmed == "pending" else confirmed
            assert final.json()["status"] == expected
            assert final.json()["voice_data"]["remote_revision"] == revision_for(provider, "3")
            saved = await asyncio.to_thread(cm.get_imported_voice, ref, include_inactive=True)
            assert saved["overwrite_status"] == expected
            assert saved["remote_revision"] == revision_for(provider, "3")
            assert saved["overwrite_operation_id"] == "same-operation"
            if confirmed in {"completed", "failed"}:
                assert await cm.adelete_imported_voice(ref)
        finally:
            release_pending.set()
            release_terminal.set()
            tasks = [task for task in (early, late) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts"], indirect=True)
@pytest.mark.parametrize("change", ["busy", "operation", "delete", "config", "timeout", "cancel", "read-failure", "save-failure"])
async def test_reconciliation_retry_preserves_ownership_and_is_bounded(remote_record, change, monkeypatch):
    cm, adapter, ref, data, storage = remote_record
    token = service.context_token(adapter.resolve_runtime(cm))
    client = httpx.AsyncClient
    calls = 0
    mutation_at_retry = None
    original = cm.aupdate_imported_voice

    async def upstream(request):
        nonlocal calls, mutation_at_retry
        calls += 1
        assert calls <= 2
        if calls == 2:
            if change == "operation":
                await original(ref, data["scope_id"], {"overwrite_operation_id": "replacement"})
            elif change == "delete":
                await original(ref, data["scope_id"], {"overwrite_status": "completed"})
                assert await cm.adelete_imported_voice(ref)
            elif change == "config":
                cm.raw["ttsModelApiKey"] = "changed-key"
                cm.raw["assistApiKeyDoubaoTts"] = "changed-key"
            elif change == "timeout":
                raise httpx.ReadTimeout("controlled timeout", request=request)
            elif change == "cancel":
                raise asyncio.CancelledError()
            elif change == "read-failure":
                await asyncio.to_thread(storage.write_text, "{broken", encoding="utf-8")
            elif change == "save-failure":
                def reject_save(value):
                    raise OSError("controlled save failure")
                monkeypatch.setattr(cm, "save_voice_storage", reject_save)
            mutation_at_retry = await asyncio.to_thread(storage.read_bytes)
        return response_for("doubao_tts", data, "completed", "3")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **{**kwargs, "transport": httpx.MockTransport(upstream)},
    ))
    commits = 0

    async def compete(local_ref, scope, values, **kwargs):
        nonlocal commits
        commits += 1
        if commits == 1 or change == "busy":
            # A supported concurrent status commit wins, without changing operation identity.
            await original(local_ref, scope, {"remote_revision": "2"})
        return await original(local_ref, scope, values, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", compete)
    error_type = asyncio.CancelledError if change == "cancel" else service.VoiceManagementError
    with pytest.raises(error_type) as error:
        await service.refresh_overwrite_status(adapter, cm, ref, token=token)
    assert calls == 2
    if change in {"operation", "config", "delete"}:
        assert error.value.code == "CONTEXT_CHANGED"
    elif change == "busy":
        assert error.value.code == "OPERATION_IN_PROGRESS" and error.value.status_code == 409
    elif change == "timeout":
        assert error.value.code == "UPSTREAM_TIMEOUT" and error.value.status_code == 504
    elif change in {"read-failure", "save-failure"}:
        assert error.value.code == "STORAGE_ERROR"
    if mutation_at_retry is not None and change != "busy":
        assert await asyncio.to_thread(storage.read_bytes) == mutation_at_retry


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts"], indirect=True)
async def test_new_operation_between_attempts_is_not_adopted(remote_record, monkeypatch):
    cm, adapter, ref, data, storage = remote_record
    token = service.context_token(adapter.resolve_runtime(cm))
    client = httpx.AsyncClient
    calls = 0

    def upstream(request):
        nonlocal calls
        calls += 1
        assert calls == 1
        return response_for("doubao_tts", data, "completed", "3")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **{**kwargs, "transport": httpx.MockTransport(upstream)},
    ))
    original = cm.aupdate_imported_voice
    winner = None

    async def handoff_after_conflict(local_ref, scope, values, **kwargs):
        nonlocal winner
        await original(local_ref, scope, {"remote_revision": "2"})
        incumbent = await original(local_ref, scope, values, **kwargs)
        winner = await original(local_ref, scope, {"overwrite_operation_id": "replacement"})
        return incumbent

    monkeypatch.setattr(cm, "aupdate_imported_voice", handoff_after_conflict)
    with pytest.raises(service.VoiceManagementError) as error:
        await service.refresh_overwrite_status(adapter, cm, ref, token=token)
    assert error.value.code == "CONTEXT_CHANGED"
    persisted = await asyncio.to_thread(cm.get_imported_voice, ref, include_inactive=True)
    assert persisted["overwrite_operation_id"] == winner["overwrite_operation_id"] == "replacement"
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts"], indirect=True)
async def test_reconciliation_deadline_cancels_second_query(remote_record, monkeypatch):
    cm, adapter, ref, data, storage = remote_record
    token = service.context_token(adapter.resolve_runtime(cm))
    client = httpx.AsyncClient
    calls = 0
    canceled = False
    timeouts = []
    timeout = asyncio.timeout

    def capture_timeout(delay):
        assert 0 < delay < 35, "The entire reconciliation must fit the frontend request deadline"
        scope = timeout(delay)
        timeouts.append(scope)
        return scope

    monkeypatch.setattr(asyncio, "timeout", capture_timeout)

    async def upstream(request):
        nonlocal calls, canceled
        calls += 1
        if calls == 2:
            timeouts[0].reschedule(asyncio.get_running_loop().time())
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                canceled = True
                raise
        assert calls == 1
        return response_for("doubao_tts", data, "completed", "3")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **{**kwargs, "transport": httpx.MockTransport(upstream)},
    ))
    original = cm.aupdate_imported_voice
    winner = None

    async def conflict(local_ref, scope, values, **kwargs):
        nonlocal winner
        winner = await original(local_ref, scope, {"remote_revision": "2"})
        return await original(local_ref, scope, values, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", conflict)
    with pytest.raises(service.VoiceManagementError) as error:
        await service.refresh_overwrite_status(adapter, cm, ref, token=token)
    assert error.value.code == "UPSTREAM_TIMEOUT" and error.value.status_code == 504
    assert calls == 2 and canceled
    persisted = await asyncio.to_thread(cm.get_imported_voice, ref, include_inactive=True)
    assert persisted["_record_revision"] == winner["_record_revision"]
    assert persisted["overwrite_status"] == "processing"


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts"], indirect=True)
async def test_storage_timeout_is_not_reported_as_upstream_deadline(remote_record, monkeypatch):
    cm, adapter, ref, data, storage = remote_record
    token = service.context_token(adapter.resolve_runtime(cm))
    client = httpx.AsyncClient
    monkeypatch.setattr(routes, "get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(routes.router)
    api = client(transport=httpx.ASGITransport(app=app), base_url="http://isolated.local")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **{**kwargs, "transport": httpx.MockTransport(lambda request: response_for("doubao_tts", data, "completed", "3"))},
    ))

    def reject_save(value):
        raise TimeoutError("controlled filesystem timeout")

    monkeypatch.setattr(cm, "save_voice_storage", reject_save)
    before = await asyncio.to_thread(storage.read_bytes)
    async with api:
        response = await api.get(f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token})
    assert response.status_code == 500
    assert response.json()["code"] == "STORAGE_ERROR"
    assert await asyncio.to_thread(storage.read_bytes) == before
