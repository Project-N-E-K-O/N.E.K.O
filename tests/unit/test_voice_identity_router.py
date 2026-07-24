from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_logic.voice_identity.enrollment_service import (
    EnrollmentAudioError,
    EnrollmentStage,
    EnrollmentStatus,
    VerificationResult,
)
from main_logic.voice_identity.profile_store import ProfileLoadState
from main_routers.cookies_login_router import verify_local_access
import main_routers.voice_identity_router as voice_identity_router


def _status(
    *,
    session_id: str | None = None,
    stage: EnrollmentStage = EnrollmentStage.IDLE,
    profile_state: ProfileLoadState = ProfileLoadState.EMPTY,
    profile_revision: int | None = None,
) -> EnrollmentStatus:
    return EnrollmentStatus(
        session_id=session_id,
        stage=stage,
        fixed_completed=0,
        verification_completed=0,
        verification_attempts=0,
        profile_state=profile_state,
        profile_revision=profile_revision,
        filter_enabled=False,
        enrollment_active=session_id is not None,
    )


class _FakeService:
    def __init__(self) -> None:
        self.current = _status()
        self.calls: list[tuple] = []
        self.segment_error: Exception | None = None

    async def status(self) -> EnrollmentStatus:
        self.calls.append(("status",))
        return self.current

    async def start(self) -> EnrollmentStatus:
        self.calls.append(("start",))
        self.current = _status(
            session_id="session-a",
            stage=EnrollmentStage.FIXED_1,
        )
        return self.current

    async def submit_fixed(self, session_id: str, pcm16: bytes) -> EnrollmentStatus:
        self.calls.append(("segment", session_id, pcm16))
        if self.segment_error is not None:
            raise self.segment_error
        self.current = replace(
            self.current,
            stage=EnrollmentStage.FIXED_2,
            fixed_completed=1,
        )
        return self.current

    async def verify(
        self,
        session_id: str,
        pcm16: bytes,
    ) -> VerificationResult:
        self.calls.append(("verify", session_id, pcm16))
        return VerificationResult(True, self.current)

    async def commit(self, session_id: str) -> EnrollmentStatus:
        self.calls.append(("commit", session_id))
        self.current = _status(
            profile_state=ProfileLoadState.READY,
            profile_revision=3,
        )
        return self.current

    async def cancel(self, session_id: str) -> EnrollmentStatus:
        self.calls.append(("cancel", session_id))
        self.current = _status()
        return self.current

    async def delete_profile(self) -> EnrollmentStatus:
        self.calls.append(("delete",))
        self.current = _status()
        return self.current

    async def set_filter_enabled(self, enabled: bool) -> EnrollmentStatus:
        self.calls.append(("filter", enabled))
        self.current = replace(self.current, filter_enabled=enabled)
        return self.current


@pytest.fixture
def service(monkeypatch) -> _FakeService:
    fake = _FakeService()
    voice_identity_router.set_voice_identity_service_for_testing(fake)
    monkeypatch.setattr(
        voice_identity_router,
        "_validate_local_mutation_request",
        lambda _request: None,
    )
    yield fake
    voice_identity_router.set_voice_identity_service_for_testing(None)


@pytest.fixture
def client(service) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[verify_local_access] = lambda: None
    app.include_router(voice_identity_router.router)
    with TestClient(app) as test_client:
        yield test_client


def test_status_is_ui_safe_and_does_not_expose_embedding_or_similarity(
    client: TestClient,
    service: _FakeService,
) -> None:
    service.current = _status(
        profile_state=ProfileLoadState.READY,
        profile_revision=8,
    )

    response = client.get("/api/voice-identity/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"] == {
        "state": "ready",
        "revision": 8,
        "available": True,
    }
    assert payload["filter"]["can_enable"] is True
    assert "embedding" not in response.text
    assert "similarity" not in response.text


def test_enrollment_routes_accept_bounded_raw_pcm_and_keep_session_explicit(
    client: TestClient,
    service: _FakeService,
) -> None:
    started = client.post("/api/voice-identity/enrollment/start")
    session_id = started.json()["enrollment"]["session_id"]
    pcm = b"\x01\x00" * 24_000
    headers = {
        "X-Voice-Identity-Enrollment": session_id,
        "Content-Type": "audio/L16;rate=16000;channels=1",
    }

    segment = client.post(
        "/api/voice-identity/enrollment/segment",
        content=pcm,
        headers=headers,
    )
    verified = client.post(
        "/api/voice-identity/enrollment/verify",
        content=pcm,
        headers=headers,
    )
    committed = client.post(
        "/api/voice-identity/enrollment/commit",
        headers={"X-Voice-Identity-Enrollment": session_id},
    )

    assert segment.status_code == 200
    assert verified.status_code == 200
    assert verified.json()["verification"]["passed"] is True
    assert committed.status_code == 200
    assert ("segment", session_id, pcm) in service.calls
    assert ("verify", session_id, pcm) in service.calls
    assert ("commit", session_id) in service.calls


def test_audio_body_is_rejected_before_service_when_too_large(
    client: TestClient,
    service: _FakeService,
) -> None:
    started = client.post("/api/voice-identity/enrollment/start")
    session_id = started.json()["enrollment"]["session_id"]

    response = client.post(
        "/api/voice-identity/enrollment/segment",
        content=b"\x00\x00" * (16_000 * 8 + 1),
        headers={"X-Voice-Identity-Enrollment": session_id},
    )

    assert response.status_code == 413
    assert not any(call[0] == "segment" for call in service.calls)


def test_service_errors_are_mapped_without_internal_details(
    client: TestClient,
    service: _FakeService,
) -> None:
    started = client.post("/api/voice-identity/enrollment/start")
    session_id = started.json()["enrollment"]["session_id"]
    service.segment_error = EnrollmentAudioError("internal audio detail")

    response = client.post(
        "/api/voice-identity/enrollment/segment",
        content=b"\x00\x00" * 24_000,
        headers={"X-Voice-Identity-Enrollment": session_id},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_enrollment_audio"
    assert "internal audio detail" not in response.text


def test_filter_requires_boolean_payload(client: TestClient) -> None:
    invalid = client.put("/api/voice-identity/filter", json={"enabled": "yes"})
    enabled = client.put("/api/voice-identity/filter", json={"enabled": True})

    assert invalid.status_code == 422
    assert enabled.status_code == 200
    assert enabled.json()["filter"]["enabled"] is True


def test_cancel_delete_and_disable_filter_routes(
    client: TestClient,
    service: _FakeService,
) -> None:
    started = client.post("/api/voice-identity/enrollment/start")
    session_id = started.json()["enrollment"]["session_id"]

    cancelled = client.post(
        "/api/voice-identity/enrollment/cancel",
        headers={"X-Voice-Identity-Enrollment": session_id},
    )
    deleted = client.delete("/api/voice-identity/profile")
    disabled = client.put(
        "/api/voice-identity/filter",
        json={"enabled": False},
    )

    assert cancelled.status_code == 200
    assert deleted.status_code == 200
    assert disabled.status_code == 200
    assert ("cancel", session_id) in service.calls
    assert ("delete",) in service.calls
    assert ("filter", False) in service.calls


def test_missing_session_and_unconfigured_service_are_ui_safe(
    client: TestClient,
) -> None:
    missing = client.post("/api/voice-identity/enrollment/commit")
    voice_identity_router.set_voice_identity_service_for_testing(None)
    unavailable = client.get("/api/voice-identity/status")

    assert missing.status_code == 404
    assert missing.json()["error"] == "enrollment_not_found"
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "voice_identity_unavailable"


async def test_runtime_callbacks_fan_out_to_all_character_managers(
    monkeypatch,
) -> None:
    first = SimpleNamespace(
        prepare_voice_identity_enrollment=AsyncMock(),
        activate_registered_speaker_profile=AsyncMock(),
    )
    second = SimpleNamespace(
        prepare_voice_identity_enrollment=AsyncMock(),
        activate_registered_speaker_profile=AsyncMock(),
    )
    monkeypatch.setattr(
        voice_identity_router,
        "get_session_manager",
        lambda: {"a": first, "b": second},
    )

    await voice_identity_router._prepare_enrollment_input("session-a")
    await voice_identity_router._activate_profile(None)

    first.prepare_voice_identity_enrollment.assert_awaited_once_with("session-a")
    second.prepare_voice_identity_enrollment.assert_awaited_once_with("session-a")
    first.activate_registered_speaker_profile.assert_awaited_once_with(None)
    second.activate_registered_speaker_profile.assert_awaited_once_with(None)


async def test_configure_restore_and_close_service(monkeypatch, tmp_path) -> None:
    fake_service = SimpleNamespace(
        restore=AsyncMock(return_value=_status()),
        close=AsyncMock(),
    )
    store_factory = MagicMock(return_value=object())
    service_factory = MagicMock(return_value=fake_service)
    monkeypatch.setattr(
        voice_identity_router,
        "get_config_manager",
        lambda: SimpleNamespace(config_dir=tmp_path),
    )
    monkeypatch.setattr(voice_identity_router, "ProfileStore", store_factory)
    monkeypatch.setattr(
        voice_identity_router,
        "OwnerEnrollmentService",
        service_factory,
    )
    voice_identity_router.set_voice_identity_service_for_testing(None)

    configured = voice_identity_router.configure_voice_identity_service()
    restored = await voice_identity_router.restore_voice_identity_profile()
    await voice_identity_router.close_voice_identity_service()

    assert configured is fake_service
    assert restored.stage is EnrollmentStage.IDLE
    assert store_factory.call_args.args[0] == tmp_path / "voice_identity.profile"
    fake_service.restore.assert_awaited_once_with()
    fake_service.close.assert_awaited_once_with()
