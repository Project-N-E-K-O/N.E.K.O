"""TSE routes preserve local mutation checks and stream uploads to the service."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from main_logic.voice_identity_service.service import VoiceIdentityServiceError
import main_routers.system_router._shared as system_shared
import main_routers.voice_identity_router as routes


BASE = "/api/voice-identity"
TOKEN = "tse-route-test-token"
HEADERS = {"Origin": "http://testserver", "X-CSRF-Token": TOKEN}
STATUS = {"tse": {"model": {"state": "ready"}, "enabled": False}}


@pytest.fixture
def service(monkeypatch):
    status = SimpleNamespace(as_dict=lambda: STATUS)
    value = SimpleNamespace(download_tse_model=AsyncMock(return_value=status),
                            import_tse_model=AsyncMock(return_value=status),
                            update_tse=AsyncMock(return_value=status))
    monkeypatch.setattr(routes, "_service", lambda: value)
    monkeypatch.setattr(system_shared, "AUTOSTART_CSRF_TOKEN", TOKEN)
    return value


@pytest.fixture
def client(service):
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, base_url="http://testserver") as result:
        yield result


@pytest.mark.parametrize("path", ["/models/tse/download", "/models/tse/import", "/tse"])
@pytest.mark.parametrize("headers", [{}, {"Origin": "http://evil.example", "X-CSRF-Token": TOKEN},
                                    {"Origin": "http://testserver", "X-CSRF-Token": "wrong"}])
def test_tse_mutations_require_existing_local_origin_and_csrf(client, service, path, headers):
    response = client.post(BASE + path, headers=headers, json={"enabled": True, "profile_id": "owner"})
    assert response.status_code == 403
    service.download_tse_model.assert_not_called()
    service.import_tse_model.assert_not_called()
    service.update_tse.assert_not_called()


def test_download_only_selects_application_resource(client, service):
    response = client.post(BASE + "/models/tse/download?url=https://evil.example&path=C:/bad",
                           headers=HEADERS, json={"url": "file:///bad", "revision": "bad"})
    assert response.status_code == 200
    assert response.json() == STATUS
    service.download_tse_model.assert_awaited_once_with()


@pytest.mark.parametrize("content_type", ["text/html", "application/json", "multipart/form-data", ""])
def test_import_requires_raw_zip_content_type(client, service, content_type):
    response = client.post(BASE + "/models/tse/import", content=b"PK archive",
                           headers={**HEADERS, "Content-Type": content_type})
    assert response.status_code == 415
    service.import_tse_model.assert_not_called()


@pytest.mark.asyncio
async def test_import_passes_lazy_request_chunks_without_materializing_body(monkeypatch, service):
    parts = [b"a" * 17, b"b" * 29, b"c" * 5]
    reads, consumed = [], []

    async def receive():
        index = len(reads)
        assert len(consumed) == index
        reads.append(index)
        return {"type": "http.request", "body": parts[index], "more_body": index < len(parts) - 1}

    async def take_stream(stream):
        assert not reads
        async for part in stream:
            if part:
                consumed.append(part)
        return SimpleNamespace(as_dict=lambda: STATUS)

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [
        (b"content-type", b"application/zip")], "query_string": b""}, receive)
    monkeypatch.setattr(routes, "_validate_mutation", lambda *_: None)
    service.import_tse_model.side_effect = take_stream
    result = await routes.import_voice_identity_tse(request)
    assert result == STATUS
    assert consumed == parts
    assert not hasattr(request, "_body")


@pytest.mark.parametrize("enabled", [True, False])
def test_enable_setting_is_bound_to_current_profile(client, service, enabled):
    result = client.post(BASE + "/tse", headers=HEADERS, json={"enabled": enabled, "profile_id": "owner-a"})
    assert result.status_code == 200
    service.update_tse.assert_awaited_once_with(enabled, "owner-a")


@pytest.mark.parametrize("value", ["true", 1, None, [], {}])
def test_enable_requires_real_boolean(client, service, value):
    result = client.post(BASE + "/tse", headers=HEADERS, json={"enabled": value, "profile_id": "owner"})
    assert result.status_code == 422
    service.update_tse.assert_not_called()


@pytest.mark.parametrize("profile", [None, "", 42])
def test_enable_requires_profile_identifier(client, service, profile):
    result = client.post(BASE + "/tse", headers=HEADERS, json={"enabled": True, "profile_id": profile})
    assert result.status_code == 400
    service.update_tse.assert_not_called()


def test_setting_accepts_existing_json_csrf_contract(client, service):
    result = client.post(BASE + "/tse", headers={"Origin": "http://testserver"},
                         json={"_csrf_token": TOKEN, "enabled": True, "profile_id": "owner"})
    assert result.status_code == 200
    service.update_tse.assert_awaited_once_with(True, "owner")


def test_setting_json_is_bounded(client, service):
    result = client.post(BASE + "/tse", headers=HEADERS, content=b"x" * 1025)
    assert result.status_code == 413
    service.update_tse.assert_not_called()


def test_disable_after_profile_deletion_binds_explicit_no_profile(client, service):
    result = client.post(BASE + "/tse", headers=HEADERS, json={"enabled": False, "profile_id": None})
    assert result.status_code == 200
    service.update_tse.assert_awaited_once_with(False, None)


def test_disable_cannot_omit_profile_generation(client, service):
    result = client.post(BASE + "/tse", headers=HEADERS, json={"enabled": False})
    assert result.status_code == 400
    service.update_tse.assert_not_called()


@pytest.mark.parametrize("code,status", [("enrollment_active", 409), ("tse_assets_busy", 409),
                                        ("tse_integrity_error", 422), ("tse_source_unconfigured", 503)])
def test_route_preserves_stable_service_error_code(client, service, code, status):
    service.download_tse_model.side_effect = VoiceIdentityServiceError(code)
    result = client.post(BASE + "/models/tse/download", headers=HEADERS)
    assert result.status_code == status
    assert result.json() == {"error_code": code}


@pytest.mark.parametrize("path", ["/models/tse/download", "/models/tse/import", "/tse"])
def test_tse_routes_report_unavailable_service(client, monkeypatch, path):
    monkeypatch.setattr(routes, "_service", lambda: None)
    headers = {**HEADERS, "Content-Type": "application/zip"} if path.endswith("import") else HEADERS
    response = client.post(BASE + path, headers=headers, json={"enabled": True, "profile_id": "owner"})
    assert response.status_code == 503
    assert response.json() == {"error_code": "runtime_degraded"}
