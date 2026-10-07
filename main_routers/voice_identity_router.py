"""Local control plane for one encrypted Owner voice profile."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from main_logic.voice_identity_service.audio_contract import (
    OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID,
    OWNER_CAMPPLUS_DESKTOP_SOURCE_SAMPLE_RATE_HZ,
    VoiceIdentityAudioContractSnapshot,
)
from main_logic.voice_identity_service.registry import (
    VoiceIdentityServiceRegistryError,
    get_voice_identity_service_for_router,
)
from main_logic.voice_identity_service.service import VoiceIdentityServiceError
from main_logic.voice_identity_service.resource_manager import MAX_TRIAL_PCM_BYTES, VoiceResourceError
from main_routers.system_router import _validate_local_mutation_request
from utils.logger_config import get_module_logger


router = APIRouter(prefix="/api/voice-identity", tags=["voice-identity"])
logger = get_module_logger(__name__)
_ENROLLMENT_HEADER = "X-Voice-Identity-Enrollment"
_PROFILE_HEADER = "X-Voice-Identity-Profile"
_SEGMENT_HEADER = "X-Voice-Identity-Segment"
_AUDIO_CONTRACT_HEADER = "X-Voice-Audio-Contract"
_PCM_CONTENT_TYPE = "audio/pcm;format=pcm_s16le;rate=48000;channels=1"
_MAX_REFERENCE_PCM_BYTES = (
    OWNER_CAMPPLUS_DESKTOP_SOURCE_SAMPLE_RATE_HZ * 4 * 2
)
_MAX_VERIFICATION_PCM_BYTES = (
    OWNER_CAMPPLUS_DESKTOP_SOURCE_SAMPLE_RATE_HZ * 5 * 2
)
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
    if exc.code in {
        "invalid_enrollment_id",
        "invalid_profile_id",
        "invalid_segment_index",
    }:
        status_code = 400
    elif exc.code in {
        "feature_disabled",
        "stale_enrollment",
        "segment_out_of_order",
        "segment_in_progress",
        "audio_contract_changed",
    }:
        status_code = 409
    elif exc.code == "audio_too_long":
        status_code = 413
    elif exc.code in {
        "invalid_pcm",
        "speech_too_short",
        "silence",
        "volume_too_low",
        "severe_clipping",
        "no_speech_detected",
        "voice_samples_inconsistent",
        "owner_verification_failed",
    }:
        status_code = 422
    elif exc.code in {
        "audio_processing_unavailable",
        "unsupported_audio_contract",
        "model_unavailable",
        "secure_storage_unavailable",
    }:
        status_code = 503
    else:
        status_code = 503
    payload = {"error_code": exc.code}
    if exc.diagnostics is not None:
        payload["diagnostics"] = exc.diagnostics
    return JSONResponse(payload, status_code=status_code)


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
    body = await _read_bounded_body(request, 1024)
    expected_audio_contract = None
    if body:
        try:
            payload = json.loads(body)
            if type(payload) is not dict or set(payload) - {"preview_audio_contract"}:
                raise ValueError
            if "preview_audio_contract" in payload:
                raw = payload["preview_audio_contract"]
                if (type(raw) is not dict or set(raw) != {"contract_id", "revision", "noise_reduction_enabled"}
                        or type(raw["revision"]) is not int or type(raw["noise_reduction_enabled"]) is not bool):
                    raise ValueError
                expected_audio_contract = VoiceIdentityAudioContractSnapshot(**raw)
        except (ValueError, TypeError, UnicodeDecodeError):
            return JSONResponse({"error_code": "unsupported_audio_contract"}, status_code=400)
    elif body is None:
        return JSONResponse({"error_code": "unsupported_audio_contract"}, status_code=413)
    try:
        if expected_audio_contract is None:
            await service.start_enrollment()
        else:
            await service.start_enrollment(expected_audio_contract=expected_audio_contract)
    except VoiceIdentityServiceError as exc:
        return _service_error(exc)
    return service.status().as_dict()


@router.put("/enrollment/segment")
async def submit_voice_identity_enrollment_segment(request: Request):
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    if request.headers.get("content-type", "").lower() != _PCM_CONTENT_TYPE:
        return JSONResponse({"error_code": "invalid_pcm"}, status_code=415)
    audio_contract_id = request.headers.get(_AUDIO_CONTRACT_HEADER, "")
    if audio_contract_id != OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID:
        return JSONResponse(
            {"error_code": "unsupported_audio_contract"},
            status_code=415,
        )
    raw_segment_index = request.headers.get(_SEGMENT_HEADER, "")
    if raw_segment_index not in {"1", "2", "3", "4"}:
        return JSONResponse(
            {"error_code": "invalid_segment_index"},
            status_code=400,
        )
    segment_index = int(raw_segment_index)
    maximum_pcm_bytes = (
        _MAX_VERIFICATION_PCM_BYTES
        if segment_index == 4
        else _MAX_REFERENCE_PCM_BYTES
    )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_content_length = int(content_length)
            if parsed_content_length < 0:
                raise ValueError
            if parsed_content_length > maximum_pcm_bytes:
                return JSONResponse(
                    {"error_code": "audio_too_long"},
                    status_code=413,
                )
        except ValueError:
            return JSONResponse({"error_code": "invalid_pcm"}, status_code=400)
    pcm16 = await _read_bounded_body(request, maximum_pcm_bytes)
    if pcm16 is None:
        return JSONResponse({"error_code": "audio_too_long"}, status_code=413)
    if len(pcm16) % 2:
        return JSONResponse({"error_code": "invalid_pcm"}, status_code=400)
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        status = await service.submit_enrollment_segment(
            request.headers.get(_ENROLLMENT_HEADER, ""),
            request.headers.get(_PROFILE_HEADER, ""),
            segment_index,
            pcm16,
            sample_rate_hz=OWNER_CAMPPLUS_DESKTOP_SOURCE_SAMPLE_RATE_HZ,
            audio_contract_id=audio_contract_id,
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
        logger.info("Voice identity filter change requested enabled=%s", enabled)
        status = await service.set_filter(enabled)
    except VoiceIdentityServiceError as exc:
        logger.warning("Voice identity filter change failed enabled=%s", enabled)
        return _service_error(exc)
    payload = status.as_dict()
    logger.info(
        "Voice identity filter change completed requested=%s effective=%s",
        payload.get("requested_enabled"), payload.get("effective_enabled"),
    )
    return payload


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


def _resource_error(exc) -> JSONResponse:
    code = exc.code
    status = 503
    if code in {"invalid_resource_operation", "invalid_pcm", "invalid_enabled", "invalid_preview_request", "preview_request_invalid"}:
        status = 400
    elif code in {"resource_operation_busy", "enrollment_in_progress", "audio_contract_changed", "wake_preference_managed",
                  "preview_invalid", "preview_busy", "preview_expired", "preview_consumed", "preview_not_ready", "preview_owner_active", "preview_owner_changed"}:
        status = 409
    return JSONResponse({"error_code": code}, status_code=status)


@router.get("/resources")
async def get_voice_resources():
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        return await service.resources()
    except (VoiceIdentityServiceError, VoiceResourceError) as exc:
        return _resource_error(exc)


def _read_voice_repair_guide() -> str:
    guide = Path(__file__).resolve().parents[1] / "docs" / "development" / "voice-readiness.md"
    try:
        if guide.stat().st_size > 64 * 1024:
            raise OSError
        with guide.open("r", encoding="utf-8") as source:
            return source.read(64 * 1024)
    except (OSError, UnicodeError):
        # Frontend displays the localized backend-machine repair explanation.
        # This fixed URL is a safe exit when an older frozen bundle lacks docs.
        return "https://github.com/Project-N-E-K-O/N.E.K.O/releases\n"


@router.get("/resources/repair-guide")
async def get_voice_resource_repair_guide():
    return PlainTextResponse(await asyncio.to_thread(_read_voice_repair_guide))


async def _start_resource_operation(request: Request, kind: str | None, operation_id: str | None = None):
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    service = _service()
    if service is None:
        return _service_unavailable()
    # This endpoint takes no client URL/path/configuration payload.
    body = await _read_bounded_body(request, 2)
    if body not in {b"", b"{}"}:
        return JSONResponse({"error_code": "invalid_resource_operation"}, status_code=400)
    try:
        if operation_id is not None:
            return JSONResponse(service.start_reserved_resource_operation(operation_id), status_code=202)
        return JSONResponse(service.start_resource_operation(kind), status_code=202)
    except (VoiceIdentityServiceError, VoiceResourceError) as exc:
        return _resource_error(exc)


@router.post("/resources/prepare")
async def prepare_voice_resources(request: Request):
    return await _start_resource_operation(request, "prepare")


@router.post("/resources/wake-word/download")
async def download_wake_word_resources(request: Request):
    return await _start_resource_operation(request, "download")


@router.get("/resources/operations/{operation_id}")
async def get_voice_resource_operation(operation_id: str):
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        return service.resource_operation(operation_id)
    except (VoiceIdentityServiceError, VoiceResourceError) as exc:
        return _resource_error(exc)


@router.post("/resources/operations")
async def reserve_voice_resource_operation(request: Request):
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    payload = await _read_resource_json(request)
    if payload is None or set(payload) != {"kind"} or payload["kind"] not in ("prepare", "download"):
        return JSONResponse({"error_code": "invalid_resource_operation"}, status_code=400)
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        return JSONResponse(service.reserve_resource_operation(payload["kind"]), status_code=201)
    except (VoiceIdentityServiceError, VoiceResourceError) as exc:
        return _resource_error(exc)


@router.post("/resources/operations/{operation_id}/start")
async def start_reserved_voice_resource_operation(operation_id: str, request: Request):
    return await _start_resource_operation(request, None, operation_id)


@router.post("/resources/operations/{operation_id}/cancel")
async def cancel_voice_resource_operation(operation_id: str, request: Request):
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        return await service.cancel_resource_operation(operation_id)
    except (VoiceIdentityServiceError, VoiceResourceError) as exc:
        return _resource_error(exc)


async def _read_resource_json(request: Request) -> dict | None:
    body = await _read_bounded_body(request, 1024)
    if body is None:
        return None
    try:
        payload = json.loads(body)
        return payload if type(payload) is dict else None
    except (ValueError, UnicodeDecodeError):
        return None


@router.post("/resources/wake-word/preference")
async def set_voice_wake_word_preference(request: Request):
    from main_logic.voice_input.preview import VoicePreviewIsolationError
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    payload = await _read_resource_json(request)
    if payload is None or set(payload) != {"enabled"} or type(payload["enabled"]) is not bool:
        return JSONResponse({"error_code": "invalid_enabled"}, status_code=400)
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        return await service.set_wake_word_preference(payload["enabled"])
    except (VoiceIdentityServiceError, VoiceResourceError, VoicePreviewIsolationError) as exc:
        return _resource_error(exc)


@router.post("/audio/check/isolation")
async def begin_voice_preview_isolation(request: Request):
    from main_logic.voice_input.preview import VoicePreviewIsolationError
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    payload = await _read_resource_json(request)
    if payload is None or set(payload) != {"request_id"} or not isinstance(payload["request_id"], str) or not 1 <= len(payload["request_id"]) <= 128:
        return JSONResponse({"error_code": "invalid_preview_request"}, status_code=400)
    service = _service()
    if service is None:
        return _service_unavailable()
    try:
        return service.begin_trial_isolation(payload["request_id"]).as_dict()
    except (VoiceIdentityServiceError, VoiceResourceError, VoicePreviewIsolationError) as exc:
        return _resource_error(exc)


@router.post("/audio/check/isolation/release")
async def release_voice_preview_isolation(request: Request):
    from main_logic.voice_input.preview import preview_isolation_registry
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    payload = await _read_resource_json(request)
    if payload is None or set(payload) != {"token"} or not isinstance(payload["token"], str) or not 1 <= len(payload["token"]) <= 128:
        return JSONResponse({"error_code": "invalid_preview_request"}, status_code=400)
    return {"released": preview_isolation_registry.release(payload["token"])}


@router.post("/audio/check")
async def check_voice_trial_audio(request: Request):
    from main_logic.voice_input.preview import preview_isolation_registry, VoicePreviewIsolationError
    rejected = _validate_mutation(request)
    if rejected is not None:
        return rejected
    if request.headers.get("content-type", "").lower() != _PCM_CONTENT_TYPE:
        return JSONResponse({"error_code": "invalid_pcm"}, status_code=415)
    if request.headers.get(_AUDIO_CONTRACT_HEADER, "") != OWNER_CAMPPLUS_DESKTOP_CONTRACT_ID:
        return JSONResponse({"error_code": "unsupported_audio_contract"}, status_code=415)
    service = _service()
    if service is None:
        return _service_unavailable()
    ticket = None
    try:
        ticket = preview_isolation_registry.claim(request.headers.get("X-Voice-Input-Check", ""))
        pcm16 = await asyncio.wait_for(
            _read_bounded_body(request, MAX_TRIAL_PCM_BYTES),
            timeout=min(15.0, ticket.as_dict()["ttl_seconds"]),
        )
        if pcm16 is None:
            return JSONResponse({"error_code": "audio_too_long"}, status_code=413)
        ticket.validate_current()
        result = await service.check_trial_audio(pcm16, noise_reduction_enabled=ticket.noise_reduction_enabled)
        ticket.validate_current()
        return result
    except (VoiceIdentityServiceError, VoiceResourceError, VoicePreviewIsolationError) as exc:
        return _resource_error(exc)
    except TimeoutError:
        return JSONResponse({"error_code": "preview_expired"}, status_code=409)
    finally:
        if ticket is not None:
            preview_isolation_registry.release(ticket)
