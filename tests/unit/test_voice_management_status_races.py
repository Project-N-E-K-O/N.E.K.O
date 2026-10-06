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


@pytest.fixture
def remote_record(request, monkeypatch, doubao_import, tmp_path):
    provider = request.param
    if provider == "doubao_tts":
        cm, adapter, ref, data = doubao_import
    else:
        cm, adapter = MemoryVoiceManager(), CosyVoiceAdapter(provider)
        monkeypatch.setattr(cm, "get_cosyvoice_clone_runtime", lambda selected: {
            "api_key": "controlled-key", "base_url": "https://controlled.vendor/api/v1",
        }, raising=False)
        lookup = providers.get_adapter
        monkeypatch.setattr(providers, "get_adapter", lambda selected: adapter if selected == provider else lookup(selected))
        runtime = adapter.resolve_runtime(cm)
        ref, data, _ = cm.import_remote_voice(runtime.scope_id, provider, "remote-cosy", {
            **adapter.import_metadata(runtime), "remote_revision": "1", "can_overwrite": True,
        })
    storage = tmp_path / "voice_storage.json"
    atomic_write_json(storage, cm.storage)
    monkeypatch.setattr(cm, "load_voice_storage", lambda: json.loads(storage.read_text(encoding="utf-8")))
    monkeypatch.setattr(cm, "save_voice_storage", lambda value: atomic_write_json(storage, value))
    cm.update_imported_voice(ref, data["scope_id"], {
        "overwrite_operation_id": "same-operation", "overwrite_status": "processing",
        "overwrite_previous_revision": "1",
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
        "status": state, "gmt_modified": revision,
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
            assert late.json() == current.json()
            assert late.json()["status"] == expected
            assert late.json()["voice_data"]["remote_revision"] == "3"
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
        error_type = (
            service.VoiceManagementError if change == "config" else OSError if change == "save-failure"
            else asyncio.CancelledError if change == "cancel" else ValueError
        )
        with pytest.raises(error_type) as error:
            await asyncio.wait_for(pending, timeout=5)
        if change == "config":
            assert error.value.code == "CONTEXT_CHANGED"
        elif change in {"operation", "delete"}:
            assert error.value.args == ("VOICE_CONTEXT_CHANGED",)
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
        assert result["voice_data"]["remote_revision"] == "3"
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
