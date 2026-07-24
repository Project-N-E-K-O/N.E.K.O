"""Local-only Owner voice identity enrollment API."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictBool

from main_logic.voice_identity.campplus import (
    CAMPPLUS_EMBEDDING_DIM,
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
    CampPlusEmbeddingModel,
)
from main_logic.voice_identity.enrollment_service import (
    EnrollmentActivationError,
    EnrollmentAudioError,
    EnrollmentBusyError,
    EnrollmentConfig,
    EnrollmentModelUnavailable,
    EnrollmentNotFoundError,
    EnrollmentPersistenceError,
    EnrollmentStage,
    EnrollmentStateError,
    EnrollmentStatus,
    OwnerEnrollmentService,
)
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.profile_store import (
    ProfileCompatibility,
    ProfileLoadState,
    ProfileStore,
)
from main_logic.voice_identity.registry import (
    get_voice_identity_profile_registry,
)
from main_routers.cookies_login_router import verify_local_access
from main_routers.shared_state import get_config_manager, get_session_manager
from main_routers.system_router import _validate_local_mutation_request


_MAX_PCM_BYTES = 16_000 * 2 * 8
_ENROLLMENT_HEADER = "X-Voice-Identity-Enrollment"
_PROFILE_FILENAME = "voice_identity.profile"

router = APIRouter(
    prefix="/api/voice-identity",
    tags=["voice-identity"],
    dependencies=[Depends(verify_local_access)],
)

_service: OwnerEnrollmentService | Any | None = None


class FilterUpdate(BaseModel):
    enabled: StrictBool


class _EnrollmentBodyTooLarge(EnrollmentAudioError):
    pass


def set_voice_identity_service_for_testing(service: Any | None) -> None:
    global _service
    _service = service


def configure_voice_identity_service() -> OwnerEnrollmentService:
    global _service
    if _service is not None:
        return _service
    config_manager = get_config_manager()
    store = ProfileStore(
        Path(config_manager.config_dir) / _PROFILE_FILENAME,
        compatibility=ProfileCompatibility(
            model_id=CAMPPLUS_MODEL_ID,
            model_revision=CAMPPLUS_MODEL_REVISION,
            embedding_dimension=CAMPPLUS_EMBEDDING_DIM,
        ),
    )
    _service = OwnerEnrollmentService(
        store=store,
        registry=get_voice_identity_profile_registry(),
        model_factory=CampPlusEmbeddingModel,
        prepare_input=_prepare_enrollment_input,
        activate_profile=_activate_profile,
        config=EnrollmentConfig(
            model_id=CAMPPLUS_MODEL_ID,
            model_revision=CAMPPLUS_MODEL_REVISION,
            embedding_dimension=CAMPPLUS_EMBEDDING_DIM,
        ),
    )
    return _service


async def restore_voice_identity_profile() -> EnrollmentStatus:
    return await configure_voice_identity_service().restore()


async def close_voice_identity_service() -> None:
    global _service
    service, _service = _service, None
    if service is not None:
        await service.close()
    get_voice_identity_profile_registry().close()


async def _prepare_enrollment_input(session_id: str) -> None:
    managers = list(get_session_manager().values())
    if not managers:
        return
    results = await asyncio.gather(
        *(
            manager.prepare_voice_identity_enrollment(session_id)
            for manager in managers
        ),
        return_exceptions=True,
    )
    if any(isinstance(result, BaseException) for result in results):
        raise RuntimeError("voice input could not enter enrollment mode")


async def _activate_profile(profile: SpeakerProfile | None) -> None:
    managers = list(get_session_manager().values())
    if not managers:
        return
    results = await asyncio.gather(
        *(
            manager.activate_registered_speaker_profile(profile)
            for manager in managers
        ),
        return_exceptions=True,
    )
    if any(isinstance(result, BaseException) for result in results):
        raise RuntimeError("speaker profile activation failed")


def _get_service() -> OwnerEnrollmentService | Any:
    if _service is None:
        raise RuntimeError("voice identity service is not configured")
    return _service


def _status_payload(status: EnrollmentStatus) -> dict[str, Any]:
    profile_available = (
        status.profile_state is ProfileLoadState.READY
        or status.profile_revision is not None
    )
    return {
        "enrollment": {
            "active": status.enrollment_active,
            "session_id": status.session_id,
            "stage": status.stage.value,
            "fixed_completed": status.fixed_completed,
            "verification_completed": status.verification_completed,
            "verification_attempts": status.verification_attempts,
        },
        "profile": {
            "state": status.profile_state.value,
            "revision": status.profile_revision,
            "available": profile_available,
        },
        "filter": {
            "enabled": status.filter_enabled,
            "can_enable": profile_available,
        },
        "audio": {
            "format": "pcm_s16le",
            "sample_rate_hz": 16_000,
            "channels": 1,
            "minimum_ms": 1_500,
            "maximum_ms": 8_000,
        },
    }


def _mutation_guard(request: Request) -> JSONResponse | None:
    return _validate_local_mutation_request(request)


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, _EnrollmentBodyTooLarge):
        code, status_code = "enrollment_audio_too_large", 413
    elif isinstance(exc, EnrollmentAudioError):
        code, status_code = "invalid_enrollment_audio", 400
    elif isinstance(exc, EnrollmentNotFoundError):
        code, status_code = "enrollment_not_found", 404
    elif isinstance(exc, (EnrollmentBusyError, EnrollmentStateError)):
        code, status_code = "enrollment_conflict", 409
    elif isinstance(exc, EnrollmentModelUnavailable):
        code, status_code = "speaker_model_unavailable", 503
    elif isinstance(
        exc,
        (EnrollmentPersistenceError, EnrollmentActivationError),
    ):
        code, status_code = "voice_identity_unavailable", 503
    else:
        code, status_code = "voice_identity_unavailable", 503
    return JSONResponse({"success": False, "error": code}, status_code=status_code)


async def _read_pcm16(request: Request) -> bytes:
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            if int(raw_length) > _MAX_PCM_BYTES:
                raise _EnrollmentBodyTooLarge("enrollment audio is too large")
        except ValueError as exc:
            raise EnrollmentAudioError("invalid content length") from exc
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_PCM_BYTES:
            raise _EnrollmentBodyTooLarge("enrollment audio is too large")
        chunks.append(chunk)
    pcm16 = b"".join(chunks)
    if not pcm16 or len(pcm16) % 2:
        raise EnrollmentAudioError("invalid PCM16LE audio")
    return pcm16


def _session_header(
    enrollment_session: str | None,
) -> str:
    normalized = str(enrollment_session or "").strip()
    if not normalized:
        raise EnrollmentNotFoundError("enrollment session is required")
    return normalized


@router.get("/status")
async def get_status():
    try:
        return _status_payload(await _get_service().status())
    except Exception as exc:
        return _error_response(exc)


@router.post("/enrollment/start")
async def start_enrollment(request: Request):
    if validation_error := _mutation_guard(request):
        return validation_error
    try:
        return _status_payload(await _get_service().start())
    except Exception as exc:
        return _error_response(exc)


@router.post("/enrollment/segment")
async def submit_enrollment_segment(
    request: Request,
    enrollment_session: str | None = Header(
        default=None,
        alias=_ENROLLMENT_HEADER,
    ),
):
    if validation_error := _mutation_guard(request):
        return validation_error
    try:
        session_id = _session_header(enrollment_session)
        pcm16 = await _read_pcm16(request)
        status = await _get_service().submit_fixed(session_id, pcm16)
        return _status_payload(status)
    except Exception as exc:
        return _error_response(exc)


@router.post("/enrollment/verify")
async def verify_enrollment_segment(
    request: Request,
    enrollment_session: str | None = Header(
        default=None,
        alias=_ENROLLMENT_HEADER,
    ),
):
    if validation_error := _mutation_guard(request):
        return validation_error
    try:
        session_id = _session_header(enrollment_session)
        pcm16 = await _read_pcm16(request)
        result = await _get_service().verify(session_id, pcm16)
        payload = _status_payload(result.status)
        payload["verification"] = {"passed": result.passed}
        return payload
    except Exception as exc:
        return _error_response(exc)


@router.post("/enrollment/commit")
async def commit_enrollment(
    request: Request,
    enrollment_session: str | None = Header(
        default=None,
        alias=_ENROLLMENT_HEADER,
    ),
):
    if validation_error := _mutation_guard(request):
        return validation_error
    try:
        status = await _get_service().commit(_session_header(enrollment_session))
        return _status_payload(status)
    except Exception as exc:
        return _error_response(exc)


@router.post("/enrollment/cancel")
async def cancel_enrollment(
    request: Request,
    enrollment_session: str | None = Header(
        default=None,
        alias=_ENROLLMENT_HEADER,
    ),
):
    if validation_error := _mutation_guard(request):
        return validation_error
    try:
        status = await _get_service().cancel(_session_header(enrollment_session))
        return _status_payload(status)
    except Exception as exc:
        return _error_response(exc)


@router.delete("/profile")
async def delete_profile(request: Request):
    if validation_error := _mutation_guard(request):
        return validation_error
    try:
        return _status_payload(await _get_service().delete_profile())
    except Exception as exc:
        return _error_response(exc)


@router.put("/filter")
async def update_filter(request: Request, update: FilterUpdate):
    if validation_error := _mutation_guard(request):
        return validation_error
    try:
        status = await _get_service().set_filter_enabled(update.enabled)
        return _status_payload(status)
    except Exception as exc:
        return _error_response(exc)
