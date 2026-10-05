"""Exercise actual ASGI resource controls, local/CSRF and isolation ownership."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import main_routers.system_router._shared as system_router_shared
import main_routers.voice_identity_router as voice_identity_router
from main_logic.voice_identity_service.resource_manager import VoiceResourceManager
from main_logic.voice_identity_service.service import VoiceIdentityServiceError
from main_logic.voice_input import preview

ROOT = "/api/voice-identity"
HEADERS = {"Origin": "http://testserver", "X-CSRF-Token": "resource-test-token"}
PCM_HEADERS = {"Content-Type": "audio/pcm;format=pcm_s16le;rate=48000;channels=1",
               "X-Voice-Audio-Contract": "owner-campplus-desktop-v1"}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    registry = preview.VoicePreviewIsolationRegistry()
    monkeypatch.setattr(preview, "preview_isolation_registry", registry)
    monkeypatch.setattr(system_router_shared, "AUTOSTART_CSRF_TOKEN", HEADERS["X-CSRF-Token"])
    manager = VoiceResourceManager(lambda: False, cache_root=tmp_path)
    service = SimpleNamespace(
        resources=manager.resources,
        start_resource_operation=MagicMock(return_value={"operation_id": "op-test", "state": "pending"}),
        reserve_resource_operation=manager.reserve,
        start_reserved_resource_operation=manager.start_reserved,
        resource_operation=manager.operation,
        cancel_resource_operation=manager.cancel,
        set_wake_word_preference=AsyncMock(return_value={"wake_enabled": True}),
        begin_trial_isolation=lambda request_id: registry.begin_inactive(request_id, noise_reduction_enabled=False),
        check_trial_audio=AsyncMock(return_value={"accepted": True, "reason": None, "diagnostics": {"active_seconds": 2}}),
        submit_enrollment_segment=AsyncMock(side_effect=VoiceIdentityServiceError("volume_too_low", diagnostics={"active_seconds": 1., "rms": .1})),
        start_enrollment=AsyncMock(),
        status=MagicMock(return_value=SimpleNamespace(as_dict=lambda: {"enrollment": None})),
    )
    monkeypatch.setattr(voice_identity_router, "get_voice_identity_service_for_router", lambda: service)
    app = FastAPI()
    app.include_router(voice_identity_router.router)
    with TestClient(app) as client:
        client.headers.update(HEADERS)
        yield client, service, registry, manager


@pytest.mark.parametrize("path", ["resources/prepare", "resources/wake-word/download", "resources/wake-word/preference", "audio/check", "audio/check/isolation", "audio/check/isolation/release", "resources/operations/id/cancel", "resources/operations", "resources/operations/id/start"])
def test_new_mutations_require_csrf_before_service_or_audio_read(setup, path):
    client, service, registry, _ = setup
    client.headers.pop("X-CSRF-Token")
    response = client.post(f"{ROOT}/{path}", content=b"malformed")
    assert response.status_code == 403
    service.start_resource_operation.assert_not_called()
    service.check_trial_audio.assert_not_called()
    assert registry._ticket is None


@pytest.mark.parametrize("path", ["resources/prepare", "resources/wake-word/download"])
def test_resource_download_never_accepts_client_url_or_path(setup, path):
    client, service, _, _ = setup
    response = client.post(f"{ROOT}/{path}", json={"url": "https://attacker.invalid", "path": "../../outside"})
    assert response.status_code == 400
    service.start_resource_operation.assert_not_called()


def test_snapshot_does_not_prepare_or_start_worker(setup, monkeypatch):
    client, service, registry, manager = setup
    async def unexpected(*args, **kwargs):
        raise AssertionError("GET must never load a model")
    monkeypatch.setattr(manager, "_execute", unexpected)
    response = client.get(f"{ROOT}/resources")
    assert response.status_code == 200
    assert set(response.json()["resources"]) == {"campp", "silero", "noise_reduction", "wake_model", "wake_runtime"}
    assert response.json()["audio_contract"]["noise_reduction_enabled"] is False
    assert manager._current is None
    assert registry._ticket is None
    service.start_resource_operation.assert_not_called()


def test_valid_prepare_returns_operation_not_false_ready(setup):
    client, service, _, _ = setup
    response = client.post(f"{ROOT}/resources/prepare")
    assert response.status_code == 202
    assert response.json() == {"operation_id": "op-test", "state": "pending"}
    service.start_resource_operation.assert_called_once_with("prepare")


def test_reserve_cancel_and_late_start_use_real_manager_without_starting_worker(setup):
    client, _, _, manager = setup
    reserved = client.post(f"{ROOT}/resources/operations", json={"kind": "download"})
    assert reserved.status_code == 201
    operation_id = reserved.json()["operation_id"]
    assert reserved.json()["state"] == "reserved" and manager._current is None
    cancelled = client.post(f"{ROOT}/resources/operations/{operation_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    late = client.post(f"{ROOT}/resources/operations/{operation_id}/start")
    assert late.status_code == 202 and late.json()["state"] == "cancelled"
    assert manager._current is None
    assert client.post(f"{ROOT}/resources/operations/unknown/start").status_code == 400


@pytest.mark.asyncio
async def test_lost_actual_start_receipt_can_cancel_and_retire_the_matching_worker(monkeypatch, tmp_path):
    import asyncio
    import httpx
    from main_logic.voice_identity_service import resource_manager as rm
    entered, retired = asyncio.Event(), asyncio.Event()
    calls = []
    async def worker(*args, **kwargs):
        calls.append(args); entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            retired.set()
    monkeypatch.setattr(rm, "_run_worker", worker)
    monkeypatch.setattr(system_router_shared, "AUTOSTART_CSRF_TOKEN", HEADERS["X-CSRF-Token"])
    manager = VoiceResourceManager(lambda: False, cache_root=tmp_path)
    service = SimpleNamespace(reserve_resource_operation=manager.reserve, start_reserved_resource_operation=manager.start_reserved,
                              cancel_resource_operation=manager.cancel, resource_operation=manager.operation)
    monkeypatch.setattr(voice_identity_router, "get_voice_identity_service_for_router", lambda: service)
    app = FastAPI(); app.include_router(voice_identity_router.router)
    actual = httpx.ASGITransport(app=app)
    class LostStartReceipt(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            response = await actual.handle_async_request(request)
            if request.url.path.endswith("/start"):
                await response.aread()
                if response.json()["state"] == "pending":
                    await response.aclose()
                    raise httpx.ReadError("accepted start receipt lost")
            return response
    try:
        async with httpx.AsyncClient(transport=LostStartReceipt(), base_url="http://testserver", headers=HEADERS) as client:
            operation_id = (await client.post(f"{ROOT}/resources/operations", json={"kind": "prepare"})).json()["operation_id"]
            with pytest.raises(httpx.ReadError, match="receipt lost"):
                await client.post(f"{ROOT}/resources/operations/{operation_id}/start")
            await asyncio.wait_for(entered.wait(), 2)
            cancelled = await client.post(f"{ROOT}/resources/operations/{operation_id}/cancel")
            assert cancelled.json()["state"] == "cancelled" and retired.is_set()
            assert (await client.post(f"{ROOT}/resources/operations/{operation_id}/start")).json()["state"] == "cancelled"
            assert len(calls) == 1
    finally:
        await manager.close()


@pytest.mark.parametrize("payload", [{"kind": "download", "url": "https://attacker.invalid"}, {"kind": "prepare", "path": "../../outside"}, {"kind": []}, {}, {"kind": "install"}])
def test_reservation_only_accepts_fixed_kind_and_start_rejects_configuration(setup, payload):
    client, _, _, manager = setup
    assert client.post(f"{ROOT}/resources/operations", json=payload).status_code == 400
    assert not manager._operations
    operation_id = client.post(f"{ROOT}/resources/operations", json={"kind": "prepare"}).json()["operation_id"]
    assert client.post(f"{ROOT}/resources/operations/{operation_id}/start", json={"path": "outside"}).status_code == 400
    assert manager._current is None


def test_trial_requires_server_isolation_and_releases_consumed_ticket(setup):
    client, service, registry, _ = setup
    rejected = client.post(f"{ROOT}/audio/check", content=b"\0" * 288000, headers=PCM_HEADERS)
    assert rejected.status_code == 409
    assert rejected.json()["error_code"] == "preview_invalid"
    service.check_trial_audio.assert_not_called()
    token = client.post(f"{ROOT}/audio/check/isolation", json={"request_id": "trial-a"}).json()["token"]
    response = client.post(f"{ROOT}/audio/check", content=b"\0" * 288000,
                           headers=dict(PCM_HEADERS, **{"X-Voice-Input-Check": token}))
    assert response.status_code == 200
    service.check_trial_audio.assert_awaited_once_with(b"\0" * 288000, noise_reduction_enabled=False)
    assert registry._ticket is None
    repeated = client.post(f"{ROOT}/audio/check", content=b"\0" * 288000,
                           headers=dict(PCM_HEADERS, **{"X-Voice-Input-Check": token}))
    assert repeated.status_code == 409
    assert service.check_trial_audio.await_count == 1


def test_oversized_trial_releases_reservation_without_processing(setup):
    client, service, registry, _ = setup
    token = client.post(f"{ROOT}/audio/check/isolation", json={"request_id": "trial-b"}).json()["token"]
    response = client.post(f"{ROOT}/audio/check", content=b"\0" * 288002,
                           headers=dict(PCM_HEADERS, **{"X-Voice-Input-Check": token}))
    assert response.status_code == 413
    service.check_trial_audio.assert_not_called()
    assert registry._ticket is None


def test_release_old_token_cannot_release_successor(setup):
    client, _, registry, _ = setup
    first = registry.begin_inactive("old")
    registry.release(first)
    second = registry.begin_inactive("new")
    response = client.post(f"{ROOT}/audio/check/isolation/release", json={"token": first.token})
    assert response.json() == {"released": False}
    assert registry._ticket is second


def test_expired_computation_does_not_publish_trial_success_or_release_successor(setup):
    client, service, registry, _ = setup
    time = [100.]
    registry.now = lambda: time[0]
    first = registry.begin_inactive("old", noise_reduction_enabled=False)
    async def delayed(_audio, **_kwargs):
        time[0] += 31
        registry.begin_inactive("successor", noise_reduction_enabled=False)
        await asyncio.sleep(0)
        return {"accepted": True}
    service.check_trial_audio.side_effect = delayed
    response = client.post(f"{ROOT}/audio/check", content=b"\0" * 288000,
                           headers=dict(PCM_HEADERS, **{"X-Voice-Input-Check": first.token}))
    assert response.status_code == 409
    assert response.json()["error_code"] == "preview_expired"
    assert registry._ticket.request_id == "successor"


def test_formal_volume_code_keeps_safe_diagnostics(setup):
    client, _, _, _ = setup
    response = client.put(f"{ROOT}/enrollment/segment", content=b"\0" * 288000,
                          headers=dict(PCM_HEADERS, **{"X-Voice-Identity-Segment": "1"}))
    assert response.status_code == 422
    assert response.json() == {"error_code": "volume_too_low", "diagnostics": {"active_seconds": 1., "rms": .1}}


def test_formal_start_preserves_legacy_and_passes_optional_trial_contract(setup):
    from main_logic.voice_identity_service.audio_contract import desktop_audio_contract_snapshot
    client, service, _, _ = setup
    legacy = client.post(f"{ROOT}/enrollment/start")
    assert legacy.status_code == 200
    service.start_enrollment.assert_awaited_once_with()
    service.start_enrollment.reset_mock()
    current = client.post(f"{ROOT}/enrollment/start", json={"preview_audio_contract": {
        "contract_id": "owner-campplus-desktop-v1", "revision": 1, "noise_reduction_enabled": False}})
    assert current.status_code == 200
    service.start_enrollment.assert_awaited_once_with(expected_audio_contract=desktop_audio_contract_snapshot(noise_reduction_enabled=False))


@pytest.mark.parametrize("contract", [None, {"revision": True, "contract_id": "owner-campplus-desktop-v1", "noise_reduction_enabled": False},
                                     {"revision": 1, "contract_id": "other", "noise_reduction_enabled": False},
                                     {"revision": 1, "contract_id": "owner-campplus-desktop-v1", "noise_reduction_enabled": "false"}])
def test_invalid_trial_contract_cannot_start_formal_enrollment(setup, contract):
    client, service, _, _ = setup
    response = client.post(f"{ROOT}/enrollment/start", json={"preview_audio_contract": contract})
    assert response.status_code == 400
    service.start_enrollment.assert_not_called()


def test_repair_guide_route_is_fixed_public_document_not_arbitrary_file(setup):
    client, service, _, _ = setup
    client.headers.pop("X-CSRF-Token")
    response = client.get(f"{ROOT}/resources/repair-guide", params={"path": "config/api_keys.json"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "scripts/prepare_speaker_model.py" in response.text
    assert "scripts/prepare_voice_turn_assets.py" in response.text
    assert "scripts/provision_wake_word_model.py" in response.text
    service.start_resource_operation.assert_not_called()
