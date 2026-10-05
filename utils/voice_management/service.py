"""Existing-voice operations, with ownership checked across network awaits."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from weakref import WeakValueDictionary

from .types import RemoteVoice, VoiceManagementAdapter, VoiceManagementError, VoiceRuntime

_CONTEXT_SECRET = secrets.token_bytes(32)
_OVERWRITE_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_IMPORT_FIELDS = frozenset({"clone_model", "doubao_resource_id", "ref_language"})
_VOICE_METADATA_FIELDS = _IMPORT_FIELDS | frozenset({
    "minimax_base_url", "elevenlabs_base_url", "dashscope_base_url",
    "doubao_base_url", "glm_base_url", "glm_voice_name", "raw_voice_id",
    "remote_revision",
})
_PUBLIC_METADATA_FIELDS = _VOICE_METADATA_FIELDS - frozenset({
    "minimax_base_url", "elevenlabs_base_url", "dashscope_base_url",
    "doubao_base_url", "glm_base_url",
})


def context_token(runtime: VoiceRuntime) -> str:
    payload = json.dumps(asdict(runtime), sort_keys=True, separators=(",", ":"))
    return hmac.new(_CONTEXT_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def public_voice_data(data: dict) -> dict:
    """Whitelist local fields rather than projecting an upstream response."""
    fields = _PUBLIC_METADATA_FIELDS | frozenset({
        "local_ref", "voice_id", "remote_voice_id", "source", "provider", "origin",
        "prefix", "display_name", "created_at", "remote_created_at", "imported_at",
        "updated_at", "remote_status", "can_overwrite", "verification", "availability",
        "overwrite_status", "overwrite_operation_id",
    })
    return {key: value for key, value in data.items() if key in fields}


def _voice_metadata(data: dict) -> dict:
    return {
        key: value for key, value in data.items()
        if key in _VOICE_METADATA_FIELDS and isinstance(value, str) and len(value) <= 2048
    }


def _overwrite_allowed(adapter: VoiceManagementAdapter, runtime: VoiceRuntime, voice: RemoteVoice | None) -> bool:
    # A ready voice without a revision cannot provide proof that this update completed.
    return bool(adapter.capabilities_for(runtime).overwrite and voice and voice.can_overwrite
                and _voice_metadata(voice.metadata).get("remote_revision"))


async def management_context(adapter: VoiceManagementAdapter, cm) -> dict:
    runtime = await asyncio.to_thread(adapter.resolve_runtime, cm)
    capabilities = adapter.capabilities_for(runtime)
    return {
        "success": True, "provider": runtime.provider,
        "context_token": context_token(runtime), "configured": bool(runtime.api_key),
        "capabilities": capabilities.to_dict(), "required_fields": adapter.manual_fields(runtime),
    }


async def _runtime(adapter: VoiceManagementAdapter, cm, token: str) -> VoiceRuntime:
    runtime = await asyncio.to_thread(adapter.resolve_runtime, cm)
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{64}", token) is None or not hmac.compare_digest(context_token(runtime), token):
        raise VoiceManagementError("CONTEXT_CHANGED", 409)
    if not runtime.api_key:
        raise VoiceManagementError("CONFIG_MISSING", 400)
    return runtime


async def _check_context(adapter: VoiceManagementAdapter, cm, runtime: VoiceRuntime) -> None:
    current = await asyncio.to_thread(adapter.resolve_runtime, cm)
    if not hmac.compare_digest(context_token(current), context_token(runtime)):
        raise VoiceManagementError("CONTEXT_CHANGED", 409)


def _remote_id(value: object) -> str:
    if not isinstance(value, str):
        raise VoiceManagementError("INVALID_VOICE_ID", 400)
    if any(ord(char) < 32 for char in value):
        raise VoiceManagementError("INVALID_VOICE_ID", 400)
    value = value.strip()
    if not value or len(value) > 512:
        raise VoiceManagementError("INVALID_VOICE_ID", 400)
    return value


async def list_remote_voices(
    adapter: VoiceManagementAdapter, cm, *, token: str, cursor: str | None = None, query: str = ""
) -> dict:
    runtime = await _runtime(adapter, cm, token)
    if not adapter.capabilities_for(runtime).list_voices:
        raise VoiceManagementError("LIST_UNSUPPORTED", 400)
    if len(query) > 200 or (cursor is not None and len(cursor) > 2048):
        raise VoiceManagementError("INVALID_CURSOR", 400)
    page = await adapter.list_voices(runtime, cursor=cursor, query=query)
    await _check_context(adapter, cm, runtime)
    local = await asyncio.to_thread(cm.get_voices_for_current_api, for_listing=True)
    registered = {
        data.get("remote_voice_id"): ref for ref, data in local.items()
        if isinstance(data, dict) and data.get("origin") == "import" and data.get("scope_id") == runtime.scope_id
    }
    voices = [{
        "voice_id": voice.voice_id, "name": voice.name or voice.voice_id,
        "created_at": voice.created_at, "status": voice.status,
        "metadata": {key: value for key, value in _voice_metadata(voice.metadata).items()
                     if key in _PUBLIC_METADATA_FIELDS},
        "can_overwrite": _overwrite_allowed(adapter, runtime, voice),
        "imported": voice.voice_id in registered, "local_ref": registered.get(voice.voice_id),
    } for voice in page.voices]
    await _check_context(adapter, cm, runtime)
    return {
        "success": True, "provider": runtime.provider, "context_token": token,
        "voices": voices, "next_cursor": page.next_cursor,
    }


async def import_remote_voice(adapter: VoiceManagementAdapter, cm, payload: dict) -> dict:
    runtime = await _runtime(adapter, cm, payload.get("context_token"))
    capabilities = adapter.capabilities_for(runtime)
    if not capabilities.manual_import:
        raise VoiceManagementError("IMPORT_UNSUPPORTED", 400)
    remote_id = adapter.validate_voice_id(_remote_id(payload.get("remote_voice_id")))
    display_name = payload.get("display_name", "")
    if not isinstance(display_name, str) or len(display_name) > 200:
        raise VoiceManagementError("INVALID_DISPLAY_NAME", 400)
    supplied = payload.get("metadata", {})
    if not isinstance(supplied, dict) or set(supplied) - _IMPORT_FIELDS:
        raise VoiceManagementError("INVALID_METADATA", 400)
    if any(not isinstance(value, str) or len(value) > 200 for value in supplied.values()):
        raise VoiceManagementError("INVALID_METADATA", 400)
    # Resource identity is taken from settings, never supplied by a stale client.
    if supplied.get("doubao_resource_id", runtime.resource_id) != runtime.resource_id:
        raise VoiceManagementError("CONTEXT_CHANGED", 409)
    remote: RemoteVoice | None = None
    if capabilities.details:
        try:
            remote = await adapter.get_voice(runtime, remote_id)
        except VoiceManagementError as exc:
            if exc.code not in {
                "DETAILS_UNSUPPORTED", "PERMISSION_DENIED", "AUTH_FAILED",
                "MANAGEMENT_CONFIG_MISSING", "LIST_UNSUPPORTED",
            }:
                raise
    await _check_context(adapter, cm, runtime)
    if remote and remote.status in {"failed", "processing", "unavailable"}:
        raise VoiceManagementError("VOICE_NOT_READY", 409)
    metadata = _voice_metadata(adapter.import_metadata(runtime))
    metadata.update(_voice_metadata(supplied))
    if remote:
        metadata.update(_voice_metadata(remote.metadata))
        remote_id = remote.voice_id
    now = datetime.now(timezone.utc).isoformat()
    name = display_name.strip() or (remote.name if remote else "") or remote_id
    metadata.update({
        "source": "clone", "provider": runtime.provider, "origin": "import",
        "remote_voice_id": remote_id, "scope_id": runtime.scope_id,
        "display_name": name, "prefix": name, "imported_at": now, "created_at": now,
        "remote_created_at": remote.created_at if remote else None,
        "remote_status": remote.status if remote else "unknown",
        "verification": "verified" if remote else "unverified",
        "can_overwrite": _overwrite_allowed(adapter, runtime, remote),
    })

    def commit():
        current = adapter.resolve_runtime(cm)
        if not hmac.compare_digest(context_token(current), context_token(runtime)):
            raise VoiceManagementError("CONTEXT_CHANGED", 409)
        return cm.import_remote_voice(runtime.scope_id, runtime.provider, remote_id, metadata)

    ref, saved, created = await asyncio.to_thread(commit)
    return {
        "success": True, "voice_id": ref, "voice_data": public_voice_data(saved),
        "created": created, "verification": saved.get("verification", "unverified"),
    }


async def overwrite_remote_voice(
    adapter: VoiceManagementAdapter, cm, local_ref: str, *, token: str, audio: bytes, filename: str
) -> dict:
    if re.fullmatch(r"voice_[0-9a-f]{32}", local_ref) is None:
        raise VoiceManagementError("VOICE_NOT_FOUND", 404)
    runtime = await _runtime(adapter, cm, token)
    record = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
    if not record or record.get("scope_id") != runtime.scope_id:
        raise VoiceManagementError("CONTEXT_CHANGED", 409)
    if not adapter.capabilities_for(runtime).overwrite or not record.get("can_overwrite"):
        raise VoiceManagementError("OVERWRITE_UNSUPPORTED", 400)
    lock = _OVERWRITE_LOCKS.setdefault(local_ref, asyncio.Lock())
    if lock.locked():
        raise VoiceManagementError("OPERATION_IN_PROGRESS", 409)
    async with lock:
        record = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
        if not record or record.get("scope_id") != runtime.scope_id:
            raise VoiceManagementError("CONTEXT_CHANGED", 409)
        if not record.get("can_overwrite"):
            raise VoiceManagementError("OVERWRITE_UNSUPPORTED", 400)
        # An unknown upstream outcome requires a status refresh, not resubmission.
        if record.get("overwrite_status") in {"processing", "unknown"}:
            raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 409)
        remote_id = record["remote_voice_id"]
        operation_id = secrets.token_hex(16)
        mutation_started = False
        claim_owned = False
        previous_revision = None

        async def before_mutation(current: RemoteVoice):
            nonlocal mutation_started, claim_owned, previous_revision
            if current.voice_id != remote_id or not _overwrite_allowed(adapter, runtime, current):
                raise VoiceManagementError("OVERWRITE_UNSUPPORTED", 400)
            previous_revision = _voice_metadata(current.metadata)["remote_revision"]
            await _check_context(adapter, cm, runtime)
            latest = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
            if not latest or any(latest.get(key) != record.get(key) for key in (
                "scope_id", "remote_voice_id", "provider",
            )):
                raise VoiceManagementError("CONTEXT_CHANGED", 409)
            claim = asyncio.create_task(cm.aupdate_imported_voice(local_ref, runtime.scope_id, {
                "overwrite_status": "processing", "overwrite_operation_id": operation_id,
                "overwrite_previous_revision": previous_revision,
            }, expected_operation_id=record.get("overwrite_operation_id") or ""))
            try:
                await asyncio.shield(claim)
            except asyncio.CancelledError:
                # A to_thread write continues after its waiter is cancelled.
                # Join it before cleanup so it cannot write a late pending marker.
                while not claim.done():
                    try:
                        await asyncio.shield(claim)
                    except asyncio.CancelledError:
                        continue
                claim.result()
                claim_owned = True
                raise
            claim_owned = True
            await _check_context(adapter, cm, runtime)
            mutation_started = True

        try:
            updated = await adapter.overwrite(
                runtime, remote_id, audio=audio, filename=filename, before_mutation=before_mutation,
            )
        except (Exception, asyncio.CancelledError) as exc:
            known_rejection = isinstance(exc, VoiceManagementError) and exc.code in {
                "PERMISSION_DENIED", "AUTH_FAILED", "RATE_LIMITED", "UPSTREAM_REJECTED",
                "INVALID_VOICE_ID", "OVERWRITE_UNSUPPORTED", "VOICE_NOT_READY",
            }
            status = "unknown" if mutation_started and not known_rejection else "failed"
            if claim_owned:
                try:
                    await asyncio.shield(cm.aupdate_imported_voice(local_ref, runtime.scope_id, {
                        "overwrite_status": status, "overwrite_operation_id": operation_id,
                    }, expected_operation_id=operation_id))
                except (ValueError, OSError):
                    # Never recreate a deleted record or overwrite a new owner.
                    pass
            if mutation_started and status == "unknown" and not isinstance(exc, (VoiceManagementError, asyncio.CancelledError)):
                raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 502) from exc
            raise
        if updated.voice_id != remote_id:
            await cm.aupdate_imported_voice(local_ref, runtime.scope_id, {"overwrite_status": "unknown"},
                                           expected_operation_id=operation_id)
            raise VoiceManagementError("UPSTREAM_INVALID_RESPONSE", 502)
        status = (
            "completed" if updated.status in {"ready", "completed", "OK"}
            and previous_revision is not None and updated.metadata.get("remote_revision")
            and updated.metadata["remote_revision"] != previous_revision
            else "failed" if updated.status in {"failed", "unavailable"}
            else "processing"
        )
        values = _voice_metadata(updated.metadata)
        values.update({
            "overwrite_status": status, "remote_status": updated.status,
            "can_overwrite": _overwrite_allowed(adapter, runtime, updated),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            saved = await cm.aupdate_imported_voice(local_ref, runtime.scope_id, values,
                                                  expected_operation_id=operation_id)
        except Exception as exc:
            raise VoiceManagementError("LOCAL_SAVE_FAILED_AFTER_UPDATE", 500) from exc
        return {
            "success": True, "voice_id": local_ref, "status": status,
            "voice_data": public_voice_data(saved),
        }


async def refresh_overwrite_status(adapter: VoiceManagementAdapter, cm, local_ref: str, *, token: str) -> dict:
    runtime = await _runtime(adapter, cm, token)
    lock = _OVERWRITE_LOCKS.get(local_ref)
    if lock is not None and lock.locked():
        raise VoiceManagementError("OPERATION_IN_PROGRESS", 409)
    record = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
    if not record:
        raise VoiceManagementError("VOICE_NOT_FOUND", 404)
    if record.get("scope_id") != runtime.scope_id:
        raise VoiceManagementError("CONTEXT_CHANGED", 409)
    remote = await adapter.get_voice(runtime, record["remote_voice_id"])
    await _check_context(adapter, cm, runtime)
    if remote is None:
        raise VoiceManagementError("VOICE_NOT_FOUND", 404)
    status = record.get("overwrite_status", "completed")
    previous = record.get("overwrite_previous_revision")
    revision = remote.metadata.get("remote_revision")
    if remote.status in {"failed", "unavailable"}:
        status = "failed"
    elif remote.status in {"ready", "completed", "OK"} and previous is not None and revision is not None and revision != previous:
        status = "completed"
    latest = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
    if not latest or latest.get("overwrite_operation_id") != record.get("overwrite_operation_id"):
        raise VoiceManagementError("CONTEXT_CHANGED", 409)
    values = _voice_metadata(remote.metadata)
    values.update({
        "overwrite_status": status, "remote_status": remote.status,
        "can_overwrite": _overwrite_allowed(adapter, runtime, remote),
    })
    saved = await cm.aupdate_imported_voice(local_ref, runtime.scope_id, values,
                                          expected_operation_id=record.get("overwrite_operation_id") or "")
    return {"success": True, "voice_id": local_ref, "status": status, "voice_data": public_voice_data(saved)}
