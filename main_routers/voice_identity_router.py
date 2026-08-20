"""Local control plane for one encrypted Owner voice profile."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from main_logic.voice_identity_service.registry import (
    VoiceIdentityServiceRegistryError,
    get_voice_identity_service_for_router,
)
from main_logic.voice_identity_service.service import VoiceIdentityServiceError
from main_routers.system_router import _validate_local_mutation_request


router = APIRouter(prefix="/api/voice-identity", tags=["voice-identity"])
_ENROLLMENT_HEADER = "X-Voice-Identity-Enrollment"
_PROFILE_HEADER = "X-Voice-Identity-Profile"
_PCM_CONTENT_TYPE = "audio/pcm;format=pcm_s16le;rate=16000;channels=1"
_MAX_PCM_BYTES = 16_000 * 4 * 2
_MAX_FILTER_JSON_BYTES = 1024


def _service():
    try:
        return get_voice_identity_service_for_router()
    except VoiceIdentityServiceRegistryError:
        return None


def _service_unavailable() -> JSONResponse:
    return JSONResponse(
        {"error_code": "runtime_degraded"},
        status_code=503,
    )


def _service_error(exc: VoiceIdentityServiceError) -> JSONResponse:
    if exc.code in {"invalid_enrollment_id", "invalid_profile_id"}:
        status_code = 400
    elif exc.code == "stale_enrollment":
        status_code = 409
    elif exc.code in {
        "invalid_pcm",
        "speech_too_short",
        "audio_too_long",
        "silence",
        "severe_clipping",
    }:
        status_code = 422
    else:
        status_code = 503
    return JSONResponse({"error_code": exc.code}, status_code=status_code)


def _validate_mutation(request: Request, payload: dict | None = None):
    return _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"error_code": "mutation_not_allowed"},
    )


async def _read_bounded_body(request: Request, maximum_bytes: int) -> bytes | None:
    """Read an ASGI request body without ever retaining more than the limit."""

    buffered = bytearray()
    try:
        async for chunk in request.stream():
            if len(chunk) > maximum_bytes - len(buffered):
                return None
            buffered.extend(chunk)
        return bytes(buffered)
    finally:
        buffered[:] = b"\x00" * len(buffered)


@router.get("/status")
async def get_voice_identity_status():
    service = _service()
    if service is None:
        return _service_unavailable()
    return service.status().as_dict()


@router.post("/enrollment/start")
async def start_voice_identity_enrollment(request: Request):
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        await service.start_enrollment()
    except VoiceIdentityServiceError as exc:
        return _service_error(exc)
    return service.status().as_dict()


@router.put("/enrollment/profile")
async def complete_voice_identity_enrollment(request: Request):
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    if request.headers.get("content-type", "").lower() != _PCM_CONTENT_TYPE:
        return JSONResponse({"error_code": "invalid_pcm"}, status_code=415)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_content_length = int(content_length)
            if parsed_content_length < 0:
                raise ValueError
            if parsed_content_length > _MAX_PCM_BYTES:
                return JSONResponse(
                    {"error_code": "audio_too_long"},
                    status_code=413,
                )
        except ValueError:
            return JSONResponse({"error_code": "invalid_pcm"}, status_code=400)
    pcm16 = await _read_bounded_body(request, _MAX_PCM_BYTES)
    if pcm16 is None:
        return JSONResponse({"error_code": "audio_too_long"}, status_code=413)
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        status = await service.complete_enrollment(
            request.headers.get(_ENROLLMENT_HEADER, ""),
            request.headers.get(_PROFILE_HEADER, ""),
            pcm16,
        )
    except VoiceIdentityServiceError as exc:
        return _service_error(exc)
    return status.as_dict()


@router.post("/enrollment/cancel")
async def cancel_voice_identity_enrollment(request: Request):
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    service = _service()
    if service is None:
        return _service_unavailable()
    enrollment_id = request.headers.get(_ENROLLMENT_HEADER, "")
    try:
        await service.cancel_enrollment(enrollment_id)
    except VoiceIdentityServiceError as exc:
        return _service_error(exc)
    return service.status().as_dict()


@router.put("/filter")
async def set_voice_identity_filter(request: Request):
    has_csrf_header = bool(request.headers.get("X-CSRF-Token"))
    if has_csrf_header:
        rejected = _validate_mutation(request)
        if rejected is not None:
            return rejected

    body = await _read_bounded_body(request, _MAX_FILTER_JSON_BYTES)
    if body is None:
        return JSONResponse({"error_code": "invalid_enabled"}, status_code=413)
    try:
        parsed_payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed_payload = None
    payload = parsed_payload if type(parsed_payload) is dict else None
    if not has_csrf_header:
        rejected = _validate_mutation(request, payload)
        if rejected is not None:
            return rejected
    if payload is None:
        return JSONResponse({"error_code": "invalid_enabled"}, status_code=422)
    enabled = payload.get("enabled")
    if type(enabled) is not bool:
        return JSONResponse({"error_code": "invalid_enabled"}, status_code=422)
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        status = await service.set_filter(enabled)
    except VoiceIdentityServiceError as exc:
        return _service_error(exc)
    return status.as_dict()


@router.delete("/profile")
async def delete_voice_identity_profile(request: Request):
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        status = await service.delete_profile()
    except VoiceIdentityServiceError as exc:
        return _service_error(exc)
    return status.as_dict()


__all__ = ["router"]
