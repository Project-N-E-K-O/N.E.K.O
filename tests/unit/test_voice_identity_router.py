from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_logic.voice_identity_service.registry import (
    VoiceIdentityServiceRegistryError,
)
from main_logic.voice_identity_service.service import VoiceIdentityServiceError
import main_routers.system_router._shared as system_router_shared
import main_routers.voice_identity_router as voice_identity_router


API_ROOT = "/api/voice-identity"
PCM_CONTENT_TYPE = "audio/pcm;format=pcm_s16le;rate=16000;channels=1"
MAX_PCM_BYTES = 16_000 * 4 * 2
MAX_FILTER_JSON_BYTES = 1024
AUTH_HEADERS = {
    "Origin": "http://testserver",
    "X-CSRF-Token": "voice-identity-test-token",
}
SAFE_STATUS = {
    "requested_enabled": True,
    "effective_enabled": True,
    "effective_reason": "ready",
    "has_profile": True,
    "enrollment": None,
    "profile_generation": "profile-a",
    "runtime_mode": "enforce",
}


class _Status:
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self._payload = copy.deepcopy(payload or SAFE_STATUS)

    def as_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._payload)


def _fake_service(payload: dict[str, object] | None = None) -> SimpleNamespace:
    status = _Status(payload)
    return SimpleNamespace(
        status=MagicMock(return_value=status),
        start_enrollment=AsyncMock(),
        complete_enrollment=AsyncMock(return_value=status),
        cancel_enrollment=AsyncMock(return_value=True),
        set_filter=AsyncMock(return_value=status),
        delete_profile=AsyncMock(return_value=status),
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    service: SimpleNamespace | None,
    *,
    authenticated: bool = True,
) -> TestClient:
    if service is None:

        def unavailable():
            raise VoiceIdentityServiceRegistryError("unavailable")

        monkeypatch.setattr(
            voice_identity_router,
            "get_voice_identity_service_for_router",
            unavailable,
        )
    else:
        monkeypatch.setattr(
            voice_identity_router,
            "get_voice_identity_service_for_router",
            lambda: service,
        )
    app = FastAPI()
    app.include_router(voice_identity_router.router)
    client = TestClient(app, base_url="http://testserver")
    if authenticated:
        client.headers.update(AUTH_HEADERS)
    return client


def _assert_private_values_absent(payload: object) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).lower()
    for private_name in ("pcm", "embedding", "similarity", "score"):
        assert private_name not in encoded


@pytest.fixture(autouse=True)
def _fixed_csrf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        system_router_shared,
        "AUTOSTART_CSRF_TOKEN",
        AUTH_HEADERS["X-CSRF-Token"],
    )


@pytest.mark.unit
def test_registry_unavailable_is_ui_safe_for_status_and_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, None)

    status = client.get(f"{API_ROOT}/status")
    start = client.post(f"{API_ROOT}/enrollment/start")

    assert status.status_code == 503
    assert start.status_code == 503
    assert status.json() == {"error_code": "runtime_degraded"}
    assert start.json() == {"error_code": "runtime_degraded"}


@pytest.mark.unit
def test_status_is_public_and_does_not_require_mutation_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service, authenticated=False)

    response = client.get(f"{API_ROOT}/status")

    assert response.status_code == 200
    assert response.json() == SAFE_STATUS
    _assert_private_values_absent(response.json())


@pytest.mark.unit
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "http://testserver"},
        {"X-CSRF-Token": AUTH_HEADERS["X-CSRF-Token"]},
        {
            "Origin": "https://attacker.invalid",
            "X-CSRF-Token": AUTH_HEADERS["X-CSRF-Token"],
        },
        {
            "Origin": "http://testserver",
            "X-CSRF-Token": "wrong-token",
        },
    ],
)
def test_mutations_require_matching_csrf_and_local_origin(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service, authenticated=False)

    response = client.post(f"{API_ROOT}/enrollment/start", headers=headers)

    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"
    service.start_enrollment.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "path", "request_kwargs"),
    [
        ("post", "/enrollment/start", {}),
        (
            "put",
            "/enrollment/profile",
            {"content": b"", "headers": {"Content-Type": PCM_CONTENT_TYPE}},
        ),
        ("post", "/enrollment/cancel", {}),
        ("put", "/filter", {"json": {"enabled": True}}),
        ("delete", "/profile", {}),
    ],
)
def test_every_mutation_route_is_csrf_guarded(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    request_kwargs: dict[str, object],
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service, authenticated=False)

    response = client.request(method, f"{API_ROOT}{path}", **request_kwargs)

    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"
    service.start_enrollment.assert_not_awaited()
    service.complete_enrollment.assert_not_awaited()
    service.cancel_enrollment.assert_not_awaited()
    service.set_filter.assert_not_awaited()
    service.delete_profile.assert_not_awaited()


@pytest.mark.unit
def test_start_returns_canonical_status_without_private_model_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        **SAFE_STATUS,
        "requested_enabled": False,
        "effective_enabled": False,
        "effective_reason": "enrollment_active",
        "has_profile": False,
        "enrollment": {
            "enrollment_id": "enrollment-1",
            "expires_at": 123.5,
        },
        "profile_generation": None,
    }
    service = _fake_service(payload)
    client = _client(monkeypatch, service)

    response = client.post(f"{API_ROOT}/enrollment/start")

    assert response.status_code == 200
    assert response.json() == payload
    service.start_enrollment.assert_awaited_once_with()
    _assert_private_values_absent(response.json())


@pytest.mark.unit
def test_binary_profile_upload_forwards_exact_headers_and_body_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service)
    pcm16 = bytes(MAX_PCM_BYTES)
    headers = {
        "Content-Type": PCM_CONTENT_TYPE,
        "X-Voice-Identity-Enrollment": "enrollment-1",
        "X-Voice-Identity-Profile": "profile-1",
    }

    first = client.put(f"{API_ROOT}/enrollment/profile", content=pcm16, headers=headers)
    second = client.put(
        f"{API_ROOT}/enrollment/profile", content=pcm16, headers=headers
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json() == SAFE_STATUS
    assert service.complete_enrollment.await_count == 2
    assert service.complete_enrollment.await_args_list[0].args == (
        "enrollment-1",
        "profile-1",
        pcm16,
    )
    assert service.complete_enrollment.await_args_list[1].args == (
        "enrollment-1",
        "profile-1",
        pcm16,
    )
    _assert_private_values_absent(first.json())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body_size", "content_type", "expected_status", "expected_code"),
    [
        (MAX_PCM_BYTES, "application/octet-stream", 415, "invalid_pcm"),
        (MAX_PCM_BYTES + 1, PCM_CONTENT_TYPE, 413, "audio_too_long"),
    ],
)
def test_profile_upload_rejects_wrong_type_and_more_than_four_seconds(
    monkeypatch: pytest.MonkeyPatch,
    body_size: int,
    content_type: str,
    expected_status: int,
    expected_code: str,
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service)

    response = client.put(
        f"{API_ROOT}/enrollment/profile",
        content=bytes(body_size),
        headers={
            "Content-Type": content_type,
            "X-Voice-Identity-Enrollment": "enrollment-1",
            "X-Voice-Identity-Profile": "profile-1",
        },
    )

    assert response.status_code == expected_status
    assert response.json() == {"error_code": expected_code}
    service.complete_enrollment.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chunked_profile_body_is_bounded_without_content_length() -> None:
    yielded_chunks = 0

    async def stream():
        nonlocal yielded_chunks
        for chunk in (bytes(MAX_PCM_BYTES), b"x", b"unreachable"):
            yielded_chunks += 1
            yield chunk

    request = SimpleNamespace(stream=stream)

    body = await voice_identity_router._read_bounded_body(request, MAX_PCM_BYTES)

    assert body is None
    assert yielded_chunks == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error_code", "expected_status"),
    [
        ("invalid_enrollment_id", 400),
        ("invalid_profile_id", 400),
        ("stale_enrollment", 409),
        ("invalid_pcm", 422),
        ("speech_too_short", 422),
        ("audio_too_long", 422),
        ("silence", 422),
        ("severe_clipping", 422),
        ("model_unavailable", 503),
    ],
)
def test_profile_upload_maps_stable_service_errors(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
    expected_status: int,
) -> None:
    service = _fake_service()
    service.complete_enrollment.side_effect = VoiceIdentityServiceError(error_code)
    client = _client(monkeypatch, service)

    response = client.put(
        f"{API_ROOT}/enrollment/profile",
        content=bytes(48_000),
        headers={
            "Content-Type": PCM_CONTENT_TYPE,
            "X-Voice-Identity-Enrollment": "enrollment-1",
            "X-Voice-Identity-Profile": "profile-1",
        },
    )

    assert response.status_code == expected_status
    assert response.json() == {"error_code": error_code}


@pytest.mark.unit
def test_cancel_forwards_enrollment_header_and_returns_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service)

    response = client.post(
        f"{API_ROOT}/enrollment/cancel",
        headers={"X-Voice-Identity-Enrollment": "enrollment-1"},
    )

    assert response.status_code == 200
    assert response.json() == SAFE_STATUS
    service.cancel_enrollment.assert_awaited_once_with("enrollment-1")
    _assert_private_values_absent(response.json())


@pytest.mark.unit
def test_cancel_without_enrollment_header_passes_stable_empty_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service)

    response = client.post(f"{API_ROOT}/enrollment/cancel")

    assert response.status_code == 200
    service.cancel_enrollment.assert_awaited_once_with("")


@pytest.mark.unit
def test_filter_requires_boolean_and_forwards_requested_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service)

    invalid = client.put(f"{API_ROOT}/filter", json={"enabled": 1})
    enabled = client.put(f"{API_ROOT}/filter", json={"enabled": True})

    assert invalid.status_code == 422
    assert invalid.json() == {"error_code": "invalid_enabled"}
    assert enabled.status_code == 200
    assert enabled.json() == SAFE_STATUS
    service.set_filter.assert_awaited_once_with(True)
    _assert_private_values_absent(enabled.json())


@pytest.mark.unit
@pytest.mark.parametrize(
    "request_kwargs",
    [
        {},
        {"content": "{"},
        {"json": []},
        {"json": None},
    ],
)
def test_filter_rejects_missing_malformed_and_non_object_json_consistently(
    monkeypatch: pytest.MonkeyPatch,
    request_kwargs: dict[str, object],
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service)

    response = client.put(f"{API_ROOT}/filter", **request_kwargs)

    assert response.status_code == 422
    assert response.json() == {"error_code": "invalid_enabled"}
    service.set_filter.assert_not_awaited()


@pytest.mark.unit
def test_filter_rejects_oversized_json_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service)

    response = client.put(
        f"{API_ROOT}/filter",
        content=b"{" + b" " * MAX_FILTER_JSON_BYTES,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {"error_code": "invalid_enabled"}
    service.set_filter.assert_not_awaited()


@pytest.mark.unit
def test_filter_accepts_json_body_at_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service)
    prefix = b'{"enabled":true,"pad":"'
    suffix = b'"}'
    padding = b"x" * (MAX_FILTER_JSON_BYTES - len(prefix) - len(suffix))

    response = client.put(
        f"{API_ROOT}/filter",
        content=prefix + padding + suffix,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    service.set_filter.assert_awaited_once_with(True)


@pytest.mark.unit
def test_filter_rejects_invalid_header_auth_before_reading_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _fake_service()
    bounded_reader = AsyncMock()
    monkeypatch.setattr(voice_identity_router, "_read_bounded_body", bounded_reader)
    client = _client(monkeypatch, service, authenticated=False)

    response = client.put(
        f"{API_ROOT}/filter",
        json={"enabled": True},
        headers={
            "Origin": AUTH_HEADERS["Origin"],
            "X-CSRF-Token": "invalid-token",
        },
    )

    assert response.status_code == 403
    bounded_reader.assert_not_awaited()
    service.set_filter.assert_not_awaited()


@pytest.mark.unit
def test_filter_preserves_bounded_body_csrf_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _fake_service()
    client = _client(monkeypatch, service, authenticated=False)

    response = client.put(
        f"{API_ROOT}/filter",
        json={
            "enabled": False,
            "_csrf_token": AUTH_HEADERS["X-CSRF-Token"],
        },
        headers={"Origin": AUTH_HEADERS["Origin"]},
    )

    assert response.status_code == 200
    service.set_filter.assert_awaited_once_with(False)


@pytest.mark.unit
def test_delete_profile_returns_canonical_disabled_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        **SAFE_STATUS,
        "requested_enabled": False,
        "effective_enabled": False,
        "effective_reason": "disabled",
        "has_profile": False,
        "profile_generation": None,
    }
    service = _fake_service(payload)
    client = _client(monkeypatch, service)

    response = client.delete(f"{API_ROOT}/profile")

    assert response.status_code == 200
    assert response.json() == payload
    service.delete_profile.assert_awaited_once_with()
    _assert_private_values_absent(response.json())
