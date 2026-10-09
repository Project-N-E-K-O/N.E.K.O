"""Explicit import and update of existing hosted clone voices."""

from __future__ import annotations

import asyncio
import json

from fastapi import File, Form, Request, UploadFile

from ..shared_state import get_config_manager
from ._shared import (
    MAX_UPLOAD_SIZE, _json_no_store_response, _read_limited_stream, _UploadTooLargeError,
    logger, router,
)
from utils.voice_management import service
from utils.voice_management.types import VoiceManagementError


def _adapter(provider: object):
    if not isinstance(provider, str) or len(provider) > 64:
        raise VoiceManagementError("IMPORT_UNSUPPORTED", 400)
    from utils.tts import provider_registry

    adapter = provider_registry.get_voice_management(provider.strip().lower())
    if adapter is None:
        raise VoiceManagementError("IMPORT_UNSUPPORTED", 400)
    return adapter


def _error(exc: Exception, *, details: dict | None = None):
    if isinstance(exc, ValueError) and exc.args == ("VOICE_CONTEXT_CHANGED",):
        exc = VoiceManagementError("CONTEXT_CHANGED", 409)
    elif isinstance(exc, (json.JSONDecodeError, OSError)) or (
        isinstance(exc, ValueError) and exc.args == ("VOICE_STORAGE_INVALID",)
    ):
        exc = VoiceManagementError("STORAGE_ERROR", 500)
    if isinstance(exc, VoiceManagementError):
        if details is not None:
            exc.details.update(details)
        return _json_no_store_response({
            "success": False, "code": exc.code, "error": exc.code, "details": exc.details,
        }, status_code=exc.status_code)
    # No credential, raw upstream body or configured URL enters diagnostics.
    logger.error("code=VOICE_MANAGEMENT_FAILED exception_type=%s", type(exc).__name__)
    return _json_no_store_response({
        "success": False, "code": "LOCAL_OPERATION_FAILED", "error": "LOCAL_OPERATION_FAILED",
        "details": details or {},
    }, status_code=500)


@router.get('/remote_voices/context')
async def remote_voice_context(provider: str, local_ref: str | None = None):
    try:
        result = await service.management_context(_adapter(provider), get_config_manager(), local_ref=local_ref)
        return _json_no_store_response(result)
    except Exception as exc:
        return _error(exc)


@router.get('/remote_voices')
async def remote_voice_list(
    provider: str, context_token: str, cursor: str | None = None, query: str = ""
):
    try:
        result = await service.list_remote_voices(
            _adapter(provider), get_config_manager(), token=context_token, cursor=cursor, query=query,
        )
        return _json_no_store_response(result)
    except Exception as exc:
        return _error(exc)


@router.post('/voices/import')
async def import_existing_voice(request: Request):
    try:
        try:
            payload = await request.json()
        except Exception:
            raise VoiceManagementError("INVALID_JSON", 400) from None
        if not isinstance(payload, dict):
            raise VoiceManagementError("INVALID_JSON", 400)
        if set(payload) - {"provider", "remote_voice_id", "display_name", "context_token", "metadata"}:
            raise VoiceManagementError("INVALID_METADATA", 400)
        result = await service.import_remote_voice(
            _adapter(payload.get("provider")), get_config_manager(), payload,
        )
        return _json_no_store_response(result)
    except Exception as exc:
        return _error(exc)


async def _record_adapter(cm, local_ref: str):
    record = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
    if not record:
        raise VoiceManagementError("VOICE_NOT_FOUND", 404)
    return _adapter(record.get("provider"))


@router.post('/voices/{local_ref}/overwrite')
async def overwrite_existing_voice(
    local_ref: str, audio: UploadFile = File(...), context_token: str = Form(...)
):
    cm = adapter = None
    service_entered = False
    try:
        cm = get_config_manager()
        adapter = await _record_adapter(cm, local_ref)
        try:
            buffer = await _read_limited_stream(audio, MAX_UPLOAD_SIZE)
        except _UploadTooLargeError:
            raise VoiceManagementError("AUDIO_TOO_LARGE", 413) from None
        if buffer.getbuffer().nbytes == 0:
            raise VoiceManagementError("INVALID_AUDIO", 400)
        from utils.audio import normalize_voice_clone_api_audio

        try:
            normalized, filename, _ = await asyncio.to_thread(
                normalize_voice_clone_api_audio, buffer, audio.filename or "reference.wav",
            )
        except ValueError:
            raise VoiceManagementError("INVALID_AUDIO", 400) from None
        service_entered = True
        result = await service.overwrite_remote_voice(
            adapter, cm, local_ref, token=context_token,
            audio=normalized.getvalue(), filename=filename,
        )
        return _json_no_store_response(result)
    except Exception as exc:
        # Only validation before service entry proves no submission or result
        # write. Preserve service evidence; an unclassified fallback is unknown.
        details = None
        if not isinstance(exc, VoiceManagementError) or "attempt_outcome" not in exc.details:
            details = await service.overwrite_result_details(
                adapter, cm, local_ref, token=context_token,
                attempt_outcome="unknown" if service_entered else "not_submitted",
                state_sync="unknown" if service_entered else "unchanged",
            ) if adapter else {
                "attempt_outcome": "not_submitted", "voice_state": None, "state_sync": "unchanged",
            }
        return _error(exc, details=details)
    finally:
        await audio.close()


@router.get('/voices/{local_ref}/overwrite_status')
async def existing_voice_overwrite_status(local_ref: str, context_token: str):
    try:
        cm = get_config_manager()
        adapter = await _record_adapter(cm, local_ref)
        result = await service.refresh_overwrite_status(adapter, cm, local_ref, token=context_token)
        return _json_no_store_response(result)
    except Exception as exc:
        return _error(exc)


@router.post('/voices/{local_ref}/recover_overwrite')
async def recover_existing_voice_overwrite(local_ref: str, request: Request):
    try:
        try:
            payload = await request.json()
        except Exception:
            raise VoiceManagementError("INVALID_JSON", 400) from None
        if not isinstance(payload, dict) or set(payload) != {
            "context_token", "operation_id", "record_revision",
        }:
            raise VoiceManagementError("INVALID_METADATA", 400)
        cm = get_config_manager()
        adapter = await _record_adapter(cm, local_ref)
        from utils.voice_management.overwrite_recovery import recover_prepared_overwrite

        result = await recover_prepared_overwrite(
            adapter, cm, local_ref, token=payload["context_token"],
            operation_id=payload["operation_id"], record_revision=payload["record_revision"],
        )
        return _json_no_store_response(result)
    except Exception as exc:
        return _error(exc)


@router.post('/voices/{local_ref}/abandon_overwrite')
async def abandon_existing_voice_overwrite(local_ref: str, request: Request):
    try:
        try:
            payload = await request.json()
        except Exception:
            raise VoiceManagementError("INVALID_JSON", 400) from None
        if not isinstance(payload, dict) or set(payload) != {
            "context_token", "operation_id", "record_revision",
        }:
            raise VoiceManagementError("INVALID_METADATA", 400)
        cm = get_config_manager()
        adapter = await _record_adapter(cm, local_ref)
        from utils.voice_management.overwrite_recovery import abandon_unknown_overwrite

        result = await abandon_unknown_overwrite(
            adapter, cm, local_ref, token=payload["context_token"],
            operation_id=payload["operation_id"], record_revision=payload["record_revision"],
        )
        return _json_no_store_response(result)
    except Exception as exc:
        return _error(exc)
