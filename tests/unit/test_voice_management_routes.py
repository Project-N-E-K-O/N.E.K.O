"""ASGI acceptance tests for explicit import, updates and credential settings."""
import io
import json
import wave

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient

from tests.unit.test_voice_management_service import (
    fixture as management_fixture, payload,
)
from tests.unit.test_core_config_secret_redaction import (
    config_manager, core_config_router, _write_core_config,
)
from utils.voice_management.types import ManagementCapabilities, VoiceManagementError


def _wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        audio.writeframes(b"\x10\x00\x20\x00" * 4410)
    return buffer.getvalue()


@pytest_asyncio.fixture
async def client(management_fixture, monkeypatch):
    from main_routers.characters_router import voice_management as routes
    from utils.tts import provider_registry
    cm, adapter = management_fixture
    monkeypatch.setattr(routes, "get_config_manager", lambda: cm)
    monkeypatch.setattr(provider_registry, "get_voice_management", lambda provider: adapter if provider == "minimax" else None)
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as session:
        yield session, cm, adapter


async def _import(client):
    session, cm, adapter = client
    response = await session.post("/api/characters/voices/import", json=payload(adapter, cm))
    assert response.status_code == 200
    return response.json()["voice_id"]


def _assert_private(response, cm):
    assert cm.key not in response.text and cm.management_secret not in response.text
    assert "scope_id" not in response.text and "storage_key" not in response.text
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.asyncio
async def test_context_list_import_and_refresh_through_asgi(client):
    session, cm, adapter = client
    context = await session.get("/api/characters/remote_voices/context", params={"provider": "minimax"})
    assert context.status_code == 200 and context.json()["configured"]
    _assert_private(context, cm)
    params = {"provider": "minimax", "context_token": context.json()["context_token"]}
    listed = await session.get("/api/characters/remote_voices", params=params)
    assert listed.status_code == 200 and not listed.json()["voices"][0]["imported"]
    assert not cm.storage
    ref = await _import(client)
    listed = await session.get("/api/characters/remote_voices", params=params)
    assert listed.json()["voices"][0]["local_ref"] == ref
    status = await session.get(f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": params["context_token"]})
    assert status.status_code == 200 and status.json()["status"] == "completed"
    _assert_private(listed, cm)
    _assert_private(status, cm)
    assert adapter.mutations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("params,status", [
    ({}, 422), ({"provider": "unknown"}, 400), ({"provider": "x" * 65}, 400),
])
async def test_context_rejects_invalid_provider(client, params, status):
    session, _, _ = client
    response = await session.get("/api/characters/remote_voices/context", params=params)
    assert response.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["processing", "unknown"])
async def test_pending_voice_delete_returns_conflict_and_retains_binding(client, monkeypatch, state):
    from main_routers.characters_router import voice_registry
    session, cm, adapter = client
    ref = await _import(client)
    cm.update_imported_voice(ref, adapter.resolve_runtime(cm).scope_id, {"overwrite_status": state})
    cm.characters = {"猫娘": {"Test": {"voice_id": ref}}}
    before = json.dumps(cm.storage, sort_keys=True)
    monkeypatch.setattr(voice_registry, "get_config_manager", lambda: cm)
    monkeypatch.setattr(voice_registry, "get_session_manager", lambda: pytest.fail("A denied delete must not clean bindings or sessions"))
    response = await session.delete(f"/api/characters/voices/{ref}")
    assert response.status_code == 409 and response.json()["code"] == "OPERATION_IN_PROGRESS"
    assert "no-store" in response.headers["cache-control"]
    assert json.dumps(cm.storage, sort_keys=True) == before
    assert cm.characters["猫娘"]["Test"]["voice_id"] == ref


@pytest.mark.asyncio
async def test_record_context_rejects_missing_record_and_provider_mismatch(client, monkeypatch):
    from dataclasses import replace
    session, cm, adapter = client
    response = await session.get("/api/characters/remote_voices/context", params={"provider": "minimax", "local_ref": "voice_" + "f" * 32})
    assert response.status_code == 404 and response.json()["code"] == "VOICE_NOT_FOUND"
    ref = await _import(client)
    original = adapter.resolve_runtime
    monkeypatch.setattr(adapter, "resolve_runtime", lambda cm, voice_data=None: replace(original(cm), provider="elevenlabs"))
    response = await session.get("/api/characters/remote_voices/context", params={"provider": "minimax", "local_ref": ref})
    assert response.status_code == 409 and response.json()["code"] == "CONTEXT_CHANGED"


@pytest.mark.asyncio
@pytest.mark.parametrize("patch,code", [
    ({"context_token": "stale"}, "CONTEXT_CHANGED"),
    ({"context_token": "中文"}, "CONTEXT_CHANGED"),
    ({"cursor": "x" * 2049}, "INVALID_CURSOR"),
    ({"query": "x" * 201}, "INVALID_CURSOR"),
])
async def test_list_validates_context_and_query_before_remote_call(client, patch, code):
    session, cm, adapter = client
    response = await session.get("/api/characters/remote_voices", params={
        "provider": "minimax", "context_token": payload(adapter, cm)["context_token"], **patch,
    })
    assert response.json()["code"] == code
    assert not cm.storage
    _assert_private(response, cm)


@pytest.mark.asyncio
@pytest.mark.parametrize("data,code", [
    ([], "INVALID_JSON"), (None, "INVALID_JSON"),
    ({"provider": []}, "IMPORT_UNSUPPORTED"),
    ({"provider": "minimax", "api_key": "attacker"}, "INVALID_METADATA"),
])
async def test_manual_import_schema_rejected_without_writing(client, data, code):
    session, cm, _ = client
    response = await session.post("/api/characters/voices/import", content=json.dumps(data), headers={"content-type": "application/json"})
    assert response.status_code == 400 and response.json()["code"] == code
    assert not cm.storage


@pytest.mark.asyncio
async def test_manual_import_rejects_malformed_json(client):
    session, cm, _ = client
    response = await session.post("/api/characters/voices/import", content="{broken", headers={"content-type": "application/json"})
    assert response.status_code == 400 and response.json()["code"] == "INVALID_JSON"
    assert not cm.storage


@pytest.mark.asyncio
async def test_public_voice_library_hides_scope_and_private_endpoint(client, monkeypatch):
    from main_routers.characters_router import voice_preview

    session, cm, adapter = client
    ref = await _import(client)
    private_endpoint = "https://user:password@vendor.example/api?api_key=query-secret"
    scope = adapter.resolve_runtime(cm).scope_id
    cm.update_imported_voice(ref, scope, {"minimax_base_url": private_endpoint})
    original_listing = cm.get_voices_for_current_api

    def mixed_legacy_listing(for_listing=False):
        return {
            **original_listing(for_listing=for_listing),
            "legacy-string": "legacy-value", "legacy-null": None,
        }

    async def core_config():
        return {}

    async def characters():
        return cm.characters

    monkeypatch.setattr(cm, "aget_core_config", core_config, raising=False)
    monkeypatch.setattr(cm, "aload_characters", characters, raising=False)
    monkeypatch.setattr(cm, "get_voices_for_current_api", mixed_legacy_listing)
    monkeypatch.setattr(voice_preview, "get_config_manager", lambda: cm)
    monkeypatch.setattr(voice_preview.tts_provider_registry, "selected_provider_key", lambda *args: None)
    monkeypatch.setattr(voice_preview, "get_active_realtime_native_provider_for_ui", lambda manager: None)
    response = await session.get("/api/characters/voices")
    assert response.status_code == 200
    record = response.json()["voices"][ref]
    assert response.json()["voices"]["legacy-string"] == "legacy-value"
    assert response.json()["voices"]["legacy-null"] is None
    assert record["local_ref"] == ref and record["availability"] == "available"
    assert not {"scope_id", "storage_key", "minimax_base_url"} & set(record)
    for secret in (cm.key, cm.management_secret, "query-secret", "password", scope):
        assert secret not in response.text
    assert cm.get_imported_voice(ref)["minimax_base_url"] == private_endpoint


@pytest.mark.asyncio
async def test_list_error_is_safe_and_manual_permission_fallback_works(client):
    session, cm, adapter = client
    adapter.list_error = VoiceManagementError("PERMISSION_DENIED", 403)
    adapter.detail_error = VoiceManagementError("PERMISSION_DENIED", 403)
    response = await session.get("/api/characters/remote_voices", params={"provider": "minimax", "context_token": payload(adapter, cm)["context_token"]})
    assert response.status_code == 403 and not cm.storage
    response = await session.post("/api/characters/voices/import", json=payload(adapter, cm))
    assert response.status_code == 200 and response.json()["verification"] == "unverified"
    _assert_private(response, cm)


@pytest.mark.asyncio
async def test_multipart_overwrite_normalizes_audio_and_retains_identity(client):
    session, cm, adapter = client
    ref = await _import(client)
    response = await session.post(f"/api/characters/voices/{ref}/overwrite", data={"context_token": payload(adapter, cm)["context_token"]}, files={"audio": ("reference.wav", _wav(), "audio/wav")})
    assert response.status_code == 200 and response.json()["voice_id"] == ref
    assert response.json()["status"] == "completed"
    key, remote_id, normalized, filename = adapter.mutations[0]
    assert key == cm.key and remote_id == "remote-original" and filename.endswith(".wav")
    with wave.open(io.BytesIO(normalized), "rb") as audio:
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
        assert audio.getframerate() <= 44100
    _assert_private(response, cm)


@pytest.mark.asyncio
@pytest.mark.parametrize("audio", [b"", b"invalid audio"])
async def test_multipart_invalid_audio_is_rejected_before_mutation(client, audio):
    session, cm, adapter = client
    ref = await _import(client)
    response = await session.post(f"/api/characters/voices/{ref}/overwrite", data={"context_token": payload(adapter, cm)["context_token"]}, files={"audio": ("reference.wav", audio, "audio/wav")})
    assert response.status_code == 400 and response.json()["code"] == "INVALID_AUDIO"
    assert adapter.mutations == []


@pytest.mark.asyncio
async def test_multipart_size_limit_is_enforced(client, monkeypatch):
    from main_routers.characters_router import voice_management as routes
    monkeypatch.setattr(routes, "MAX_UPLOAD_SIZE", 10)
    session, cm, adapter = client
    ref = await _import(client)
    response = await session.post(f"/api/characters/voices/{ref}/overwrite", data={"context_token": payload(adapter, cm)["context_token"]}, files={"audio": ("reference.wav", b"x" * 11, "audio/wav")})
    assert response.status_code == 413 and response.json()["code"] == "AUDIO_TOO_LARGE"
    assert adapter.mutations == []


@pytest.mark.asyncio
async def test_multipart_overwrite_permission_rejection_is_not_retried(client):
    session, cm, adapter = client
    ref = await _import(client)

    async def denied():
        raise VoiceManagementError("PERMISSION_DENIED", 403)

    adapter.on_mutation = denied
    response = await session.post(f"/api/characters/voices/{ref}/overwrite", data={"context_token": payload(adapter, cm)["context_token"]}, files={"audio": ("reference.wav", _wav(), "audio/wav")})
    assert response.status_code == 403 and response.json()["code"] == "PERMISSION_DENIED"
    assert len(adapter.mutations) == 1
    assert cm.get_imported_voice(ref)["overwrite_status"] == "failed"
    _assert_private(response, cm)


@pytest.mark.asyncio
async def test_overwrite_and_status_missing_record_return_404(client):
    session, cm, adapter = client
    ref = "voice_" + "a" * 32
    token = payload(adapter, cm)["context_token"]
    update = await session.post(f"/api/characters/voices/{ref}/overwrite", data={"context_token": token}, files={"audio": ("reference.wav", _wav(), "audio/wav")})
    status = await session.get(f"/api/characters/voices/{ref}/overwrite_status", params={"context_token": token})
    assert update.status_code == status.status_code == 404
    assert status.json()["code"] == "VOICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_overwrite_rejects_unsupported_capability(client):
    session, cm, adapter = client
    ref = await _import(client)
    adapter.capabilities = ManagementCapabilities(True, True, False)
    response = await session.post(f"/api/characters/voices/{ref}/overwrite", data={"context_token": payload(adapter, cm)["context_token"]}, files={"audio": ("reference.wav", _wav(), "audio/wav")})
    assert response.status_code == 400 and response.json()["code"] == "OVERWRITE_UNSUPPORTED"
    assert not adapter.mutations


@pytest.mark.asyncio
@pytest.mark.parametrize("exception,code,status", [
    (ValueError("VOICE_CONTEXT_CHANGED"), "CONTEXT_CHANGED", 409),
    (ValueError("VOICE_STORAGE_INVALID"), "STORAGE_ERROR", 500),
    (OSError("private-synthesis-key"), "STORAGE_ERROR", 500),
    (RuntimeError("private-synthesis-key"), "LOCAL_OPERATION_FAILED", 500),
])
async def test_route_error_mapping_does_not_leak_exception_details(client, monkeypatch, exception, code, status):
    session, cm, _ = client

    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(cm, "import_remote_voice", fail)
    response = await session.post("/api/characters/voices/import", json=payload(client[2], cm))
    assert response.status_code == status and response.json()["code"] == code
    _assert_private(response, cm)


@pytest.mark.asyncio
async def test_management_settings_get_save_masks_preserves_and_clears(config_manager, core_config_router):
    from main_routers import config_router
    stored = {
        "coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "core-key",
        "doubaoVoiceManagementAccessKey": "private-access-key",
        "doubaoVoiceManagementSecretKey": "private-secret-key",
        "doubaoVoiceManagementAppId": "original-app", "doubaoVoiceManagementProjectName": "original-project",
    }
    _write_core_config(config_manager, stored)
    app = FastAPI()
    app.include_router(config_router.router)
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as session:
        response = await session.get("/api/config/core_api")
        assert response.status_code == 200 and response.json()["success"]
        for field in core_config_router.CORE_CONFIG_VOICE_MANAGEMENT_SECRET_FIELDS:
            assert response.json()[field] == core_config_router.CORE_CONFIG_SECRET_SENTINEL
            assert stored[field] not in response.text
            assert response.json()[field + "_display"] != stored[field]
        saved = await session.post("/api/config/core_api", json={
            "coreApi": "qwen", "coreApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
            "doubaoVoiceManagementAccessKey": response.json()["doubaoVoiceManagementAccessKey"],
            "doubaoVoiceManagementSecretKey": response.json()["doubaoVoiceManagementSecretKey"],
            "doubaoVoiceManagementAppId": " new-app ", "doubaoVoiceManagementProjectName": " new-project ",
        })
        assert saved.json()["success"]
        config = config_manager.load_json_config("core_config.json")
        assert config["doubaoVoiceManagementAccessKey"] == stored["doubaoVoiceManagementAccessKey"]
        assert config["doubaoVoiceManagementSecretKey"] == stored["doubaoVoiceManagementSecretKey"]
        assert config["doubaoVoiceManagementAppId"] == "new-app"
        assert config["doubaoVoiceManagementProjectName"] == "new-project"
        cleared = await session.post("/api/config/core_api", json={
            "coreApi": "qwen", "coreApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
            "doubaoVoiceManagementAccessKey": "", "doubaoVoiceManagementSecretKey": "",
        })
        assert cleared.json()["success"]
        response = await session.get("/api/config/core_api")
        for field in core_config_router.CORE_CONFIG_VOICE_MANAGEMENT_SECRET_FIELDS:
            assert response.json()[field] == response.json()[field + "_display"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("doubaoVoiceManagementAccessKey", None), ("doubaoVoiceManagementSecretKey", []),
    ("doubaoVoiceManagementAccessKey", "x" * 2049), ("doubaoVoiceManagementAppId", 123),
    ("doubaoVoiceManagementProjectName", "x" * 201),
])
async def test_management_settings_reject_types_and_lengths_without_saving(config_manager, core_config_router, field, value):
    from main_routers import config_router
    _write_core_config(config_manager, {"coreApi": "qwen", "coreApiKey": "original-key", "doubaoVoiceManagementAccessKey": "original-access"})
    before = config_manager.load_json_config("core_config.json")
    app = FastAPI()
    app.include_router(config_router.router)
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as session:
        response = await session.post("/api/config/core_api", json={
            "coreApi": "qwen", "coreApiKey": core_config_router.CORE_CONFIG_SECRET_SENTINEL,
            field: value,
        })
    assert response.json() == {"success": False, "error": "INVALID_MANAGEMENT_CONFIG"}
    assert config_manager.load_json_config("core_config.json") == before
