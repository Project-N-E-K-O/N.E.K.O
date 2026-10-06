"""Reject lagging remote evidence using actual adapters, routes and disk commits."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from main_routers.characters_router import voice_management as routes
from tests.unit.test_voice_management_routes import _wav
from tests.unit.test_voice_management_status_races import remote_record, response_for, revision_for  # noqa: F401
from tests.unit.test_voice_management_storage import doubao_import  # noqa: F401
from utils.voice_management import providers, service

PROVIDERS = ["doubao_tts", "cosyvoice", "cosyvoice_intl"]


def isolated_api(cm, monkeypatch):
    monkeypatch.setattr(routes, "get_config_manager", lambda: cm)
    app = FastAPI()
    app.include_router(routes.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated.local")


def upstream_transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        **{**kwargs, "transport": httpx.MockTransport(handler)},
    ))


@pytest.mark.parametrize("current,previous,expected", [
    ("v10", "v9", 1), ("v9", "v10", -1), ("v1", "1", 0), ("v01", "v1", 0),
    ("V10", "V9", 1), ("V9", "V10", -1), ("V1", "v1", 0),
    ("v1", "V1", 0), ("V01", "1", 0), ("V-1", "V1", None),
    ("0", "v1", -1), (None, "v1", None), ("v1", None, None), ("new", "old", None),
    ("v1 ", "v1", None), ("v-1", "v1", None), ("v1.0", "v1", None),
    ("v" + "1" * 100, "v1", None), (1, "v1", None),
])
def test_doubao_revision_is_training_count(current, previous, expected):
    assert providers.get_adapter("doubao_tts").compare_revisions(current, previous) == expected


@pytest.mark.parametrize("provider", ["cosyvoice", "cosyvoice_intl"])
@pytest.mark.parametrize("current,previous,expected", [
    ("2026-10-06 10:00:10", "2026-10-06 10:00:09", 1),
    ("2026-10-06 10:00:09", "2026-10-06 10:00:10", -1),
    ("2026-10-06T10:00:10", "2026-10-06 10:00:10", 0),
    ("2026-10-06 10:00:10.001", "2026-10-06 10:00:10", 1),
    ("2026-10-06T10:00:10+08:00", "2026-10-06T02:00:10Z", 0),
    ("2026-10-06T10:00:10+08:00", "2026-10-06T02:00:10.001Z", -1),
    ("2026-10-06T10:00:10Z", "2026-10-06 10:00:10", None),
    ("2026-02-30 10:00:10", "2026-10-06 10:00:10", None),
    ("2026-10-06", "2026-10-06 10:00:10", None),
    ("3", "2", None), ("new", "old", None), (None, None, None),
])
def test_cosy_revision_is_comparable_modification_time(provider, current, previous, expected):
    assert providers.get_adapter(provider).compare_revisions(current, previous) == expected


@pytest.mark.parametrize("provider", ["minimax", "minimax_intl", "elevenlabs", "glm_tts"])
def test_import_only_provider_does_not_invent_revision_order(provider):
    assert providers.get_adapter(provider).compare_revisions("2", "1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", PROVIDERS, indirect=True)
@pytest.mark.parametrize("initial", ["processing", "unknown"])
@pytest.mark.parametrize("kind", ["stale-ready", "pending", "failed"])
@pytest.mark.parametrize("revision", ["1", "invalid", None])
async def test_stale_or_unordered_refresh_preserves_disk_and_pending_owner(
    remote_record, initial, kind, revision, monkeypatch,
):
    cm, adapter, ref, data, storage = remote_record
    provider = adapter.resolve_runtime(cm).provider
    await cm.aupdate_imported_voice(ref, data["scope_id"], {
        "remote_revision": revision_for(provider, "2"),
        "overwrite_previous_revision": revision_for(provider, "2"), "overwrite_status": initial,
    })
    token = service.context_token(adapter.resolve_runtime(cm))
    api = isolated_api(cm, monkeypatch)
    calls = []

    def upstream(request):
        calls.append(request)
        return response_for(provider, data, kind if len(calls) == 1 else "completed", revision if len(calls) == 1 else "3")

    upstream_transport(monkeypatch, upstream)
    before = await asyncio.to_thread(storage.read_bytes)
    async with api:
        path = f"/api/characters/voices/{ref}/overwrite_status"
        reply = await api.get(path, params={"context_token": token})
        assert reply.status_code == 409 and reply.json()["code"] == "UPDATE_OUTCOME_UNKNOWN"
        assert await asyncio.to_thread(storage.read_bytes) == before
        with pytest.raises(ValueError, match="VOICE_OPERATION_IN_PROGRESS"):
            await cm.adelete_imported_voice(ref)
        # A later forward revision recovers through the same route, without mutation.
        recovered = await api.get(path, params={"context_token": token})
        assert recovered.status_code == 200 and recovered.json()["status"] == "completed"
        assert recovered.json()["voice_data"]["remote_revision"] == revision_for(provider, "3")
        assert recovered.json()["voice_data"]["overwrite_operation_id"] == "same-operation"
        assert await cm.adelete_imported_voice(ref)
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", PROVIDERS, indirect=True)
@pytest.mark.parametrize("initial", ["processing", "unknown"])
@pytest.mark.parametrize("kind", ["stale-ready", "pending", "failed"])
@pytest.mark.parametrize("older", ["1", "2"])
async def test_retry_cannot_forget_first_query_revision_floor(remote_record, initial, kind, older, monkeypatch):
    cm, adapter, ref, data, storage = remote_record
    provider = adapter.resolve_runtime(cm).provider
    await cm.aupdate_imported_voice(ref, data["scope_id"], {"overwrite_status": initial})
    token = service.context_token(adapter.resolve_runtime(cm))
    api = isolated_api(cm, monkeypatch)
    calls = 0

    def upstream(request):
        nonlocal calls
        calls += 1
        assert calls <= 2
        return response_for(provider, data, "completed" if calls == 1 else kind, "3" if calls == 1 else older)

    upstream_transport(monkeypatch, upstream)
    original = cm.aupdate_imported_voice
    first_commit, release = asyncio.Event(), asyncio.Event()
    commits = 0

    async def pause(local_ref, scope, values, **kwargs):
        nonlocal commits
        commits += 1
        first_commit.set()
        await release.wait()
        return await original(local_ref, scope, values, **kwargs)

    monkeypatch.setattr(cm, "aupdate_imported_voice", pause)
    pending = None
    async with api:
        try:
            pending = asyncio.create_task(api.get(
                f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token},
            ))
            await asyncio.wait_for(first_commit.wait(), 5)
            await original(ref, data["scope_id"], {"remote_revision": revision_for(provider, "2")})
            winner = await asyncio.to_thread(storage.read_bytes)
            release.set()
            reply = await asyncio.wait_for(pending, 5)
            assert reply.status_code == 409 and reply.json()["code"] == "UPDATE_OUTCOME_UNKNOWN"
            assert calls == 2 and commits == 1
            assert await asyncio.to_thread(storage.read_bytes) == winner
            with pytest.raises(ValueError, match="VOICE_OPERATION_IN_PROGRESS"):
                await cm.adelete_imported_voice(ref)
        finally:
            release.set()
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", PROVIDERS, indirect=True)
@pytest.mark.parametrize("initial", ["completed", "failed"])
async def test_stale_refresh_does_not_replace_confirmed_record(remote_record, initial, monkeypatch):
    cm, adapter, ref, data, storage = remote_record
    provider = adapter.resolve_runtime(cm).provider
    await cm.aupdate_imported_voice(ref, data["scope_id"], {
        "overwrite_status": initial, "remote_revision": revision_for(provider, "3"),
    })
    token = service.context_token(adapter.resolve_runtime(cm))
    upstream_transport(monkeypatch, lambda request: response_for(provider, data, "stale-ready", "2"))
    before = await asyncio.to_thread(storage.read_bytes)
    with pytest.raises(service.VoiceManagementError, match="UPDATE_OUTCOME_UNKNOWN"):
        await service.refresh_overwrite_status(adapter, cm, ref, token=token)
    assert await asyncio.to_thread(storage.read_bytes) == before
    assert await cm.adelete_imported_voice(ref)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", PROVIDERS, indirect=True)
@pytest.mark.parametrize("followup,expected", [("1", "unknown"), ("invalid", "unknown"), (None, "unknown"), ("2", "processing"), ("3", "completed")])
async def test_actual_overwrite_response_requires_forward_evidence(remote_record, followup, expected, monkeypatch):
    from utils import voice_clone

    cm, adapter, ref, data, storage = remote_record
    provider = adapter.resolve_runtime(cm).provider
    baseline = revision_for(provider, "2")
    await cm.aupdate_imported_voice(ref, data["scope_id"], {
        "overwrite_status": "completed", "remote_revision": baseline, "can_overwrite": True,
    })
    token = service.context_token(adapter.resolve_runtime(cm))
    api = isolated_api(cm, monkeypatch)
    queries, mutations = 0, []

    class Uploader:
        def __init__(self, *args):
            pass

        async def upload_file(self, *args):
            return "https://controlled.upload/reference.wav"

    monkeypatch.setattr(voice_clone, "QwenVoiceCloneClient", Uploader)

    def upstream(request):
        nonlocal queries
        body = json.loads(request.content)
        action = body.get("input", {}).get("action")
        if request.url.path.endswith("/voice_clone") or action == "update_voice":
            mutations.append(request)
            return httpx.Response(200, json={"code": 0, "speaker_id": data["remote_voice_id"]} if provider == "doubao_tts" else {"output": {}})
        queries += 1
        return response_for(provider, data, "stale-ready", "2" if queries == 1 else followup if queries == 2 else "3")

    upstream_transport(monkeypatch, upstream)
    result = await service.overwrite_remote_voice(adapter, cm, ref, token=token, audio=b"reference", filename="reference.wav")
    assert result["status"] == expected
    assert len(mutations) == 1 and queries == 2
    saved = json.loads(await asyncio.to_thread(storage.read_text, encoding="utf-8"))
    record = next(bucket[ref] for bucket in saved.values() if ref in bucket)
    assert record["remote_revision"] == (revision_for(provider, "3") if expected == "completed" else baseline)
    assert record["overwrite_operation_id"] != "same-operation"
    assert record["overwrite_previous_revision"] == baseline
    async with api:
        if expected != "completed":
            with pytest.raises(ValueError, match="VOICE_OPERATION_IN_PROGRESS"):
                await cm.adelete_imported_voice(ref)
            recovered = await api.get(f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token})
            assert recovered.status_code == 200 and recovered.json()["status"] == "completed"
            assert recovered.json()["voice_data"]["overwrite_operation_id"] == record["overwrite_operation_id"]
            assert len(mutations) == 1 and queries == 3
        assert await cm.adelete_imported_voice(ref)


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", PROVIDERS, indirect=True)
async def test_lagging_preflight_never_submits_an_overwrite(remote_record, monkeypatch):
    from utils import voice_clone

    cm, adapter, ref, data, storage = remote_record
    provider = adapter.resolve_runtime(cm).provider
    await cm.aupdate_imported_voice(ref, data["scope_id"], {
        "overwrite_status": "completed", "remote_revision": revision_for(provider, "2"), "can_overwrite": True,
    })
    token = service.context_token(adapter.resolve_runtime(cm))
    before = await asyncio.to_thread(storage.read_bytes)
    calls = []

    class Uploader:
        def __init__(self, *args):
            pass

        async def upload_file(self, *args):
            return "https://controlled.upload/reference.wav"

    monkeypatch.setattr(voice_clone, "QwenVoiceCloneClient", Uploader)

    def upstream(request):
        calls.append(request)
        return response_for(provider, data, "stale-ready", "1")

    upstream_transport(monkeypatch, upstream)
    with pytest.raises(service.VoiceManagementError, match="UPDATE_OUTCOME_UNKNOWN"):
        await service.overwrite_remote_voice(adapter, cm, ref, token=token, audio=b"reference", filename="reference.wav")
    assert len(calls) == 1
    assert await asyncio.to_thread(storage.read_bytes) == before
    lock = service._OVERWRITE_LOCKS.get(ref)
    assert lock is None or not lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", PROVIDERS, indirect=True)
@pytest.mark.parametrize("followup,expected", [("1", "processing"), ("invalid", "processing"), (None, "processing"), ("2", "processing"), ("3", "ready")])
async def test_adapter_acknowledgment_alone_cannot_credit_old_ready(remote_record, followup, expected, monkeypatch):
    from utils import voice_clone

    cm, adapter, _, data, _ = remote_record
    runtime = adapter.resolve_runtime(cm)
    provider = runtime.provider
    queries, mutations, guarded = 0, [], []

    class Uploader:
        def __init__(self, *args):
            pass

        async def upload_file(self, *args):
            return "https://controlled.upload/reference.wav"

    monkeypatch.setattr(voice_clone, "QwenVoiceCloneClient", Uploader)

    async def before_mutation(current):
        assert current.metadata["remote_revision"] == revision_for(provider, "2")
        guarded.append(current.voice_id)

    def upstream(request):
        nonlocal queries
        action = json.loads(request.content).get("input", {}).get("action")
        if request.url.path.endswith("/voice_clone") or action == "update_voice":
            assert guarded == [data["remote_voice_id"]]
            mutations.append(request)
            return httpx.Response(200, json={"code": 0, "speaker_id": data["remote_voice_id"]} if provider == "doubao_tts" else {"output": {}})
        queries += 1
        return response_for(provider, data, "stale-ready", "2" if queries == 1 else followup)

    upstream_transport(monkeypatch, upstream)
    result = await adapter.overwrite(runtime, data["remote_voice_id"], audio=b"reference", filename="reference.wav", before_mutation=before_mutation)
    assert result.status == expected and result.voice_id == data["remote_voice_id"]
    assert queries == 2 and len(mutations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", PROVIDERS, indirect=True)
@pytest.mark.parametrize("revision", [None, "invalid"])
async def test_unordered_voice_import_remains_available_without_overwrite(remote_record, revision, monkeypatch):
    cm, adapter, ref, data, _ = remote_record
    runtime = adapter.resolve_runtime(cm)
    await cm.aupdate_imported_voice(ref, data["scope_id"], {"overwrite_status": "completed"})
    assert await cm.adelete_imported_voice(ref)
    api = isolated_api(cm, monkeypatch)
    calls = []

    def upstream(request):
        calls.append(request)
        return response_for(runtime.provider, data, "stale-ready", revision)

    upstream_transport(monkeypatch, upstream)
    token = service.context_token(runtime)
    async with api:
        reply = await api.post("/api/characters/voices/import", json={
            "provider": runtime.provider, "remote_voice_id": data["remote_voice_id"], "context_token": token,
        })
    assert reply.status_code == 200 and reply.json()["verification"] == "verified"
    assert reply.json()["voice_id"] != ref
    assert reply.json()["voice_data"]["can_overwrite"] is False
    imported = cm.get_imported_voice(reply.json()["voice_id"])
    assert imported["availability"] == "available" and imported["remote_voice_id"] == data["remote_voice_id"]
    with pytest.raises(service.VoiceManagementError, match="OVERWRITE_UNSUPPORTED"):
        await service.overwrite_remote_voice(adapter, cm, imported["local_ref"], token=token, audio=b"reference", filename="reference.wav")
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_record", ["doubao_tts"], indirect=True)
@pytest.mark.parametrize("project", [False, True], ids=["app-api", "project-api"])
@pytest.mark.parametrize("before,same,newer", [
    ("V9", "v09", "V10"), ("v9", "V09", "v10"), ("9", "V9", "10"),
])
async def test_doubao_version_casing_preserves_import_overwrite_and_recovery(
    remote_record, project, before, same, newer, monkeypatch,
):
    cm, adapter, old_ref, data, storage = remote_record
    await cm.aupdate_imported_voice(old_ref, data["scope_id"], {"overwrite_status": "completed"})
    assert await cm.adelete_imported_voice(old_ref)
    if project:
        cm.raw["doubaoVoiceManagementProjectName"] = "controlled-project"
    runtime = adapter.resolve_runtime(cm)
    api = isolated_api(cm, monkeypatch)
    token = service.context_token(runtime)
    observation = before
    mutations = []
    requests = []

    def upstream(request):
        nonlocal observation
        requests.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("/voice_clone"):
            assert body["speaker_id"] == data["remote_voice_id"]
            mutations.append(request)
            observation = same
            return httpx.Response(200, json={"code": 0, "speaker_id": data["remote_voice_id"]})
        assert request.url.params["Action"] == "BatchListMegaTTSTrainStatus"
        assert request.url.params["Version"] == ("2025-05-21" if project else "2023-11-07")
        assert body.get("ProjectName") == ("controlled-project" if project else None)
        assert body.get("AppID") == (None if project else runtime.settings["app_id"])
        return httpx.Response(200, json={"Result": {"Statuses": [{
            "SpeakerID": data["remote_voice_id"], "State": "Success", "Version": observation,
            "AvailableTrainingTimes": 5,
        }]}})

    upstream_transport(monkeypatch, upstream)
    async with api:
        params = {"provider": "doubao_tts", "context_token": token}
        untouched = await asyncio.to_thread(storage.read_bytes)
        listed = await api.get("/api/characters/remote_voices", params=params)
        assert listed.status_code == 200
        assert listed.json()["voices"][0]["can_overwrite"] is True
        assert listed.json()["voices"][0]["metadata"]["remote_revision"] == before
        assert await asyncio.to_thread(storage.read_bytes) == untouched and not mutations
        payload = {**params, "remote_voice_id": data["remote_voice_id"]}
        imported = await api.post("/api/characters/voices/import", json=payload)
        assert imported.status_code == 200 and imported.json()["voice_data"]["can_overwrite"] is True
        ref = imported.json()["voice_id"]
        duplicate = await api.post("/api/characters/voices/import", json=payload)
        assert duplicate.status_code == 200 and duplicate.json()["voice_id"] == ref
        updated = await api.post(
            f"/api/characters/voices/{ref}/overwrite", data={"context_token": token},
            files={"audio": ("reference.wav", _wav(), "audio/wav")},
        )
        assert updated.status_code == 200 and updated.json()["status"] == "processing"
        assert len(mutations) == 1
        owner = updated.json()["voice_data"]["overwrite_operation_id"]
        with pytest.raises(ValueError, match="VOICE_OPERATION_IN_PROGRESS"):
            await cm.adelete_imported_voice(ref)
        observation = newer
        recovered = await api.get(f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token})
        assert recovered.status_code == 200 and recovered.json()["status"] == "completed"
        assert recovered.json()["voice_data"]["overwrite_operation_id"] == owner
        assert recovered.json()["voice_data"]["remote_revision"] == newer
        assert recovered.json()["voice_data"]["can_overwrite"] is True
        persisted = await asyncio.to_thread(storage.read_bytes)
        observation = before
        stale = await api.get(f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token})
        assert stale.status_code == 409 and stale.json()["code"] == "UPDATE_OUTCOME_UNKNOWN"
        assert await asyncio.to_thread(storage.read_bytes) == persisted
        assert len(mutations) == 1 and len(requests) == 8
        assert await cm.adelete_imported_voice(ref)
